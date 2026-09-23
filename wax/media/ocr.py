"""
OCR preprocessing + Tesseract extraction.

Improves phone photos of notes/textbooks without becoming a vision engine.
Deterministic ImageMagick/Tesseract pipeline; intelligence decides when to call.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from wax.media.types import ExtractionEvidence, QualityStatus


def _run(argv: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        return (
            proc.returncode,
            (proc.stdout or b"").decode("utf-8", errors="replace"),
            (proc.stderr or b"").decode("utf-8", errors="replace"),
        )
    except FileNotFoundError:
        return 127, "", "not_found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)[:200]


def preprocess_for_ocr(src: Path, dest: Path) -> dict[str, Any]:
    """
    Normalize image for OCR: auto-orient, grayscale, contrast, deskew-ish via
    ImageMagick. Does not invent content; returns status only.
    """
    if not shutil.which("convert") and not shutil.which("magick"):
        return {"ok": False, "error": "imagemagick_not_found", "used_preprocess": False}
    bin_name = "magick" if shutil.which("magick") else "convert"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Pipeline tuned for phone photos of printed notes / textbooks
    argv = [
        bin_name,
        str(src),
        "-auto-orient",
        "-colorspace",
        "Gray",
        "-normalize",
        "-contrast-stretch",
        "2%x2%",
        "-sharpen",
        "0x1",
        "-density",
        "300",
        str(dest),
    ]
    code, _out, err = _run(argv, timeout=45.0)
    if code != 0 or not dest.is_file():
        return {
            "ok": False,
            "error": "preprocess_failed",
            "detail": err[:300],
            "used_preprocess": False,
        }
    return {"ok": True, "path": str(dest), "used_preprocess": True}


def run_tesseract(image: Path, *, lang: str = "eng") -> dict[str, Any]:
    if not shutil.which("tesseract"):
        return {"ok": False, "error": "tesseract_not_found", "text": ""}
    code, out, err = _run(
        ["tesseract", str(image), "stdout", "-l", lang, "--psm", "3"],
        timeout=90.0,
    )
    text = (out or "").strip()
    if code != 0 and not text:
        return {
            "ok": False,
            "error": "tesseract_failed",
            "detail": err[:300],
            "text": "",
        }
    return {"ok": bool(text), "text": text[:20000], "lang": lang}


def _ocr_quality(text: str) -> tuple[QualityStatus, list[str]]:
    warnings: list[str] = []
    if not text.strip():
        return "unusable", ["empty_ocr"]
    # crude printable / word density signals
    alpha = sum(1 for c in text if c.isalpha())
    if alpha < 12:
        warnings.append("very_few_letters")
        return "uncertain", warnings
    words = text.split()
    if len(words) < 3:
        warnings.append("few_words")
        return "uncertain", warnings
    # high non-alnum ratio often means garbage OCR
    non = sum(1 for c in text if not (c.isalnum() or c.isspace() or c in ".,;:!?-'\"()[]/%"))
    if len(text) > 0 and non / len(text) > 0.35:
        warnings.append("high_noise_ratio")
        return "uncertain", warnings
    return "usable", warnings


def ocr_image(path: str | Path, *, preprocess: bool = True) -> ExtractionEvidence:
    """Full OCR path with optional preprocess. Returns ExtractionEvidence."""
    src = Path(path)
    provenance: dict[str, Any] = {"source_path": str(src.resolve()) if src.exists() else str(src)}
    if not src.is_file():
        return ExtractionEvidence(
            kind="ocr",
            processor="tesseract",
            payload={"text": ""},
            quality_status="unusable",
            warnings=["file_not_found"],
            provenance=provenance,
        )

    work_img = src
    prep_meta: dict[str, Any] = {"used_preprocess": False}
    tmp: Path | None = None
    if preprocess:
        fd, tmp_name = tempfile.mkstemp(suffix=".png", prefix="wax-ocr-")
        import os

        os.close(fd)
        tmp = Path(tmp_name)
        prep_meta = preprocess_for_ocr(src, tmp)
        if prep_meta.get("ok"):
            work_img = tmp
            provenance["preprocessed_path"] = str(tmp)
        else:
            provenance["preprocess_error"] = prep_meta.get("error")

    result = run_tesseract(work_img)
    if tmp is not None:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

    text = result.get("text") or ""
    status, warnings = _ocr_quality(text)
    if not result.get("ok") and not text:
        status = "unusable"
        warnings.append(result.get("error") or "ocr_failed")

    return ExtractionEvidence(
        kind="ocr",
        processor="tesseract",
        processor_version=None,
        payload={
            "text": text,
            "lang": result.get("lang") or "eng",
            "preprocess": prep_meta,
        },
        quality_status=status,
        warnings=warnings,
        provenance=provenance,
    )
