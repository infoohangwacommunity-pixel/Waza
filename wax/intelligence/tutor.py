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
from wax.intelligence.session_continuity import SessionContinuityService
from wax.memory.observations import ObservationService
from wax.observability.logging import get_logger
from wax.tools.registry import execute_tool
from wax.delivery.presentation import InteractiveChoice, PresentableResponse
from wax.intelligence.context import ContextAssembler

logger = get_logger(__name__)


TUTOR_SYSTEM = """You are WAX Prep — a persistent tutor that gets to know this person over time.

Talk naturally. You are not a form, a menu, or a rigid course system.

How you work:
- Meet them where they are. Discover goals and needs through conversation.
- Use what you genuinely remember about them when it helps.
- If you are unsure what you remember, say so. Do not invent history.
- Adapt explanations, pace, and style to this person from evidence, not from labels.
- Use tools only when they clearly help (files, schedule, memory, workspace). Ordinary chat needs no tools.
- Learning materials come from the learner (what they say, send, or upload) or from what you create together in the moment — not from a fixed curriculum bank in the product.
- Keep replies readable on messaging apps. Be warm, clear, and honest.

You are not controlled by subject lists, exam modes, or preset lesson scripts.
Respond as a real tutor who is actually paying attention to this person.

First contact and onboarding:
- Respond to what they actually said. Do not run an intake questionnaire.
- Do not stack multiple onboarding questions. One natural follow-up is enough.
- Do not re-introduce yourself or explain what WAX is unless they ask.
- Do not say "let's get started" as a habit. Discover goals only when it fits the conversation.
- If you already know their name, goals, or preferences from memory, use them quietly — do not dump a memory list.

Message length:
- Honor durable preferences (e.g. they asked for short messages) as a default.
- A clear current-turn request always wins: "explain deeply" → more detail; "just answer" → short.
- Match length to the moment: confirmations stay brief; hard concepts may need more; messaging apps still prefer readable chunks.
- Vary length. Not every reply should be the same size.

Artifacts and files:
- Only say a file was sent when the delivery layer confirmed it.
- create_artifact stores the file; channel delivery is separate.

Active learning context:
- Notice what you and the learner were doing before a new request.
- If the new request is unrelated, you may briefly offer to pause and resume later — do not invent subject bans.

Buttons:
- Use present_choices only when a small set of options clearly helps.
- If they say they do not want buttons, stop using them for this conversation unless they ask again.
- Typed answers are always valid even when buttons were offered.
"""


