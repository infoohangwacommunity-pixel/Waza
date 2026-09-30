"""Materialize inbound student reality into the student's World.

Infrastructure only: place facts and files the AI can inspect.
No transcription, OCR, classification, summarization, or type routing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Message, Work
from wax.observability.logging import get_logger
from wax.world.manager import get_or_create_world
from wax.world.stage import principal_workspace
from wax.world.transcript import archive_message, record_message, contains_message

logger = get_logger(__name__)


def _inbound_dir(world_root: Path) -> Path:
    d = world_root / "workspace" / "inbound"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_inbound_fact(world_root: Path, work_id: str, fact: dict[str, Any]) -> str:
    """Atomic write of one inbound fact JSON (tmp + replace)."""
    dest = _inbound_dir(world_root) / f"{work_id}.json"
    tmp = dest.with_suffix(".json.tmp")
    payload = {
        **fact,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(dest)
    return str(dest)


async def materialize_inbound_reality(session: AsyncSession, work: Work) -> dict[str, Any]:
    """
    Ensure this Work's student input exists as durable World reality before AI runs.

    - Ensures World exists
    - Stages channel media into World (caller may already have done download)
    - Writes workspace/inbound/{work_id}.json with factual fields only
    - Archives inbound Message rows into history/transcript.jsonl

    Returns paths/facts for the tutor prompt (no interpretation).
    """
    out: dict[str, Any] = {"ok": False}
    if not work.principal_id:
        return {"ok": False, "error": "no_principal"}

    world = get_or_create_world(str(work.principal_id))
    root = world.root
    payload = dict(work.input_payload or {})

    media_path = payload.get("local_media_path") or payload.get("principal_media_path")
    media_note = None
    if media_path and Path(str(media_path)).is_file():
        media_note = {
            "path": str(media_path),
            "mime": payload.get("media_mime"),
            "size": payload.get("media_size"),
        }

    fact = {
        "work_id": str(work.id),
        "principal_id": str(work.principal_id),
        "channel": payload.get("channel"),
        "external_id": payload.get("external_id"),
        "message_id": payload.get("message_id"),
        "content_type": payload.get("content_type") or "text",
        "text": payload.get("text") or payload.get("user_text") or "",
        "media": media_note,
        "interactive_id": payload.get("interactive_id"),
    }
    fact_path = write_inbound_fact(root, str(work.id), fact)
    out["inbound_fact_path"] = fact_path
    out["world_root"] = str(root)
    if media_note:
        out["media_path"] = media_note["path"]

    # Link inbound messages: by message_id in payload and recent conversation inbound
    message_id = payload.get("message_id")
    archived = 0
    if message_id:
        try:
            from uuid import UUID

            mid = UUID(str(message_id))
            msg = await session.get(Message, mid)
            if msg is not None:
                if msg.work_id is None:
                    msg.work_id = work.id
                    await session.flush()
                await archive_message(session, msg)
                archived += 1
        except Exception:
            logger.exception("inbound_archive_message_failed", work_id=str(work.id))

    # Any other inbound messages already tied to this work
    try:
        rows = (
            await session.execute(
                select(Message).where(
                    Message.work_id == work.id,
                    Message.direction == "inbound",
                )
            )
        ).scalars().all()
        for m in rows:
            await archive_message(session, m)
            archived += 1
    except Exception:
        logger.exception("inbound_archive_work_messages_failed", work_id=str(work.id))

    out["ok"] = True
    out["archived_inbound"] = archived
    logger.info(
        "inbound_reality_materialized",
        work_id=str(work.id),
        world_root=str(root),
        fact_path=fact_path,
        has_media=bool(media_note),
        archived=archived,
    )
    return out
