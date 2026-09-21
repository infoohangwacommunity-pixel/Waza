"""Phase 6: conversation-level evaluation suite (architecture coverage)."""

from wax.eval.scenarios import SCENARIOS, scenario_by_id
from wax.eval.rubric import RUBRIC_CRITERIA, rubric_ids
from wax.eval.capability_map import capability_evidence, missing_capabilities


def test_all_master_scenarios_present():
    ids = {s.id for s in SCENARIOS}
    expected = {
        "A_new_learner",
        "B_learning_preference",
        "C_message_length",
        "D_misconception",
        "E_retrieval_failure",
        "F_transfer",
        "G_subject_switch",
        "H_long_term",
        "I_audio",
        "J_document",
        "K_current_info",
        "L_interactive",
        "M_timed",
        "N_security",
        "O_tool_failure",
    }
    assert expected <= ids


def test_rubric_covers_core_dimensions():
    ids = set(rubric_ids())
    for need in (
        "context",
        "misconceptions",
        "preferences",
        "tools",
        "media",
        "continuity",
        "failure",
    ):
        assert need in ids


def test_each_scenario_capabilities_present_in_codebase():
    """Every required capability for every scenario must exist in the repo."""
    failures = []
    for s in SCENARIOS:
        missing = missing_capabilities(s.required_capabilities)
        if missing:
            failures.append(f"{s.id}: missing {missing}")
    assert not failures, failures


def test_capability_evidence_has_reliability_flags():
    ev = capability_evidence()
    for key in (
        "work_claim_skip_locked",
        "delivery_retry",
        "stale_work_reclaim",
        "webhook_dedupe",
        "callback_dedupe",
        "transcribe_audio",
        "ingest_document",
        "teaching_judgment",
    ):
        assert ev.get(key) is True, f"{key} not evidenced"


def test_scenario_lookup():
    s = scenario_by_id("I_audio")
    assert s is not None
    assert "transcribe_audio" in s.required_capabilities


def test_no_quizmode_in_tutor_system_for_subject_switch():
    s = scenario_by_id("G_subject_switch")
    assert s is not None
    missing = missing_capabilities(s.required_capabilities)
    assert not missing
