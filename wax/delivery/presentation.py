"""
Channel-aware presentation architecture.

Pipeline:
  Tutor (semantic / model text)
      → parse / interpret (Markdown is an input adapter, not the product language)
      → channel-neutral structure
      → channel renderer (WhatsApp | Telegram — separate)
      → thin defensive normalizer
      → structurally aware chunking (callers)
      → delivery adapter

Deterministic software only — no second LLM for formatting.
Domain-neutral structure only (paragraph, emphasis, heading, list, link, code, table, …).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from wax.config import get_settings
from wax.delivery.chunking import chunk_message
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Channel capability profiles (factual, small — not a formatting rule engine)
# ---------------------------------------------------------------------------


@dataclass
class ChannelProfile:
    name: str
    max_text_chars: int
    supports_markdown: bool
    supports_reply_buttons: bool
    max_reply_buttons: int
    supports_lists: bool
    max_list_rows: int
    supports_typing_indicator: bool
    notes: str


PROFILES: dict[str, ChannelProfile] = {
    "whatsapp": ChannelProfile(
        name="whatsapp",
        max_text_chars=settings.whatsapp_max_message_chars or 3500,
        supports_markdown=False,  # limited * _ ~ formatting only
        supports_reply_buttons=True,
        max_reply_buttons=3,
        supports_lists=True,
        max_list_rows=10,
        supports_typing_indicator=True,
        notes=(
            "WhatsApp: keep messages readable. Prefer short paragraphs. "
            "Bold with *text*, italic with _text_. Avoid giant walls. "
            "If a response is long, structure it so it can be split on paragraphs. "
            "You may offer up to 3 quick-reply buttons OR a list (up to 10 options) "
            "when a clear choice would help the learner — never as a rigid menu for everything. "
            "Do not use buttons when free-form conversation is better."
        ),
    ),
    "telegram": ChannelProfile(
        name="telegram",
        max_text_chars=settings.telegram_max_message_chars or 4000,
        supports_markdown=True,
        supports_reply_buttons=True,
        max_reply_buttons=8,
        supports_lists=False,
        max_list_rows=0,
        supports_typing_indicator=True,
        notes=(
            "Telegram: Markdown is available. Keep messages readable. "
            "Optional reply keyboard buttons when a clear choice helps."
        ),
    ),
    "web": ChannelProfile(
        name="web",
        max_text_chars=12000,
        supports_markdown=True,
        supports_reply_buttons=True,
        max_reply_buttons=12,
        supports_lists=True,
        max_list_rows=20,
        supports_typing_indicator=False,
        notes="Web UI: richer formatting and longer responses are fine.",
    ),
}


def get_profile(channel: str) -> ChannelProfile:
    return PROFILES.get(channel, PROFILES["whatsapp"])


def platform_context_block(channel: str) -> str:
    """Soft quality hint for the tutor system prompt — not the correctness mechanism."""
    p = get_profile(channel)
    return (
        f"\n--- Delivery channel: {p.name} ---\n"
        f"Max practical message length: ~{p.max_text_chars} characters per bubble.\n"
        f"{p.notes}\n"
        f"Write so the response is readable in a chat environment. "
        f"Prefer clear paragraph breaks for long content. "
        f"Avoid document-style formatting when conversational text is enough.\n"
        f"--- End channel constraints ---\n"
    )


# ---------------------------------------------------------------------------
# Interactive / presentable response (existing contract)
# ---------------------------------------------------------------------------


@dataclass
class InteractiveChoice:
    id: str
    title: str
    description: str | None = None


@dataclass
class PresentableResponse:
    """Normalized tutor output ready for channel delivery."""

    text: str
    interactive_type: str | None = None  # reply_buttons | list | None
    buttons: list[InteractiveChoice] = field(default_factory=list)
    list_button_label: str | None = None
    list_sections: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def text_chunks(self, channel: str) -> list[str]:
        p = get_profile(channel)
        return chunk_message(self.text, max_chars=p.max_text_chars)


# ---------------------------------------------------------------------------
# Channel-neutral structure (domain-neutral communication form only)
# ---------------------------------------------------------------------------

BlockKind = Literal[
    "paragraph",
    "heading",
    "list",
    "code",
    "table",
    "quote",
    "blank",
]


@dataclass
class InlineSpan:
    text: str
    strong: bool = False
    emphasis: bool = False
    code: bool = False
    link_url: str | None = None


@dataclass
class ContentBlock:
    kind: BlockKind
    # paragraph / heading / quote
    spans: list[InlineSpan] = field(default_factory=list)
    level: int = 0  # heading level 1–6
    # list
    ordered: bool = False
    items: list[list[InlineSpan]] = field(default_factory=list)
    # code
    code_text: str = ""
    code_lang: str = ""
    # table
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class ContentDocument:
    blocks: list[ContentBlock] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parse / interpret model output (Markdown + HTML as input adapters)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_FENCE_RE = re.compile(r"^```(\w*)\s*$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(
    r"</?(?:div|p|span|strong|b|em|i|u|s|strike|h[1-6]|ul|ol|li|pre|code|blockquote|hr)(?:\s[^>]*)?>",
    re.IGNORECASE,
)
_HTML_A_RE = re.compile(
    r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_RESIDUAL_HTML_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _parse_inlines(text: str) -> list[InlineSpan]:
    """Parse a single line of inline Markdown into spans. Tolerant, not full CommonMark."""
    text = _HTML_BR_RE.sub("\n", text)
    text = _HTML_A_RE.sub(r"[\2](\1)", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)

    spans: list[InlineSpan] = []
    i = 0
    n = len(text)

    def emit(s: str, **flags: Any) -> None:
        if not s:
            return
        spans.append(InlineSpan(text=s, **flags))

    while i < n:
        if text[i] == "`":
            j = text.find("`", i + 1)
            if j > i:
                emit(text[i + 1 : j], code=True)
                i = j + 1
                continue
        if text[i] == "[":
            close = text.find("]", i + 1)
            if close > i and close + 1 < n and text[close + 1] == "(":
                end = text.find(")", close + 2)
                if end > close:
                    label = text[i + 1 : close]
                    url = text[close + 2 : end]
                    emit(label, link_url=url)
                    i = end + 1
                    continue
        if text.startswith("**", i) or text.startswith("__", i):
            marker = text[i : i + 2]
            j = text.find(marker, i + 2)
            if j > i:
                emit(text[i + 2 : j], strong=True)
                i = j + 2
                continue
        if text[i] in "*_" and (i + 1 >= n or text[i + 1] != text[i]):
            marker = text[i]
            j = text.find(marker, i + 1)
            if j > i and (j + 1 >= n or text[j + 1] != marker):
                emit(text[i + 1 : j], emphasis=True)
                i = j + 1
                continue
        j = i + 1
        while j < n and text[j] not in "`*[$_":
            if text.startswith("**", j) or text.startswith("__", j):
                break
            j += 1
        if j == i + 1 and text[i] in "*_":
            emit(text[i])
            i = j
            continue
        emit(text[i:j])
        i = j

    if not spans:
        spans.append(InlineSpan(text=text))
    return spans


def _split_table_cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [_clean_cell(c.strip()) for c in line.split("|")]


def _clean_cell(cell: str) -> str:
    """Strip residual MD markers from table cells (cells are plain strings)."""
    if not cell:
        return cell
    cell = re.sub(r"\*\*(.+?)\*\*", r"\1", cell)
    cell = re.sub(r"__(.+?)__", r"\1", cell)
    cell = re.sub(r"`([^`]+)`", r"\1", cell)
    return cell.strip()


def parse_model_output(text: str) -> ContentDocument:
    """
    Interpret model output into a channel-neutral ContentDocument.
    Markdown/HTML are input adapters — tolerant of imperfect markup.
    """
    if not text:
        return ContentDocument()

    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    raw = _HTML_BR_RE.sub("\n", raw)
    lines = raw.split("\n")

    blocks: list[ContentBlock] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            if blocks and blocks[-1].kind != "blank":
                blocks.append(ContentBlock(kind="blank"))
            i += 1
            continue

        fm = _FENCE_RE.match(stripped)
        if fm:
            lang = fm.group(1) or ""
            body_lines: list[str] = []
            i += 1
            while i < n and not _FENCE_RE.match(lines[i].strip()):
                body_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            blocks.append(
                ContentBlock(kind="code", code_text="\n".join(body_lines), code_lang=lang)
            )
            continue

        hm = _HEADING_RE.match(stripped)
        if hm:
            level = len(hm.group(1))
            blocks.append(
                ContentBlock(
                    kind="heading",
                    level=level,
                    spans=_parse_inlines(hm.group(2).strip()),
                )
            )
            i += 1
            continue

        if "|" in stripped and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            headers = _split_table_cells(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                if _TABLE_SEP_RE.match(lines[i].strip()):
                    i += 1
                    continue
                rows.append(_split_table_cells(lines[i]))
                i += 1
            blocks.append(ContentBlock(kind="table", headers=headers, rows=rows))
            continue

        ul = _UL_RE.match(line)
        ol = _OL_RE.match(line)
        if ul or ol:
            ordered = bool(ol)
            items: list[list[InlineSpan]] = []
            while i < n:
                ul2 = _UL_RE.match(lines[i])
                ol2 = _OL_RE.match(lines[i])
                if ordered and ol2:
                    items.append(_parse_inlines(ol2.group(3)))
                    i += 1
                elif not ordered and ul2:
                    items.append(_parse_inlines(ul2.group(3)))
                    i += 1
                elif not lines[i].strip():
                    if i + 1 < n and (_UL_RE.match(lines[i + 1]) or _OL_RE.match(lines[i + 1])):
                        i += 1
                        continue
                    break
                else:
                    break
            blocks.append(ContentBlock(kind="list", ordered=ordered, items=items))
            continue

        if stripped.startswith(">"):
            q_lines: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                q_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            blocks.append(
                ContentBlock(kind="quote", spans=_parse_inlines(" ".join(q_lines)))
            )
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            if blocks and blocks[-1].kind != "blank":
                blocks.append(ContentBlock(kind="blank"))
            i += 1
            continue

        para_parts: list[str] = [stripped]
        i += 1
        while i < n:
            nxt = lines[i]
            ns = nxt.strip()
            if not ns:
                break
            if (
                _HEADING_RE.match(ns)
                or _FENCE_RE.match(ns)
                or _UL_RE.match(nxt)
                or _OL_RE.match(nxt)
                or ns.startswith(">")
                or (ns.startswith("|") and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()))
                or re.match(r"^(-{3,}|\*{3,}|_{3,})$", ns)
            ):
                break
            para_parts.append(ns)
            i += 1
        blocks.append(
            ContentBlock(kind="paragraph", spans=_parse_inlines(" ".join(para_parts)))
        )

    while blocks and blocks[0].kind == "blank":
        blocks.pop(0)
    while blocks and blocks[-1].kind == "blank":
        blocks.pop()

    return ContentDocument(blocks=blocks)


# ---------------------------------------------------------------------------
# Inline rendering helpers
# ---------------------------------------------------------------------------


def _spans_plain(spans: list[InlineSpan]) -> str:
    parts: list[str] = []
    for s in spans:
        t = s.text
        if s.link_url:
            parts.append(f"{t} ({s.link_url})" if t and t != s.link_url else (s.link_url or t))
        else:
            parts.append(t)
    return "".join(parts)


def _spans_whatsapp(spans: list[InlineSpan]) -> str:
    """WhatsApp: *bold* _italic_ ; links as readable text + URL."""
    parts: list[str] = []
    for s in spans:
        t = s.text
        if s.code:
            parts.append(t)
            continue
        if s.strong:
            t = f"*{t}*" if t else t
        elif s.emphasis:
            t = f"_{t}_" if t else t
        if s.link_url:
            if t and t != s.link_url:
                parts.append(f"{t} {s.link_url}")
            else:
                parts.append(s.link_url)
        else:
            parts.append(t)
    return "".join(parts)


def _telegram_escape(text: str) -> str:
    """Escape for Telegram legacy Markdown (parse_mode=Markdown)."""
    for ch in ("\\", "_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def _spans_telegram(spans: list[InlineSpan]) -> str:
    """Telegram Markdown: *bold* _italic_ `code` [text](url)."""
    parts: list[str] = []
    for s in spans:
        t = s.text
        if s.code:
            safe = t.replace("`", "'")
            parts.append(f"`{safe}`")
            continue
        if s.link_url:
            label = _telegram_escape(t) if t else s.link_url
            url = s.link_url.replace(")", "%29")
            parts.append(f"[{label}]({url})")
            continue
        t = _telegram_escape(t)
        if s.strong:
            t = f"*{t}*" if t else t
        elif s.emphasis:
            t = f"_{t}_" if t else t
        parts.append(t)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Table presentation (preserve information; adapt shape)
# ---------------------------------------------------------------------------


def _render_table_chat(headers: list[str], rows: list[list[str]]) -> str:
    """
    Represent tabular data for constrained chat surfaces.
    Preserves cell values; chooses compact structure by shape.
    """
    cols = max(len(headers), max((len(r) for r in rows), default=0))
    if cols == 0:
        return ""

    hdr = (headers + [""] * cols)[:cols]
    norm_rows = [(r + [""] * cols)[:cols] for r in rows]
    lines: list[str] = []

    if cols == 2:
        h0, h1 = hdr[0].strip(), hdr[1].strip()
        for row in norm_rows:
            a, b = row[0].strip(), row[1].strip()
            if not a and not b:
                continue
            if a and b:
                lines.append(f"• {a}: {b}")
            elif a:
                lines.append(f"• {a}")
            else:
                lines.append(f"• {b}")
        body = "\n".join(lines)
        if h0 or h1:
            title = h0 if not h1 else (f"{h0} / {h1}" if h0 else h1)
            return f"{title}\n{body}" if body else title
        return body

    if cols == 1:
        for row in norm_rows:
            cell = row[0].strip()
            if cell:
                lines.append(f"• {cell}")
        title = hdr[0].strip() if hdr else ""
        body = "\n".join(lines)
        return f"{title}\n{body}" if title and body else (body or title)

    for row in norm_rows:
        parts = []
        for c in range(cols):
            cell = row[c].strip()
            label = hdr[c].strip() if c < len(hdr) else ""
            if not cell and not label:
                continue
            if label:
                parts.append(f"{label}: {cell}" if cell else label)
            else:
                parts.append(cell)
        if parts:
            lines.append("• " + " · ".join(parts))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Channel renderers (genuinely separate)
# ---------------------------------------------------------------------------


def render_whatsapp(doc: ContentDocument) -> str:
    """
    WhatsApp presentation: limited * _ formatting, natural chat structure.
    No Markdown headings, no pipe tables, no HTML.
    """
    out: list[str] = []
    for block in doc.blocks:
        if block.kind == "blank":
            if out and out[-1] != "":
                out.append("")
            continue
        if block.kind == "heading":
            title = _spans_whatsapp(block.spans).strip()
            if not title:
                continue
            if out and out[-1] != "":
                out.append("")
            out.append(title)
            continue
        if block.kind == "paragraph":
            out.append(_spans_whatsapp(block.spans))
            continue
        if block.kind == "quote":
            q = _spans_whatsapp(block.spans)
            out.append(f"“{q}”" if q else "")
            continue
        if block.kind == "list":
            for idx, item in enumerate(block.items, start=1):
                body = _spans_whatsapp(item)
                prefix = f"{idx}. " if block.ordered else "• "
                out.append(f"{prefix}{body}")
            continue
        if block.kind == "code":
            code = block.code_text.strip("\n")
            if code:
                out.append(code)
            continue
        if block.kind == "table":
            table_text = _render_table_chat(block.headers, block.rows)
            if table_text:
                if out and out[-1] != "":
                    out.append("")
                out.append(table_text)
            continue

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def render_telegram(doc: ContentDocument) -> str:
    """
    Telegram presentation with Markdown parse_mode compatibility.
    Escapes ordinary text; applies * _ ` [text](url) only where intended.
    """
    out: list[str] = []
    for block in doc.blocks:
        if block.kind == "blank":
            if out and out[-1] != "":
                out.append("")
            continue
        if block.kind == "heading":
            plain = _spans_plain(block.spans).strip()
            if not plain:
                continue
            if out and out[-1] != "":
                out.append("")
            out.append(f"*{_telegram_escape(plain)}*")
            continue
        if block.kind == "paragraph":
            out.append(_spans_telegram(block.spans))
            continue
        if block.kind == "quote":
            q = _spans_telegram(block.spans)
            out.append(f"_{q}_" if q else "")
            continue
        if block.kind == "list":
            for idx, item in enumerate(block.items, start=1):
                body = _spans_telegram(item)
                prefix = f"{idx}. " if block.ordered else "• "
                out.append(f"{prefix}{body}")
            continue
        if block.kind == "code":
            code = block.code_text.strip("\n")
            if code:
                if "\n" in code:
                    safe = code.replace("```", "'''")
                    out.append(f"```\n{safe}\n```")
                else:
                    safe = code.replace("`", "'")
                    out.append(f"`{safe}`")
            continue
        if block.kind == "table":
            table_text = _render_table_chat(block.headers, block.rows)
            if table_text:
                if out and out[-1] != "":
                    out.append("")
                # Escape specials but keep bullets readable
                escaped_lines = []
                for ln in table_text.split("\n"):
                    if ln.startswith("• "):
                        escaped_lines.append("• " + _telegram_escape(ln[2:]))
                    else:
                        escaped_lines.append(_telegram_escape(ln))
                out.append("\n".join(escaped_lines))
            continue

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


# ---------------------------------------------------------------------------
# Thin defensive normalizer
# ---------------------------------------------------------------------------

_RESIDUAL_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def normalize_for_channel(text: str, channel: str) -> str:
    """
    Final safety net only. Must stay small.
    Guarantees: no residual HTML tags, no raw MD tables/headings leaking to chat,
    no pathological whitespace. Does not aggressively rewrite natural language.
    """
    if not text:
        return ""

    text = _HTML_BR_RE.sub("\n", text)
    text = _HTML_A_RE.sub(r"\2 (\1)", text)
    text = _RESIDUAL_HTML_RE.sub("", text)
    text = html.unescape(text)

    if "|" in text and re.search(r"^\|.+\|$", text, re.MULTILINE):
        fixed_lines: list[str] = []
        for line in text.split("\n"):
            s = line.strip()
            if _TABLE_SEP_RE.match(s):
                continue
            if s.startswith("|") and s.endswith("|"):
                cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
                if cells:
                    fixed_lines.append(" · ".join(cells))
                continue
            fixed_lines.append(line)
        text = "\n".join(fixed_lines)

    text = _RESIDUAL_HEADING_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def plain_text_fallback(text: str) -> str:
    """Safe plain representation when structured rendering fails."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = _HTML_BR_RE.sub("\n", t)
    t = _HTML_A_RE.sub(r"\2 (\1)", t)
    t = _RESIDUAL_HTML_RE.sub("", t)
    t = html.unescape(t)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", t)
    lines = []
    for line in t.split("\n"):
        s = line.strip()
        if _TABLE_SEP_RE.match(s):
            continue
        if s.startswith("|") and "|" in s[1:]:
            cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
            lines.append(" · ".join(cells) if cells else "")
            continue
        lines.append(line)
    t = "\n".join(lines)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


# ---------------------------------------------------------------------------
# Public presentation API
# ---------------------------------------------------------------------------



def telegram_markdown_is_balanced(text: str) -> bool:
    """
    Structural check that Telegram legacy Markdown markers are balanced.
    Used by tests and as a post-render invariant — not a full Telegram parser.
    """
    if not text:
        return True
    # Remove fenced code blocks (```...```) from consideration
    stripped = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code `...`
    stripped = re.sub(r"`[^`]*`", "", stripped)
    # Remove links [label](url)
    stripped = re.sub(r"\[[^\]]*\]\([^)]*\)", "", stripped)
    # Remaining unescaped * and _ should be even counts
    # Count unescaped * and _
    stars = re.findall(r"(?<!\\)\*", stripped)
    unders = re.findall(r"(?<!\\)_", stripped)
    if len(stars) % 2 != 0:
        return False
    if len(unders) % 2 != 0:
        return False
    return True


def present_for_channel(raw: str, channel: str) -> str:
    """
    Full presentation pipeline for a channel.

    Returns channel-safe text. Never raises to the caller for ordinary content;
    falls back to plain text on failure.
    """
    channel = (channel or "whatsapp").lower()
    if channel not in ("whatsapp", "telegram"):
        try:
            return normalize_for_channel(plain_text_fallback(raw or ""), channel)
        except Exception:
            return (raw or "").strip()

    try:
        doc = parse_model_output(raw or "")
        if channel == "whatsapp":
            rendered = render_whatsapp(doc)
        else:
            rendered = render_telegram(doc)
        normalized = normalize_for_channel(rendered, channel)
        logger.info(
            "presentation_ok",
            channel=channel,
            blocks=len(doc.blocks),
            in_chars=len(raw or ""),
            out_chars=len(normalized),
        )
        return normalized
    except Exception as e:
        logger.warning(
            "presentation_fallback",
            channel=channel,
            error=str(e)[:200],
        )
        try:
            return normalize_for_channel(plain_text_fallback(raw or ""), channel)
        except Exception:
            return (raw or "").strip()


def render_and_chunk(raw: str, channel: str) -> list[str]:
    """Present for channel, then structure-aware chunk. Preferred delivery path."""
    text = present_for_channel(raw, channel)
    profile = get_profile(channel)
    return chunk_message(text, max_chars=profile.max_text_chars)
