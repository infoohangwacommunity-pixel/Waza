"""Request-driven capability selection — not full tool dump every turn."""

from __future__ import annotations

from wax.intelligence.capability_resolve import (
    FAMILY_TOOLS,
    families_from_brief,
    select_tool_specs,
    normalize_families,
)
from wax.intelligence.context_intel.brief import ContextBrief


class _FakeTool:
    def __init__(self, name: str):
        self.name = name


ALL = [_FakeTool(n) for fam in FAMILY_TOOLS.values() for n in fam]
# de-dupe
seen = set()
ALL_TOOLS = []
for t in ALL:
    if t.name not in seen:
        seen.add(t.name)
        ALL_TOOLS.append(t)


def test_no_context_brief_exposes_zero_or_core_only():
    brief = ContextBrief(no_context_required=True, capability_families=[])
    fams = families_from_brief(brief)
    assert fams == set()
    tools = select_tool_specs(ALL_TOOLS, fams)
    assert tools == []


def test_explicit_memory_family_only():
    brief = ContextBrief(capability_families=["memory"])
    fams = families_from_brief(brief)
    assert fams == {"memory"}
    tools = select_tool_specs(ALL_TOOLS, fams)
    names = {t.name for t in tools}
    assert "inspect_memories" in names
    assert "run_python" not in names
    assert "world_exec" not in names
    assert len(tools) < len(ALL_TOOLS)


def test_never_dumps_full_registry_by_default():
    brief = ContextBrief(no_context_required=False, capability_families=[])
    fams = families_from_brief(brief)
    tools = select_tool_specs(ALL_TOOLS, fams)
    assert len(tools) < len(ALL_TOOLS)
    assert len(tools) <= 25  # default families are bounded


def test_complex_multi_family_still_possible():
    fams = normalize_families(["memory", "artifacts", "media", "workspace"])
    tools = select_tool_specs(ALL_TOOLS, fams)
    names = {t.name for t in tools}
    assert "inspect_memories" in names
    assert "read_artifact" in names
    assert "ingest_document" in names
    assert "run_python" in names


def test_tutor_source_filters_tools():
    src = open("wax/intelligence/tutor.py").read()
    assert "select_tool_specs" in src
    assert "tutor_capability_selection" in src
    assert "tools=AVAILABLE_TOOLS if agent.can_call_tool()" not in src
