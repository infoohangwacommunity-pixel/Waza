"""
Minimal machine-readable bridge from model text to infrastructure.

This is NOT a tool catalogue or application capability menu.

The AI reasons in free text. When it needs infrastructure to act, it may
emit a fenced block. Infrastructure validates security and executes.

Infrastructure channels (domains of reality, not specialized app features):

  world    — general execution environment (files, packages, terminal)
  state    — durable student persistence (memory lifecycle)
  time     — delayed / scheduled wake
  publish  — secure temporary publication (surfaces)
  interact — channel interaction (e.g. choice buttons)

Aliases kept for continuity: memory→state, schedule→time, choices→interact.

The model decides the objective. Infrastructure does not plan teaching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Canonical infrastructure channels
CHANNELS = frozenset({"world", "state", "time", "publish", "interact"})

# Backward-compatible aliases → canonical
_ALIASES = {
    "memory": "state",
    "schedule": "time",
    "choices": "interact",
    "do": "world",  # bare do defaults to world if no channel: line
}


@dataclass
class Directive:
    """One infrastructure request parsed from model output."""

    channel: str  # canonical: world | state | time | publish | interact
    body: str
    parsed: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        """Alias for channel — used by existing callers."""
        return self.channel


@dataclass
class AgentTurn:
    reply: str
    directives: list[Directive] = field(default_factory=list)
    done: bool = True


# Accept canonical names and aliases in fence language tags
_FENCE = re.compile(
    r"```(world|state|time|publish|interact|memory|schedule|choices|do|infra)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def parse_agent_output(text: str | None) -> AgentTurn:
    """Extract student-facing reply and optional infrastructure directives."""
    if not text:
        return AgentTurn(reply="", directives=[], done=True)

    directives: list[Directive] = []
    for m in _FENCE.finditer(text):
        tag = m.group(1).lower().strip()
        body = m.group(2).strip()
        channel, body2, parsed = _normalize(tag, body)
        if channel not in CHANNELS:
            continue
        directives.append(Directive(channel=channel, body=body2, parsed=parsed))

    reply = _FENCE.sub("", text).strip()
    reply = re.sub(r"\[\[CONTINUE\]\]", "", reply, flags=re.I).strip()
    reply = re.sub(r"\[\[DONE\]\]", "", reply, flags=re.I).strip()

    done = len(directives) == 0
    if "[[DONE]]" in (text or "").upper():
        done = True

    return AgentTurn(reply=reply, directives=directives, done=done)


def _normalize(tag: str, body: str) -> tuple[str, str, dict[str, Any]]:
    """
    Map fence tag + body → (canonical_channel, body, parsed headers).

    For ```infra or ```do, the first line may be `channel: world` etc.
    """
    parsed: dict[str, Any] = {"raw": body}
    lines = body.splitlines()
    channel = _ALIASES.get(tag, tag)

    if tag in ("do", "infra") and lines:
        # Optional first-line channel: ...
        first = lines[0].strip()
        if first.lower().startswith("channel:"):
            ch = first.split(":", 1)[1].strip().lower()
            channel = _ALIASES.get(ch, ch)
            body = "\n".join(lines[1:]).strip()
            parsed["raw"] = body
            lines = body.splitlines()

    # Light key:value headers (not a schema catalogue) — remaining free text is body
    header_keys = {
        "action",
        "type",
        "memory_type",
        "id",
        "memory_id",
        "content",
        "query",
        "tags",
        "title",
        "delay",
        "delay_seconds",
        "reason",
        "prompt",
        "preferred_lifetime_hours",
        "lifetime_hours",
        "hours",
        "ttl_hours",
        "extend_hours",
        "surface_id",
        "lifecycle_intent",
    }
    consumed = 0
    for i, line in enumerate(lines):
        if ":" not in line:
            break
        k, _, v = line.partition(":")
        key = k.strip().lower()
        if key not in header_keys and key != "channel":
            break
        if key == "channel":
            channel = _ALIASES.get(v.strip().lower(), v.strip().lower())
            consumed = i + 1
            continue
        parsed[key] = v.strip()
        consumed = i + 1

    if consumed:
        rest = "\n".join(lines[consumed:]).strip()
        if rest:
            body = rest
            parsed["raw"] = rest

    # Defaults for state channel when no action header
    if channel == "state" and "action" not in parsed:
        if "query" in parsed or (body and body.lower().startswith("search")):
            parsed.setdefault("action", "search")
        else:
            parsed.setdefault("action", "create")
            if "content" not in parsed and body:
                parsed["content"] = body

    if channel == "time" and "delay_seconds" not in parsed and "delay" in parsed:
        parsed["delay_seconds"] = parsed["delay"]

    if channel == "interact":
        choices = []
        for line in (body or "").splitlines():
            line = line.strip()
            if line.startswith("- ") or line.startswith("* "):
                choices.append(line[2:].strip())
            elif line and not line.endswith(":") and "prompt" not in parsed:
                # first non-bullet may be prompt if choices already started
                pass
        if choices:
            parsed["choices"] = choices
        if "prompt" not in parsed and body:
            # first line as prompt if bullets exist
            for line in body.splitlines():
                s = line.strip()
                if s and not s.startswith(("-", "*")):
                    parsed.setdefault("prompt", s)
                    break

    if channel == "publish":
        for line in (body or "").splitlines():
            if line.lower().startswith("title:"):
                parsed["title"] = line.split(":", 1)[1].strip()

    return channel, body, parsed
