"""
Dynamic runtime system-state for Context Intelligence and the tutor.

Assembled from live configuration and registries — not a static marketing prompt.
WAX evolves; this description should track what is actually available now.
"""

from __future__ import annotations

from typing import Any


def build_runtime_system_state(
    *,
    channel: str = "",
    capability_families: list[str] | None = None,
    tools_available: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured capability snapshot for this process/turn."""
    from wax.config import get_settings

    s = get_settings()

    # Chat vs embedding providers (identity never equals model name)
    primary = (getattr(s, "primary_provider", None) or "none").strip()
    fallback = (getattr(s, "fallback_provider", None) or "none").strip()
    ci_prov = (getattr(s, "context_intelligence_provider", None) or "none").strip()
    emb_prov = (getattr(s, "embedding_provider", None) or "none").strip()
    emb_key = bool((getattr(s, "embedding_api_key", None) or "").strip())

    memory = {
        "durable_memories": True,
        "multi_layer_graph": True,
        "layers": [
            "working",
            "episodic",
            "semantic",
            "procedural",
            "goal",
            "relationship",
        ],
        "episodes_and_links": True,
        "continuity_digest": bool(getattr(s, "continuity_digest_enabled", True)),
        "embeddings": emb_key and emb_prov not in ("none", "", "null"),
        "embedding_provider_configured": emb_prov if emb_key else None,
        "learner_can_request_forget_via_tool": True,  # forget_memory tool exists
        "full_account_wipe_not_automatic": True,  # truthful privacy
    }

    channels = {
        "whatsapp": bool(getattr(s, "whatsapp_enabled", False)),
        "telegram": bool(getattr(s, "telegram_enabled", False)),
        "web_surfaces": True,
        "current_channel": channel or None,
    }

    orchestration = {
        "context_intelligence": bool(getattr(s, "context_intelligence_enabled", True)),
        "context_intelligence_mode": getattr(s, "context_intelligence_mode", None),
        "request_driven_tool_families": True,
        "capability_families_selected": list(capability_families or []),
    }

    actions = {
        "schedule_followups": True,
        "artifacts_and_surfaces": True,
        "workspace_and_code": bool(getattr(s, "allow_code_execution", True)),
        "research_fetch": bool(getattr(s, "allow_external_network", True)),
        "media_transcribe_describe": True,
        "assessments": True,
        "channel_linking": True,
    }

    restricted = []
    if not actions["workspace_and_code"]:
        restricted.append("code_execution")
    if not actions["research_fetch"]:
        restricted.append("external_network_research")
    if not memory["embeddings"]:
        restricted.append("vector_embeddings")

    tools = list(tools_available or [])
    state = {
        "product": {
            "company": "WAX Prep",
            "tutor_name": "WAX",
            "identity_rule": (
                "The tutor is WAX, created by WAX Prep. "
                "Underlying model/provider names are infrastructure only — never the product identity."
            ),
        },
        "providers": {
            "primary_chat": primary,
            "fallback_chat": fallback,
            "context_intelligence": ci_prov,
            "embeddings": emb_prov if emb_key else "none",
            "note": "Provider names must not be stated as who the tutor is.",
        },
        "memory": memory,
        "channels": channels,
        "orchestration": orchestration,
        "permitted_actions": actions,
        "restricted_or_unavailable": restricted,
        "tools_exposed_this_turn": tools[:40],
        "tool_count_exposed": len(tools),
    }
    if extra:
        state["extra"] = extra
    return state


def render_system_state_for_model(state: dict[str, Any] | None = None, **kwargs: Any) -> str:
    """Compact natural-language block for model system/user context."""
    st = state or build_runtime_system_state(**kwargs)
    prod = st.get("product") or {}
    mem = st.get("memory") or {}
    acts = st.get("permitted_actions") or {}
    ch = st.get("channels") or {}
    orch = st.get("orchestration") or {}
    restr = st.get("restricted_or_unavailable") or []
    tools = st.get("tools_exposed_this_turn") or []
    fams = (orch.get("capability_families_selected") or [])

    lines = [
        "CURRENT SYSTEM STATE (runtime — not a fixed product brochure):",
        f"- Identity: tutor={prod.get('tutor_name')}; creator={prod.get('company')}. "
        "Never introduce yourself as the model or provider vendor.",
        f"- Channels enabled: whatsapp={ch.get('whatsapp')}, telegram={ch.get('telegram')}, "
        f"surfaces={ch.get('web_surfaces')}; current={ch.get('current_channel') or 'unknown'}.",
        f"- Memory: durable={mem.get('durable_memories')}, graph={mem.get('multi_layer_graph')}, "
        f"digest={mem.get('continuity_digest')}, embeddings={mem.get('embeddings')}. "
        "Learner may request forgetting specific memories via tools when available; "
        "full data deletion is not an automatic chat promise.",
        f"- Orchestration: context_intelligence={orch.get('context_intelligence')}, "
        f"families_selected={fams or 'none_yet'}.",
        f"- Actions permitted: schedule={acts.get('schedule_followups')}, "
        f"artifacts/surfaces={acts.get('artifacts_and_surfaces')}, "
        f"code={acts.get('workspace_and_code')}, research={acts.get('research_fetch')}, "
        f"media={acts.get('media_transcribe_describe')}, assessments={acts.get('assessments')}.",
    ]
    if restr:
        lines.append(f"- Restricted/unavailable now: {', '.join(restr)}.")
    if tools:
        lines.append(
            f"- Tools exposed this turn ({len(tools)}): " + ", ".join(tools[:24])
            + ("…" if len(tools) > 24 else "")
        )
    else:
        lines.append("- Tools exposed this turn: none (text-only path is fine).")
    lines.append(
        "Use this state to decide what context and tools are relevant. "
        "Do not invent capabilities that are restricted. Do not predict the learner's future."
    )
    return "\n".join(lines)
