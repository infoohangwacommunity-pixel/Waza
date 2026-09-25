"""
Student-safe error presentation.

Internal diagnostics stay internal.
Students receive natural, generic language — never stack traces, keys, URLs, or exception text.
"""

from __future__ import annotations

import re
from typing import Any

# Patterns that must never appear in student-facing text
_SECRETISH = re.compile(
    r"(?i)("
    r"sk-[a-zA-Z0-9]{10,}"
    r"|ghp_[a-zA-Z0-9]{20,}"
    r"|Bearer\s+[A-Za-z0-9\-._~+/]+=*"
    r"|postgresql(\+asyncpg)?://[^\s]+"
    r"|DATABASE_URL"
    r"|SECRET_KEY\s*="
    r"|api[_-]?key\s*[:=]"
    r"|access[_-]?token\s*[:=]"
    r"|traceback\s*\(most recent call last\)"
    r"|File \"/.+\.py\""
    r")"
)

SAFE_GENERIC = "Something went wrong on my side. Give me a moment and try again."
SAFE_RETRY = "I couldn't finish that just now. Your message is safe, and I'll continue once the system is ready."
SAFE_TIMEOUT = "That took longer than expected. Your message is still here — try again in a moment."


def classify_error(exc: BaseException | str | None) -> str:
    """Internal classification only — never shown to students as-is."""
    text = str(exc or "").lower()
    if any(x in text for x in ("timeout", "timed out", "deadline")):
        return "timeout"
    if any(x in text for x in ("rate limit", "429", "too many requests")):
        return "rate_protection"
    if any(x in text for x in ("connection", "unavailable", "503", "502", "network")):
        return "transient_provider"
    if any(x in text for x in ("auth", "401", "403", "invalid api key")):
        return "permanent_provider"
    if any(x in text for x in ("validation", "invalid input")):
        return "validation"
    return "unexpected"


def student_facing_message(exc: BaseException | str | None = None, *, error_class: str | None = None) -> str:
    """
    Map any internal failure to a safe natural message.
    Guarantees secret-like substrings are stripped.
    """
    cls = error_class or classify_error(exc)
    if cls in ("timeout",):
        msg = SAFE_TIMEOUT
    elif cls in ("transient_provider", "service_interruption", "rate_protection"):
        msg = SAFE_RETRY
    else:
        msg = SAFE_GENERIC
    # Belt-and-suspenders: never emit secret-like material
    if _SECRETISH.search(msg):
        return SAFE_GENERIC
    return msg


def sanitize_for_student(text: str | None) -> str:
    """Strip accidental secret/path/traceback leakage from a string before student delivery."""
    if not text:
        return SAFE_GENERIC
    if _SECRETISH.search(text):
        return SAFE_GENERIC
    # Avoid dumping long exception-like blobs
    if "Traceback" in text or 'File "/' in text:
        return SAFE_GENERIC
    return text
