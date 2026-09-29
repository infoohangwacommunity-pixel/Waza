"""
Identity linking ops — AI-facing directives for cross-channel linking.

The AI uses these directives to request and complete identity linking.
Infrastructure owns verification; the AI only relays information.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from wax.identity_link.service import IdentityLinkService
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


async def request_link(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Request a one-time verification code for linking a second identity.

    The AI calls this when a student wants to link another channel (e.g., Telegram)
    to their existing account. Infrastructure generates a short-lived code.

    Required args:
      - pending_channel: the channel being linked (whatsapp, telegram)
      - pending_external_id: the external ID on that channel

    Returns the challenge info including a display_hint the AI can relay.
    The actual code stays in the DB — infrastructure-only.
    """
    principal_id = _as_uuid(ctx.get("principal_id"))
    if not principal_id:
        return {"ok": False, "error": "no_principal"}

    pending_channel = str(args.get("pending_channel") or "").strip().lower()
    if pending_channel not in ("whatsapp", "telegram"):
        return {"ok": False, "error": "unsupported_channel"}

    pending_external_id = str(args.get("pending_external_id") or "").strip()
    if not pending_external_id:
        return {"ok": False, "error": "pending_external_id_required"}

    ttl = args.get("code_ttl_seconds")
    try:
        ttl = int(ttl) if ttl is not None else 300
    except (TypeError, ValueError):
        ttl = 300
    ttl = max(60, min(ttl, 3600))  # 1min to 1hour

    display_hint = str(args.get("display_hint") or "").strip() or None

    svc = IdentityLinkService(session)
    try:
        challenge = await svc.request_link(
            principal_id=principal_id,
            pending_channel=pending_channel,
            pending_external_id=pending_external_id,
            code_ttl_seconds=ttl,
            display_hint=display_hint,
        )
        logger.info(
            "link_requested_via_directive",
            challenge_id=str(challenge.id),
            principal_id=str(principal_id),
            pending_channel=pending_channel,
        )
        return {
            "ok": True,
            "challenge_id": str(challenge.id),
            "display_hint": challenge.display_hint,
            "expires_at": challenge.expires_at.isoformat() if challenge.expires_at else None,
            "note": "Tell the student to present this code from their other device/channel.",
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}


async def present_code(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Present a verification code to complete identity linking.

    Called when a student sends a code they received on another channel.
    Infrastructure verifies the code and links the identities.

    Required args:
      - code: the verification code
      - presenting_channel: the channel presenting the code
      - presenting_external_id: the external ID on the presenting channel

    The AI relays whatever message infrastructure returns.
    """
    code = str(args.get("code") or "").strip()
    if not code:
        return {"ok": False, "error": "code_required"}

    presenting_channel = str(args.get("presenting_channel") or "").strip().lower()
    if presenting_channel not in ("whatsapp", "telegram"):
        return {"ok": False, "error": "unsupported_channel"}

    presenting_external_id = str(args.get("presenting_external_id") or "").strip()
    if not presenting_external_id:
        return {"ok": False, "error": "presenting_external_id_required"}

    svc = IdentityLinkService(session)
    result = await svc.verify_code(
        code=code,
        presenting_channel=presenting_channel,
        presenting_external_id=presenting_external_id,
    )
    logger.info(
        "link_code_presented_via_directive",
        code=code[:8] + "..." if len(code) > 8 else code,
        ok=result.get("ok"),
        error=result.get("error"),
    )
    return result


async def link_status(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    """
    Check the status of a pending link challenge.

    Used by the AI to check if a previously-requested link is still valid.
    """
    challenge_id = _as_uuid(args.get("challenge_id"))
    if not challenge_id:
        return {"ok": False, "error": "challenge_id_required"}

    svc = IdentityLinkService(session)
    challenge = await session.get(IdentityLinkChallenge, challenge_id)  # type: ignore
    if not challenge:
        return {"ok": False, "error": "challenge_not_found"}

    # Only allow principals to check their own challenges
    if challenge.principal_id != _as_uuid(ctx.get("principal_id")):
        return {"ok": False, "error": "forbidden"}

    return {
        "ok": True,
        "challenge_id": str(challenge.id),
        "status": challenge.status,
        "pending_channel": challenge.pending_channel,
        "display_hint": challenge.display_hint,
        "expires_at": challenge.expires_at.isoformat() if challenge.expires_at else None,
        "created_at": challenge.created_at.isoformat() if challenge.created_at else None,
    }
