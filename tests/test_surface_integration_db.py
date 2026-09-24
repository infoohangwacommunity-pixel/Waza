"""
PostgreSQL integration tests for Surface AI loop, concurrency, and isolation.

Skip entire module when DATABASE_URL is unset or SQLAlchemy/asyncpg unavailable.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("asyncpg")

DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
if not DATABASE_URL:
    pytest.skip("DATABASE_URL not set — skipping PostgreSQL surface integration tests", allow_module_level=True)

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from wax.db.models import Base, Principal, Surface, SurfaceRevision, SurfaceAiRequest, Work
from wax.surfaces.service import SurfaceService
from wax.surfaces.policy import SurfaceStatus
from wax.surfaces.tokens import generate_token, hash_token


def _async_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(_async_url(DATABASE_URL), echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def principal(session: AsyncSession):
    p = Principal(id=uuid.uuid4(), display_name="Test Learner")
    session.add(p)
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_create_update_same_surface_revision_chain(session, principal):
    svc = SurfaceService(session)
    created = await svc.create(
        principal_id=principal.id,
        html="<h1>Rev1</h1>",
        title="Photosynthesis",
    )
    assert created["ok"]
    sid = created["surface_id"]
    token = None  # raw token only at create — check response
    # create returns public_url / may include token depending on impl
    r1 = created["revision"]
    assert r1 == 1

    upd = await svc.update(
        surface_id=sid,
        principal_id=principal.id,
        html="<h1>Rev2</h1>",
        expected_revision=1,
    )
    assert upd["ok"]
    assert upd["revision"] == 2

    # Same surface id, higher revision
    surface = await session.get(Surface, uuid.UUID(sid))
    assert surface.current_revision == 2
    assert str(surface.principal_id) == str(principal.id)


@pytest.mark.asyncio
async def test_revision_conflict_atomic(session, principal):
    svc = SurfaceService(session)
    created = await svc.create(
        principal_id=principal.id,
        html="<p>A</p>",
        title="Conflict",
    )
    sid = created["surface_id"]

    ok = await svc.update(
        surface_id=sid,
        principal_id=principal.id,
        html="<p>B</p>",
        expected_revision=1,
    )
    assert ok["ok"] and ok["revision"] == 2

    conflict = await svc.update(
        surface_id=sid,
        principal_id=principal.id,
        html="<p>C</p>",
        expected_revision=1,  # stale
    )
    assert conflict["ok"] is False
    assert conflict["error"] == "revision_conflict"


@pytest.mark.asyncio
async def test_ai_request_idempotency_returns_same_request_id(session, principal):
    svc = SurfaceService(session)
    created = await svc.create(
        principal_id=principal.id,
        html="<p>AI</p>",
        title="AI loop",
    )
    surface = await session.get(Surface, uuid.UUID(created["surface_id"]))
    # grant is default on create
    r1 = await svc.accept_ai_request(
        surface=surface,
        message="Explain this",
        idempotency_key="idem-1",
    )
    assert r1["ok"] and r1["request_id"]
    r2 = await svc.accept_ai_request(
        surface=surface,
        message="Explain this",
        idempotency_key="idem-1",
    )
    assert r2["ok"]
    assert r2.get("idempotent") is True
    assert r2["request_id"] == r1["request_id"]
    assert r2["request_id"] is not None

    # Only one Work
    from sqlalchemy import select, func
    n = await session.execute(
        select(func.count()).select_from(Work).where(
            Work.principal_id == principal.id,
            Work.kind == "surface_ai",
        )
    )
    assert n.scalar_one() == 1


@pytest.mark.asyncio
async def test_cross_principal_denied(session, principal):
    other = Principal(id=uuid.uuid4(), display_name="Other")
    session.add(other)
    await session.flush()

    svc = SurfaceService(session)
    created = await svc.create(
        principal_id=principal.id,
        html="<p>private</p>",
        title="A",
    )
    denied = await svc.update(
        surface_id=created["surface_id"],
        principal_id=other.id,
        html="<p>hack</p>",
    )
    assert denied["ok"] is False


@pytest.mark.asyncio
async def test_ai_work_uses_surface_principal(session, principal):
    svc = SurfaceService(session)
    created = await svc.create(
        principal_id=principal.id,
        html="<p>x</p>",
        title="P",
    )
    surface = await session.get(Surface, uuid.UUID(created["surface_id"]))
    result = await svc.accept_ai_request(surface=surface, message="hi")
    assert result["ok"]
    from sqlalchemy import select
    work = (await session.execute(select(Work).where(Work.kind == "surface_ai"))).scalars().first()
    assert work is not None
    assert str(work.principal_id) == str(principal.id)
    assert work.input_payload.get("surface_id") == created["surface_id"]
    # work_id must not be in browser-facing result
    assert "work_id" not in result
