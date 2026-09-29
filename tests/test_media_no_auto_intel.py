"""Media path is download+stage only — no auto interpretation."""

from pathlib import Path


def test_media_module_no_stt_ocr_libraries():
    src = Path("wax/messaging/media.py").read_text().lower()
    for banned in ("whisper", "vosk", "pytesseract", "easyocr"):
        assert banned not in src
    assert "def transcribe" not in src
    assert "def run_ocr" not in src


def test_stage_is_placement_only():
    src = Path("wax/world/stage.py").read_text()
    assert "AI decides how to process" in src or "only places" in src.lower()


def test_worker_message_path_no_media_prepare_job():
    src = Path("wax/workers/main.py").read_text()
    assert "media_placed_in_world" in src
    assert "process_media_prepare" not in src
    assert 'kind == "media_prepare"' not in src


def test_tutor_media_is_fact_only():
    src = Path("wax/intelligence/tutor.py").read_text()
    assert "Inbound file" in src
    assert "def transcribe" not in src
