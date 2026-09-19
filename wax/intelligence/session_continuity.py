"""
Long-session continuity (100+ messages, hour-long chats).

Strategy:
- Keep recent turns fully
- Maintain a rolling conversation summary on Conversation.summary
- Periodically extract episodic digests into Memory
- Never rely on stuffing the entire history into one prompt
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Conversation, Memory, Message
from wax.intelligence.providers import ChatMessage, CompletionRequest, get_intelligence
from wax.observability.logging import get_logger

logger = get_logger(__name__)

DIGEST_SYSTEM = """You summarize a tutoring conversation segment for future continuity.
Write 5-12 dense bullet points: goals, concepts covered, mistakes, preferences, open threads.
No fluff. Pure plain text bullets.
"""


class SessionContinuityService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.intelligence = get_intelligence()

    async def message_count(self, conversation_id) -> int:
        if not conversation_id:
            return 0
        stmt = select(func.count()).select_from(Message).where(
            Message.conversation_id == conversation_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)

    async def maybe_digest(
        self,
        *,
        principal_id,
        conversation_id,
        every_n: int = 20,
    ) -> bool:
        """
        Every N messages, compress older turns into summary + durable episodic memory.
        Safe to fail — tutor continues either way.
        """
        try:
            count = await self.message_count(conversation_id)
            if count < every_n or count % every_n != 0:
                return False

            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(every_n + 8)
            )
            result = await self.session.execute(stmt)
            msgs = list(reversed(result.scalars().all()))
            if len(msgs) < 8:
                return False

            lines = [f"{m.role}: {m.content[:500]}" for m in msgs]
            transcript = "\n".join(lines)
            response = await self.intelligence.complete(
                CompletionRequest(
                    messages=[
                        ChatMessage(role="system", content=DIGEST_SYSTEM),
                        ChatMessage(role="user", content=transcript[:12000]),
                    ],
                    temperature=0.2,
                    max_tokens=800,
                ),
                use_memory_model=True,
                allow_fallback=True,
            )
            digest = (response.content or "").strip()
            if not digest:
                return False

            conv = await self.session.get(Conversation, conversation_id)
            if conv:
                prev = conv.summary or ""
                conv.summary = (digest + "\n\n" + prev)[:4000]

            mem = Memory(
                id=uuid4(),
                principal_id=principal_id,
                memory_type="episodic",
                content=f"Session digest ({count} messages): {digest[:1500]}",
                confidence=0.7,
                importance=0.65,
                source="observed",
                evidence=[{"kind": "session_digest", "message_count": count}],
                is_active=True,
                tags=["session_digest", "continuity"],
            )
            self.session.add(mem)
            await self.session.flush()
            logger.info(
                "session_digest_created",
                conversation_id=str(conversation_id),
                message_count=count,
            )
            return True
        except Exception as e:
            logger.error("session_digest_failed", error=str(e))
            return False
