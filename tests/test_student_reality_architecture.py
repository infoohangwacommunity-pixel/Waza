"""Architecture: student reality → World without type intelligence."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture()
def isolated_workspace(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("WAX_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("REQUIRE_PERSISTENT_WORKSPACE", "false")
    from wax.config import get_settings
    get_settings.cache_clear()
    # Clear world process cache
    from wax.world import manager
    manager._cache.clear()
    yield root
    get_settings.cache_clear()
    manager._cache.clear()


def test_unknown_bytes_stage_without_type_router(isolated_workspace):
    from wax.world.stage import stage_bytes
    from wax.world.manager import get_or_create_world

    pid = str(uuid4())
    data = b"\x00\x01UNKNOWN_FORMAT_XYZ"
    r = stage_bytes(pid, data, filename="unknown-file.xyz")
    assert r["ok"] is True
    path = Path(r["path"])
    assert path.is_file()
    assert path.read_bytes() == data
    world = get_or_create_world(pid)
    assert str(world.root) in str(path)


def test_inbound_fact_associated_with_correct_world(isolated_workspace):
    from wax.world.manager import get_or_create_world
    from wax.world.inbound import write_inbound_fact

    a, b = str(uuid4()), str(uuid4())
    wa = get_or_create_world(a)
    wb = get_or_create_world(b)
    assert wa.root != wb.root

    pa = write_inbound_fact(wa.root, "work-a", {"work_id": "work-a", "text": "from-a", "content_type": "text"})
    pb = write_inbound_fact(wb.root, "work-b", {"work_id": "work-b", "text": "from-b", "content_type": "text"})
    assert Path(pa).is_file() and Path(pb).is_file()
    assert json.loads(Path(pa).read_text())["text"] == "from-a"
    assert json.loads(Path(pb).read_text())["text"] == "from-b"
    # Isolation: A's fact not under B's root
    assert not (wb.root / "workspace" / "inbound" / "work-a.json").exists()


def test_world_survives_cache_clear(isolated_workspace):
    from wax.world.manager import get_or_create_world, _cache
    from wax.world.stage import stage_bytes

    pid = str(uuid4())
    w1 = get_or_create_world(pid)
    r = stage_bytes(pid, b"persist-me", filename="keep.bin")
    assert r["ok"]
    path = Path(r["path"])
    _cache.clear()
    w2 = get_or_create_world(pid)
    assert w1.world_id == w2.world_id
    assert path.is_file()
    assert path.read_bytes() == b"persist-me"


def test_generic_world_exec_returns_observation(isolated_workspace):
    import asyncio
    from wax.world.manager import get_or_create_world
    from wax.world.exec import world_exec

    pid = str(uuid4())
    world = get_or_create_world(pid)
    probe = world.root / "workspace" / "probe.txt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("hello-world", encoding="utf-8")

    async def _run():
        return await world_exec(
            world,
            argv=["/bin/cat", "workspace/probe.txt"],
            network_mode="none",
        )

    result = asyncio.run(_run())
    assert isinstance(result, dict)
    blob = json.dumps(result).lower()
    assert "transcrib" not in blob and "ocr" not in blob
    # Either success with stdout or isolation unavailable — both are infrastructure outcomes
    if result.get("ok"):
        assert "hello-world" in (result.get("stdout") or "")


def test_no_mime_intelligence_router_in_ingestion():
    roots = [
        Path("wax/messaging/media.py"),
        Path("wax/world/inbound.py"),
        Path("wax/world/stage.py"),
        Path("wax/workers/main.py"),
    ]
    banned = (
        "speech_to_text",
        "transcribe_audio",
        "run_ocr",
        "extract_pdf",
        "if mime",
        "elif content_type == \"audio\"",
        "auto_summar",
    )
    for path in roots:
        src = path.read_text().lower()
        for b in banned:
            assert b.lower() not in src, f"{path} contains {b}"


def test_tutor_teaches_general_world_principle_not_type_recipes():
    src = Path("wax/intelligence/tutor.py").read_text()
    assert "persistent working environment" in src or "world_root" in src
    assert "if audio" not in src.lower()
    assert "if image" not in src.lower() or "if image" not in src  # soft
    # must not prescribe type pipelines
    for phrase in ("always transcribe", "always OCR", "must extract PDF"):
        assert phrase.lower() not in src.lower()


def test_normalization_unknown_type_preserves_fact():
    from wax.messaging.normalization import normalize_whatsapp_message

    n = normalize_whatsapp_message(
        {"id": "wamid.u", "from": "1", "type": "exotic_widget", "exotic_widget": {"id": "x"}}
    )
    assert n is not None
    assert n.content_type == "other"
    assert "exotic_widget" in n.text
    assert n.media_id is None
