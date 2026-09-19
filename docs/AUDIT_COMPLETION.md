# Production completion audit (Waza / WAX Prep)

## Preserved
Principal, channel identity, WA/TG adapters, Work/Execution/Delivery, Memory graph,
Activity, Observation, Concept/LearnerConceptState, embeddings, session continuity,
workspace, artifacts, scheduler, provider abstraction, no educational modes, no cost gates.

## Corrected (this pass)
1. **Webhook purity** — no media download, typing, AI, or delivery inside accept transaction
2. **HTTP semantics** — 200 only for accepted/duplicate; 5xx if not persisted; 403 invalid auth
3. **Memory decoupled** — `memory_process` Work after tutor reply; never blocks delivery latency
4. **Memory dedup** — reinforce similar active memories instead of spawning duplicates
5. **Media async** — worker prepares media before tutor; webhook only stores media_id
6. **Production fail-closed** — weak SECRET_KEY rejected when APP_ENV=production
7. **Version aligned** — API/package 0.2.0
8. **Misconception + KnowledgeSource + DocumentChunk** — learner material pipeline (no curriculum)
9. **Secret encryption helper** — at-rest encoding abstraction
10. **Durable artifact storage root** separate from workspace TTL
11. **CI workflow** — syntax, unit tests, forbidden pattern scan
12. **Scheduled delivery** — resolves channel identity and creates Delivery

## Explicitly not built
Product curriculum, QuizMode/JAMBMode, cost budgets, forced onboarding forms.

## Known limitations
- Terminal isolation is allowlist + path bounds (not full containers on all hosts)
- Assessment item-by-item UI is mechanism-level via Activity, not a full UI product
- Object storage is local path abstraction (configure WAX_ARTIFACT_ROOT / future S3)
- Fernet-grade crypto can replace XOR helper when production key management is ready

## Follow-up implementation (e25342e)

### Terminal sandbox
- Prefers `bwrap` (bubblewrap): network unshare, bind workspace only, die-with-parent
- Fallback: RLIMIT_CPU / AS / NOFILE / NPROC + scrubbed env
- Never inherits API keys / DB URLs into child process

### Secrets
- Primary: Fernet (`cryptography`) with key derived from `WAX_ENCRYPTION_KEY` or `SECRET_KEY`
- Legacy `wax1:` tokens still decrypt

### Artifact storage
- `WAX_STORAGE_BACKEND=local|s3`
- S3-compatible: `WAX_S3_BUCKET`, `WAX_S3_ENDPOINT`, keys via env

### Assessment infrastructure
- Tables: assessments, assessment_items, assessment_attempts, assessment_responses
- Tools: create_assessment, submit_assessment_answer
- Optional timer + durable Activity linkage
- One-at-a-time item flow for WhatsApp/Telegram
- Still **not** QuizMode / fixed curriculum

## Continued (fbca47d → next)

### Migrations
- 001/002: `create_all(checkfirst=True)` documented for greenfield
- 003/004: explicit DDL for evidence/hypotheses/assessments
- 005: ensure full schema with checkfirst for catch-up installs

### Terminal isolation
- Prefer docker (`WAX_TERMINAL_DOCKER=1` / `terminal_use_docker`) with `--network none`, `--read-only`, cap-drop, memory/pids limits
- Then bwrap; rlimits only if sandbox not required

### Research loop
- `ResearchLoopService`: propose_next_test, schedule_hypothesis_recheck, record_test_outcome
- Context may surface optional next check (non-forcing)
- Tools: propose_learning_check, schedule_hypothesis_recheck
