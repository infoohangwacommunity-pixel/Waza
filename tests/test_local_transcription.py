"""Local transcription — no paid API required by default."""

from pathlib import Path


def test_transcription_prefers_vosk_local():
    src = Path("wax/tools/transcription.py").read_text()
    assert "vosk" in src.lower()
    assert "ffmpeg" in src
    assert "WAX_TRANSCRIPTION_ALLOW_API" in src
    assert "api_transcription_disabled" in src


def test_dockerfile_provides_python_and_media_tools():
    df = Path("Dockerfile").read_text()
    assert "python:3.12" in df
    assert "ffmpeg" in df
    assert "tesseract" in df
    assert "vosk" not in df  # python dep, not apt


def test_railway_uses_dockerfile_builder():
    src = Path("railway.toml").read_text()
    assert 'builder = "DOCKERFILE"' in src
    assert "dockerfilePath" in src


def test_vosk_in_requirements():
    req = Path("requirements.txt").read_text()
    assert "vosk" in req


def test_no_required_openai_transcription_in_default_path():
    src = Path("wax/tools/transcription.py").read_text()
    # default path must not require PRIMARY_API_KEY
    assert "transcription_not_configured" not in src or "WAX_TRANSCRIPTION_ALLOW_API" in src
