"""
Semantic publication model v1.1

Compositional tree — AI expresses WHAT; infrastructure expresses HOW.
No educational page types. No CSS. No HTML. No JavaScript.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


SCHEMA_VERSION = "1.1"


# ─── Inline content ───────────────────────────────────────────────────────────

class TextSpan(BaseModel):
    text: str = ""
    bold: bool = False
    italic: bool = False
    code: bool = False
    href: Optional[str] = None


# ─── Data structures ──────────────────────────────────────────────────────────

class TableCell(BaseModel):
    text: str = ""
    header: bool = False
    align: Literal["left", "center", "right"] = "left"
    emphasis: bool = False


class TableRow(BaseModel):
    cells: list[TableCell] = Field(default_factory=list)


class TableData(BaseModel):
    rows: list[TableRow] = Field(default_factory=list)
    caption: Optional[str] = None
    compact: bool = False


# ─── Assets & references ──────────────────────────────────────────────────────

class ArtifactRef(BaseModel):
    """Reference to a principal-owned Artifact. IDs never appear in public HTML metadata."""

    artifact_id: str
    label: Optional[str] = None
    description: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    show_download: bool = True
    show_preview: bool = False


class MediaRef(BaseModel):
    artifact_id: Optional[str] = None
    # External URLs only allowed after strict protocol validation in renderer/planner
    url: Optional[str] = None
    alt: str = ""
    caption: Optional[str] = None
    media_kind: Literal["image", "audio", "video", "file"] = "image"


class SourceRef(BaseModel):
    label: str
    href: Optional[str] = None
    note: Optional[str] = None


# ─── Presentation hints (non-educational, optional) ───────────────────────────

class PresentHints(BaseModel):
    """Soft layout hints — never educational rules. Renderer may ignore."""

    emphasize: bool = False
    compact: bool = False
    full_width: bool = False
    # Unknown keys preserved for forward compatibility via model_extra
    model_config = {"extra": "allow"}


# ─── Node types ───────────────────────────────────────────────────────────────

class NodeType(str, Enum):
    # Leaf / content
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    RICH_TEXT = "rich_text"
    LIST = "list"
    ORDERED_LIST = "ordered_list"
    TABLE = "table"
    DEFINITION = "definition"
    CALLOUT = "callout"
    QUOTE = "quote"
    CODE = "code"
    FORMULA = "formula"
    IMAGE = "image"
    MEDIA = "media"
    ARTIFACT = "artifact"
    LINK = "link"
    DIVIDER = "divider"
    STATUS = "status"
    REFERENCES = "references"
    TIMELINE = "timeline"
    # Structural containers
    SECTION = "section"
    CARD = "card"
    COLUMNS = "columns"
    GRID = "grid"
    EXPANDABLE = "expandable"
    # Document-level (rarely in body)
    TITLE = "title"
    SUBTITLE = "subtitle"
    METADATA = "metadata"


class CalloutTone(str, Enum):
    INFO = "info"
    TIP = "tip"
    WARNING = "warning"
    NOTE = "note"
    SUCCESS = "success"


class Node(BaseModel):
    """
    Compositional semantic node.

    Containers hold children. Leaves hold content.
    Unknown future fields in `extra` / present.hints must not break rendering.
    """

    type: NodeType
    id: Optional[str] = None  # stable anchor for TOC/navigation

    # Text content
    text: Optional[str] = None
    title: Optional[str] = None
    level: Optional[int] = Field(default=None, ge=1, le=4)

    # Structured
    items: Optional[list[str]] = None
    table: Optional[TableData] = None
    rows: Optional[list[TableRow]] = None  # legacy alias → table
    term: Optional[str] = None
    definition: Optional[str] = None
    tone: Optional[CalloutTone] = None
    language: Optional[str] = None
    spans: Optional[list[TextSpan]] = None

    # Assets
    artifact: Optional[ArtifactRef] = None
    media: Optional[MediaRef] = None
    href: Optional[str] = None
    label: Optional[str] = None
    sources: Optional[list[SourceRef]] = None

    # Structure
    children: Optional[list["Node"]] = None
    columns: Optional[list[list["Node"]]] = None

    # Soft presentation
    present: Optional[PresentHints] = None
    meta: Optional[dict[str, Any]] = None

    # Forward compatibility — unknown keys ignored by renderer, preserved in semantic store
    model_config = {"extra": "allow"}

    @field_validator("text", "title", "term", "definition", "label", mode="before")
    @classmethod
    def _coerce_str(cls, v: Any) -> Any:
        if v is None:
            return v
        return str(v) if not isinstance(v, str) else v


Node.model_rebuild()


# Backward-compatible aliases for foundation pass 1 call sites
Block = Node
BlockType = NodeType


class DocumentMeta(BaseModel):
    share_title: Optional[str] = Field(default=None, max_length=200)
    share_description: Optional[str] = Field(default=None, max_length=300)
    # Privacy: never put learner identifiers here
    locale: Optional[str] = None


class PublicationDocument(BaseModel):
    """Top-level semantic document produced by the AI / tool."""

    schema_version: str = SCHEMA_VERSION
    title: str = Field(..., min_length=1, max_length=500)
    subtitle: Optional[str] = Field(default=None, max_length=500)
    summary: Optional[str] = Field(default=None, max_length=2000)
    nodes: list[Node] = Field(default_factory=list)
    # Foundation-pass alias
    blocks: Optional[list[Node]] = None
    preferred_lifetime_hours: Optional[float] = Field(default=None, ge=0.1, le=168.0)
    meta: Optional[DocumentMeta] = None
    # legacy share fields
    share_title: Optional[str] = Field(default=None, max_length=200)
    share_description: Optional[str] = Field(default=None, max_length=300)

    model_config = {"extra": "allow"}

    @model_validator(mode="after")
    def _normalize(self) -> "PublicationDocument":
        if self.blocks and not self.nodes:
            self.nodes = list(self.blocks)
        if not self.nodes and not (self.title or "").strip():
            raise ValueError("publication must have a title or nodes")
        # Normalize legacy table rows on nodes
        for n in self.nodes:
            _normalize_node(n)
        return self

    @property
    def effective_share_title(self) -> str:
        if self.meta and self.meta.share_title:
            return self.meta.share_title
        return self.share_title or self.title

    @property
    def effective_share_description(self) -> str:
        if self.meta and self.meta.share_description:
            return self.meta.share_description
        return self.share_description or self.summary or "Prepared for you by WAX Prep"


def _normalize_node(n: Node) -> None:
    if n.rows and not n.table:
        n.table = TableData(rows=n.rows)
    if n.children:
        for c in n.children:
            _normalize_node(c)
    if n.columns:
        for col in n.columns:
            for c in col:
                _normalize_node(c)


def normalize_document(raw: dict[str, Any] | PublicationDocument) -> PublicationDocument:
    if isinstance(raw, PublicationDocument):
        return raw
    # Accept blocks as nodes
    if isinstance(raw, dict) and "blocks" in raw and "nodes" not in raw:
        raw = dict(raw)
        raw["nodes"] = raw.get("blocks") or []
    return PublicationDocument.model_validate(raw)


def document_to_dict(doc: PublicationDocument) -> dict[str, Any]:
    return doc.model_dump(mode="json", exclude_none=True)


def collect_artifact_ids(doc: PublicationDocument) -> list[str]:
    ids: list[str] = []

    def walk(n: Node) -> None:
        if n.artifact and n.artifact.artifact_id:
            ids.append(str(n.artifact.artifact_id))
        if n.media and n.media.artifact_id:
            ids.append(str(n.media.artifact_id))
        for c in n.children or []:
            walk(c)
        for col in n.columns or []:
            for c in col:
                walk(c)

    for n in doc.nodes:
        walk(n)
    # preserve order, unique
    return list(dict.fromkeys(ids))
