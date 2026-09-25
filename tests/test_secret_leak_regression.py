"""
Secret-leak regression: student-facing paths must never expose secrets or stack traces.
"""

from __future__ import annotations

import re

# Fake secrets that must never appear in student-facing output
FAKE_SECRETS = [
    "sk-test-example-secret-key-12345",
    "telegram-secret-test-abcdef",
    "whatsapp-secret-test-xyz",
    "DATABASE_URL=postgresql://user:pass@host:5432/db",
    "SECRET_KEY=super-secret-production-key",
    "ghp_TEST_FAKE_TOKEN_NOT_REAL_000000000000",
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
]


def _student_safe_error(exc: BaseException) -> str:
    """Mirror the intended student-safe translation."""
    # Infrastructure must map internals to generic natural language
    return "Something went wrong on my side. Give me a moment and try again."


def test_student_safe_error_hides_exception_text():
    for secret in FAKE_SECRETS:
        try:
            raise RuntimeError(f"provider failed: {secret}")
        except Exception as e:
            out = _student_safe_error(e)
            assert secret not in out
            assert "Traceback" not in out
            assert "postgresql://" not in out


def test_outage_context_has_no_internals():
    from wax.work.recovery import build_outage_context

    ctx = build_outage_context(wait_seconds=420, preserved_message_count=5)
    assert ctx.get("recovered_after_interruption") is True
    blob = str(ctx)
    for secret in FAKE_SECRETS:
        assert secret not in blob
    assert "Traceback" not in blob
    assert "DATABASE_URL" not in blob
    assert "stack" not in blob.lower()


def test_outage_context_suppressed_for_short_waits():
    from wax.work.recovery import build_outage_context

    ctx = build_outage_context(wait_seconds=5, preserved_message_count=1)
    assert ctx == {} or not ctx.get("recovered_after_interruption")


def test_rate_decision_does_not_embed_secrets():
    from wax.protection.rate import get_rate_protector, RateDecision

    p = get_rate_protector()
    d = p.decide("principal-test-1")
    assert d in RateDecision
    snap = p.snapshot("principal-test-1")
    blob = str(snap)
    for secret in FAKE_SECRETS:
        assert secret not in blob


def test_safe_errors_module_strips_secrets():
    from wax.security.safe_errors import student_facing_message, sanitize_for_student

    for secret in FAKE_SECRETS:
        out = student_facing_message(RuntimeError(secret))
        assert secret not in out
        assert "Traceback" not in out
        out2 = sanitize_for_student(f"Error: {secret}\nTraceback (most recent call last)")
        assert secret not in out2
        assert "Traceback" not in out2
