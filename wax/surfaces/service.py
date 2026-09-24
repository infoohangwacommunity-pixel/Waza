"""
Web Surface service — create/update/access/lifecycle.

AI supplies the experience bundle (HTML entry + optional assets).
Infrastructure stores, isolates, authorizes, and cleans.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from wax.artifacts.storage import delete_uri, read_bytes, store_bytes
from wax.config import get_settings
from wax.db.models import Surface, SurfaceAiRequest, SurfaceEvent, SurfaceRevision, SurfaceSession
from wax.observability.logging import get_logger
from wax.surfaces.policy import (
    DEFAULT_SURFACE_POLICY,
    CapabilityScope,
    SurfaceLifecyclePolicy,
    SurfaceStatus,
    can_transition,
    utcnow,
)
from wax.surfaces.runtime import inject_runtime_bridge, render_unavailable, wrap_ai_html
from wax.surfaces.tokens import generate_token, hash_token, tokens_match

logger = get_logger(__name__)

# Hard limits (infrastructure safety, not product quotas)
MAX_ENTRY_BYTES = 2_000_000  # 2 MB entry HTML
MAX_STATE_JSON_BYTES = 200_000


class SurfaceService:
    def __init__(self, session: AsyncSession, policy: SurfaceLifecyclePolicy | None = None):
        self.session = session
        self.settings = get_settings()
        self.policy = policy or DEFAULT_SURFACE_POLICY

    def surface_origin(self) -> str:
        """Origin that hosts AI-authored Surface HTML (security principal boundary)."""
        origin = (getattr(self.settings, "surface_public_origin", None) or "").rstrip("/")
        if origin:
            return origin
        return (self.settings.public_base_url or "").rstrip("/")

    def public_url(self, token: str) -> Optional[str]:
        base = self.surface_origin()
        if not base:
            return None
        return f"{base}/s/{token}"

    def api_base_for_bridge(self) -> str:
        """Absolute origin for the bridge when a dedicated Surface origin is set; else empty."""
        dedicated = (getattr(self.settings, "surface_public_origin", None) or "").rstrip("/")
        if dedicated:
            return dedicated
        # Same-origin relative: bridge uses path-only /s/{token}/api
        return ""

    def _default_scopes(self) -> list[str]:
        return [
            CapabilityScope.VIEW.value,
            CapabilityScope.STATE_READ.value,
            CapabilityScope.STATE_WRITE.value,
            CapabilityScope.EVENT_WRITE.value,
            CapabilityScope.AI_REQUEST.value,
            CapabilityScope.ARTIFACT_READ.value,
        ]

    async def create(
        self,
        *,
        principal_id: UUID | str,
        html: str,
        title: str | None = None,
        description: str | None = None,
        work_id: UUID | str | None = None,
        parent_surface_id: UUID | str | None = None,
        preferred_lifetime_hours: float | None = None,
        lifecycle_intent: str | None = None,
        requested_scopes: list[str] | None = None,
        assets: dict[str, str] | None = None,  # name -> data_uri or ignored for now
        idempotency_key: str | None = None,
        source_note: str | None = None,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new surface with revision 1 from AI-authored HTML."""
        pid = UUID(str(principal_id))
        now = utcnow()

        if idempotency_key:
            existing = await self.session.execute(
                select(Surface).where(
                    Surface.principal_id == pid,
                    Surface.idempotency_key == str(idempotency_key)[:200],
                )
            )
            row = existing.scalar_one_or_none()
            if row and row.status in (
                SurfaceStatus.CREATING.value,
                SurfaceStatus.ACTIVE.value,
                SurfaceStatus.IDLE.value,
            ):
                token_hint = None  # cannot re-issue raw token
                return {
                    "ok": True,
                    "surface_id": str(row.id),
                    "title": row.title,
                    "status": row.status,
                    "revision": row.current_revision,
                    "page_url": None,
                    "idempotent": True,
                    "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                    "note": "Surface already exists for this request.",
                }

        html = html or ""
        if len(html.encode("utf-8")) > MAX_ENTRY_BYTES:
            return {"ok": False, "error": "entry_too_large"}

        # Server-side scope grant (ignore escalation attempts)
        allowed = set(self._default_scopes())
        if requested_scopes:
            scopes = [s for s in requested_scopes if s in allowed]
        else:
            scopes = list(allowed)

        expires = now + self.policy.resolve_lifetime(preferred_lifetime_hours)
        surface_id = uuid4()
        token = generate_token(32)
        token_h = hash_token(token)

        surface = Surface(
            id=surface_id,
            principal_id=pid,
            work_id=UUID(str(work_id)) if work_id else None,
            token_hash=token_h,
            title=(title or "WAX Surface")[:500],
            description=(description or None),
            status=SurfaceStatus.CREATING.value,
            expires_at=expires,
            current_revision=0,
            granted_scopes=scopes,
            idempotency_key=str(idempotency_key)[:200] if idempotency_key else None,
            parent_surface_id=UUID(str(parent_surface_id)) if parent_surface_id else None,
            lifecycle_intent=(lifecycle_intent or "temporary")[:80],
            state_json=initial_state or {},
            last_activity_at=now,
            metadata_={"source": "create_surface"},
        )
        self.session.add(surface)
        await self.session.flush()

        try:
            rev = await self._write_revision(
                surface=surface,
                html=html,
                source_note=source_note,
                scopes=scopes,
            )
            surface.current_revision = rev.revision
            surface.status = SurfaceStatus.ACTIVE.value
            await self.session.flush()
        except Exception as e:
            surface.status = SurfaceStatus.FAILED.value
            surface.metadata_ = {**(surface.metadata_ or {}), "failure": str(e)[:300]}
            await self.session.flush()
            logger.error("surface_create_failed", surface_id=str(surface_id), error=str(e)[:200])
            return {"ok": False, "error": "surface_create_failed", "detail": str(e)[:200]}

        url = self.public_url(token)
        logger.info(
            "surface_created",
            surface_id=str(surface_id),
            principal_id=str(pid),
            revision=surface.current_revision,
            expires_at=expires.isoformat(),
        )
        return {
            "ok": True,
            "surface_id": str(surface_id),
            "title": surface.title,
            "status": SurfaceStatus.ACTIVE.value,
            "revision": surface.current_revision,
            "page_url": url,
            "expires_at": expires.isoformat(),
            "granted_scopes": scopes,
            "note": (
                "Share page_url with the learner. Same URL stays stable across updates/renames."
                if url
                else "Set PUBLIC_BASE_URL to issue openable links."
            ),
        }

    async def update(
        self,
        *,
        surface_id: UUID | str,
        principal_id: UUID | str,
        html: str | None = None,
        title: str | None = None,
        description: str | None = None,
        source_note: str | None = None,
        merge_state: dict[str, Any] | None = None,
        extend_hours: float | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Update existing surface in place — same URL, new revision if html provided.

        Concurrency: when expected_revision is provided, reject if current_revision
        differs (optimistic locking). Without it, still serializes via DB row update
        of current_revision after read.
        """
        surface = await self.session.get(Surface, UUID(str(surface_id)))
        if not surface or str(surface.principal_id) != str(principal_id):
            return {"ok": False, "error": "not_found"}
        if surface.status not in (
            SurfaceStatus.ACTIVE.value,
            SurfaceStatus.IDLE.value,
            SurfaceStatus.DORMANT.value,
        ):
            return {"ok": False, "error": "invalid_state", "status": surface.status}
        if expected_revision is not None and int(surface.current_revision or 0) != int(expected_revision):
            return {
                "ok": False,
                "error": "revision_conflict",
                "current_revision": surface.current_revision,
                "expected_revision": expected_revision,
            }

        if title is not None:
            surface.title = title[:500]
        if description is not None:
            surface.description = description
        if merge_state:
            st = dict(surface.state_json or {})
            st.update(merge_state)
            # size guard
            if len(str(st).encode()) <= MAX_STATE_JSON_BYTES:
                surface.state_json = st
        if extend_hours is not None:
            delta = self.policy.resolve_lifetime(extend_hours)
            surface.expires_at = max(surface.expires_at, utcnow() + delta)

        if html is not None:
            if len(html.encode("utf-8")) > MAX_ENTRY_BYTES:
                return {"ok": False, "error": "entry_too_large"}
            # Atomic optimistic concurrency: claim next revision only if still at expected.
            base_rev = int(surface.current_revision or 0)
            if expected_revision is not None:
                base_rev = int(expected_revision)
            next_rev = base_rev + 1
            cas = await self.session.execute(
                update(Surface)
                .where(
                    Surface.id == surface.id,
                    Surface.current_revision == base_rev,
                    Surface.principal_id == UUID(str(principal_id)),
                )
                .values(current_revision=next_rev)
            )
            if cas.rowcount != 1:
                await self.session.refresh(surface)
                return {
                    "ok": False,
                    "error": "revision_conflict",
                    "current_revision": surface.current_revision,
                    "expected_revision": base_rev,
                }
            # Keep in-memory object aligned with CAS result before writing revision bytes
            surface.current_revision = base_rev  # _write_revision increments from this
            rev = await self._write_revision(
                surface=surface,
                html=html,
                source_note=source_note,
                scopes=list(surface.granted_scopes or []),
            )
            # _write_revision sets current_revision = base+1; ensure matches CAS
            surface.current_revision = next_rev

        surface.last_activity_at = utcnow()
        if surface.status in (SurfaceStatus.IDLE.value, SurfaceStatus.DORMANT.value):
            surface.status = SurfaceStatus.ACTIVE.value
        await self.session.flush()

        logger.info(
            "surface_updated",
            surface_id=str(surface.id),
            revision=surface.current_revision,
            title_changed=title is not None,
            html_changed=html is not None,
        )
        return {
            "ok": True,
            "surface_id": str(surface.id),
            "title": surface.title,
            "status": surface.status,
            "revision": surface.current_revision,
            "expires_at": surface.expires_at.isoformat(),
            "note": "Surface updated in place — URL unchanged.",
        }

    async def _write_revision(
        self,
        *,
        surface: Surface,
        html: str,
        source_note: str | None,
        scopes: list[str],
    ) -> SurfaceRevision:
        revision_n = int(surface.current_revision or 0) + 1
        # Wrap AI HTML with runtime bridge (gateway client) + security shell
        entry = wrap_ai_html(
            html,
            surface_token_placeholder="{{SURFACE_TOKEN}}",
            api_base_placeholder="{{SURFACE_API_BASE}}",
            title=surface.title,
            scopes=scopes,
        )
        data = entry.encode("utf-8")
        checksum = hashlib.sha256(data).hexdigest()
        uri = store_bytes(
            surface.principal_id,
            f"surfaces/{surface.id}/r{revision_n}.html",
            data,
            content_type="text/html; charset=utf-8",
        )
        rev = SurfaceRevision(
            id=uuid4(),
            surface_id=surface.id,
            revision=revision_n,
            entry_uri=uri,
            content_type="text/html; charset=utf-8",
            size_bytes=len(data),
            checksum=checksum,
            manifest={
                "entry": "index.html",
                "runtime_version": "1.0",
                "required_scopes": scopes,
                "assets": [],
            },
            source_note=(source_note or None),
        )
        self.session.add(rev)
        surface.last_revision_at = utcnow()
        await self.session.flush()
        return rev

    async def resolve_by_token(self, token: str) -> tuple[Optional[Surface], Optional[str]]:
        if not token or len(token) < 16:
            return None, "not_found"
        th = hash_token(token)
        result = await self.session.execute(select(Surface).where(Surface.token_hash == th))
        surface = result.scalar_one_or_none()
        if not surface:
            return None, "not_found"
        now = utcnow()
        if surface.status == SurfaceStatus.REVOKED.value or surface.revoked_at:
            return surface, "revoked"
        if surface.status in (
            SurfaceStatus.EXPIRED.value,
            SurfaceStatus.CLEANED.value,
            SurfaceStatus.CLEANUP_PENDING.value,
        ) or (surface.expires_at and surface.expires_at <= now):
            if surface.status == SurfaceStatus.ACTIVE.value and can_transition(
                surface.status, SurfaceStatus.EXPIRED
            ):
                surface.status = SurfaceStatus.EXPIRED.value
                await self.session.flush()
            return surface, "expired"
        if surface.status == SurfaceStatus.FAILED.value:
            return surface, "failed"
        if surface.status not in (
            SurfaceStatus.ACTIVE.value,
            SurfaceStatus.IDLE.value,
            SurfaceStatus.DORMANT.value,
        ):
            return surface, "not_found"
        return surface, None

    async def load_entry_html(self, surface: Surface, *, public_token: str) -> Optional[bytes]:
        if not surface.current_revision:
            return None
        result = await self.session.execute(
            select(SurfaceRevision).where(
                SurfaceRevision.surface_id == surface.id,
                SurfaceRevision.revision == surface.current_revision,
            )
        )
        rev = result.scalar_one_or_none()
        if not rev or not rev.entry_uri:
            return None
        try:
            data = read_bytes(rev.entry_uri)
        except Exception as e:
            logger.error("surface_storage_miss", surface_id=str(surface.id), error=str(e)[:120])
            return None
        # Inject actual public token + API base for gateway (placeholder replacement)
        text = data.decode("utf-8", errors="replace")
        text = text.replace("{{SURFACE_TOKEN}}", public_token)
        text = text.replace("{{SURFACE_API_BASE}}", self.api_base_for_bridge())
        return text.encode("utf-8")

    async def record_access(self, surface: Surface) -> None:
        now = utcnow()
        surface.access_count = int(surface.access_count or 0) + 1
        surface.last_activity_at = now
        surface.last_opened_at = now
        if surface.status in (SurfaceStatus.IDLE.value, SurfaceStatus.DORMANT.value):
            surface.status = SurfaceStatus.ACTIVE.value

    async def get_state(self, surface: Surface) -> dict[str, Any]:
        return dict(surface.state_json or {})

    async def set_state(self, surface: Surface, state: dict[str, Any]) -> dict[str, Any]:
        if len(str(state).encode()) > MAX_STATE_JSON_BYTES:
            return {"ok": False, "error": "state_too_large"}
        surface.state_json = state
        surface.last_activity_at = utcnow()
        await self.session.flush()
        return {"ok": True}

    async def patch_state(self, surface: Surface, patch: dict[str, Any]) -> dict[str, Any]:
        st = dict(surface.state_json or {})
        st.update(patch)
        return await self.set_state(surface, st)

    async def record_event(
        self,
        *,
        surface: Surface,
        event_type: str,
        payload: dict[str, Any] | None = None,
        session_id: UUID | None = None,
        source: str = "browser",
    ) -> None:
        ev = SurfaceEvent(
            id=uuid4(),
            surface_id=surface.id,
            principal_id=surface.principal_id,
            session_id=session_id,
            event_type=(event_type or "interaction")[:80],
            payload=payload or {},
            revision=surface.current_revision,
            source=source[:40],
        )
        self.session.add(ev)
        now = utcnow()
        surface.last_activity_at = now
        if event_type in ("interaction", "learner_interaction", "state_changed", "save_requested"):
            surface.last_learner_interaction_at = now
        await self.session.flush()

    async def list_for_principal(
        self,
        principal_id: UUID | str,
        *,
        limit: int = 20,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        q = select(Surface).where(Surface.principal_id == UUID(str(principal_id)))
        if active_only:
            q = q.where(
                Surface.status.in_(
                    (
                        SurfaceStatus.ACTIVE.value,
                        SurfaceStatus.IDLE.value,
                        SurfaceStatus.DORMANT.value,
                    )
                )
            )
        q = q.order_by(Surface.last_activity_at.desc().nullslast(), Surface.created_at.desc()).limit(
            min(50, max(1, limit))
        )
        result = await self.session.execute(q)
        rows = []
        for s in result.scalars():
            rows.append(
                {
                    "surface_id": str(s.id),
                    "title": s.title,
                    "description": s.description,
                    "status": s.status,
                    "revision": s.current_revision,
                    "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                    "last_activity_at": s.last_activity_at.isoformat() if s.last_activity_at else None,
                    "lifecycle_intent": s.lifecycle_intent,
                    "work_id": str(s.work_id) if s.work_id else None,
                }
            )
        return rows

    async def revoke(self, surface_id: UUID | str, principal_id: UUID | str) -> dict[str, Any]:
        surface = await self.session.get(Surface, UUID(str(surface_id)))
        if not surface or str(surface.principal_id) != str(principal_id):
            return {"ok": False, "error": "not_found"}
        if not can_transition(surface.status, SurfaceStatus.REVOKED):
            return {"ok": False, "error": "invalid_state", "status": surface.status}
        surface.status = SurfaceStatus.REVOKED.value
        surface.revoked_at = utcnow()
        await self.session.flush()
        logger.info("surface_revoked", surface_id=str(surface.id))
        return {"ok": True, "status": SurfaceStatus.REVOKED.value}

    async def expire_due(self, limit: int = 200) -> int:
        now = utcnow()
        result = await self.session.execute(
            select(Surface)
            .where(
                Surface.status.in_(
                    (
                        SurfaceStatus.ACTIVE.value,
                        SurfaceStatus.IDLE.value,
                        SurfaceStatus.DORMANT.value,
                    )
                ),
                Surface.expires_at <= now,
            )
            .limit(limit)
        )
        rows = list(result.scalars())
        for s in rows:
            s.status = SurfaceStatus.EXPIRED.value
        if rows:
            await self.session.flush()
            logger.info("surfaces_expired", count=len(rows))
        return len(rows)

    async def cleanup_expired(self, limit: int = 50) -> int:
        now = utcnow()
        grace = now - self.policy.cleanup_grace
        result = await self.session.execute(
            select(Surface)
            .where(
                Surface.status.in_(
                    (
                        SurfaceStatus.EXPIRED.value,
                        SurfaceStatus.REVOKED.value,
                        SurfaceStatus.FAILED.value,
                    )
                ),
                Surface.cleaned_at.is_(None),
                Surface.expires_at <= grace,
            )
            .limit(limit)
        )
        rows = list(result.scalars())
        cleaned = 0
        for s in rows:
            # Mark cleanup in progress; only mark CLEANED if all revision bytes removed.
            if can_transition(s.status, SurfaceStatus.CLEANUP_PENDING):
                s.status = SurfaceStatus.CLEANUP_PENDING.value
            revs = await self.session.execute(
                select(SurfaceRevision).where(SurfaceRevision.surface_id == s.id)
            )
            failed = False
            for rev in revs.scalars():
                if rev.entry_uri:
                    try:
                        delete_uri(rev.entry_uri)
                        rev.entry_uri = None
                    except Exception as e:
                        failed = True
                        logger.error(
                            "surface_cleanup_delete_failed",
                            surface_id=str(s.id),
                            error=str(e)[:160],
                        )
            if failed:
                # Leave in cleanup_pending / prior state for retry — do NOT claim success.
                logger.warning("surface_cleanup_incomplete", surface_id=str(s.id))
                continue
            s.status = SurfaceStatus.CLEANED.value
            s.cleaned_at = now
            cleaned += 1
        if cleaned:
            await self.session.flush()
            logger.info("surfaces_cleaned", count=cleaned)
        return cleaned

    async def mark_idle_dormant(self, limit: int = 200) -> int:
        now = utcnow()
        n = 0
        # active → idle
        result = await self.session.execute(
            select(Surface)
            .where(
                Surface.status == SurfaceStatus.ACTIVE.value,
                Surface.last_activity_at.is_not(None),
                Surface.last_activity_at <= now - self.policy.idle_after,
            )
            .limit(limit)
        )
        for s in result.scalars():
            s.status = SurfaceStatus.IDLE.value
            n += 1
        # idle → dormant
        result = await self.session.execute(
            select(Surface)
            .where(
                Surface.status == SurfaceStatus.IDLE.value,
                Surface.last_activity_at.is_not(None),
                Surface.last_activity_at <= now - self.policy.dormant_after,
            )
            .limit(limit)
        )
        for s in result.scalars():
            s.status = SurfaceStatus.DORMANT.value
            n += 1
        if n:
            await self.session.flush()
        return n


    async def inspect(self, surface_id: UUID | str, principal_id: UUID | str) -> dict[str, Any]:
        surface = await self.session.get(Surface, UUID(str(surface_id)))
        if not surface or str(surface.principal_id) != str(principal_id):
            return {"ok": False, "error": "not_found"}
        return {
            "ok": True,
            "surface_id": str(surface.id),
            "title": surface.title,
            "description": surface.description,
            "status": surface.status,
            "revision": surface.current_revision,
            "expires_at": surface.expires_at.isoformat() if surface.expires_at else None,
            "retention_requested": bool(surface.retention_requested),
            "lifecycle_intent": surface.lifecycle_intent,
            "related_surface_ids": list(surface.related_surface_ids or []),
            "last_activity_at": surface.last_activity_at.isoformat() if surface.last_activity_at else None,
            "last_opened_at": surface.last_opened_at.isoformat() if surface.last_opened_at else None,
            "last_learner_interaction_at": (
                surface.last_learner_interaction_at.isoformat()
                if surface.last_learner_interaction_at
                else None
            ),
            "last_ai_request_at": surface.last_ai_request_at.isoformat() if surface.last_ai_request_at else None,
            "last_ai_response_at": surface.last_ai_response_at.isoformat() if surface.last_ai_response_at else None,
            "work_id": str(surface.work_id) if surface.work_id else None,
        }

    async def set_retention(
        self, surface_id: UUID | str, principal_id: UUID | str, *, keep: bool = True
    ) -> dict[str, Any]:
        surface = await self.session.get(Surface, UUID(str(surface_id)))
        if not surface or str(surface.principal_id) != str(principal_id):
            return {"ok": False, "error": "not_found"}
        surface.retention_requested = bool(keep)
        if keep:
            # Extend toward max policy without exceeding it
            surface.expires_at = utcnow() + self.policy.max_lifetime
            surface.lifecycle_intent = "retain"
        await self.session.flush()
        return {
            "ok": True,
            "surface_id": str(surface.id),
            "retention_requested": surface.retention_requested,
            "expires_at": surface.expires_at.isoformat(),
        }

    async def relate(
        self, surface_id: UUID | str, related_id: UUID | str, principal_id: UUID | str
    ) -> dict[str, Any]:
        surface = await self.session.get(Surface, UUID(str(surface_id)))
        other = await self.session.get(Surface, UUID(str(related_id)))
        if (
            not surface
            or not other
            or str(surface.principal_id) != str(principal_id)
            or str(other.principal_id) != str(principal_id)
        ):
            return {"ok": False, "error": "not_found"}
        ids = list(surface.related_surface_ids or [])
        rid = str(other.id)
        if rid not in ids:
            ids.append(rid)
        surface.related_surface_ids = ids
        await self.session.flush()
        return {"ok": True, "related_surface_ids": ids}

    def _opaque_request_token(self, surface_id: UUID | str, idempotency_key: str | None) -> str:
        """Opaque poll token. Deterministic when idempotency_key is set so retries re-derive it."""
        if idempotency_key:
            import hmac
            import hashlib
            import base64
            from wax.surfaces.tokens import _secret
            dig = hmac.new(
                _secret(),
                f"{surface_id}:{str(idempotency_key)[:200]}".encode(),
                hashlib.sha256,
            ).digest()
            return base64.urlsafe_b64encode(dig).decode("ascii").rstrip("=")[:43]
        return generate_token(24)

    async def accept_ai_request(
        self,
        *,
        surface: Surface,
        message: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        from wax.db.models import Work

        if idempotency_key:
            existing = await self.session.execute(
                select(SurfaceAiRequest).where(
                    SurfaceAiRequest.surface_id == surface.id,
                    SurfaceAiRequest.idempotency_key == str(idempotency_key)[:200],
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                # Re-derive the same opaque request_id so the client can keep polling.
                req_token = self._opaque_request_token(surface.id, idempotency_key)
                return {
                    "ok": True,
                    "request_id": req_token,
                    "status": row.status,
                    "idempotent": True,
                    "revision": surface.current_revision,
                }

        req_token = self._opaque_request_token(surface.id, idempotency_key)
        req = SurfaceAiRequest(
            id=uuid4(),
            surface_id=surface.id,
            principal_id=surface.principal_id,
            request_token_hash=hash_token(req_token),
            status="accepted",
            message_preview=(message or "")[:240],
            idempotency_key=str(idempotency_key)[:200] if idempotency_key else None,
        )
        work = Work(
            id=uuid4(),
            principal_id=surface.principal_id,
            kind="surface_ai",
            status="queued",
            input_payload={
                "source": "surface",
                "channel": "surface",
                "surface_id": str(surface.id),
                "revision": surface.current_revision,
                "user_text": message,
                "text": message,
                "context": context or {},
                "surface_title": surface.title,
                "surface_state": surface.state_json or {},
                "request_id": str(req.id),
            },
        )
        self.session.add(work)
        req.work_id = work.id
        self.session.add(req)
        surface.last_ai_request_at = utcnow()
        surface.last_activity_at = utcnow()
        await self.session.flush()
        await self.record_event(
            surface=surface,
            event_type="ai_requested",
            payload={"request_id": str(req.id)},
        )
        logger.info("surface_ai_accepted", surface_id=str(surface.id), request_id=str(req.id))
        return {
            "ok": True,
            "request_id": req_token,
            "status": "accepted",
            "revision": surface.current_revision,
        }

    async def get_ai_request_by_token(self, token: str, surface: Surface) -> SurfaceAiRequest | None:
        if not token or len(token) < 16:
            return None
        result = await self.session.execute(
            select(SurfaceAiRequest).where(
                SurfaceAiRequest.surface_id == surface.id,
                SurfaceAiRequest.request_token_hash == hash_token(token),
            )
        )
        return result.scalar_one_or_none()

    async def apply_ai_result(
        self,
        *,
        request_row_id: UUID | str,
        reply: str | None,
        tools: list | None = None,
        error: str | None = None,
    ) -> None:
        row = await self.session.get(SurfaceAiRequest, UUID(str(request_row_id)))
        if not row:
            return
        surface = await self.session.get(Surface, row.surface_id)
        if error:
            row.status = "failed"
            row.error = str(error)[:300]
        else:
            row.status = "completed"
            row.reply_preview = (reply or "")[:4000]
            row.result = {"tools": tools or []}
        if surface:
            surface.last_ai_response_at = utcnow()
            surface.last_activity_at = utcnow()
            # Store latest reply in surface state under reserved key (not memory)
            st = dict(surface.state_json or {})
            st["_wax_last_ai"] = {
                "status": row.status,
                "reply": (reply or "")[:4000],
                "revision": surface.current_revision,
            }
            if len(str(st).encode()) <= MAX_STATE_JSON_BYTES:
                surface.state_json = st
            await self.record_event(
                surface=surface,
                event_type="ai_completed" if not error else "ai_failed",
                payload={"status": row.status},
                source="worker",
            )
        await self.session.flush()

    def revision_status(self, surface: Surface) -> dict[str, Any]:
        return {
            "ok": True,
            "revision": surface.current_revision,
            "status": surface.status,
            "title": surface.title,
        }
