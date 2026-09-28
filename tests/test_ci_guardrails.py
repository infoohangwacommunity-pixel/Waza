"""
CI Architecture Guardrails Test.

Ensures removed feature-specific/narrow tools cannot be re-introduced into HANDLERS,
AVAILABLE_TOOLS, or CAPABILITY_FAMILIES.
"""

from __future__ import annotations

import pytest
from wax.tools.registry import HANDLERS
from wax.intelligence.tutor import AVAILABLE_TOOLS
from wax.intelligence.capability_resolve import FAMILY_TOOLS

FORBIDDEN_TOOLS = [
    # Media tools
    "transcribe_audio",
    "extract_video_audio",
    "extract_video_frames",
    "extract_subtitles",
    "describe_image",
    "inspect_media",
    "ingest_document",
    "fetch_inbound_media",
    # Legacy web tools
    "create_html_page",
    "publish_web_surface",
    "revoke_publication",
    # Legacy hypothesis / learning tools
    "form_hypothesis",
    "record_evidence",
    "why_we_believe",
    "propose_learning_check",
    "schedule_hypothesis_recheck",
    "start_activity",
    "complete_activity",
    "pause_activity",
    "resume_activity",
    "create_assessment",
    "submit_assessment_answer",
    "record_assessment_timeout",
    "update_concept_state",
    # Research tools (folded into terminal execution)
    "research_fetch",
    "research_search",
    # Legacy workspace aliases
    "run_python",
    "workspace_command",
    "list_workspace",
    "read_workspace_file",
    "write_workspace_file",
    "workspace_env",
]


def test_forbidden_tools_absent_from_handlers():
    for tool in FORBIDDEN_TOOLS:
        assert tool not in HANDLERS, f"Forbidden tool '{tool}' found in HANDLERS!"


def test_forbidden_tools_absent_from_available_tools():
    available_names = {t.name for t in AVAILABLE_TOOLS}
    for tool in FORBIDDEN_TOOLS:
        assert tool not in available_names, f"Forbidden tool '{tool}' found in AVAILABLE_TOOLS!"


def test_forbidden_tools_absent_from_capability_families():
    for family, tools in FAMILY_TOOLS.items():
        for tool in FORBIDDEN_TOOLS:
            assert tool not in tools, f"Forbidden tool '{tool}' found in FAMILY_TOOLS['{family}']!"


def test_generic_primitives_present():
    expected_primitives = {
        "schedule",
        "cancel_schedule",
        "get_current_time",
        "get_learner_state",
        "set_preference",
        "manage_goal",
        "inspect_memories",
        "forget_memory",
        "create_artifact",
        "list_artifacts",
        "read_artifact",
        "redeliver_artifact",
        "export_learner_data",
        "link_channel_identity",
        "request_channel_link",
        "confirm_channel_link",
        "present_choices",
        "create_surface",
        "update_surface",
        "list_surfaces",
        "inspect_surface",
        "revoke_surface",
        "retain_surface",
        "world_discover",
        "world_exec",
        "world_acquire",
        "world_files",
        "world_jobs",
    }
    available_names = {t.name for t in AVAILABLE_TOOLS}
    missing = expected_primitives - available_names
    assert not missing, f"Expected generic primitives missing from AVAILABLE_TOOLS: {missing}"
