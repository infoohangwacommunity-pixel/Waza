"""
Map scenario required_capabilities → source-level evidence in the repo.

This is architecture coverage for conversation scenarios — not a live LLM grade.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(errors="ignore")


def capability_evidence() -> dict[str, bool]:
    tutor = _read("wax", "intelligence", "tutor.py")
    ctx = _read("wax", "intelligence", "context.py")
    registry = _read("wax", "tools", "registry.py")
    tg = _read("wax", "messaging", "telegram", "handler.py")
    wa = _read("wax", "messaging", "whatsapp", "handler.py")
    worker = _read("wax", "workers", "main.py")
    workspace = _read("wax", "terminal", "workspace.py")
    interaction = _read("wax", "interaction", "service.py")
    engine = _read("wax", "work", "engine.py")
    retry = _read("wax", "delivery", "retry.py")

    return {
        "tutor_loop": "class TutorService" in tutor and "handle_message" in tutor,
        "natural_onboarding": "Do not run an intake questionnaire" in tutor
        or "intake questionnaire" in tutor,
        "set_preference": "set_preference" in registry and "handle_set_preference" in registry,
        "preferences_in_context": "_preferences_block" in ctx,
        "message_length_policy": "message_length" in tutor or "Keep replies readable" in tutor,
        "teaching_judgment": "Teaching judgment" in tutor,
        "evidence_tools": "record_evidence" in registry and "form_hypothesis" in registry,
        "retrieval_first": "hint before revealing" in tutor or "retrieval cue" in tutor,
        "transfer_check": "transfer" in tutor.lower(),
        "no_subject_mode": "QuizMode" not in tutor.split("TUTOR_SYSTEM")[1].split('"""')[0]
        if "TUTOR_SYSTEM" in tutor
        else True,
        "memory_retrieve": "plan_and_retrieve" in ctx or "MemoryService" in ctx,
        "session_continuity": "SessionContinuity" in tutor or "session_continuity" in tutor,
        "learner_state": "learner_state" in ctx or "get_learner_state" in registry,
        "transcribe_audio": "transcribe_audio" in registry and "transcribe_local_audio" in worker,
        "media_pipeline": "fetch_inbound_media" in registry and "local_media_path" in worker,
        "ingest_document": "ingest_document" in registry,
        "learner_materials_context": "_learner_materials_block" in ctx,
        "research_search": "research_search" in registry,
        "research_fetch": "research_fetch" in registry,
        "present_choices": "present_choices" in registry,
        "interaction_consume": "InteractionService" in tg and "InteractionService" in wa,
        "present_choices_expires": "expires_in_seconds" in registry,
        "assessment_timeout": "record_assessment_timeout" in registry,
        "workspace_isolation": "principals" in workspace and "path_escape" in registry,
        "path_escape_checks": "path_escape" in registry,
        "tool_honesty": "Never claim a tool" in tutor,
        "schedule_tools": "schedule_at" in registry or "schedule_followup" in registry,
        # reliability
        "work_claim_skip_locked": "with_for_update" in engine and "skip_locked" in engine,
        "delivery_idempotency": "delivery_idempotent_hit" in engine or "idempotency_key" in engine,
        "delivery_retry": "DeliveryRetryService" in retry and "retry_backoff_seconds" in retry,
        "stale_work_reclaim": "reclaim_stale_works" in worker,
        "webhook_dedupe": "on_conflict_do_nothing" in tg and "on_conflict_do_nothing" in wa,
        "callback_dedupe": "tg_cb:" in tg,
    }


def missing_capabilities(required: tuple[str, ...]) -> list[str]:
    ev = capability_evidence()
    return [c for c in required if not ev.get(c, False)]
