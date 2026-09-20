"""Tool risk / permission metadata — software policy, not prompt-only."""

from __future__ import annotations

from typing import Any

# permission classes
READ = "READ"
WRITE = "WRITE"
EXTERNAL_NETWORK = "EXTERNAL_NETWORK"
SCHEDULE = "SCHEDULE"
MESSAGE = "MESSAGE"
DATABASE_READ = "DATABASE_READ"
DATABASE_WRITE = "DATABASE_WRITE"
CODE_EXECUTION = "CODE_EXECUTION"
FILE_WRITE = "FILE_WRITE"

TOOL_META: dict[str, dict[str, Any]] = {
    "get_current_time": {"risk": "low", "permissions": [READ]},
    "get_learner_state": {"risk": "low", "permissions": [DATABASE_READ]},
    "inspect_memories": {"risk": "low", "permissions": [DATABASE_READ]},
    "list_artifacts": {"risk": "low", "permissions": [DATABASE_READ]},
    "read_artifact": {"risk": "low", "permissions": [DATABASE_READ]},
    "list_workspace": {"risk": "low", "permissions": [READ]},
    "read_workspace_file": {"risk": "low", "permissions": [READ]},
    "present_choices": {"risk": "medium", "permissions": [MESSAGE, DATABASE_WRITE]},
    "set_preference": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "manage_goal": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "schedule_followup": {"risk": "medium", "permissions": [SCHEDULE]},
    "schedule_at": {"risk": "medium", "permissions": [SCHEDULE]},
    "schedule_continuous": {"risk": "medium", "permissions": [SCHEDULE]},
    "pause_activity": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "resume_activity": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "start_activity": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "complete_activity": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "create_artifact": {"risk": "medium", "permissions": [FILE_WRITE, DATABASE_WRITE]},
    "write_workspace_file": {"risk": "medium", "permissions": [FILE_WRITE]},
    "run_python": {"risk": "high", "permissions": [CODE_EXECUTION]},
    "workspace_command": {"risk": "high", "permissions": [CODE_EXECUTION]},
    "research_fetch": {"risk": "high", "permissions": [EXTERNAL_NETWORK]},
    "research_search": {"risk": "high", "permissions": [EXTERNAL_NETWORK]},
    "record_assessment_timeout": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "workspace_env": {"risk": "low", "permissions": [READ, WRITE]},
    "check_quiet_hours": {"risk": "low", "permissions": [READ]},
    "cancel_schedule": {"risk": "medium", "permissions": [SCHEDULE]},
    "schedule_series": {"risk": "medium", "permissions": [SCHEDULE]},
    "redeliver_artifact": {"risk": "medium", "permissions": [MESSAGE]},
    "export_learner_data": {"risk": "medium", "permissions": [DATABASE_READ]},
    "link_channel_identity": {"risk": "high", "permissions": [DATABASE_WRITE]},
    "create_html_page": {"risk": "medium", "permissions": [FILE_WRITE]},
    "create_assessment": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "submit_assessment_answer": {"risk": "medium", "permissions": [DATABASE_WRITE]},
}


def tool_meta(name: str) -> dict[str, Any]:
    return dict(TOOL_META.get(name, {"risk": "medium", "permissions": [READ]}))
