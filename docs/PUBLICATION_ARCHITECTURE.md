# WAX Ephemeral Web Publication Architecture

## Philosophy

WAX is an AI tutor. The temporary browser surface is a **capability**, not a content type
and not a second chat application. The main tutor intelligence decides when a browser
surface materially helps. Infrastructure never hardcodes educational page types.

```
Learner → Channel → Tutor Intelligence
                      ↓
              Capability decision
                      ↓
         publish_web_surface (semantic doc)
                      ↓
    validate → plan → render → store snapshot
                      ↓
         opaque capability URL (/p/{token})
                      ↓
              Browser (immutable)
                      ↓
         lifecycle: active → expired/revoked → cleaned
```

## Layers

| Layer | Responsibility |
|-------|----------------|
| **Semantic model** (`schema.py`) | Compositional nodes: content, structure, assets, soft present-hints |
| **Planner** (`planner.py`) | Structure analysis → TOC, counts, asset list (no educational rules) |
| **Policy** (`policy.py`) | Lifetime bounds, state machine transitions |
| **Renderer** (`renderer.py`) | Registry of trusted node renderers → branded HTML |
| **Service** (`service.py`) | Create (preparing→active), access, revoke, expire, cleanup, recovery |
| **Tokens** (`tokens.py`) | Opaque capability issuance + constant-time verify |
| **HTTP** (`/p/{token}`) | Serve snapshot or branded unavailable state |

## Semantic model (v1.1)

Document → nodes (tree). Containers: section, card, columns, grid, expandable.
Leaves: paragraph, heading, list, table, callout, code, formula, artifact, media, …
AI never sends HTML/CSS/JS. Unknown fields are preserved but ignored by the renderer.

## State machine

`preparing → active → expired|revoked → cleaned`  
Also: `preparing|active → failed → cleaned`

## Security

- Opaque tokens (256-bit); only HMAC hash stored
- Principal ownership on Publication + artifact refs verified same-principal
- Strict CSP (no scripts by default), noindex, nosniff, no-referrer
- XSS: all text escaped in trusted renderer
- URL allowlist: http(s)/mailto only
- Never log raw tokens or private content

## Artifact integration

Publications **reference** Artifacts; they do not copy large files.
Expiry of a publication does not delete shared Artifacts.
Download links are principal-bound signed URLs injected at render time.

## Idempotency & recovery

- Optional `idempotency_key` (tool_call_id / work_id) prevents duplicate active pubs
- Create writes `preparing` row before storage; storage failure → `failed`
- Worker recovers stuck `preparing` and cleans expired snapshots via `delete_uri`

## Caching

Active publications: `private, no-cache, must-revalidate` so revocation/expiry is visible.
Expired responses: `no-store`.

## Legacy

`create_html_page` is a thin adapter to `publish_web_surface`. Prefer the latter.

## Extension boundary

Static trusted publication (this system) vs future **controlled interactive publication**
(separate privilege, sandbox, CSP). Do not inject arbitrary JS into static publications.

## Tests

See `tests/test_publication.py` — tokens, policy, composition, XSS, planner TOC,
tables, forward-compat, content-agnostic materials.
