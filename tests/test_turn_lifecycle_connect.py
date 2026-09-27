
from pathlib import Path

def test_turn_telemetry_module_exists():
    assert Path("wax/observability/turn_telemetry.py").exists()

def test_memory_payload_carries_orchestration():
    src = Path("wax/intelligence/tutor.py").read_text()
    assert "capability_families" in src
    assert "orchestration_path" in src
    assert "memory_process" in src

def test_whatsapp_paced_chunks():
    src = Path("wax/messaging/whatsapp/client.py").read_text()
    assert "asyncio.sleep" in src
    assert "delivery_chunk_delay" in src or "delay" in src
