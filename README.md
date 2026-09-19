# WAX Prep

**The tutor that actually knows you.**

Persistent adaptive tutoring over **WhatsApp** and **Telegram**.

## Philosophy

- No hardcoded subjects, exams, or quiz modes
- Intelligence decides; software provides durable mechanisms
- Memory evolves with the learner
- Terminal is the AI workspace (media on disk, local inspect first)
- No internal cost counters that refuse to help

## Quick start (developer)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — set DATABASE_URL and PRIMARY_API_KEY

# migrate
alembic upgrade head

# API (webhooks)
uvicorn wax.api.main:app --host 0.0.0.0 --port 8000

# worker (tutor + delivery + scheduler)
python -m wax.workers.main
```

## Architecture (short)

1. Webhook accepts message fast (idempotent)
2. Durable `Work` is queued
3. Worker claims work
4. Context assembler loads memories / goals / recent turns
5. Tutor model reasons (+ tools)
6. Delivery to WhatsApp/Telegram (chunked, optional buttons)
7. Memory extraction runs isolated after the reply

### Media path

Photo/audio/video → download to workspace → `inspect_media` (OCR/ffprobe/pdf) → optional `describe_image` only if needed.

## Channels

- WhatsApp Cloud API (typing, reply buttons, lists, media)
- Telegram Bot API (typing, keyboards, media)

## Safety

- Webhook signature verification
- Terminal allowlist + path bounds
- Secrets never passed into terminal env
- Delivery retries without losing completed tutoring work
