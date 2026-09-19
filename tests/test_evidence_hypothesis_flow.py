"""Unit-level flow: confidence moves with supporting vs contradicting evidence."""
from types import SimpleNamespace
from wax.memory.evidence import EvidenceService


def test_odds_update_support_increases():
    svc = EvidenceService.__new__(EvidenceService)
    ev = SimpleNamespace(
        weight=0.7,
        directness=0.8,
        independence=0.9,
        specificity=0.8,
        evidence_type="independent_success",
        assistance_level="independent",
    )
    prior = 0.4
    post = svc._apply_odds_update(prior, ev, supports=True)
    assert post > prior


def test_odds_update_contradict_decreases():
    svc = EvidenceService.__new__(EvidenceService)
    ev = SimpleNamespace(
        weight=0.7,
        directness=0.8,
        independence=0.9,
        specificity=0.8,
        evidence_type="performance",
        assistance_level="independent",
    )
    prior = 0.7
    post = svc._apply_odds_update(prior, ev, supports=False)
    assert post < prior


def test_assisted_weaker_than_independent():
    svc = EvidenceService.__new__(EvidenceService)
    strong = SimpleNamespace(
        weight=0.6, directness=0.8, independence=0.9, specificity=0.7,
        evidence_type="independent_success", assistance_level="independent",
    )
    weak = SimpleNamespace(
        weight=0.6, directness=0.8, independence=0.2, specificity=0.7,
        evidence_type="tutor_intervention", assistance_level="answer_revealed",
    )
    assert abs(svc._confidence_delta(strong, True)) > abs(svc._confidence_delta(weak, True))
