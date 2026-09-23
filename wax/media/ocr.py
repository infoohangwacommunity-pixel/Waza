"""
OCR preprocessing + Tesseract extraction via World isolation when possible.

Optimized capability — OS execution goes through the World substrate, not a
parallel host subprocess empire.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

from wax.media.types import ExtractionEvidence, QualityStatus


def _ocr_quality(text: str) -> tuple[QualityStatus, list[str]]:
    warnings: list[str] = []
    if not text.strip():
        return "unusable", ["empty_ocr"]
    alpha = sum(1 for c in text if c.isalpha())
    if alpha < 12:
        warnings.append("very_few_letters")
        return "uncertain", warnings
    words = text.split()
    if len(words) < 3:
        warnings.append("few_words")
        return "uncertain", warnings
    non = sum(
        1
        for c in text
        if not (c.isalnum() or c.isspace() or c in ".,;:!?-'\"()[]/%")
    )
    if len(text) > 0 and non / len(text) > 0.35:
        warnings.append("high_noise_ratio")
        return "uncertain", warnings
    return "usable", warnings


async def _run_isolated(
    argv: list[str],
    *,
    world_root: Path | None,
    principal_id: str | None,
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    """Prefer World isolation; fall back only when no world context (dev)."""
    if world_root is not None or principal_id:
        from wax.world.run_tool import run_in_world

        r = await run_in_world(
            principal_id,
            argv,
            cwd_rel="workspace",
            network_mode="none",
            timeout_sec=timeout,
            world_root=world_root,
        )
        code = 0 if r.success else (r.exit_code if r.exit_code is not None else 1)
        return code, r.stdout or "", r.stderr or r.error or ""
    # Dev fallback without principal — still scrubbed env, no secrets
    import subprocess

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "HOME": "/tmp",
            },
        )
        return (
            proc.returncode,
            (proc.stdout or b"").decode("utf-8", errors="replace"),
            (proc.stderr or b"").decode("utf-8", errors="replace"),
        )
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "not_found"
    except Exception as e:
        return 1, "", str(e)[:200]


async def preprocess_for_ocr(
    src: Path,
    dest: Path,
    *,
    world_root: Path | None = None,
    principal_id: str | None = None,
) -> dict[str, Any]:
    if not shutil.which("convert") and not shutil.which("magick"):
        return {"ok": False, "error": "imagemagick_not_found", "used_preprocess": False}
    bin_name = "magick" if shutil.which("magick") else "convert"
    dest.parent.mkdir(parents=True, exist_ok=True)
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
    code, _out, err = await _run_isolated(
        argv, world_root=world_root, principal_id=principal_id, timeout=45.0
    )
    if code != 0 or not dest.is_file():
        return {
            "ok": False,
            "error": "preprocess_failed",
            "detail": err[:300],
            "used_preprocess": False,
        }
    return {"ok": True, "path": str(dest), "used_preprocess": True}


async def run_tesseract(
    image: Path,
    *,
    lang: str = "eng",
    world_root: Path | None = None,
    principal_id: str | None = None,
) -> dict[str, Any]:
    if not shutil.which("tesseract"):
        return {"ok": False, "error": "tesseract_not_found", "text": ""}
    code, out, err = await _run_isolated(
        ["tesseract", str(image), "stdout", "-l", lang, "--psm", "3"],
        world_root=world_root,
        principal_id=principal_id,
        timeout=90.0,
    )
    text = (out or "").strip()
    if code != 0 and not text:
        return {"ok": False, "error": "tesseract_failed", "detail": err[:300], "text": ""}
    return {"ok": bool(text), "text": text[:20000], "lang": lang}


def ocr_image(
    path: str | Path,
    *,
    preprocess: bool = True,
    principal_id: str | None = None,
    world_root: Path | None = None,
) -> ExtractionEvidence:
    """Sync wrapper — runs async OCR pipeline."""
    return asyncio.get_event_loop().run_until_complete(
        ocr_image_async(
            path,
            preprocess=preprocess,
            principal_id=principal_id,
            world_root=world_root,
        )
    ) if False else _ocr_image_sync_bridge(
        path, preprocess=preprocess, principal_id=principal_id, world_root=world_root
    )


def _ocr_image_sync_bridge(
    path: str | Path,
    *,
    preprocess: bool = True,
    principal_id: str | None = None,
    world_root: Path | None = None,
) -> ExtractionEvidence:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Caller should use ocr_image_async in async context
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    lambda: asyncio.run(
                        ocr_image_async(
                            path,
                            preprocess=preprocess,
                            principal_id=principal_id,
                            world_root=world_root,
                        )
                    )
                ).result()
        return loop.run_until_complete(
            ocr_image_async(
                path,
                preprocess=preprocess,
                principal_id=principal_id,
                world_root=world_root,
            )
        )
    except RuntimeError:
        return asyncio.run(
            ocr_image_async(
                path,
                preprocess=preprocess,
                principal_id=principal_id,
                world_root=world_root,
            )
        )


async def ocr_image_async(
    path: str | Path,
    *,
    preprocess: bool = True,
    principal_id: str | None = None,
    world_root: Path | None = None,
) -> ExtractionEvidence:
    src = Path(path)
    provenance: dict[str, Any] = {
        "source_path": str(src.resolve()) if src.exists() else str(src)
    }
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
        # Prefer writing preprocess output under world tmp when available
        if world_root is not None:
            tmp = Path(world_root) / "tmp" / f"wax-ocr-{src.stem}.png"
            tmp.parent.mkdir(parents=True, exist_ok=True)
        prep_meta = await preprocess_for_ocr(
            src, tmp, world_root=world_root, principal_id=principal_id
        )
        if prep_meta.get("ok"):
            work_img = tmp
            provenance["preprocessed_path"] = str(tmp)
        else:
            provenance["preprocess_error"] = prep_meta.get("error")

    result = await run_tesseract(
        work_img, world_root=world_root, principal_id=principal_id
    )
    if tmp is not None and str(tmp).startswith("/tmp"):
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
        payload={
            "text": text,
            "lang": result.get("lang") or "eng",
            "preprocess": prep_meta,
        },
        quality_status=status,
        warnings=warnings,
        provenance=provenance,
    )
