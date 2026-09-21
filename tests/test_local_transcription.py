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


def test_ffmpeg_not_via_open_shell_bwrap():
    """Conversion is path-isolated controlled pipeline, not model shell."""
    src = Path("wax/tools/transcription.py").read_text()
    assert "create_subprocess_exec" in src
    # Should not require run_sandboxed for media conversion reliability
    assert "run_sandboxed" not in src


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
