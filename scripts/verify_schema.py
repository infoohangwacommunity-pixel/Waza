#!/usr/bin/env python3
"""Fail with exit code 1 if required production schema is missing.

Safe diagnostics only — never prints passwords or full DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


REQUIRED_TABLES = (
    "works",
    "inbound_events",
    "principals",
    "conversations",
    "messages",
    "executions",
    "deliveries",
    "memories",
    "scheduled_actions",
    "alembic_version",
    "publications",
    "principal_workloads",
    "learning_events",
    "surfaces",
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
            revs = [
                r[0]
                for r in (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).fetchall()
            ]
            print(f"verify_schema_alembic_revisions={revs!r}")
            if len(revs) > 1:
                print(
                    "verify_schema_FAILED multiple alembic_version rows "
                    f"(overlap risk): {revs!r}",
                    file=sys.stderr,
                )
                return 1
            rev = revs[0] if revs else None
            print(f"verify_schema_alembic_revision={rev}")
            if not rev:
                print(
                    "verify_schema_FAILED alembic_version empty or missing",
                    file=sys.stderr,
                )
                return 1

            schema = await conn.scalar(text("SELECT current_schema()"))
            print(f"verify_schema_current_schema={schema}")

            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            )
            tables = [r[0] for r in rows]
            print(
                f"verify_schema_public_tables count={len(tables)} names={tables}"
            )

            works = await conn.scalar(text("SELECT to_regclass('public.works')"))
            inbound = await conn.scalar(
                text("SELECT to_regclass('public.inbound_events')")
            )
            print(f"verify_schema_works={works}")
            print(f"verify_schema_inbound_events={inbound}")

            missing = [t for t in REQUIRED_TABLES if t not in tables]
            if missing:
                print(f"verify_schema_FAILED missing={missing}", file=sys.stderr)
                return 1
            if works is None or inbound is None:
                print(
                    "verify_schema_FAILED to_regclass null for works or inbound_events",
                    file=sys.stderr,
                )
                return 1

            print("verify_schema_OK")
            return 0
    except Exception as e:
        print(f"verify_schema_FAILED error={type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
