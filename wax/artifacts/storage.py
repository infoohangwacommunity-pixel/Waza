"""Durable artifact storage — not the temporary workspace TTL path."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from wax.config import get_settings

settings = get_settings()


def artifact_root() -> Path:
    root = Path(os.environ.get("WAX_ARTIFACT_ROOT") or "/tmp/wax-artifacts")
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_bytes(principal_id: Any, filename: str, data: bytes) -> str:
    safe = str(principal_id).replace("/", "_")[:64]
    dest_dir = artifact_root() / safe
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = "".join(c for c in filename if c.isalnum() or c in "._-")[:120] or f"a-{uuid4().hex[:8]}"
    path = dest_dir / f"{uuid4().hex[:8]}-{name}"
    path.write_bytes(data)
    return str(path)


def read_bytes(path: str) -> bytes:
    return Path(path).read_bytes()
