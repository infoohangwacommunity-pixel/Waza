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

- **Same origin by default:** Surfaces use `PUBLIC_BASE_URL` (e.g. Railway public URL).
  No separate domain is required. Optional `SURFACE_PUBLIC_ORIGIN` only for advanced multi-host.
- Capability token in URL; scopes granted server-side; bridge uses `credentials: omit`
- Principal is always derived server-side from the Surface token (browser identity ignored)
- Gateway: state, events, AI request under `/s/{token}/api/*` (queues Work — no LLM in web process)
- CSP: default-src none; script-src unsafe-inline only; worker-src none; connect-src
  limited to self + configured public origin
- Service workers forcibly unregistered and `register` stubbed
- No DATABASE_URL, secrets, or provider keys in browser
- Main webhooks/admin paths are not Surface capabilities; opaque token does not grant them
- Browser-supplied context is untrusted (identity keys stripped)
- Cross-principal: token hash lookup + ownership on mutations
- Optimistic concurrency on `update_surface` via `expected_revision`

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

## Intelligence loop (completed)

```
Learner (surface) → POST /s/{token}/api/ai
                 → SurfaceAiRequest (opaque request_id)
                 → Work(kind=surface_ai) queued
                 → Worker → TutorService.handle_surface_request
                 → SAME tutor + memory + tools
                 → apply_ai_result (state + events; update_surface if AI chose)
                 → GET /s/{token}/api/ai/{request_id}
                 → GET /s/{token}/api/revision  (open tab can watch)
```

Browser never receives `work_id`.

## AI tools (canonical)

create_surface, update_surface, list_surfaces, inspect_surface, retain_surface, revoke_surface

Publication tools removed from the tutor tool list. `/p/{token}` remains only to serve already-issued legacy URLs.

## Activity vs expiry

Surfaces track last_opened, last_learner_interaction, last_ai_request/response, last_revision.
`retain_surface` sets retention_requested and extends expires_at within max policy.
Expiry disables access; cleanup removes revision bytes only — never Work, memory, or Artifacts.

## Security additions

- CSP default-src none; no external img/media; worker-src none; form-action none
- Bridge unregisters service workers
- Capability scopes enforced server-side
- Token remains surface-scoped, not a master credential


## Legacy publication compatibility

`wax/publication/` and `GET /p/{token}` remain as a **read-only compatibility layer**
for capability URLs already issued before Surfaces became canonical.

- AI tools `publish_web_surface`, `create_html_page`, and `revoke_publication` are
  **removed from the active tool catalog** — the intelligence cannot create new
  publications.
- No new code path queues publication Work.
- Operators may delete the package once all issued `/p/` tokens have expired.


## Production origin

In `APP_ENV=production`:

- `PUBLIC_BASE_URL` **must** be set (canonical origin for app + Surfaces)
- `SURFACE_PUBLIC_ORIGIN` is **optional** (leave empty for same-origin Railway deploy)
- Surface URLs: `{PUBLIC_BASE_URL}/s/{token}`

## Idempotency

- `create_surface`: unique `(principal_id, idempotency_key)`; token is deterministic from those parts so URL recovers on retry
- Surface AI: unique `(surface_id, idempotency_key)` (migration 016); request_id re-derived via HMAC
- IntegrityError on concurrent insert → return existing resource safely

## State concurrency

`state_revision` column + CAS on PUT/PATCH state. Conflict → `state_conflict`.
