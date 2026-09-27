"""
Continuity Digest — dense, durable personalization without re-retrieving the world.

Hot path: inject a compact, resonance-selected portrait of the learner so the tutor
is *more* personal with *fewer* tokens and *fewer* provider calls.

Cold path: rebuild from structured Waza data (memories, goals, concept state) —
no keyword subject router, no second LLM required on the request path.

The digest is the learner's "rolling self-model" the system already believes,
not a re-search of every table every turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

def _log():
    try:
        from wax.observability.logging import get_logger
        return get_logger(__name__)
    except Exception:
        import logging
        return logging.getLogger(__name__)


class _L:
    def exception(self, *a, **k):
        _log().exception(*a, **k)
    def info(self, *a, **k):
        _log().info(*a, **k)


logger = _L()

DIGEST_META_KEY = "continuity_digest"
DEFAULT_MAX_AGE_HOURS = 72
DEFAULT_RENDER_CHARS = 1400


@dataclass
class ContinuityDigest:
    version: int = 1
    updated_at: str = ""
    display_name: str = ""
    relationship: str = ""
    focus: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    open_threads: list[str] = field(default_factory=list)
    mastery: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    style_notes: list[str] = field(default_factory=list)
    # Capability families that were useful recently (orchestration hint, not a classifier)
    recent_families: list[str] = field(default_factory=list)

    def age_hours(self) -> float:
        if not self.updated_at:
            return 1e9
        try:
            ts = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        except Exception:
            return 1e9

    def is_fresh(self, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> bool:
        return bool(self.updated_at) and self.age_hours() <= max_age_hours

    def is_rich(self) -> bool:
        return bool(
            self.preferences
            or self.focus
            or self.goals
            or self.open_threads
            or self.mastery
            or self.relationship
        )


def _tok(s: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in (s or "")).split() if len(w) > 2}


def resonance_score(user_text: str, fragment: str) -> float:
    """Content overlap between this message and a digest fragment (no subject taxonomy)."""
    a, b = _tok(user_text), _tok(fragment)
    if not a or not b:
        return 0.05  # tiny base so stable identity still surfaces
    inter = len(a & b)
    return inter / max(1, len(a)) + 0.02 * min(inter, 5)


def render_digest_block(
    digest: ContinuityDigest,
    user_text: str = "",
    *,
    max_chars: int = DEFAULT_RENDER_CHARS,
) -> str:
    """
    Select the most resonant slices of the digest for *this* message.
    Always includes a short relationship/identity spine when present.
    """
    if not digest.is_rich() and not digest.display_name:
        return ""

    lines: list[str] = ["\n--- Learner continuity (dense self-model) ---"]
    if digest.display_name:
        lines.append(f"Name: {digest.display_name}")
    if digest.relationship:
        lines.append(f"Relationship: {digest.relationship[:240]}")

    buckets: list[tuple[str, list[str]]] = [
        ("Focus", digest.focus),
        ("Preferences", digest.preferences),
        ("Goals", digest.goals),
        ("Open threads", digest.open_threads),
        ("Mastery / struggle", digest.mastery),
        ("Style", digest.style_notes),
    ]
    scored: list[tuple[float, str, str]] = []
    for label, items in buckets:
        for item in items:
            if not (item or "").strip():
                continue
            scored.append((resonance_score(user_text, item), label, item.strip()[:220]))

    scored.sort(key=lambda x: -x[0])
    used_labels: set[str] = set()
    for score, label, item in scored:
        # Always take high resonance; keep some low for continuity spine
        if score < 0.08 and len(used_labels) >= 3 and score < 0.12:
            continue
        lines.append(f"{label}: {item}")
        used_labels.add(label)
        if sum(len(x) for x in lines) > max_chars:
            break

    lines.append(
        "Use this self-model as authoritative continuity. "
        "Prefer it over inventing biography. Retrieve more only if a claim is missing."
    )
    lines.append("--- End continuity ---\n")
    text = "\n".join(lines)
    if len(text) > max_chars + 200:
        text = text[: max_chars + 180] + "\n…\n"
    return text


async def load_digest(session, principal_id) -> ContinuityDigest | None:
    from wax.db.models import Principal

    p = await session.get(Principal, principal_id)
    if p is None:
        return None
    meta = dict(getattr(p, "metadata_", None) or {})
    raw = meta.get(DIGEST_META_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return ContinuityDigest(
            version=int(raw.get("version") or 1),
            updated_at=str(raw.get("updated_at") or ""),
            display_name=str(raw.get("display_name") or "")[:120],
            relationship=str(raw.get("relationship") or "")[:400],
            focus=[str(x)[:220] for x in (raw.get("focus") or [])[:8]],
            preferences=[str(x)[:220] for x in (raw.get("preferences") or [])[:8]],
            open_threads=[str(x)[:220] for x in (raw.get("open_threads") or [])[:6]],
            mastery=[str(x)[:220] for x in (raw.get("mastery") or [])[:8]],
            goals=[str(x)[:220] for x in (raw.get("goals") or [])[:6]],
            style_notes=[str(x)[:180] for x in (raw.get("style_notes") or [])[:4]],
            recent_families=[str(x)[:40] for x in (raw.get("recent_families") or [])[:8]],
        )
    except Exception:
        logger.exception("continuity_digest_load_failed")
        return None


async def rebuild_digest_from_state(
    session,
    principal_id,
) -> ContinuityDigest:
    """Structured rebuild — memories/goals/concepts only. No provider call."""
    from sqlalchemy import select
    from wax.db.models import Principal, Memory, Goal, LearnerConceptState, Concept

    dig = ContinuityDigest(updated_at=datetime.now(timezone.utc).isoformat())
    p = await session.get(Principal, principal_id)
    if p is not None:
        dig.display_name = (getattr(p, "display_name", None) or "")[:120]
        pref = getattr(p, "preferences", None) or {}
        if isinstance(pref, dict):
            for k, v in list(pref.items())[:6]:
                dig.style_notes.append(f"{k}: {v}"[:180])
            if pref.get("relationship"):
                dig.relationship = str(pref.get("relationship"))[:400]
        if not dig.relationship:
            dig.relationship = "Returning learner; continue with warmth and precision."

    try:
        rows = list(
            (
                await session.execute(
                    select(Memory)
                    .where(
                        Memory.principal_id == principal_id,
                        Memory.is_active.is_(True),
                    )
                    .order_by(Memory.importance.desc(), Memory.updated_at.desc())
                    .limit(24)
                )
            )
            .scalars()
            .all()
        )
        for m in rows:
            content = (m.content or "").strip()
            if not content:
                continue
            mt = (m.memory_type or "").lower()
            if mt in ("preference", "relationship"):
                dig.preferences.append(content[:220])
            elif mt in ("goal",):
                dig.goals.append(content[:220])
            elif mt in ("learning", "semantic"):
                dig.focus.append(content[:220])
            else:
                dig.open_threads.append(content[:220])
    except Exception:
        logger.exception("continuity_digest_memories_failed")

    try:
        grows = list(
            (
                await session.execute(
                    select(Goal)
                    .where(Goal.principal_id == principal_id, Goal.status == "active")
                    .order_by(Goal.updated_at.desc())
                    .limit(6)
                )
            )
            .scalars()
            .all()
        )
        for g in grows:
            title = getattr(g, "title", None) or str(g.id)
            dig.goals.append(str(title)[:220])
    except Exception:
        logger.exception("continuity_digest_goals_failed")

    try:
        snap = list(
            (
                await session.execute(
                    select(LearnerConceptState, Concept)
                    .join(Concept, Concept.id == LearnerConceptState.concept_id)
                    .where(LearnerConceptState.principal_id == principal_id)
                    .limit(12)
                )
            ).all()
        )
        for state, concept in snap:
            label = getattr(concept, "label", None) or getattr(concept, "key", "")
            status = getattr(state, "status", "")
            mastery = getattr(state, "mastery", None)
            dig.mastery.append(f"{label}: {status}" + (f" (m={mastery})" if mastery is not None else ""))
    except Exception:
        logger.exception("continuity_digest_concepts_failed")

    # Dedup preserve order
    def _dedup(xs: list[str], n: int) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            k = x.strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(x.strip())
            if len(out) >= n:
                break
        return out

    dig.preferences = _dedup(dig.preferences, 8)
    dig.focus = _dedup(dig.focus, 8)
    dig.goals = _dedup(dig.goals, 6)
    dig.open_threads = _dedup(dig.open_threads, 6)
    dig.mastery = _dedup(dig.mastery, 8)
    dig.style_notes = _dedup(dig.style_notes, 4)
    return dig


async def save_digest(session, principal_id, digest: ContinuityDigest) -> None:
    from wax.db.models import Principal

    p = await session.get(Principal, principal_id)
    if p is None:
        return
    meta = dict(getattr(p, "metadata_", None) or {})
    payload = asdict(digest)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    meta[DIGEST_META_KEY] = payload
    p.metadata_ = meta
    await session.flush()


async def ensure_fresh_digest(
    session,
    principal_id,
    *,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> ContinuityDigest:
    existing = await load_digest(session, principal_id)
    if existing and existing.is_fresh(max_age_hours) and existing.is_rich():
        return existing
    dig = await rebuild_digest_from_state(session, principal_id)
    try:
        await save_digest(session, principal_id, dig)
    except Exception:
        logger.exception("continuity_digest_save_failed")
    return dig
