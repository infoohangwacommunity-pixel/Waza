"""Integration-style tests: probe → capabilities → processors → evidence."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from wax.media.assets import append_evidence, list_assets, load_asset, register_asset
from wax.media.ocr import _ocr_quality, ocr_image
from wax.media.probe import probe_local_file
from wax.media.types import ExtractionEvidence, MediaProbe
from wax.tools.transcription import _assess_quality, _find_existing_vosk_model, _model_search_roots


def _silent_wav(path: Path, seconds: float = 0.2) -> None:
    rate = 16000
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))


def test_model_search_roots_include_image_path(monkeypatch):
    monkeypatch.setenv("WAX_MODEL_ROOT", "/data/wax-models")
    monkeypatch.setenv("WAX_IMAGE_MODEL_ROOT", "/opt/wax-models")
    roots = [str(r) for r in _model_search_roots()]
    assert "/data/wax-models" in roots
    assert "/opt/wax-models" in roots


def test_find_existing_model_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("WAX_MODEL_ROOT", str(tmp_path / "vol"))
    monkeypatch.setenv("WAX_IMAGE_MODEL_ROOT", str(tmp_path / "img"))
    assert _find_existing_vosk_model() is None


def test_find_existing_model_from_image_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("WAX_MODEL_ROOT", str(tmp_path / "vol"))
    img = tmp_path / "img" / "models" / "vosk" / "vosk-model-small-en-us-0.15"
    img.mkdir(parents=True)
    (img / "am").mkdir()
    (img / "am" / "final.mdl").write_bytes(b"x")
    monkeypatch.setenv("WAX_IMAGE_MODEL_ROOT", str(tmp_path / "img"))
    found = _find_existing_vosk_model()
    assert found is not None
    assert found == img


def test_ocr_quality_empty():
    status, warnings = _ocr_quality("")
    assert status == "unusable"


def test_ocr_quality_usable():
    status, _ = _ocr_quality("The mitochondria is the powerhouse of the cell in biology class.")
    assert status == "usable"


def test_ocr_image_missing_file(tmp_path):
    ev = ocr_image(tmp_path / "nope.png")
    assert ev.quality_status == "unusable"
    assert "file_not_found" in ev.warnings


def test_asset_register_and_evidence(tmp_path, monkeypatch):
    # Redirect workspace root for principal isolation test
    from wax.terminal import workspace as ws

    monkeypatch.setattr(ws, "workspace_root", lambda: tmp_path)
    principal_id = "test-principal-1"
    media = tmp_path / "principals" / principal_id / "media" / "note.wav"
    media.parent.mkdir(parents=True)
    _silent_wav(media)
    probe = probe_local_file(media)
    manifest = register_asset(principal_id, path=str(media), probe=probe, work_id="w1")
    assert manifest["asset_id"]
    loaded = load_asset(principal_id, manifest["asset_id"])
    assert loaded is not None
    assert loaded["path"] == str(media)
    ev = ExtractionEvidence(
        kind="transcript",
        processor="vosk_local",
        payload={"text": "hello"},
        quality_status="usable",
    )
    updated = append_evidence(principal_id, manifest["asset_id"], ev)
    assert updated is not None
    assert len(updated["evidence"]) == 1
    assets = list_assets(principal_id)
    assert len(assets) >= 1


def test_composition_capabilities_for_video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    probe = probe_local_file(p)
    caps = probe.capabilities.as_list()
    assert "inspect" in caps
    assert "extract_audio" in caps
    assert "extract_frames" in caps
    # No auto workflow — tools must be invoked separately
    assert "tutor_from_video" not in caps


def test_worker_default_auto_transcribe_off():
    src = Path("wax/workers/main.py").read_text()
    assert 'os.environ.get("WAX_AUTO_TRANSCRIBE", "0")' in src


def test_dockerfile_bakes_image_model_path():
    df = Path("Dockerfile").read_text()
    assert "/opt/wax-models" in df
    assert "provision_vosk_model.sh" in df
    assert "WAX_IMAGE_MODEL_ROOT" in df
    assert "WAX_VOSK_ALLOW_DOWNLOAD=0" in df


def test_seed_and_provision_scripts_exist():
    assert Path("scripts/provision_vosk_model.sh").is_file()
    assert Path("scripts/seed_models.sh").is_file()
    assert Path("scripts/eval_stt.py").is_file()


def test_probe_to_evidence_roundtrip_types():
    probe = MediaProbe(path="/x", kind="audio")
    d = probe.to_dict()
    assert "capability_list" in d
    ev = ExtractionEvidence(kind="ocr", processor="tesseract", payload={"text": "a"})
    assert ev.to_dict()["kind"] == "ocr"
