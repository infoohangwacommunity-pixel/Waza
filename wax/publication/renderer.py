"""
Trusted WAX publication renderer v1.1

Registry of specialized node renderers. AI never supplies HTML/CSS/JS.
"""

from __future__ import annotations

import base64
import html as html_lib
import re
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from wax.publication.planner import PlannedDocument, plan_document
from wax.publication.schema import (
    Node,
    NodeType,
    PublicationDocument,
    TableData,
    TableRow,
)

RENDERER_VERSION = "1.1"

_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "brand" / "wax-prep-logo.png"
_LOGO_DATA_URI: Optional[str] = None

NodeRenderer = Callable[[Node, "RenderContext"], str]


class RenderContext:
    def __init__(
        self,
        plan: PlannedDocument,
        artifact_urls: Optional[dict[str, str]] = None,
        expires_at_iso: Optional[str] = None,
    ):
        self.plan = plan
        self.artifact_urls = artifact_urls or {}
        self.expires_at_iso = expires_at_iso


def _logo_data_uri() -> str:
    global _LOGO_DATA_URI
    if _LOGO_DATA_URI is not None:
        return _LOGO_DATA_URI
    try:
        data = _LOGO_PATH.read_bytes()
        _LOGO_DATA_URI = f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"
    except Exception:
        _LOGO_DATA_URI = ""
    return _LOGO_DATA_URI


def _esc(s: Any) -> str:
    return html_lib.escape("" if s is None else str(s), quote=True)


