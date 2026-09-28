"""
Resolve where to reach a person (WhatsApp / Telegram).

Principal is the canonical learner. InterfaceIdentity is a channel door.
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


async def find_identity(
    session: AsyncSession, *, channel: str, external_id: str
) -> InterfaceIdentity | None:
    channel = channel.lower().strip()
    external_id = str(external_id).strip()
    result = await session.execute(
        select(InterfaceIdentity).where(
            InterfaceIdentity.channel == channel,
            InterfaceIdentity.external_id == external_id,
        )
    )
    return result.scalar_one_or_none()


async def resolve_or_create_messaging_identity(
    session: AsyncSession,
    *,
    channel: str,
    external_id: str,
    display_name: str | None = None,
    metadata: dict | None = None,
) -> tuple:
    """
    Canonical inbound resolution for permanent messaging channels.

    If InterfaceIdentity exists → return its Principal (never create a second learner).
    If not → create Principal + InterfaceIdentity (genuinely new door).
    """
    from uuid import uuid4
    from wax.db.models import Principal

    channel = channel.lower().strip()
    external_id = str(external_id).strip()
    if not external_id:
        raise ValueError("missing external_id")

    existing = await find_identity(session, channel=channel, external_id=external_id)
    if existing:
        principal = await session.get(Principal, existing.principal_id)
        return principal, existing

    principal = Principal(id=uuid4(), display_name=display_name)
    session.add(principal)
    await session.flush()
    identity = InterfaceIdentity(
        id=uuid4(),
        principal_id=principal.id,
        channel=channel,
        external_id=external_id,
        display_name=display_name,
        is_primary=True,
        metadata_=metadata or {},
    )
    session.add(identity)
    await session.flush()
    return principal, identity
