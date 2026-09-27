"""
Turn-level inbound asset manifest.

Channel ingestion → staged file → probe → InboundAsset entries.
CI and Tutor reason over structured metadata, not MIME routing tables.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class InboundAsset:
    id: str
    kind: str = "unknown"
    mime: str | None = None
    path: str | None = None
    size_bytes: int = 0
    duration_sec: float | None = None
    width: int | None = None
    height: int | None = None
    page_count: int | None = None
    capabilities: list[str] = field(default_factory=list)
    status: str = "ready"  # pending | ready | failed | unsupported
    relevance: str = "unknown"  # relevant | irrelevant | unknown
    external_media_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    probe: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def render_line(self) -> str:
        caps = ",".join(self.capabilities) if self.capabilities else "inspect"
        bits = [f"id={self.id}", f"kind={self.kind}", f"caps=[{caps}]"]
        if self.mime:
            bits.append(f"mime={self.mime}")
        if self.duration_sec is not None:
            bits.append(f"duration_s={self.duration_sec}")
        if self.width and self.height:
            bits.append(f"{self.width}x{self.height}")
        if self.page_count:
            bits.append(f"pages={self.page_count}")
        if self.status != "ready":
            bits.append(f"status={self.status}")
        return "  - " + " ".join(bits)


@dataclass
class AssetManifest:
    assets: list[InboundAsset] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"assets": [a.to_dict() for a in self.assets], "count": len(self.assets)}

    def render_for_ci(self, *, max_assets: int = 8) -> str:
        if not self.assets:
            return ""
        lines = ["ATTACHED ASSETS (metadata only — not binary content):"]
        for a in self.assets[:max_assets]:
            lines.append(a.render_line())
        lines.append(
            "Decide which assets are relevant and which capabilities are required. "
            "Do not claim extraction succeeded without tool evidence."
        )
        return "\n".join(lines)

    def kinds(self) -> list[str]:
        return [a.kind for a in self.assets]

    def has_kind(self, *kinds: str) -> bool:
        kset = set(kinds)
        return any(a.kind in kset for a in self.assets)


def build_manifest_from_payload(payload: dict[str, Any] | None) -> AssetManifest:
    """Assemble manifest from worker payload fields (probe already run when available)."""
    payload = payload or {}
    assets: list[InboundAsset] = []

    # Explicit multi-asset list if present
    raw_list = payload.get("assets") or payload.get("inbound_assets") or []
    if isinstance(raw_list, list) and raw_list:
        for i, item in enumerate(raw_list):
            if not isinstance(item, dict):
                continue
            assets.append(
                InboundAsset(
                    id=str(item.get("id") or f"asset_{i+1}"),
                    kind=str(item.get("kind") or item.get("media_kind") or "unknown"),
                    mime=item.get("mime") or item.get("content_type"),
                    path=item.get("path") or item.get("local_media_path"),
                    size_bytes=int(item.get("size_bytes") or item.get("size") or 0),
                    duration_sec=item.get("duration_sec"),
                    width=item.get("width"),
                    height=item.get("height"),
                    page_count=item.get("page_count"),
                    capabilities=list(item.get("capabilities") or item.get("capability_list") or []),
                    status=str(item.get("status") or "ready"),
                    external_media_id=item.get("media_id") or item.get("external_media_id"),
                    warnings=list(item.get("warnings") or []),
                    probe=dict(item.get("probe") or {}),
                )
            )
        return AssetManifest(assets=assets)

    # Single attachment path (common WhatsApp/Telegram case)
    path = payload.get("local_media_path") or payload.get("principal_media_path")
    media_id = payload.get("media_id")
    probe = payload.get("media_probe") if isinstance(payload.get("media_probe"), dict) else {}
    caps = payload.get("media_capabilities")
    if not isinstance(caps, list):
        caps = list((probe.get("capability_list") or probe.get("capabilities") or []))
        if isinstance(probe.get("capabilities"), dict):
            caps = [k for k, v in probe["capabilities"].items() if v]

    if path or media_id or probe:
        kind = str(probe.get("kind") or "unknown")
        if kind == "unknown" and payload.get("content_type"):
            ct = str(payload.get("content_type") or "").lower()
            if ct.startswith("audio"):
                kind = "audio"
            elif ct.startswith("image"):
                kind = "image"
            elif ct.startswith("video"):
                kind = "video"
            elif "pdf" in ct:
                kind = "document"
        assets.append(
            InboundAsset(
                id=str(payload.get("media_asset_id") or media_id or f"asset_{uuid4().hex[:8]}"),
                kind=kind,
                mime=probe.get("mime") or payload.get("content_type") or payload.get("media_mime"),
                path=str(path) if path else None,
                size_bytes=int(probe.get("size_bytes") or payload.get("media_size") or 0),
                duration_sec=probe.get("duration_sec"),
                width=probe.get("width"),
                height=probe.get("height"),
                page_count=probe.get("page_count"),
                capabilities=[str(c) for c in caps] if caps else ["inspect"],
                status="ready" if path else ("pending" if media_id else "unknown"),
                external_media_id=str(media_id) if media_id else None,
                warnings=list(probe.get("warnings") or []),
                probe=probe,
            )
        )
    return AssetManifest(assets=assets)


def structural_media_families(manifest: AssetManifest, user_text: str = "") -> list[str]:
    """
    Bounded deterministic family hints when CI is degraded.
    Structure-only: attachment present → media family available — not keyword routing.
    """
    if not manifest.assets:
        return []
    families = ["media", "core"]
    # Surfaces only if no attachment requiring understanding? leave to CI
    return families
