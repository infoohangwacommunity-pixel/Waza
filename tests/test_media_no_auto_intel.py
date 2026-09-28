Media path is download+stage only — no auto interpretation.

from pathlib import Path


def test_media_module_no_stt_ocr():
    src = Path("wax/messaging/media.py").read_text().lower()
    assert "whisper" not in src
    assert "transcri" not in src
    assert "ocr" not in src
    assert "classif" not in src


def test_stage_is_placement_only():
    src = Path("wax/world/stage.py").read_text()
    assert "AI decides how to process" in src or "only places" in src.lower()


def test_worker_message_path_no_auto_stt():
    src = Path("wax/workers/main.py").read_text()
    assert "No auto-STT" in src or "media_placed_in_world" in src
    assert "whisper" not in src.lower()
