"""Minimal branded HTML page as an artifact (ephemeral study sheet / summary page)."""

from __future__ import annotations

import html as html_lib
from typing import Any


def render_branded_html(*, title: str, body: str, brand: str = "WAX") -> str:
    safe_title = html_lib.escape(title or "Notes")
    # Allow basic line breaks only — escape the rest
    safe_body = html_lib.escape(body or "").replace("\n", "<br>\n")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{safe_title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; color: #111; }}
    header {{ border-bottom: 2px solid #2563eb; margin-bottom: 1.5rem; padding-bottom: .5rem; }}
    header span {{ color: #2563eb; font-weight: 700; letter-spacing: .04em; }}
    h1 {{ font-size: 1.4rem; margin: .25rem 0; }}
    footer {{ margin-top: 2rem; font-size: .85rem; color: #666; }}
  </style>
</head>
<body>
  <header><span>{html_lib.escape(brand)}</span><h1>{safe_title}</h1></header>
  <main>{safe_body}</main>
  <footer>Generated for learning — not medical/legal advice.</footer>
</body>
</html>
"""
