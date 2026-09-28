"""
WAX Tutor — the AI is the agent.

No tool registry. No ToolSpec menu. No primitive catalogue sent to the model.
The AI reasons, writes optional directive blocks, infrastructure executes them,
observations return, the AI continues until the objective is complete.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.agent.runtime import AgentRuntime
from wax.db.models import Delivery, Message, Work
from wax.delivery.presentation import InteractiveChoice, PresentableResponse
from wax.intelligence.directives import parse_agent_output, Directive
from wax.intelligence.providers import ChatMessage, CompletionRequest, get_intelligence
from wax.observability.logging import get_logger
from wax.memory import store as mem
from wax.scheduler import ops as sched
from wax.world import ops as world_ops
from wax.surfaces import ops as pub
from wax.interaction.ops import present_choices

logger = get_logger(__name__)

TUTOR_SYSTEM = """You are WAX — a persistent adaptive tutor by WAX Prep.

You are the tutor. Model vendors are infrastructure only — never claim to be those products.

You have a secure persistent World for this student (files, packages, terminal) and durable
state you control. Infrastructure enforces security and delivery; you decide.

How you work:
- Meet the student where they are. Discover needs through conversation.
- Recent conversation is provided for continuity. You decide what durable state or World
  content to inspect.
- When you need infrastructure to act, write a fenced block. Infrastructure validates
  security and returns observations. Ordinary chat needs no fences.

Infrastructure channels (not a menu of app features — domains of reality):

```world
# general execution environment — shell, files, packages inside the student's World
python3 -c "print(2+2)"
```

```state
action: search
query: preferred explanation style
```

```state
action: create
content: Student prefers short worked examples
```

```time
delay_seconds: 3600
reason: check-in on practice set
```

```publish
title: Practice sheet
<div>…html…</div>
```

```interact
prompt: Which path?
- More examples
- Try a problem
```

Aliases still accepted: memory→state, schedule→time, choices→interact.

