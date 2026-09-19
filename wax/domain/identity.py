"""
Resolve where to reach a person (WhatsApp / Telegram).

No educational assumptions — only channel identities.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import InterfaceIdentity


async def primary_channel_target(
    session: AsyncSession, principal_id
) -> tuple[str, str] | None:
    """Return (channel, external_id) preferring primary identity."""
    stmt = (
        select(InterfaceIdentity)
        .where(
            InterfaceIdentity.principal_id == principal_id,
            InterfaceIdentity.is_primary.is_(True),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    identity = result.scalar_one_or_none()
    if identity:
        return identity.channel, identity.external_id

    stmt = (
        select(InterfaceIdentity)
        .where(InterfaceIdentity.principal_id == principal_id)
        .order_by(InterfaceIdentity.updated_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    identity = result.scalar_one_or_none()
    if identity:
        return identity.channel, identity.external_id
    return None
