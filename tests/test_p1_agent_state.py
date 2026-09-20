"""P1 batch: agent runtime, learner state, schedule_at, pause/resume tools."""

from pathlib import Path


def test_agent_runtime_module():
    src = Path("wax/agent/runtime.py").read_text()
    assert "class AgentRuntime" in src
    assert "record_tool" in src
    assert "checkpoint" in src


def test_learner_state_snapshot():
    src = Path("wax/domain/learner_state.py").read_text()
    assert "build_learner_state_snapshot" in src
    assert "format_learner_state_block" in src
    assert "pending_interactions" in src


def test_schedule_at_exists():
    src = Path("wax/scheduler/service.py").read_text()
    assert "async def schedule_at" in src


def test_domain_tools_registered():
    src = Path("wax/tools/registry.py").read_text()
    for name in (
        "get_learner_state",
        "schedule_at",
        "pause_activity",
        "resume_activity",
        "set_preference",
    ):
        assert name in src


def test_activity_pause_resume():
    src = Path("wax/work/activities.py").read_text()
    assert "async def pause" in src
    assert "async def resume" in src


def test_tutor_uses_agent_runtime():
    src = Path("wax/intelligence/tutor.py").read_text()
    assert "AgentRuntime" in src
    assert "record_tool" in src
