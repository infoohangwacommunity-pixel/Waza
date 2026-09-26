#!/usr/bin/env python3
"""Normalize alembic_version before upgrade when production has multi-head or stale IDs.

Railway error seen:
  Requested revision 017_principal_workloads overlaps with other requested
  revisions 009_channel_link_challenges

That almost always means alembic_version has multiple rows (or a stale short
revision id), not a broken linear graph in source.

Safe rules:
- Never DROP user data.
- Never invent schema.
- Map known renamed revision ids.
- If multiple version rows, keep the single farthest revision on the known
  linear chain (or stamp HEAD if 017 schema objects already exist).
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

# Canonical linear chain (must match alembic/versions)
CHAIN = [
    "001",
    "002",
    "003",
    "004",
    "005",
    "006",
    "007",
    "008",
    "009_channel_link_challenges",
    "010_worlds",
    "011_learner_intelligence",
    "012_learner_intelligence_cleanup",
    "013_publications",
    "014_web_surfaces",
    "015_surface_ai_loop",
    "016_surface_hardening",
    "017_principal_workloads",
]
HEAD = CHAIN[-1]
ORDER = {r: i for i, r in enumerate(CHAIN)}

# Historical short ids that were renamed in-repo
ALIASES = {
    "009": "009_channel_link_challenges",
    "010": "010_worlds",
    "011": "011_learner_intelligence",
    "012": "012_learner_intelligence_cleanup",
    "013": "013_publications",
    "014": "014_web_surfaces",
    "015": "015_surface_ai_loop",
    "016": "016_surface_hardening",
    "017": "017_principal_workloads",
}

HEAD_MARKERS = (
    "principal_workloads",
    "publications",
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


def _normalize(rev: str | None) -> str | None:
    if not rev:
        return None
    rev = rev.strip()
    return ALIASES.get(rev, rev)


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
            # Ensure table exists
            exists = await conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='alembic_version'"
                )
            )
            if not exists:
                print("repair_alembic: alembic_version missing — leaving for upgrade")
                return 0

            rows = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).fetchall()
            versions = [r[0] for r in rows if r and r[0]]
            print(f"repair_alembic_current_rows={versions!r}")

            # Detect HEAD schema markers
            present = {}
            for tname in HEAD_MARKERS:
                present[tname] = bool(
                    await conn.scalar(
                        text(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema='public' AND table_name=:t"
                        ),
                        {"t": tname},
                    )
                )
            print(f"repair_alembic_schema_markers={present}")

            normalized = []
            for v in versions:
                n = _normalize(v)
                if n not in ORDER:
                    print(f"repair_alembic_WARN unknown_revision={v!r} normalized={n!r}")
                normalized.append(n)

            # Already clean single head
            if len(versions) == 1 and normalized[0] == versions[0] and normalized[0] in ORDER:
                if normalized[0] == HEAD or not all(present.values()):
                    print(f"repair_alembic: single revision ok ({normalized[0]})")
                    return 0
                # single non-head, fine — upgrade will advance
                print(f"repair_alembic: single revision {normalized[0]} — upgrade may proceed")
                return 0

            # Alias-only fix (one row, old short id)
            if len(versions) == 1 and normalized[0] != versions[0] and normalized[0] in ORDER:
                print(
                    f"repair_alembic: renaming version {versions[0]!r} -> {normalized[0]!r}"
                )
                await conn.execute(text("DELETE FROM alembic_version"))
                await conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                    {"v": normalized[0]},
                )
                print("repair_alembic: alias normalized")
                return 0

            # Multiple rows or messy state
            known = [n for n in normalized if n in ORDER]
            if all(present.values()):
                # Full 017 schema already present — stamp HEAD only
                target = HEAD
                print(
                    "repair_alembic: multi-row or messy version with HEAD schema present "
                    f"— stamping {target}"
                )
            elif known:
                target = max(known, key=lambda r: ORDER[r])
                print(
                    "repair_alembic: multi-row version — keeping farthest known "
                    f"revision {target}"
                )
            else:
                print(
                    "repair_alembic_FAILED cannot determine safe stamp "
                    f"versions={versions!r} markers={present}",
                    file=sys.stderr,
                )
                return 1

            await conn.execute(text("DELETE FROM alembic_version"))
            await conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": target},
            )
            print(f"repair_alembic: normalized to single revision {target}")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
