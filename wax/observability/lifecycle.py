
"""
Waza intelligence lifecycle — one circle.

message.received → work.queued → work.claimed
  → context_intelligence (digest → brief → families)
  → tutor (bounded tools)
  → delivery (paced chunks)
  → turn.completed (telemetry)
  → memory_process → digest refresh
  → research suggestion (when cool)
  → next message

Failures: rate limit → retrying → resuscitate → complete.
"""

from __future__ import annotations

from typing import Any

from wax.observability.events import emit


def stage(name: str, **fields: Any) -> None:
    emit(f"lifecycle.{name}", **fields)
