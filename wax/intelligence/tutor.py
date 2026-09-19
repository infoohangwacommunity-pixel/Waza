"""
WAX Prep Tutor Intelligence.

Context assembly → AI (every learner message) → tools → delivery → memory.
No hardcoded subjects, exams, or modes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Conversation, Delivery, Message, Work
from wax.intelligence.providers import (
    ChatMessage,
    CompletionRequest,
    ToolSpec,
    get_intelligence,
)
from wax.memory.service import MemoryService
from wax.observability.logging import get_logger
from wax.tools.registry import execute_tool
from wax.delivery.presentation import platform_context_block, InteractiveChoice, PresentableResponse

logger = get_logger(__name__)


TUTOR_SYSTEM = """You are WAX Prep — the tutor that actually knows the learner.

You are not a rigid educational app. You are a persistent, adaptive learning companion.

Core principles:
- Meet the learner where they are. Never assume they are a particular type of student.
- Learn about them organically. Never dump questionnaires.
- Adapt depth, pace, examples, challenge, and style to this person.
- Use the memories provided when relevant.
- Prefer natural conversation over forms or menus.
- When useful: explain, ask a diagnostic question, give an example, create practice, challenge, summarize, schedule a follow-up, or create a durable artifact.
- If you do not remember something clearly, say so honestly. Never invent memories.
- Help the person learn and progress — not merely answer the latest message.
- Keep messages readable for messaging platforms.
- Never mention internal system details, costs, tokens, or budgets.

You may use tools when they materially help. Do not use tools for ordinary conversation.

