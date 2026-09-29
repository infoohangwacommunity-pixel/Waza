"""
Explicit student preference data on Principal.

Stored as free-form JSON. Infrastructure does not schedule engagement from these
values. The AI may read them when present; the student (or AI via state) may set them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Principal


# Optional keys only — not product engagement policy.
DEFAULT_PREFERENCES: dict[str, Any] = {
    "language": None,
    "quiet_hours": None,  # {"start": "HH:MM", "end": "HH:MM"} or null
    "timezone": None,
    "message_length": None,
    "tone": None,
    "emoji": None,
}


async def get_preferences(session: AsyncSession, principal_id) -> dict[str, Any]:
    p = await session.get(Principal, principal_id)
    if not p:
        return dict(DEFAULT_PREFERENCES)
    prefs = dict(DEFAULT_PREFERENCES)
    prefs.update(p.preferences or {})
    return prefs


async def update_preferences(
    session: AsyncSession, principal_id, patch: dict[str, Any]
) -> dict[str, Any]:
    p = await session.get(Principal, principal_id)
    if not p:
        return dict(DEFAULT_PREFERENCES)
    current = dict(p.preferences or {})
    for k, v in patch.items():
        if v is None:
            current.pop(k, None)
        else:
            current[k] = v
    p.preferences = current
    await session.flush()
    return current


def is_in_quiet_hours(prefs: dict[str, Any], now_hhmm: str) -> bool:
    """Pure helper over preference data — not an automatic engagement gate."""
    qh = prefs.get("quiet_hours")
    if not qh or not isinstance(qh, dict):
        return False
    start = qh.get("start")
    end = qh.get("end")
    if not start or not end:
        return False
    if start <= end:
        return start <= now_hhmm < end
    return now_hhmm >= start or now_hhmm < end
