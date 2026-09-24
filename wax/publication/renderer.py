"""
Trusted WAX publication renderer.

AI never supplies HTML/CSS/JS. This module owns presentation, branding,
escaping, accessibility, and responsive layout.
"""

from __future__ import annotations

import base64
import html as html_lib
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from wax.publication.schema import (
    Block,
    BlockType,
    CalloutTone,
    PublicationDocument,
    TableRow,
)

RENDERER_VERSION = "1.0"

# Brand asset (baked into image)
_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "brand" / "wax-prep-logo.png"
_LOGO_DATA_URI: Optional[str] = None


def _logo_data_uri() -> str:
    global _LOGO_DATA_URI
    if _LOGO_DATA_URI is not None:
        return _LOGO_DATA_URI
    try:
        data = _LOGO_PATH.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        _LOGO_DATA_URI = f"data:image/png;base64,{b64}"
    except Exception:
        _LOGO_DATA_URI = ""
    return _LOGO_DATA_URI


def _esc(s: Any) -> str:
    return html_lib.escape("" if s is None else str(s), quote=True)


def _safe_href(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    if not u:
        return None
    lower = u.lower()
    if lower.startswith(("javascript:", "data:", "vbscript:")):
        return None
    if lower.startswith(("http://", "https://", "mailto:", "/")):
        return u
    return None


def _render_text(text: Optional[str]) -> str:
    if not text:
        return ""
    # Preserve intentional line breaks as <br>, escape everything else
    parts = _esc(text).split("\n")
    return "<br>\n".join(parts)


def _render_block(block: Block, *, depth: int = 0) -> str:
    t = block.type
    if isinstance(t, str):
        try:
            t = BlockType(t)
        except ValueError:
            t = BlockType.PARAGRAPH

    if t == BlockType.TITLE:
        return f'<h1 class="wx-title">{_render_text(block.text or block.title)}</h1>'
    if t == BlockType.SUBTITLE:
        return f'<p class="wx-subtitle">{_render_text(block.text)}</p>'
    if t == BlockType.HEADING:
        level = max(1, min(4, block.level or 2))
        tag = f"h{level}"
        anchor = f' id="{_esc(block.id)}"' if block.id else ""
        return f'<{tag} class="wx-h{level}"{anchor}>{_render_text(block.text)}</{tag}>'
    if t == BlockType.PARAGRAPH:
        return f'<p class="wx-p">{_render_text(block.text)}</p>'
    if t == BlockType.RICH_TEXT and block.spans:
        bits = []
        for sp in block.spans:
            frag = _esc(sp.text)
            if sp.code:
                frag = f"<code>{frag}</code>"
            if sp.bold:
                frag = f"<strong>{frag}</strong>"
            if sp.italic:
                frag = f"<em>{frag}</em>"
            href = _safe_href(sp.href)
            if href:
                frag = f'<a href="{_esc(href)}" rel="noopener noreferrer" target="_blank">{frag}</a>'
            bits.append(frag)
        return f'<p class="wx-p">{"".join(bits)}</p>'
    if t == BlockType.SECTION:
        kids = "".join(_render_block(c, depth=depth + 1) for c in (block.children or []))
        title = f'<h2 class="wx-h2">{_render_text(block.title or block.text)}</h2>' if (block.title or block.text) else ""
        return f'<section class="wx-section">{title}{kids}</section>'
    if t in (BlockType.LIST, BlockType.ORDERED_LIST):
        tag = "ol" if t == BlockType.ORDERED_LIST else "ul"
        items = block.items or []
        lis = "".join(f"<li>{_render_text(it)}</li>" for it in items)
        return f'<{tag} class="wx-list">{lis}</{tag}>'
    if t == BlockType.TABLE and block.rows:
        return _render_table(block.rows)
    if t == BlockType.DEFINITION:
        return (
            f'<dl class="wx-def"><dt>{_render_text(block.term)}</dt>'
            f"<dd>{_render_text(block.definition)}</dd></dl>"
        )
    if t == BlockType.CALLOUT:
        tone = (block.tone.value if hasattr(block.tone, "value") else block.tone) or "info"
        tone = _esc(str(tone))
        title = f'<div class="wx-callout-title">{_render_text(block.title)}</div>' if block.title else ""
        return (
            f'<aside class="wx-callout wx-callout-{tone}" role="note">'
            f"{title}<div class=\"wx-callout-body\">{_render_text(block.text)}</div></aside>"
        )
    if t == BlockType.QUOTE:
        return f'<blockquote class="wx-quote">{_render_text(block.text)}</blockquote>'
    if t == BlockType.CODE:
        lang = _esc(block.language or "")
        return f'<pre class="wx-code" data-lang="{lang}"><code>{_esc(block.text or "")}</code></pre>'
    if t == BlockType.FORMULA:
        return f'<div class="wx-formula" role="math">{_esc(block.text or "")}</div>'
    if t == BlockType.DIVIDER:
        return '<hr class="wx-divider"/>'
    if t == BlockType.LINK:
        href = _safe_href(block.href)
        label = _render_text(block.label or block.text or href or "Link")
        if not href:
            return f'<span class="wx-link-dead">{label}</span>'
        return f'<a class="wx-link" href="{_esc(href)}" rel="noopener noreferrer" target="_blank">{label}</a>'
    if t == BlockType.ARTIFACT and block.artifact:
        return _render_artifact_card(block.artifact.model_dump() if hasattr(block.artifact, "model_dump") else block.artifact)
    if t == BlockType.IMAGE and block.media:
        return _render_media(block.media.model_dump() if hasattr(block.media, "model_dump") else block.media)
    if t == BlockType.MEDIA and block.media:
        return _render_media(block.media.model_dump() if hasattr(block.media, "model_dump") else block.media)
    if t == BlockType.CARD:
        kids = "".join(_render_block(c, depth=depth + 1) for c in (block.children or []))
        title = f'<div class="wx-card-title">{_render_text(block.title)}</div>' if block.title else ""
        return f'<div class="wx-card">{title}{kids}</div>'
    if t == BlockType.COLUMNS and block.columns:
        cols = []
        for col in block.columns:
            inner = "".join(_render_block(c, depth=depth + 1) for c in col)
            cols.append(f'<div class="wx-col">{inner}</div>')
        return f'<div class="wx-columns">{"".join(cols)}</div>'
    if t == BlockType.GRID and block.children:
        kids = "".join(f'<div class="wx-grid-item">{_render_block(c, depth=depth + 1)}</div>' for c in block.children)
        return f'<div class="wx-grid">{kids}</div>'
    if t == BlockType.REFERENCES and block.items:
        lis = "".join(f"<li>{_render_text(it)}</li>" for it in block.items)
        return f'<section class="wx-refs"><h3 class="wx-h3">References</h3><ol>{lis}</ol></section>'
    if t == BlockType.TIMELINE and block.items:
        lis = "".join(f'<li class="wx-tl-item">{_render_text(it)}</li>' for it in block.items)
        return f'<ol class="wx-timeline">{lis}</ol>'
    if t == BlockType.STATUS:
        return f'<p class="wx-status">{_render_text(block.text)}</p>'
    if t == BlockType.EXPANDABLE:
        summary = _render_text(block.title or "Details")
        body = "".join(_render_block(c, depth=depth + 1) for c in (block.children or []))
        if block.text:
            body = f"<p>{_render_text(block.text)}</p>" + body
        return f'<details class="wx-expand"><summary>{summary}</summary><div class="wx-expand-body">{body}</div></details>'
    if t == BlockType.METADATA and block.meta:
        rows = "".join(
            f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in block.meta.items()
        )
        return f'<table class="wx-meta"><tbody>{rows}</tbody></table>'
    # Fallback: treat as paragraph
    return f'<p class="wx-p">{_render_text(block.text or block.title)}</p>'


def _render_table(rows: list[TableRow] | list[Any]) -> str:
    body_rows = []
    for i, row in enumerate(rows):
        cells = row.cells if hasattr(row, "cells") else (row.get("cells") if isinstance(row, dict) else [])
        tds = []
        for cell in cells or []:
            if hasattr(cell, "text"):
                txt, is_h, align = cell.text, cell.header, cell.align
            else:
                txt = cell.get("text", "") if isinstance(cell, dict) else str(cell)
                is_h = bool(cell.get("header")) if isinstance(cell, dict) else (i == 0)
                align = (cell.get("align") if isinstance(cell, dict) else None) or "left"
            tag = "th" if is_h or i == 0 else "td"
            tds.append(f'<{tag} style="text-align:{_esc(align)}">{_render_text(txt)}</{tag}>')
        body_rows.append(f"<tr>{''.join(tds)}</tr>")
    return (
        '<div class="wx-table-wrap"><table class="wx-table">'
        f"<tbody>{''.join(body_rows)}</tbody></table></div>"
    )


def _render_artifact_card(ref: dict[str, Any] | Any) -> str:
    if hasattr(ref, "model_dump"):
        ref = ref.model_dump()
    label = _esc(ref.get("label") or "Download file")
    desc = _render_text(ref.get("description") or "")
    aid = _esc(ref.get("artifact_id") or "")
    # Download URL is filled by publication service via placeholder replacement
    return (
        f'<div class="wx-artifact" data-artifact-id="{aid}">'
        f'<div class="wx-artifact-icon" aria-hidden="true">📄</div>'
        f'<div class="wx-artifact-body"><div class="wx-artifact-label">{label}</div>'
        f'{f"<p class=wx-artifact-desc>{desc}</p>" if desc else ""}'
        f'<a class="wx-btn" href="{{{{ARTIFACT_URL:{aid}}}}}" rel="noopener">Download</a>'
        f"</div></div>"
    )


def _render_media(ref: dict[str, Any] | Any) -> str:
    if hasattr(ref, "model_dump"):
        ref = ref.model_dump()
    alt = _esc(ref.get("alt") or "")
    caption = _render_text(ref.get("caption") or "")
    kind = ref.get("media_kind") or "image"
    aid = ref.get("artifact_id")
    url = _safe_href(ref.get("url"))
    src = f"{{{{ARTIFACT_URL:{_esc(aid)}}}}}" if aid else (url or "")
    if not src:
        return ""
    if kind == "image":
        img = f'<img class="wx-img" src="{_esc(src)}" alt="{alt}" loading="lazy"/>'
        if caption:
            return f'<figure class="wx-figure">{img}<figcaption>{caption}</figcaption></figure>'
        return img
    if kind == "audio":
        return f'<audio class="wx-audio" controls src="{_esc(src)}"></audio>'
    if kind == "video":
        return f'<video class="wx-video" controls src="{_esc(src)}"></video>'
    return f'<a class="wx-link" href="{_esc(src)}">{alt or "File"}</a>'


def render_document(
    doc: PublicationDocument,
    *,
    expires_at_iso: Optional[str] = None,
    public_token: Optional[str] = None,
) -> str:
    """Produce full HTML document string (immutable snapshot)."""
    logo = _logo_data_uri()
    logo_html = (
        f'<img class="wx-logo" src="{logo}" alt="WAX Prep" width="120" height="120"/>'
        if logo
        else '<span class="wx-logo-text">WAX</span>'
    )
    body_blocks = "".join(_render_block(b) for b in doc.blocks)
    subtitle = f'<p class="wx-subtitle">{_render_text(doc.subtitle)}</p>' if doc.subtitle else ""
    summary = f'<p class="wx-summary">{_render_text(doc.summary)}</p>' if doc.summary else ""
    share_title = _esc(doc.share_title or doc.title)
    share_desc = _esc(doc.share_description or doc.summary or "Prepared for you by WAX Prep")
    expiry_note = ""
    if expires_at_iso:
        expiry_note = f'<p class="wx-expiry">Temporary surface · available until {_esc(expires_at_iso[:16].replace("T", " "))} UTC</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex, nofollow, noarchive"/>
<meta name="referrer" content="no-referrer"/>
<meta property="og:title" content="{share_title}"/>
<meta property="og:description" content="{share_desc}"/>
<meta property="og:type" content="article"/>
<meta name="theme-color" content="#0B1F3A"/>
<title>{_esc(doc.title)} · WAX Prep</title>
<style>
{ _CSS }
</style>
</head>
<body>
<div class="wx-shell">
  <header class="wx-header">
    <div class="wx-brand">
      {logo_html}
      <div class="wx-brand-text">
        <span class="wx-brand-name">WAX PREP</span>
        <span class="wx-brand-tag">The tutor that actually knows you</span>
      </div>
    </div>
  </header>
  <main class="wx-main">
    <article class="wx-article">
      <h1 class="wx-title">{_esc(doc.title)}</h1>
      {subtitle}
      {summary}
      <div class="wx-content">
        {body_blocks}
      </div>
    </article>
  </main>
  <footer class="wx-footer">
    {expiry_note}
    <p class="wx-footer-brand">Prepared by WAX Prep · temporary learning surface</p>
  </footer>
</div>
</body>
</html>
"""


_CSS = """
:root {
  --wx-bg: #f6f8fb;
  --wx-surface: #ffffff;
  --wx-ink: #0f172a;
  --wx-muted: #64748b;
  --wx-accent: #00c853;
  --wx-navy: #0B1F3A;
  --wx-border: #e2e8f0;
  --wx-radius: 14px;
  --wx-shadow: 0 10px 40px rgba(11, 31, 58, 0.06);
  --wx-font: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
  --wx-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --wx-max: 720px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --wx-bg: #0a1220;
    --wx-surface: #111b2e;
    --wx-ink: #e8eef7;
    --wx-muted: #94a3b8;
    --wx-border: #1e2d45;
    --wx-shadow: 0 10px 40px rgba(0,0,0,0.35);
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  font-family: var(--wx-font);
  background: var(--wx-bg);
  color: var(--wx-ink);
  line-height: 1.6;
  font-size: 16px;
}
.wx-shell { min-height: 100vh; display: flex; flex-direction: column; }
.wx-header {
  padding: 1.25rem 1.25rem 0.75rem;
  border-bottom: 1px solid var(--wx-border);
  background: var(--wx-surface);
}
.wx-brand { display: flex; align-items: center; gap: 0.85rem; max-width: var(--wx-max); margin: 0 auto; }
.wx-logo {
  width: 52px; height: 52px; object-fit: contain; border-radius: 12px;
  box-shadow: 0 2px 10px rgba(0,200,83,0.15);
}
.wx-logo-text {
  font-weight: 800; font-size: 1.4rem; color: var(--wx-navy);
  background: var(--wx-accent); color: #fff; padding: 0.35rem 0.55rem; border-radius: 10px;
}
.wx-brand-text { display: flex; flex-direction: column; gap: 0.1rem; }
.wx-brand-name {
  font-weight: 800; letter-spacing: 0.06em; font-size: 0.95rem; color: var(--wx-navy);
}
@media (prefers-color-scheme: dark) {
  .wx-brand-name { color: #e8eef7; }
}
.wx-brand-tag { font-size: 0.78rem; color: var(--wx-muted); }
.wx-main { flex: 1; padding: 1.5rem 1.25rem 2.5rem; }
.wx-article {
  max-width: var(--wx-max); margin: 0 auto;
  background: var(--wx-surface); border-radius: var(--wx-radius);
  box-shadow: var(--wx-shadow); padding: 1.75rem 1.5rem 2rem;
  border: 1px solid var(--wx-border);
  animation: wx-in 0.45s ease both;
}
@keyframes wx-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .wx-article { animation: none; }
}
.wx-title {
  font-size: clamp(1.35rem, 4vw, 1.75rem);
  line-height: 1.25; margin: 0 0 0.5rem; font-weight: 750; letter-spacing: -0.02em;
}
.wx-subtitle { color: var(--wx-muted); margin: 0 0 0.75rem; font-size: 1.05rem; }
.wx-summary {
  color: var(--wx-muted); margin: 0 0 1.25rem; padding-bottom: 1rem;
  border-bottom: 1px solid var(--wx-border); font-size: 0.95rem;
}
.wx-content > *:first-child { margin-top: 0; }
.wx-p { margin: 0.85rem 0; }
.wx-h2 { font-size: 1.2rem; margin: 1.6rem 0 0.6rem; font-weight: 700; }
.wx-h3 { font-size: 1.05rem; margin: 1.3rem 0 0.5rem; font-weight: 700; }
.wx-h4 { font-size: 1rem; margin: 1.1rem 0 0.4rem; font-weight: 650; }
.wx-list { margin: 0.75rem 0; padding-left: 1.25rem; }
.wx-list li { margin: 0.3rem 0; }
.wx-table-wrap { overflow-x: auto; margin: 1rem 0; -webkit-overflow-scrolling: touch; }
.wx-table {
  width: 100%; border-collapse: collapse; font-size: 0.92rem;
  min-width: 280px;
}
.wx-table th, .wx-table td {
  border: 1px solid var(--wx-border); padding: 0.55rem 0.7rem; text-align: left;
}
.wx-table th { background: rgba(11,31,58,0.04); font-weight: 650; }
@media (prefers-color-scheme: dark) {
  .wx-table th { background: rgba(255,255,255,0.04); }
}
.wx-callout {
  margin: 1rem 0; padding: 0.9rem 1rem; border-radius: 12px;
  border-left: 4px solid var(--wx-accent); background: rgba(0,200,83,0.06);
}
.wx-callout-warning { border-left-color: #f59e0b; background: rgba(245,158,11,0.08); }
.wx-callout-note { border-left-color: #3b82f6; background: rgba(59,130,246,0.08); }
.wx-callout-tip { border-left-color: var(--wx-accent); }
.wx-callout-title { font-weight: 700; margin-bottom: 0.25rem; }
.wx-quote {
  margin: 1rem 0; padding: 0.75rem 1rem; border-left: 3px solid var(--wx-border);
  color: var(--wx-muted); font-style: italic;
}
.wx-code {
  margin: 1rem 0; padding: 0.9rem 1rem; border-radius: 10px;
  background: #0f172a; color: #e2e8f0; overflow-x: auto; font-family: var(--wx-mono);
  font-size: 0.88rem; line-height: 1.45;
}
.wx-formula {
  margin: 0.9rem 0; padding: 0.75rem 1rem; text-align: center;
  font-family: var(--wx-mono); background: rgba(11,31,58,0.04); border-radius: 10px;
}
.wx-divider { border: none; border-top: 1px solid var(--wx-border); margin: 1.5rem 0; }
.wx-card {
  border: 1px solid var(--wx-border); border-radius: 12px; padding: 1rem;
  margin: 0.9rem 0; background: var(--wx-surface);
}
.wx-card-title { font-weight: 700; margin-bottom: 0.4rem; }
.wx-columns { display: grid; gap: 1rem; margin: 1rem 0; }
@media (min-width: 640px) {
  .wx-columns { grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
}
.wx-grid { display: grid; gap: 0.75rem; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
.wx-artifact {
  display: flex; gap: 0.85rem; align-items: flex-start;
  border: 1px solid var(--wx-border); border-radius: 12px; padding: 0.9rem 1rem;
  margin: 1rem 0; background: rgba(0,200,83,0.04);
}
.wx-artifact-icon { font-size: 1.4rem; line-height: 1; }
.wx-artifact-label { font-weight: 650; }
.wx-artifact-desc { margin: 0.25rem 0 0.5rem; color: var(--wx-muted); font-size: 0.9rem; }
.wx-btn {
  display: inline-block; margin-top: 0.25rem; padding: 0.45rem 0.9rem;
  background: var(--wx-navy); color: #fff !important; text-decoration: none;
  border-radius: 999px; font-size: 0.88rem; font-weight: 600;
}
.wx-btn:hover { filter: brightness(1.08); }
.wx-img { max-width: 100%; height: auto; border-radius: 10px; display: block; }
.wx-figure { margin: 1rem 0; }
.wx-figure figcaption { font-size: 0.85rem; color: var(--wx-muted); margin-top: 0.4rem; }
.wx-link { color: #0d9488; text-decoration: underline; text-underline-offset: 2px; }
.wx-expand { margin: 0.9rem 0; border: 1px solid var(--wx-border); border-radius: 10px; padding: 0.5rem 0.85rem; }
.wx-expand summary { cursor: pointer; font-weight: 600; }
.wx-timeline { list-style: none; padding-left: 1.1rem; border-left: 2px solid var(--wx-border); margin: 1rem 0; }
.wx-tl-item { margin: 0.55rem 0; position: relative; }
.wx-tl-item::before {
  content: ""; position: absolute; left: -1.35rem; top: 0.45rem;
  width: 8px; height: 8px; border-radius: 50%; background: var(--wx-accent);
}
.wx-footer {
  max-width: var(--wx-max); margin: 0 auto; padding: 0 1.25rem 2rem;
  color: var(--wx-muted); font-size: 0.82rem; text-align: center;
}
.wx-expiry { margin: 0 0 0.35rem; }
.wx-footer-brand { margin: 0; opacity: 0.85; }
.wx-meta { font-size: 0.88rem; margin: 0.75rem 0; }
.wx-meta th { text-align: left; padding-right: 1rem; color: var(--wx-muted); font-weight: 600; }
@media print {
  body { background: #fff; color: #000; }
  .wx-header, .wx-footer { display: none; }
  .wx-article { box-shadow: none; border: none; padding: 0; }
}
"""


def render_expired_page(*, reason: str = "expired") -> str:
    logo = _logo_data_uri()
    logo_html = (
        f'<img class="wx-logo" src="{logo}" alt="WAX Prep" width="64" height="64"/>'
        if logo
        else '<span class="wx-logo-text">WAX</span>'
    )
    msg = {
        "expired": "This temporary surface is no longer available.",
        "revoked": "This surface has been withdrawn.",
        "not_found": "We couldn't find this surface.",
        "forbidden": "You don't have access to this surface.",
    }.get(reason, "This surface is unavailable.")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex, nofollow"/>
<title>Unavailable · WAX Prep</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wx-shell">
  <header class="wx-header">
    <div class="wx-brand">{logo_html}
      <div class="wx-brand-text">
        <span class="wx-brand-name">WAX PREP</span>
        <span class="wx-brand-tag">The tutor that actually knows you</span>
      </div>
    </div>
  </header>
  <main class="wx-main">
    <article class="wx-article" style="text-align:center">
      <h1 class="wx-title">Surface unavailable</h1>
      <p class="wx-subtitle">{_esc(msg)}</p>
      <p class="wx-p">Return to WAX on WhatsApp or Telegram if you need it again — WAX can prepare a fresh one for you.</p>
    </article>
  </main>
  <footer class="wx-footer">
    <p class="wx-footer-brand">WAX Prep · temporary learning surfaces</p>
  </footer>
</div>
</body>
</html>
"""
