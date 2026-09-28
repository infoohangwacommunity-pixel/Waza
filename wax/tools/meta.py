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
    "present_choices": {"risk": "medium", "permissions": [MESSAGE, DATABASE_WRITE]},
    "set_preference": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "manage_goal": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "schedule": {"risk": "medium", "permissions": [SCHEDULE]},
    "cancel_schedule": {"risk": "medium", "permissions": [SCHEDULE]},
    "create_artifact": {"risk": "medium", "permissions": [FILE_WRITE, DATABASE_WRITE]},
    "redeliver_artifact": {"risk": "medium", "permissions": [MESSAGE]},
    "export_learner_data": {"risk": "medium", "permissions": [DATABASE_READ]},
    "link_channel_identity": {"risk": "high", "permissions": [DATABASE_WRITE]},
    "request_channel_link": {"risk": "high", "permissions": [MESSAGE, DATABASE_WRITE]},
    "confirm_channel_link": {"risk": "high", "permissions": [DATABASE_WRITE]},
    "retain_surface": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "inspect_surface": {"risk": "low", "permissions": [DATABASE_READ]},
    "create_surface": {"risk": "medium", "permissions": [FILE_WRITE, DATABASE_WRITE]},
    "update_surface": {"risk": "medium", "permissions": [FILE_WRITE, DATABASE_WRITE]},
    "list_surfaces": {"risk": "low", "permissions": [DATABASE_READ]},
    "revoke_surface": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "world_discover": {"risk": "low", "permissions": [READ]},
    "world_exec": {"risk": "high", "permissions": [CODE_EXECUTION]},
    "world_acquire": {"risk": "medium", "permissions": [WRITE, EXTERNAL_NETWORK]},
    "world_files": {"risk": "medium", "permissions": [READ, WRITE]},
    "world_jobs": {"risk": "low", "permissions": [READ]},
}


def tool_meta(name: str) -> dict[str, Any]:
    return dict(TOOL_META.get(name, {"risk": "medium", "permissions": [READ]}))
