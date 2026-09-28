"""
WAX Prep Tutor — the brain.

Infrastructure provides identity, World, messaging, security, and primitives.
The AI decides what to retrieve, remember, process, create, and say.
No Context Intelligence. No automatic context assembler. No workflow tool menu.
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
from wax.intelligence.providers import ChatMessage, CompletionRequest, get_intelligence
from wax.observability.logging import get_logger
from wax.primitives.registry import execute_primitive, list_primitive_specs

logger = get_logger(__name__)

TUTOR_SYSTEM = """You are WAX — a persistent adaptive tutor by WAX Prep.

You are the tutor. Model vendors are infrastructure only — never claim to be those products.

You have a secure persistent World for this student (files, packages, terminal) and durable
memory that you control. Infrastructure enforces security and delivery; you decide.

How you work:
- Meet the student where they are. Discover needs through conversation.
- You decide when to search, create, update, supersede, or forget memory.
- You decide whether to inspect World files, run code, install packages, or publish a page.
- When a file is in the World, you decide how to process it (run code, install tools, etc.).
- Materials come from the student or what you create together — not a fixed curriculum bank.
- Keep replies readable on messaging apps. Be warm, clear, honest.
- Never invent memories, tool results, or URLs. Only claim success when a primitive returns ok.
- Ordinary chat needs no primitives. Use them when they help.

Teaching: adapt to this person. Prefer a useful next step over a lecture dump.
Vary how you check understanding. Do not run rigid quiz scripts.

