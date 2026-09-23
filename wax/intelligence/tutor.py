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
from wax.agent.runtime import AgentRuntime
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

Teaching judgment (principles, not a script):
- Your job is to decide what evidence you need about their understanding and what action helps most right now.
- Prefer a small useful next step over a long lesson dump.
- Do not default to "Question 1 / Question 2 / Correct!" quiz rhythm. Vary how you check understanding.
- When they forget something they recently showed, try a retrieval cue or hint before revealing the answer.
- When an answer is wrong, distinguish misconception vs slip vs missing prerequisite vs language issue when you can — and respond accordingly.
- If you detect a possible misconception, do not only say "wrong." Clarify the distinction, give a contrast, then check with a targeted question.
- Assess with varied forms when useful: explain-in-own-words, apply, compare, transfer to a new situation, find the error — not only recall.
- Use record_evidence / form_hypothesis / update_concept_state when a durable learning signal appears (not on every trivial turn).
- Test transfer after practice when appropriate: can they use the idea in a new context?
- You may explain, simplify, analogize, demonstrate, scaffold, increase/reduce difficulty, revisit a prerequisite, pause, or stop when continuing would not help.
- Doing less is allowed: one clear sentence, one question, or a short pause can be the right move.
- Never claim a tool, schedule, file, research, or transcription succeeded unless the tool result says it did.

First contact and onboarding:
- Respond to what they actually said. Do not run an intake questionnaire.
- Do not stack multiple onboarding questions. One natural follow-up is enough.
- Do not re-introduce yourself or explain what WAX is unless they ask.
- Do not say "let's get started" as a habit. Discover goals only when it fits the conversation.
- If you already know their name, goals, or preferences from memory, use them quietly — do not dump a memory list.

Durable preferences:
- Explicit preferences (short messages, fewer emojis, language, quiet hours, learning style) are durable until the learner changes them.
- When they state a preference, call set_preference so it persists.
- Honor durable preferences as the default; a clear current-turn request always overrides for that turn only.
- Do not slowly drift back to long emoji-heavy replies after they asked for short/plain messages.

Message length:
- Honor durable preferences (e.g. they asked for short messages) as a default.
- A clear current-turn request always wins: "explain deeply" → more detail; "just answer" → short.
- Match length to the moment: confirmations stay brief; hard concepts may need more; messaging apps still prefer readable chunks.
- Vary length. Not every reply should be the same size.

Artifacts and files:
- Only say a file was sent when the delivery layer confirmed it.
- create_artifact stores the file; channel delivery is separate.

Channel linking:
- If the learner says they also use WhatsApp or Telegram, ask for the number or chat naturally.
- Call request_channel_link (OTP). Tell them to open the other app, copy the code, and paste it here.
- When they paste a 6-digit code, call confirm_channel_link.
- Only use knowledge questions if OTP delivery failed.
- Never invent that accounts are linked without a successful confirm.

Mini pages:
- create_html_page returns page_url when PUBLIC_BASE_URL is set — share that link for browser notes.

Activities and assessments:
- Long-running or timed work should use durable activity/assessment tools, not only chat text.
- Timed interactions are server-authoritative: create them with real expiration; do not only write "⏱️ 3 seconds" in text.
- Interactive buttons must be real present_choices interactions — never tell the learner to tap options that were not delivered.

Research and currency:
- When freshness matters (news, prices, current policy, recent events), use research tools rather than pretending model knowledge is current.
- Distinguish model knowledge from retrieved sources; do not present research as if it were private memory.

