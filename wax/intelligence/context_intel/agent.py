"""
Context Intelligence investigation loop.

Model-guided when enabled and a provider is available; otherwise a bounded
capability probe that still produces a structured Context Brief.
Never writes durable learner state. Never answers the student.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from wax.config.settings import get_settings
from wax.intelligence.context_intel.brief import BriefItem, ContextBrief
from wax.intelligence.context_intel.capabilities import CAPABILITY_SPECS, ContextCapabilities
from wax.observability.logging import get_logger

logger = get_logger(__name__)

_ORCH_SYSTEM = """You are Waza Context Intelligence — an internal investigator, not the tutor.

Your only job: decide what the main tutor needs to know about THIS learner for THIS message.
You do not answer the learner. You do not invent biography. You do not write permanent memory.

Rules:
1. Use only the provided tools. Principal isolation is enforced by tools.
2. Prefer no context when personalization would not change the response (e.g. simple greeting, pure arithmetic).
3. Distinguish facts/evidence from inferences. Label inferences clearly.
4. Stop when more tools would not materially change what the tutor should do.
5. If critical information is missing, set insufficient_evidence rather than guessing.
6. Keep the brief small. Relevance over completeness.

When finished, respond with ONLY a JSON object (no markdown fences):
{
  "request_understanding": "short",
  "task_intent": "short",
  "no_context_required": false,
  "insufficient_evidence": false,
  "recommended_objective": "short",
  "items": [
    {"kind": "fact|evidence|inference|hypothesis", "text": "...", "source": "...", "confidence": "high|medium|low", "why": "..."}
  ],
  "uncertainties": [],
  "suggested_actions": []
}
"""


def _settings_flags() -> dict[str, Any]:
    s = get_settings()
    return {
        "enabled": bool(getattr(s, "context_intelligence_enabled", True)),
        "use_model": bool(getattr(s, "context_intelligence_use_model", True)),
        "max_tool_calls": int(getattr(s, "context_intelligence_max_tool_calls", 6) or 6),
        "max_tokens": int(getattr(s, "context_intelligence_max_tokens", 900) or 900),
        "temperature": float(getattr(s, "context_intelligence_temperature", 0.2) or 0.2),
        "provider": (getattr(s, "context_intelligence_provider", None) or "none"),
        "fallback_to_primary": bool(getattr(s, "context_intelligence_fallback_to_primary", False)),
    }


def _looks_minimal(text: str) -> bool:
    t = (text or "").strip().lower()
    if len(t) <= 2:
        return True
    greetings = {
        "hi", "hello", "hey", "yo", "sup", "good morning", "good evening",
        "good night", "thanks", "thank you", "ok", "okay", "k", "yes", "no",
        "yeah", "yep", "nah",
    }
    if t in greetings:
        return True
    if len(t) < 12 and not any(c.isdigit() for c in t):
        # short social noise without substance
        if all(w in greetings or len(w) <= 3 for w in t.replace("!", "").replace("?", "").split()):
            return True
    return False


def _parse_brief_json(raw: str) -> ContextBrief | None:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    items: list[BriefItem] = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "evidence")
        if kind not in ("fact", "evidence", "inference", "hypothesis"):
            kind = "evidence"
        conf = str(it.get("confidence") or "medium")
        if conf not in ("high", "medium", "low", "none"):
            conf = "medium"
        items.append(
            BriefItem(
                kind=kind,  # type: ignore[arg-type]
                text=str(it.get("text") or "")[:500],
                source=str(it.get("source") or "")[:80],
                confidence=conf,  # type: ignore[arg-type]
                why=str(it.get("why") or "")[:160],
            )
        )
    return ContextBrief(
        request_understanding=str(data.get("request_understanding") or "")[:400],
        task_intent=str(data.get("task_intent") or "")[:200],
        no_context_required=bool(data.get("no_context_required")),
        insufficient_evidence=bool(data.get("insufficient_evidence")),
        items=items[:30],
        recommended_objective=str(data.get("recommended_objective") or "")[:300],
        suggested_actions=[str(a)[:120] for a in (data.get("suggested_actions") or [])[:6]],
        uncertainties=[str(u)[:160] for u in (data.get("uncertainties") or [])[:8]],
    )


async def _deterministic_probe(
    caps: ContextCapabilities,
    *,
    user_text: str,
    max_calls: int,
) -> ContextBrief:
    """Bounded investigation without a model — uses existing services, not subject rules."""
    brief = ContextBrief()
    tools: list[str] = []
    if _looks_minimal(user_text):
        brief.no_context_required = True
        brief.request_understanding = "Brief social or minimal message"
        brief.task_intent = "acknowledge"
        brief.recommended_objective = "Respond naturally; avoid dumping learner history"
        brief.tools_used = tools
        return brief

    # Always allow evidence + light memory for non-minimal turns
    order = [
        ("search_evidence", {"purpose": "reply"}),
        ("search_memories", {"query": (user_text or "")[:200], "limit": 6}),
        ("inspect_recent_conversation", {"limit": 8}),
        ("inspect_learner_state", {}),
        ("inspect_preferences", {}),
        ("inspect_goals", {}),
        ("inspect_linked_channels", {}),
    ]
    for name, args in order[:max_calls]:
        result = await caps.execute(name, args)
        tools.append(name)
        if not result.get("ok"):
            continue
        source = str(result.get("source") or name)
        kind = str(result.get("kind") or "evidence")
        if kind not in ("fact", "evidence", "inference", "hypothesis"):
            kind = "evidence"
        if name == "search_evidence":
            for it in (result.get("items") or [])[:8]:
                brief.items.append(
                    BriefItem(
                        kind="evidence",
                        text=str(it.get("text") or "")[:400],
                        source=source,
                        confidence="medium",
                        why=str(it.get("relevance") or "evidence_planner"),
                    )
                )
            for h in result.get("hints") or []:
                brief.investigation_notes.append(str(h)[:160])
        elif name == "search_memories":
            for it in (result.get("items") or [])[:6]:
                brief.items.append(
                    BriefItem(
                        kind="evidence",
                        text=str(it.get("content") or "")[:400],
                        source="memory",
                        confidence="high" if float(it.get("confidence") or 0) >= 0.7 else "medium",
                        why="memory_search",
                    )
                )
        elif name == "inspect_recent_conversation":
            items = result.get("items") or []
            if items:
                brief.items.append(
                    BriefItem(
                        kind="fact",
                        text=f"{len(items)} recent turns available in current conversation",
                        source="conversation",
                        confidence="high",
                        why="continuity",
                    )
                )
        elif name == "inspect_learner_state":
            data = result.get("data")
            if data:
                brief.items.append(
                    BriefItem(
                        kind="fact",
                        text=str(data)[:400],
                        source="learner_state",
                        confidence="high",
                        why="state_snapshot",
                    )
                )
        elif name == "inspect_preferences":
            summary = result.get("summary")
            items = result.get("items") or []
            if summary:
                brief.items.append(
                    BriefItem(
                        kind="evidence",
                        text=str(summary)[:400],
                        source="preferences",
                        confidence="medium",
                        why="preference_summary",
                    )
                )
            for it in items[:4]:
                brief.items.append(
                    BriefItem(
                        kind="evidence",
                        text=str(it.get("content") or "")[:300],
                        source="preferences",
                        confidence="medium",
                    )
                )
        elif name == "inspect_goals":
            for it in (result.get("items") or [])[:4]:
                brief.items.append(
                    BriefItem(
                        kind="fact",
                        text=str(it.get("title") or it),
                        source="goals",
                        confidence="high",
                    )
                )
        elif name == "inspect_linked_channels":
            data = result.get("data") or {}
            brief.items.append(
                BriefItem(
                    kind="fact",
                    text=(
                        f"channels linked={data.get('linked_channels')}; "
                        f"current={data.get('current_channel')}"
                    ),
                    source="identity",
                    confidence="high",
                )
            )

    brief.request_understanding = (user_text or "")[:200]
    brief.task_intent = "reply"
    brief.recommended_objective = "Answer helpfully using only grounded items above"
    if not brief.items:
        brief.no_context_required = True
    brief.tools_used = tools
    return brief


async def _model_investigate(
    caps: ContextCapabilities,
    *,
    user_text: str,
    channel: str,
    max_tool_calls: int,
    max_tokens: int,
    temperature: float,
) -> ContextBrief:
    from wax.intelligence.providers import (
        ChatMessage,
        CompletionRequest,
        ToolSpec,
        get_intelligence,
    )

    tools = [
        ToolSpec(name=s["name"], description=s["description"], parameters=s.get("parameters") or {})
        for s in CAPABILITY_SPECS
    ]
    messages = [
        ChatMessage(role="system", content=_ORCH_SYSTEM),
        ChatMessage(
            role="user",
            content=(
                f"Channel: {channel or 'unknown'}\n"
                f"Learner message:\n{(user_text or '')[:2000]}\n\n"
                "Investigate only if useful, then return the JSON brief."
            ),
        ),
    ]
    intel = get_intelligence()
    tools_used: list[str] = []
    calls = 0

    while calls <= max_tool_calls:
        resp = await intel.complete(
            CompletionRequest(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                metadata={"role": "context_intelligence"},
            ),
            role="context",
            allow_fallback=False,
        )
        if resp.tool_calls:
            # append assistant tool_calls message
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=resp.content or "",
                    tool_calls=resp.tool_calls,
                )
            )
            for tc in resp.tool_calls:
                calls += 1
                if calls > max_tool_calls:
                    break
                name = tc.get("function", {}).get("name") or tc.get("name") or ""
                raw_args = tc.get("function", {}).get("arguments") or tc.get("arguments") or {}
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args.strip() else {}
                    except Exception:
                        args = {}
                else:
                    args = dict(raw_args)
                tools_used.append(name)
                result = await caps.execute(name, args)
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(result, default=str)[:4000],
                        tool_call_id=str(tc.get("id") or name),
                        name=name,
                    )
                )
            continue

        # final text
        brief = _parse_brief_json(resp.content or "")
        if brief is None:
            brief = ContextBrief(
                degraded=True,
                degradation_reason="unparseable_model_brief",
                request_understanding=(user_text or "")[:200],
            )
            # fall back to deterministic merge
            det = await _deterministic_probe(caps, user_text=user_text, max_calls=min(4, max_tool_calls))
            brief.items = det.items
            brief.no_context_required = det.no_context_required
            brief.tools_used = tools_used + det.tools_used
            return brief
        brief.tools_used = tools_used
        return brief

    # budget exhausted — deterministic completion
    brief = await _deterministic_probe(caps, user_text=user_text, max_calls=3)
    brief.degraded = True
    brief.degradation_reason = "tool_budget_exhausted"
    brief.tools_used = tools_used + brief.tools_used
    return brief


async def investigate_context(
    session,
    *,
    principal_id: UUID | None,
    conversation_id: UUID | None,
    channel: str,
    user_text: str,
) -> ContextBrief:
    """
    Entry point for Context Intelligence.
    Returns a structured brief; never raises into the student path.
    """
    flags = _settings_flags()
    if not flags["enabled"]:
        return ContextBrief(
            degraded=True,
            degradation_reason="context_intelligence_disabled",
            no_context_required=False,
        )
    if not principal_id:
        return ContextBrief(
            no_context_required=True,
            request_understanding="No authenticated principal",
            task_intent="reply",
        )

    caps = ContextCapabilities(
        session,
        principal_id=principal_id,
        conversation_id=conversation_id,
        channel=channel,
        user_text=user_text,
    )

    try:
        # Only call the CI model when a context provider is configured (or explicit primary fallback)
        has_ci_model = False
        try:
            from wax.intelligence.providers import get_intelligence
            has_ci_model = get_intelligence().context_model is not None
        except Exception:
            has_ci_model = False
        if flags["use_model"] and has_ci_model:
            try:
                return await _model_investigate(
                    caps,
                    user_text=user_text,
                    channel=channel,
                    max_tool_calls=flags["max_tool_calls"],
                    max_tokens=flags["max_tokens"],
                    temperature=flags["temperature"],
                )
            except Exception:
                logger.exception("context_intelligence_model_failed")
                brief = await _deterministic_probe(
                    caps, user_text=user_text, max_calls=flags["max_tool_calls"]
                )
                brief.degraded = True
                brief.degradation_reason = "model_failed_fallback_probe"
                return brief
        return await _deterministic_probe(
            caps, user_text=user_text, max_calls=flags["max_tool_calls"]
        )
    except Exception:
        logger.exception("context_intelligence_failed")
        return ContextBrief(
            degraded=True,
            degradation_reason="investigation_exception",
            insufficient_evidence=True,
        )
