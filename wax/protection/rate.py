"""
Burst-tolerant student-level rate protection.

Principle:
  receive → verify → dedupe → durably preserve → acknowledge
  → rate / workload decision (process now | defer | throttle)

Never discards an already-accepted message.
Protection is modular and channel-agnostic.
The tutor never sees which limiter produced the decision.

LIMITATION: token-bucket state is process-local. Message durability does NOT
depend on it (persist-first). For multi-worker hard enforcement of sustained
rate across instances, add a shared durable store (Postgres rate_buckets).
Until then each worker applies local burst tolerance.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any
from uuid import UUID

from wax.config.settings import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)


class RateDecision(str, Enum):
    ALLOW = "allow"
    DEFER = "defer"       # accepted but process later (backpressure)
    THROTTLE = "throttle" # temporarily slow this principal
    PROTECT = "protect"   # runaway — hold processing, still durable


@dataclass
class PrincipalRateState:
    timestamps: deque = field(default_factory=deque)
    tokens: float = 0.0
    last_refill: float = 0.0
    cooldown_until: float = 0.0
    deferred_count: int = 0


class RateProtector:
    """
    Token-bucket + sliding-window hybrid.
    Burst capacity absorbs normal human bursts.
    Sustained abnormal volume → throttle / defer.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._states: dict[str, PrincipalRateState] = defaultdict(PrincipalRateState)
        self._settings = get_settings()

    def _key(self, principal_id: UUID | str, channel: str | None = None) -> str:
        return str(principal_id)

    def decide(
        self,
        principal_id: UUID | str,
        *,
        channel: str | None = None,
        now: float | None = None,
        message_count: int = 1,
    ) -> RateDecision:
        if not getattr(self._settings, "rate_limit_enabled", True):
            return RateDecision.ALLOW

        now = now or time.monotonic()
        key = self._key(principal_id, channel)
        burst = int(getattr(self._settings, "rate_burst_capacity", 12) or 12)
        sustained = int(getattr(self._settings, "rate_sustained_per_minute", 30) or 30)
        window = float(getattr(self._settings, "rate_window_seconds", 60) or 60)
        cooldown = float(getattr(self._settings, "rate_cooldown_seconds", 15) or 15)
        queue_limit = int(getattr(self._settings, "rate_queue_limit_per_principal", 40) or 40)

        with self._lock:
            st = self._states[key]
            if st.cooldown_until > now:
                return RateDecision.THROTTLE

            while st.timestamps and st.timestamps[0] < now - window:
                st.timestamps.popleft()

            if st.last_refill == 0.0:
                st.tokens = float(burst)
                st.last_refill = now
            else:
                elapsed = now - st.last_refill
                refill_rate = sustained / window
                st.tokens = min(float(burst), st.tokens + elapsed * refill_rate)
                st.last_refill = now

            cost = float(message_count)
            recent = len(st.timestamps)

            if recent + message_count > queue_limit:
                st.deferred_count += message_count
                logger.info(
                    "rate_protect",
                    principal_id=str(principal_id),
                    recent=recent,
                    decision="protect",
                )
                return RateDecision.PROTECT

            if st.tokens >= cost:
                st.tokens -= cost
                for _ in range(message_count):
                    st.timestamps.append(now)
                return RateDecision.ALLOW

            if recent < sustained:
                st.tokens = max(0.0, st.tokens)
                for _ in range(message_count):
                    st.timestamps.append(now)
                st.deferred_count += 1
                return RateDecision.DEFER

            st.cooldown_until = now + cooldown
            logger.info(
                "rate_throttle",
                principal_id=str(principal_id),
                recent=recent,
                tokens=round(st.tokens, 2),
            )
            return RateDecision.THROTTLE

    def note_processed(self, principal_id: UUID | str, count: int = 1) -> None:
        key = self._key(principal_id)
        with self._lock:
            st = self._states.get(key)
            if st:
                st.deferred_count = max(0, st.deferred_count - count)

    def snapshot(self, principal_id: UUID | str) -> dict[str, Any]:
        key = self._key(principal_id)
        with self._lock:
            st = self._states.get(key)
            if not st:
                return {}
            return {
                "recent_count": len(st.timestamps),
                "tokens": round(st.tokens, 2),
                "deferred_count": st.deferred_count,
                "in_cooldown": st.cooldown_until > time.monotonic(),
            }


_protector: RateProtector | None = None


def get_rate_protector() -> RateProtector:
    global _protector
    if _protector is None:
        _protector = RateProtector()
    return _protector
