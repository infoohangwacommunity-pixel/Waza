"""
Canonical model/tool protocol normalization.

Providers must map native responses into CompletionResponse-shaped data.
Raw tool markup in assistant *content* is never learner-visible text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

def _emit(event: str, **fields):
    try:
        from wax.observability.events import emit as _e
        _e(event, **fields)
    except Exception:
        pass


def _log():
    try:
        from wax.observability.logging import get_logger
        return get_logger(__name__)
    except Exception:
        import logging
        return logging.getLogger(__name__)

# Suspicious markup that must never reach WhatsApp/Telegram as the reply body.
_TOOL_LEAK_PATTERNS = [
    re.compile(r"<\|tool_call[^|]*>", re.I),
    re.compile(r"<\|tool_call_begin\|>", re.I),
    re.compile(r"<\|tool_calls_section", re.I),
    re.compile(r"</?tool_call>", re.I),
    re.compile(r"```(?:json|tool)?\s*\{\s*\"name\"\s*:\s*\"create_surface\"", re.I),
]

# Large HTML blobs that belong in create_surface args, not chat
_HTML_DOC_RE = re.compile(
    r"<!DOCTYPE\s+html|<html[\s>]|<head[\s>]|<body[\s>]",
    re.I,
)


@dataclass
class ProviderCapabilities:
    structured_tool_calls: bool = True
    json_schema: bool = False
    vision: bool = False
    audio_input: bool = False
    document_input: bool = False
    streaming: bool = False


@dataclass
class NormalizedToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


def parse_arguments(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            val = json.loads(s)
            return val if isinstance(val, dict) else {"value": val}
        except json.JSONDecodeError:
            return {"_raw": s[:4000]}
    return {}


def normalize_tool_calls(raw_calls: list[Any] | None) -> list[dict[str, Any]]:
    """Normalize provider tool_calls into OpenAI-shaped dicts with dict arguments."""
    out: list[dict[str, Any]] = []
    for i, tc in enumerate(raw_calls or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        if not isinstance(fn, dict):
            fn = {}
        name = (fn.get("name") or tc.get("name") or "").strip()
        if not name:
            continue
        args = parse_arguments(fn.get("arguments") if "arguments" in fn else tc.get("arguments"))
        tc_id = str(tc.get("id") or f"call_{i}_{name}")
        out.append(
            {
                "id": tc_id,
                "type": tc.get("type") or "function",
                "function": {
                    "name": name,
                    "arguments": args,  # dict — execute_tool accepts both
                },
            }
        )
    return out


def content_has_tool_protocol_leak(text: str | None) -> bool:
    if not text:
        return False
    for pat in _TOOL_LEAK_PATTERNS:
        if pat.search(text):
            return True
    # Bare create_surface + html document dump
    if "create_surface" in text.lower() and _HTML_DOC_RE.search(text):
        return True
    if _HTML_DOC_RE.search(text) and len(text) > 800:
        return True
    return False


def strip_tool_protocol_from_content(text: str | None) -> str:
    """Remove leaked tool markup / HTML docs from assistant content for delivery."""
    if not text:
        return ""
    s = text
    # Remove common special-token tool blocks
    s = re.sub(
        r"<\|tool_call[^|]*\|>.*?(?:<\|tool_call_end\|>|<\|end\|>|$)",
        "",
        s,
        flags=re.I | re.S,
    )
    s = re.sub(r"<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>", "", s, flags=re.I | re.S)
    s = re.sub(r"<\|tool_call_begin\|>.*?<\|tool_call_end\|>", "", s, flags=re.I | re.S)
    s = re.sub(r"</?tool_call>", "", s, flags=re.I)
    # If remaining is essentially an HTML document, drop it
    if _HTML_DOC_RE.search(s) and len(s) > 400:
        s = re.sub(r"(?is)<!DOCTYPE\s+html.*?</html>", "", s)
        s = re.sub(r"(?is)<html\b.*?</html>", "", s)
    s = s.strip()
    return s


def sanitize_learner_reply(text: str | None, *, had_tool_results: bool = False) -> str:
    """Final gate before WhatsApp/Telegram/Message persistence."""
    cleaned = strip_tool_protocol_from_content(text)
    if content_has_tool_protocol_leak(cleaned):
        _log().warning("tool_protocol_leak_blocked")
        _emit("tool_protocol_anomaly", stage="sanitize_learner_reply")
        if had_tool_results:
            return (
                "I put that together for you, but the interactive link did not come through cleanly. "
                "Say if you want me to try the page again."
            )
        return (
            "I couldn't complete that interactive step cleanly just now. "
            "I can try again, or we can continue in chat."
        )
    if not cleaned.strip():
        if had_tool_results:
            return "Done — let me know what you want to do next."
        return ""
    return cleaned


def normalize_completion_payload(
    *,
    content: str | None,
    tool_calls: list[Any] | None,
    finish_reason: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    raw: dict | None = None,
) -> dict[str, Any]:
    """
    Single choke point for provider adapters before Tutor sees the response.
    """
    normalized_calls = normalize_tool_calls(tool_calls)
    text = content if content is not None else None
    anomaly = False

    if content_has_tool_protocol_leak(text):
        anomaly = True
        _emit(
            "tool_protocol_anomaly",
            provider=provider or "",
            model=model or "",
            structured_tool_calls=len(normalized_calls),
            content_len=len(text or ""),
        )
        try:
            _log().warning(
                "tool_protocol_anomaly",
                provider=provider,
                model=model,
                structured_calls=len(normalized_calls),
                content_preview=(text or "")[:120],
            )
        except TypeError:
            _log().warning(
                "tool_protocol_anomaly provider=%s model=%s calls=%s",
                provider,
                model,
                len(normalized_calls),
            )
        # Prefer structured calls; never leave leak in content for the tutor final path
        if normalized_calls:
            text = strip_tool_protocol_from_content(text) or None
        else:
            # No structured calls — strip leak; do not invent tool execution from text
            text = strip_tool_protocol_from_content(text) or None

    return {
        "content": text,
        "tool_calls": normalized_calls,
        "finish_reason": finish_reason,
        "model": model,
        "provider": provider,
        "raw": raw,
        "protocol_anomaly": anomaly,
    }


_URL_RE = re.compile(r"https?://\S+", re.I)


def verified_urls_from_tool_results(tool_results: list[dict] | None) -> set[str]:
    """URLs that tools actually returned (page_url, url, public_url)."""
    out: set[str] = set()
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        res = item.get("result") if "result" in item else item
        if not isinstance(res, dict) or not res.get("ok"):
            continue
        for key in ("page_url", "url", "public_url", "link"):
            v = res.get(key)
            if isinstance(v, str) and v.startswith("http"):
                out.add(v.rstrip(".,);]}"))
        # nested data
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        for key in ("page_url", "url", "public_url"):
            v = data.get(key)
            if isinstance(v, str) and v.startswith("http"):
                out.add(v.rstrip(".,);]}"))
    return out


def surface_tool_succeeded(tool_results: list[dict] | None) -> bool:
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        res = item.get("result") if "result" in item else item
        if name in ("create_surface", "update_surface") and isinstance(res, dict) and res.get("ok"):
            if res.get("page_url") or (isinstance(res.get("data"), dict) and res["data"].get("page_url")):
                return True
    return False


def enforce_verified_artifacts(
    text: str | None,
    *,
    tool_results: list[dict] | None = None,
    surfaces_tools_exposed: bool = False,
) -> str:
    """
    Learner-visible text may only cite surface/page URLs that tools returned.

    Prevents invented domains (e.g. wax.run) when create_surface never ran.
    """
    if not text:
        return ""
    verified = verified_urls_from_tool_results(tool_results)
    cleaned = text
    found_unverified = False
    for m in list(_URL_RE.finditer(text)):
        url = m.group(0).rstrip(".,);]}")
        if url in verified:
            continue
        # Block unverified links when surface tools were in play or URL looks like a page share
        if surfaces_tools_exposed or "/s/" in url or "wax.run" in url.lower() or "surface" in url.lower():
            found_unverified = True
            cleaned = cleaned.replace(m.group(0), "")
        elif not verified and surfaces_tools_exposed:
            found_unverified = True
            cleaned = cleaned.replace(m.group(0), "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if found_unverified and not verified:
        note = (
            "I wasn't able to publish a live page this turn — no verified link was created. "
            "I can try again if you still want an interactive page."
        )
        if not cleaned or len(cleaned) < 20:
            return note
        # Avoid stacking false "link is ready" with the note
        low = cleaned.lower()
        if any(p in low for p in ("link is ready", "page is ready", "here's the link", "here is the link", "i've created", "i created the page")):
            return note
        return cleaned + "\n\n" + note
    if found_unverified and verified:
        # Keep text but ensure at least one verified URL remains visible
        primary = next(iter(verified))
        if primary not in cleaned:
            cleaned = cleaned + f"\n\n{primary}"
    return cleaned