def _safe_href(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    if not u or len(u) > 2000:
        return None
    lower = u.lower()
    if lower.startswith(("javascript:", "data:", "vbscript:", "file:")):
        return None
    if lower.startswith("mailto:"):
        # mailto has empty netloc; path holds address
        try:
            p = urlparse(u)
            if p.scheme != "mailto" or not p.path:
                return None
        except Exception:
            return None
        return u
    if lower.startswith(("http://", "https://")):
        try:
            p = urlparse(u)
            if not p.scheme or not p.netloc:
                return None
        except Exception:
            return None
        return u
    if u.startswith("/") and not u.startswith("//"):
        return u
    return None


def _render_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return "<br>\n".join(_esc(text).split("\n"))


def _node_type(n: Node) -> Optional[NodeType]:
    t = n.type
    if isinstance(t, NodeType):
        return t
    try:
        return NodeType(str(t))
    except ValueError:
        return None


# ─── Specialized renderers ────────────────────────────────────────────────────

def r_paragraph(n: Node, ctx: RenderContext) -> str:
    return f'<p class="wx-p">{_render_text(n.text)}</p>'


def r_heading(n: Node, ctx: RenderContext) -> str:
    level = max(1, min(4, n.level or 2))
    tag = f"h{level}"
    anchor = f' id="{_esc(n.id)}"' if n.id else ""
    return f'<{tag} class="wx-h{level}"{anchor}>{_render_text(n.text or n.title)}</{tag}>'


def r_rich_text(n: Node, ctx: RenderContext) -> str:
    if not n.spans:
        return r_paragraph(n, ctx)
    bits = []
    for sp in n.spans:
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


def r_section(n: Node, ctx: RenderContext) -> str:
    kids = "".join(render_node(c, ctx) for c in (n.children or []))
    title = ""
    if n.title or n.text:
        anchor = f' id="{_esc(n.id)}"' if n.id else ""
        title = f'<h2 class="wx-h2"{anchor}>{_render_text(n.title or n.text)}</h2>'
    return f'<section class="wx-section">{title}{kids}</section>'


def r_list(n: Node, ctx: RenderContext) -> str:
    tag = "ol" if _node_type(n) == NodeType.ORDERED_LIST else "ul"
    items = n.items or []
    lis = "".join(f"<li>{_render_text(it)}</li>" for it in items)
    return f'<{tag} class="wx-list">{lis}</{tag}>'


def r_table(n: Node, ctx: RenderContext) -> str:
    table = n.table
    rows = table.rows if table else (n.rows or [])
    caption = (table.caption if table else None) or n.title
    compact = bool(table and table.compact) or bool(n.present and n.present.compact)
    return _render_table(rows, caption=caption, compact=compact)


def _render_table(rows: list[TableRow] | list[Any], *, caption: Optional[str] = None, compact: bool = False) -> str:
    if not rows:
        return ""
    body_rows = []
    for i, row in enumerate(rows):
        cells = row.cells if hasattr(row, "cells") else (row.get("cells") if isinstance(row, dict) else [])
        tds = []
        for cell in cells or []:
            if hasattr(cell, "text"):
                txt, is_h, align = cell.text, cell.header, getattr(cell, "align", "left") or "left"
                emphasis = getattr(cell, "emphasis", False)
            else:
                txt = cell.get("text", "") if isinstance(cell, dict) else str(cell)
                is_h = bool(cell.get("header")) if isinstance(cell, dict) else (i == 0)
                align = (cell.get("align") if isinstance(cell, dict) else None) or "left"
                emphasis = bool(cell.get("emphasis")) if isinstance(cell, dict) else False
            tag = "th" if is_h or i == 0 else "td"
            cls = ' class="wx-em"' if emphasis else ""
            scope = ' scope="col"' if tag == "th" and i == 0 else (' scope="row"' if tag == "th" else "")
            tds.append(f'<{tag}{scope}{cls} style="text-align:{_esc(align)}">{_render_text(txt)}</{tag}>')
        body_rows.append(f"<tr>{''.join(tds)}</tr>")
    cap = f"<caption>{_render_text(caption)}</caption>" if caption else ""
    cls = "wx-table wx-table-compact" if compact else "wx-table"
    return (
        f'<div class="wx-table-wrap" role="region" aria-label="Table" tabindex="0">'
        f'<table class="{cls}">{cap}<tbody>{"".join(body_rows)}</tbody></table></div>'
    )


def r_definition(n: Node, ctx: RenderContext) -> str:
    return (
        f'<dl class="wx-def"><dt>{_render_text(n.term)}</dt>'
        f"<dd>{_render_text(n.definition)}</dd></dl>"
    )


def r_callout(n: Node, ctx: RenderContext) -> str:
    tone = (n.tone.value if hasattr(n.tone, "value") else n.tone) or "info"
    tone = re.sub(r"[^a-z]", "", str(tone).lower())[:20] or "info"
    title = f'<div class="wx-callout-title">{_render_text(n.title)}</div>' if n.title else ""
    return (
        f'<aside class="wx-callout wx-callout-{_esc(tone)}" role="note">'
        f'{title}<div class="wx-callout-body">{_render_text(n.text)}</div></aside>'
    )


def r_quote(n: Node, ctx: RenderContext) -> str:
    return f'<blockquote class="wx-quote">{_render_text(n.text)}</blockquote>'


def r_code(n: Node, ctx: RenderContext) -> str:
    lang = _esc(n.language or "")
    return f'<pre class="wx-code" data-lang="{lang}"><code>{_esc(n.text or "")}</code></pre>'


def r_formula(n: Node, ctx: RenderContext) -> str:
    return f'<div class="wx-formula" role="math">{_esc(n.text or "")}</div>'


def r_divider(n: Node, ctx: RenderContext) -> str:
    return '<hr class="wx-divider"/>'


def r_link(n: Node, ctx: RenderContext) -> str:
    href = _safe_href(n.href)
    label = _render_text(n.label or n.text or href or "Link")
    if not href:
        return f'<span class="wx-link-dead">{label}</span>'
    return f'<a class="wx-link" href="{_esc(href)}" rel="noopener noreferrer" target="_blank">{label}</a>'


def r_artifact(n: Node, ctx: RenderContext) -> str:
    ref = n.artifact
    if not ref:
        return ""
    aid = str(ref.artifact_id)
    label = _esc(ref.label or ref.filename or "Download file")
    desc = _render_text(ref.description or "")
    meta_bits = []
    if ref.content_type:
        meta_bits.append(_esc(ref.content_type.split(";")[0]))
    if ref.size_bytes:
        kb = ref.size_bytes / 1024
        meta_bits.append(f"{kb:.0f} KB" if kb < 1024 else f"{kb/1024:.1f} MB")
    meta = f'<div class="wx-artifact-meta">{" · ".join(meta_bits)}</div>' if meta_bits else ""
    url = ctx.artifact_urls.get(aid, "#")
    dl = ""
    if ref.show_download:
        dl = f'<a class="wx-btn" href="{_esc(url)}" rel="noopener">Download</a>'
    return (
        f'<div class="wx-artifact" data-artifact-ref="1">'
        f'<div class="wx-artifact-icon" aria-hidden="true">📄</div>'
        f'<div class="wx-artifact-body"><div class="wx-artifact-label">{label}</div>'
        f'{(("<p class=\"wx-artifact-desc\">" + desc + "</p>") if desc else "")}'
        f"{meta}{dl}</div></div>"
    )


def r_media(n: Node, ctx: RenderContext) -> str:
    ref = n.media
    if not ref:
        return ""
    alt = _esc(ref.alt or "")
    caption = _render_text(ref.caption or "")
    kind = ref.media_kind or "image"
    if ref.artifact_id:
        src = ctx.artifact_urls.get(str(ref.artifact_id), "")
    else:
        src = _safe_href(ref.url) or ""
    if not src:
        return ""
    if kind == "image":
        img = f'<img class="wx-img" src="{_esc(src)}" alt="{alt}" loading="lazy"/>'
        if caption:
            return f'<figure class="wx-figure">{img}<figcaption>{caption}</figcaption></figure>'
        return img
    if kind == "audio":
        return f'<audio class="wx-audio" controls preload="metadata" src="{_esc(src)}">Audio</audio>'
    if kind == "video":
        return f'<video class="wx-video" controls preload="metadata" src="{_esc(src)}">Video</video>'
    return f'<a class="wx-link" href="{_esc(src)}">{alt or "File"}</a>'


def r_card(n: Node, ctx: RenderContext) -> str:
    kids = "".join(render_node(c, ctx) for c in (n.children or []))
    title = f'<div class="wx-card-title">{_render_text(n.title)}</div>' if n.title else ""
    return f'<div class="wx-card">{title}{kids}</div>'


def r_columns(n: Node, ctx: RenderContext) -> str:
    cols = []
    for col in n.columns or []:
        inner = "".join(render_node(c, ctx) for c in col)
        cols.append(f'<div class="wx-col">{inner}</div>')
    return f'<div class="wx-columns">{"".join(cols)}</div>'


def r_grid(n: Node, ctx: RenderContext) -> str:
    kids = "".join(f'<div class="wx-grid-item">{render_node(c, ctx)}</div>' for c in (n.children or []))
    return f'<div class="wx-grid">{kids}</div>'


def r_references(n: Node, ctx: RenderContext) -> str:
    if n.sources:
        lis = []
        for s in n.sources:
            href = _safe_href(s.href)
            label = _esc(s.label)
            if href:
                lis.append(f'<li><a href="{_esc(href)}" rel="noopener noreferrer" target="_blank">{label}</a>'
                           f'{f" — {_esc(s.note)}" if s.note else ""}</li>')
            else:
                lis.append(f"<li>{label}{f' — {_esc(s.note)}' if s.note else ''}</li>")
        return f'<section class="wx-refs"><h3 class="wx-h3">References</h3><ol>{"".join(lis)}</ol></section>'
    items = n.items or []
    lis = "".join(f"<li>{_render_text(it)}</li>" for it in items)
    return f'<section class="wx-refs"><h3 class="wx-h3">References</h3><ol>{lis}</ol></section>'


def r_timeline(n: Node, ctx: RenderContext) -> str:
    lis = "".join(f'<li class="wx-tl-item">{_render_text(it)}</li>' for it in (n.items or []))
    return f'<ol class="wx-timeline">{lis}</ol>'


def r_status(n: Node, ctx: RenderContext) -> str:
    return f'<p class="wx-status">{_render_text(n.text)}</p>'


def r_expandable(n: Node, ctx: RenderContext) -> str:
    summary = _render_text(n.title or "Details")
    body = "".join(render_node(c, ctx) for c in (n.children or []))
    if n.text:
        body = f"<p>{_render_text(n.text)}</p>" + body
    return f'<details class="wx-expand"><summary>{summary}</summary><div class="wx-expand-body">{body}</div></details>'


def r_metadata(n: Node, ctx: RenderContext) -> str:
    if not n.meta:
        return ""
    rows = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in n.meta.items())
    return f'<table class="wx-meta"><tbody>{rows}</tbody></table>'