You have access to relevant memories about this learner (provided below).
Respond as a warm, intelligent, patient tutor.
"""


AVAILABLE_TOOLS = [
    ToolSpec(
        name="schedule_followup",
        description="Schedule a future follow-up for this learner when a later action would help.",
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "delay_hours": {"type": "number"},
                "message_hint": {"type": "string"},
            },
            "required": ["reason", "delay_hours"],
        },
    ),
    ToolSpec(
        name="create_artifact",
        description="Create a durable artifact (notes, practice, summary, guide) the learner can return to.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["kind", "title", "content"],
        },
    ),
    ToolSpec(
        name="run_python",
        description="Run a short Python snippet for calculation, generation, or analysis. Prefer small, focused code.",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute"},
            },
            "required": ["code"],
        },
    ),

    ToolSpec(
        name="present_choices",
        description=(
            "Offer the learner a small set of clear choices as interactive buttons (or a list). "
            "Use only when a genuine choice helps (e.g. Continue / Try another way / I'm done). "
            "Do NOT use for every message. Do NOT build rigid menus. Max 3 for buttons, more for list."
        ),
        parameters={
            "type": "object",
            "properties": {
                "style": {"type": "string", "description": "buttons or list"},
                "prompt": {"type": "string", "description": "Optional short prompt for the interactive bubble"},
                "list_button_label": {"type": "string"},
                "choices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["choices"],
        },
    ),
]


class TutorService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.intelligence = get_intelligence()
        self.memory = MemoryService(session)

    async def handle_message(self, work: Work) -> dict[str, Any]:
        payload = work.input_payload or {}
        principal_id = work.principal_id
        conversation_id = work.conversation_id
        user_text = payload.get("text", "")
        channel = payload.get("channel", "unknown")
        target = payload.get("target_external_id")

        recent: list[dict[str, str]] = []
        if conversation_id:
            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(16)
            )
            result = await self.session.execute(stmt)
            msgs = list(reversed(result.scalars().all()))
            for m in msgs:
                recent.append({"role": m.role, "content": m.content})

        memory_summary = "No prior memories yet."
        relevant_memories = []
        if principal_id:
            relevant_memories = await self.memory.plan_and_retrieve(
                principal_id, user_text, limit=14
            )
            if relevant_memories:
                lines = [
                    f"- [{m.memory_type}|c={m.confidence:.2f}] {m.content}"
                    for m in relevant_memories
                ]
                memory_summary = "\n".join(lines)
            else:
                memory_summary = await self.memory.get_active_summary(principal_id)

        system = (
            TUTOR_SYSTEM
            + platform_context_block(channel)
            + "\n\n--- What WAX currently understands about this learner ---\n"
            + memory_summary
            + "\n--- End of learner understanding ---\n"
        )

        llm_messages: list[ChatMessage] = [ChatMessage(role="system", content=system)]
        for m in recent:
            role = "assistant" if m["role"] == "assistant" else "user"
            llm_messages.append(ChatMessage(role=role, content=m["content"]))
        if not recent or recent[-1].get("content") != user_text:
            llm_messages.append(ChatMessage(role="user", content=user_text))

        tool_ctx = {
            "principal_id": principal_id,
            "work_id": work.id,
            "conversation_id": conversation_id,
        }

        # Tool loop: up to a few rounds so the model can act then respond
        reply_text = ""
        tool_notes: list[str] = []
        interactive_payload: dict | None = None
        max_rounds = 3
        for _ in range(max_rounds):
            response = await self.intelligence.complete(
                CompletionRequest(
                    messages=llm_messages,
                    tools=AVAILABLE_TOOLS,
                    temperature=0.7,
                    max_tokens=2048,
                ),
                allow_fallback=True,
            )

            if response.tool_calls:
                llm_messages.append(
                    ChatMessage(
                        role="assistant",
                        content=response.content or "",
                        tool_calls=response.tool_calls,
                    )
                )
                for tc in response.tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or "unknown"
                    args = fn.get("arguments")
                    tc_id = tc.get("id") or str(uuid4())
                    outcome = await execute_tool(self.session, name, args, tool_ctx)
                    tool_notes.append(f"{name}:{outcome.get('ok')}")
                    if name == "present_choices" and outcome.get("ok"):
                        interactive_payload = outcome
                    llm_messages.append(
                        ChatMessage(
                            role="tool",
                            content=str(outcome)[:4000],
                            tool_call_id=tc_id,
                            name=name,
                        )
                    )
                continue

            reply_text = (response.content or "").strip()
            break

        if not reply_text:
            reply_text = "I'm here with you. Could you say that again another way?"

        out_msg_id = None
        if conversation_id and principal_id:
            out_msg = Message(
                id=uuid4(),
                conversation_id=conversation_id,
                principal_id=principal_id,
                channel=channel,
                direction="outbound",
                role="assistant",
                content=reply_text,
                work_id=work.id,
                metadata_={"tools": tool_notes} if tool_notes else {},
            )
            self.session.add(out_msg)
            await self.session.flush()
            out_msg_id = out_msg.id
            conv = await self.session.get(Conversation, conversation_id)
            if conv:
                conv.last_message_at = datetime.now(timezone.utc)

        delivery_id = None
        if principal_id and target and reply_text:
            delivery = Delivery(
                id=uuid4(),
                work_id=work.id,
                principal_id=principal_id,
                channel=channel,
                target_external_id=str(target),
                content=reply_text,
                status="pending",
                idempotency_key=f"delivery:{work.id}",
            )
            self.session.add(delivery)
            await self.session.flush()
            delivery_id = delivery.id

        if principal_id:
            await self.memory.extract_and_store(
                principal_id=principal_id,
                conversation_id=str(conversation_id) if conversation_id else None,
                recent_messages=recent
                + [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": reply_text},
                ],
                source_work_id=work.id,
            )

        return {
            "reply": reply_text,
            "message_id": str(out_msg_id) if out_msg_id else None,
            "delivery_id": str(delivery_id) if delivery_id else None,
            "memories_used": len(relevant_memories),
            "tools": tool_notes,
            "interactive": interactive_payload,
        }

    async def handle_scheduled_action(self, work: Work) -> dict[str, Any]:
        """Proactive / scheduled turn — still goes through intelligence when appropriate."""
        payload = work.input_payload or {}
        principal_id = work.principal_id
        action_type = payload.get("action_type", "tutor_followup")
        reason = payload.get("reason") or ""
        hint = (payload.get("payload") or {}).get("message_hint") or reason

        memory_summary = ""
        if principal_id:
            memory_summary = await self.memory.get_active_summary(principal_id)

        system = (
            TUTOR_SYSTEM
            + "\n\nThis is a scheduled follow-up you previously decided was useful.\n"
            + f"Reason: {reason}\nHint: {hint}\n\n"
            + "--- Learner understanding ---\n"
            + (memory_summary or "Sparse history.")
            + "\nWrite a natural, useful follow-up message. Do not spam. If it no longer seems useful, keep it brief and gentle.\n"
        )

        response = await self.intelligence.complete(
            CompletionRequest(
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(
                        role="user",
                        content="Compose the follow-up message for this learner now.",
                    ),
                ],
                temperature=0.6,
                max_tokens=1024,
            ),
            allow_fallback=True,
        )
        reply = (response.content or "Just checking in — how is your learning going?").strip()
        return {"reply": reply, "action_type": action_type}
