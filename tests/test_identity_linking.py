"""
Tests for cross-channel identity linking infrastructure.

Covers:
- Successful linking flow (request → verify → linked)
- Code expiry (used after TTL)
- Reuse prevention (used codes cannot be reused)
- Wrong-code rejection
- Identity isolation (cannot link to another Principal's identity)
- Already-linked handling
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import wax.db.models as models
from wax.db.session import session_scope
from wax.identity_link.service import IdentityLinkService, DEFAULT_CODE_TTL_SECONDS
from wax.domain.identity import resolve_or_create_messaging_identity


async def _create_principal_with_identity(session, channel: str, external_id: str, display_name: str | None = None):
    """Helper: create a Principal with one InterfaceIdentity."""
    principal, identity = await resolve_or_create_messaging_identity(
        session,
        channel=channel,
        external_id=external_id,
        display_name=display_name,
    )
    await session.flush()
    return principal, identity


class TestIdentityLinkingSuccess:
    """Happy path: request link, verify code, identities become associated."""

    @pytest.mark.asyncio
    async def test_request_and_verify_link(self, session):
        """One identity requests a link, the other presents the code, both share a Principal."""
        # Create first identity (WhatsApp)
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+1234567890", "Alice"
        )

        svc = IdentityLinkService(session)

        # Request a link for Telegram
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="123456789",
        )

        assert challenge.status == "pending"
        assert challenge.code is not None
        assert len(challenge.code) == 16  # secrets.token_hex(8).upper()
        assert challenge.pending_channel == "telegram"
        assert challenge.pending_external_id == "123456789"
        assert challenge.expires_at is not None

        # Verify the code from the Telegram side
        result = await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="123456789",
        )

        assert result["ok"] is True
        assert result["already_linked"] is False
        assert result["principal_id"] == str(principal.id)

        # Refresh the challenge to check status
        await session.refresh(challenge)
        assert challenge.status == "used"
        assert challenge.consumed_at is not None

        # Verify the identity was created and linked
        from sqlalchemy import select
        result = await session.execute(
            select(models.InterfaceIdentity).where(
                models.InterfaceIdentity.principal_id == principal.id,
                models.InterfaceIdentity.channel == "telegram",
                models.InterfaceIdentity.external_id == "123456789",
            )
        )
        identity = result.scalar_one_or_none()
        assert identity is not None
        assert identity.principal_id == principal.id

    @pytest.mark.asyncio
    async def test_already_linked_identity(self, session):
        """If the pending identity already exists and is linked, verification succeeds with already_linked=True."""
        # Create Principal with both identities already linked
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+1234567890", "Bob"
        )

        # Manually create the Telegram identity linked to the same Principal
        telegram_id = models.InterfaceIdentity(
            id=uuid4(),
            principal_id=principal.id,
            channel="telegram",
            external_id="987654321",
            is_primary=False,
        )
        session.add(telegram_id)
        await session.flush()

        svc = IdentityLinkService(session)

        # Request a link for the already-linked Telegram identity
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="987654321",
        )

        # Verify
        result = await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="987654321",
        )

        assert result["ok"] is True
        assert result["already_linked"] is True
        assert result["principal_id"] == str(principal.id)

    @pytest.mark.asyncio
    async def test_link_creates_identity_if_not_exists(self, session):
        """If the pending identity doesn't exist, it should be created on verification."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+1111111111"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="new_user_42",
        )

        # Verify — identity should be created
        result = await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="new_user_42",
        )

        assert result["ok"] is True
        assert result["already_linked"] is False

        # Check identity was created
        from sqlalchemy import select
        result = await session.execute(
            select(models.InterfaceIdentity).where(
                models.InterfaceIdentity.channel == "telegram",
                models.InterfaceIdentity.external_id == "new_user_42",
            )
        )
        identity = result.scalar_one_or_none()
        assert identity is not None
        assert identity.principal_id == principal.id


