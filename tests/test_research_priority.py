"""Research loop ranks by value of information, not uncertainty alone."""
from types import SimpleNamespace
from wax.memory.research_loop import ResearchLoopService


def _hyp(**kw):
    defaults = dict(
        confidence=0.5,
        claim_key="concept:x",
        claim="x",
        status="active",
        supporting_evidence_ids=[],
        contradicting_evidence_ids=[],
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_concept_outranks_preference_at_same_confidence():
    svc = ResearchLoopService.__new__(ResearchLoopService)
    pref = _hyp(claim_key="preference:diagrams", confidence=0.32)
    concept = _hyp(claim_key="concept:negative_numbers", confidence=0.38)
    assert svc._research_priority(concept) > svc._research_priority(pref)


def test_misconception_high_priority():
    svc = ResearchLoopService.__new__(ResearchLoopService)
    m = _hyp(claim_key="misconception:fraction_add", confidence=0.4)
    p = _hyp(claim_key="preference:examples", confidence=0.4)
    assert svc._research_priority(m) > svc._research_priority(p)


def test_thin_evidence_boosts_priority():
    svc = ResearchLoopService.__new__(ResearchLoopService)
    thin = _hyp(supporting_evidence_ids=[], contradicting_evidence_ids=[])
    thick = _hyp(
        supporting_evidence_ids=["a", "b", "c", "d"],
        contradicting_evidence_ids=["e"],
    )
    assert svc._research_priority(thin) > svc._research_priority(thick)
