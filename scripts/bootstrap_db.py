#!/usr/bin/env python3
"""Create all tables from models (dev/bootstrap). Prefer alembic in production."""
import asyncio
from wax.db.base import Base
from wax.db.session import get_engine
import wax.db.models  # noqa: F401


async def main():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("schema ready")


if __name__ == "__main__":
    asyncio.run(main())