class TestCodeExpiry:
    """Codes that expire cannot be used."""

    @pytest.mark.asyncio
    async def test_expired_code_rejected(self, session):
        """A code that has passed its TTL should be rejected."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+2222222222"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="expired_test",
            code_ttl_seconds=1,  # 1 second TTL
        )

        # Wait for expiry (using TTL manipulation for test determinism)
        challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        await session.flush()

        result = await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="expired_test",
        )

        assert result["ok"] is False
        assert result["error"] == "code_expired"

        # Refresh and check status
        await session.refresh(challenge)
        assert challenge.status == "expired"

    @pytest.mark.asyncio
    async def test_expiry_cleanup(self, session):
        """Expired pending challenges should be cleaned up by the service."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+3333333333"
        )

        svc = IdentityLinkService(session)

        # Create several challenges with past expiry
        for i in range(5):
            c = await svc.request_link(
                principal_id=principal.id,
                pending_channel="telegram",
                pending_external_id=f"expiry_test_{i}",
                code_ttl_seconds=1,
            )
            c.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
            await session.flush()

        # Also create one valid pending challenge
        valid = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="valid_one",
        )

        # Run cleanup
        n = await svc.cleanup_expired()
        assert n == 5

        # Check that only the valid one remains pending
        pending = await svc.get_pending_challenge_for_principal(principal.id)
        assert pending is not None
        assert pending.id == valid.id


class TestReusePrevention:
    """Used codes cannot be reused."""

    @pytest.mark.asyncio
    async def test_used_code_cannot_be_reused(self, session):
        """A code that has already been used must be rejected on second attempt."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+4444444444"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="reuse_test",
        )

        # First use: should succeed
        result1 = await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="reuse_test",
        )
        assert result1["ok"] is True

        # Second use: should fail
        result2 = await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="reuse_test",
        )
        assert result2["ok"] is False
        assert result2["error"] == "code_already_used"

    @pytest.mark.asyncio
    async def test_used_code_with_different_identity_rejected(self, session):
        """A used code cannot be used even with a different presenting identity."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+5555555555"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="original_id",
        )

        # Use the code with the correct identity
        result1 = await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="original_id",
        )
        assert result1["ok"] is True

        # Try to use the same code with a different external_id
        result2 = await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="different_id",
        )
        assert result2["ok"] is False
        # Should fail because code is already used (not because of identity mismatch)
        assert result2["error"] == "code_already_used"


class TestWrongCodeRejection:
    """Invalid or mismatched codes are rejected."""

    @pytest.mark.asyncio
    async def test_invalid_code_rejected(self, session):
        """A completely invalid code should be rejected."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+6666666666"
        )

        svc = IdentityLinkService(session)
        await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="some_id",
        )

        result = await svc.verify_code(
            code="INVALID_CODE_12345",
            presenting_channel="telegram",
            presenting_external_id="some_id",
        )

        assert result["ok"] is False
        assert result["error"] == "invalid_code"

    @pytest.mark.asyncio
    async def test_code_with_wrong_channel_rejected(self, session):
        """A valid code presented from the wrong channel should be rejected."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+7777777777"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="tg_user",
        )

        result = await svc.verify_code(
            code=challenge.code,
            presenting_channel="whatsapp",  # Wrong channel!
            presenting_external_id="tg_user",
        )

        assert result["ok"] is False
        assert result["error"] == "code_does_not_match_identity"

    @pytest.mark.asyncio
    async def test_code_with_wrong_external_id_rejected(self, session):
        """A valid code presented by a different external_id should be rejected."""
        principal, _ = await _create_principal_with_identity(
            session, "telegram", "user_a"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="user_b",
        )

        result = await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="user_wrong",  # Wrong ID!
        )

        assert result["ok"] is False
        assert result["error"] == "code_does_not_match_identity"


class TestIdentityIsolation:
    """Cannot link an identity that belongs to another Principal."""

    @pytest.mark.asyncio
    async def test_cannot_link_to_another_principal(self, session):
        """request_link must refuse if the pending identity already belongs to a different Principal."""
        # Create two different Principals
        principal_a, _ = await _create_principal_with_identity(
            session, "whatsapp", "+aa11111111", "Alice"
        )
        principal_b, _ = await _create_principal_with_identity(
            session, "telegram", "user_bob", "Bob"
        )

        svc = IdentityLinkService(session)

        # Alice tries to link Bob's Telegram identity - should raise ValueError
        with pytest.raises(ValueError, match="already belongs to another"):
            await svc.request_link(
                principal_id=principal_a.id,
                pending_channel="telegram",
                pending_external_id="user_bob",
            )

    @pytest.mark.asyncio
    async def test_cannot_request_link_for_existing_other_principal_identity(self, session):
        """request_link should raise ValueError if pending identity belongs to another Principal."""
        principal_a, _ = await _create_principal_with_identity(
            session, "whatsapp", "+aa22222222"
        )
        principal_b, _ = await _create_principal_with_identity(
            session, "telegram", "exclusive_user"
        )

        svc = IdentityLinkService(session)

        with pytest.raises(ValueError, match="already belongs to another"):
            await svc.request_link(
                principal_id=principal_a.id,
                pending_channel="telegram",
                pending_external_id="exclusive_user",
            )


