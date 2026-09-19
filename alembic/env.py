"""Alembic environment — async migrations via asyncpg.

Important for Railway:
- Never print full DATABASE_URL or passwords.
- Prefer create_async_engine(url) over config.set_main_option (ConfigParser mangles %).
- Normalize scheme safely with sqlalchemy.engine.make_url.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config, create_async_engine

from wax.config import get_settings
from wax.db.base import Base
import wax.db.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """
    Resolve DB URL for migrations.

    Priority:
    1. DATABASE_URL from environment / settings (normal Railway variable)
    2. Optional DATABASE_PUBLIC_URL if set (public proxy host when private DNS
       is unavailable in a given phase — still never logged in full)
    """
    # Fresh settings (avoid stale lru_cache across processes)
    try:
        get_settings.cache_clear()
    except Exception:
        pass

    settings = get_settings()
    raw = (os.environ.get("DATABASE_URL") or settings.database_url or "").strip()

    # If private/internal host cannot be used in this environment, allow explicit public URL
    public = (os.environ.get("DATABASE_PUBLIC_URL") or "").strip()
    prefer_public = (os.environ.get("WAX_MIGRATE_USE_PUBLIC_URL") or "").lower() in (
        "1",
        "true",
        "yes",
    )
    if prefer_public and public:
        raw = public

    if not raw:
        raise RuntimeError(
            "DATABASE_URL is not set. Link the Railway Postgres service or set DATABASE_URL."
        )

    return _to_asyncpg_url(raw)


def _to_asyncpg_url(raw: str) -> str:
    """Convert postgres/postgresql URLs to postgresql+asyncpg without breaking host/user/pass."""
    # Already asyncpg
    if "+asyncpg" in raw.split("://", 1)[0]:
        return raw

    try:
        u = make_url(raw)
    except Exception as e:
        raise RuntimeError(f"DATABASE_URL is not a valid SQLAlchemy URL: {e}") from e

    # make_url understands postgres:// and postgresql://
    driver = u.drivername  # e.g. postgresql, postgresql+psycopg2
    if driver == "postgres":
        driver = "postgresql"
    # Strip any existing driver suffix and force asyncpg
    base = driver.split("+")[0]
    if base not in ("postgresql", "postgres"):
        raise RuntimeError(f"Unsupported database scheme for WAX Prep: {u.drivername}")
    u = u.set(drivername="postgresql+asyncpg")
    # render_as_string(hide_password=False) needed to actually connect
    return u.render_as_string(hide_password=False)


def _safe_url_diagnostics(url: str) -> dict:
    """Safe fields only — never password or full URL."""
    try:
        u = make_url(url)
        return {
            "driver": u.drivername,
            "host": u.host,
            "port": u.port or 5432,
            "database": u.database,
            "user_set": bool(u.username),
        }
    except Exception as e:
        return {"parse_error": str(e)}


def run_migrations_offline() -> None:
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = _resolve_database_url()
    diag = _safe_url_diagnostics(url)
    print(
        "alembic_db_target "
        f"driver={diag.get('driver')} "
        f"host={diag.get('host')} "
        f"port={diag.get('port')} "
        f"database={diag.get('database')} "
        f"user_set={diag.get('user_set')}"
    )
    if not diag.get("host"):
        raise RuntimeError(
            "DATABASE_URL has no hostname after parsing. "
            "Check that DATABASE_URL is set from the Railway Postgres plugin."
        )

    # Create engine directly — do NOT use set_main_option (ConfigParser mangles % in passwords)
    connectable = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
