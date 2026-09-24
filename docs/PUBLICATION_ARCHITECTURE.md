# WAX Ephemeral Web Publication Architecture

## Principle

WAX is an AI tutor. The temporary browser surface is a **capability**, not a content type.
The main tutor intelligence decides when a browser surface materially helps the learner.
Infrastructure never hardcodes educational page types (no TimetablePage, NotesPage, etc.).

## Flow

1. Tutor invokes `publish_web_surface` with a **semantic document** (title + blocks).
2. `PublicationService` validates, renders via trusted renderer (WAX logo + design system), stores immutable HTML in object storage, persists `Publication` row with opaque token hash.
3. Tool returns `page_url` = `{PUBLIC_BASE_URL}/p/{token}`.
4. Channel delivery shares the link; learner opens it.
5. Web service resolves token → serves snapshot (no LLM, no tutor).
6. Worker expires and cleans storage after lifecycle policy.

## Semantic model

See `wax/publication/schema.py`. Primitives include title, heading, paragraph, list, table, callout, code, artifact ref, media, section, columns, etc.
AI never sends HTML/CSS/JS.

## Security

- Opaque URL-safe tokens (256-bit); only keyed hash stored.
- Principal ownership on every publication.
- CSP, noindex, nosniff, no-referrer.
- No arbitrary JS. Static first.
- Artifact refs verified same-principal before embedding download links.

## Lifecycle

`active` → (`expired` | `revoked`) → `cleaned`

Defaults: 48h lifetime, max 7d, 24h cleanup grace after expiry.

## Legacy

`create_html_page` remains as a thin compatibility path but new tutoring should use `publish_web_surface`.

## Branding

Logo: `wax/static/brand/wax-prep-logo.png` (embedded as data-URI in renderer for self-contained pages).
