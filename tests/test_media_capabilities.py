"""Media capability layer: probe, transcription quality, no hardcoded workflows."""

from __future__ import annotations

import wave
from pathlib import Path

from wax.media.probe import probe_local_file
from wax.media.types import MediaCapabilities, TranscriptionQuality
from wax.tools.transcription import _assess_quality


def _write_silent_wav(path: Path, seconds: float = 0.3, rate: int = 16000) -> None:
    n = int(rate * seconds)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n)


def test_probe_image_capabilities(tmp_path: Path):
    p = tmp_path / "page.png"
    # minimal PNG header-ish bytes — probe uses suffix primarily
    p.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    # workspace root may not include tmp_path — probe still works on any file
    probe = probe_local_file(p)
    assert probe.kind == "image"
    assert probe.capabilities.ocr is True
    assert probe.capabilities.vision is True
    assert probe.capabilities.transcribe is False
    assert "ocr" in probe.capabilities.as_list()
    assert "transcribe" not in probe.capabilities.as_list()


def test_probe_audio_capabilities(tmp_path: Path, monkeypatch):
    from wax.media import probe as probe_mod

    # Avoid requiring workspace for path checks in ffmpeg; probe itself doesn't need workspace
    p = tmp_path / "note.ogg"
    p.write_bytes(b"OggS\x00fake")
    probe = probe_local_file(p)
    assert probe.kind == "audio"
    assert probe.capabilities.transcribe is True
    assert probe.capabilities.ocr is False


def test_probe_pdf_capabilities(tmp_path: Path):
    p = tmp_path / "notes.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    probe = probe_local_file(p)
    assert probe.kind == "document"
    assert probe.capabilities.pdf_text is True


def test_probe_video_capabilities(tmp_path: Path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    probe = probe_local_file(p)
    assert probe.kind == "video"
    assert probe.capabilities.extract_audio is True
    assert probe.capabilities.extract_frames is True


def test_quality_empty_unusable():
    q = _assess_quality(text="", word_confs=[])
    assert q.status == "unusable"
    assert q.empty is True


def test_quality_high_conf_usable():
    q = _assess_quality(text="hello world this is clear speech", word_confs=[0.9, 0.88, 0.92, 0.85])
    assert q.status == "usable"


def test_quality_low_conf_unusable():
    q = _assess_quality(text="a b c d e", word_confs=[0.1, 0.15, 0.12, 0.2, 0.1])
    assert q.status == "unusable"


def test_quality_mid_uncertain():
    q = _assess_quality(text="maybe this is ok speech here", word_confs=[0.5, 0.45, 0.55, 0.48])
    assert q.status == "uncertain"


def test_transcription_result_dict_shape():
    from wax.media.types import TranscriptionResult

    r = TranscriptionResult(
        ok=True,
        transcript="hello",
        provider="vosk_local",
        model="vosk-model-small-en-us-0.15",
        quality=TranscriptionQuality(status="usable", mean_word_conf=0.9),
    )
    d = r.to_dict()
    assert d["ok"] is True
    assert d["quality"]["status"] == "usable"
    assert "provenance" in d


def test_inspect_handler_requires_path():
    import asyncio
    from wax.tools.registry import handle_inspect_media

    out = asyncio.run(handle_inspect_media(None, {}, {}))
    assert out["ok"] is False
    assert out["error"] == "path_required"


def test_telegram_video_normalization():
    from wax.messaging.normalization import normalize_telegram_update

    payload = {
        "message": {
            "message_id": 1,
            "chat": {"id": 99},
            "from": {"id": 1, "first_name": "A"},
            "video": {"file_id": "vid123", "duration": 5},
        }
    }
    n = normalize_telegram_update(payload)
    assert n is not None
    assert n.content_type == "video"
    assert n.media_id == "vid123"


def test_worker_does_not_always_force_transcript_as_text():
    """Architecture: unusable transcript must not become user text."""
    src = Path("wax/workers/main.py").read_text()
    assert "transcript_quality" in src
    assert 'quality == "usable"' in src
    assert "WAX_AUTO_TRANSCRIBE" in src
    assert "media_probe" in src


def test_capabilities_not_educational_workflows():
    """Ensure we did not add tutor_from_* style tools."""
    src = Path("wax/intelligence/tutor.py").read_text()
    forbidden = [
        "tutor_from_audio",
        "teach_from_image",
        "quiz_from_pdf",
        "analyze_chemistry_video",
        "study_document",
        "lesson_from_notes",
    ]
    for name in forbidden:
        assert f'name="{name}"' not in src
    assert 'name="inspect_media"' in src
    assert 'name="transcribe_audio"' in src
    assert 'name="extract_video_audio"' in src
    assert 'name="extract_video_frames"' in src


def test_sender_still_does_not_represent():
    import ast
    import inspect
    from wax.delivery import senders

    def _calls_present(fn) -> bool:
        tree = ast.parse(inspect.getsource(fn))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "present_for_channel":
                    return True
                if isinstance(node.func, ast.Attribute) and node.func.attr == "present_for_channel":
                    return True
        return False

    assert not _calls_present(senders.send_whatsapp)
    assert not _calls_present(senders.deliver)