def r_title(n: Node, ctx: RenderContext) -> str:
    return f'<h1 class="wx-title">{_render_text(n.text or n.title)}</h1>'


def r_subtitle(n: Node, ctx: RenderContext) -> str:
    return f'<p class="wx-subtitle">{_render_text(n.text)}</p>'


def r_fallback(n: Node, ctx: RenderContext) -> str:
    # Unknown node types: render children + text safely; never fail the whole page
    parts = []
    if n.title or n.text:
        parts.append(f'<p class="wx-p">{_render_text(n.title or n.text)}</p>')
    if n.children:
        parts.append("".join(render_node(c, ctx) for c in n.children))
    if n.items:
        parts.append(r_list(n, ctx))
    return "".join(parts) or ""


RENDERERS: dict[NodeType, NodeRenderer] = {
    NodeType.PARAGRAPH: r_paragraph,
    NodeType.HEADING: r_heading,
    NodeType.RICH_TEXT: r_rich_text,
    NodeType.SECTION: r_section,
    NodeType.LIST: r_list,
    NodeType.ORDERED_LIST: r_list,
    NodeType.TABLE: r_table,
    NodeType.DEFINITION: r_definition,
    NodeType.CALLOUT: r_callout,
    NodeType.QUOTE: r_quote,
    NodeType.CODE: r_code,
    NodeType.FORMULA: r_formula,
    NodeType.DIVIDER: r_divider,
    NodeType.LINK: r_link,
    NodeType.ARTIFACT: r_artifact,
    NodeType.IMAGE: r_media,
    NodeType.MEDIA: r_media,
    NodeType.CARD: r_card,
    NodeType.COLUMNS: r_columns,
    NodeType.GRID: r_grid,
    NodeType.REFERENCES: r_references,
    NodeType.TIMELINE: r_timeline,
    NodeType.STATUS: r_status,
    NodeType.EXPANDABLE: r_expandable,
    NodeType.METADATA: r_metadata,
    NodeType.TITLE: r_title,
    NodeType.SUBTITLE: r_subtitle,
}


