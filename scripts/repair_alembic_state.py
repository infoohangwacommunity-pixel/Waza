#!/usr/bin/env python3
"""Pre-upgrade Alembic state check for the canonical baseline era.

Active graph is only:
  001_waza_baseline (down_revision=None)

If alembic_version still references the archived 001–017 chain, or has
multiple rows, this script reports the conflict and refuses to silently
stamp when user tables still exist.

For intentional clean rebuild (documented in ops):
  DROP SCHEMA public CASCADE; CREATE SCHEMA public;
  then alembic upgrade head
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

BASELINE = "001_waza_baseline"
LEGACY_PREFIXES = (
    "001",
    "002",
    "003",
    "004",
    "005",
    "006",
    "007",
    "008",
    "009",
    "010",
    "011",
    "012",
    "013",
    "014",
    "015",
    "016",
    "017",
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


def _is_legacy(rev: str) -> bool:
    if rev == BASELINE:
        return False
    return rev.startswith(LEGACY_PREFIXES) or rev in LEGACY_PREFIXES


async def main() -> int:
    raw = (os.environ.get("DATABASE_URL") or "").strip()
    if not raw:
        print("repair_alembic: DATABASE_URL not set", file=sys.stderr)
        return 2

    url = _to_asyncpg(raw)
    try:
        u = make_url(url)
        print(
            "repair_alembic_target "
            f"driver={u.drivername} host={u.host} port={u.port or 5432} "
            f"database={u.database}"
        )
    except Exception as e:
        print(f"repair_alembic: url parse error: {e}", file=sys.stderr)
        return 2

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            exists = await conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='alembic_version'"
                )
            )
            if not exists:
                print("repair_alembic: no alembic_version — clean path for baseline upgrade")
                return 0

            rows = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).fetchall()
            versions = [r[0] for r in rows if r and r[0]]
            print(f"repair_alembic_current_rows={versions!r}")

            user_tables = [
                r[0]
                for r in (
                    await conn.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema='public' AND table_type='BASE TABLE' "
                            "AND table_name <> 'alembic_version' "
                            "ORDER BY table_name"
                        )
                    )
                ).fetchall()
            ]
            print(f"repair_alembic_user_table_count={len(user_tables)}")

            if len(versions) == 1 and versions[0] == BASELINE:
                print("repair_alembic: already on 001_waza_baseline")
                return 0

            if len(versions) > 1:
                print(
                    "repair_alembic_FAILED multiple alembic_version rows "
                    f"{versions!r}. Clean rebuild required: "
                    "DROP SCHEMA public CASCADE; CREATE SCHEMA public; "
                    "then redeploy so alembic upgrade head applies 001_waza_baseline.",
                    file=sys.stderr,
                )
                return 1

            if versions and _is_legacy(versions[0]):
                if user_tables:
                    print(
                        "repair_alembic_FAILED database still has legacy revision "
                        f"{versions[0]!r} and {len(user_tables)} user tables. "
                        "This environment is configured for a clean baseline rebuild. "
                        "Run against the intended Railway Postgres only:\n"
                        "  DROP SCHEMA public CASCADE;\n"
                        "  CREATE SCHEMA public;\n"
                        "Then redeploy Web so startup runs alembic upgrade head.",
                        file=sys.stderr,
                    )
                    return 1
                # Empty of user tables — clear legacy version so baseline can apply
                await conn.execute(text("DELETE FROM alembic_version"))
                print(
                    f"repair_alembic: cleared legacy revision {versions[0]!r} "
                    "(no user tables); baseline upgrade may proceed"
                )
                return 0

            if versions and versions[0] != BASELINE:
                print(
                    f"repair_alembic_FAILED unknown revision {versions[0]!r}",
                    file=sys.stderr,
                )
                return 1

            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
