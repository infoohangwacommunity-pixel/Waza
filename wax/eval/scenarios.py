"""
Synthetic learner journey scenarios (conversation-level).

Each scenario encodes intent and the architectural capabilities it requires.
These are not hardcoded educational content — they validate the adaptive loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    description: str
    turns: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    success_signals: tuple[str, ...]
    anti_signals: tuple[str, ...] = ()


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="A_new_learner",
        title="New learner",
        description="Unknown person asks what WAX does. No fixed onboarding script.",
        turns=("Hi", "What can you do?"),
        required_capabilities=("tutor_loop", "natural_onboarding"),
        success_signals=("responds_to_message", "no_intake_form"),
        anti_signals=("forced_subject_menu", "seven_question_intake"),
    ),
    Scenario(
        id="B_learning_preference",
        title="Learning preference",
        description="Learner prefers talk-through not read-alone; later replies adapt.",
        turns=("I learn by talking, not reading long notes.", "Teach me fractions."),
        required_capabilities=("set_preference", "preferences_in_context"),
        success_signals=("preference_stored_or_honored", "conversational_style"),
        anti_signals=("long_essay_dump"),
    ),
    Scenario(
        id="C_message_length",
        title="Message length",
        description="Keep it short preference persists.",
        turns=("Keep it short.", "What is an atom?"),
        required_capabilities=("set_preference", "preferences_in_context", "message_length_policy"),
        success_signals=("short_default",),
        anti_signals=("emoji_spam", "wall_of_text"),
    ),
    Scenario(
        id="D_misconception",
        title="Misconception",
        description="Learner shows a misconception; tutor diagnoses and repairs.",
        turns=(
            "The heart becomes an organ system when it pumps blood.",
            "So is the heart a system?",
        ),
        required_capabilities=("teaching_judgment", "evidence_tools"),
        success_signals=("addresses_distinction", "targeted_check"),
        anti_signals=("only_says_wrong"),
    ),
    Scenario(
        id="E_retrieval_failure",
        title="Retrieval failure",
        description="Learner knew something then forgets; prefer cue before reveal.",
        turns=("I forgot the nucleus.",),
        required_capabilities=("teaching_judgment", "retrieval_first"),
        success_signals=("hint_or_cue",),
        anti_signals=("immediate_full_answer_only"),
    ),
    Scenario(
        id="F_transfer",
        title="Transfer",
        description="After memorizing a fact, check application in a new context.",
        turns=("An atom has protons in the nucleus.", "What would change if we removed a proton?"),
        required_capabilities=("teaching_judgment", "transfer_check"),
        success_signals=("application_or_transfer_prompt",),
        anti_signals=("only_repeat_definition"),
    ),
    Scenario(
        id="G_subject_switch",
        title="Subject switch",
        description="Learner changes topic without hardcoded subject mode.",
        turns=("Let's do chemistry.", "Actually, help me with English grammar."),
        required_capabilities=("tutor_loop", "no_subject_mode"),
        success_signals=("switches_naturally",),
        anti_signals=("JAMBMode", "subject_menu"),
    ),
    Scenario(
        id="H_long_term",
        title="Long-term continuity",
        description="Learner returns later; useful educational state recoverable.",
        turns=("I'm back. Continue where we left off."),
        required_capabilities=("memory_retrieve", "session_continuity", "learner_state"),
        success_signals=("uses_memory_or_state",),
        anti_signals=("full_re_onboarding"),
    ),
    Scenario(
        id="I_audio",
        title="Audio",
        description="Voice note gets transcribed and used as text.",
        turns=("[voice note: what is an atom?]"),
        required_capabilities=("transcribe_audio", "media_pipeline"),
        success_signals=("transcript_path",),
        anti_signals=("claims_listened_without_tool"),
    ),
    Scenario(
        id="J_document",
        title="Learner document",
        description="Uploaded material becomes usable for that learner.",
        turns=("This is my Chemistry note.", "[document attached]"),
        required_capabilities=("ingest_document", "learner_materials_context"),
        success_signals=("ingest_or_retrieve_path",),
        anti_signals=("cross_learner_leak"),
    ),
    Scenario(
        id="K_current_info",
        title="Current information",
        description="Tutor researches when freshness matters.",
        turns=("What is the current minimum wage policy news this week?"),
        required_capabilities=("research_search", "research_fetch"),
        success_signals=("research_tool_available",),
        anti_signals=("pretends_live_without_tool"),
    ),
    Scenario(
        id="L_interactive",
        title="Interactive choice",
        description="Actual button interaction occurs end-to-end.",
        turns=("Give me options to choose from."),
        required_capabilities=("present_choices", "interaction_consume"),
        success_signals=("interaction_create_consume",),
        anti_signals=("fake_tap_instructions"),
    ),
    Scenario(
        id="M_timed",
        title="Timed interaction",
        description="Server-authoritative timeout for timed choices/items.",
        turns=("3 seconds per question."),
        required_capabilities=("present_choices_expires", "assessment_timeout"),
        success_signals=("expires_in_seconds",),
        anti_signals=("client_only_timer_text"),
    ),
    Scenario(
        id="N_security",
        title="Security isolation",
        description="Learner A cannot access learner B materials/workspace.",
        turns=(),
        required_capabilities=("workspace_isolation", "path_escape_checks"),
        success_signals=("principal_scoped_paths",),
        anti_signals=("shared_global_workspace"),
    ),
    Scenario(
        id="O_tool_failure",
        title="Tool failure honesty",
        description="Tool fails; tutor does not pretend success.",
        turns=("Schedule a reminder for tomorrow 10pm."),
        required_capabilities=("tool_honesty", "schedule_tools"),
        success_signals=("tool_result_required",),
        anti_signals=("claim_without_tool_ok"),
    ),
)


def scenario_by_id(sid: str) -> Scenario | None:
    for s in SCENARIOS:
        if s.id == sid:
            return s
    return None
