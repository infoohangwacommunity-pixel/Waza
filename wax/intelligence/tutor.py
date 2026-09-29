"""
Tutor — one small agent loop around the model.

1. Student objective + recent conversation
2. Ask the AI what to do
3. Execute infrastructure directives if any
4. Return observations
5. Repeat until the AI finishes (or safety bounds)
6. Deliver the reply

The model owns teaching decisions; this module only runs the loop.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wax.agent.runtime import AgentRuntime
from wax.db.models import Message, Work
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

Infrastructure channels (domains of reality, not an app menu):

```world
python3 -c "print(2+2)"
```

```world
network_mode: pkg
python3 -m pip install numpy
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
delay_seconds: 30
reason: short pause then continue
```

```time
action: list
```

```publish
title: Practice sheet
lifetime_hours: 168
<div>…AI-authored HTML/CSS/JS…</div>
```

```interact
prompt: Which path?
- More examples
- Try a problem
```

After observations, continue or finish with a clear reply to the student.
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

        # Inbound file fact only (path, size, type)
        media_path = payload.get("local_media_path") or payload.get("principal_media_path")
        if media_path:
            mime = payload.get("media_mime") or ""
            size = payload.get("media_size")
            note = f"[Inbound file in your World at {media_path}"
            if mime:
                note += f", type hint {mime}"
            if size:
                note += f", size {size} bytes"
            note += ". Inspect via a world directive only if needed.]"
            user_text = f"{user_text}\n\n{note}".strip() if user_text else note

        history = await self._recent_messages(work, limit=20)
        system = TUTOR_SYSTEM
        try:
            from wax.domain.preferences import get_preferences

            prefs = await get_preferences(self.session, principal_id) if principal_id else {}
            if prefs:
                system += f"\n\nStudent preferences (explicit settings): {prefs}"
        except Exception:
            pass

        env = (
            f"CURRENT ENVIRONMENT\n"
            f"- time_utc: {datetime.now(timezone.utc).isoformat()}\n"
            f"- channel: {channel or 'unknown'}\n"
            f"- principal_id: {principal_id or 'none'}\n"
            f"- world / state / time / publish / interact: available via fenced directives when needed.\n"
            f"- Nothing is preloaded; inspect only when needed.\n"
        )

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="system", content=env),
        ]
        for h in history:
            messages.append(ChatMessage(role=h["role"], content=h["content"]))
        if user_text:
            messages.append(ChatMessage(role="user", content=user_text))
        elif not history:
            messages.append(ChatMessage(role="user", content="(student opened the conversation)"))

        intelligence = get_intelligence()
        final_reply = ""
        interactive = None

        while agent.can_continue():
            try:
                response = await intelligence.complete(
                    CompletionRequest(messages=messages, temperature=0.7)
                )
            except Exception as e:
                logger.exception("tutor_completion_failed")
                await agent.fail(str(e))
                final_reply = (
                    "I hit a temporary issue thinking that through. "
                    "Please try again in a moment."
                )
                break

            content = (response.content or "").strip()
            turn = parse_agent_output(content)
            messages.append(ChatMessage(role="assistant", content=content))
            if turn.reply:
                final_reply = turn.reply

            if not turn.directives:
                await agent.tick("reply")
                break

            obs_parts: list[str] = []
            for d in turn.directives:
                result = await self._execute(d, principal_id=principal_id, work=work)
                obs_parts.append(f"[{d.channel}] {self._fmt(result)}")
                if d.channel == "interact" and result.get("ok"):
                    interactive = result.get("interactive")

            messages.append(ChatMessage(role="user", content="OBSERVATIONS:\n" + "\n".join(obs_parts)))
            await agent.tick("directive")

            if {d.channel for d in turn.directives} <= {"interact", "publish"} and final_reply:
                break
        else:
            if not final_reply:
                final_reply = (
                    "I need a moment longer on that — please send a short follow-up "
                    "and I'll continue."
                )

        await agent.complete(final_reply[:400])
        if principal_id and final_reply:
            await self._deliver(work, final_reply, interactive=interactive)
        return {"reply": final_reply, "interactive": interactive}

    async def handle_scheduled_action(self, work: Work) -> dict[str, Any]:
        payload = work.input_payload or {}
        hint = payload.get("message_hint") or payload.get("reason") or "Scheduled follow-up."
        work.input_payload = {
            **payload,
            "user_text": f"[Scheduled] {hint}",
            "text": f"[Scheduled] {hint}",
        }
        return await self.handle_message(work)

    async def handle_surface_request(self, work: Work) -> dict[str, Any]:
        """Same intelligence path as messaging — surface channel is only context."""
        payload = work.input_payload or {}
        work.input_payload = {**payload, "channel": payload.get("channel") or "surface"}
        return await self.handle_message(work)

    async def _execute(self, d: Directive, *, principal_id, work: Work) -> dict[str, Any]:
        payload = work.input_payload or {}
        ctx = {
            "principal_id": principal_id,
            "work_id": str(work.id) if work.id else None,
            "conversation_id": work.conversation_id or payload.get("conversation_id"),
            "channel": payload.get("channel"),
            "target_external_id": payload.get("target_external_id") or payload.get("external_id"),
        }
        f = d.fields
        try:
            if d.channel == "world":
                return await world_ops.world_exec(
                    self.session,
                    {
                        "command": f.get("command") or d.body,
                        "network_mode": f.get("network_mode") or "none",
                    },
                    ctx,
                )
            if d.channel == "state":
                return await self._state(d, principal_id)
            if d.channel == "time":
                return await sched.handle_time_directive(
                    self.session, {**f, "raw": d.body}, ctx
                )
            if d.channel == "publish":
                return await pub.handle_publish_directive(
                    self.session,
                    {
                        **f,
                        "html": f.get("html") or d.body,
                        "title": f.get("title") or "Surface",
                    },
                    ctx,
                )
            if d.channel == "interact":
                return await present_choices(
                    self.session,
                    {
                        "choices": f.get("choices") or [],
                        "prompt": f.get("prompt") or d.body,
                    },
                    ctx,
                )
            return {"ok": False, "error": f"unknown_channel:{d.channel}"}
        except Exception as e:
            logger.exception("directive_failed", channel=d.channel)
            return {"ok": False, "error": str(e)[:500]}

    async def _state(self, d: Directive, principal_id) -> dict[str, Any]:
        f = d.fields
        action = (f.get("action") or "create").lower()
        mid = str(f.get("memory_id") or f.get("id") or "")
        if action == "search":
            return await mem.memory_search(
                self.session, principal_id, query=f.get("query") or d.body, limit=20
            )
        if action in ("get", "inspect"):
            return await mem.memory_get(self.session, principal_id, mid)
        if action == "create":
            return await mem.memory_create(
                self.session,
                principal_id,
                content=str(f.get("content") or d.body),
                memory_type=str(f.get("memory_type") or f.get("type") or "semantic"),
            )
        if action == "update":
            return await mem.memory_update(
                self.session, principal_id, mid, content=f.get("content")
            )
        if action == "supersede":
            return await mem.memory_supersede(
                self.session,
                principal_id,
                mid,
                new_content=str(f.get("content") or d.body),
            )
        if action in ("forget", "delete"):
            return await mem.memory_forget(self.session, principal_id, mid)
        return {"ok": False, "error": f"unknown_state_action:{action}"}

    # Infrastructure safety cap only — a transport limit on how much observation
    # text may re-enter the model context. It is NOT summarization or selective
    # extraction; complete results are preserved wherever practical.
    OBSERVATION_FIELD_CAP = 8000
    OBSERVATION_TOTAL_CAP = 32000

    @classmethod
    def _fmt(cls, result: dict[str, Any]) -> str:
        if not result:
            return "empty"
        if result.get("ok") is False:
            return f"error: {result.get('error')}"[: cls.OBSERVATION_TOTAL_CAP]
        parts = [f"{k}={result[k]!r}" for k in result if k != "ok"]
        text = "\n".join(p[: cls.OBSERVATION_FIELD_CAP] for p in parts)
        return text[: cls.OBSERVATION_TOTAL_CAP]

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
        out: list[dict[str, str]] = []
        for m in reversed(rows):
            role = (m.role or "").strip() or (
                "assistant"
                if (m.direction or "").lower() in ("outbound", "out")
                else "user"
            )
            if role not in ("user", "assistant", "system"):
                role = "assistant" if role in ("tutor", "bot") else "user"
            content = (m.content or "")[:4000]
            if content:
                out.append({"role": role, "content": content})
        return out

    async def _record_outbound_message(self, work: Work, reply: str) -> None:
        """Persist the assistant's reply into durable chat history.

        Infrastructure records exactly what was said; the AI owns meaning.
        Idempotent per Work (unique (channel, external_id='assistant:{work_id}')),
        so retries of the same turn never duplicate transcript rows.
        """
        if not work.conversation_id or not work.principal_id or not work.id:
            return
        ext = f"assistant:{work.id}"
        channel = (work.input_payload or {}).get("channel") or "whatsapp"
        exists = await self.session.execute(
            select(Message.id).where(
                Message.channel == channel, Message.external_id == ext
            )
        )
        if exists.scalar_one_or_none():
            return
        try:
            async with self.session.begin_nested():
                message = Message(
                    id=uuid.uuid4(),
                    conversation_id=work.conversation_id,
                    principal_id=work.principal_id,
                    channel=channel,
                    direction="outbound",
                    role="assistant",
                    content=reply,
                    external_id=ext,
                    work_id=work.id,
                    metadata_={"source": "tutor_reply"},
                )
                self.session.add(message)
                await self.session.flush()
            # Durable long-term archive inside the student's own World.
            from wax.world.transcript import archive_message

            await archive_message(self.session, message)
        except IntegrityError:
            # Another attempt already recorded this turn — the savepoint
            # rollback discarded only this insert; never duplicate transcript rows.
            logger.info("outbound_message_already_recorded", work_id=str(work.id))

    async def _deliver(self, work: Work, reply: str, *, interactive=None) -> None:
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
                channel=ch,
                target=external_id,
                text=reply,
                interactive=interactive,
            )
        except Exception:
            logger.exception("tutor_deliver_failed")
        finally:
            # The DB must hold the assistant response regardless of whether
            # external delivery succeeded or failed.
            try:
                await self._record_outbound_message(work, reply)
            except Exception:
                logger.exception("outbound_message_record_failed")
