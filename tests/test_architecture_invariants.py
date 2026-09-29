"""Final-architecture invariants — not historical systems."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_no_tool_registry():
    for rel in ("wax/intelligence/tutor.py", "wax/intelligence/directives.py", "wax/intelligence/providers.py"):
        src = _read(rel)
        assert "ToolSpec" not in src
        assert "tool_registry" not in src.lower()


def test_no_context_intelligence():
    blob = "\n".join(
        _read(p)
        for p in (
            "wax/intelligence/tutor.py",
            "wax/config/settings.py",
            ".env.example",
        )
    )
    assert "Context Intelligence" not in blob
    assert "ContextAssembler" not in blob
    assert "plan_and_retrieve" not in blob


def test_no_educational_engine_classes():
    src = _read("wax/db/models.py")
    for banned in (
        "class Curriculum",
        "class Assessment",
        "class Quiz",
        "class Mastery",
        "class Misconception",
    ):
        assert banned not in src


def test_no_artificial_tutor_token_budget():
    tutor = _read("wax/intelligence/tutor.py")
    # Tutor must not pass max_tokens into CompletionRequest
    assert "max_tokens=" not in tutor
    assert "max_tokens =" not in tutor
    block = _read("wax/intelligence/providers.py").split("class CompletionRequest")[1].split(
        "class CompletionResponse"
    )[0]
    # Field list on the dataclass (not docstring prose)
    assert "max_tokens:" not in block


def test_no_automatic_memory_injection():
    tutor = _read("wax/intelligence/tutor.py")
    assert "_memory_snapshot" not in tutor
    assert "Nothing is preloaded" in tutor or "preloaded" in tutor


def test_no_automatic_media_interpretation():
    media = _read("wax/messaging/media.py")
    # No STT/OCR libraries or auto-pipeline functions
    for banned in ("whisper", "vosk", "pytesseract", "easyocr"):
        assert banned not in media.lower()
    assert "def transcribe" not in media
    assert "def run_ocr" not in media
    worker = _read("wax/workers/main.py")
    assert "process_media_prepare" not in worker
    assert 'kind == "media_prepare"' not in worker


def test_one_intelligence_path():
    providers = _read("wax/intelligence/providers.py")
    assert "class IntelligenceService" in providers
    assert "memory_provider" not in _read("wax/config/settings.py")
    assert "handle_message" in _read("wax/intelligence/tutor.py")
    assert "handle_surface_request" in _read("wax/intelligence/tutor.py")


def test_durable_world_not_ephemeral_policy():
    layout = _read("wax/world/layout.py")
    assert "Student Worlds are durable" in layout
    settings = _read("wax/config/settings.py")
    assert "workspace_tmp_ttl_hours" in settings
    assert "workspace_ttl_hours:" not in settings


def test_directive_channels_canonical_only():
    from wax.intelligence.directives import CHANNELS

    # Core channels + identity linking channels (cross-channel verification)
    assert CHANNELS == frozenset({
        "world", "state", "time", "publish", "interact",
        "link", "link_request",
    })
