"""Privacy / data export — student-owned data package (JSON).

Does not include secrets or other tenants.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import (
    Artifact,
    Conversation,
    Goal,
    InterfaceIdentity,
    Memory,
    Message,
    Principal,
    ScheduledAction,
)
from wax.observability.logging import get_logger

logger = get_logger(__name__)


async def export_principal_package(
    session: AsyncSession, principal_id: UUID | str, *, message_limit: int = 200
) -> dict[str, Any]:
    pid = principal_id if isinstance(principal_id, UUID) else UUID(str(principal_id))
    principal = await session.get(Principal, pid)
    if not principal:
        return {"ok": False, "error": "not_found"}

    identities = list(
        (await session.execute(select(InterfaceIdentity).where(InterfaceIdentity.principal_id == pid)))
        .scalars()
        .all()
    )
    conversations = list(
        (await session.execute(select(Conversation).where(Conversation.principal_id == pid)))
        .scalars()
        .all()
    )
    mems = list(
        (
            await session.execute(
                select(Memory)
                .where(Memory.principal_id == pid)
                .order_by(Memory.updated_at.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    goals = list(
        (await session.execute(select(Goal).where(Goal.principal_id == pid))).scalars().all()
    )
    artifacts = list(
        (await session.execute(select(Artifact).where(Artifact.principal_id == pid))).scalars().all()
    )
    scheduled = list(
        (
            await session.execute(
                select(ScheduledAction)
                .where(ScheduledAction.principal_id == pid)
                .order_by(ScheduledAction.execute_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )

    messages: list[dict[str, Any]] = []
    for conv in conversations[:20]:
        rows = list(
            (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.desc())
                    .limit(message_limit)
                )
            )
            .scalars()
            .all()
        )
        for m in reversed(rows):
            messages.append(
                {
                    "id": str(m.id),
                    "conversation_id": str(conv.id),
                    "role": m.role,
                    "direction": m.direction,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
            )

    package = {
        "ok": True,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "principal": {
            "id": str(principal.id),
            "display_name": principal.display_name,
            "preferences": principal.preferences or {},
        },
        "identities": [
            {"channel": i.channel, "external_id": i.external_id, "is_primary": i.is_primary}
            for i in identities
        ],
        "memories": [
            {
                "id": str(m.id),
                "memory_type": m.memory_type,
                "content": m.content,
                "active": getattr(m, "is_active", True),
            }
            for m in mems
        ],
        "goals": [{"id": str(g.id), "title": getattr(g, "title", None), "status": getattr(g, "status", None)} for g in goals],
        "artifacts": [{"id": str(a.id), "kind": getattr(a, "kind", None)} for a in artifacts],
        "scheduled_actions": [
            {
                "id": str(s.id),
                "execute_at": s.execute_at.isoformat() if s.execute_at else None,
                "status": s.status,
            }
            for s in scheduled
        ],
        "messages": messages,
        "note": "Export is for this student only. Secrets and other tenants are excluded.",
    }
    logger.info("student_data_exported", principal_id=str(pid))
    return package