Privacy: if they ask to forget something, use memory_forget and confirm from the result.
Do not invent privacy guarantees.
"""


class TutorService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.intelligence = get_intelligence()

    async def handle_message(self, work: Work) -> dict[str, Any]:
        payload = work.input_payload or {}
        principal_id = work.principal_id
        conversation_id = work.conversation_id
        user_text = (payload.get("text") or "").strip()
        channel = payload.get("channel") or "unknown"
        target = payload.get("target_external_id")

        # Recent conversation only — no Assembler/CI selection
        recent: list[dict[str, str]] = []
        if conversation_id:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(20)
            )
            result = await self.session.execute(stmt)
            msgs = list(reversed(result.scalars().all()))
            for m in msgs:
                recent.append({"role": m.role, "content": m.content or ""})

        # Preferences (data availability, not intelligence selection)
        prefs_block = ""
        try:
            from wax.domain.preferences import get_preferences

            if principal_id:
                prefs = await get_preferences(self.session, principal_id)
                interesting = [
                    f"- {k}: {v}"
                    for k, v in (prefs or {}).items()
                    if v not in (None, "", False)
                ]
                if interesting:
                    prefs_block = (
                        "\n--- Durable preferences (honor unless current turn overrides) ---\n"
                        + "\n".join(interesting)
                        + "\n--- End preferences ---\n"
                    )
        except Exception:
            logger.exception("preferences_load_failed")

        identity_block = ""
        try:
            if principal_id:
                from wax.domain.identity import linked_channels_state, format_linked_channels_block

                state = await linked_channels_state(
                    self.session, principal_id=principal_id, current_channel=channel
                )
                identity_block = format_linked_channels_block(state)
        except Exception:
            logger.exception("identity_block_failed")

        media_note = ""
        local_path = payload.get("local_media_path") or payload.get("principal_media_path")
        if local_path:
            media_note = (
                f"\n[System: a file is available in this student's World at {local_path}. "
                f"You decide whether to inspect or process it via world_files / world_exec / world_acquire.]\n"
            )
        elif payload.get("media_id"):
            media_note = (
                "\n[System: inbound media was referenced but not yet staged. "
                "Ask the student to resend if needed.]\n"
            )

        system = TUTOR_SYSTEM + prefs_block + identity_block + media_note
        llm_messages: list[ChatMessage] = [ChatMessage(role="system", content=system)]
        for m in recent:
            role = "assistant" if m["role"] == "assistant" else "user"
            llm_messages.append(ChatMessage(role=role, content=m["content"]))
        if user_text:
            # Ensure current message is last if not already in recent
            if not recent or recent[-1].get("content") != user_text:
                llm_messages.append(ChatMessage(role="user", content=user_text))

        primitives = list_primitive_specs()
        tool_ctx = {
            "principal_id": principal_id,
            "work_id": work.id,
            "conversation_id": conversation_id,
            "channel": channel,
            "target_external_id": target,
            "local_media_path": local_path,
        }

        agent = AgentRuntime(self.session, work)
        await agent.start()
        reply_text = ""
        tool_results: list[dict] = []
        interactive_payload: dict | None = None
        max_rounds = agent.max_rounds

        for round_i in range(max_rounds):
            if not agent.can_call_tool() and round_i > 0:
                break
            response = await self.intelligence.complete(
                CompletionRequest(
                    messages=llm_messages,
                    tools=primitives if agent.can_call_tool() else None,
                    temperature=0.7,
                    max_tokens=2048,
                ),
                allow_fallback=True,
            )
            if response.tool_calls and agent.can_call_tool():
                llm_messages.append(
                    ChatMessage(
                        role="assistant",
                        content=response.content or "",
                        tool_calls=response.tool_calls,
                    )
                )
                for tc in response.tool_calls:
                    if not agent.can_call_tool():
                        break
                    fn = tc.get("function") or {}
                    name = fn.get("name") or "unknown"
                    args = fn.get("arguments")
                    tc_id = tc.get("id") or str(uuid4())
                    outcome = await execute_primitive(self.session, name, args, tool_ctx)
                    out_dict = outcome if isinstance(outcome, dict) else {"ok": False}
                    await agent.record_tool(name, args if isinstance(args, dict) else {}, out_dict)
                    tool_results.append({"name": name, "result": out_dict})
                    if name == "present_choices" and out_dict.get("ok"):
                        interactive_payload = out_dict
                    llm_messages.append(
                        ChatMessage(
                            role="tool",
                            content=str(out_dict)[:4000],
                            tool_call_id=tc_id,
                            name=name,
                        )
                    )
                continue

            reply_text = (response.content or "").strip()
            break

        if not reply_text:
            reply_text = "I'm here — try sending that again."

        await agent.complete(reply_preview=reply_text)

        # Persist assistant message
        if conversation_id and principal_id:
            self.session.add(
                Message(
                    id=uuid4(),
                    conversation_id=conversation_id,
                    principal_id=principal_id,
                    role="assistant",
                    direction="outbound",
                    content=reply_text,
                    channel=channel,
                    work_id=work.id,
                )
            )
            await self.session.flush()

        buttons: list[InteractiveChoice] = []
        if interactive_payload:
            for c in interactive_payload.get("choices") or []:
                buttons.append(
                    InteractiveChoice(
                        id=str(c.get("id") or ""),
                        title=str(c.get("title") or ""),
                        description=c.get("description"),
                    )
                )

        presentable = PresentableResponse(
            text=reply_text,
            interactive_type="reply_buttons" if buttons else None,
            buttons=buttons,
            list_button_label=(interactive_payload or {}).get("list_button_label"),
        )

        # Delivery — infrastructure sends; AI already decided content
        if target and channel and principal_id:
            did = uuid4()
            delivery = Delivery(
                id=did,
                work_id=work.id,
                principal_id=principal_id,
                channel=channel,
                target_external_id=str(target),
                status="pending",
                content=reply_text,
                idempotency_key=f"work:{work.id}:{did}",
            )
            self.session.add(delivery)
            await self.session.flush()
            try:
                from wax.delivery.senders import deliver

                result = await deliver(
                    channel=channel,
                    target=str(target),
                    text=reply_text,
                    interactive=interactive_payload,
                )
                if isinstance(result, dict) and result.get("status") == "failed":
                    delivery.status = "failed"
                    delivery.error = str(result.get("reason") or "send_failed")[:500]
                else:
                    delivery.status = "sent"
                    delivery.delivered_at = datetime.now(timezone.utc)
                await self.session.flush()
            except Exception as e:
                logger.exception("delivery_failed", work_id=str(work.id))
                delivery.status = "failed"
                delivery.error = str(e)[:500]
                await self.session.flush()

        return {
            "ok": True,
            "reply": reply_text,
            "tool_results": tool_results,
            "interaction": interactive_payload,
            "presentable": presentable,
        }

    async def handle_scheduled_action(self, work: Work) -> dict[str, Any]:
        """Wake path: scheduled Work becomes a message the brain handles."""
        payload = dict(work.input_payload or {})
        hint = payload.get("message_hint") or payload.get("reason") or "scheduled follow-up"
        payload["text"] = (
            f"[System scheduled wake] {hint}\n"
            "Continue helpfully for this student based on memory and context. "
            "Do not mention internal scheduling machinery."
        )
        work.input_payload = payload
        await self.session.flush()
        return await self.handle_message(work)

    async def handle_surface_request(self, work: Work) -> dict[str, Any]:
        return await self.handle_message(work)
