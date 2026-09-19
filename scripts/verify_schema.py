#!/usr/bin/env python3
"""Fail the deploy if core tables (especially works) are missing.

Safe diagnostics only — never prints passwords or full DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


REQUIRED = (
    "works",
    "principals",
    "conversations",
    "messages",
    "executions",
    "deliveries",
    "memories",
    "scheduled_actions",
)


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
        print("verify_schema: DATABASE_URL not set", file=sys.stderr)
        return 2
    url = _to_asyncpg(raw)
    try:
        u = make_url(url)
        print(
            "verify_schema_target "
            f"driver={u.drivername} host={u.host} port={u.port or 5432} "
            f"database={u.database} user_set={bool(u.username)}"
        )
    except Exception as e:
        print(f"verify_schema: could not parse URL: {e}", file=sys.stderr)
        return 2

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rev = await conn.scalar(text(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ))
            print(f"verify_schema_alembic_revision={rev}")

            schema = await conn.scalar(text("SELECT current_schema()"))
            print(f"verify_schema_current_schema={schema}")

            rows = await conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            ))
            tables = [r[0] for r in rows]
            print(f"verify_schema_public_tables count={len(tables)} names={tables}")

            works = await conn.scalar(text("SELECT to_regclass('public.works')"))
            print(f"verify_schema_works={works}")

            missing = [t for t in REQUIRED if t not in tables]
            if missing:
                print(
                    f"verify_schema_FAILED missing={missing}",
                    file=sys.stderr,
                )
                return 1
            print("verify_schema_OK")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
