"""Factual student context — pure read-only aggregation of identity facts.

This is infrastructure reality, not intelligence: it reports what the system
already knows (display name, linked channels, first contact, explicit durable
settings) and never interprets it. The AI decides what these facts mean and
how they affect interaction. Judgment-bearing personalization (goals, ability,
learning style, teaching preferences, plans) belongs to the AI's own notebook
and memory store — not here.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Conversation, InterfaceIdentity, Message, Principal


async def factual_context(session: AsyncSession, principal_id) -> dict[str, Any]:
    """Aggregate only facts that already exist in durable rows.

    No interpretation, no ranking, no inference — mechanical reads of stored
    identity/account data for the AI to reason over itself.
    """
    p = await session.get(Principal, principal_id)
    if p is None:
        return {"found": False}

    identities = list(
        (
            await session.execute(
                select(InterfaceIdentity).where(
                    InterfaceIdentity.principal_id == p.id
                )
            )
        )
        .scalars()
        .all()
    )

    first_message_at = (
        await session.execute(
            select(func.min(Message.created_at)).where(Message.principal_id == p.id)
        )
    ).scalar()

    conversations = list(
        (
            await session.execute(
                select(Conversation).where(Conversation.principal_id == p.id)
            )
        )
        .scalars()
        .all()
    )

    return {
        "found": True,
        "principal_id": str(p.id),
        "display_name": p.display_name,
        # Basic account facts — presence flags only; content stays private to
        # the AI's own stores (memory/notebook/transcript). The retired
        # column is deliberately NOT surfaced: notebook/ and memory are the
        # single personalization authority.
        "has_world": bool((p.metadata_ or {}).get("world_root"))
        or any(bool((i.metadata_ or {}).get("world_root")) for i in identities),
        "is_active": p.is_active,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "first_message_at": first_message_at.isoformat() if first_message_at else None,
        "linked_channel_identities": [
            {
                "channel": i.channel,
                "external_id": i.external_id,
                "display_name": i.display_name,
                "is_primary": bool(i.is_primary),
                "linked_since": i.created_at.isoformat() if i.created_at else None,
            }
            for i in sorted(identities, key=lambda x: (x.channel, x.external_id))
        ],
        "conversations": [
            {
                "id": str(c.id),
                "channel": c.channel,
                "started_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in sorted(conversations, key=lambda x: str(x.channel))
        ],
    }
