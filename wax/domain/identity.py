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



async def list_identities(session: AsyncSession, principal_id) -> list[InterfaceIdentity]:
    stmt = (
        select(InterfaceIdentity)
        .where(InterfaceIdentity.principal_id == principal_id)
        .order_by(InterfaceIdentity.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def link_identity_to_principal(
    session: AsyncSession,
    *,
    principal_id,
    channel: str,
    external_id: str,
    display_name: str | None = None,
    make_primary: bool = False,
) -> InterfaceIdentity:
    """Attach a channel identity to an existing principal (cross-channel continuity)."""
    channel = channel.lower().strip()
    external_id = str(external_id).strip()
    existing = await session.execute(
        select(InterfaceIdentity).where(
            InterfaceIdentity.channel == channel,
            InterfaceIdentity.external_id == external_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        if row.principal_id != principal_id:
            # Move link only if same person flow — explicit reassignment
            row.principal_id = principal_id
        if display_name:
            row.display_name = display_name
        if make_primary:
            # clear other primaries
            others = await list_identities(session, principal_id)
            for o in others:
                o.is_primary = o.id == row.id
            row.is_primary = True
        await session.flush()
        return row

    if make_primary:
        for o in await list_identities(session, principal_id):
            o.is_primary = False

    identity = InterfaceIdentity(
        principal_id=principal_id,
        channel=channel,
        external_id=external_id,
        display_name=display_name,
        is_primary=make_primary,
    )
    session.add(identity)
    await session.flush()
    return identity