Honesty:
- Do not invent memories, tool results, or past sessions.
- If memory is missing, say so briefly and continue helpfully.
- End or pause when more teaching would not help right now.
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
        name="schedule_at",
        description="Schedule a follow-up at an absolute datetime (ISO 8601). Optional IANA timezone.",
        parameters={
            "type": "object",
            "properties": {
                "execute_at": {"type": "string"},
                "timezone": {"type": "string"},
                "reason": {"type": "string"},
                "message_hint": {"type": "string"},
                "action_type": {"type": "string"},
            },
            "required": ["execute_at", "reason"],
        },
    ),
    ToolSpec(
        name="resolve_natural_time",
        description="Resolve 'tomorrow at 10am' etc. to absolute time using learner timezone and authoritative clock. Prefer this over guessing.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}, "timezone": {"type": "string"}},
            "required": ["text"],
        },
    ),
    ToolSpec(
        name="schedule_intent",
        description="Schedule a learner intention (reminder/review/followup). Stores intent to reassess at wake time — not fixed wording. Prefer when_text for natural language times.",
        parameters={
            "type": "object",
            "properties": {
                "purpose": {"type": "string"},
                "target": {"type": "string"},
                "when_text": {"type": "string"},
                "execute_at": {"type": "string"},
                "timezone": {"type": "string"},
                "flexibility": {"type": "string"},
                "completion_condition": {"type": "string"},
                "concept_key": {"type": "string"},
            },
            "required": ["target"],
        },
    ),
    ToolSpec(
        name="schedule_continuous",
        description="Schedule several future follow-ups. Use only when the person wants ongoing support.",
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
        name="schedule_series",
        description="Schedule a finite series of follow-ups (max 30) at a fixed interval.",
        parameters={
            "type": "object",
            "properties": {
                "interval_hours": {"type": "number"},
                "count": {"type": "number"},
                "delay_hours": {"type": "number"},
                "first_at": {"type": "string"},
                "reason": {"type": "string"},
                "message_hint": {"type": "string"},
            },
            "required": ["interval_hours", "reason"],
        },
    ),
    ToolSpec(
        name="cancel_schedule",
        description="Cancel a pending scheduled action by id.",
        parameters={
            "type": "object",
            "properties": {"scheduled_action_id": {"type": "string"}},
            "required": ["scheduled_action_id"],
        },
    ),
    ToolSpec(
        name="get_current_time",
        description="Get authoritative current UTC and optional learner timezone.",
        parameters={
            "type": "object",
            "properties": {"timezone": {"type": "string"}},
        },
    ),
    ToolSpec(
        name="get_learner_state",
        description="Read current goals, activities, pending choices, and upcoming schedule.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="check_quiet_hours",
        description="Check whether the learner is currently in quiet hours.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="set_preference",
        description="Store an explicit learner preference (message_length, timezone, language, etc.).",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string"}, "value": {}},
            "required": ["key", "value"],
        },
    ),
    ToolSpec(
        name="present_choices",
        description="Show interactive choices when helpful. Optional expires_in_seconds for timed choices.",
        parameters={
            "type": "object",
            "properties": {
                "style": {"type": "string"},
                "prompt": {"type": "string"},
                "list_button_label": {"type": "string"},
                "choices": {"type": "array"},
                "expires_in_seconds": {"type": "number"},
            },
            "required": ["choices"],
        },
    ),
    ToolSpec(
        name="create_artifact",
        description="Create a durable document (notes, study sheet, PDF). format=txt or pdf.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "format": {"type": "string"},
            },
            "required": ["kind", "title", "content"],
        },
    ),
    ToolSpec(
        name="create_html_page",
        description="Create a branded HTML study page; returns page_url when PUBLIC_BASE_URL is set.",
        parameters={
            "type": "object",
            "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
            "required": ["title", "content"],
        },
    ),
    ToolSpec(
        name="list_artifacts",
        description="List artifacts saved for this person.",
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
        name="redeliver_artifact",
        description="Re-send an existing artifact without regenerating content.",
        parameters={
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "channel": {"type": "string"},
                "target_external_id": {"type": "string"},
            },
            "required": ["artifact_id"],
        },
    ),
    ToolSpec(
        name="run_python",
        description="Run Python in the learner World (isolated). Prefer world_exec for general commands.",
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
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="read_workspace_file",
        description="Read a workspace file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="list_workspace",
        description="List files in the learner workspace.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
    ToolSpec(
        name="workspace_command",
        description="Deprecated alias for world_exec — runs a shell command line in the World (isolated).",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="world_discover",
        description=(
            "Inspect the learner's personal computing World: lifecycle, disk, "
            "runtimes, installed software (observed), files, jobs. Prefer this "
            "before assuming software is missing or present."
        ),
        parameters={
            "type": "object",
            "properties": {"sections": {"type": "string"}},
        },
    ),
    ToolSpec(
        name="world_exec",
        description=(
            "Run a command (argv list) or Python script inside the learner World "
            "under isolation. No developer command allowlist — infrastructure "
            "enforces mounts, resources, and network mode (none|pkg)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "script": {"type": "string"},
                "cwd": {"type": "string"},
                "network_mode": {"type": "string"},
                "budget_class": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="world_acquire",
        description=(
            "Install software into the World. kind=python_package uses pip into "
            "the world's private venv (never the WAX app). Always verified by import."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "name": {"type": "string"},
                "version_spec": {"type": "string"},
            },
            "required": ["name"],
        },
    ),
    ToolSpec(
        name="world_jobs",
        description="List recent World executions/jobs and lifecycle for this learner.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="world_files",
        description="List/read/write/delete files inside the learner World workspace.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="workspace_env",
        description="Get or update workspace environment manifest (packages/tools).",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "name": {"type": "string"},
                "version": {"type": "string"},
                "source": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="fetch_inbound_media",
        description="Fetch inbound media from the messaging channel into the workspace.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="inspect_media",
        description=(
            "Probe a local media file: kind, metadata, available capabilities, "
            "and optional text extraction (OCR/PDF). Does not transcribe audio. "
            "Use capabilities listed to decide next tools."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "extract_text": {"type": "boolean"},
                "max_pages": {"type": "number"},
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="describe_image",
        description="Optional vision description of a local image.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "question": {"type": "string"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="transcribe_audio",
        description=(
            "Transcribe a local audio/voice file. Returns transcript plus quality "
            "(usable/uncertain/unusable). Do not treat uncertain/unusable as reliable user text."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "language": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="extract_video_audio",
        description="Extract audio track from a local video to a WAV path. Then transcribe_audio if needed.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    ),
    ToolSpec(
        name="extract_video_frames",
        description="Extract a small number of frames from a local video (capped). Inspect/OCR/describe frames as needed.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_frames": {"type": "number"},
                "fps": {"type": "number"},
            },
        },
    ),
    ToolSpec(
        name="extract_subtitles",
        description="Extract embedded subtitles from a local video when a subtitle stream exists.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    ),
    ToolSpec(
        name="ingest_document",
        description="Store learner-provided notes/PDF/text as retrievable knowledge for this learner only.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "text": {"type": "string"},
                "path": {"type": "string"},
                "kind": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="inspect_memories",
        description="Inspect active memories for this learner.",
        parameters={"type": "object", "properties": {"limit": {"type": "number"}}},
    ),
    ToolSpec(
        name="forget_memory",
        description="Forget something when the learner asks.",
        parameters={
            "type": "object",
            "properties": {"memory_id": {"type": "string"}, "query": {"type": "string"}},
        },
    ),
    ToolSpec(
        name="manage_goal",
        description="Create or update a learning goal.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "title": {"type": "string"},
                "goal_id": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="start_activity",
        description="Start a durable learning activity.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "objective": {"type": "string"},
                "duration_seconds": {"type": "number"},
            },
            "required": ["kind"],
        },
    ),
    ToolSpec(
        name="complete_activity",
        description="Complete an activity.",
        parameters={
            "type": "object",
            "properties": {"activity_id": {"type": "string"}},
        },
    ),
    ToolSpec(
        name="pause_activity",
        description="Pause the active learning activity.",
        parameters={
            "type": "object",
            "properties": {
                "activity_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="resume_activity",
        description="Resume a paused learning activity.",
        parameters={
            "type": "object",
            "properties": {"activity_id": {"type": "string"}},
        },
    ),
    ToolSpec(
        name="create_assessment",
        description="Create a durable assessment (not a fixed quiz mode).",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "items": {"type": "array"},
                "timed": {"type": "boolean"},
                "duration_seconds": {"type": "number"},
            },
            "required": ["title", "items"],
        },
    ),
    ToolSpec(
        name="submit_assessment_answer",
        description="Submit an answer to the current assessment item.",
        parameters={
            "type": "object",
            "properties": {
                "attempt_id": {"type": "string"},
                "item_id": {"type": "string"},
                "response_text": {"type": "string"},
            },
            "required": ["attempt_id", "item_id"],
        },
    ),
    ToolSpec(
        name="record_assessment_timeout",
        description="Record that a timed assessment item expired and get the next item if any.",
        parameters={
            "type": "object",
            "properties": {
                "attempt_id": {"type": "string"},
                "item_id": {"type": "string"},
            },
            "required": ["attempt_id"],
        },
    ),
    ToolSpec(
        name="update_concept_state",
        description="Update observed mastery/confidence for a concept.",
        parameters={
            "type": "object",
            "properties": {
                "concept_key": {"type": "string"},
                "status": {"type": "string"},
                "mastery": {"type": "number"},
                "confidence": {"type": "number"},
            },
        },
    ),
    ToolSpec(
        name="record_evidence",
        description="Record evidence about the learner.",
        parameters={
            "type": "object",
            "properties": {
                "claim_key": {"type": "string"},
                "content": {"type": "string"},
                "strength": {"type": "number"},
            },
        },
    ),
    ToolSpec(
        name="form_hypothesis",
        description="Form or update a hypothesis about the learner.",
        parameters={
            "type": "object",
            "properties": {
                "claim_key": {"type": "string"},
                "statement": {"type": "string"},
                "confidence": {"type": "number"},
            },
        },
    ),
    ToolSpec(
        name="why_we_believe",
        description="Explain why we currently believe something about the learner.",
        parameters={
            "type": "object",
            "properties": {"claim_key": {"type": "string"}},
        },
    ),
    ToolSpec(
        name="propose_learning_check",
        description="Propose a light check related to an active hypothesis.",
        parameters={
            "type": "object",
            "properties": {"hypothesis_id": {"type": "string"}, "hint": {"type": "string"}},
        },
    ),
    ToolSpec(
        name="schedule_hypothesis_recheck",
        description="Schedule a future recheck for a hypothesis.",
        parameters={
            "type": "object",
            "properties": {
                "hypothesis_id": {"type": "string"},
                "delay_hours": {"type": "number"},
            },
        },
    ),
    ToolSpec(
        name="research_fetch",
        description="Fetch a public http(s) URL for world knowledge. Not learner memory.",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    ),
    ToolSpec(
        name="research_search",
        description="Search the web when a search provider is configured.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="request_channel_link",
        description=(
            "Start linking another channel (WhatsApp/Telegram). Prefer OTP: sends a 6-digit code "
            "to that number/chat. method=knowledge only if OTP cannot be delivered."
        ),
        parameters={
            "type": "object",
            "properties": {
                "target_channel": {"type": "string"},
                "target_external_id": {"type": "string"},
                "method": {"type": "string"},
                "questions": {"type": "array"},
            },
            "required": ["target_channel", "target_external_id"],
        },
    ),
    ToolSpec(
        name="confirm_channel_link",
        description="Confirm channel link when the learner pastes the OTP or knowledge answers.",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "challenge_id": {"type": "string"},
                "answers": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    ToolSpec(
        name="link_channel_identity",
        description="Directly attach a channel identity (admin/system). Prefer request_channel_link for learners.",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "external_id": {"type": "string"},
                "display_name": {"type": "string"},
                "make_primary": {"type": "boolean"},
            },
            "required": ["channel", "external_id"],
        },
    ),
    ToolSpec(
        name="export_learner_data",
        description="Export this learner's data package (memories, messages, goals, artifacts).",
        parameters={
            "type": "object",
            "properties": {"message_limit": {"type": "number"}},
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
        user_text = payload.get("text", "") or ""
        # Voice notes: prefer transcript over placeholder labels
        if payload.get("transcript") and (
            not user_text.strip()
            or user_text.strip().startswith("[audio")
            or user_text.strip() in ("[voice note]", "[audio received]")
        ):
            user_text = str(payload.get("transcript"))
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
                f"fetch_inbound_media if not on disk, then inspect_media to see capabilities.]"
            )
        if payload.get("local_media_path"):
            media_note += f"\n[System: media on disk at {payload.get('local_media_path')}]"
        probe = payload.get("media_probe") or {}
        caps = payload.get("media_capabilities") or probe.get("capability_list") or []
        if probe or caps:
            media_note += (
                f"\n[System: media kind={probe.get('kind', payload.get('content_type'))}; "
                f"capabilities={caps}. "
                f"Compose tools as needed (inspect_media, transcribe_audio, extract_video_audio, "
                f"extract_video_frames, describe_image, ingest_document). "
                f"Do not claim you processed media unless a tool succeeded.]"
            )
        tq = payload.get("transcript_quality")
        if payload.get("transcript") and tq == "usable":
            media_note += (
                f"\n[System: usable transcript available:\n{str(payload.get('transcript'))[:4000]}]"
            )
        elif payload.get("transcript") and tq in ("uncertain", "unusable"):
            media_note += (
                f"\n[System: transcription quality={tq}. "
                f"Do not treat as reliable user text. You may ask the learner to resend or type, "
                f"or call transcribe_audio again if appropriate. "
                f"Snippet: {str(payload.get('transcript'))[:500]}]"
            )
        elif payload.get("transcript"):
            media_note += (
                f"\n[System: transcript present (quality unknown):\n{str(payload.get('transcript'))[:2000]}]"
            )
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
            "local_media_path": payload.get("local_media_path"),
            "target_external_id": payload.get("target_external_id"),
        }

        # Tool loop: up to a few rounds so the model can act then respond
        reply_text = ""
        tool_notes: list[str] = []
        tool_results: list[dict] = []
        interactive_payload: dict | None = None
        agent = AgentRuntime(self.session, work)
        await agent.start()
        max_rounds = agent.max_rounds
        for _ in range(max_rounds):
            if not agent.can_call_tool() and _ > 0:
                # Budget exhausted — force a text reply next
                pass
            response = await self.intelligence.complete(
                CompletionRequest(
                    messages=llm_messages,
                    tools=AVAILABLE_TOOLS if agent.can_call_tool() else None,
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
                    parsed_args = args if isinstance(args, dict) else {}
                    outcome = await execute_tool(self.session, name, args, tool_ctx)
                    out_dict = outcome if isinstance(outcome, dict) else {"ok": False}
                    await agent.record_tool(name, parsed_args, out_dict)
                    tool_notes.append(f"{name}:{out_dict.get('ok')}")
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
            # Message.content stays canonical (channel-neutral).
            # Delivery.content is the channel-rendered form that will be sent.
            from wax.delivery.presentation import present_for_channel

            rendered = present_for_channel(reply_text, channel)
            delivery = Delivery(
                id=uuid4(),
                work_id=work.id,
                principal_id=principal_id,
                channel=channel,
                target_external_id=str(target),
                content=rendered,
                status="pending",
                idempotency_key=f"delivery:{work.id}",
                metadata_={"canonical_preview": reply_text[:500]},
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

        await agent.complete(reply_preview=reply_text)
        return {
            "reply": reply_text,
            "message_id": str(out_msg_id) if out_msg_id else None,
            "delivery_id": str(delivery_id) if delivery_id else None,
            "memories_used": getattr(ctx, "memories_used", 0),
            "tools": tool_results,
            "tool_notes": tool_notes,
            "interactive": interactive_payload,
            "execution_id": str(agent.execution.id) if agent.execution else None,
        }

    async def handle_scheduled_action(self, work: Work) -> dict[str, Any]:
        """Scheduled wake: rebuild Learner Model, reassess intent, then tutor decides."""
        payload = work.input_payload or {}
        principal_id = work.principal_id
        action_type = payload.get("action_type", "tutor_followup")
        reason = payload.get("reason") or ""
        hint = (payload.get("payload") or {}).get("message_hint") or reason
        inner = payload.get("payload") or {}

        decision = "deliver"
        decision_reason = "due"
        model_summary = ""
        if principal_id:
            try:
                from wax.learner.model import build_learner_model
                from wax.learner.temporal import TemporalService

                model = await build_learner_model(self.session, principal_id)
                model_summary = (
                    f"Goals: {[g.get('title') for g in model.goals[:3]]}\n"
                    f"Activity: {(model.activities[0] if model.activities else None)}\n"
                    f"Fragile: {[f.get('label') for f in model.fragile[:3]]}\n"
                    f"Prefs: {[p.get('content')[:80] for p in model.preferences[:3]]}\n"
                    f"Timezone: {model.timezone}\n"
                )
                # if this is a temporal_intent, evaluate
                if action_type == "temporal_intent" or payload.get("action_type") == "temporal_intent":
                    from wax.db.models import ScheduledAction, TemporalIntent
                    from sqlalchemy import select

                    intent = None
                    sa_id = payload.get("scheduled_action_id") or work.input_payload.get("scheduled_action_id")
                    # load by target match
                    stmt = (
                        select(TemporalIntent)
                        .where(
                            TemporalIntent.principal_id == principal_id,
                            TemporalIntent.status.in_(["scheduled", "due", "rescheduled"]),
                        )
                        .order_by(TemporalIntent.execute_at.asc())
                        .limit(5)
                    )
                    intents = list((await self.session.execute(stmt)).scalars().all())
                    for it in intents:
                        if it.target and reason and it.target[:80] in reason or (reason and reason[:80] in (it.target or "")):
                            intent = it
                            break
                    if not intent and intents:
                        intent = intents[0]
                    if intent:
                        ev = await TemporalService(self.session).evaluate_due_intent(
                            intent, model.to_dict()
                        )
                        decision = ev.get("decision") or "deliver"
                        decision_reason = ev.get("reason") or ""
                        if decision in ("suppress", "fulfilled"):
                            return {
                                "reply": None,
                                "action_type": action_type,
                                "decision": decision,
                                "reason": decision_reason,
                                "suppressed": True,
                            }
            except Exception:
                from wax.observability.logging import get_logger
                get_logger(__name__).exception("scheduled_learner_model_failed")
                model_summary = await self.memory.get_active_summary(principal_id)

        system = (
            TUTOR_SYSTEM
            + "\n\nThis is a scheduled wake. You must reassess whether the intention is still useful.\n"
            + f"Original reason: {reason}\nHint: {hint}\n"
            + f"Evaluation decision: {decision} ({decision_reason})\n\n"
            + "--- Learner model now ---\n"
            + (model_summary or "Sparse history.")
            + "\n\nIf the learner is deep in study and the reminder is non-urgent, acknowledge gently without derailing.\n"
            + "If the intention appears already completed, say almost nothing or skip.\n"
            + "Do not spam. Write a natural useful message only if still valuable.\n"
        )

        response = await self.intelligence.complete(
            CompletionRequest(
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(
                        role="user",
                        content="Compose the follow-up message for this learner now, using current learner state.",
                    ),
                ],
                temperature=0.6,
                max_tokens=1024,
            ),
            allow_fallback=True,
        )
        reply = (response.content or "").strip()
        return {
            "reply": reply,
            "action_type": action_type,
            "decision": decision,
            "reason": decision_reason,
        }
