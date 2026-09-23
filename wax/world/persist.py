"""Postgres is authoritative for World identity + lifecycle.

Filesystem JSON is a recovery representation only.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import World as WorldRow
from wax.observability.logging import get_logger
from wax.world.manager import World

logger = get_logger(__name__)


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def upsert_world(session: AsyncSession, world: World) -> WorldRow:
    wid = _as_uuid(world.world_id)
    row = await session.get(WorldRow, wid)
    if row is None:
        # look up by principal
        try:
            pid = _as_uuid(world.principal_id)
        except Exception:
            pid = None
        if pid is not None:
            r = await session.execute(select(WorldRow).where(WorldRow.principal_id == pid))
            row = r.scalar_one_or_none()
        if row is None and pid is not None:
            row = WorldRow(
                id=wid,
                principal_id=pid,
                lifecycle_state=world.lifecycle,
                lifecycle_reason=world.lifecycle_reason or None,
                schema_version=world.schema_version,
                root_path=str(world.root),
            )
            session.add(row)
            await session.flush()
            logger.info("world_row_created", world_id=str(wid), principal_id=str(pid))
            return row
    if row is not None:
        row.lifecycle_state = world.lifecycle
        row.lifecycle_reason = world.lifecycle_reason or None
        row.schema_version = world.schema_version
        row.root_path = str(world.root)
        await session.flush()
    return row  # type: ignore[return-value]


async def apply_lifecycle(
    session: AsyncSession,
    world_id: str,
    state: str,
    reason: str = "",
    *,
    disk_used: int | None = None,
    env_size: int | None = None,
) -> None:
    try:
        wid = _as_uuid(world_id)
    except Exception:
        return
    row = await session.get(WorldRow, wid)
    if row is None:
        return
    row.lifecycle_state = state
    row.lifecycle_reason = reason or None
    if disk_used is not None:
        row.disk_used_bytes = disk_used
    if env_size is not None:
        row.env_size_bytes = env_size
    await session.flush()


async def load_lifecycle(session: AsyncSession, principal_id: str) -> dict[str, Any] | None:
    try:
        pid = _as_uuid(principal_id)
    except Exception:
        return None
    r = await session.execute(select(WorldRow).where(WorldRow.principal_id == pid))
    row = r.scalar_one_or_none()
    if not row:
        return None
    return {
        "world_id": str(row.id),
        "principal_id": str(row.principal_id),
        "lifecycle_state": row.lifecycle_state,
        "lifecycle_reason": row.lifecycle_reason,
        "schema_version": row.schema_version,
        "root_path": row.root_path,
        "disk_used_bytes": row.disk_used_bytes,
        "env_size_bytes": row.env_size_bytes,
    }
