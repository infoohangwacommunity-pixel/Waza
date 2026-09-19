"""
WAX Prep Tutor Intelligence.

The primary experience the learner has.

- Assembles context intelligently (not dump everything)
- Reasons with the primary model
- Supports tool use (terminal, schedule, artifacts)
- Updates memory after the turn (isolated)
- Adapts to the person over time

No hardcoded subjects, exams, or modes.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Conversation, Message, Work
from wax.intelligence.providers import (
    ChatMessage,
    CompletionRequest,
    ToolSpec,
    get_intelligence,
)
from wax.memory.service import MemoryService
from wax.observability.logging import get_logger

logger = get_logger(__name__)


TUTOR_SYSTEM = """You are WAX Prep — the tutor that actually knows the learner.

You are not a rigid educational app. You are a persistent, adaptive learning companion.

Core principles:
- Meet the learner where they are. Never assume they are a "student" of a particular type.
- Learn about them organically through conversation. Never dump questionnaires.
- Adapt explanation depth, pace, examples, challenge level, and style to this specific person.
- Use the memories provided about this learner when relevant.
- Prefer natural conversation over forms or menus.
- When useful you may: explain, ask a diagnostic question, give an example, create a short practice, challenge, summarize, or plan a follow-up.
- If you do not remember something clearly, say so honestly. Never invent memories.
- Help the person learn and progress — not merely answer the latest message.
- Keep messages readable for messaging platforms: concise when appropriate, well structured when longer. Avoid giant walls of text.
- Never mention internal system details, costs, tokens, or budgets.

You have access to relevant memories about this learner (provided below).
Respond as a warm, intelligent, patient tutor.
"""


AVAILABLE_TOOLS = [
    ToolSpec(
        name="schedule_followup",
        description="Schedule a future reminder or learning follow-up for this learner. Use when a future action would materially help.",
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why this follow-up is useful"},
                "delay_hours": {"type": "number", "description": "Hours from now to execute"},
                "message_hint": {"type": "string", "description": "What the follow-up should roughly say"},
            },
            "required": ["reason", "delay_hours"],
        },
    ),
    ToolSpec(
        name="create_artifact",
        description="Create a durable study artifact (notes, practice set, summary, guide) the learner can return to later.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "notes|practice|summary|guide|other"},
                "title": {"type": "string"},
                "content": {"type": "string", "description": "Full content of the artifact"},
            },
            "required": ["kind", "title", "content"],
        },
    ),
]


class TutorService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.intelligence = get_intelligence()
        self.memory = MemoryService(session)

    async def handle_message(
        self,
        work: Work,
    ) -> dict[str, Any]:
        """
        Full tutor turn:
        1. Load conversation history
        2. Retrieve relevant memories
        3. Assemble context
        4. Call intelligence (every learner message goes through AI)
        5. Optionally run tools
        6. Persist outbound message
        7. Create delivery
        8. Extract memory (isolated)
        """
        payload = work.input_payload or {}
        principal_id = work.principal_id
        conversation_id = work.conversation_id
        user_text = payload.get("text", "")
        channel = payload.get("channel", "unknown")
        target = payload.get("target_external_id")

        # 1. Recent conversation
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

        # 2. Advanced memory retrieval
        memory_summary = "No prior memories yet."
        relevant_memories: list = []
        if principal_id:
            relevant_memories = await self.memory.plan_and_retrieve(
                principal_id, user_text, limit=14
            )
            if relevant_memories:
                lines = []
                for m in relevant_memories:
                    lines.append(
                        f"- [{m.memory_type}|c={m.confidence:.2f}] {m.content}"
                    )
                memory_summary = "\n".join(lines)
            else:
                memory_summary = await self.memory.get_active_summary(principal_id)

        # 3. Assemble context
        system = (
            TUTOR_SYSTEM
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

        # 4. Intelligence call — every meaningful message goes through AI
        response = await self.intelligence.complete(
            CompletionRequest(
                messages=llm_messages,
                tools=AVAILABLE_TOOLS,
                temperature=0.7,
                max_tokens=2048,
            ),
            allow_fallback=True,
        )

        reply_text = (response.content or "").strip()
        if not reply_text and not response.tool_calls:
            reply_text = "I'm here with you. Could you say that again another way?"

        # 5. Simple tool handling (expand later)
        tool_notes: list[str] = []
        for tc in response.tool_calls or []:
            fn = tc.get("function") or {}
            name = fn.get("name")
            tool_notes.append(f"tool:{name}")
            # Full tool execution will be wired to terminal / scheduler / artifacts
            # For now we acknowledge the intention in metadata

        if tool_notes and not reply_text:
            reply_text = "I've noted that and will follow through."

        # 6. Persist outbound message
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

            # Update conversation last_message_at
            conv = await self.session.get(Conversation, conversation_id)
            if conv:
                from datetime import datetime, timezone
                conv.last_message_at = datetime.now(timezone.utc)

        # 7. Delivery record (actual send is separate)
        delivery_id = None
        if principal_id and target and reply_text:
            from wax.db.models import Delivery

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

        # 8. Memory extraction (isolated — never fails the turn)
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
        }