After infrastructure observations, continue or finish with a clear reply to the student.
Privacy: if they ask to forget something, use state forget and confirm from the result.
"""


class TutorService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def handle_message(self, work: Work) -> dict[str, Any]:
        agent = AgentRuntime(self.session, work)
        await agent.start()

        principal_id = work.principal_id
        payload = work.input_payload or {}
        user_text = (payload.get("text") or payload.get("user_text") or "").strip()
        channel = payload.get("channel") or ""

        # Conversation continuity only — no preselected memory/World content.
        history = await self._recent_messages(work, limit=20)
        now = datetime.now(timezone.utc).isoformat()

        system = TUTOR_SYSTEM
        try:
            from wax.domain.preferences import get_preferences
            prefs = await get_preferences(self.session, principal_id) if principal_id else {}
            if prefs:
                # Explicit user settings, not semantic retrieval
                system += f"\n\nStudent preferences (explicit settings): {prefs}"
        except Exception:
            pass

        # Honest environment facts — not curated memories or a World file listing
        env_block = (
            f"CURRENT ENVIRONMENT\n"
            f"- time_utc: {now}\n"
            f"- channel: {channel or 'unknown'}\n"
            f"- principal_id: {principal_id or 'none'}\n"
            f"- world: available — persistent isolated workspace (files, packages, terminal). "
            f"Inspect or act only via a ```world directive when needed.\n"
            f"- durable_memory: available — you own search/get/create/update/supersede/forget. "
            f"No memories are preloaded; use a ```memory directive when you need them.\n"
            f"- schedule/publish/choices: available via matching directives when needed.\n"
        )

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="system", content=env_block),
        ]
        for h in history:
            messages.append(ChatMessage(role=h["role"], content=h["content"]))
        if user_text:
            messages.append(ChatMessage(role="user", content=user_text))
        elif not history:
            messages.append(ChatMessage(role="user", content="(student opened the conversation)"))

        intelligence = get_intelligence()
        observations: list[str] = []
        final_reply = ""
        interactive = None
        actions_log: list[dict[str, Any]] = []

        while agent.can_continue():
            req = CompletionRequest(messages=messages, temperature=0.7)
            try:
                response = await intelligence.complete(req)
            except Exception as e:
                logger.exception("tutor_completion_failed")
                await agent.fail(str(e))
                final_reply = "I hit a temporary issue thinking that through. Please try again in a moment."
                break

            content = (response.content or "").strip()
            turn = parse_agent_output(content)
            messages.append(ChatMessage(role="assistant", content=content))

            if turn.reply:
                final_reply = turn.reply

            if not turn.directives:
                await agent.record_continuation("reply", {"chars": len(final_reply)})
                break

            # Execute directives as infrastructure, not as model tool-calls
            obs_parts: list[str] = []
            for d in turn.directives:
                result = await self._execute_directive(d, principal_id=principal_id, work=work)
                actions_log.append({"kind": d.kind, "ok": result.get("ok"), "summary": str(result)[:300]})
                obs_parts.append(f"[{d.kind}] {self._format_obs(result)}")
                if getattr(d, "channel", d.kind) == "interact" and result.get("ok"):
                    interactive = result.get("interactive")

            observation = "OBSERVATIONS:\n" + "\n".join(obs_parts)
            observations.append(observation)
            messages.append(ChatMessage(role="user", content=observation))
            await agent.record_continuation("directive", {"n": len(turn.directives)})

            # If only choices / publish with a reply, can stop
            kinds = {getattr(d, "channel", d.kind) for d in turn.directives}
            if kinds <= {"interact", "publish"} and final_reply:
                break

        else:
            # safety ceiling hit
            if not final_reply:
                final_reply = "I need a moment longer on that — please send a short follow-up and I'll continue."

        await agent.complete({"reply_preview": final_reply[:400], "actions": len(actions_log)})

        # Deliver
        if principal_id and final_reply:
            await self._deliver(work, final_reply, interactive=interactive, channel=channel)

        return {
            "reply": final_reply,
            "actions": actions_log,
            "observations": observations,
            "interactive": interactive,
        }

    async def handle_scheduled_action(self, work: Work) -> dict[str, Any]:
        """Wake path: same agent, message_hint as the user-facing objective."""
        payload = work.input_payload or {}
        hint = payload.get("message_hint") or payload.get("reason") or "Scheduled follow-up."
        # Reuse handle_message shape
        work.input_payload = {**(payload), "user_text": f"[Scheduled] {hint}", "text": f"[Scheduled] {hint}"}
        return await self.handle_message(work)

    async def _execute_directive(
        self, d: Directive, *, principal_id, work: Work
    ) -> dict[str, Any]:
        ctx = {
            "principal_id": principal_id,
            "work_id": str(work.id) if work.id else None,
            "conversation_id": work.conversation_id,
        }
        try:
            # Infrastructure channels only — not an application capability menu
            ch = getattr(d, "channel", None) or d.kind
            if ch == "world":
                body = d.parsed.get("commands") or d.body
                return await world_ops.world_exec(
                    self.session,
                    {"command": body},
                    ctx,
                )
            if ch == "state":
                return await self._memory_directive(d, principal_id)
            if ch == "time":
                return await sched.schedule_action(self.session, d.parsed, ctx)
            if ch == "publish":
                return await pub.publish_surface(
                    self.session,
                    {
                        "title": d.parsed.get("title") or "WAX page",
                        "html": d.body,
                    },
                    ctx,
                )
            if ch == "interact":
                choices = d.parsed.get("choices") or []
                prompt = d.parsed.get("prompt") or d.body
                return await present_choices(
                    self.session,
                    {"choices": choices, "prompt": prompt},
                    ctx,
                )
            return {"ok": False, "error": f"unknown_channel:{ch}"}
        except Exception as e:
            logger.exception("directive_failed", kind=d.kind)
            return {"ok": False, "error": str(e)[:500]}

    async def _memory_directive(self, d: Directive, principal_id) -> dict[str, Any]:
        action = (d.parsed.get("action") or "create").lower()
        if action == "search":
            return await mem.memory_search(
                self.session,
                principal_id,
                query=d.parsed.get("query") or d.body,
                limit=20,
            )
        if action in ("get", "inspect"):
            return await mem.memory_get(
                self.session,
                principal_id,
                str(d.parsed.get("memory_id") or d.parsed.get("id") or ""),
            )
        if action == "create":
            return await mem.memory_create(
                self.session,
                principal_id,
                content=str(d.parsed.get("content") or d.body),
                memory_type=str(d.parsed.get("memory_type") or d.parsed.get("type") or "semantic"),
            )
        if action == "update":
            return await mem.memory_update(
                self.session,
                principal_id,
                str(d.parsed.get("memory_id") or d.parsed.get("id") or ""),
                content=d.parsed.get("content"),
            )
        if action == "supersede":
            return await mem.memory_supersede(
                self.session,
                principal_id,
                str(d.parsed.get("memory_id") or d.parsed.get("id") or ""),
                new_content=str(d.parsed.get("content") or d.body),
            )
        if action in ("forget", "delete"):
            return await mem.memory_forget(
                self.session,
                principal_id,
                str(d.parsed.get("memory_id") or d.parsed.get("id") or ""),
            )
        return {"ok": False, "error": f"unknown_memory_action:{action}"}

    def _format_obs(self, result: dict[str, Any]) -> str:
        if not result:
            return "empty"
        if result.get("ok") is False:
            return f"error: {result.get('error')}"
        # compact
        keys = [k for k in result if k not in ("ok",)][:8]
        parts = [f"{k}={result[k]!r}"[:120] for k in keys]
        return "; ".join(parts)[:800]

    async def _recent_messages(self, work: Work, limit: int = 20) -> list[dict[str, str]]:
        if not work.conversation_id:
            return []
        q = (
            select(Message)
            .where(Message.conversation_id == work.conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(q)).scalars().all()
        out = []
        for m in reversed(rows):
            role = (m.role or "").strip() or (
                "assistant" if (m.direction or "").lower() in ("outbound", "out") else "user"
            )
            if role not in ("user", "assistant", "system"):
                role = "assistant" if role in ("tutor", "bot") else "user"
            content = (m.content or "")[:4000]
            if content:
                out.append({"role": role, "content": content})
        return out



    async def _deliver(
        self,
        work: Work,
        reply: str,
        *,
        interactive=None,
        channel: str = "",
    ) -> None:
        try:
            from wax.delivery.senders import deliver
            from wax.domain.identity import primary_channel_target

            if not work.principal_id:
                return
            target = await primary_channel_target(self.session, work.principal_id)
            if not target:
                return
            ch, external_id = target
            await deliver(
                self.session,
                channel=ch,
                external_id=external_id,
                content=reply,
                principal_id=work.principal_id,
                work_id=work.id,
                interactive=interactive,
            )
        except Exception:
            logger.exception("tutor_deliver_failed")
