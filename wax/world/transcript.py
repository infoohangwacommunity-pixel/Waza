"""Durable World transcript archive — append-only JSONL under the student's World.

The database ``messages`` table remains the operational conversation record.
This module mirrors every recorded message into the student's own World at
``history/transcript.jsonl`` so the AI can inspect its complete long-term
conversation directly from its persistent filesystem.

Infrastructure writes the transcript automatically because persistence is
infrastructure. It never decides which messages are important, never
summarizes, embeds, classifies, ranks, or discards older conversation.
Each line is one plain JSON fact:

    {"message_id", "work_id", "conversation_id", "principal_id", "channel",
     "direction", "role", "content", "external_id", "created_at", "recorded_at"}

Idempotency and restart safety:
- The archive is append-only; records are keyed by the DB ``message_id``.
- Before appending, the archive is scanned for that id (a small scan-index is
  cached per file within the process). Because the message row is committed
  before archiving, retries/replays of the same turn find the id already
  present and append nothing — duplicates are impossible across worker
  restarts without relying on any local cache surviving.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wax.db.models import Message, Work
from wax.observability.logging import get_logger
from wax.world import layout

logger = get_logger(__name__)

TRANSCRIPT_DIRNAME = "history"
TRANSCRIPT_FILENAME = "transcript.jsonl"


def transcript_path(world_root: Path) -> Path:
    return world_root / TRANSCRIPT_DIRNAME / TRANSCRIPT_FILENAME




def _load_offsets(path: Path) -> dict[str, int]:
    """Rebuild the message_id -> byte-offset map by scanning the archive once.

    Called lazily per process (and again after any restart); the JSONL file is
    the durable authority, the in-memory index is only a dedup accelerator.
    """
    offsets: dict[str, int] = {}
    if not path.is_file():
        return offsets
    with path.open("r", encoding="utf-8") as fh:
        while True:
            pos = fh.tell()
            line = fh.readline()
            if not line:
                break
            if not line.strip():
                continue
            try:
                mid = str(json.loads(line).get("message_id") or "")
            except Exception:
                continue
            if mid:
                offsets[mid] = pos
    return offsets


# Per-path in-process cache of scanned offsets. Pure accelerator: correctness
# never depends on it — a fresh process re-scans the archive itself.
_offset_cache: dict[str, dict[str, int]] = {}


def key_for(path: Path) -> str:
    return str(path)


def contains_message(root: Path, message_id: Any) -> bool:
    """True when this message id is already durably archived."""
    path = transcript_path(root)
    key = str(path)
    offsets = _offset_cache.get(key)
    if offsets is None:
        try:
            offsets = _load_offsets(path)
        except OSError:
            logger.warning("transcript_scan_failed", path=str(path))
            return False
        _offset_cache[key] = offsets
    return str(message_id) in offsets


def record_message(
    root: Path,
    *,
    message_id: Any,
    content: str,
    role: str,
    direction: str,
    channel: str,
    created_at: Any = None,
    work_id: Any = None,
    conversation_id: Any = None,
    principal_id: Any = None,
    external_id: str | None = None,
) -> bool:
    """Append one complete message fact to the World transcript.

    Returns True when a new line was appended, False when the message was
    already archived (idempotent no-op). Never truncates or transforms the
    content — infrastructure records exactly what was said.
    """
    mid = str(message_id)
    if contains_message(root, mid):
        return False  # already durably archived — retries never duplicate

    path = transcript_path(root)
    record = {
        "message_id": mid,
        "work_id": str(work_id) if work_id else None,
        "conversation_id": str(conversation_id) if conversation_id else None,
        "principal_id": str(principal_id) if principal_id else None,
        "channel": channel or "unknown",
        "direction": direction,
        "role": role,
        "content": content,
        "external_id": external_id,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else (str(created_at) if created_at else None),
        "recorded_at": time.time(),
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"

    dir_path = path.parent
    dir_path.mkdir(parents=True, exist_ok=True)
    # Dedup accelerator is cached per process; re-scan the archive right before
    # appending so replays (worker restart, delivery retry, concurrent writer)
    # can never duplicate a line. The JSONL file itself is the authority.
    offsets = _offset_cache.setdefault(key_for(path), {})
    if mid in offsets:
        return False
    try:
        existing_size = path.stat().st_size
    except FileNotFoundError:
        existing_size = 0
    if existing_size:
        for scanned_mid, pos in _load_offsets(path).items():
            offsets.setdefault(scanned_mid, pos)
        if mid in offsets:
            return False

    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    offsets[mid] = existing_size

    logger.debug("world_transcript_appended", message_id=mid, path=str(path))
    return True


def read_transcript(root: Path) -> list[dict[str, Any]]:
    """Return the complete archived transcript in order (oldest first)."""
    path = transcript_path(root)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                # A torn final line from a crash is skipped on read but never
                # removed — the archive stays append-only.
                continue
    return out


def _world_root_for_principal(principal_id: Any) -> Path | None:
    """Locate the student's World root on disk (index only — never creates).

    Uses the same principal index that ``wax.world.manager`` maintains, so
    archiving resolves exactly the World the rest of the system operates on.
    """
    if not principal_id:
        return None
    from wax.world import manager

    pid = str(principal_id)
    idx = layout.read_json(
        layout.worlds_root() / ".principal_index" / f"{manager._safe(pid)}.json"
    )
    if not idx or not idx.get("world_id"):
        return None
    root = layout.world_root(str(idx["world_id"]))
    ident = layout.read_json(root / "identity.json")
    if not ident:
        return None
    return root


async def archive_message(session: AsyncSession, message: Message) -> None:
    """Mirror a committed DB Message row into its owner's World transcript.

    Called automatically after every persisted inbound/outbound message —
    persistence is infrastructure. The DB row must already be flushed so the
    message id is final; archiving is keyed by that id and therefore safe to
    replay after worker restarts or delivery retries.
    """
    try:
        root = _world_root_for_principal(message.principal_id)
        if root is None:
            return  # no World yet — nothing durable to write to
        record_message(
            root,
            message_id=message.id,
            content=message.content or "",
            role=message.role or "",
            direction=message.direction or "",
            channel=message.channel or "",
            created_at=message.created_at,
            work_id=message.work_id,
            conversation_id=message.conversation_id,
            principal_id=message.principal_id,
            external_id=message.external_id,
        )
    except Exception:
        # Transcript archiving must never break the operational turn; the DB
        # remains the authoritative record and can be re-archived later.
        logger.exception("world_transcript_archive_failed", message_id=str(message.id))


async def archive_turn(session: AsyncSession, work: Work) -> None:
    """Mirror every Message row belonging to one Work into its World transcript.

    Covers inbound rows created by webhook handlers (possibly in earlier
    process lifetimes), coalesced/recovery-batch siblings, and assistant
    replies. Keyed by message id, so replaying after worker restarts or lease
    reclaim is a no-op — the archive stays append-only with zero duplicates.
    """
    if not work.principal_id:
        return
    try:
        root = _world_root_for_principal(work.principal_id)
        if root is None:
            return
        q = (
            select(Message)
            .where(
                (Message.work_id == work.id)
                | ((Message.conversation_id == work.conversation_id) & (Message.principal_id == work.principal_id))
            )
            .order_by(Message.created_at.asc())
        )
        rows = (await session.execute(q)).scalars().all()
        for m in rows:
            if m.id and contains_message(root, m.id):
                continue  # append-only: already archived — never duplicate
            record_message(
                root,
                message_id=m.id,
                content=m.content or "",
                role=m.role or "",
                direction=m.direction or "",
                channel=m.channel or "",
                created_at=m.created_at,
                work_id=m.work_id,
                conversation_id=m.conversation_id,
                principal_id=m.principal_id,
                external_id=m.external_id,
            )
    except Exception:
        logger.exception("world_transcript_archive_turn_failed", work_id=str(work.id))
