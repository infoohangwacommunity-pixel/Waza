"""Primitive risk / permission metadata — software policy, not prompt-only."""

from __future__ import annotations

from typing import Any

READ = "READ"
WRITE = "WRITE"
EXTERNAL_NETWORK = "EXTERNAL_NETWORK"
SCHEDULE = "SCHEDULE"
MESSAGE = "MESSAGE"
DATABASE_READ = "DATABASE_READ"
DATABASE_WRITE = "DATABASE_WRITE"
CODE_EXECUTION = "CODE_EXECUTION"
FILE_WRITE = "FILE_WRITE"

# Metadata keyed by primitive name (wax.primitives.registry)
TOOL_META: dict[str, dict[str, Any]] = {
    "memory_search": {"risk": "low", "permissions": [DATABASE_READ]},
    "memory_get": {"risk": "low", "permissions": [DATABASE_READ]},
    "memory_create": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "memory_update": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "memory_supersede": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "memory_forget": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "world_discover": {"risk": "low", "permissions": [READ]},
    "world_exec": {"risk": "high", "permissions": [CODE_EXECUTION]},
    "world_acquire": {"risk": "medium", "permissions": [WRITE, EXTERNAL_NETWORK]},
    "world_files": {"risk": "medium", "permissions": [READ, FILE_WRITE]},
    "get_current_time": {"risk": "low", "permissions": [READ]},
    "schedule": {"risk": "medium", "permissions": [SCHEDULE]},
    "cancel_schedule": {"risk": "medium", "permissions": [SCHEDULE]},
    "list_scheduled": {"risk": "low", "permissions": [DATABASE_READ]},
    "present_choices": {"risk": "medium", "permissions": [MESSAGE, DATABASE_WRITE]},
    "publish": {"risk": "medium", "permissions": [FILE_WRITE, DATABASE_WRITE]},
    "update_surface": {"risk": "medium", "permissions": [FILE_WRITE, DATABASE_WRITE]},
    "revoke_surface": {"risk": "medium", "permissions": [DATABASE_WRITE]},
    "set_preference": {"risk": "medium", "permissions": [DATABASE_WRITE]},
}


def tool_meta(name: str) -> dict[str, Any]:
    return TOOL_META.get(name) or {"risk": "medium", "permissions": [READ]}
