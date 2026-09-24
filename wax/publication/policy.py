"""
Publication lifecycle policy — infrastructure safety, not learner product limits.

AI may hint preferred lifetime; this module enforces safe bounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


class PublicationStatus(str, Enum):
    PREPARING = "preparing"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CLEANUP_PENDING = "cleanup_pending"
    CLEANED = "cleaned"
    FAILED = "failed"


# Explicit allowed transitions (state machine)
TRANSITIONS: dict[PublicationStatus, set[PublicationStatus]] = {
    PublicationStatus.PREPARING: {PublicationStatus.ACTIVE, PublicationStatus.FAILED},
    PublicationStatus.ACTIVE: {
        PublicationStatus.EXPIRED,
        PublicationStatus.REVOKED,
        PublicationStatus.FAILED,
    },
    PublicationStatus.EXPIRED: {PublicationStatus.CLEANUP_PENDING, PublicationStatus.CLEANED},
    PublicationStatus.REVOKED: {PublicationStatus.CLEANUP_PENDING, PublicationStatus.CLEANED},
    PublicationStatus.CLEANUP_PENDING: {PublicationStatus.CLEANED, PublicationStatus.FAILED},
    PublicationStatus.CLEANED: set(),
    PublicationStatus.FAILED: {PublicationStatus.CLEANUP_PENDING, PublicationStatus.CLEANED},
}


@dataclass(frozen=True)
class LifecyclePolicy:
    """Infrastructure policy for publication lifetimes."""

    default_lifetime: timedelta = timedelta(hours=48)
    max_lifetime: timedelta = timedelta(hours=168)  # 7 days
    min_lifetime: timedelta = timedelta(minutes=15)
    cleanup_grace: timedelta = timedelta(hours=24)

    def resolve_lifetime(self, preferred_hours: Optional[float]) -> timedelta:
        if preferred_hours is None:
            return self.default_lifetime
        try:
            hours = float(preferred_hours)
        except (TypeError, ValueError):
            return self.default_lifetime
        delta = timedelta(hours=hours)
        if delta < self.min_lifetime:
            return self.min_lifetime
        if delta > self.max_lifetime:
            return self.max_lifetime
        return delta

    def expires_at(self, now: datetime, preferred_hours: Optional[float] = None) -> datetime:
        return now + self.resolve_lifetime(preferred_hours)

    def cleanup_eligible_after(self, expires_at: datetime) -> datetime:
        return expires_at + self.cleanup_grace


DEFAULT_POLICY = LifecyclePolicy()


def can_transition(current: str | PublicationStatus, target: str | PublicationStatus) -> bool:
    try:
        cur = current if isinstance(current, PublicationStatus) else PublicationStatus(str(current))
        tgt = target if isinstance(target, PublicationStatus) else PublicationStatus(str(target))
    except ValueError:
        return False
    return tgt in TRANSITIONS.get(cur, set())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
