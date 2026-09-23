"""
Contradiction / current-truth resolution.

Does not invent certainty. Scores competing evidence and returns:
- current (winner)
- historical (losers)
- uncertain (too close to call)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class EvidenceView:
    id: str
    content: str
    kind: str = "semantic"  # goal|preference|semantic|performance
    source: str = "inferred"  # explicit|inferred|observed|correction
    confidence: float = 0.5
    recency: float = 0.5  # 0 old → 1 newest
    is_correction: bool = False
    is_active: bool = True
    observed_at: str | None = None


def _source_weight(source: str, is_correction: bool) -> float:
    if is_correction:
        return 1.15
    s = (source or "").lower()
    if s in ("explicit", "learner_correction", "correction"):
        return 1.0
    if s in ("observed", "performance", "independent"):
        return 0.95
    if s == "inferred":
        return 0.55
    return 0.7


def score_evidence(ev: EvidenceView) -> float:
    """Higher = more likely current truth."""
    return (
        0.35 * max(0.0, min(1.0, ev.confidence))
        + 0.30 * max(0.0, min(1.0, ev.recency))
        + 0.35 * _source_weight(ev.source, ev.is_correction)
    )


def resolve_conflict(items: list[EvidenceView], *, margin: float = 0.12) -> dict[str, Any]:
    """
    Pick current vs historical among competing claims.
    If scores are within margin, status=uncertain (keep both marked uncertain).
    """
    if not items:
        return {"status": "empty", "current": None, "historical": [], "uncertain": []}
    ranked = sorted(items, key=score_evidence, reverse=True)
    if len(ranked) == 1:
        return {
            "status": "current",
            "current": ranked[0],
            "historical": [],
            "uncertain": [],
        }
    top, second = ranked[0], ranked[1]
    if abs(score_evidence(top) - score_evidence(second)) < margin and not top.is_correction:
        return {
            "status": "uncertain",
            "current": None,
            "historical": [],
            "uncertain": ranked,
        }
    return {
        "status": "resolved",
        "current": top,
        "historical": ranked[1:],
        "uncertain": [],
        "winner_score": score_evidence(top),
        "runner_score": score_evidence(second),
    }


def recency_from_timestamps(ts_list: list[datetime | None]) -> list[float]:
    now = datetime.now(timezone.utc)
    ages = []
    for ts in ts_list:
        if ts is None:
            ages.append(1e9)
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ages.append(max(0.0, (now - ts).total_seconds()))
    if not ages:
        return []
    mx = max(ages) or 1.0
    return [1.0 - (a / mx) for a in ages]