def render_node(n: Node, ctx: RenderContext) -> str:
    t = _node_type(n)
    fn = RENDERERS.get(t) if t else None
    if fn:
        return fn(n, ctx)
    return r_fallback(n, ctx)


def _render_toc(plan: PlannedDocument) -> str:
    if not plan.show_toc or not plan.toc:
        return ""
    items = []
    for e in plan.toc:
        items.append(
            f'<li class="wx-toc-l{e.level}"><a href="#{_esc(e.id)}">{_esc(e.title)}</a></li>'
        )
    return (
        '<nav class="wx-toc" aria-label="Contents">'
        f'<div class="wx-toc-title">Contents</div><ol>{"".join(items)}</ol></nav>'
    )


def render_document(
    doc: PublicationDocument,
    *,
    expires_at_iso: Optional[str] = None,
    artifact_urls: Optional[dict[str, str]] = None,
    plan: Optional[PlannedDocument] = None,
) -> str:
    plan = plan or plan_document(doc)
    ctx = RenderContext(plan=plan, artifact_urls=artifact_urls, expires_at_iso=expires_at_iso)

    logo = _logo_data_uri()
    logo_html = (
        f'<img class="wx-logo" src="{logo}" alt="WAX Prep" width="52" height="52"/>'
        if logo
        else '<span class="wx-logo-text">WAX</span>'
    )
    body = "".join(render_node(n, ctx) for n in doc.nodes)
    toc = _render_toc(plan)
    subtitle = f'<p class="wx-subtitle">{_render_text(doc.subtitle)}</p>' if doc.subtitle else ""
    summary = f'<p class="wx-summary">{_render_text(doc.summary)}</p>' if doc.summary else ""
    share_title = _esc(doc.effective_share_title)
    share_desc = _esc(doc.effective_share_description)
    expiry_note = ""
    if expires_at_iso:
        expiry_note = (
            f'<p class="wx-expiry">Temporary surface · available until '
            f'{_esc(expires_at_iso[:16].replace("T", " "))} UTC</p>'
        )
    back = ""
    if plan.show_back_to_top:
        back = '<p class="wx-back"><a href="#wx-top">Back to top</a></p>'

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
{_CSS}
</style>
</head>
<body id="wx-top">
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
      {toc}
      <div class="wx-content">
        {body}
      </div>
      {back}
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


