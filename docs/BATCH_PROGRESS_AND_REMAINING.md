# WAX infrastructure batch progress & remaining work

## How we count

The original “~500 items” mix **product principles**, **ops**, **tests**, and **capabilities**.
Many are duplicates or one-liners. We track **implementation batches**, not 500 separate PRs.

| Batch | Commit (approx) | Scope |
|-------|-----------------|--------|
| P0 | 9aa1e8e | Interactions, TG callbacks, expiry, clock |
| P1 | fa2b312 | AgentRuntime, learner state, schedule_at, pause/resume |
| P1-B | 22d30cd | WA interactions, storage defaults, tool meta, research fetch |
| **P1-C** | **this** | Assessment timeout, workspace env manifest, quiet hours, row locks, correlation |

## Rough remaining (high-value infrastructure only)

| Area | Status | Est. remaining “units” |
|------|--------|-------------------------|
| Interaction + deadlines | Mostly done | ~10 (chaos tests, WA list polish) |
| Agent runtime / checkpoints | Done baseline | ~15 (heartbeats, cancel) |
| Scheduler / time / TZ | Partial | ~20 (recurrence, cancel API) |
| Learner state / task stack | Partial | ~25 (full task stack table) |
| Assessment progression | Timeout done | ~15 (rubrics, adaptive metadata) |
| Artifacts / storage | TXT/PDF + S3 path | ~25 (DOCX, delivery retry polish) |
| Research | Fetch only | ~30 (provider search, citations store) |
| Terminal / workspace env | Manifest started | ~40 (capability network, quotas) |
| Domain tools / policy | Meta started | ~20 (policy engine) |
| Ephemeral websites | Not started | ~40 |
| Observability / chaos / eval | Partial | ~50 |
| Identity linking / privacy export | Not started | ~25 |
| Scale (Redis, multi-replica migrate lock) | Not needed yet | ~20 |

**Estimate:** ~**300–350** conceptual backlog items still open, of which **~150–200** are real eng work units.

At **~50–100 per batch**: about **3–5 more batches** to cover the *high-value* path in `INFRASTRUCTURE_REBUILD_SPEC.md`.  
The long tail (investor eval suites, full Dia web shell, browser capability) is **more batches after that**.

## Do not

- Expect “all 500” in one week  
- Hardcode 5s quiz / subject modes  

## Next batch (P2-A candidates)

1. Interaction chaos tests (double consume, expire vs answer)  
2. Scheduled action cancel + recurrence basics  
3. Artifact delivery retry without regenerating  
4. Search provider hook (optional API key)  
5. Work heartbeat / lease renew  
