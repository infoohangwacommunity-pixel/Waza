"""
Local media probe — identity, metadata, capability catalog.

Deterministic. Does not run STT/OCR/vision. Intelligence decides next steps.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from wax.media.types import MediaCapabilities, MediaKind, MediaProbe

_IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
_AUDIO = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".webm", ".mpeg", ".mpga"}
_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
_DOC = {".pdf", ".doc", ".docx", ".txt", ".md", ".csv", ".json", ".rtf"}
_TEXT = {".txt", ".md", ".csv", ".json"}

# webm can be audio or video — refined by ffprobe streams when available


def _sha256_file(path: Path, limit: int = 32_000_000) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            remaining = limit
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _kind_from_suffix(suffix: str) -> MediaKind:
    """Supporting metadata only — prefer magic/container when available."""
    s = suffix.lower()
    if s in _IMAGE:
        return "image"
    if s in _VIDEO:
        return "video"
    if s in _AUDIO:
        return "audio"
    if s == ".pdf" or s in _DOC:
        return "document" if s == ".pdf" or s in {".doc", ".docx", ".rtf"} else "text"
    if s in _TEXT:
        return "text"
    return "unknown"


def _kind_from_magic(path: Path, file_desc: str | None) -> MediaKind | None:
    """Authoritative kind from magic bytes / file(1) description when possible."""
    try:
        head = path.read_bytes()[:32]
    except Exception:
        head = b""
    desc = (file_desc or "").lower()
    if head.startswith(b"%PDF") or "pdf document" in desc:
        return "document"
    if head.startswith(b"\x89PNG") or head[:3] == b"\xff\xd8\xff" or head.startswith(b"GIF8"):
        return "image"
    if "png image" in desc or "jpeg image" in desc or "gif image" in desc or "webp" in desc:
        return "image"
    if head.startswith(b"OggS") or "ogg" in desc:
        # Ogg can be audio or video; refine with ffprobe later
        if "video" in desc:
            return "video"
        return "audio"
    if b"ftyp" in head[4:12] or "mp4" in desc or "quicktime" in desc or "matroska" in desc:
        if "audio" in desc and "video" not in desc:
            return "audio"
        return "video"
    if "audio" in desc or "mpeg" in desc or "wave" in desc or "flac" in desc:
        return "audio"
    if "video" in desc:
        return "video"
    if desc.startswith("ascii") or "text" in desc or "utf-8" in desc or "json" in desc:
        return "text"
    return None


def _vision_provider_configured() -> bool:
    """Truthful: vision only available when a multimodal provider/key is configured."""
    try:
        from wax.config import get_settings

        s = get_settings()
        if (getattr(s, "multimodal_api_key", None) or "").strip():
            return True
        provider = (getattr(s, "multimodal_provider", None) or "none").strip().lower()
        if provider and provider not in ("none", "null", ""):
            if (getattr(s, "primary_api_key", None) or "").strip():
                return True
        # Primary key alone does not imply vision models
        return False
    except Exception:
        return False


def _capabilities_for(
    kind: MediaKind,
    *,
    has_audio: bool | None = None,
    has_subtitles: bool | None = None,
) -> MediaCapabilities:
    caps = MediaCapabilities(inspect=True)
    vision_ok = _vision_provider_configured()
    if kind == "audio":
        caps.transcribe = True
        caps.extract_audio = True
    elif kind == "image":
        caps.ocr = True
        caps.vision = vision_ok
    elif kind == "video":
        caps.extract_audio = True
        caps.extract_frames = True
        caps.transcribe = bool(has_audio) if has_audio is not None else True
        caps.vision = vision_ok
        caps.extract_subtitles = bool(has_subtitles) if has_subtitles is not None else False
    elif kind == "document":
        caps.pdf_text = True
        caps.ocr = True
    elif kind == "text":
        caps.read_text_file = True
    return caps


def _run(argv: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        return proc.returncode, out, err
    except FileNotFoundError:
        return 127, "", "not_found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)[:200]


def _ffprobe_json(path: Path) -> dict[str, Any] | None:
    if not shutil.which("ffprobe"):
        return None
    code, out, _ = _run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=45.0,
    )
    if code != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except Exception:
        return None


def _pdf_page_count(path: Path) -> int | None:
    if not shutil.which("pdfinfo"):
        return None
    code, out, _ = _run(["pdfinfo", str(path)], timeout=20.0)
    if code != 0:
        return None
    m = re.search(r"Pages:\s*(\d+)", out)
    return int(m.group(1)) if m else None


def _file_description(path: Path) -> str | None:
    if not shutil.which("file"):
        return None
    code, out, _ = _run(["file", "-b", str(path)], timeout=10.0)
    return out.strip()[:500] if code == 0 and out.strip() else None


def probe_local_file(path: str | Path) -> MediaProbe:
    """
    Probe a local file and return structured MediaProbe with capability catalog.
    Does not transcribe, OCR, or call vision.
    """
    p = Path(path)
    warnings: list[str] = []
    if not p.is_file():
        return MediaProbe(
            path=str(path),
            kind="unknown",
            warnings=["file_not_found"],
            capabilities=MediaCapabilities(inspect=False),
        )

    suffix = p.suffix.lower()
    size = p.stat().st_size
    file_desc = _file_description(p)
    magic_kind = _kind_from_magic(p, file_desc)
    suffix_kind = _kind_from_suffix(suffix)
    # Magic/container is authoritative when available; suffix is supporting metadata
    kind = magic_kind or suffix_kind
    warnings_early: list[str] = []
    if magic_kind and suffix_kind != "unknown" and magic_kind != suffix_kind:
        warnings_early.append(f"suffix_kind_mismatch:suffix={suffix_kind},magic={magic_kind}")
    probe = MediaProbe(
        path=str(p.resolve()),
        kind=kind,
        suffix=suffix,
        size_bytes=size,
        sha256=_sha256_file(p),
        file_description=file_desc,
        warnings=list(warnings_early),
    )

    # Refine webm / containers via ffprobe
    if kind in ("audio", "video", "unknown") or suffix in {".webm", ".ogg", ".oga", ".mp4", ".mkv", ".mov"}:
        meta = _ffprobe_json(p)
        if meta:
            probe.raw_probe["ffprobe"] = {
                "format": (meta.get("format") or {}).get("format_name"),
                "duration": (meta.get("format") or {}).get("duration"),
            }
            try:
                dur = (meta.get("format") or {}).get("duration")
                if dur is not None:
                    probe.duration_sec = float(dur)
            except (TypeError, ValueError):
                pass
            has_a = has_v = has_s = False
            for stream in meta.get("streams") or []:
                st = stream.get("codec_type")
                if st == "audio":
                    has_a = True
                elif st == "video":
                    has_v = True
                    try:
                        if stream.get("width"):
                            probe.width = int(stream["width"])
                        if stream.get("height"):
                            probe.height = int(stream["height"])
                    except (TypeError, ValueError):
                        pass
                elif st == "subtitle":
                    has_s = True
            probe.has_audio_stream = has_a
            probe.has_video_stream = has_v
            probe.has_subtitle_stream = has_s
            if has_v:
                kind = "video"
            elif has_a and kind == "unknown":
                kind = "audio"
            probe.kind = kind
        elif kind in ("audio", "video"):
            warnings.append("ffprobe_unavailable_or_failed")

    if kind == "image" and shutil.which("identify"):
        code, out, _ = _run(["identify", "-format", "%w %h %m", str(p)], timeout=15.0)
        if code == 0 and out.strip():
            parts = out.strip().split()
            try:
                if len(parts) >= 2:
                    probe.width = int(parts[0])
                    probe.height = int(parts[1])
                if len(parts) >= 3:
                    probe.mime = f"image/{parts[2].lower()}"
            except ValueError:
                pass

    if suffix == ".pdf" or kind == "document":
        pages = _pdf_page_count(p)
        if pages is not None:
            probe.page_count = pages
            probe.kind = "document"

    if kind == "text" or suffix in _TEXT:
        probe.kind = "text"

    probe.capabilities = _capabilities_for(
        probe.kind,
        has_audio=probe.has_audio_stream,
        has_subtitles=probe.has_subtitle_stream,
    )

    if size > 25_000_000:
        warnings.append("file_large")
    if size == 0:
        warnings.append("empty_file")
        probe.capabilities = MediaCapabilities(inspect=True)

    probe.warnings = list(probe.warnings) + warnings
    return probe
