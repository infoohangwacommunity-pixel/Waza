"""
Semantic publication model (versioned).

AI expresses WHAT; the trusted renderer decides HOW.
No educational page types. Composition from generic primitives only.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


SCHEMA_VERSION = "1.0"


class BlockType(str, Enum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    RICH_TEXT = "rich_text"
    SECTION = "section"
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
    CARD = "card"
    GRID = "grid"
    COLUMNS = "columns"
    METADATA = "metadata"
    REFERENCES = "references"
    TIMELINE = "timeline"
    STATUS = "status"
    EXPANDABLE = "expandable"


class CalloutTone(str, Enum):
    INFO = "info"
    TIP = "tip"
    WARNING = "warning"
    NOTE = "note"
    SUCCESS = "success"


class TextSpan(BaseModel):
    """Inline span — renderer escapes; no raw HTML."""

    text: str = ""
    bold: bool = False
    italic: bool = False
    code: bool = False
    href: Optional[str] = None  # validated later


class TableCell(BaseModel):
    text: str = ""
    header: bool = False
    align: Literal["left", "center", "right"] = "left"


class TableRow(BaseModel):
    cells: list[TableCell] = Field(default_factory=list)


class ArtifactRef(BaseModel):
    """Secure reference to an existing Artifact owned by the same principal."""

    artifact_id: str
    label: Optional[str] = None
    description: Optional[str] = None
    show_download: bool = True
    show_preview: bool = False  # images only when safe


class MediaRef(BaseModel):
    artifact_id: Optional[str] = None
    url: Optional[str] = None  # only internal/safe resolved URLs after validation
    alt: str = ""
    caption: Optional[str] = None
    media_kind: Literal["image", "audio", "video", "file"] = "image"


class Block(BaseModel):
    type: BlockType
    # Common optional fields; presence depends on type (validated lightly)
    text: Optional[str] = None
    level: Optional[int] = Field(default=None, ge=1, le=4)  # headings
    items: Optional[list[str]] = None
    rows: Optional[list[TableRow]] = None
    term: Optional[str] = None
    definition: Optional[str] = None
    tone: Optional[CalloutTone] = None
    language: Optional[str] = None  # code
    spans: Optional[list[TextSpan]] = None
    children: Optional[list["Block"]] = None
    artifact: Optional[ArtifactRef] = None
    media: Optional[MediaRef] = None
    href: Optional[str] = None
    label: Optional[str] = None
    title: Optional[str] = None
    columns: Optional[list[list["Block"]]] = None
    meta: Optional[dict[str, Any]] = None
    id: Optional[str] = None  # optional stable anchor within the publication

    @field_validator("text", "term", "definition", "label", "title", mode="before")
    @classmethod
    def _strip_str(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v
        return v


Block.model_rebuild()


class PublicationDocument(BaseModel):
    """
    Top-level semantic document the AI produces.
    Renderer turns this into a branded immutable HTML snapshot.
    """

    schema_version: str = SCHEMA_VERSION
    title: str = Field(..., min_length=1, max_length=500)
    subtitle: Optional[str] = Field(default=None, max_length=500)
    summary: Optional[str] = Field(default=None, max_length=2000)
    blocks: list[Block] = Field(default_factory=list)
    # Soft hints only — infrastructure enforces policy
    preferred_lifetime_hours: Optional[float] = Field(default=None, ge=0.1, le=168.0)
    # Privacy-safe share preview (no learner PII)
    share_title: Optional[str] = Field(default=None, max_length=200)
    share_description: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _non_empty(self) -> "PublicationDocument":
        if not self.blocks and not (self.title or "").strip():
            raise ValueError("publication must have a title or blocks")
        return self


def normalize_document(raw: dict[str, Any] | PublicationDocument) -> PublicationDocument:
    """Parse + validate AI / tool input into a PublicationDocument."""
    if isinstance(raw, PublicationDocument):
        return raw
    return PublicationDocument.model_validate(raw)


def document_to_dict(doc: PublicationDocument) -> dict[str, Any]:
    return doc.model_dump(mode="json")
