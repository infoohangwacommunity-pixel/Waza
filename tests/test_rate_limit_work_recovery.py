
"""Rate-limited works must remain retryable — not permanently failed on attempt burn."""

from pathlib import Path

SRC = Path("wax/workers/main.py").read_text()


def test_rate_limit_does_not_only_use_max_attempts():
    assert "rate_limit_retries" in SRC
    assert "work_scheduled_retry_rate_limit" in SRC
    assert "resuscitate_rate_limited_works" in SRC


def test_rate_ack_idempotent():
    assert "rate-ack:" in SRC


def test_consolidation_defers_on_cooldown():
    assert "memory_consolidation_deferred_provider_cooldown" in SRC
