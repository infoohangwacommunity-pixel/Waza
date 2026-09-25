"""
Burst-tolerant student-level rate protection.

Order:
  receive → verify → dedupe → durably preserve → acknowledge
  → rate / workload decision (process now | defer | throttle)

Never discards an already-accepted message.

Durable path: principal_workloads row with SELECT FOR UPDATE.
Process-local path: fallback only when no DB session is available
(tests / early boot). Correctness of message durability never depends
on the limiter.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from wax.config.settings import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)


class RateDecision(str, Enum):
    ALLOW = "allow"
    DEFER = "defer"
    THROTTLE = "throttle"
    PROTECT = "protect"


@dataclass
class PrincipalRateState:
    timestamps: deque = field(default_factory=deque)
    tokens: float = 0.0
    last_refill: float = 0.0
    cooldown_until: float = 0.0
    deferred_count: int = 0


def _params():
    s = get_settings()
    return {
        "enabled": bool(getattr(s, "rate_limit_enabled", True)),
        "burst": int(getattr(s, "rate_burst_capacity", 12) or 12),
        "sustained": int(getattr(s, "rate_sustained_per_minute", 30) or 30),
        "window": float(getattr(s, "rate_window_seconds", 60) or 60),
        "cooldown": float(getattr(s, "rate_cooldown_seconds", 15) or 15),
        "queue_limit": int(getattr(s, "rate_queue_limit_per_principal", 40) or 40),
    }


def decide_from_counts(*, tokens: float, recent: int, message_count: int, cooldown_active: bool) -> tuple[str, float]:
    """Pure decision function — no I/O. Used by durable and local paths and tests."""
    p = _params()
    if not p["enabled"]:
        return RateDecision.ALLOW.value, tokens
    if cooldown_active:
        return RateDecision.THROTTLE.value, tokens
    cost = float(message_count)
    if recent + message_count > p["queue_limit"]:
        return RateDecision.PROTECT.value, tokens
    if tokens >= cost:
        return RateDecision.ALLOW.value, tokens - cost
    if recent < p["sustained"]:
        return RateDecision.DEFER.value, max(0.0, tokens)
    return RateDecision.THROTTLE.value, tokens


class RateProtector:
    """Process-local fallback. Prefer decide_durable() when a session exists."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._states: dict[str, PrincipalRateState] = defaultdict(PrincipalRateState)

    def decide(
        self,
        principal_id: UUID | str,
        *,
        channel: str | None = None,
        now: float | None = None,
        message_count: int = 1,
    ) -> RateDecision:
        p = _params()
        if not p["enabled"]:
            return RateDecision.ALLOW
        now = now or time.monotonic()
        key = str(principal_id)
        with self._lock:
            st = self._states[key]
            if st.last_refill == 0.0:
                st.tokens = float(p["burst"])
                st.last_refill = now
            else:
                elapsed = now - st.last_refill
                st.tokens = min(float(p["burst"]), st.tokens + elapsed * (p["sustained"] / p["window"]))
                st.last_refill = now
            while st.timestamps and st.timestamps[0] < now - p["window"]:
                st.timestamps.popleft()
            decision, tokens = decide_from_counts(
                tokens=st.tokens,
                recent=len(st.timestamps),
                message_count=message_count,
                cooldown_active=st.cooldown_until > now,
            )
            st.tokens = tokens
            if decision == RateDecision.ALLOW.value:
                for _ in range(message_count):
                    st.timestamps.append(now)
            elif decision == RateDecision.DEFER.value:
                for _ in range(message_count):
                    st.timestamps.append(now)
                st.deferred_count += 1
            elif decision == RateDecision.THROTTLE.value:
                st.cooldown_until = now + p["cooldown"]
            else:
                st.deferred_count += message_count
            return RateDecision(decision)

    def snapshot(self, principal_id: UUID | str) -> dict[str, Any]:
        key = str(principal_id)
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


async def decide_durable(session, principal_id, *, message_count: int = 1) -> RateDecision:
    """
    Cross-worker decision using principal_workloads + row lock.
    If the table is missing or session fails, falls back to process-local.
    """
    p = _params()
    if not p["enabled"]:
        return RateDecision.ALLOW
    try:
        from sqlalchemy import select
        from wax.db.models import PrincipalWorkload

        now = datetime.now(timezone.utc)
        stmt = (
            select(PrincipalWorkload)
            .where(PrincipalWorkload.principal_id == principal_id)
            .with_for_update()
        )
        row = await session.scalar(stmt)
        if row is None:
            row = PrincipalWorkload(
                id=uuid4(),
                principal_id=principal_id,
                tokens=float(p["burst"]),
                last_refill_at=now,
                window_started_at=now,
                window_count=0,
            )
            session.add(row)
            await session.flush()

        # Refill
        last = row.last_refill_at or now
        elapsed = max(0.0, (now - last).total_seconds())
        row.tokens = min(float(p["burst"]), float(row.tokens or 0) + elapsed * (p["sustained"] / p["window"]))
        row.last_refill_at = now

        # Sliding window
        if not row.window_started_at or (now - row.window_started_at).total_seconds() > p["window"]:
            row.window_started_at = now
            row.window_count = 0

        cooldown_active = bool(row.cooldown_until and row.cooldown_until > now)
        decision, tokens = decide_from_counts(
            tokens=float(row.tokens),
            recent=int(row.window_count or 0),
            message_count=message_count,
            cooldown_active=cooldown_active,
        )
        row.tokens = tokens
        if decision in (RateDecision.ALLOW.value, RateDecision.DEFER.value):
            row.window_count = int(row.window_count or 0) + message_count
        if decision == RateDecision.THROTTLE.value:
            row.cooldown_until = now + timedelta(seconds=p["cooldown"])
        await session.flush()
        return RateDecision(decision)
    except Exception:
        logger.exception("durable_rate_fallback_local")
        return get_rate_protector().decide(principal_id, message_count=message_count)
