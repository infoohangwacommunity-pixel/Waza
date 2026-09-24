# WAX Web Surface Architecture

## Principle

**Intelligence owns the experience. Infrastructure owns the environment.**

WhatsApp and Telegram are conversational interfaces.
Web Surfaces are temporary, richer interactive interfaces created by the **same** WAX intelligence.

The AI authors the browser experience (HTML/CSS/JS/assets).
WAX hosts, isolates, authorizes, lifecycles, and cleans it.
AI-generated code never receives secrets, DB credentials, or unrestricted backend access.

## Boundary diagram

```
                    LEARNER
                       |
          +------------+------------+
          |                         |
      WhatsApp                  Web Surface
          |                         |
          v                         v
     Channel Gateway          Surface Runtime
          |                         |
          +------------+------------+
                       |
                       v
                WAX INTELLIGENCE
                       |
       +---------------+----------------+
       |               |                |
     Memory           Work           Tools
                       |
                    Storage / Postgres
```

```
AI-generated browser code
          |
   isolated origin path
          |
   scoped capability token
          |
          v
   Surface Gateway (/s/{token}/api/*)
          |
          v
      WAX Core (principal-scoped)
```

## Domain

| Concept | Role |
|---------|------|
| **Surface** | Durable identity: owner, title, lifecycle, current revision |
| **SurfaceRevision** | Immutable bundle snapshot (entry HTML + asset manifest + checksum) |
| **SurfaceSession** | Browser session bound to surface + principal |
| **SurfaceEvent** | Generic interaction event (not educational taxonomy) |
| **Capability** | Least-privilege scopes: view, state_read, state_write, ai_request, artifact_read |

## What AI decides

Experience content, layout, colors, interactivity, chat UI presence, when to update vs create, lifecycle intent, what becomes Work/Artifact/memory.

## What infrastructure decides

Authorization, principal isolation, storage, serving, CSP/sandbox, capability grants, expiry/revoke/cleanup, rate limits, recovery.

## Explicit non-goals

No educational templates. No NodeType catalog as primary contract.
No second LLM. No browser→Postgres. No automatic memory from clicks.

## Legacy

`wax/publication/` remains as **legacy static publication** compatibility.
New work uses `wax/surfaces/`.

## KEEP / MODIFY / DELETE / ADD

| Component | Decision | Reason |
|-----------|----------|--------|
| Publication model | KEEP (legacy) | Static semantic docs still valid |
| Semantic Node schema | KEEP as legacy | Used by publish_web_surface |
| Trusted renderer | KEEP as legacy | Static publications |
| Planner | KEEP as legacy | Document structure only |
| `/p/{token}` | KEEP | Legacy publication URLs |
| `/pages/{id}` | KEEP (artifact HTML) | Artifact downloads |
| create_html_page | KEEP thin adapter | Compatibility |
| publish_web_surface | KEEP | Static semantic path |
| **Surface model** | **ADD** | Durable AI-authored environments |
| **SurfaceRevision** | **ADD** | Immutable bundles, same URL |
| **SurfaceSession** | **ADD** | Future session binding |
| **SurfaceEvent** | **ADD** | Generic interaction events |
| **create_surface / update / list / revoke** | **ADD** | AI tools |
| **`/s/{token}` + gateway API** | **ADD** | Runtime + controlled capabilities |
| **wax/surfaces/** | **ADD** | New domain package |

## Security boundary

- AI HTML/CSS/JS runs in the learner's browser only
- Capability token in URL; scopes granted server-side
- Gateway: state, events, AI request (queues Work — no LLM in web process)
- CSP: no remote scripts; connect-src 'self' only
- No DATABASE_URL, secrets, or provider keys in browser
- Cross-principal: token hash lookup + ownership on mutations

## Tools (AI)

- `create_surface` — new experience (HTML you author)
- `update_surface` — same URL, new revision / rename / state
- `list_surfaces` — discovery / continuation
- `revoke_surface` — access stops; Work/Artifacts remain

## Lifecycle

creating → active ⇄ idle ⇄ dormant → expired|revoked → cleaned

Worker: expire, idle/dormant, cleanup (revision files via delete_uri).

## Migration

Run `alembic upgrade head` (014_web_surfaces).
Existing publications continue to work on `/p/{token}`.