AVAILABLE_TOOLS = [
    ToolSpec(
        name="schedule_followup",
        description="Schedule a future follow-up when a later check-in would help this person.",
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
        name="schedule_continuous",
        description="Schedule several future follow-ups (e.g. over a few days). Use only when the person wants ongoing support.",
        parameters={
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "message_hint": {"type": "string"},
                "hours_from_now": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["reason", "hours_from_now"],
        },
    ),
    ToolSpec(
        name="create_artifact",
        description="Create a durable document (notes, study sheet, PDF) for this learner. Use format=pdf when they ask for a PDF. Do not claim the file was sent until delivery succeeds.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "format": {"type": "string", "description": "txt or pdf"},
            },
            "required": ["kind", "title", "content"],
        },
    ),
    ToolSpec(
        name="list_artifacts",
        description="List artifacts previously saved for this person.",
        parameters={"type": "object", "properties": {"limit": {"type": "number"}}},
    ),
    ToolSpec(
        name="read_artifact",
        description="Read a saved artifact by id.",
        parameters={
            "type": "object",
            "properties": {"artifact_id": {"type": "string"}},
            "required": ["artifact_id"],
        },
    ),
    ToolSpec(
        name="run_python",
        description="Run a short Python snippet when calculation or generation helps.",
        parameters={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    ),
    ToolSpec(
        name="write_workspace_file",
        description="Write a file in the workspace for multi-step work.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
                "subdir": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    ),
    ToolSpec(
        name="read_workspace_file",
        description="Read a file from the workspace.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="list_workspace",
        description="List files in the workspace (default: media).",
        parameters={
            "type": "object",
            "properties": {"subdir": {"type": "string"}, "limit": {"type": "number"}},
        },
    ),
    ToolSpec(
        name="fetch_inbound_media",
        description="Download media the learner sent into the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "media_id": {"type": "string"},
                "filename": {"type": "string"},
            },
            "required": ["channel", "media_id"],
        },
    ),
    ToolSpec(
        name="inspect_media",
        description="Inspect a local media file (type, OCR, etc.) when the learner sent a photo or document.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="workspace_command",
        description="Run one allowed command in the workspace.",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="describe_image",
        description="Optional vision description of a local image — only if simpler inspection is not enough.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "question": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
    ToolSpec(
        name="get_current_time",
        description="Get authoritative current UTC and optional learner timezone. Use before scheduling or deadlines.",
        parameters={
            "type": "object",
            "properties": {"timezone": {"type": "string", "description": "IANA timezone e.g. Africa/Lagos"}},
        },
    ),
        name="present_choices",
        description="Optional quick choices as buttons when a real choice helps. Not a permanent menu.",
        parameters={
            "type": "object",
            "properties": {
                "style": {"type": "string"},
                "prompt": {"type": "string"},
                "list_button_label": {"type": "string"},
                "choices": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["choices"],
        },
    ),
    ToolSpec(
        name="forget_memory",
        description="Forget something when the learner asks.",
        parameters={
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
    ),
    ToolSpec(
        name="inspect_memories",
        description="Show what is remembered when the learner asks.",
        parameters={"type": "object", "properties": {"limit": {"type": "number"}}},
    ),
    ToolSpec(
        name="manage_goal",
        description="Track a goal this person stated or implied.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "goal_id": {"type": "string"},
                "priority": {"type": "number"},
            },
            "required": ["action"],
        },
    ),
    ToolSpec(
        name="start_activity",
        description="Start a durable practice or timed session if useful — not a fixed course mode.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "objective": {"type": "string"},
                "duration_minutes": {"type": "number"},
                "notes": {"type": "string"},
            },
            "required": ["kind", "objective"],
        },
    ),
    ToolSpec(
        name="complete_activity",
        description="Complete a durable activity.",
        parameters={
            "type": "object",
            "properties": {
                "activity_id": {"type": "string"},
                "outcome": {"type": "object"},
            },
            "required": ["activity_id"],
        },
    ),
    ToolSpec(
        name="update_concept_state",
        description="Optionally note how this person is doing with a concept they are working on.",
        parameters={
            "type": "object",
            "properties": {
                "concept": {"type": "string"},
                "mastery_delta": {"type": "number"},
                "status": {"type": "string"},
                "note": {"type": "string"},
                "related_concept": {"type": "string"},
                "relation_type": {"type": "string"},
            },
            "required": ["concept"],
        },
    ),

    ToolSpec(
        name="create_assessment",
        description="Create a durable practice/assessment (optional timer). Not a fixed quiz mode.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "objective": {"type": "string"},
                "timed": {"type": "boolean"},
                "duration_minutes": {"type": "number"},
                "one_at_a_time": {"type": "boolean"},
                "items": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["title", "items"],
        },
    ),
    ToolSpec(
        name="submit_assessment_answer",
        description="Record an assessment answer and advance.",
        parameters={
            "type": "object",
            "properties": {
                "attempt_id": {"type": "string"},
                "item_id": {"type": "string"},
                "response_text": {"type": "string"},
            },
            "required": ["attempt_id", "item_id", "response_text"],
        },
    ),
    ToolSpec(
        name="record_evidence",
        description="Record observable evidence. Do not invent scores.",
        parameters={
            "type": "object",
            "properties": {
                "evidence_type": {"type": "string"},
                "description": {"type": "string"},
                "claim_key": {"type": "string"},
                "assistance_level": {"type": "string"},
                "payload": {"type": "object"},
                "weight": {"type": "number"},
            },
            "required": ["evidence_type", "description"],
        },
    ),
    ToolSpec(
        name="form_hypothesis",
        description="Form a candidate hypothesis about the learner (not a fact).",
        parameters={
            "type": "object",
            "properties": {
                "claim_key": {"type": "string"},
                "claim": {"type": "string"},
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
            },
            "required": ["claim_key", "claim"],
        },
    ),
    ToolSpec(
        name="why_we_believe",
        description="Explain the evidence behind a claim about the learner.",
        parameters={
            "type": "object",
            "properties": {"claim_key": {"type": "string"}},
            "required": ["claim_key"],
        },
    ),

    ToolSpec(
        name="propose_learning_check",
        description="Suggest a light next check for an uncertain hypothesis about this learner. Do not spam.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="schedule_hypothesis_recheck",
        description="Schedule a durable later re-check of a hypothesis.",
        parameters={
            "type": "object",
            "properties": {
                "hypothesis_id": {"type": "string"},
                "delay_hours": {"type": "number"},
                "message_hint": {"type": "string"},
            },
            "required": ["hypothesis_id"],
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

        assembler = ContextAssembler(self.session)
        ctx = await assembler.assemble(
            principal_id=principal_id,
            conversation_id=conversation_id,
            channel=channel,
            user_text=user_text,
            tutor_system=TUTOR_SYSTEM,
        )
        relevant_memories = []
        system = (
            ctx.system_prefix
            + ctx.memory_block
            + ctx.learning_block
            + ctx.goals_block
        )
        recent = ctx.recent_messages
        llm_messages: list[ChatMessage] = [ChatMessage(role="system", content=system)]
        for m in recent:
            role = "assistant" if m["role"] == "assistant" else "user"
            llm_messages.append(ChatMessage(role=role, content=m["content"]))
        media_note = ""
        if payload.get("media_id"):
            media_note = (
                f"\n\n[System: inbound media available. channel={channel} "
                f"content_type={payload.get('content_type')} media_id={payload.get('media_id')}. "
                f"You can fetch_inbound_media then inspect_media.]"
            )
        if payload.get("local_media_path"):
            media_note += f"\n[System: media already on disk at {payload.get('local_media_path')}]"
        user_content = user_text + media_note
        if not recent or recent[-1].get("content") != user_text:
            llm_messages.append(ChatMessage(role="user", content=user_content))
        elif media_note:
            llm_messages.append(ChatMessage(role="user", content=user_content))
        await assembler.maybe_refresh_conversation_summary(conversation_id, recent)

        tool_ctx = {
            "principal_id": principal_id,
            "work_id": work.id,
            "conversation_id": conversation_id,
            "channel": channel,
            "media_id": payload.get("media_id"),
            "content_type": payload.get("content_type"),
        }

        # Tool loop: up to a few rounds so the model can act then respond
        reply_text = ""
        tool_notes: list[str] = []
        tool_results: list[dict] = []
        interactive_payload: dict | None = None
        # Budgeted agent loop (not a fixed product ceiling of 3 forever)
        from wax.config import get_settings as _gs
        _s = _gs()
        max_rounds = int(getattr(_s, 'agent_max_tool_rounds', 8) or 8)
        max_rounds = max(1, min(max_rounds, 20))
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
                    tool_results.append({"name": name, "result": outcome})
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

        # Memory is durable Work — never blocks learner response latency
        if principal_id:
            try:
                mem_work = Work(
                    id=uuid4(),
                    principal_id=principal_id,
                    conversation_id=conversation_id,
                    kind="memory_process",
                    status="queued",
                    priority=80,
                    objective="Post-turn memory extraction and continuity",
                    input_payload={
                        "source_work_id": str(work.id),
                        "conversation_id": str(conversation_id) if conversation_id else None,
                        "user_text": user_text[:2000],
                        "reply_text": reply_text[:2000],
                        "recent": recent[-8:] if recent else [],
                    },
                )
                self.session.add(mem_work)
                await self.session.flush()
            except Exception:
                logger.exception("memory_work_enqueue_failed")

        return {
            "reply": reply_text,
            "message_id": str(out_msg_id) if out_msg_id else None,
            "delivery_id": str(delivery_id) if delivery_id else None,
            "memories_used": getattr(ctx, "memories_used", 0),
            "tools": tool_results,
            "tool_notes": tool_notes,
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
