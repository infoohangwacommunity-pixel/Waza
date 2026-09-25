"""
Resolve where to reach a person (WhatsApp / Telegram).

Principal is the canonical learner. InterfaceIdentity is a channel door.
No educational assumptions — only channel identities.
"""

from __future__ import annotations

from typing import Any

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


async def linked_channels_state(
    session: AsyncSession,
    *,
    principal_id,
    current_channel: str | None = None,
) -> dict[str, Any]:
    """
    Authoritative semantic state for the tutor — no secrets, OTPs, or raw tokens.

    This is the single source of truth for "is Telegram/WhatsApp connected?"
    """
    identities = await list_identities(session, principal_id)
    channels: list[dict[str, Any]] = []
    linked_set: set[str] = set()
    for ident in identities:
        ch = (ident.channel or "").lower()
        if ch in ("whatsapp", "telegram"):
            linked_set.add(ch)
            channels.append(
                {
                    "channel": ch,
                    "verified": True,
                    "is_primary": bool(ident.is_primary),
                    "display_name": ident.display_name,
                }
            )
        elif ch and ch != "surface":
            linked_set.add(ch)
            channels.append(
                {
                    "channel": ch,
                    "verified": True,
                    "is_primary": bool(ident.is_primary),
                    "display_name": ident.display_name,
                }
            )

    current = (current_channel or "").lower().strip() or None
    return {
        "current_channel": current,
        "linked_channels": sorted(linked_set),
        "whatsapp_linked": "whatsapp" in linked_set,
        "telegram_linked": "telegram" in linked_set,
        "identity_count": len(channels),
        "identities": channels,
    }


def format_linked_channels_block(state: dict[str, Any]) -> str:
    """Compact tutor-facing block. Facts only — never invent beyond this."""
    if not state:
        return ""
    current = state.get("current_channel") or "unknown"
    linked = state.get("linked_channels") or []
    wa = "linked" if state.get("whatsapp_linked") else "not linked"
    tg = "linked" if state.get("telegram_linked") else "not linked"
    lines = [
        "\n--- Messaging identities (authoritative) ---",
        f"Current interface: {current}",
        f"WhatsApp: {wa}",
        f"Telegram: {tg}",
    ]
    if linked:
        lines.append(f"Verified linked channels: {', '.join(linked)}")
    else:
        lines.append("No permanent messaging channels verified yet beyond this interface.")
    lines.append(
        "Use only this state when the learner asks if WhatsApp/Telegram is connected. "
        "Do not invent linked accounts. Do not claim a channel is unlinked if it is listed above."
    )
    lines.append("--- End messaging identities ---\n")
    return "\n".join(lines)


async def link_identity_to_principal(
    session: AsyncSession,
    *,
    principal_id,
    channel: str,
    external_id: str,
    display_name: str | None = None,
    make_primary: bool = False,
    allow_reassign: bool = False,
) -> InterfaceIdentity:
    """
    Attach a channel identity to an existing principal (cross-channel continuity).

    If the identity already belongs to another principal, refuse unless allow_reassign
    (recovery flows only). Ordinary OTP confirm must not steal another learner's channel.
    """
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
            if not allow_reassign:
                raise IdentityConflictError(
                    f"channel {channel} identity already belongs to another principal"
                )
            row.principal_id = principal_id
        if display_name:
            row.display_name = display_name
        if make_primary:
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


class IdentityConflictError(Exception):
    """Target channel identity is owned by a different principal."""


async def recent_cross_channel_snippets(
    session: AsyncSession,
    *,
    principal_id,
    current_channel: str | None,
    limit_conversations: int = 2,
    messages_per_conversation: int = 4,
) -> list[dict[str, str]]:
    """
    Relevant recent messages from OTHER permanent channels for the same Principal.

    Not a full transcript dump. Bounded snippets so the tutor can continue
    when the learner switches interfaces and asks to resume.
    """
    from wax.db.models import Conversation, Message

    current = (current_channel or "").lower().strip()
    stmt = (
        select(Conversation)
        .where(
            Conversation.principal_id == principal_id,
            Conversation.status == "active",
        )
        .order_by(Conversation.updated_at.desc())
        .limit(8)
    )
    convs = list((await session.execute(stmt)).scalars().all())
    out: list[dict[str, str]] = []
    used = 0
    for conv in convs:
        ch = (conv.channel or "").lower()
        if ch == current or ch in ("surface", "web"):
            continue
        if used >= limit_conversations:
            break
        mstmt = (
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(messages_per_conversation)
        )
        msgs = list(reversed((await session.execute(mstmt)).scalars().all()))
        if not msgs:
            continue
        used += 1
        for m in msgs:
            role = m.role or "user"
            content = (m.content or "")[:400]
            if not content.strip():
                continue
            out.append(
                {
                    "role": role,
                    "content": f"[earlier on {ch}] {content}",
                    "channel": ch,
                }
            )
    return out


def format_cross_channel_continuity_block(snippets: list[dict[str, str]]) -> str:
    if not snippets:
        return ""
    lines = [
        "\n--- Other-channel continuity (same learner, bounded) ---",
        "These are brief recent turns from another verified interface for the SAME principal.",
        "Use only if relevant (e.g. learner asks to continue). Do not dump or re-teach everything.",
    ]
    for s in snippets[:12]:
        role = s.get("role") or "user"
        content = (s.get("content") or "")[:350]
        lines.append(f"{role}: {content}")
    lines.append("--- End other-channel continuity ---\n")
    return "\n".join(lines)



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
