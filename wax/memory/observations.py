"""
Observation pipeline.

Every interaction can produce observations.
Only meaningful ones become durable memories (via extraction/consolidation).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Observation
from wax.observability.logging import get_logger

logger = get_logger(__name__)


class ObservationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        *,
        principal_id,
        kind: str,
        content: str,
        weight: float = 0.5,
        conversation_id=None,
        message_id=None,
        work_id=None,
        structured: dict[str, Any] | None = None,
    ) -> Observation:
        obs = Observation(
            id=uuid4(),
            principal_id=principal_id,
            conversation_id=conversation_id,
            message_id=message_id,
            work_id=work_id,
            kind=kind[:80],
            content=content[:4000],
            weight=weight,
            structured=structured or {},
        )
        self.session.add(obs)
        await self.session.flush()
        return obs
