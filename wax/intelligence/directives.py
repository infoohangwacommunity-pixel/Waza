"""
Minimal machine-readable bridge from model text to infrastructure.

The AI reasons in free text. When it needs infrastructure to act, it may
emit a fenced block named for a domain of reality. Infrastructure validates
security and executes. The AI decides why.

Channels (domains of reality):

  world    — execute inside the student's World
  state    — durable student persistence
  time     — schedule or inspect time
  publish  — secure temporary surface
  interact — choices on the current channel
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CHANNELS = frozenset({"world", "state", "time", "publish", "interact"})

_FENCE = re.compile(
    r"```(world|state|time|publish|interact)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass
class Directive:
    """One infrastructure request parsed from model output."""

    channel: str
    body: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTurn:
    reply: str
    directives: list[Directive] = field(default_factory=list)


def parse_agent_output(text: str | None) -> AgentTurn:
    """Split student-facing reply from optional infrastructure fences."""
    if not text:
        return AgentTurn(reply="", directives=[])

    directives: list[Directive] = []
    for m in _FENCE.finditer(text):
        channel = m.group(1).lower().strip()
        if channel not in CHANNELS:
            continue
        body = m.group(2).strip()
        fields, body = _split_fields(body)
        _enrich(channel, fields, body)
        directives.append(Directive(channel=channel, body=body, fields=fields))

    reply = _FENCE.sub("", text).strip()
    return AgentTurn(reply=reply, directives=directives)


def _split_fields(body: str) -> tuple[dict[str, Any], str]:
    """
    Leading simple `key: value` lines become fields; the rest is free body.

    Not a schema catalogue — infrastructure reads only keys it understands.
    """
    fields: dict[str, Any] = {}
    lines = body.splitlines()
    consumed = 0
    for i, line in enumerate(lines):
        if ":" not in line:
            break
        key, _, val = line.partition(":")
        key = key.strip().lower()
        if not key or " " in key or not re.match(r"^[a-z][a-z0-9_]*$", key):
            break
        fields[key] = val.strip()
        consumed = i + 1
    rest = "\n".join(lines[consumed:]).strip() if consumed else body
    if not fields:
        rest = body
    return fields, rest


def _enrich(channel: str, fields: dict[str, Any], body: str) -> None:
    """Minimal structure extraction from free body — not specialized actions."""
    if channel == "world":
        fields.setdefault("command", body)
        return

    if channel == "state" and "action" not in fields:
        if "query" in fields:
            fields["action"] = "search"
        elif body:
            fields["action"] = "create"
            fields.setdefault("content", body)
        return

    if channel == "time" and "delay" in fields and "delay_seconds" not in fields:
        fields["delay_seconds"] = fields["delay"]
        return

    if channel == "interact":
        choices: list[str] = []
        prompt_line: str | None = None
        for line in (body or "").splitlines():
            s = line.strip()
            if s.startswith(("- ", "* ")):
                choices.append(s[2:].strip())
            elif s and prompt_line is None and not choices:
                prompt_line = s
        if choices:
            fields["choices"] = choices
        if prompt_line and "prompt" not in fields:
            fields["prompt"] = prompt_line
        return

    if channel == "publish":
        fields.setdefault("html", body)
