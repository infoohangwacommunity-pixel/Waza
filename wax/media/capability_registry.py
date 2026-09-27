"""
Media capability registry — discoverable specs, not MIME→action hardcoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MediaCapabilitySpec:
    name: str
    tool_name: str
    input_kinds: list[str]
    produces: list[str]
    expensive: bool = False
    requires_provider: str | None = None  # e.g. vision → multimodal
    description: str = ""


REGISTRY: list[MediaCapabilitySpec] = [
    MediaCapabilitySpec(
        name="inspect",
        tool_name="inspect_media",
        input_kinds=["audio", "image", "video", "document", "text", "other", "unknown"],
        produces=["probe"],
        description="Probe identity and metadata without extraction",
    ),
    MediaCapabilitySpec(
        name="transcribe",
        tool_name="transcribe_audio",
        input_kinds=["audio", "video"],
        produces=["transcript"],
        expensive=True,
        description="Speech-to-text for audio tracks",
    ),
    MediaCapabilitySpec(
        name="vision",
        tool_name="describe_image",
        input_kinds=["image"],
        produces=["image_description"],
        expensive=True,
        requires_provider="multimodal",
        description="Visual description when multimodal provider configured",
    ),
    MediaCapabilitySpec(
        name="ocr",
        tool_name="inspect_media",
        input_kinds=["image", "document"],
        produces=["ocr_text"],
        expensive=True,
        description="Optical character recognition",
    ),
    MediaCapabilitySpec(
        name="pdf_text",
        tool_name="inspect_media",
        input_kinds=["document"],
        produces=["pdf_text"],
        description="Extract embedded text from PDF",
    ),
    MediaCapabilitySpec(
        name="extract_audio",
        tool_name="extract_video_audio",
        input_kinds=["video"],
        produces=["audio_file"],
        description="Pull audio track from video",
    ),
    MediaCapabilitySpec(
        name="extract_frames",
        tool_name="extract_video_frames",
        input_kinds=["video"],
        produces=["frame_images"],
        expensive=True,
        description="Sample frames for visual inspection",
    ),
    MediaCapabilitySpec(
        name="extract_subtitles",
        tool_name="extract_subtitles",
        input_kinds=["video"],
        produces=["subtitles"],
        description="Extract embedded subtitles if present",
    ),
]


def specs_for_kind(kind: str) -> list[MediaCapabilitySpec]:
    return [s for s in REGISTRY if kind in s.input_kinds]


def runtime_availability() -> dict[str, str]:
    """Map capability name → available|unavailable based on settings."""
    from wax.config import get_settings

    s = get_settings()
    vision = bool((getattr(s, "multimodal_api_key", None) or "").strip())
    out: dict[str, str] = {}
    for spec in REGISTRY:
        if spec.requires_provider == "multimodal" and not vision:
            out[spec.name] = "unavailable"
        else:
            out[spec.name] = "available"
    return out


def render_registry_for_ci() -> str:
    avail = runtime_availability()
    lines = ["MEDIA CAPABILITY REGISTRY (runtime):"]
    for spec in REGISTRY:
        st = avail.get(spec.name, "available")
        lines.append(
            f"- {spec.name} tool={spec.tool_name} kinds={spec.input_kinds} "
            f"status={st} expensive={spec.expensive}"
        )
    return "\n".join(lines)
