"""
WAX closed lifecycle — one circle.

message.received → work.queued → work.claimed
  → World stage (files land; AI decides)
  → tutor (AI + primitives)
  → delivery (paced)
  → turn.completed
  → optional schedule → wake → continue

Failures: rate limit → retrying → resuscitate → complete.
Infrastructure owns durability and security; AI owns decisions and memory.
"""

from __future__ import annotations

from typing import Any

from wax.observability.events import emit


def stage(name: str, **fields: Any) -> None:
    emit(f"lifecycle.{name}", **fields)
