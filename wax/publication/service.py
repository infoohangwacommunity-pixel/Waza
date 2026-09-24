"""
Publication lifecycle service — transactional creation, state machine, cleanup.

AI supplies semantic material. This service validates, plans, renders, stores,
issues capability URLs, and manages lifecycle. Deterministic; no LLM.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.artifacts.access import make_download_token, public_download_url
from wax.artifacts.storage import delete_uri, read_bytes, store_bytes
from wax.config import get_settings
from wax.db.models import Artifact, Publication
from wax.observability.logging import get_logger
from wax.publication.planner import plan_document
from wax.publication.policy import (
    DEFAULT_POLICY,
    LifecyclePolicy,
    PublicationStatus,
    can_transition,
    utcnow,
)
from wax.publication.renderer import RENDERER_VERSION, render_document, render_expired_page
from wax.publication.schema import (
    SCHEMA_VERSION,
    collect_artifact_ids,
    document_to_dict,
    normalize_document,
)
from wax.publication.tokens import generate_public_token, hash_token

logger = get_logger(__name__)


class PublicationService:
    def __init__(self, session: AsyncSession, policy: LifecyclePolicy | None = None):
        self.session = session
        self.settings = get_settings()
        self.policy = policy or DEFAULT_POLICY

    def public_url(self, token: str) -> Optional[str]:
        base = (self.settings.public_base_url or "").rstrip("/")
        if not base:
            return None
        return f"{base}/p/{token}"

    def _idempotency_lookup_key(self, principal_id: str, key: str) -> str:
        return hashlib.sha256(f"{principal_id}:{key}".encode()).hexdigest()

    async def create(
        self,
        *,
        principal_id: UUID | str,
        document: dict[str, Any],
        work_id: Optional[UUID | str] = None,
        parent_publication_id: Optional[UUID | str] = None,
        idempotency_key: Optional[str] = None,
        artifact_download_urls: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """
        Validate → plan → render → store snapshot → persist as ACTIVE.

        Uses preparing state + idempotency to survive retries and partial failures.
        """
        pid = UUID(str(principal_id))
        now = utcnow()

        # Idempotency: return existing if same key within active/preparing window
        if idempotency_key:
            ik = self._idempotency_lookup_key(str(pid), str(idempotency_key)[:200])
            existing = await self.session.execute(
                select(Publication).where(
                    Publication.principal_id == pid,
                    Publication.token_hash == ik,  # reuse field only if we stored key hash separately
                )
            )
            # Better: look up via metadata idempotency_key
            existing = await self.session.execute(
                select(Publication)
                .where(Publication.principal_id == pid)
                .where(Publication.status.in_(("preparing", "active")))
                .order_by(Publication.created_at.desc())
                .limit(20)
            )
            for row in existing.scalars():
                meta = row.metadata_ or {}
                if meta.get("idempotency_key") == str(idempotency_key)[:200]:
                    # Re-issue URL requires original token — not stored; return metadata only
                    logger.info(
                        "publication_idempotent_hit",
                        publication_id=str(row.id),
                        principal_id=str(pid),
                    )
                    return {
                        "ok": True,
                        "publication_id": str(row.id),
                        "title": row.title,
                        "status": row.status,
                        "page_url": None,  # token not recoverable; client should not retry blindly
                        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                        "idempotent": True,
                        "note": "Publication already exists for this request.",
                    }

        try:
            doc = normalize_document(document)
        except Exception as e:
            logger.warning("publication_validation_failed", error=str(e)[:200])
            return {"ok": False, "error": "validation_failed", "detail": str(e)[:300]}

        expires = self.policy.expires_at(now, doc.preferred_lifetime_hours)
        plan = plan_document(doc)

        # Resolve artifact ownership
        requested_ids = collect_artifact_ids(doc)
        verified: list[str] = []
        enriched_urls: dict[str, str] = dict(artifact_download_urls or {})
        for aid in requested_ids:
            try:
                art = await self.session.get(Artifact, UUID(aid))
            except Exception:
                art = None
            if not art or str(art.principal_id) != str(pid):
                logger.warning(
                    "publication_artifact_ref_denied",
                    artifact_id=aid,
                    principal_id=str(pid),
                )
                continue
            verified.append(aid)
            if aid not in enriched_urls:
                try:
                    tok = make_download_token(str(art.id), str(pid), ttl_seconds=int(self.policy.max_lifetime.total_seconds()))
                    url = public_download_url(str(art.id), tok)
                    if url:
                        enriched_urls[aid] = url
                except Exception:
                    pass
            # Enrich artifact node metadata from DB when missing
            self._enrich_artifact_nodes(doc, art)

        # Asset manifest
        asset_manifest = {
            "logo": "embedded",
            "artifacts": verified,
            "media_count": plan.media_count,
            "renderer_version": RENDERER_VERSION,
            "schema_version": SCHEMA_VERSION,
        }

        pub_id = uuid4()
        token = generate_public_token(32)
        token_h = hash_token(token)

        # DB first in preparing state (recovery-friendly)
        pub = Publication(
            id=pub_id,
            principal_id=pid,
            work_id=UUID(str(work_id)) if work_id else None,
            token_hash=token_h,
            title=doc.title[:500],
            status=PublicationStatus.PREPARING.value,
            expires_at=expires,
            schema_version=SCHEMA_VERSION,
            renderer_version=RENDERER_VERSION,
            semantic=document_to_dict(doc),
            storage_uri=None,
            content_type="text/html; charset=utf-8",
            size_bytes=None,
            parent_publication_id=UUID(str(parent_publication_id)) if parent_publication_id else None,
            artifact_ids=verified,
            metadata_={
                "source": "publish_web_surface",
                "idempotency_key": str(idempotency_key)[:200] if idempotency_key else None,
                "asset_manifest": asset_manifest,
                "plan": {
                    "show_toc": plan.show_toc,
                    "heading_count": plan.heading_count,
                    "table_count": plan.table_count,
                    "estimated_blocks": plan.estimated_blocks,
                },
            },
        )
        self.session.add(pub)
        await self.session.flush()

        try:
            html = render_document(
                doc,
                expires_at_iso=expires.isoformat(),
                artifact_urls=enriched_urls,
                plan=plan,
            )
            data = html.encode("utf-8")
            uri = store_bytes(
                pid,
                f"publications/{pub_id}.html",
                data,
                content_type="text/html; charset=utf-8",
            )
            pub.storage_uri = uri
            pub.size_bytes = len(data)
            pub.status = PublicationStatus.ACTIVE.value
            await self.session.flush()
        except Exception as e:
            pub.status = PublicationStatus.FAILED.value
            pub.metadata_ = {**(pub.metadata_ or {}), "failure": str(e)[:300]}
            await self.session.flush()
            logger.error(
                "publication_create_failed",
                publication_id=str(pub_id),
                error=str(e)[:200],
            )
            return {"ok": False, "error": "publication_failed", "detail": str(e)[:200]}

        url = self.public_url(token)
        logger.info(
            "publication_created",
            publication_id=str(pub_id),
            principal_id=str(pid),
            size_bytes=pub.size_bytes,
            expires_at=expires.isoformat(),
            show_toc=plan.show_toc,
        )
        return {
            "ok": True,
            "publication_id": str(pub_id),
            "title": doc.title,
            "status": PublicationStatus.ACTIVE.value,
            "page_url": url,
            "expires_at": expires.isoformat(),
            "schema_version": SCHEMA_VERSION,
            "renderer_version": RENDERER_VERSION,
            "note": (
                "Share page_url with the learner. Temporary browser surface — not a chat replacement."
                if url
                else "Set PUBLIC_BASE_URL so openable links can be issued."
            ),
        }

    def _enrich_artifact_nodes(self, doc, art: Artifact) -> None:
        from wax.publication.schema import Node, NodeType

        def walk(nodes):
            for n in nodes:
                if n.artifact and str(n.artifact.artifact_id) == str(art.id):
                    if not n.artifact.filename and art.title:
                        n.artifact.filename = art.title
                    if not n.artifact.content_type and art.content_type:
                        n.artifact.content_type = art.content_type
                    if not n.artifact.size_bytes and art.size_bytes:
                        n.artifact.size_bytes = art.size_bytes
                    if not n.artifact.label and art.title:
                        n.artifact.label = art.title
                if n.children:
                    walk(n.children)
                if n.columns:
                    for col in n.columns:
                        walk(col)

        walk(doc.nodes)

    async def resolve_by_token(self, token: str) -> tuple[Optional[Publication], Optional[str]]:
        if not token or len(token) < 16:
            return None, "not_found"
        th = hash_token(token)
        result = await self.session.execute(
            select(Publication).where(Publication.token_hash == th)
        )
        pub = result.scalar_one_or_none()
        if not pub:
            return None, "not_found"
        now = utcnow()
        if pub.status in (
            PublicationStatus.REVOKED.value,
        ) or pub.revoked_at:
            return pub, "revoked"
        if pub.status in (
            PublicationStatus.EXPIRED.value,
            PublicationStatus.CLEANED.value,
            PublicationStatus.CLEANUP_PENDING.value,
        ) or (pub.expires_at and pub.expires_at <= now):
            if pub.status == PublicationStatus.ACTIVE.value:
                if can_transition(pub.status, PublicationStatus.EXPIRED):
                    pub.status = PublicationStatus.EXPIRED.value
                    await self.session.flush()
            return pub, "expired"
        if pub.status == PublicationStatus.FAILED.value:
            return pub, "failed"
        if pub.status != PublicationStatus.ACTIVE.value:
            return pub, "not_found"
        return pub, None

    async def load_html(self, pub: Publication) -> Optional[bytes]:
        if not pub.storage_uri:
            return None
        try:
            return read_bytes(pub.storage_uri)
        except Exception as e:
            logger.error("publication_storage_miss", publication_id=str(pub.id), error=str(e)[:120])
            return None

    async def record_access(self, pub: Publication) -> None:
        pub.access_count = int(pub.access_count or 0) + 1
        pub.last_accessed_at = utcnow()

    async def revoke(self, publication_id: UUID | str, principal_id: UUID | str) -> dict[str, Any]:
        pub = await self.session.get(Publication, UUID(str(publication_id)))
        if not pub or str(pub.principal_id) != str(principal_id):
            return {"ok": False, "error": "not_found"}
        if not can_transition(pub.status, PublicationStatus.REVOKED):
            return {"ok": False, "error": "invalid_state", "status": pub.status}
        pub.status = PublicationStatus.REVOKED.value
        pub.revoked_at = utcnow()
        await self.session.flush()
        logger.info("publication_revoked", publication_id=str(pub.id))
        return {"ok": True, "status": PublicationStatus.REVOKED.value}

    async def expire_due(self, limit: int = 200, *, now: Optional[datetime] = None) -> int:
        now = now or utcnow()
        result = await self.session.execute(
            select(Publication)
            .where(
                Publication.status == PublicationStatus.ACTIVE.value,
                Publication.expires_at <= now,
            )
            .limit(limit)
        )
        rows = list(result.scalars())
        for pub in rows:
            if can_transition(pub.status, PublicationStatus.EXPIRED):
                pub.status = PublicationStatus.EXPIRED.value
        if rows:
            await self.session.flush()
            logger.info("publications_expired", count=len(rows))
        return len(rows)

    async def cleanup_expired(self, limit: int = 50, *, now: Optional[datetime] = None) -> int:
        """Remove snapshot bytes for expired/revoked past grace. Never deletes shared Artifacts."""
        now = now or utcnow()
        grace_cutoff = now - self.policy.cleanup_grace
        result = await self.session.execute(
            select(Publication)
            .where(
                Publication.status.in_(
                    (
                        PublicationStatus.EXPIRED.value,
                        PublicationStatus.REVOKED.value,
                        PublicationStatus.FAILED.value,
                    )
                ),
                Publication.cleaned_at.is_(None),
                Publication.expires_at <= grace_cutoff,
            )
            .limit(limit)
        )
        rows = list(result.scalars())
        cleaned = 0
        for pub in rows:
            if pub.storage_uri:
                delete_uri(pub.storage_uri)
            if can_transition(pub.status, PublicationStatus.CLEANED) or pub.status in (
                PublicationStatus.EXPIRED.value,
                PublicationStatus.REVOKED.value,
                PublicationStatus.FAILED.value,
            ):
                pub.status = PublicationStatus.CLEANED.value
            pub.cleaned_at = now
            pub.storage_uri = None
            cleaned += 1
        if cleaned:
            await self.session.flush()
            logger.info("publications_cleaned", count=cleaned)
        return cleaned

    async def recover_failed(self, limit: int = 20) -> int:
        """Mark long-stuck preparing publications as failed for cleanup."""
        now = utcnow()
        cutoff = now - timedelta(hours=1)
        result = await self.session.execute(
            select(Publication)
            .where(
                Publication.status == PublicationStatus.PREPARING.value,
                Publication.created_at <= cutoff,
            )
            .limit(limit)
        )
        rows = list(result.scalars())
        for pub in rows:
            pub.status = PublicationStatus.FAILED.value
            if pub.storage_uri:
                delete_uri(pub.storage_uri)
                pub.storage_uri = None
        if rows:
            await self.session.flush()
            logger.info("publications_recovered_stuck", count=len(rows))
        return len(rows)
