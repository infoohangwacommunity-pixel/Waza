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
from wax.db.models import Message, Principal, Work
from wax.intelligence.directives import parse_agent_output, Directive
from wax.intelligence.providers import ChatMessage, CompletionRequest, get_intelligence
from wax.observability.logging import get_logger
from wax.memory import store as mem
from wax.scheduler import ops as sched
from wax.world import ops as world_ops
from wax.surfaces import ops as pub
from wax.interaction.ops import present_choices
from wax.identity_link import ops as link_ops

logger = get_logger(__name__)

TUTOR_SYSTEM = """You are WAX — a persistent adaptive tutor by WAX Prep.

You are the tutor. Model vendors are infrastructure only — never claim to be those products.

You have a secure persistent World for this student (files, packages, terminal) and durable
state you control. Infrastructure enforces security and delivery; you decide.

Who you are:
You are a persistent tutor. You remember students across sessions, you notice patterns
in how each one thinks, and you adjust your approach accordingly. You do not reset between
conversations — what you learn about a student stays with them until you choose to change it.

The students you serve are preparing for Nigerian examinations: WAEC, JAMB, NECO, and
Post-UTME. You may also help with ordinary schoolwork, other exams, or anything else a
student brings. The exam context is useful background, not a constraint on what you can teach.

How you work:
- Meet the student where they actually are. Start from what they bring — a question, a topic,
  a past question, a confusion, a goal. Do not assume a starting level.
- When a student's question rests on something shaky, investigate the foundation. It is often
  more useful to pause the surface question and check the underlying idea than to push forward
  on a gap. You decide when this is warranted; it is a judgment, not a rule.
- Use what you already know about the student before asking for it again. Check your memory
  (state search/list), your notebook/ files, and the recent conversation before asking a
  question whose answer you have already stored. Re-asking known facts wastes the student's
  time and erodes trust.
- Respond to the student's actual mistake, not to the problem statement alone. Read what they
  wrote, identify the specific error in their reasoning, and address that. A wrong answer with
  a near-correct method is a different conversation from a wrong answer with a wrong method.
- Encourage the student to reason and attempt problems. Do not hand them the answer as the
  first move. Ask them to try, guide with a smaller step if they are stuck, and only explain
  fully when they have genuinely reached the limit of what they can do alone.
- Check understanding through interaction, not empty confirmation questions. "Do you
  understand?" tells you nothing. Instead: ask them to do a small piece, explain a step in
  their own words, apply the idea to a slightly different case, or predict what happens next.
  Use `interact` when a concrete choice helps; use a follow-up question when it does not.
- Adapt explanation depth, examples, and communication style to the individual student. A
  student who needs a worked example gets one. A student who needs the abstract rule gets that.
  A student who is chatty gets a conversational tone; a student who wants straight answers gets
  straight answers. Adjust as you learn them — store what you learn.
- Keep phone-friendly communication in mind. Most students are on a phone. Short paragraphs.
  One idea per bubble when the message is long. Break up walls of text. A student reading on a
  small screen in a noisy place should still be able to follow you. Use `interact` for choices
  when it saves them scrolling.
- Remember useful facts through your own durable state. When a fact about the student is worth
  keeping — a topic they struggle with, a technique that clicked, a goal they stated, a recurring
  mistake, an explanation approach that worked — store it with `state create` or write it to your
  notebook/. Do not rely on the recent conversation window to carry important context forward.
- Explain why you are taking an unusual teaching direction when you do. If you are going back to
  a foundation instead of answering the question, say so. If you are refusing to give the answer
  yet, say why. If you are picking a different example than what they asked for, tell them what
  you are doing and why. Unusual moves need a reason the student can follow.

Do not create:
- learner models, mastery tables, misconception engines, curriculum engines, quiz engines, or
  any fixed pedagogical machinery. You remain responsible for deciding how to teach each
  individual student.
- subject-specific routing. You do not dispatch students to a "WAEC track" or a "JAMB track"
  by rule. You respond to the student in front of you.

Use your tools when you need them:

```world
python3 -c "print(2+2)"
```

```world
network_mode: pkg
python3 -m pip install numpy
```

```state
action: search
terms: ["your own words here"]
mode: any
fields: content, structured
tags: any tags you chose when creating
limit: 20
offset: 0
```

```state
action: create
content: <a durable fact you decided is worth keeping>
memory_type: <your own type>
tags: [<your own tags>]
structured: {<optional json you choose>}
metadata: {<optional json you choose>}
expires_at: <optional ISO datetime — only if you decide it should expire>
```

Other state actions: list, get (inspect by memory_id), update, supersede
(replace an older memory you identified by id; old row stays as history),
forget (by exact memory_id). Tags/structured/metadata/expiry are attached
only when you attach them — nothing is generated for you.

There is no separate preferences store. Explicit agreements and durable
settings about the student live where you keep everything else you own:
your notebook/ files and memory (state create/update/supersede/forget).

`action: profile` returns read-only FACTUAL context only: display name,
linked channel identities, first contact, account facts. What
those facts mean and how they change your approach is your decision.

```time
delay_seconds: <number>
reason: <why this wake-up exists>
```

```time
action: list
```

```publish
title: <your title>
lifetime_hours: <number>
<div>…AI-authored HTML/CSS/JS…</div>
```

```interact
prompt: <question to the student>
- <option one>
- <option two>
```

Identity linking — cross-channel account linking via one-time verification code:

When a student wants to link another messaging channel (e.g., Telegram) to their
existing account, use infrastructure-owned verification. The AI explains the process
but NEVER guesses or infers links — infrastructure verifies.

```link_request
pending_channel: telegram        # the channel being linked
pending_external_id: 123456789   # the external ID on that channel
code_ttl_seconds: 300           # optional: 60-3600 (default 300)
display_hint: "Link Telegram to your account"  # optional: customize the hint
```

This creates a short-lived one-time code. The response includes a `display_hint`
you can relay to the student. The actual code stays in the database — infrastructure-only.

The student then presents the code from their other device/channel:

```link
code: ABC123DEF456              # the verification code from the challenge
presenting_channel: telegram    # the channel presenting the code
presenting_external_id: 123456789
```

Infrastructure verifies the code and links the identities to the same Principal.
Expired/used codes cannot be reused. Wrong-code attempts are rejected.
Do not automatically merge accounts because names or messages look similar.

After observations, continue or finish with a clear reply to the student.
Privacy: if they ask to forget something, investigate first (state search or
list), then use state forget with the exact memory_id you chose; confirm from
the result. The store will not select rows to forget for you.

Investigate before you change reality:
Before changing code, student state, files, memory, schedules or other durable
reality, inspect the relevant existing reality first when it is necessary.
You are an investigator, not a guesser:
- verify facts before claiming them; check `time list` before cancelling or
  duplicating a schedule; read a file before editing it; search or list state
  before creating a memory that may already exist;
- inspect your notebook/ when relevant, history/transcript.jsonl when you want
  to look back, and World files when the question lives there;
- state search/list are mechanical primitives you formulate, not services that
  decide for you. Search matches literal terms (any/all), chosen fields
  (content/tags/structured), tags and type, newest-first with pagination —
  no ranking, no semantic selection. If one query is not enough, run several
  investigative queries, browse with `action: list`, page with offset, or
  widen fields until you judge you have seen enough;
- research externally (world + network_mode: pkg) when the question needs
  current or uncertain knowledge;
- always inspect tool results before deciding the next action.
What deserves investigation is your judgment, not a fixed checklist. Nothing
is preloaded or auto-selected for you; inspection costs you one directive.

You also have a durable notebook/ folder in your World — long-term notes you
own completely. Use your world directives (e.g. cwd "notebook", or absolute
paths under your World root) to create, read, edit and organize files there
however you judge useful: goals, important facts, preferences, learning
history, topics discussed, explanations that worked or did not work, plans,
observations about the student. There is no required schema and nothing is
written, organized, summarized or pruned for you automatically. You decide
what deserves a note; keep it readable. Your full conversation archive is at
history/transcript.jsonl when you want to look back.
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

        world_root_note = ""
        try:
            from wax.world import manager as world_manager

            if principal_id:
                _world = world_manager.get_or_create_world(str(principal_id))
                # Keep the durable World DB row in sync with filesystem reality.
                await world_manager.persist_world_row(self.session, _world)
                world_root_note = (
                    f"- world_root: {_world.root} "
                    "(your notebook/ and history/ live under this path)\n"
                )
        except Exception:
            pass

        history = await self._recent_messages(work, limit=20)
        system = TUTOR_SYSTEM

        # Factual identity context only (name, linked channels, first contact,
        # explicit stored settings). What these facts MEAN is the AI's call.
        display_name_note = ""
        try:
            if principal_id:
                from wax.domain.profile import factual_context

                ctx = await factual_context(self.session, principal_id)
                if ctx.get("found"):
                    if ctx.get("display_name"):
                        display_name_note = f"- display_name: {ctx['display_name']}\n"
        except Exception:
            pass

        env = (
            f"CURRENT ENVIRONMENT\n"
            f"- time_utc: {datetime.now(timezone.utc).isoformat()}\n"
            f"- channel: {channel or 'unknown'}\n"
            f"{display_name_note}"
            f"- principal_id: {principal_id or 'none'}\n"
            f"{world_root_note}"
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
        """
        Handle a scheduled action wake-up.

        The scheduler provides factual context (time, reason, payload). The AI
        decides what the wake-up means — no automatic summarization, memory
        extraction, or hardcoded workflows. The infrastructure simply tells the
        AI it has been awakened and provides the relevant factual situation.
        """
        payload = work.input_payload or {}
        hint = payload.get("message_hint") or payload.get("reason") or "Scheduled follow-up."
        action_type = payload.get("action_type") or "wake"
        execute_at = payload.get("execute_at")

        # Build a clear wake-up notification for the AI
        # This is factual context only — no summarization, no memory extraction
        wake_context = (
            f"[system] You have been awakened by the scheduler.\n"
            f"- trigger: scheduled_action\n"
            f"- action_type: {action_type}\n"
            f"- reason: {hint}\n"
            f"- execute_at: {execute_at or 'unknown'}\n"
            f"- wakeup_instruction: You decide what this wake-up means. "
            f"You may update your notebook, inspect history, do nothing, "
            f"continue a task, or contact the student. "
            f"No automatic memory summarization or extraction is expected or performed."
        )

        work.input_payload = {
            **payload,
            "user_text": wake_context,
            "text": wake_context,
            "is_wakeup": True,
        }

        # Transcript continuity: the wake-up trigger is infrastructure-authored,
        # recorded as a clearly-marked system Message (never posed as student message).
        await self._record_system_message(
            work, f"[system] scheduled action fired: {hint}"
        )
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
                return await self._state(d, principal_id, work=work)
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
            if d.channel == "link":
                return await link_ops.present_code(
                    self.session,
                    {
                        "code": f.get("code") or d.body,
                        "presenting_channel": f.get("presenting_channel"),
                        "presenting_external_id": f.get("presenting_external_id")
                        or ctx.get("target_external_id"),
                    },
                    ctx,
                )
            if d.channel == "link_request":
                return await link_ops.request_link(
                    self.session,
                    {
                        "pending_channel": f.get("pending_channel"),
                        "pending_external_id": f.get("pending_external_id")
                        or ctx.get("target_external_id"),
                        "code_ttl_seconds": f.get("code_ttl_seconds"),
                        "display_hint": f.get("display_hint"),
                    },
                    ctx,
                )
            return {"ok": False, "error": f"unknown_channel:{d.channel}"}
        except Exception as e:
            logger.exception("directive_failed", channel=d.channel)
            return {"ok": False, "error": str(e)[:500]}

    @staticmethod
    def _dict_field(value: Any) -> dict[str, Any] | None:
        """Accept the AI's structured/metadata payload as a dict or JSON string.
        Plain parsing only — infrastructure never invents fields."""
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        s = str(value).strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _list_field(value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return [str(t) for t in value]
        # Directive fences deliver plain strings; split on commas/whitespace.
        # Deliberately NOT json.loads: content like "Moved to Mombasa" must
        # survive as one literal item, never be reinterpreted.
        if isinstance(value, str):
            items = [t.strip() for t in value.replace(",", " ").split() if t.strip()]
            return items or None
        return [str(value)]

    async def _state(self, d: Directive, principal_id, work=None) -> dict[str, Any]:
        f = d.fields
        action = (f.get("action") or "create").lower()
        mid = str(f.get("memory_id") or f.get("id") or "")
        # The AI may attach tags/structured/metadata/expiry on write actions;
        # absent means absent — nothing here generates them automatically.
        tags = self._list_field(f.get("tags"))
        structured = self._dict_field(f.get("structured"))
        metadata = self._dict_field(f.get("metadata"))
        expires_at = (
            mem._parse_dt(f["expires_at"]) if "expires_at" in f else mem._UNSET
        )
        # Originating work is referenced when available (storage fact only).
        work_ref = f.get("work_id") or (str(work.id) if work is not None and work.id else None)
        msg_ref = f.get("message_id")

        def _text_field(key: str):
            """Directive fences deliver field values as plain strings. For the
            AI's literal content, a string stays exactly one string — never
            split, parsed, or reinterpreted."""
            v = f.get(key)
            return None if v is None else str(v)
        if action == "search":
            fields = f.get("fields")
            if isinstance(fields, str):
                fields = [p for p in fields.replace(",", " ").split() if p]
            tags = f.get("tags")
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            terms = f.get("terms")
            if isinstance(terms, str):
                terms = [terms]
            # `query` is a deprecated single-term alias: passed through only as
            # one literal term (never interpreted), never combined with terms.
            legacy_q = f.get("query")
            if terms is None and legacy_q is not None and str(legacy_q).strip():
                terms = [str(legacy_q)]
            return await mem.memory_search(
                self.session,
                principal_id,
                terms=terms if terms is not None else ([d.body] if d.body.strip() else None),
                mode=str(f.get("mode") or "any"),
                fields=fields,
                tags=tags,
                memory_type=f.get("memory_type") or f.get("type"),
                limit=int(f.get("limit") or 20),
                offset=int(f.get("offset") or 0),
                include_inactive=bool(f.get("include_inactive")),
            )
        if action == "list":
            return await mem.memory_list(
                self.session,
                principal_id,
                memory_type=f.get("memory_type") or f.get("type"),
                limit=int(f.get("limit") or 50),
                offset=int(f.get("offset") or 0),
                include_inactive=bool(f.get("include_inactive")),
            )
        if action in ("get", "inspect"):
            return await mem.memory_get(self.session, principal_id, mid)
        if action == "create":
            return await mem.memory_create(
                self.session,
                principal_id,
                content=str(f.get("content") or d.body),
                memory_type=str(f.get("memory_type") or f.get("type") or "semantic"),
                tags=tags,
                structured=structured,
                metadata=metadata,
                expires_at=None if expires_at is mem._UNSET else expires_at,
                work_id=work_ref,
                message_id=msg_ref,
            )
        if action == "update":
            return await mem.memory_update(
                self.session,
                principal_id,
                mid,
                content=f.get("content"),
                memory_type=f.get("memory_type") or f.get("type"),
                tags=tags,
                structured=structured,
                metadata=metadata,
                expires_at=expires_at,
            )
        if action == "supersede":
            return await mem.memory_supersede(
                self.session,
                principal_id,
                mid,
                new_content=str(f.get("content") or d.body),
                memory_type=f.get("memory_type") or f.get("type"),
                tags=tags,
                structured=structured,
                metadata=metadata,
                expires_at=expires_at,
                reason=f.get("reason"),
                work_id=work_ref,
                message_id=msg_ref,
            )
        if action in ("forget", "delete"):
            return await mem.memory_forget(self.session, principal_id, mid)
        if action == "preferences":
            # Retired surface: there is no separate preferences store. The AI's
            # durable judgment-bearing state lives in notebook/ and memory.
            return {
                "ok": False,
                "error": "preferences_retired",
                "note": (
                    "Preferences are no longer a separate store — one personalization "
                    "authority only. Keep agreements/settings you own in your notebook/ "
                    "files or as memory (state create/update/supersede/forget)."
                ),
            }
        if action == "profile":
            # Read-only factual identity context (name, linked channels, first
            # contact, account facts). Interpretation is the AI's.
            from wax.domain.profile import factual_context

            pid = mem._as_uuid(principal_id)
            if not pid:
                return {"ok": False, "error": "no_principal"}
            ctx = await factual_context(self.session, pid)
            return {"ok": True, "profile": ctx}
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

    async def _record_system_message(self, work: Work, content: str) -> None:
        """Persist an infrastructure-originated system line (e.g. the wake-up
        hint of a scheduled action) as a clearly-marked Message row.

        This keeps transcript continuity honest: every assistant reply has
        its real trigger recorded beside it. Idempotent per Work via unique
        (channel, external_id='system:{work_id}'). Infrastructure records
        the fact; interpreting it is the AI's job.
        """
        if not work.conversation_id or not work.principal_id or not work.id:
            return
        ext = f"system:{work.id}"
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
                    direction="inbound",
                    role="system",
                    content=content,
                    external_id=ext,
                    work_id=work.id,
                    metadata_={"source": "infrastructure"},
                )
                self.session.add(message)
                await self.session.flush()
            from wax.world.transcript import archive_message

            await archive_message(self.session, message)
        except IntegrityError:
            logger.info("system_message_already_recorded", work_id=str(work.id))

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
        """Record the assistant's reply into durable transcript.

        Outbound delivery to the messaging channel is handled by the durable
        Delivery record path in the worker (process_message_response), not by
        this direct call. This keeps exactly one outbound delivery path and
        preserves retries/idempotency through the Delivery table.
        """
        try:
            await self._record_outbound_message(work, reply)
        except Exception:
            logger.exception("outbound_message_record_failed")
