"""Phase 4: audio transcription + document ingest wiring."""

from pathlib import Path
import ast


def test_transcribe_tool_registered():
    src = Path("wax/tools/registry.py").read_text()
    assert "handle_transcribe_audio" in src
    assert '"transcribe_audio"' in src
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "handle_transcribe_audio" in names
    assert "handle_ingest_document" in names


def test_transcribe_in_available_tools():
    src = Path("wax/intelligence/tutor.py").read_text()
    assert 'name="transcribe_audio"' in src
    assert 'name="ingest_document"' in src


def test_transcription_module_exists():
    src = Path("wax/tools/transcription.py").read_text()
    assert "transcribe_local_audio" in src
    assert "vosk" in src.lower()
    assert "ffmpeg" in src


def test_worker_auto_transcribes_audio():
    src = Path("wax/workers/main.py").read_text()
    assert "transcribe_local_audio" in src
    assert "audio_auto_transcribed" in src


def test_knowledge_ingest_has_list_and_chunks():
    src = Path("wax/knowledge/ingest.py").read_text()
    assert "list_sources" in src
    assert "recent_chunks_for_context" in src
    # no duplicated broken trailing method body
    assert src.count("async def ingest_text") == 1


def test_context_surfaces_learner_materials():
    src = Path("wax/intelligence/context.py").read_text()
    assert "_learner_materials_block" in src
    assert "Learner materials" in src


def test_path_isolation_in_handlers():
    src = Path("wax/tools/registry.py").read_text()
    assert "path_escape" in src
