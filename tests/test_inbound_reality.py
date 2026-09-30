"""Inbound student reality lands in the World without type intelligence."""

from pathlib import Path
import json
import tempfile


def test_write_inbound_fact_is_plain_json(tmp_path):
    from wax.world.inbound import write_inbound_fact

    path = write_inbound_fact(
        tmp_path,
        "work-1",
        {
            "work_id": "work-1",
            "text": "hello",
            "content_type": "text",
            "channel": "whatsapp",
            "media": None,
        },
    )
    data = json.loads(Path(path).read_text())
    assert data["text"] == "hello"
    assert data["content_type"] == "text"
    assert "recorded_at" in data
    # no intelligence fields
    for banned in ("transcript", "ocr", "summary", "embedding", "classification"):
        assert banned not in data


def test_normalization_location_is_factual():
    from wax.messaging.normalization import normalize_whatsapp_message

    n = normalize_whatsapp_message(
        {
            "id": "wamid.x",
            "from": "15551234567",
            "type": "location",
            "location": {"latitude": 1.2, "longitude": 3.4, "name": "X"},
        }
    )
    assert n is not None
    assert "latitude=1.2" in n.text
    assert n.content_type == "other"
    assert n.media_id is None


def test_media_module_doc_forbids_intelligence():
    src = Path("wax/messaging/media.py").read_text()
    assert "Does not transcribe" in src or "not transcribe" in src.lower()
