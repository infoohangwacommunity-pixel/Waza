"""Phase 4: terminal-first media execution and workspace isolation."""

from pathlib import Path
import ast


def test_terminal_and_world_tools_registered():
    src = Path("wax/tools/registry.py").read_text()
    assert "handle_world_exec" in src
    assert "handle_world_files" in src
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "handle_world_exec" in names
    assert "handle_world_files" in names


def test_world_exec_in_available_tools():
    src = Path("wax/intelligence/tutor.py").read_text()
    assert 'name="world_exec"' in src
    assert 'name="world_files"' in src


def test_transcription_module_exists():
    src = Path("wax/tools/transcription.py").read_text()
    assert "transcribe_local_audio" in src


def test_worker_auto_transcribes_audio():
    src = Path("wax/workers/main.py").read_text()
    assert "transcribe_local_audio" in src


def test_knowledge_ingest_has_list_and_chunks():
    src = Path("wax/knowledge/ingest.py").read_text()
    assert "list_sources" in src
    assert "recent_chunks_for_context" in src


def test_context_surfaces_learner_materials():
    src = Path("wax/intelligence/context.py").read_text()
    assert "_learner_materials_block" in src


def test_path_isolation_in_workspace():
    src = Path("wax/terminal/workspace.py").read_text()
    assert "path_in_principal_scope" in src
