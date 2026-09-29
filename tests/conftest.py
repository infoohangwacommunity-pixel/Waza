"""Shared async-DB test fixture on SQLite (aiosqlite).

SQLite-compatible variants of the Postgres column types used by the schema, so
transcript/delivery regression tests can exercise real SQLAlchemy sessions
without a live PostgreSQL server. Production still runs on Postgres; this only
affects type rendering inside tests.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import JSON, TypeDecorator, Uuid
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import wax.db.models as models


class PgJsonLikeJSONB(TypeDecorator):
    """JSONB that renders as JSON on SQLite and JSONB on Postgres."""

    impl = JSON
    cache_ok = True

    def get_col_spec(self, **kw):  # pragma: no cover - dialect detail
        return "JSON"

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB())
        return dialect.type_descriptor(JSON())


class PgUuidLikeUUID(TypeDecorator):
    """Postgres UUID that renders as CHAR(32) on SQLite."""

    impl = Uuid
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.UUID())
        return dialect.type_descriptor(Uuid())


@pytest.fixture(scope="session", autouse=True)
def _patch_pg_types_for_sqlite():
    models.JSONB = PgJsonLikeJSONB
    models.UUID = PgUuidLikeUUID
    yield
    del models.JSONB
    del models.UUID


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    async with Session() as s:
        s.info["engine"] = engine
        yield s
    await engine.dispose()
