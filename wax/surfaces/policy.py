"""Surface lifecycle policy and capability scopes — infrastructure, not product limits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


SURFACE_SCHEMA_VERSION = "2.0"
RUNTIME_VERSION = "1.0"


class SurfaceStatus(str, Enum):
    CREATING = "creating"
    ACTIVE = "active"
    IDLE = "idle"
    DORMANT = "dormant"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CLEANUP_PENDING = "cleanup_pending"
    CLEANED = "cleaned"
    FAILED = "failed"


TRANSITIONS: dict[SurfaceStatus, set[SurfaceStatus]] = {
    SurfaceStatus.CREATING: {SurfaceStatus.ACTIVE, SurfaceStatus.FAILED},
    SurfaceStatus.ACTIVE: {
        SurfaceStatus.IDLE,
        SurfaceStatus.EXPIRED,
        SurfaceStatus.REVOKED,
        SurfaceStatus.FAILED,
    },
    SurfaceStatus.IDLE: {
        SurfaceStatus.ACTIVE,
        SurfaceStatus.DORMANT,
        SurfaceStatus.EXPIRED,
        SurfaceStatus.REVOKED,
    },
    SurfaceStatus.DORMANT: {
        SurfaceStatus.ACTIVE,
        SurfaceStatus.EXPIRED,
        SurfaceStatus.REVOKED,
    },
    SurfaceStatus.EXPIRED: {SurfaceStatus.CLEANUP_PENDING, SurfaceStatus.CLEANED},
    SurfaceStatus.REVOKED: {SurfaceStatus.CLEANUP_PENDING, SurfaceStatus.CLEANED},
    SurfaceStatus.CLEANUP_PENDING: {SurfaceStatus.CLEANED, SurfaceStatus.FAILED},
    SurfaceStatus.CLEANED: set(),
    SurfaceStatus.FAILED: {SurfaceStatus.CLEANUP_PENDING, SurfaceStatus.CLEANED},
}


class CapabilityScope(str, Enum):
    VIEW = "view"
    STATE_READ = "state_read"
    STATE_WRITE = "state_write"
    AI_REQUEST = "ai_request"
    ARTIFACT_READ = "artifact_read"
    EVENT_WRITE = "event_write"


@dataclass(frozen=True)
class SurfaceLifecyclePolicy:
    """Infrastructure bounds — not learner product quotas."""

    default_lifetime: timedelta = timedelta(days=7)
    max_lifetime: timedelta = timedelta(days=30)
    min_lifetime: timedelta = timedelta(minutes=30)
    idle_after: timedelta = timedelta(hours=24)
    dormant_after: timedelta = timedelta(days=7)
    cleanup_grace: timedelta = timedelta(hours=48)

    def resolve_lifetime(self, preferred_hours: float | None) -> timedelta:
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


DEFAULT_SURFACE_POLICY = SurfaceLifecyclePolicy()


def can_transition(current: str | SurfaceStatus, target: str | SurfaceStatus) -> bool:
    try:
        cur = current if isinstance(current, SurfaceStatus) else SurfaceStatus(str(current))
        tgt = target if isinstance(target, SurfaceStatus) else SurfaceStatus(str(target))
    except ValueError:
        return False
    return tgt in TRANSITIONS.get(cur, set())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
