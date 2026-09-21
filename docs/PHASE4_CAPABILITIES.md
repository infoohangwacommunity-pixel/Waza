# Phase 4 — Capabilities (2026-09-21)

Connect existing infrastructure to real learner workflows.

## Delivered

### Audio transcription
- `wax/tools/transcription.py` — OpenAI-compatible `/audio/transcriptions`
- Tool: `transcribe_audio` (path-isolated to principal workspace)
- Worker auto-transcribes voice notes after media download
- Tutor prefers transcript over `[audio received]` placeholders
- Honest failures when PRIMARY_API_KEY missing or API fails

### Learner document ingest
- Tool: `ingest_document` wires `KnowledgeIngestService`
- Accepts text and/or workspace path (OCR/pdf via inspect_media)
- Principal isolation (path_escape)
- Context surfaces learner materials (sources + extracts) for this person only

### Media path
- Local media path + transcript in tool context
- System notes guide model to transcribe / inspect / ingest without claiming success falsely

### Already connected (prior phases)
- Interactive buttons (Telegram callback + WhatsApp)
- Timed `present_choices` expires_in_seconds
- Research tools
- Terminal / workspace
- Scheduled actions

## Operator notes
- Transcription requires PRIMARY_API_KEY with Whisper-compatible endpoint
- OCR/PDF still need OS binaries from nixpacks.toml after successful build
- Volume at `/data` for durable media

## Tests
- `tests/test_phase4_capabilities.py`
