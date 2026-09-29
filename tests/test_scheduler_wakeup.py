"""
Tests for scheduler wake-up mechanism.

Covers:
- Wake-up payload structure and content
- Factual context vs. summarization
- AI instruction clarity
- Scheduled action flow
- No automatic summarization jobs
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import wax.db.models as models


class TestSchedulerWakeupPayload:
    """Verify the wake-up payload structure when a scheduled action fires."""

    @pytest.mark.asyncio
    async def test_wakeup_payload_exists(self, session):
        """A scheduled action should produce a work with wakeup context."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(
            id=uuid4(),
            display_name="Test Student",
        )
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        action = await svc.schedule(
            principal_id=principal.id,
            action_type="reminder",
            execute_at=execute_at,
            reason="Scheduled reminder",
        )

        work = await svc.create_work_for_action(action)
        payload = work.input_payload or {}

        assert "wakeup" in payload
        assert payload["wakeup"]["trigger"] == "scheduled_action"

    @pytest.mark.asyncio
    async def test_wakeup_payload_has_no_summary(self, session):
        """Wake-up payload must not contain automatic summaries."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(
            id=uuid4(),
            display_name="No Summary Student",
        )
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        action = await svc.schedule(
            principal_id=principal.id,
            action_type="wake",
            execute_at=execute_at,
            reason="Just a wake-up",
            payload={"channel": "whatsapp"},
        )

        work = await svc.create_work_for_action(action)
        payload = work.input_payload or {}
        wakeup = payload.get("wakeup", {})

        # The wakeup should NOT contain automatic summarization
        forbidden_fields = [
            "summary",
            "last_messages",
            "conversation_summary",
            "memory_summary",
            "activity_summary",
            "message_count",
            "recent_activity",
        ]
        for field in forbidden_fields:
            assert field not in wakeup, f"Wakeup payload contains forbidden summary field: {field}"

    @pytest.mark.asyncio
    async def test_wakeup_instruction_is_clear(self, session):
        """The AI instruction in wakeup should be clear about its role."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(id=uuid4())
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(minutes=1)

        action = await svc.schedule(
            principal_id=principal.id,
            action_type="test",
            execute_at=execute_at,
            reason="Test wake-up",
        )

        work = await svc.create_work_for_action(action)
        payload = work.input_payload or {}
        wakeup = payload.get("wakeup", {})

        instruction = wakeup.get("ai_instruction", "")

        # Instruction should tell AI it decides what to do
        assert "You decide" in instruction or "you decide" in instruction.lower()
        assert "what this wake-up means" in instruction or "what to do" in instruction.lower()

        # Should NOT say "summarize" or "extract memory"
        assert "summarize" not in instruction.lower()
        assert "automatic memory" in instruction.lower() or "no automatic" in instruction.lower()

    @pytest.mark.asyncio
    async def test_wakeup_payload_has_factual_context(self, session):
        """Wake-up should contain factual context (reason, action_type, execute_at)."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(id=uuid4())
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(hours=2)

        action = await svc.schedule(
            principal_id=principal.id,
            action_type="progress_check",
            execute_at=execute_at,
            reason="Check student's progress on chapter 5",
            payload={
                "objective": "Review chapter 5 exercises",
                "message_hint": "How is chapter 5 going?",
                "channel": "telegram",
            },
        )

        work = await svc.create_work_for_action(action)
        payload = work.input_payload or {}
        wakeup = payload.get("wakeup", {})

        assert wakeup["trigger"] == "scheduled_action"
        assert wakeup["action_type"] == "progress_check"
        assert wakeup["reason"] == "Check student's progress on chapter 5"
        assert wakeup.get("triggered_at") is not None


class TestScheduledActionCreation:
    """Verify that scheduled actions create proper work for the AI."""

    @pytest.mark.asyncio
    async def test_work_has_scheduled_action_kind(self, session):
        """Work created from scheduled action should have kind='scheduled_action'."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(id=uuid4())
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        action = await svc.schedule(
            principal_id=principal.id,
            action_type="future_task",
            execute_at=execute_at,
        )

        work = await svc.create_work_for_action(action)

        assert work.kind == "scheduled_action"
        assert work.status == "queued"
        assert work.principal_id == principal.id

    @pytest.mark.asyncio
    async def test_work_objective_from_action(self, session):
        """Work objective should come from action reason or action_type."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(id=uuid4())
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        action = await svc.schedule(
            principal_id=principal.id,
            action_type="custom_action",
            execute_at=execute_at,
            reason="Custom reason for this wake-up",
        )

        work = await svc.create_work_for_action(action)

        # Objective should be set from reason or action_type
        assert work.objective is not None
        assert work.objective in ("Custom reason for this wake-up", "custom_action")

    @pytest.mark.asyncio
    async def test_work_payload_contains_scheduled_action_id(self, session):
        """Work payload should contain the scheduled_action_id for reference."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(id=uuid4())
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        action = await svc.schedule(
            principal_id=principal.id,
            action_type="link_test",
            execute_at=execute_at,
        )

        work = await svc.create_work_for_action(action)
        payload = work.input_payload or {}

        assert payload.get("scheduled_action_id") == str(action.id)
        assert payload.get("action_type") == "link_test"


