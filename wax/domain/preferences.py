"""
Learner preferences as evolving state — never hardcoded product rules.

Quiet hours, language, pacing, encouragement style live here as data.
The tutor and scheduler read preferences; they do not invent rigid rules.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Principal


DEFAULT_PREFERENCES: dict[str, Any] = {
    "language": None,
    "quiet_hours": None,  # {"start": "22:00", "end": "07:00"} or null
    "encouragement": None,  # light | direct | minimal | null
    "explanation_style": None,  # examples_first | theory_first | mixed | null
    "message_length": None,  # short | medium | detailed | null
    "timezone": None,  # IANA e.g. Africa/Lagos
    "proactivity_level": None,  # quiet | balanced | active
    "emoji": None,  # few | normal | none | null
    "tone": None,  # casual | formal | mixed | null
    "learning_style": None,  # talk_through | read | examples | mixed | null
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
    """
    Pure function over preference data.
    Returns False if quiet hours unset — no global hardcode forcing silence.
    """
    qh = prefs.get("quiet_hours")
    if not qh or not isinstance(qh, dict):
        return False
    start = qh.get("start")
    end = qh.get("end")
    if not start or not end:
        return False
    # Simple HH:MM compare; overnight windows supported
    if start <= end:
        return start <= now_hhmm < end
    return now_hhmm >= start or now_hhmm < end
