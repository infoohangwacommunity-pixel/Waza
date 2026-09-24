"""
Publication lifecycle service.

Create → store immutable HTML snapshot → issue opaque URL → expire/revoke → cleanup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.artifacts.storage import read_bytes, store_bytes
from wax.config import get_settings
from wax.db.models import Artifact, Publication
from wax.observability.logging import get_logger
from wax.publication.renderer import RENDERER_VERSION, render_document, render_expired_page
from wax.publication.schema import PublicationDocument, document_to_dict, normalize_document
from wax.publication.tokens import generate_public_token, hash_token, tokens_match, utcnow

logger = get_logger(__name__)

DEFAULT_LIFETIME_HOURS = 48.0
MAX_LIFETIME_HOURS = 168.0  # 7 days hard ceiling
CLEANUP_GRACE_HOURS = 24.0


class PublicationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    def _lifetime(self, preferred_hours: Optional[float]) -> timedelta:
        hours = DEFAULT_LIFETIME_HOURS
        if preferred_hours is not None:
            try:
                hours = float(preferred_hours)
            except (TypeError, ValueError):
                hours = DEFAULT_LIFETIME_HOURS
        hours = max(0.25, min(MAX_LIFETIME_HOURS, hours))
        return timedelta(hours=hours)

    def public_url(self, token: str) -> Optional[str]:
        base = (self.settings.public_base_url or "").rstrip("/")
        if not base:
            return None
        return f"{base}/p/{token}"

    async def create(
        self,
        *,
        principal_id: UUID | str,
        document: dict[str, Any] | PublicationDocument,
        work_id: Optional[UUID | str] = None,
        parent_publication_id: Optional[UUID | str] = None,
        artifact_download_urls: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """
        Validate semantic doc → render → store snapshot → persist Publication.
        Returns public URL + metadata for the tutor tool.
        """
        pid = UUID(str(principal_id))
        doc = normalize_document(document)
        now = utcnow()
        expires = now + self._lifetime(doc.preferred_lifetime_hours)

        # Resolve artifact ownership for referenced artifacts
        artifact_ids: list[str] = []
        for block in doc.blocks:
            ref = None
            if block.artifact and block.artifact.artifact_id:
                ref = block.artifact.artifact_id
            elif block.media and block.media.artifact_id:
                ref = block.media.artifact_id
            if ref:
                artifact_ids.append(str(ref))
        # Deduplicate + verify ownership
        verified_ids: list[str] = []
        for aid in dict.fromkeys(artifact_ids):
            try:
                art = await self.session.get(Artifact, UUID(aid))
            except Exception:
                art = None
            if art and str(art.principal_id) == str(pid):
                verified_ids.append(aid)
            else:
                logger.warning("publication_artifact_ref_denied", artifact_id=aid, principal_id=str(pid))

        html = render_document(doc, expires_at_iso=expires.isoformat())
        # Inject artifact download URLs (principal-bound tokens supplied by caller or empty)
        if artifact_download_urls:
            for aid, url in artifact_download_urls.items():
                html = html.replace(f"{{{{ARTIFACT_URL:{aid}}}}}", url)
        # Strip unresolved placeholders to safe #
        import re
        html = re.sub(r"\{\{ARTIFACT_URL:[^}]+\}\}", "#", html)

        pub_id = uuid4()
        token = generate_public_token(32)
        token_h = hash_token(token)
        uri = store_bytes(
            pid,
            f"publications/{pub_id}.html",
            html.encode("utf-8"),
            content_type="text/html; charset=utf-8",
        )

        pub = Publication(
            id=pub_id,
            principal_id=pid,
            work_id=UUID(str(work_id)) if work_id else None,
            token_hash=token_h,
            title=doc.title[:500],
            status="active",
            expires_at=expires,
            schema_version=doc.schema_version,
            renderer_version=RENDERER_VERSION,
            semantic=document_to_dict(doc),
            storage_uri=uri,
            content_type="text/html; charset=utf-8",
            size_bytes=len(html.encode("utf-8")),
            parent_publication_id=UUID(str(parent_publication_id)) if parent_publication_id else None,
            artifact_ids=verified_ids,
            metadata_={"source": "publish_web_surface"},
        )
        self.session.add(pub)
        await self.session.flush()

        url = self.public_url(token)
        logger.info(
            "publication_created",
            publication_id=str(pub_id),
            principal_id=str(pid),
            size_bytes=pub.size_bytes,
            expires_at=expires.isoformat(),
        )
        return {
            "ok": True,
            "publication_id": str(pub_id),
            "title": doc.title,
            "status": "active",
            "page_url": url,
            "expires_at": expires.isoformat(),
            "schema_version": doc.schema_version,
            "renderer_version": RENDERER_VERSION,
            "note": (
                "Share page_url with the learner. Temporary browser surface — not a chat replacement."
                if url
                else "Set PUBLIC_BASE_URL so openable links can be issued."
            ),
        }

    async def resolve_by_token(self, token: str) -> tuple[Optional[Publication], Optional[str]]:
        """
        Returns (publication, deny_reason).
        deny_reason: None if active & valid; else expired|revoked|not_found|forbidden
        """
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
        if pub.status == "revoked" or pub.revoked_at:
            return pub, "revoked"
        if pub.status in ("expired", "cleaned") or (pub.expires_at and pub.expires_at <= now):
            if pub.status == "active":
                pub.status = "expired"
                await self.session.flush()
            return pub, "expired"
        if pub.status != "active":
            return pub, "not_found"
        return pub, None

    async def load_html(self, pub: Publication) -> Optional[bytes]:
        if not pub.storage_uri:
            return None
        try:
            return read_bytes(pub.storage_uri)
        except Exception as e:
            logger.error("publication_storage_miss", publication_id=str(pub.id), error=str(e))
            return None

    async def record_access(self, pub: Publication) -> None:
        pub.access_count = int(pub.access_count or 0) + 1
        pub.last_accessed_at = utcnow()
        # best-effort; caller may commit

    async def revoke(self, publication_id: UUID | str, principal_id: UUID | str) -> dict[str, Any]:
        pub = await self.session.get(Publication, UUID(str(publication_id)))
        if not pub or str(pub.principal_id) != str(principal_id):
            return {"ok": False, "error": "not_found"}
        pub.status = "revoked"
        pub.revoked_at = utcnow()
        await self.session.flush()
        logger.info("publication_revoked", publication_id=str(pub.id))
        return {"ok": True, "status": "revoked"}

    async def expire_due(self, limit: int = 200) -> int:
        """Mark active publications past expires_at as expired. Idempotent."""
        now = utcnow()
        result = await self.session.execute(
            select(Publication)
            .where(Publication.status == "active", Publication.expires_at <= now)
            .limit(limit)
        )
        rows = list(result.scalars())
        for pub in rows:
            pub.status = "expired"
        if rows:
            await self.session.flush()
            logger.info("publications_expired", count=len(rows))
        return len(rows)

    async def cleanup_expired(self, limit: int = 50) -> int:
        """
        Remove storage bytes for expired/revoked publications past grace period.
        Does NOT delete shared Artifacts — only publication HTML snapshots.
        """
        from wax.artifacts.storage import get_storage

        now = utcnow()
        grace_cutoff = now - timedelta(hours=CLEANUP_GRACE_HOURS)
        result = await self.session.execute(
            select(Publication)
            .where(
                Publication.status.in_(("expired", "revoked")),
                Publication.cleaned_at.is_(None),
                Publication.expires_at <= grace_cutoff,
            )
            .limit(limit)
        )
        rows = list(result.scalars())
        storage = get_storage()
        cleaned = 0
        for pub in rows:
            if pub.storage_uri:
                try:
                    # extract key from uri
                    uri = pub.storage_uri
                    key = uri
                    if uri.startswith("local://"):
                        key = uri[len("local://") :]
                    elif uri.startswith("s3://"):
                        parts = uri[5:].split("/", 1)
                        key = parts[1] if len(parts) > 1 else parts[0]
                    storage.delete(key)
                except Exception as e:
                    logger.warning(
                        "publication_cleanup_storage_error",
                        publication_id=str(pub.id),
                        error=str(e),
                    )
                    continue
            pub.status = "cleaned"
            pub.cleaned_at = now
            pub.storage_uri = None
            cleaned += 1
        if cleaned:
            await self.session.flush()
            logger.info("publications_cleaned", count=cleaned)
        return cleaned
