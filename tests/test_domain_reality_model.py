"""Domain model is durable reality, not educational theory."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_goal_episode_link_tool_classes():
    src = (ROOT / "wax/db/models.py").read_text()
    for banned in (
        "class Goal",
        "class MemoryEpisode",
        "class MemoryLink",
        "class ToolExecution",
        "class Activity",
        "class Curriculum",
        "class Assessment",
        "class Quiz",
        "class Lesson",
        "class ChannelLinkChallenge",
        "class Mastery",
        "class Misconception",
        "class KnowledgeGraph",
    ):
        assert banned not in src


def test_core_reality_classes_exist():
    src = (ROOT / "wax/db/models.py").read_text()
    for needed in (
        "class Principal",
        "class World",
        "class InterfaceIdentity",
        "class Conversation",
        "class Message",
        "class Work",
        "class Execution",
        "class Delivery",
        "class Memory",
        "class Artifact",
        "class ScheduledAction",
        "class Interaction",
        "class Surface",
        "class PrincipalWorkload",
    ):
        assert needed in src


def test_no_checkin_columns_on_workload():
    src = (ROOT / "wax/db/models.py").read_text()
    start = src.find("class PrincipalWorkload")
    block = src[start : start + 2000]
    assert "last_checkin_at" not in block
    assert "checkin_week" not in block


def test_memory_has_no_evidence_column():
    src = (ROOT / "wax/db/models.py").read_text()
    start = src.find("class Memory(")
    end = src.find("class Artifact(", start)
    assert "evidence" not in src[start:end]


def test_no_checkin_settings():
    src = (ROOT / "wax/config/settings.py").read_text()
    assert "checkin_" not in src


def test_no_publication_token_alias():
    src = (ROOT / "wax/surfaces/tokens.py").read_text()
    assert "WAX_PUBLICATION_TOKEN_SECRET" not in src
