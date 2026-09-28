"""
DEPRECATED — specialized tool registry removed.

All capabilities go through wax.primitives.registry.
This module remains only as a thin redirect so residual imports do not crash.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wax.primitives.registry import execute_primitive


async def execute_tool(
    session: AsyncSession,
    name: str,
    arguments: str | dict | None,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    return await execute_primitive(session, name, arguments, ctx)


HANDLERS: dict = {}
