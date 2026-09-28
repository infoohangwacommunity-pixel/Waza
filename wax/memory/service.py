"""
Thin compatibility shim — memory operations live in wax.primitives.memory.

Old plan_and_retrieve / injection paths are gone.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from wax.primitives import memory as mem


class MemoryService:
    """Compatibility wrapper; prefer primitives from the tutor."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_user(self, principal_id, limit: int = 20):
        result = await mem.memory_search(
            self.session, principal_id, limit=limit
        )

        class _M:
            def __init__(self, d):
                self.id = d["id"]
                self.memory_type = d.get("memory_type")
                self.content = d.get("content")
                self.confidence = d.get("confidence")
                self.source = "memory"

        return [_M(x) for x in result.get("memories") or []]

    async def plan_and_retrieve(self, principal_id, user_text: str, limit: int = 14):
        return await self.list_for_user(principal_id, limit=limit)

    async def get_active_summary(self, principal_id, limit: int = 12) -> str:
        rows = await self.list_for_user(principal_id, limit=limit)
        if not rows:
            return "(no active memories)"
        return "\n".join(f"- {r.content}" for r in rows)
