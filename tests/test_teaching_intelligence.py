"""Phase 3: teaching policy in prompt + preference surface in context."""

from pathlib import Path


def test_tutor_system_has_teaching_judgment():
    src = Path("wax/intelligence/tutor.py").read_text()
    assert "Teaching judgment" in src
    assert "hint before revealing" in src or "retrieval cue" in src
    assert "misconception" in src
    assert "Question 1" in src
    assert "record_evidence" in src
    assert "set_preference" in src
    assert "Never claim a tool" in src


def test_tutor_system_no_subject_hardcoding():
    src = Path("wax/intelligence/tutor.py").read_text()
    block_start = src.index("TUTOR_SYSTEM")
    block = src[block_start : src.index('"""', block_start + 20) + 3]
    for banned in ("JAMBMode", "QuizMode", "ExamMode", "ChemistryTutor"):
        assert banned not in block


def test_context_surfaces_preferences():
    src = Path("wax/intelligence/context.py").read_text()
    assert "_preferences_block" in src
    assert "Durable preferences" in src
    assert "message_length" in src


def test_preference_defaults_include_emoji_tone():
    src = Path("wax/domain/preferences.py").read_text()
    assert '"emoji"' in src
    assert '"tone"' in src
    assert '"learning_style"' in src
    assert '"message_length"' in src


def test_set_preference_allows_emoji_tone_style():
    src = Path("wax/tools/registry.py").read_text()
    assert '"emoji"' in src
    assert '"tone"' in src
    assert '"learning_style"' in src
    assert "handle_set_preference" in src
