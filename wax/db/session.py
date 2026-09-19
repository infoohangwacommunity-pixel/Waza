
from __future__ import annotations
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from wax.config import get_settings

_engine = None
_session_factory = None

def get_engine():
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(s.database_url, pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, echo=s.db_echo, pool_pre_ping=True)
    return _engine

def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False, autoflush=False)
    return _session_factory

@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
