
from wax.memory.evidence import EvidenceService, EVIDENCE_TYPES, ASSISTANCE_LEVELS

def test_taxonomies():
    assert "explicit" in EVIDENCE_TYPES
    assert "independent" in ASSISTANCE_LEVELS
    assert "transfer" in EVIDENCE_TYPES

def test_confidence_delta_independent_stronger_than_assisted():
    class E:
        weight = 0.6
        directness = 0.8
        independence = 0.9
        specificity = 0.7
        evidence_type = "independent_success"
        assistance_level = "independent"
    class E2:
        weight = 0.6
        directness = 0.8
        independence = 0.2
        specificity = 0.7
        evidence_type = "tutor_intervention"
        assistance_level = "answer_revealed"
    # Use unbound method via instance dummy
    class S:
        session = None
    svc = EvidenceService.__new__(EvidenceService)
    d1 = EvidenceService._confidence_delta(svc, E(), supports=True)
    d2 = EvidenceService._confidence_delta(svc, E2(), supports=True)
    assert d1 > d2
