"""
Runtime system-state snapshot for the tutor.

Describes what infrastructure actually provides — not a product brochure,
not Context Intelligence, not a capability menu the AI must follow.
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

    primary = (getattr(s, "primary_provider", None) or "none").strip()
    emb_prov = (getattr(s, "embedding_provider", None) or "none").strip()
    emb_key = bool((getattr(s, "embedding_api_key", None) or "").strip())

    memory = {
        "durable_memories": True,
        "ai_owned_lifecycle": True,  # search / create / update / supersede / forget
        "embeddings": emb_key and emb_prov not in ("none", "", "null"),
        "embedding_provider_configured": emb_prov if emb_key else None,
        "learner_can_request_forget": True,
        "full_account_wipe_not_automatic": True,
    }

    channels = {
        "whatsapp": bool(getattr(s, "whatsapp_enabled", False)),
        "telegram": bool(getattr(s, "telegram_enabled", False)),
        "web_surfaces": True,
        "current_channel": channel or None,
    }

    actions = {
        "schedule_followups": True,
        "artifacts_and_surfaces": True,
        "workspace_and_code": bool(getattr(s, "allow_code_execution", True)),
        "external_network": bool(getattr(s, "allow_external_network", True)),
    }

    state: dict[str, Any] = {
        "product": {
            "tutor_name": "WAX",
            "company": "WAX Prep",
            "primary_provider": primary,
        },
        "memory": memory,
        "channels": channels,
        "permitted_actions": actions,
        "primitives_available": list(tools_available or []),
        "restricted_or_unavailable": [],
    }
    if extra:
        state["extra"] = extra
    return state


def render_system_state_for_model(state: dict[str, Any] | None = None, **kwargs: Any) -> str:
    """Compact natural-language block for model context when useful."""
    st = state or build_runtime_system_state(**kwargs)
    prod = st.get("product") or {}
    mem = st.get("memory") or {}
    acts = st.get("permitted_actions") or {}
    ch = st.get("channels") or {}
    tools = st.get("primitives_available") or []

    lines = [
        "CURRENT SYSTEM STATE (runtime):",
        f"- Identity: tutor={prod.get('tutor_name')}; creator={prod.get('company')}. "
        "Never introduce yourself as the model vendor.",
        f"- Channels: whatsapp={ch.get('whatsapp')}, telegram={ch.get('telegram')}, "
        f"surfaces={ch.get('web_surfaces')}; current={ch.get('current_channel') or 'unknown'}.",
        f"- Memory: durable={mem.get('durable_memories')}, AI owns lifecycle "
        f"(search/create/update/supersede/forget). Embeddings={mem.get('embeddings')}.",
        f"- Actions: schedule={acts.get('schedule_followups')}, "
        f"surfaces={acts.get('artifacts_and_surfaces')}, "
        f"code={acts.get('workspace_and_code')}, network={acts.get('external_network')}.",
    ]
    if tools:
        lines.append(f"- Primitives available this turn ({len(tools)}): " + ", ".join(tools[:24]))
    lines.append(
        "You decide what to retrieve, remember, run, or schedule. "
        "Infrastructure enforces security and delivery."
    )
    return "\n".join(lines)
