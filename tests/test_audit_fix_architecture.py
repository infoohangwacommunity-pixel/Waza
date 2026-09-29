"""Architecture guard for the two audit correctness fixes.

Rule protected here: infrastructure caps are fixed transport limits, never
intelligence. Semantic summarization / selective extraction of observations
would be the AI's job — and the AI is not supposed to delegate it back to us.
If someone re-adds truncation heuristics to tutor.py, this fails loudly.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tutor_src() -> str:
    return (ROOT / "wax/intelligence/tutor.py").read_text(encoding="utf-8")


def test_observation_caps_are_fixed_constants():
    src = _tutor_src()
    assert "OBSERVATION_FIELD_CAP = 8000" in src
    assert "OBSERVATION_TOTAL_CAP = 32000" in src


def test_no_semantic_truncation_heuristics_in_tutor():
    src = _tutor_src()
    # Strip comments/docstrings: guard behaviour, not prose.
    code_lines = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)
    banned = (
        "summariz",  # summarization of results
        "extract_key",
        "select_relevant",
        "rank_",
        "importance",
        "[:120]",  # the old per-field clip
    )
    for token in banned:
        assert token not in code, (
            f"infrastructure must not decide observation meaning: {token!r}"
        )


def test_outbound_transcript_record_is_idempotent_per_work():
    src = _tutor_src()
    assert "_record_outbound_message" in src
    # Idempotency key derived from Work identity, enforced by unique constraint.
    assert 'f"assistant:{work.id}"' in src or "assistant:%s" % "{work.id}" in src
    assert "begin_nested" in src  # savepoint: duplicates rejected without poisoning txn


def test_reply_recorded_regardless_of_delivery_outcome():
    src = _tutor_src()
    deliver_block = src.split("async def _deliver")[1]
    assert "finally:" in deliver_block
    assert "_record_outbound_message(work, reply)" in deliver_block
