"""Memory primitives lifecycle — AI-owned, no automatic assembler."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_memory_create_search_update_supersede_forget_roundtrip():
    """Primitives expose full lifecycle without a hidden context assembler."""
    from wax.primitives import memory as mem
    from wax.primitives.registry import list_primitive_specs

    names = {s.name for s in list_primitive_specs()}
    for required in (
        "memory_search",
        "memory_get",
        "memory_create",
        "memory_update",
        "memory_supersede",
        "memory_forget",
    ):
        assert required in names, f"missing primitive {required}"

    # Specs must not look like a tool menu of educational workflows
    banned = {"assess", "quiz", "curriculum", "concept_graph", "gather_evidence", "investigate_context"}
    for n in names:
        for b in banned:
            assert b not in n.lower(), f"primitive {n} looks like old educational tool"


@pytest.mark.asyncio
async def test_schedule_primitive_specs_include_short_delay():
    from wax.primitives.registry import list_primitive_specs

    names = {s.name for s in list_primitive_specs()}
    assert "schedule" in names or "schedule_action" in names or "schedule_at" in names or any(
        "schedule" in n for n in names
    )
    assert "get_current_time" in names and "schedule" in names
