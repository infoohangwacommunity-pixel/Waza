"""Regression: tutor observations must not truncate useful information to ~120 chars.

Fix 1 (audit): _fmt previously cut each result field at 120 characters and the
whole observation line at 800. Now complete results are preserved where
practical, with only a fixed infrastructure safety cap on how much text may
re-enter the model context. No summarization, no selective extraction —
the AI owns meaning; this is transport reality only.
"""

from __future__ import annotations

import json

from wax.intelligence.tutor import TutorService


def test_long_world_stdout_is_preserved_not_truncated_to_120():
    long_output = "L" * 5000
    text = TutorService._fmt({"ok": True, "stdout": long_output})
    # The old behaviour clipped every field at 120 chars; the full payload
    # must survive under the sensible caps (field 8000 / total 32000).
    assert long_output in text
    assert len(text) >= len(long_output)


def test_many_fields_all_survive_under_total_cap():
    result = {f"k{i}": f"v{i}" * 400 for i in range(10)}
    text = TutorService._fmt(result)
    for i in range(10):
        assert f"k{i}=" in text
    assert len(text) <= TutorService.OBSERVATION_TOTAL_CAP


def test_caps_are_fixed_infrastructure_limits_not_semantic_selection():
    huge = "X" * 50000
    text = TutorService._fmt({"ok": True, "stdout": huge})
    assert len(text) <= TutorService.OBSERVATION_TOTAL_CAP
    # Cap values are plain constants declared as an output ceiling.
    assert TutorService.OBSERVATION_FIELD_CAP == 8000
    assert TutorService.OBSERVATION_TOTAL_CAP == 32000


def test_error_results_keep_full_error_detail():
    detail = "boom: " + ("context " * 90)  # >600 chars, previously squeezed by 800-total join
    text = TutorService._fmt({"ok": False, "error": detail})
    assert text.startswith("error:")
    assert detail in text


def test_no_key_limitation_first_eight():
    # Old code kept only the first 8 keys of a result dict.
    result = {"ok": True, **{f"f{i}": str(i) for i in range(15)}}
    text = TutorService._fmt(result)
    for i in range(15):
        assert f"f{i}=" in text


def test_memory_search_style_payload_survives_intact():
    rows = [
        {"id": str(i), "content": json.dumps({"note": f"memory-{i}-" + "y" * 300})}
        for i in range(20)
    ]
    text = TutorService._fmt({"ok": True, "results": rows})
    # Complete results are preserved where practical: every row's marker present.
    for i in range(20):
        assert f"memory-{i}-" in text
