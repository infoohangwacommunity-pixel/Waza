"""Explicit durable settings on Principal — mechanical key/value storage only.

Preferences are whatever the AI and student explicitly agree to store via the
`state action: preferences` directive. Infrastructure merges keys mechanically,
never infers them, never gates behaviour on them, and defines no schema for
them. Judgment-bearing personalization (teaching method, ability, learning
style, plans) is NOT stored here by infrastructure — the AI keeps those in its
own notebook/memory if it wants them.

Factual identity context (display name, channels, first contact) lives in
wax.domain.profile.factual_context, not here.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Principal


async def get_preferences(session: AsyncSession, principal_id) -> dict[str, Any]:
    """Return exactly what was explicitly stored — no defaults injected."""
    p = await session.get(Principal, principal_id)
    if not p:
        return {}
    return dict(p.preferences or {})


async def update_preferences(
    session: AsyncSession, principal_id, patch: dict[str, Any]
) -> dict[str, Any]:
    """Mechanical merge of explicit keys; None values remove a key."""
    p = await session.get(Principal, principal_id)
    if not p:
        return {}
    current = dict(p.preferences or {})
    for k, v in patch.items():
        if v is None:
            current.pop(k, None)
        else:
            current[k] = v
    p.preferences = current
    await session.flush()
    return current