class TestDisplayHint:
    """Display hints help the AI relay the right message to the student."""

    @pytest.mark.asyncio
    async def test_default_display_hint_generated(self, session):
        """When no display_hint is provided, one should be generated."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+8888888888", "Charlie"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="charlie_tg",
        )

        assert challenge.display_hint is not None
        assert "Telegram" in challenge.display_hint
        assert "Charlie" in challenge.display_hint

    @pytest.mark.asyncio
    async def test_custom_display_hint_accepted(self, session):
        """Custom display hints should be accepted when provided."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+9999999999"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="custom_tg",
            display_hint="Connect your Telegram to this account",
        )

        assert challenge.display_hint == "Connect your Telegram to this account"

    @pytest.mark.asyncio
    async def test_display_hint_truncated_if_too_long(self, session):
        """Very long display hints should be truncated."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+0000000000"
        )

        svc = IdentityLinkService(session)
        long_hint = "A" * 200  # Way over the 128 char limit
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="long_hint_test",
            display_hint=long_hint,
        )

        assert challenge.display_hint is not None
        assert len(challenge.display_hint) <= 128
        assert challenge.display_hint == long_hint[:128]


class TestChallengeStatus:
    """Challenge status tracking."""

    @pytest.mark.asyncio
    async def test_pending_challenge_retrievable(self, session):
        """A pending challenge should be retrievable via get_pending_challenge_for_principal."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+pp11111111"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="status_test",
        )

        retrieved = await svc.get_pending_challenge_for_principal(principal.id)
        assert retrieved is not None
        assert retrieved.id == challenge.id
        assert retrieved.status == "pending"

    @pytest.mark.asyncio
    async def test_used_challenge_not_retrievable_as_pending(self, session):
        """Used challenges should not appear in pending lookups."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+pp22222222"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="used_status_test",
        )

        # Use the code
        await svc.verify_code(
            code=challenge.code,
            presenting_channel="telegram",
            presenting_external_id="used_status_test",
        )

        # Should not be retrievable as pending
        retrieved = await svc.get_pending_challenge_for_principal(principal.id)
        assert retrieved is None


class TestCodeFormat:
    """Code generation format tests."""

    @pytest.mark.asyncio
    async def test_code_is_uppercase_hex(self, session):
        """Generated codes should be uppercase hexadecimal strings."""
        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+ff11111111"
        )

        svc = IdentityLinkService(session)
        challenge = await svc.request_link(
            principal_id=principal.id,
            pending_channel="telegram",
            pending_external_id="format_test",
        )

        code = challenge.code
        assert len(code) == 16  # token_hex(8) = 16 chars
        assert code == code.upper()  # All uppercase
        # Should be valid hex
        int(code, 16)  # Should not raise


class TestSchedulerWakeup:
    """Tests for the scheduler wake-up mechanism that tells the AI it has been awakened."""

    @pytest.mark.asyncio
    async def test_scheduled_action_payload_contains_wakeup_context(self, session):
        """The scheduled action Work should contain wakeup context for the AI."""
        from wax.scheduler.service import SchedulerService
        from datetime import timedelta

        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+wake111111"
        )

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        action = await svc.schedule(
            principal_id=principal.id,
            action_type="check_in",
            execute_at=execute_at,
            reason="Student check-in after 5 minutes of activity",
            payload={
                "channel": "whatsapp",
                "message_hint": "How are you progressing?",
            },
        )

        # Create the work for this action (simulating what the worker does)
        work = await svc.create_work_for_action(action)

        # Check the payload contains wakeup context
        payload = work.input_payload or {}
        assert "wakeup" in payload
        wakeup = payload["wakeup"]
        assert wakeup["trigger"] == "scheduled_action"
        assert wakeup["action_type"] == "check_in"
        assert wakeup["reason"] == "Student check-in after 5 minutes of activity"
        assert "triggered_at" in wakeup
        assert "ai_instruction" in wakeup

        # The AI instruction should tell the AI to decide, not auto-summarize
        assert "You decide what to do" in wakeup["ai_instruction"]
        assert "No automatic memory summarization" in wakeup["ai_instruction"]

    @pytest.mark.asyncio
    async def test_wakeup_payload_has_factual_context_not_summary(self, session):
        """The wakeup payload should contain factual context, not a summary of events."""
        from wax.scheduler.service import SchedulerService
        from datetime import timedelta

        principal, _ = await _create_principal_with_identity(
            session, "telegram", "user_wake_test"
        )

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(hours=1)

        action = await svc.schedule(
            principal_id=principal.id,
            action_type="follow_up",
            execute_at=execute_at,
            reason="Follow up on pending exercise",
            payload={
                "objective": "Check if student completed the exercise",
                "channel": "telegram",
            },
        )

        work = await svc.create_work_for_action(action)
        payload = work.input_payload or {}

        wakeup = payload["wakeup"]

        # Should NOT contain any summary-like fields
        assert "summary" not in wakeup
        assert "messages_since" not in wakeup
        assert "last_message" not in wakeup
        assert "activity_summary" not in wakeup

        # Should contain only factual context
        assert wakeup["trigger"] == "scheduled_action"
        assert wakeup["action_type"] == "follow_up"
        assert wakeup["reason"] == "Follow up on pending exercise"

    @pytest.mark.asyncio
    async def test_inactivity_wakeup_is_possible(self, session):
        """The AI should be able to schedule a wake-up after a period of inactivity."""
        from wax.scheduler.service import SchedulerService
        from datetime import timedelta

        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+inact111111"
        )

        svc = SchedulerService(session)

        # Schedule a wake-up 30 minutes from now (simulating inactivity check)
        action = await svc.schedule_in_hours(
            principal_id=principal.id,
            action_type="inactivity_check",
            hours=0.5,  # 30 minutes
            reason="Check if student returned after 30 minutes of inactivity",
            payload={
                "message_hint": "Have you had a chance to continue?",
                "channel": "whatsapp",
            },
        )

        # Create work
        work = await svc.create_work_for_action(action)
        payload = work.input_payload or {}

        assert payload["wakeup"]["action_type"] == "inactivity_check"
        assert "30 minutes" in payload["wakeup"]["reason"].lower() or "inactivity" in payload["wakeup"]["reason"].lower()

    @pytest.mark.asyncio
    async def test_handle_scheduled_action_records_system_message(self, session):
        """handle_scheduled_action should record a system message for transcript continuity."""
        from wax.intelligence.tutor import TutorService
        from wax.db.models import Conversation, Work
        from uuid import uuid4
        from datetime import timedelta

        principal, _ = await _create_principal_with_identity(
            session, "whatsapp", "+sys111111"
        )

        # Create a conversation
        conv = Conversation(
            id=uuid4(),
            principal_id=principal.id,
            channel="whatsapp",
            status="active",
        )
        session.add(conv)
        await session.flush()

        # Create a scheduled action work
        work = Work(
            id=uuid4(),
            principal_id=principal.id,
            conversation_id=conv.id,
            kind="scheduled_action",
            status="queued",
            objective="Test scheduled action",
            input_payload={
                "scheduled_action_id": str(uuid4()),
                "action_type": "test_wake",
                "reason": "Test reason",
                "message_hint": "Test hint",
                "wakeup": {
                    "trigger": "scheduled_action",
                    "action_type": "test_wake",
                    "reason": "Test reason",
                    "ai_instruction": "You have been awakened.",
                },
            },
        )
        session.add(work)
        await session.flush()

        tutor = TutorService(session)

        # Process the scheduled action
        await tutor.handle_scheduled_action(work)

        # Check that a system message was recorded
        from sqlalchemy import select
        from wax.db.models import Message

        result = await session.execute(
            select(Message).where(
                Message.work_id == work.id,
                Message.role == "system",
            )
        )
        messages = list(result.scalars().all())
        assert len(messages) > 0
        assert "scheduled action fired" in messages[0].content