def render_expired_page(*, reason: str = "expired") -> str:
    logo = _logo_data_uri()
    logo_html = (
        f'<img class="wx-logo" src="{logo}" alt="WAX Prep" width="52" height="52"/>'
        if logo
        else '<span class="wx-logo-text">WAX</span>'
    )
    msg = {
        "expired": "This temporary surface is no longer available.",
        "revoked": "This surface has been withdrawn.",
        "not_found": "We couldn't find this surface.",
        "forbidden": "You don't have access to this surface.",
        "failed": "This surface could not be loaded.",
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


_CSS = """
:root {
  --wx-bg: #f4f7fb;
  --wx-surface: #ffffff;
  --wx-ink: #0f172a;
  --wx-muted: #64748b;
  --wx-accent: #00c853;
  --wx-navy: #0B1F3A;
  --wx-border: #e2e8f0;
  --wx-radius: 16px;
  --wx-shadow: 0 12px 40px rgba(11, 31, 58, 0.07);
  --wx-font: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
  --wx-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --wx-max: 740px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --wx-bg: #080f1a;
    --wx-surface: #0f1829;
    --wx-ink: #e8eef7;
    --wx-muted: #94a3b8;
    --wx-border: #1c2a40;
    --wx-shadow: 0 12px 40px rgba(0,0,0,0.4);
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth; }
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
}
body {
  margin: 0;
  font-family: var(--wx-font);
  background: var(--wx-bg);
  color: var(--wx-ink);
  line-height: 1.65;
  font-size: 16.5px;
  -webkit-font-smoothing: antialiased;
}
.wx-shell { min-height: 100vh; display: flex; flex-direction: column; }
.wx-header {
  padding: 1.1rem 1.25rem 0.85rem;
  border-bottom: 1px solid var(--wx-border);
  background: var(--wx-surface);
  position: sticky; top: 0; z-index: 10;
  backdrop-filter: blur(8px);
  background: color-mix(in srgb, var(--wx-surface) 92%, transparent);
}
.wx-brand { display: flex; align-items: center; gap: 0.85rem; max-width: var(--wx-max); margin: 0 auto; }
.wx-logo {
  width: 48px; height: 48px; object-fit: contain; border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0,200,83,0.18);
}
.wx-logo-text {
  font-weight: 800; font-size: 1.2rem; background: var(--wx-accent); color: #fff;
  padding: 0.35rem 0.55rem; border-radius: 10px;
}
.wx-brand-text { display: flex; flex-direction: column; gap: 0.05rem; }
.wx-brand-name {
  font-weight: 800; letter-spacing: 0.07em; font-size: 0.9rem; color: var(--wx-navy);
}
@media (prefers-color-scheme: dark) { .wx-brand-name { color: #e8eef7; } }
.wx-brand-tag { font-size: 0.75rem; color: var(--wx-muted); }
.wx-main { flex: 1; padding: 1.5rem 1.1rem 2.5rem; }
.wx-article {
  max-width: var(--wx-max); margin: 0 auto;
  background: var(--wx-surface); border-radius: var(--wx-radius);
  box-shadow: var(--wx-shadow); padding: 1.75rem 1.4rem 2rem;
  border: 1px solid var(--wx-border);
  animation: wx-in 0.4s ease both;
}
@keyframes wx-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .wx-article { animation: none; }
}
.wx-title {
  font-size: clamp(1.35rem, 4.2vw, 1.85rem);
  line-height: 1.22; margin: 0 0 0.45rem; font-weight: 750; letter-spacing: -0.025em;
}
.wx-subtitle { color: var(--wx-muted); margin: 0 0 0.7rem; font-size: 1.05rem; }
.wx-summary {
  color: var(--wx-muted); margin: 0 0 1.2rem; padding-bottom: 1rem;
  border-bottom: 1px solid var(--wx-border); font-size: 0.95rem;
}
.wx-toc {
  margin: 0 0 1.4rem; padding: 0.9rem 1rem; border-radius: 12px;
  background: rgba(11,31,58,0.03); border: 1px solid var(--wx-border);
}
@media (prefers-color-scheme: dark) {
  .wx-toc { background: rgba(255,255,255,0.03); }
}
.wx-toc-title { font-weight: 700; font-size: 0.85rem; margin-bottom: 0.4rem; letter-spacing: 0.02em; }
.wx-toc ol { margin: 0; padding-left: 1.15rem; }
.wx-toc li { margin: 0.25rem 0; }
.wx-toc a { color: inherit; text-decoration: none; border-bottom: 1px solid transparent; }
.wx-toc a:hover { border-bottom-color: var(--wx-accent); color: var(--wx-accent); }
.wx-toc-l3, .wx-toc-l4 { margin-left: 0.6rem; font-size: 0.92em; }
.wx-content > *:first-child { margin-top: 0; }
.wx-p { margin: 0.85rem 0; }
.wx-h2 { font-size: 1.22rem; margin: 1.7rem 0 0.55rem; font-weight: 700; scroll-margin-top: 4.5rem; }
.wx-h3 { font-size: 1.06rem; margin: 1.35rem 0 0.45rem; font-weight: 700; scroll-margin-top: 4.5rem; }
.wx-h4 { font-size: 1rem; margin: 1.15rem 0 0.4rem; font-weight: 650; scroll-margin-top: 4.5rem; }
.wx-list { margin: 0.75rem 0; padding-left: 1.25rem; }
.wx-list li { margin: 0.32rem 0; }
.wx-table-wrap {
  overflow-x: auto; margin: 1rem 0; -webkit-overflow-scrolling: touch;
  border-radius: 10px; border: 1px solid var(--wx-border);
}
.wx-table {
  width: 100%; border-collapse: collapse; font-size: 0.92rem; min-width: 260px;
}
.wx-table th, .wx-table td {
  border-bottom: 1px solid var(--wx-border); padding: 0.6rem 0.75rem; text-align: left;
  vertical-align: top;
}
.wx-table th { background: rgba(11,31,58,0.04); font-weight: 650; }
.wx-table tr:last-child td { border-bottom: none; }
.wx-table .wx-em { font-weight: 650; }
.wx-table-compact th, .wx-table-compact td { padding: 0.4rem 0.55rem; font-size: 0.88rem; }
.wx-table caption {
  caption-side: bottom; text-align: left; padding: 0.5rem 0.75rem;
  font-size: 0.85rem; color: var(--wx-muted);
}
@media (prefers-color-scheme: dark) {
  .wx-table th { background: rgba(255,255,255,0.04); }
}
.wx-callout {
  margin: 1rem 0; padding: 0.95rem 1.05rem; border-radius: 12px;
  border-left: 4px solid var(--wx-accent); background: rgba(0,200,83,0.06);
}
.wx-callout-warning { border-left-color: #f59e0b; background: rgba(245,158,11,0.08); }
.wx-callout-note { border-left-color: #3b82f6; background: rgba(59,130,246,0.08); }
.wx-callout-tip { border-left-color: var(--wx-accent); }
.wx-callout-success { border-left-color: #10b981; background: rgba(16,185,129,0.08); }
.wx-callout-title { font-weight: 700; margin-bottom: 0.25rem; }
.wx-quote {
  margin: 1rem 0; padding: 0.8rem 1rem; border-left: 3px solid var(--wx-border);
  color: var(--wx-muted); font-style: italic;
}
.wx-code {
  margin: 1rem 0; padding: 0.95rem 1.05rem; border-radius: 12px;
  background: #0f172a; color: #e2e8f0; overflow-x: auto; font-family: var(--wx-mono);
  font-size: 0.86rem; line-height: 1.5;
}
.wx-formula {
  margin: 0.9rem 0; padding: 0.8rem 1rem; text-align: center;
  font-family: var(--wx-mono); background: rgba(11,31,58,0.04); border-radius: 12px;
}
.wx-divider { border: none; border-top: 1px solid var(--wx-border); margin: 1.6rem 0; }
.wx-card {
  border: 1px solid var(--wx-border); border-radius: 14px; padding: 1.05rem;
  margin: 0.9rem 0; background: var(--wx-surface);
}
.wx-card-title { font-weight: 700; margin-bottom: 0.4rem; }
.wx-columns { display: grid; gap: 1rem; margin: 1rem 0; }
@media (min-width: 640px) {
  .wx-columns { grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
}
.wx-grid { display: grid; gap: 0.8rem; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.wx-artifact {
  display: flex; gap: 0.9rem; align-items: flex-start;
  border: 1px solid var(--wx-border); border-radius: 14px; padding: 1rem 1.1rem;
  margin: 1rem 0; background: rgba(0,200,83,0.04);
}
.wx-artifact-icon { font-size: 1.35rem; line-height: 1; }
.wx-artifact-label { font-weight: 650; }
.wx-artifact-desc { margin: 0.25rem 0 0.35rem; color: var(--wx-muted); font-size: 0.9rem; }
.wx-artifact-meta { font-size: 0.8rem; color: var(--wx-muted); margin-bottom: 0.35rem; }
.wx-btn {
  display: inline-block; margin-top: 0.2rem; padding: 0.5rem 1rem;
  background: var(--wx-navy); color: #fff !important; text-decoration: none;
  border-radius: 999px; font-size: 0.88rem; font-weight: 600;
  min-height: 44px; line-height: 1.6;
}
.wx-btn:hover { filter: brightness(1.08); }
.wx-btn:focus-visible { outline: 2px solid var(--wx-accent); outline-offset: 2px; }
.wx-img { max-width: 100%; height: auto; border-radius: 12px; display: block; }
.wx-figure { margin: 1rem 0; }
.wx-figure figcaption { font-size: 0.85rem; color: var(--wx-muted); margin-top: 0.4rem; }
.wx-link { color: #0d9488; text-decoration: underline; text-underline-offset: 2px; }
.wx-link:focus-visible { outline: 2px solid var(--wx-accent); outline-offset: 2px; }
.wx-expand {
  margin: 0.9rem 0; border: 1px solid var(--wx-border); border-radius: 12px;
  padding: 0.55rem 0.95rem;
}
.wx-expand summary { cursor: pointer; font-weight: 600; min-height: 44px; display: flex; align-items: center; }
.wx-expand-body { padding: 0.4rem 0 0.5rem; }
.wx-timeline {
  list-style: none; padding-left: 1.15rem; border-left: 2px solid var(--wx-border); margin: 1rem 0;
}
.wx-tl-item { margin: 0.55rem 0; position: relative; }
.wx-tl-item::before {
  content: ""; position: absolute; left: -1.4rem; top: 0.5rem;
  width: 9px; height: 9px; border-radius: 50%; background: var(--wx-accent);
}
.wx-footer {
  max-width: var(--wx-max); margin: 0 auto; padding: 0 1.25rem 2.2rem;
  color: var(--wx-muted); font-size: 0.82rem; text-align: center;
}
.wx-expiry { margin: 0 0 0.35rem; }
.wx-footer-brand { margin: 0; opacity: 0.85; }
.wx-meta { font-size: 0.88rem; margin: 0.75rem 0; }
.wx-meta th { text-align: left; padding-right: 1rem; color: var(--wx-muted); font-weight: 600; }
.wx-back { margin-top: 2rem; font-size: 0.9rem; }
.wx-back a { color: var(--wx-muted); }
.wx-status { color: var(--wx-muted); font-size: 0.92rem; }
@media print {
  body { background: #fff; color: #000; }
  .wx-header, .wx-footer, .wx-back, .wx-toc { display: none; }
  .wx-article { box-shadow: none; border: none; padding: 0; }
  .wx-btn { border: 1px solid #000; color: #000 !important; background: none; }
}
a:focus-visible, summary:focus-visible, .wx-table-wrap:focus-visible {
  outline: 2px solid var(--wx-accent); outline-offset: 2px;
}
"""
