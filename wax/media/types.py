"""
Structured media types.

Media → probe → capabilities → intelligence chooses processors → evidence.
No educational workflow types (no tutor_from_audio, etc.).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

MediaKind = Literal["audio", "image", "video", "document", "text", "other", "unknown"]
QualityStatus = Literal["usable", "uncertain", "unusable", "unknown"]


@dataclass
class MediaCapabilities:
    """What safe processors can reasonably run on this asset."""

    inspect: bool = True
    transcribe: bool = False
    ocr: bool = False
    pdf_text: bool = False
    extract_audio: bool = False
    extract_frames: bool = False
    vision: bool = False  # optional multimodal; may be unavailable
    read_text_file: bool = False

    def as_list(self) -> list[str]:
        out = []
        for name in (
            "inspect",
            "transcribe",
            "ocr",
            "pdf_text",
            "extract_audio",
            "extract_frames",
            "vision",
            "read_text_file",
        ):
            if getattr(self, name):
                out.append(name)
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MediaProbe:
    """Structured identity + metadata for a local media file."""

    path: str
    kind: MediaKind = "unknown"
    mime: str | None = None
    suffix: str = ""
    size_bytes: int = 0
    sha256: str | None = None
    # optional metadata
    duration_sec: float | None = None
    width: int | None = None
    height: int | None = None
    page_count: int | None = None
    has_audio_stream: bool | None = None
    has_video_stream: bool | None = None
    has_subtitle_stream: bool | None = None
    file_description: str | None = None
    capabilities: MediaCapabilities = field(default_factory=MediaCapabilities)
    warnings: list[str] = field(default_factory=list)
    raw_probe: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["capabilities"] = self.capabilities.to_dict()
        d["capability_list"] = self.capabilities.as_list()
        return d


@dataclass
class TranscriptionQuality:
    status: QualityStatus = "unknown"
    mean_word_conf: float | None = None
    min_word_conf: float | None = None
    word_count: int = 0
    empty: bool = False
    decode_error: bool = False
    preprocessing_failed: bool = False
    signals: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptionResult:
    ok: bool
    transcript: str = ""
    provider: str | None = None
    model: str | None = None
    duration_sec: float | None = None
    processing_ms: int | None = None
    language: str | None = None
    preprocessing: dict[str, Any] = field(default_factory=dict)
    quality: TranscriptionQuality = field(default_factory=TranscriptionQuality)
    provenance: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["quality"] = self.quality.to_dict()
        return d


@dataclass
class ExtractionEvidence:
    """Processor output with provenance. Extensible; not an exhaustive enum of the future."""

    kind: str  # transcript | ocr | pdf_text | vision | probe | frame | subtitle | metadata | other
    processor: str
    processor_version: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    quality_status: QualityStatus = "unknown"
    span: dict[str, Any] = field(default_factory=dict)  # pages, time range, frame index
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
