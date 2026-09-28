"""Parse AI-authored directives from free-form model output.

This is NOT a tool registry or function-calling schema.
The model writes natural language and optional fenced directive blocks.
Infrastructure executes those blocks and returns observations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Directive:
    kind: str  # world | memory | schedule | publish | choices | done
    body: str
    parsed: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTurn:
    reply: str  # student-facing text (may be empty if more work needed)
    directives: list[Directive] = field(default_factory=list)
    done: bool = True  # True if no further continuation needed


_FENCE = re.compile(
    r"```(world|memory|schedule|publish|choices)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def parse_agent_output(text: str | None) -> AgentTurn:
    """Extract student reply and optional directive fences from model text."""
    if not text:
        return AgentTurn(reply="", directives=[], done=True)

    directives: list[Directive] = []
    for m in _FENCE.finditer(text):
        kind = m.group(1).lower().strip()
        body = m.group(2).strip()
        directives.append(Directive(kind=kind, body=body, parsed=_parse_body(kind, body)))

    # Strip fences from student-facing reply
    reply = _FENCE.sub("", text).strip()
    # Remove common internal markers
    reply = re.sub(r"\[\[CONTINUE\]\]", "", reply, flags=re.I).strip()

    done = len(directives) == 0 or not any(d.kind != "done" for d in directives)
    if directives:
        done = False
    if "[[DONE]]" in (text or "").upper():
        done = True

    return AgentTurn(reply=reply, directives=directives, done=done and not directives)


def _parse_body(kind: str, body: str) -> dict[str, Any]:
    """Best-effort structured parse; body text is always kept for the runtime."""
    out: dict[str, Any] = {"raw": body}
    if kind == "memory":
        # action: create|update|supersede|forget|search
        for line in body.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                key = k.strip().lower()
                val = v.strip()
                if key in ("action", "type", "memory_type", "id", "memory_id", "content", "query", "tags"):
                    out[key] = val
        if "action" not in out:
            # default: create if content-like
            out["action"] = "create" if len(body) > 10 else "search"
            if out["action"] == "create" and "content" not in out:
                out["content"] = body
    elif kind == "schedule":
        for line in body.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                key = k.strip().lower().replace(" ", "_")
                out[key] = v.strip()
    elif kind == "world":
        out["commands"] = body
    elif kind == "publish":
        out["html_or_path"] = body
        for line in body.splitlines()[:5]:
            if line.lower().startswith("title:"):
                out["title"] = line.split(":", 1)[1].strip()
    elif kind == "choices":
        opts = [ln.strip("- •*").strip() for ln in body.splitlines() if ln.strip()]
        out["choices"] = opts
    return out
