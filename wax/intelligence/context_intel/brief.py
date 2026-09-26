"""Structured Context Brief / orchestration contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Confidence = Literal["high", "medium", "low", "none"]
InfoKind = Literal["fact", "evidence", "inference", "hypothesis"]
ResponseMode = Literal["tutor", "unified", "defer"]


@dataclass
class BriefItem:
    kind: InfoKind
    text: str
    source: str = ""
    confidence: Confidence = "medium"
    timestamp: str | None = None
    why: str = ""

    def render(self) -> str:
        conf = self.confidence
        src = f" source={self.source}" if self.source else ""
        why = f" ({self.why})" if self.why else ""
        return f"[{self.kind}|{conf}{src}] {self.text}{why}"


@dataclass
class ContextBrief:
    """
    Orchestration output for this turn.

    Not only retrieved snippets — also strategy: whether more evidence is needed,
    whether the intelligence model may answer directly (unified mode), etc.
    """

    request_understanding: str = ""
    task_intent: str = ""
    no_context_required: bool = False
    insufficient_evidence: bool = False
    # When False, ContextResolver should not blindly re-run full gather_evidence
    needs_evidence_gather: bool = True
    response_mode: ResponseMode = "tutor"
    recommended_objective: str = ""
    response_strategy: str = ""
    direct_reply: str = ""  # only used when response_mode=unified and model produced answer
    items: list[BriefItem] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    investigation_notes: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    rejected_candidates: list[str] = field(default_factory=list)
    # Capability families for tutor tool exposure (empty = no tools)
    capability_families: list[str] = field(default_factory=list)
    degraded: bool = False
    degradation_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def selected_facts(self) -> list[BriefItem]:
        return [i for i in self.items if i.kind in ("fact", "evidence")]

    def selected_inferences(self) -> list[BriefItem]:
        return [i for i in self.items if i.kind in ("inference", "hypothesis")]


def brief_to_tutor_text(brief: ContextBrief, *, max_chars: int = 6000) -> str:
    if brief.no_context_required and not brief.items and not brief.direct_reply:
        return (
            "\n--- Context Intelligence ---\n"
            "No learner-specific context is materially required for this turn.\n"
            "--- End Context Intelligence ---\n"
        )

    lines = ["\n--- Context Intelligence (orchestration) ---"]
    if brief.request_understanding:
        lines.append(f"Request: {brief.request_understanding}")
    if brief.task_intent:
        lines.append(f"Intent: {brief.task_intent}")
    if brief.response_strategy:
        lines.append(f"Strategy: {brief.response_strategy}")
    if brief.insufficient_evidence:
        lines.append("Status: insufficient evidence — do not invent missing facts.")
    if brief.recommended_objective:
        lines.append(f"Objective: {brief.recommended_objective}")
    if brief.needs_evidence_gather is False:
        lines.append("Evidence gather: skipped (intelligence judged not required).")

    facts = brief.selected_facts()
    if facts:
        lines.append("Facts / evidence:")
        for it in facts[:24]:
            lines.append(f"  - {it.render()}")

    inf = brief.selected_inferences()
    if inf:
        lines.append("Inferences (not durable facts):")
        for it in inf[:12]:
            lines.append(f"  - {it.render()}")

    if brief.rejected_candidates:
        lines.append("Rejected as irrelevant:")
        for r in brief.rejected_candidates[:6]:
            lines.append(f"  - {r}")

    if brief.uncertainties:
        lines.append("Uncertainties:")
        for u in brief.uncertainties[:8]:
            lines.append(f"  - {u}")

    if brief.suggested_actions:
        lines.append("Suggested actions (optional):")
        for a in brief.suggested_actions[:6]:
            lines.append(f"  - {a}")

    if brief.degraded:
        lines.append(f"(degraded: {brief.degradation_reason or 'partial'})")

    lines.append(
        "Treat facts/evidence as grounded. Treat inferences as provisional. "
        "Do not write inferences into durable learner state from this block alone."
    )
    lines.append("--- End Context Intelligence ---\n")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…(truncated)\n"
    return text
