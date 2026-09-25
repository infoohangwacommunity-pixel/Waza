"""Pure rate decision tests — no database required."""

from wax.protection.rate import decide_from_counts, RateDecision


def test_normal_burst_allowed():
    # burst capacity 12 default; tokens start high
    d, tokens = decide_from_counts(tokens=12.0, recent=3, message_count=1, cooldown_active=False)
    assert d == RateDecision.ALLOW.value
    assert tokens == 11.0


def test_human_burst_of_ten_still_allow_while_tokens_remain():
    tokens = 12.0
    last = None
    for i in range(10):
        last, tokens = decide_from_counts(tokens=tokens, recent=i, message_count=1, cooldown_active=False)
    assert last == RateDecision.ALLOW.value


def test_queue_limit_protects():
    d, _ = decide_from_counts(tokens=12.0, recent=40, message_count=1, cooldown_active=False)
    assert d == RateDecision.PROTECT.value


def test_cooldown_throttles():
    d, _ = decide_from_counts(tokens=12.0, recent=1, message_count=1, cooldown_active=True)
    assert d == RateDecision.THROTTLE.value


def test_low_tokens_defer_when_under_sustained():
    d, _ = decide_from_counts(tokens=0.0, recent=5, message_count=1, cooldown_active=False)
    assert d == RateDecision.DEFER.value


def test_low_tokens_throttle_when_sustained_exceeded():
    d, _ = decide_from_counts(tokens=0.0, recent=31, message_count=1, cooldown_active=False)
    assert d == RateDecision.THROTTLE.value