class TestNoAutomaticSummarization:
    """Verify the system does NOT implement automatic summarization."""

    @pytest.mark.asyncio
    async def test_no_summary_method_in_scheduler_service(self, session):
        """SchedulerService should not have automatic summarization methods."""
        from wax.scheduler.service import SchedulerService
        import inspect

        methods = [m for m in dir(SchedulerService) if not m.startswith("_")]
        method_names = [m for m in methods if callable(getattr(SchedulerService, m))]

        # Look for methods that suggest automatic summarization
        summarization_methods = [
            "summarize",
            "extract_memory",
            "summarize_conversation",
            "create_summary",
            "auto_summarize",
        ]

        for method in summarization_methods:
            assert method not in method_names, f"SchedulerService should not have {method}"

    @pytest.mark.asyncio
    async def test_no_memory_extraction_in_scheduler_ops(self):
        """Scheduler ops should not contain memory extraction logic."""
        from pathlib import Path

        ops_path = Path("wax/scheduler/ops.py")
        src = ops_path.read_text().lower()

        forbidden_patterns = [
            "memory extraction",
            "summarize",
            "every n messages",
            "automatic memory",
            "extract memory",
            "memory_summarization",
        ]

        for pattern in forbidden_patterns:
            assert pattern not in src, f"Scheduler ops contains forbidden pattern: {pattern}"

    @pytest.mark.asyncio
    async def test_scheduler_only_stores_and_wakes(self, session):
        """Scheduler service should only store time and create wake-up work."""
        from wax.scheduler.service import SchedulerService
        import inspect

        # Get the public methods
        public_methods = [
            m for m in dir(SchedulerService)
            if not m.startswith("_") and callable(getattr(SchedulerService, m))
        ]

        # These are the expected core operations
        expected_operations = {
            "schedule",
            "schedule_at",
            "schedule_in_hours",
            "due_actions",
            "mark_executing",
            "complete",
            "fail",
            "cancel",
            "reschedule",
            "schedule_series",
            "recover_stuck_executing",
            "create_work_for_action",
        }

        for method in expected_operations:
            assert method in public_methods, f"Missing expected method: {method}"

        # No unexpected intelligence-related methods
        unexpected_methods = [
            "analyze",
            "summarize",
            "extract",
            "compile",
            "generate_summary",
        ]

        for method in unexpected_methods:
            assert method not in public_methods, f"Unexpected method found: {method}"


class TestWakeupFlow:
    """End-to-end verification of the wake-up flow."""

    @pytest.mark.asyncio
    async def test_full_schedule_and_wakeup_flow(self, session):
        """Full flow: schedule → due → create work → AI context available."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(
            id=uuid4(),
            display_name="Flow Test Student",
        )
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)

        # Schedule a wake-up for "now" (so it's due immediately)
        execute_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        action = await svc.schedule(
            principal_id=principal.id,
            action_type="immediate_wake",
            execute_at=execute_at,
            reason="Immediate wake-up test",
            payload={
                "message_hint": "Time to check in",
                "channel": "whatsapp",
            },
        )

        # Create the work (simulating the worker doing this)
        work = await svc.create_work_for_action(action)

        # Verify the work contains all the context the AI needs
        payload = work.input_payload or {}

        # The AI should have:
        # 1. Factual context (what triggered, when, why)
        assert payload.get("action_type") == "immediate_wake"
        assert payload.get("reason") == "Immediate wake-up test"
        assert payload.get("message_hint") == "Time to check in"

        # 2. Wakeup notification
        wakeup = payload.get("wakeup", {})
        assert wakeup["trigger"] == "scheduled_action"
        assert wakeup["action_type"] == "immediate_wake"

        # 3. AI instruction about what to do
        assert "ai_instruction" in wakeup
        instruction = wakeup["ai_instruction"]

        # Verify instruction tells AI to decide, not auto-summarize
        assert "You have been woken up" in instruction or "woken up" in instruction
        assert "No automatic memory summarization" in instruction

        # 4. No automatic intelligence in the payload
        assert "summary" not in payload
        assert "extracted_memories" not in payload
        assert "recent_messages" not in payload


class TestAIWakeupDecision:
    """Verify the AI has full discretion over wake-up actions."""

    @pytest.mark.asyncio
    async def test_wakeup_payload_does_not_prejudge_action(self, session):
        """Wake-up payload should not tell the AI what action to take."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(id=uuid4())
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(minutes=1)

        # Schedule with various reasons — none should hardcode behavior
        reasons = [
            "Check in after 10 minutes",
            "Remind about pending exercise",
            "Follow up on yesterday's topic",
            "Inactivity check",
        ]

        for reason in reasons:
            action = await svc.schedule(
                principal_id=principal.id,
                action_type="check",
                execute_at=execute_at,
                reason=reason,
            )
            work = await svc.create_work_for_action(action)
            payload = work.input_payload or {}
            wakeup = payload.get("wakeup", {})

            # The instruction should say "You decide" not "You must"
            instruction = wakeup.get("ai_instruction", "")
            assert "you must" not in instruction.lower()
            assert "you should" not in instruction.lower() or "you decide" in instruction.lower()

    @pytest.mark.asyncio
    async def test_wakeup_payload_neutral_on_channel(self, session):
        """Wake-up payload should not assume a specific delivery channel."""
        from wax.scheduler.service import SchedulerService

        principal = models.Principal(id=uuid4())
        session.add(principal)
        await session.flush()

        svc = SchedulerService(session)
        execute_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        # Schedule without channel in payload
        action = await svc.schedule(
            principal_id=principal.id,
            action_type="neutral_wake",
            execute_at=execute_at,
            reason="Channel-neutral wake-up",
        )

        work = await svc.create_work_for_action(action)
        payload = work.input_payload or {}

        # The wakeup context itself shouldn't mandate a channel
        wakeup = payload.get("wakeup", {})
        assert "channel" not in wakeup or wakeup.get("channel") is None
