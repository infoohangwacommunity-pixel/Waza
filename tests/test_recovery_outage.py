"""Recovery and outage context unit tests (no DB required for pure helpers)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from wax.work.recovery import build_outage_context, attach_outage_to_payload


def test_build_outage_context_bounded():
    ctx = build_outage_context(wait_seconds=999999, preserved_message_count=3)
    assert ctx["approximate_wait_seconds"] <= 86400
    assert ctx["preserved_message_count"] == 3


def test_attach_outage_preserves_existing():
    work = SimpleNamespace(
        metadata_={"recovery": True},
        created_at=datetime.now(timezone.utc) - timedelta(seconds=400),
    )
    payload = {"outage_context": {"recovered_after_interruption": True, "approximate_wait_seconds": 10}}
    out = attach_outage_to_payload(payload, work=work)
    assert out["outage_context"]["approximate_wait_seconds"] == 10


def test_attach_outage_detects_long_queue():
    work = SimpleNamespace(
        metadata_={},
        created_at=datetime.now(timezone.utc) - timedelta(seconds=400),
    )
    out = attach_outage_to_payload({}, work=work)
    assert out.get("outage_context", {}).get("recovered_after_interruption") is True
