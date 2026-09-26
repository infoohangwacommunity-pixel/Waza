"""
Request-driven capability / tool selection.

Capabilities exist globally; only families required for *this* turn are exposed
to the model. Selection is driven by Context Intelligence (or a bounded default
when CI is unavailable) — not a hardcoded message→tool map.
"""

from __future__ import annotations

from typing import Any, Iterable

# Family → tool names (must match AVAILABLE_TOOLS name= values)
FAMILY_TOOLS: dict[str, frozenset[str]] = {
    "core": frozenset(
        {
            "present_choices",
            "get_current_time",
            "get_learner_state",
        }
    ),
    "memory": frozenset({"inspect_memories", "forget_memory", "set_preference"}),
    "learning": frozenset(
        {
            "record_evidence",
            "form_hypothesis",
            "why_we_believe",
            "propose_learning_check",
            "schedule_hypothesis_recheck",
            "update_concept_state",
            "create_assessment",
            "submit_assessment_answer",
            "record_assessment_timeout",
        }
    ),
    "goals": frozenset(
        {
            "manage_goal",
            "start_activity",
            "complete_activity",
            "pause_activity",
            "resume_activity",
        }
    ),
    "schedule": frozenset(
        {
            "schedule_followup",
            "schedule_at",
            "resolve_natural_time",
            "schedule_intent",
            "schedule_continuous",
            "schedule_series",
            "cancel_schedule",
            "check_quiet_hours",
        }
    ),
    "artifacts": frozenset(
        {
            "create_artifact",
            "list_artifacts",
            "read_artifact",
            "redeliver_artifact",
        }
    ),
    "surfaces": frozenset(
        {
            "create_surface",
            "update_surface",
            "list_surfaces",
            "revoke_surface",
            "inspect_surface",
            "retain_surface",
        }
    ),
    "media": frozenset(
        {
            "fetch_inbound_media",
            "inspect_media",
            "describe_image",
            "transcribe_audio",
            "extract_video_audio",
            "extract_video_frames",
            "extract_subtitles",
            "ingest_document",
        }
    ),
    "workspace": frozenset(
        {
            "run_python",
            "write_workspace_file",
            "read_workspace_file",
            "list_workspace",
            "workspace_command",
            "workspace_env",
            "world_discover",
            "world_exec",
            "world_acquire",
            "world_jobs",
            "world_files",
        }
    ),
    "research": frozenset({"research_fetch", "research_search"}),
    "identity": frozenset(
        {
            "request_channel_link",
            "confirm_channel_link",
            "link_channel_identity",
            "export_learner_data",
        }
    ),
}

# Always available for safety when tools are enabled at all
ALWAYS_CORE = frozenset({"present_choices", "get_current_time"})

# Bounded default when CI does not specify families (not "all tools")
DEFAULT_FAMILIES = frozenset({"core", "memory", "learning"})

# Lightweight path: greeting / pure Q with no personalization
MINIMAL_FAMILIES = frozenset({"core"})


def normalize_families(raw: Iterable[str] | None) -> set[str]:
    out: set[str] = set()
    for f in raw or []:
        key = str(f or "").strip().lower().replace("-", "_")
        if key in FAMILY_TOOLS:
            out.add(key)
        # aliases from model wording
        elif key in ("memories", "preference", "preferences"):
            out.add("memory")
        elif key in ("teach", "tutoring", "assessment", "hypothesis", "evidence"):
            out.add("learning")
        elif key in ("goal", "activity", "activities"):
            out.add("goals")
        elif key in ("calendar", "reminder", "followup", "follow_up"):
            out.add("schedule")
        elif key in ("file", "files", "document", "pdf", "material", "materials"):
            out.add("artifacts")
            out.add("media")
        elif key in ("surface", "web_surface", "page"):
            out.add("surfaces")
        elif key in ("code", "python", "compute", "terminal", "sandbox"):
            out.add("workspace")
        elif key in ("web", "search", "browse", "url"):
            out.add("research")
        elif key in ("channel", "whatsapp", "telegram", "link"):
            out.add("identity")
        elif key in ("none", "empty", "direct"):
            pass
    return out


def families_from_brief(brief: Any | None) -> set[str]:
    """Derive capability families from a ContextBrief (or compatible object)."""
    if brief is None:
        return set(DEFAULT_FAMILIES)

    if getattr(brief, "no_context_required", False) and not getattr(
        brief, "suggested_actions", None
    ):
        # Explicit families from CI win; empty list means truly no tools.
        raw_fams = getattr(brief, "capability_families", None)
        meta = getattr(brief, "metadata", None) or {}
        if raw_fams is not None:
            return normalize_families(raw_fams)
        if isinstance(meta, dict) and "capability_families" in meta:
            return normalize_families(meta.get("capability_families"))
        return set()  # no tools for pure social / no-context turns

    explicit = normalize_families(getattr(brief, "capability_families", None))
    if explicit:
        return explicit

    meta = getattr(brief, "metadata", None) or {}
    if isinstance(meta, dict) and meta.get("capability_families"):
        explicit = normalize_families(meta.get("capability_families"))
        if explicit:
            return explicit

    # Infer lightly from suggested_actions / strategy text (still not a message classifier)
    hints = " ".join(
        [
            str(getattr(brief, "response_strategy", "") or ""),
            str(getattr(brief, "recommended_objective", "") or ""),
            " ".join(str(a) for a in (getattr(brief, "suggested_actions", None) or [])),
            " ".join(str(t) for t in (getattr(brief, "tools_used", None) or [])),
        ]
    ).lower()
    inferred: set[str] = set(DEFAULT_FAMILIES)
    for token, family in (
        ("memory", "memory"),
        ("preference", "memory"),
        ("goal", "goals"),
        ("schedule", "schedule"),
        ("follow", "schedule"),
        ("artifact", "artifacts"),
        ("material", "artifacts"),
        ("pdf", "media"),
        ("upload", "media"),
        ("surface", "surfaces"),
        ("python", "workspace"),
        ("code", "workspace"),
        ("research", "research"),
        ("search the web", "research"),
        ("channel", "identity"),
        ("whatsapp", "identity"),
        ("telegram", "identity"),
    ):
        if token in hints:
            inferred.add(family)
    return inferred


def select_tool_specs(all_tools: list[Any], families: set[str] | None) -> list[Any]:
    """Filter ToolSpec list to the selected families (+ always-core if any tools)."""
    if not families:
        # Zero capabilities: no tool schemas in the request
        return []
    allowed: set[str] = set(ALWAYS_CORE)
    for fam in families:
        allowed |= FAMILY_TOOLS.get(fam, frozenset())
    selected = [t for t in all_tools if getattr(t, "name", None) in allowed]
    return selected


def instrumentation(
    *,
    families: set[str],
    tools_exposed: int,
    tools_total: int,
    path: str,
) -> dict[str, Any]:
    return {
        "capability_families": sorted(families),
        "tools_exposed": tools_exposed,
        "tools_total": tools_total,
        "orchestration_path": path,
        "tool_schema_reduction": max(0, tools_total - tools_exposed),
    }
