"""Cross-channel identity linking via OTP (preferred) or knowledge challenge.

Natural flow:
1. Learner (on Telegram): "I also use WhatsApp +234..."
2. Tutor calls request_channel_link → OTP sent to that WhatsApp number
3. Learner pastes OTP in Telegram → confirm_channel_link → identities merged
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.config import get_settings
from wax.db.models import ChannelLinkChallenge, InterfaceIdentity, Principal
from wax.domain.identity import link_identity_to_principal
from wax.observability.logging import get_logger

logger = get_logger(__name__)


def _hash_code(code: str) -> str:
    s = get_settings()
    raw = f"{s.secret_key}:{code.strip()}".encode()
    return hashlib.sha256(raw).hexdigest()


def _normalize_wa_id(raw: str) -> str:
    digits = "".join(c for c in str(raw) if c.isdigit())
    return digits


async def request_otp_link(
    session: AsyncSession,
    *,
    principal_id,
    source_channel: str,
    target_channel: str,
    target_external_id: str,
    ttl_minutes: int = 10,
) -> dict[str, Any]:
    target_channel = target_channel.lower().strip()
    source_channel = source_channel.lower().strip()
    if target_channel not in ("whatsapp", "telegram"):
        return {"ok": False, "error": "unsupported_target_channel"}
    if target_channel == "whatsapp":
        target_external_id = _normalize_wa_id(target_external_id)
    else:
        target_external_id = str(target_external_id).strip()
    if not target_external_id:
        return {"ok": False, "error": "target_id_required"}

    # Already linked to this principal?
    existing = await session.execute(
        select(InterfaceIdentity).where(
            InterfaceIdentity.channel == target_channel,
            InterfaceIdentity.external_id == target_external_id,
            InterfaceIdentity.principal_id == principal_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"ok": True, "already_linked": True, "channel": target_channel}

    # Owned by someone else — do not start OTP that would conflict on confirm
    other = await session.execute(
        select(InterfaceIdentity).where(
            InterfaceIdentity.channel == target_channel,
            InterfaceIdentity.external_id == target_external_id,
        )
    )
    owned = other.scalar_one_or_none()
    if owned and owned.principal_id != principal_id:
        return {
            "ok": False,
            "error": "identity_conflict",
            "note": "That account is already linked to a different learner.",
        }

    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.now(timezone.utc)
    challenge = ChannelLinkChallenge(
        id=uuid4(),
        principal_id=principal_id,
        source_channel=source_channel,
        target_channel=target_channel,
        target_external_id=target_external_id,
        method="otp",
        code_hash=_hash_code(code),
        status="pending",
        attempts=0,
        max_attempts=5,
        expires_at=now + timedelta(minutes=ttl_minutes),
        metadata_={},
    )
    session.add(challenge)
    await session.flush()

    # Deliver OTP on the *target* channel
    otp_text = (
        f"WAX verification code: {code}\n\n"
        f"Someone asked to link this {target_channel} account with another chat.\n"
        f"If that was you, paste this code in the other chat within {ttl_minutes} minutes.\n"
        f"If not you, ignore this message."
    )
    send_result = await _send_to_channel(target_channel, target_external_id, otp_text)
    logger.info(
        "channel_link_otp_sent",
        challenge_id=str(challenge.id),
        target_channel=target_channel,
        send_status=send_result.get("status"),
    )
    return {
        "ok": True,
        "challenge_id": str(challenge.id),
        "method": "otp",
        "target_channel": target_channel,
        "expires_at": challenge.expires_at.isoformat(),
        "otp_delivery": send_result,
        "note": (
            "OTP was sent to the other channel. Ask the learner to open that chat, "
            "copy the 6-digit code, and paste it here. Never ask them to share the code publicly."
        ),
        # Never return the code to the LLM in production logs path — omit from tool result
    }


async def confirm_otp_link(
    session: AsyncSession,
    *,
    principal_id,
    code: str,
    challenge_id: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stmt = (
        select(ChannelLinkChallenge)
        .where(
            ChannelLinkChallenge.principal_id == principal_id,
            ChannelLinkChallenge.status == "pending",
            ChannelLinkChallenge.method == "otp",
        )
        .order_by(ChannelLinkChallenge.created_at.desc())
        .limit(5)
    )
    if challenge_id:
        from uuid import UUID
        try:
            stmt = select(ChannelLinkChallenge).where(
                ChannelLinkChallenge.id == UUID(str(challenge_id)),
                ChannelLinkChallenge.principal_id == principal_id,
            )
        except Exception:
            return {"ok": False, "error": "invalid_challenge_id"}

    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return {"ok": False, "error": "no_pending_challenge"}

    code = (code or "").strip().replace(" ", "")
    for ch in rows:
        if ch.expires_at and ch.expires_at < now:
            ch.status = "expired"
            continue
        if ch.attempts >= ch.max_attempts:
            ch.status = "failed"
            continue
        ch.attempts += 1
        if not secrets.compare_digest(ch.code_hash, _hash_code(code)):
            await session.flush()
            continue
        # Success — refuse if target identity already owned by another principal
        from wax.domain.identity import IdentityConflictError, find_identity

        owned = await find_identity(
            session, channel=ch.target_channel, external_id=ch.target_external_id
        )
        if owned and owned.principal_id != principal_id:
            ch.status = "failed"
            await session.flush()
            logger.warning(
                "channel_link_conflict",
                challenge_id=str(ch.id),
                channel=ch.target_channel,
                principal_id=str(principal_id),
            )
            return {
                "ok": False,
                "error": "identity_conflict",
                "note": "That account is already linked to a different learner. Linking was not changed.",
            }
        try:
            identity = await link_identity_to_principal(
                session,
                principal_id=principal_id,
                channel=ch.target_channel,
                external_id=ch.target_external_id,
                make_primary=False,
                allow_reassign=False,
            )
        except IdentityConflictError:
            ch.status = "failed"
            await session.flush()
            return {"ok": False, "error": "identity_conflict"}
        ch.status = "verified"
        ch.verified_at = now
        await session.flush()
        logger.info(
            "channel_link_verified",
            challenge_id=str(ch.id),
            channel=ch.target_channel,
            principal_id=str(principal_id),
        )
        return {
            "ok": True,
            "linked": True,
            "channel": ch.target_channel,
            "external_id": ch.target_external_id,
            "identity_id": str(identity.id),
            "note": "Channels are now linked. Memory and goals are shared for this person.",
        }

    await session.flush()
    return {"ok": False, "error": "invalid_or_expired_code"}


async def request_knowledge_link(
    session: AsyncSession,
    *,
    principal_id,
    source_channel: str,
    target_channel: str,
    target_external_id: str,
    questions: list[dict[str, str]],
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    """Fallback when OTP cannot be delivered. questions: [{id, prompt, answer}]."""
    target_channel = target_channel.lower().strip()
    if target_channel == "whatsapp":
        target_external_id = _normalize_wa_id(target_external_id)
    answers = []
    for q in questions[:5]:
        ans = (q.get("answer") or "").strip().lower()
        if not ans:
            continue
        answers.append({"id": q.get("id") or secrets.token_hex(4), "hash": _hash_code(ans)})
    if len(answers) < 2:
        return {"ok": False, "error": "need_at_least_two_answerable_questions"}

    code_material = "|".join(a["hash"] for a in answers)
    now = datetime.now(timezone.utc)
    challenge = ChannelLinkChallenge(
        id=uuid4(),
        principal_id=principal_id,
        source_channel=source_channel.lower(),
        target_channel=target_channel,
        target_external_id=str(target_external_id).strip(),
        method="knowledge",
        code_hash=_hash_code(code_material),
        status="pending",
        attempts=0,
        max_attempts=5,
        expires_at=now + timedelta(minutes=ttl_minutes),
        metadata_={
            "questions": [{"id": q.get("id"), "prompt": q.get("prompt")} for q in questions[:5]],
            "answer_ids": [a["id"] for a in answers],
        },
    )
    session.add(challenge)
    await session.flush()
    return {
        "ok": True,
        "challenge_id": str(challenge.id),
        "method": "knowledge",
        "questions_to_ask": [q.get("prompt") for q in questions[:5] if q.get("prompt")],
        "note": "Ask these questions in chat. When the learner answers, call confirm_knowledge_link with their answers.",
    }


async def confirm_knowledge_link(
    session: AsyncSession,
    *,
    principal_id,
    challenge_id: str,
    answers: list[str],
) -> dict[str, Any]:
    from uuid import UUID

    try:
        cid = UUID(str(challenge_id))
    except Exception:
        return {"ok": False, "error": "invalid_challenge_id"}
    ch = await session.get(ChannelLinkChallenge, cid)
    if not ch or ch.principal_id != principal_id:
        return {"ok": False, "error": "not_found"}
    now = datetime.now(timezone.utc)
    if ch.status != "pending" or (ch.expires_at and ch.expires_at < now):
        ch.status = "expired"
        await session.flush()
        return {"ok": False, "error": "expired"}
    ch.attempts += 1
    hashes = [_hash_code((a or "").strip().lower()) for a in answers]
    material = "|".join(hashes)
    if not secrets.compare_digest(ch.code_hash, _hash_code(material)):
        if ch.attempts >= ch.max_attempts:
            ch.status = "failed"
        await session.flush()
        return {"ok": False, "error": "answers_incorrect"}
    from wax.domain.identity import IdentityConflictError, find_identity

    owned = await find_identity(
        session, channel=ch.target_channel, external_id=ch.target_external_id
    )
    if owned and owned.principal_id != principal_id:
        ch.status = "failed"
        await session.flush()
        return {
            "ok": False,
            "error": "identity_conflict",
            "note": "That account is already linked to a different learner.",
        }
    try:
        identity = await link_identity_to_principal(
            session,
            principal_id=principal_id,
            channel=ch.target_channel,
            external_id=ch.target_external_id,
            make_primary=False,
            allow_reassign=False,
        )
    except IdentityConflictError:
        ch.status = "failed"
        await session.flush()
        return {"ok": False, "error": "identity_conflict"}
    ch.status = "verified"
    ch.verified_at = now
    await session.flush()
    return {
        "ok": True,
        "linked": True,
        "channel": ch.target_channel,
        "identity_id": str(identity.id),
    }


async def _send_to_channel(channel: str, external_id: str, text: str) -> dict[str, Any]:
    try:
        if channel == "telegram":
            from wax.messaging.telegram.client import send_text
            return await send_text(external_id, text)
        if channel == "whatsapp":
            from wax.messaging.whatsapp.client import send_text
            return await send_text(external_id, text)
    except Exception as e:
        logger.exception("channel_link_send_failed")
        return {"status": "failed", "error": str(e)[:200]}
    return {"status": "skipped", "reason": "unknown_channel"}
