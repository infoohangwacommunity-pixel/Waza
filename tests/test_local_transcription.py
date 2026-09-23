"""Local transcription — controlled ffmpeg, no paid API by default."""

from pathlib import Path


def test_transcription_prefers_vosk_local():
    src = Path("wax/tools/transcription.py").read_text()
    assert "vosk" in src.lower()
    assert "ffmpeg" in src
    assert "WAX_TRANSCRIPTION_ALLOW_API" in src
    assert "api_transcription_disabled" in src
    assert "path_escape" in src
    assert "pcm_s16le" in src


def test_ffmpeg_goes_through_world_isolation():
    """Conversion is World-isolated, not a host subprocess fallback."""
    src = Path("wax/tools/transcription.py").read_text()
    assert "run_in_world" in src
    assert "create_subprocess_exec" not in src
    assert "run_sandboxed" not in src
    assert "world_required" in src


def test_dockerfile_provides_python_and_media_tools():
    df = Path("Dockerfile").read_text()
    assert "python:3.12" in df
    assert "ffmpeg" in df
    assert "tesseract" in df


def test_railway_uses_dockerfile_builder():
    src = Path("railway.toml").read_text()
    assert 'builder = "DOCKERFILE"' in src


def test_vosk_in_requirements():
    assert "vosk" in Path("requirements.txt").read_text()
