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

HEAD = "001_reality"

# Durable-reality tables only (must match wax/db/models.py)
REQUIRED_TABLES = (
    "principals",
    "interface_identities",
    "worlds",
    "conversations",
    "messages",
    "inbound_events",
    "works",
    "executions",
    "deliveries",
    "memories",
    "artifacts",
    "scheduled_actions",
    "interactions",
    "channel_link_challenges",
    "surfaces",
    "surface_revisions",
    "surface_sessions",
    "surface_events",
    "surface_ai_requests",
    "principal_workloads",
    "alembic_version",
)

# Must not exist (deleted architecture)
FORBIDDEN_TABLES = (
    "goals",
    "memory_episodes",
    "memory_links",
    "tool_executions",
    "activities",
    "assessments",
    "assessment_items",
    "assessment_attempts",
    "assessment_responses",
    "publications",
    "learning_events",
    "concepts",
    "evidence",
    "hypotheses",
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
                    f"verify_schema_FAILED multiple alembic_version rows: {revs!r}",
                    file=sys.stderr,
                )
                return 1
            rev = revs[0] if revs else None
            print(f"verify_schema_alembic_revision={rev}")
            if rev != HEAD:
                print(
                    f"verify_schema_FAILED expected {HEAD!r} got {rev!r}",
                    file=sys.stderr,
                )
                return 1

            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE'"
                )
            )
            existing = {r[0] for r in rows.fetchall()}
            missing = [t for t in REQUIRED_TABLES if t not in existing]
            if missing:
                print(f"verify_schema_FAILED missing_tables={missing}", file=sys.stderr)
                return 1
            forbidden = [t for t in FORBIDDEN_TABLES if t in existing]
            if forbidden:
                print(
                    f"verify_schema_FAILED forbidden_legacy_tables={forbidden}",
                    file=sys.stderr,
                )
                return 1

            # Ownership: principal-owned tables must CASCADE on principal delete
            fk_rows = await conn.execute(
                text(
                    """
                    SELECT tc.table_name, kcu.column_name, ccu.table_name AS foreign_table,
                           rc.delete_rule
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                      ON ccu.constraint_name = tc.constraint_name
                    JOIN information_schema.referential_constraints AS rc
                      ON rc.constraint_name = tc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema = 'public'
                      AND ccu.table_name = 'principals'
                      AND kcu.column_name = 'principal_id'
                    """
                )
            )
            bad_cascade = []
            for table, col, ftable, rule in fk_rows.fetchall():
                if rule not in ("CASCADE",):
                    bad_cascade.append((table, col, rule))
            if bad_cascade:
                print(
                    f"verify_schema_FAILED principal_id FKs must CASCADE: {bad_cascade}",
                    file=sys.stderr,
                )
                return 1

            print("verify_schema_ok required_tables present, no forbidden tables, principal CASCADE ok")
            return 0
    except Exception as e:
        print(f"verify_schema_FAILED {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
