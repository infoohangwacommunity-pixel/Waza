#!/usr/bin/env python3
"""Align alembic_version with the single baseline 001_reality.

Active graph:
  001_reality (down_revision=None)

If the database already has application tables but alembic_version points at
a deleted historical revision (001_waza_baseline … 007), stamp 001_reality
so deploy can proceed without re-creating tables.

Empty database: do nothing — alembic upgrade head creates schema.
Intentional wipe: DROP SCHEMA public CASCADE; CREATE SCHEMA public; then upgrade.
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

HEAD = "001_reality"
# Historical revision ids that no longer exist as files
LEGACY = {
    "001_waza_baseline",
    "002_memory_graph",
    "003_retire_educational",
    "004_drop_tool_executions",
    "004",
    "005",
    "006",
    "007",
}


def _to_asyncpg(raw: str) -> str:
    if "+asyncpg" in raw.split("://", 1)[0]:
        return raw
    u = make_url(raw)
    base = u.drivername.split("+")[0]
    if base in ("postgres", "postgresql"):
        u = u.set(drivername="postgresql+asyncpg")
        return u.render_as_string(hide_password=False)
    return raw


async def main() -> int:
    raw = (os.environ.get("DATABASE_URL") or "").strip()
    if not raw:
        print("repair_alembic: DATABASE_URL not set", file=sys.stderr)
        return 0
    url = _to_asyncpg(raw)
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            has_av = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_name='alembic_version'"
                    )
                )
            ).scalar() is not None
            if not has_av:
                print("repair_alembic: no alembic_version — clean path for upgrade")
                return 0
            rows = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).fetchall()
            versions = [r[0] for r in rows]
            print(f"repair_alembic_current={versions!r}")
            user_tables = (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_type='BASE TABLE' "
                        "AND table_name <> 'alembic_version' "
                        "ORDER BY 1"
                    )
                )
            ).fetchall()
            names = [r[0] for r in user_tables]
            print(f"repair_alembic_user_tables={len(names)}")

            if not versions:
                if names:
                    await conn.execute(
                        text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                        {"v": HEAD},
                    )
                    print(f"repair_alembic: stamped {HEAD} (tables present, empty version)")
                return 0

            if len(versions) > 1:
                # collapse to head
                await conn.execute(text("DELETE FROM alembic_version"))
                await conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                    {"v": HEAD},
                )
                print(f"repair_alembic: collapsed multi-head → {HEAD}")
                return 0

            cur = versions[0]
            if cur == HEAD:
                print("repair_alembic: already at head")
                return 0
            if cur in LEGACY or cur.startswith("001") or cur.startswith("00"):
                if names:
                    await conn.execute(text("DELETE FROM alembic_version"))
                    await conn.execute(
                        text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                        {"v": HEAD},
                    )
                    print(f"repair_alembic: stamped {HEAD} (was legacy {cur!r})")
                    return 0
                # legacy version, no tables — clear so upgrade creates schema
                await conn.execute(text("DELETE FROM alembic_version"))
                print(f"repair_alembic: cleared orphan legacy {cur!r}")
                return 0

            print(f"repair_alembic_FAILED unknown revision {cur!r}", file=sys.stderr)
            return 1
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
