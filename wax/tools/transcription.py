"""
Local audio transcription via terminal workspace tools.

Primary path (no paid transcription API):
  ffmpeg → 16 kHz mono WAV → Vosk offline model → text

ffmpeg runs as a controlled, path-isolated pipeline on files already under the
workspace root — not as an open model shell. Optional API transcription only
when WAX_TRANSCRIPTION_ALLOW_API=1.

Images/OCR remain tesseract via inspect_media (local).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import wave
import zipfile
from pathlib import Path
from typing import Any

import httpx

from wax.config import get_settings
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

VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"


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
    root = _model_root()
    model_dir = root / VOSK_MODEL_NAME
    if model_dir.is_dir() and any(model_dir.iterdir()):
        return model_dir
    zip_path = root / f"{VOSK_MODEL_NAME}.zip"
    try:
        if not zip_path.is_file():
            logger.info("vosk_model_download_start", url=VOSK_MODEL_URL)
            with httpx.Client(timeout=180.0, follow_redirects=True) as client:
                with client.stream("GET", VOSK_MODEL_URL) as resp:
                    resp.raise_for_status()
                    with zip_path.open("wb") as out:
                        for chunk in resp.iter_bytes(1024 * 64):
                            out.write(chunk)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(root)
        if model_dir.is_dir():
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
    """
    Convert any common voice/audio container to 16 kHz mono PCM WAV.

    Path-isolated: source and output must live under the workspace root.
    Uses a controlled ffmpeg invocation (allowlisted binary), not model shell access.
    """
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
    # Telegram voice notes are often Opus in OGG (.oga/.ogg). Let ffmpeg probe;
    # force pcm_s16le output for Vosk.
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
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=90.0)
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
        return {"ok": True, "wav": str(wav_out)}
    except FileNotFoundError:
        return {"ok": False, "error": "ffmpeg_not_found"}
    except Exception as e:
        logger.exception("ffmpeg_exception")
        return {"ok": False, "error": "ffmpeg_exception", "detail": str(e)[:300]}


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
        with wave.open(str(wav_path), "rb") as wf:
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                return {"ok": False, "error": "wav_format_expected_16bit_mono"}
            rec = KaldiRecognizer(model, wf.getframerate())
            rec.SetWords(True)
            parts: list[str] = []
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                if rec.AcceptWaveform(data):
                    try:
                        j = json.loads(rec.Result())
                        if j.get("text"):
                            parts.append(j["text"])
                    except Exception:
                        pass
            try:
                j = json.loads(rec.FinalResult())
                if j.get("text"):
                    parts.append(j["text"])
            except Exception:
                pass
        text = " ".join(parts).strip()
        if not text:
            return {"ok": False, "error": "empty_transcript", "provider": "vosk"}
        return {
            "ok": True,
            "transcript": text[:20000],
            "path": str(wav_path),
            "provider": "vosk_local",
            "model": VOSK_MODEL_NAME,
        }
    except Exception as e:
        logger.exception("vosk_transcribe_failed")
        return {"ok": False, "error": "vosk_exception", "detail": str(e)[:300]}


async def _api_transcribe_fallback(path: Path, language: str | None) -> dict[str, Any]:
    allow = os.environ.get("WAX_TRANSCRIPTION_ALLOW_API", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not allow:
        return {
            "ok": False,
            "error": "api_transcription_disabled",
            "hint": "Local Vosk/ffmpeg path is preferred. Set WAX_TRANSCRIPTION_ALLOW_API=1 only if you intentionally want a paid API.",
        }
    api_key = settings.primary_api_key or ""
    if not api_key:
        return {"ok": False, "error": "transcription_api_key_missing"}
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
            return {
                "ok": False,
                "error": "transcription_api_failed",
                "status_code": resp.status_code,
                "detail": resp.text[:300],
            }
        text = (resp.json().get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "empty_transcript"}
        return {
            "ok": True,
            "transcript": text[:20000],
            "path": str(path),
            "provider": "openai_compatible",
            "model": model,
        }
    except Exception as e:
        return {"ok": False, "error": "transcription_api_exception", "detail": str(e)[:300]}


async def transcribe_local_audio(path: str, *, language: str | None = None) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": "file_not_found", "path": path}

    model_dir = _ensure_vosk_model()
    if model_dir is not None:
        wav_path = p.parent / f".wax-{p.stem}-16k.wav"
        conv = await _ffmpeg_to_wav(p, wav_path)
        if conv.get("ok"):
            local = _vosk_transcribe_wav(Path(conv["wav"]), model_dir)
            try:
                Path(conv["wav"]).unlink(missing_ok=True)
            except Exception:
                pass
            if local.get("ok"):
                local["source_path"] = str(p)
                return local
            logger.info("vosk_local_failed", error=local.get("error"), detail=local.get("detail"))
        else:
            logger.info(
                "ffmpeg_convert_failed",
                error=conv.get("error"),
                detail=conv.get("detail"),
                returncode=conv.get("returncode"),
            )
    else:
        logger.info("vosk_model_unavailable")

    return await _api_transcribe_fallback(p, language)
