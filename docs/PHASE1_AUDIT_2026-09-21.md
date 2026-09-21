# Phase 1 Audit — WAX Prep (Waza)

**Date:** 2026-09-21  
**Commit audited:** `4c965df` (main)  
**Scope:** Full repository inspection before any Phase 2 implementation.  
**Rule:** No blind coding. Trace real execution paths. Preserve working architecture.

---

## 1. Repository snapshot

| Item | Value |
|------|--------|
| Repo | `infoohangwacommunity-pixel/Waza` |
| Branch | `main` @ `4c965df` |
| Stack | FastAPI, SQLAlchemy async, Postgres, WhatsApp + Telegram, Railway |
| Approx size | ~12k LOC in `wax/`, 28 test files, Alembic through `009` |

Architecture matches the product philosophy in spirit:

- Webhook accepts fast → durable Work → worker runs tutor → tools → delivery → memory
- No subject/exam/quiz mode engines in application logic
- Forbidden-pattern CI guard against `JAMBMode` / `QuizMode` / cost gates

---

## 2. Classification

### Implemented and working (preserve)

- Core tutor agent loop (`wax/intelligence/tutor.py`) — model decides; tools optional
- `TUTOR_SYSTEM` states no curriculum/modes; materials from learner or co-created
- Work / Execution / Delivery pipeline with idempotent inbound events
- Interaction model + `InteractionService` (create, find_by_callback, consume once, expire_due, enqueue_continuation)
- WhatsApp interactive button consume path (wired in handler)
- Per-principal workspace (`principals/<id>/{media,out,tmp}`)
- Sandbox allowlist + bubblewrap / docker / rlimits preference
- Environment manifest concept (`env_manifest.py`)
- Evidence / hypothesis / learning-state / concept-state infrastructure
- Assessment as general activity (not QuizMode)
- Scheduler, research tools, memory service, embeddings
- Forbidden-pattern CI guard (intent correct)
- Provider abstraction, session continuity, preferences domain
- Artifact storage (local/S3), mini pages, channel linking OTP

### Partially implemented / unwired

| Area | Status |
|------|--------|
| Telegram callbacks | Calls `_handle_telegram_callback` — **function body does not exist** |
| Typing indicators | `send_typing` / `send_typing_and_read` exist; never called from delivery |
| KnowledgeIngestService | Real chunk+embed service; never called from any tool or handler |
| OS media binaries | Whitelisted (`ffmpeg`, `tesseract`, `sox`, `mediainfo`); no Dockerfile / nixpacks / Aptfile |
| pip in sandbox | Not in allowlist; manifest has nothing to track |
| Workspace persistence | Defaults to `/tmp/wax-workspaces` — wiped on redeploy unless volume + `WAX_WORKSPACE_ROOT` |
| Audio transcription | Media download + inspect exist; no transcription pipeline |
| Preference durability | Domain exists; no strong regression that short/emoji prefs survive across turns |

### Broken

1. **Telegram button callbacks** — missing `_handle_telegram_callback` → every tap raises `NameError` (swallowed by broad handler)
2. **CI false positive** — `test_no_forbidden_patterns` string-matches `"QuizMode"` in comments/docstrings in:
   - `wax/work/activities.py`
   - `wax/db/models.py`
   - `wax/assessment/service.py`
3. **Production terminal media tools** — coded for OS binaries the Railway container almost certainly does not install

### Duplicated / dead

- `wax/security/webhooks.py` `verify_whatsapp_signature` — never imported; live HMAC is in `whatsapp/handler.py`
- Telegram callback tests only assert the *name* string exists in source, not runtime behavior

### Missing (high leverage for Phase 2–4)

- Real audio → transcript path
- Controlled package install into per-principal env
- Railway volume / durable workspace root in deploy config
- Typing indicators wired into send path
- Tool that calls `KnowledgeIngestService` for learner documents
- Stronger teaching decision policy in system prompt (retrieval-first, misconception repair, transfer, avoid “Question 1 / Correct!” rhythm)
- Conversation-level tutor evaluation suite

---

## 3. Execution path traces (verified)

### Inbound WhatsApp message
`handle_whatsapp_webhook` → signature check (local HMAC) → InboundEvent insert (idempotent) → identity → conversation → optional InteractionService.consume → Message + Work → 200  
Worker: TutorService.handle_message → context → model → tools → delivery

### Inbound Telegram message
`handle_telegram_webhook` → secret check → normalize → InboundEvent → identity → conversation → Message + Work  
**Callback branch:** `if payload.get("callback_query"): return await _handle_telegram_callback(payload)` — **function undefined**

### present_choices tool
Model → `handle_present_choices` → InteractionService.create → choices with opaque callback tokens → channel delivery of buttons

### Telegram button tap (intended)
callback_query → find Interaction by callback_data → consume once → enqueue_continuation Work → tutor continues  
**Actual:** NameError before any of that

### Knowledge ingest
`KnowledgeIngestService.ingest_text` exists and writes KnowledgeSource + DocumentChunk + embeddings  
**No tool or handler calls it**

---

## 4. Security / production notes

- Sandbox is allowlist + path bounds (not full multi-tenant container isolation on all hosts)
- Production fail-closed for weak `SECRET_KEY` already present
- No educational cost-gating in intelligence (correct and protected by test)
- Workspace isolation by principal id path is present; durability depends on volume mount

---

## 5. Implementation order (locked)

### Phase 2 — Production safety (next)
1. Implement real `_handle_telegram_callback` end-to-end (idempotent, single consume, continuation Work)
2. Fix forbidden-pattern test (match definitions / class names, not comment substrings)
3. Build/deploy notes for OS binaries + workspace volume
4. Consolidate webhook signature helpers (one authoritative path)

### Phase 3 — Teaching intelligence
System prompt upgrade, evidence-driven updates, durable preferences, varied teaching strategies

### Phase 4 — Capabilities
Audio transcription, ingest tool, typing, controlled pip, research honesty

### Phase 5–6
Reliability hardening + conversation-level evaluation suite

---

## 6. Definition of done reminder

A feature is done only when:

1. Implementation exists  
2. Real execution path reaches it  
3. Tests cover it  
4. Failure behavior handled  
5. Persistence correct  
6. Security boundaries respected  
7. Observability where needed  
8. Works through actual user channel  
9. Docs match reality  
10. No unnecessary duplicate architecture  

---

## 7. Explicit non-goals this cycle

- No subject-specific branches  
- No QuizMode / ExamMode / JAMBMode  
- No graph database  
- No cost gating in educational intelligence  
- No rebuild of working core loop  
- No deletion of useful-but-unwired infrastructure (connect first)

---

*Phase 1 complete. Phase 2 begins with Telegram callback + CI fix.*
