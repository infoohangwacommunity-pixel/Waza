# Phase 2 — Production Safety (2026-09-21)

Completes the production-safety batch after Phase 1 audit.

## Done

### 1. Telegram callback crash
- `_handle_telegram_callback` implemented end-to-end
- Idempotent `InboundEvent`, single consume, continuation Work, `answerCallbackQuery`
- Commit: `1350289`

### 2. CI false-positive anti-hardcoding test
- AST identifier matching (not comment substrings)
- Commit: `1350289`

### 3. Terminal / OS media binaries
- `nixpacks.toml` installs: ffmpeg, tesseract, sox, mediainfo, bubblewrap, poppler, imagemagick, file
- `Aptfile` mirror for platforms that use apt
- `scripts/verify_capabilities.py` probes binaries at startup (non-fatal)
- Hooked in `scripts/startup.sh`

### 4. Workspace persistence
- `.env.example` documents Railway Volume at `/data` + `WORKSPACE_ROOT`
- Production refuses `/tmp` workspace when `REQUIRE_PERSISTENT_WORKSPACE=true`
- Loud error log when production still points at `/tmp`

### 5. Webhook signature consolidation
- Single implementation: `wax/security/webhooks.py`
- WhatsApp + Telegram handlers import shared helpers
- Local duplicate HMAC removed from WhatsApp handler

### 6. Typing indicators
- `senders.send_typing` + `deliver(..., show_typing=True)`
- Worker passes `inbound_message_id` when present
- Webhook accept path remains pure (no typing inside accept transaction)

### 7. Capability visibility
- Startup probe reports missing media binaries so tool failures are diagnosable
- Tutor must not claim tool success when binaries are absent (existing tool honesty path)

## Operator checklist (Railway)

1. Attach a Volume mounted at `/data`
2. Set `WORKSPACE_ROOT=/data/wax-workspaces` and `REQUIRE_PERSISTENT_WORKSPACE=true`
3. Prefer `STORAGE_BACKEND=s3` for artifacts across redeploys
4. Confirm `nixpacks.toml` is used (builder = NIXPACKS in railway.toml)
5. After deploy, check logs for `WAX CAPABILITY PROBE` — missing binaries mean OCR/media tools will fail closed

## Not in Phase 2 (Phase 3–4)

- Teaching policy / system prompt upgrade
- Audio transcription pipeline
- KnowledgeIngest tool wiring
- Controlled pip install in sandbox
- Conversation-level evaluation suite
