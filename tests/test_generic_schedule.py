import pytest
from unittest.mock import AsyncMock, MagicMock
from wax.tools.registry import execute_tool


@pytest.mark.asyncio
async def test_generic_schedule_and_cancel():
    session = AsyncMock()

    ctx = {"principal_id": "test-scheduler-user"}

    # Mock TemporalService create_intent
    mock_intent = MagicMock()
    mock_intent.id = "intent-123"
    mock_intent.scheduled_action_id = "action-456"
    mock_intent.execute_at.isoformat.return_value = "2026-10-01T10:00:00+00:00"
    mock_intent.timezone = "UTC"

    mock_svc = MagicMock()
    mock_svc.create_intent = AsyncMock(return_value=mock_intent)

    mock_action = MagicMock()
    mock_action.status = "cancelled"
    mock_action.id = "action-456"
    mock_sched = MagicMock()
    mock_sched.cancel = AsyncMock(return_value=mock_action)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("wax.learner.temporal.TemporalService", lambda s: mock_svc)
        mp.setattr("wax.scheduler.service.SchedulerService", lambda s: mock_sched)

        # 1. Schedule with delay_hours and flexible payload
        res = await execute_tool(
            session,
            "schedule",
            {
                "reason": "check_in_on_fractions",
                "delay_hours": 2,
                "purpose": "followup",
                "payload": {"topic": "fractions", "difficulty": "hard"},
            },
            ctx,
        )
        assert res["ok"] is True
        assert res["action_id"] == "action-456"

        # 2. Cancel schedule
        cancel_res = await execute_tool(
            session,
            "cancel_schedule",
            {"action_id": "action-456"},
            ctx,
        )
        assert cancel_res["ok"] is True
        assert cancel_res["status"] == "cancelled"
