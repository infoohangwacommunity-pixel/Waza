"""
Surface runtime shell — hosts AI-authored HTML/CSS/JS safely.

AI may supply arbitrary browser experience code.
Infrastructure injects a capability bridge and enforces CSP at the HTTP layer.
Generated code never receives secrets or unrestricted backend access.
"""

from __future__ import annotations

import base64
import html as html_lib
import re
from pathlib import Path
from typing import Optional

_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "brand" / "wax-prep-logo.png"
_LOGO_CACHE: Optional[str] = None


def _logo_data_uri() -> str:
    global _LOGO_CACHE
    if _LOGO_CACHE is not None:
        return _LOGO_CACHE
    try:
        _LOGO_CACHE = (
            "data:image/png;base64,"
            + base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
        )
    except Exception:
        _LOGO_CACHE = ""
    return _LOGO_CACHE


def _esc(s: str) -> str:
    return html_lib.escape(s or "", quote=True)


def inject_runtime_bridge(token_placeholder: str = "{{SURFACE_TOKEN}}") -> str:
    """Minimal client bridge: talk only to same-origin /s/{token}/api/*."""
    return f"""
<script>
(function() {{
  const TOKEN = "{token_placeholder}";
  const API = "/s/" + TOKEN + "/api";
  window.WAX = window.WAX || {{}};
  window.WAX.surface = {{
    token: TOKEN,
    async getState() {{
      const r = await fetch(API + "/state", {{ credentials: "same-origin" }});
      if (!r.ok) throw new Error("state_read_failed");
      return r.json();
    }},
    async setState(state) {{
      const r = await fetch(API + "/state", {{
        method: "PUT",
        credentials: "same-origin",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(state || {{}}),
      }});
      if (!r.ok) throw new Error("state_write_failed");
      return r.json();
    }},
    async patchState(patch) {{
      const r = await fetch(API + "/state", {{
        method: "PATCH",
        credentials: "same-origin",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(patch || {{}}),
      }});
      if (!r.ok) throw new Error("state_patch_failed");
      return r.json();
    }},
    async event(type, payload) {{
      try {{
        await fetch(API + "/events", {{
          method: "POST",
          credentials: "same-origin",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ type: type || "interaction", payload: payload || {{}} }}),
        }});
      }} catch (e) {{ /* non-fatal */ }}
    }},
    async askAI(message, context) {{
      const r = await fetch(API + "/ai", {{
        method: "POST",
        credentials: "same-origin",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ message: String(message || ""), context: context || {{}} }}),
      }});
      if (!r.ok) {{
        const err = await r.json().catch(() => ({{ error: "ai_failed" }}));
        throw new Error(err.error || "ai_failed");
      }}
      return r.json();
    }},
  }};
  // Mark opened
  try {{ window.WAX.surface.event("opened", {{}}); }} catch (e) {{}}
}})();
</script>
"""


def wrap_ai_html(
    ai_html: str,
    *,
    surface_token_placeholder: str = "{{SURFACE_TOKEN}}",
    title: str = "WAX Surface",
    scopes: list[str] | None = None,
) -> str:
    """
    Package AI-authored content into a complete HTML document with runtime bridge.

    If AI already provided a full HTML document, inject bridge before </body>.
    Otherwise wrap fragment in a minimal shell.
    """
    content = (ai_html or "").strip()
    bridge = inject_runtime_bridge(surface_token_placeholder)
    logo = _logo_data_uri()

    lower = content[:500].lower()
    is_full = "<html" in lower or content.lower().startswith("<!doctype")

    if is_full:
        # Inject bridge before </body> or at end
        if re.search(r"</body>", content, re.I):
            return re.sub(r"</body>", bridge + "</body>", content, count=1, flags=re.I)
        return content + bridge

    # Fragment → shell
    logo_html = (
        f'<img src="{logo}" alt="WAX Prep" width="36" height="36" style="border-radius:8px"/>'
        if logo
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex, nofollow, noarchive"/>
<meta name="referrer" content="no-referrer"/>
<title>{_esc(title)} · WAX Prep</title>
<style>
  :root {{ --wx-navy:#0B1F3A; --wx-accent:#00c853; --wx-bg:#f4f7fb; --wx-ink:#0f172a; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: system-ui, -apple-system, Segoe UI, sans-serif;
         background: var(--wx-bg); color: var(--wx-ink); line-height:1.55; }}
  .wx-chrome {{ display:flex; align-items:center; gap:.6rem; padding:.7rem 1rem;
                background:#fff; border-bottom:1px solid #e2e8f0; position:sticky; top:0; z-index:5; }}
  .wx-chrome-title {{ font-weight:700; font-size:.85rem; letter-spacing:.04em; color:var(--wx-navy); }}
  .wx-chrome-tag {{ font-size:.72rem; color:#64748b; }}
  .wx-body {{ max-width: 920px; margin: 0 auto; padding: 1.25rem 1rem 2.5rem; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --wx-bg:#0a1220; --wx-ink:#e8eef7; }}
    .wx-chrome {{ background:#111b2e; border-color:#1e2d45; }}
    .wx-chrome-title {{ color:#e8eef7; }}
  }}
</style>
</head>
<body>
<header class="wx-chrome">
  {logo_html}
  <div>
    <div class="wx-chrome-title">WAX PREP</div>
    <div class="wx-chrome-tag">Temporary surface</div>
  </div>
</header>
<main class="wx-body">
{content}
</main>
{bridge}
</body>
</html>
"""


def render_unavailable(*, reason: str = "expired") -> str:
    logo = _logo_data_uri()
    logo_html = (
        f'<img src="{logo}" alt="WAX Prep" width="48" height="48" style="border-radius:10px"/>'
        if logo
        else ""
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
<style>
body {{ font-family: system-ui, sans-serif; background:#f4f7fb; color:#0f172a;
       display:flex; min-height:100vh; align-items:center; justify-content:center; margin:0; }}
.card {{ background:#fff; border-radius:16px; padding:2rem; max-width:420px; text-align:center;
        box-shadow:0 12px 40px rgba(11,31,58,.08); border:1px solid #e2e8f0; }}
h1 {{ font-size:1.3rem; margin:.8rem 0 .4rem; }}
p {{ color:#64748b; }}
</style>
</head>
<body>
<div class="card">
  {logo_html}
  <h1>Surface unavailable</h1>
  <p>{_esc(msg)}</p>
  <p>Return to WAX on WhatsApp or Telegram — WAX can prepare a fresh surface if you need one.</p>
</div>
</body>
</html>
"""
