
"""Continuity digest: dense personalization, resonance selection."""

from wax.learner.continuity_digest import ContinuityDigest, render_digest_block, resonance_score


def test_resonance_prefers_overlapping_fragments():
    user = "I want to continue my calculus homework on derivatives"
    a = resonance_score(user, "Working on calculus derivatives and limits")
    b = resonance_score(user, "Prefers short WhatsApp replies")
    assert a > b


def test_render_includes_identity_spine():
    dig = ContinuityDigest(
        display_name="Ada",
        relationship="Patient tutor relationship; likes worked examples.",
        focus=["Derivatives chain rule"],
        preferences=["Short messages on WhatsApp"],
        goals=["Pass calculus midterm"],
        updated_at="2099-01-01T00:00:00+00:00",
    )
    text = render_digest_block(dig, "help with chain rule please")
    assert "Ada" in text
    assert "chain" in text.lower() or "Derivatives" in text
    assert "continuity" in text.lower()
    assert len(text) < 2500


def test_fresh_rich():
    dig = ContinuityDigest(
        updated_at="2099-01-01T00:00:00+00:00",
        preferences=["likes examples"],
    )
    # age_hours will be huge if year 2099 is future... actually future means negative age
    # use recent
    from datetime import datetime, timezone, timedelta
    dig.updated_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert dig.is_fresh(24)
    assert dig.is_rich()
