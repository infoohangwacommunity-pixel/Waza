"""
Local audio transcription via terminal workspace tools.

Primary path (no paid transcription API):
  ffmpeg → 16 kHz mono WAV → Vosk offline model → text + quality signals

ffmpeg runs path-isolated on workspace files. Optional API only when
WAX_TRANSCRIPTION_ALLOW_API=1.

Model is configurable via WAX_VOSK_MODEL_NAME; prefer persistent packaging
under WAX_MODEL_ROOT rather than surprise runtime downloads in production.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import wave
import zipfile
from pathlib import Path
from typing import Any

import httpx

from wax.config import get_settings
from wax.media.types import TranscriptionQuality, TranscriptionResult
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

AUDIO_SUFFIXES = {
    ".ogg",
    ".oga",
    ".mp3",
    ".m4a",
    ".wav",
    ".webm",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".flac",
    ".opus",
}

DEFAULT_VOSK_MODEL = "vosk-model-small-en-us-0.15"


def _vosk_model_name() -> str:
    return (os.environ.get("WAX_VOSK_MODEL_NAME") or DEFAULT_VOSK_MODEL).strip()


def _model_root() -> Path:
    root = (
        os.environ.get("WAX_MODEL_ROOT")
        or os.environ.get("WAX_WORKSPACE_ROOT")
        or getattr(settings, "workspace_root", None)
        or "/tmp/wax-models"
    )
    path = Path(str(root)) / "models" / "vosk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workspace_root() -> Path:
    from wax.terminal.workspace import workspace_root

    return workspace_root().resolve()


def _under_workspace(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = _workspace_root()
        return str(resolved).startswith(str(root) + os.sep) or resolved == root
    except Exception:
        return False


def _ensure_vosk_model() -> Path | None:
    """
    Locate model directory. Prefer already-present model (image/volume).
    Runtime download only when WAX_VOSK_ALLOW_DOWNLOAD=1 (not production default).
    """
    name = _vosk_model_name()
    root = _model_root()
    model_dir = root / name
    if model_dir.is_dir() and any(model_dir.iterdir()):
        return model_dir
    # Alternate layout: model extracted as sole child
    for child in root.iterdir() if root.is_dir() else []:
        if child.is_dir() and "vosk-model" in child.name:
            if any(child.iterdir()):
                return child

    allow_dl = os.environ.get("WAX_VOSK_ALLOW_DOWNLOAD", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    # Dev convenience: allow download outside production unless explicitly disabled
    app_env = (getattr(settings, "app_env", None) or os.environ.get("APP_ENV") or "").lower()
    if not allow_dl:
        if app_env == "production":
            logger.warning(
                "vosk_model_missing_no_download",
                model=name,
                path=str(model_dir),
                hint="Place model under WAX_MODEL_ROOT or bake into image; set WAX_VOSK_ALLOW_DOWNLOAD=1 only intentionally",
            )
            return None
        # Non-production: allow one-time download for developer convenience
    url = f"https://alphacephei.com/vosk/models/{name}.zip"
    zip_path = root / f"{name}.zip"
    try:
        if not zip_path.is_file():
            logger.info("vosk_model_download_start", url=url)
            with httpx.Client(timeout=180.0, follow_redirects=True) as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with zip_path.open("wb") as out:
                        for chunk in resp.iter_bytes(1024 * 64):
                            out.write(chunk)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(root)
        if model_dir.is_dir() and any(model_dir.iterdir()):
            logger.info("vosk_model_ready", path=str(model_dir))
            return model_dir
        for child in root.iterdir():
            if child.is_dir() and "vosk-model" in child.name:
                return child
    except Exception:
        logger.exception("vosk_model_prepare_failed")
        return None
    return None


def _ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


async def _ffmpeg_to_wav(src: Path, wav_out: Path) -> dict[str, Any]:
    src = src.resolve()
    wav_out = wav_out.resolve()
    if not src.is_file():
        return {"ok": False, "error": "file_not_found", "path": str(src)}
    if not _under_workspace(src):
        return {"ok": False, "error": "path_escape", "path": str(src)}
    if not _under_workspace(wav_out.parent):
        return {"ok": False, "error": "path_escape", "path": str(wav_out)}

    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return {
            "ok": False,
            "error": "ffmpeg_not_found",
            "hint": "Install ffmpeg in the container image (Dockerfile apt package).",
        }

    wav_out.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_out),
    ]
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(src.parent),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(src.parent),
            env=env,
        )
        try:
            _stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=90.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"ok": False, "error": "ffmpeg_timeout"}
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")[:800]
        if proc.returncode != 0:
            logger.warning(
                "ffmpeg_failed",
                returncode=proc.returncode,
                stderr=stderr,
                src=str(src),
            )
            return {
                "ok": False,
                "error": "ffmpeg_failed",
                "returncode": proc.returncode,
                "detail": stderr or "no_stderr",
            }
        if not wav_out.is_file() or wav_out.stat().st_size < 44:
            return {"ok": False, "error": "ffmpeg_no_output", "detail": stderr}
        return {
            "ok": True,
            "wav": str(wav_out),
            "sample_rate": 16000,
            "channels": 1,
            "codec": "pcm_s16le",
        }
    except FileNotFoundError:
        return {"ok": False, "error": "ffmpeg_not_found"}
    except Exception as e:
        logger.exception("ffmpeg_exception")
        return {"ok": False, "error": "ffmpeg_exception", "detail": str(e)[:300]}


def _assess_quality(
    *,
    text: str,
    word_confs: list[float],
    decode_error: bool = False,
    preprocessing_failed: bool = False,
    duration_sec: float | None = None,
) -> TranscriptionQuality:
    """Map STT signals to usable | uncertain | unusable. Not an accent rule engine."""
    warnings: list[str] = []
    signals: dict[str, Any] = {}
    empty = not (text or "").strip()
    signals["empty"] = empty
    signals["word_count"] = len(word_confs) if word_confs else len(text.split())
    if duration_sec is not None:
        signals["duration_sec"] = duration_sec

    mean_c = min_c = None
    if word_confs:
        mean_c = sum(word_confs) / len(word_confs)
        min_c = min(word_confs)
        signals["mean_word_conf"] = round(mean_c, 4)
        signals["min_word_conf"] = round(min_c, 4)

    # Repetition / fragmentation heuristic (signal only)
    tokens = text.lower().split()
    if len(tokens) >= 6:
        uniq = len(set(tokens))
        ratio = uniq / len(tokens)
        signals["unique_token_ratio"] = round(ratio, 3)
        if ratio < 0.35:
            warnings.append("high_repetition")

    if preprocessing_failed:
        return TranscriptionQuality(
            status="unusable",
            mean_word_conf=mean_c,
            min_word_conf=min_c,
            word_count=signals["word_count"],
            empty=empty,
            decode_error=decode_error,
            preprocessing_failed=True,
            signals=signals,
            warnings=warnings + ["preprocessing_failed"],
        )
    if decode_error:
        return TranscriptionQuality(
            status="unusable",
            mean_word_conf=mean_c,
            min_word_conf=min_c,
            word_count=signals.get("word_count", 0),
            empty=empty,
            decode_error=True,
            signals=signals,
            warnings=warnings + ["decode_error"],
        )
    if empty:
        return TranscriptionQuality(
            status="unusable",
            mean_word_conf=mean_c,
            min_word_conf=min_c,
            word_count=0,
            empty=True,
            signals=signals,
            warnings=warnings + ["empty_transcript"],
        )

    # Confidence bands when word confs available
    if mean_c is not None:
        if mean_c >= 0.65 and (min_c is None or min_c >= 0.25):
            status = "usable"
        elif mean_c >= 0.40:
            status = "uncertain"
            warnings.append("moderate_confidence")
        else:
            status = "unusable"
            warnings.append("low_confidence")
    else:
        # No confidences — treat non-empty as uncertain rather than falsely usable
        status = "uncertain"
        warnings.append("no_word_confidence")

    if "high_repetition" in warnings and status == "usable":
        status = "uncertain"

    return TranscriptionQuality(
        status=status,
        mean_word_conf=mean_c,
        min_word_conf=min_c,
        word_count=signals.get("word_count", 0),
        empty=False,
        signals=signals,
        warnings=warnings,
    )


def _vosk_transcribe_wav(wav_path: Path, model_dir: Path) -> dict[str, Any]:
    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
    except ImportError:
        return {
            "ok": False,
            "error": "vosk_not_installed",
            "hint": "Install vosk in requirements (local offline STT)",
        }

    try:
        model = Model(str(model_dir))
        duration_sec = None
        with wave.open(str(wav_path), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                return {"ok": False, "error": "wav_format_expected_16bit_mono", "decode_error": True}
            fr = wf.getframerate()
            nframes = wf.getnframes()
            if fr > 0:
                duration_sec = nframes / float(fr)
            rec = KaldiRecognizer(model, fr)
            rec.SetWords(True)
            parts: list[str] = []
            word_confs: list[float] = []

            def _consume(result_json: str) -> None:
                try:
                    j = json.loads(result_json)
                except Exception:
                    return
                if j.get("text"):
                    parts.append(j["text"])
                for w in j.get("result") or []:
                    try:
                        c = float(w.get("conf"))
                        word_confs.append(c)
                    except (TypeError, ValueError):
                        pass

            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                if rec.AcceptWaveform(data):
                    _consume(rec.Result())
            _consume(rec.FinalResult())

        text = " ".join(parts).strip()
        return {
            "ok": bool(text),
            "transcript": text[:20000],
            "path": str(wav_path),
            "provider": "vosk_local",
            "model": _vosk_model_name(),
            "word_confs": word_confs,
            "duration_sec": duration_sec,
            "error": None if text else "empty_transcript",
        }
    except Exception as e:
        logger.exception("vosk_transcribe_failed")
        return {
            "ok": False,
            "error": "vosk_exception",
            "detail": str(e)[:300],
            "decode_error": True,
        }


async def _api_transcribe_fallback(path: Path, language: str | None) -> TranscriptionResult:
    allow = os.environ.get("WAX_TRANSCRIPTION_ALLOW_API", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not allow:
        return TranscriptionResult(
            ok=False,
            error="api_transcription_disabled",
            detail="Local Vosk/ffmpeg path is preferred. Set WAX_TRANSCRIPTION_ALLOW_API=1 only intentionally.",
            quality=TranscriptionQuality(status="unusable", warnings=["api_disabled"]),
        )
    api_key = settings.primary_api_key or ""
    if not api_key:
        return TranscriptionResult(
            ok=False,
            error="transcription_api_key_missing",
            quality=TranscriptionQuality(status="unusable"),
        )
    base_url = (settings.primary_base_url or "https://api.openai.com/v1").rstrip("/")
    model = getattr(settings, "transcription_model", None) or "whisper-1"
    mime = {
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".opus": "audio/ogg",
    }.get(path.suffix.lower(), "application/octet-stream")
    data: dict[str, str] = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language[:16]
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            with path.open("rb") as f:
                resp = await client.post(
                    f"{base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files={"file": (path.name, f, mime)},
                )
        if resp.status_code >= 400:
            return TranscriptionResult(
                ok=False,
                error="transcription_api_failed",
                detail=resp.text[:300],
                quality=TranscriptionQuality(status="unusable", decode_error=True),
            )
        text = (resp.json().get("text") or "").strip()
        ms = int((time.monotonic() - t0) * 1000)
        quality = _assess_quality(text=text, word_confs=[])  # API may not give word conf
        if text and quality.status == "uncertain":
            # API non-empty without confidences: treat as usable with warning
            quality.status = "usable"
            quality.warnings.append("api_no_word_confidence")
        return TranscriptionResult(
            ok=bool(text),
            transcript=text[:20000],
            provider="openai_compatible",
            model=model,
            processing_ms=ms,
            language=language,
            quality=quality,
            provenance={"source_path": str(path)},
            error=None if text else "empty_transcript",
        )
    except Exception as e:
        return TranscriptionResult(
            ok=False,
            error="transcription_api_exception",
            detail=str(e)[:300],
            quality=TranscriptionQuality(status="unusable", decode_error=True),
        )


async def transcribe_local_audio(
    path: str,
    *,
    language: str | None = None,
    work_id: str | None = None,
    asset_id: str | None = None,
) -> dict[str, Any]:
    """
    Transcribe local audio. Returns TranscriptionResult as dict (tool-friendly).
    Does not invent success; quality.status tells usable/uncertain/unusable.
    """
    p = Path(path)
    t0 = time.monotonic()
    if not p.is_file():
        return TranscriptionResult(
            ok=False,
            error="file_not_found",
            detail=path,
            quality=TranscriptionQuality(status="unusable", empty=True),
            provenance={"source_path": path},
        ).to_dict()

    model_dir = _ensure_vosk_model()
    preprocessing: dict[str, Any] = {}
    if model_dir is not None:
        wav_path = p.parent / f".wax-{p.stem}-16k.wav"
        conv = await _ffmpeg_to_wav(p, wav_path)
        preprocessing = {k: v for k, v in conv.items() if k != "wav"}
        if conv.get("ok"):
            local = _vosk_transcribe_wav(Path(conv["wav"]), model_dir)
            try:
                Path(conv["wav"]).unlink(missing_ok=True)
            except Exception:
                pass
            ms = int((time.monotonic() - t0) * 1000)
            if local.get("ok") or local.get("transcript") is not None:
                text = local.get("transcript") or ""
                quality = _assess_quality(
                    text=text,
                    word_confs=list(local.get("word_confs") or []),
                    decode_error=bool(local.get("decode_error")),
                    duration_sec=local.get("duration_sec"),
                )
                result = TranscriptionResult(
                    ok=bool(text) and quality.status != "unusable",
                    transcript=text,
                    provider=local.get("provider") or "vosk_local",
                    model=local.get("model") or _vosk_model_name(),
                    duration_sec=local.get("duration_sec"),
                    processing_ms=ms,
                    language=language,
                    preprocessing=preprocessing,
                    quality=quality,
                    provenance={
                        "source_path": str(p),
                        "work_id": work_id,
                        "asset_id": asset_id,
                    },
                    error=local.get("error") if not text else None,
                )
                logger.info(
                    "stt_completed",
                    provider=result.provider,
                    model=result.model,
                    quality=quality.status,
                    chars=len(text),
                    processing_ms=ms,
                    mean_conf=quality.mean_word_conf,
                )
                return result.to_dict()
            logger.info("vosk_local_failed", error=local.get("error"), detail=local.get("detail"))
            if local.get("decode_error"):
                return TranscriptionResult(
                    ok=False,
                    error=local.get("error") or "vosk_failed",
                    detail=local.get("detail"),
                    processing_ms=ms,
                    preprocessing=preprocessing,
                    quality=TranscriptionQuality(
                        status="unusable",
                        decode_error=True,
                        warnings=["vosk_decode_error"],
                    ),
                    provenance={"source_path": str(p)},
                ).to_dict()
        else:
            logger.info(
                "ffmpeg_convert_failed",
                error=conv.get("error"),
                detail=conv.get("detail"),
            )
            preprocessing_failed = True
            # fall through to API if allowed
            api = await _api_transcribe_fallback(p, language)
            if not api.ok:
                return TranscriptionResult(
                    ok=False,
                    error=conv.get("error") or "ffmpeg_failed",
                    detail=conv.get("detail"),
                    preprocessing=preprocessing,
                    quality=TranscriptionQuality(
                        status="unusable",
                        preprocessing_failed=preprocessing_failed,
                        warnings=["ffmpeg_failed"],
                    ),
                    provenance={"source_path": str(p)},
                ).to_dict()
            return api.to_dict()
    else:
        logger.info("vosk_model_unavailable")

    return (await _api_transcribe_fallback(p, language)).to_dict()
