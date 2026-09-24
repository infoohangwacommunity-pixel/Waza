"""
Publication planner — deterministic structure analysis for rendering.

Does NOT decide educational intent. Works purely from semantic structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from wax.publication.schema import Node, NodeType, PublicationDocument


@dataclass
class TocEntry:
    id: str
    title: str
    level: int


@dataclass
class PlannedDocument:
    document: PublicationDocument
    toc: list[TocEntry] = field(default_factory=list)
    heading_count: int = 0
    section_count: int = 0
    table_count: int = 0
    artifact_count: int = 0
    media_count: int = 0
    has_code: bool = False
    depth: int = 0
    # Soft presentation decisions derived from structure only
    show_toc: bool = False
    show_back_to_top: bool = False
    estimated_blocks: int = 0
    asset_ids: list[str] = field(default_factory=list)
    unknown_types: list[str] = field(default_factory=list)


def _ensure_ids(nodes: list[Node], prefix: str = "n", counter: list[int] | None = None) -> None:
    if counter is None:
        counter = [0]
    for n in nodes:
        if n.type in (NodeType.HEADING, NodeType.SECTION) and not n.id:
            counter[0] += 1
            n.id = f"{prefix}{counter[0]}"
        if n.children:
            _ensure_ids(n.children, prefix, counter)
        if n.columns:
            for col in n.columns:
                _ensure_ids(col, prefix, counter)


def _walk(nodes: list[Node], plan: PlannedDocument, depth: int = 0) -> None:
    plan.depth = max(plan.depth, depth)
    for n in nodes:
        plan.estimated_blocks += 1
        t = n.type
        if isinstance(t, str):
            try:
                t = NodeType(t)
            except ValueError:
                plan.unknown_types.append(str(t))
                t = None

        if t == NodeType.HEADING:
            plan.heading_count += 1
            title = (n.text or n.title or "").strip()
            if n.id and title:
                plan.toc.append(TocEntry(id=n.id, title=title, level=n.level or 2))
        elif t == NodeType.SECTION:
            plan.section_count += 1
            title = (n.title or n.text or "").strip()
            if n.id and title:
                plan.toc.append(TocEntry(id=n.id, title=title, level=2))
        elif t == NodeType.TABLE:
            plan.table_count += 1
        elif t == NodeType.ARTIFACT:
            plan.artifact_count += 1
            if n.artifact and n.artifact.artifact_id:
                plan.asset_ids.append(str(n.artifact.artifact_id))
        elif t in (NodeType.IMAGE, NodeType.MEDIA):
            plan.media_count += 1
            if n.media and n.media.artifact_id:
                plan.asset_ids.append(str(n.media.artifact_id))
        elif t == NodeType.CODE:
            plan.has_code = True

        if n.children:
            _walk(n.children, plan, depth + 1)
        if n.columns:
            for col in n.columns:
                _walk(col, plan, depth + 1)


def plan_document(doc: PublicationDocument) -> PlannedDocument:
    """Analyze semantic structure and produce rendering plan."""
    _ensure_ids(doc.nodes)
    plan = PlannedDocument(document=doc)
    _walk(doc.nodes, plan)

    # Structure-based navigation — NOT content-type based
    # TOC when there are enough navigable headings/sections
    plan.show_toc = len(plan.toc) >= 3
    plan.show_back_to_top = plan.estimated_blocks >= 12 or plan.show_toc
    plan.asset_ids = list(dict.fromkeys(plan.asset_ids))
    return plan
