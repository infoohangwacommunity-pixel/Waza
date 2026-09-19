# WAX Prep

**The tutor that actually knows you.**

Persistent adaptive tutoring on **WhatsApp** and **Telegram**.

## What this is

A person messages WAX. WAX learns who they are as a learner over time, remembers what matters, and helps them move forward.

No subject menus. No exam modes. No built-in curriculum library.  
Materials come from the learner (chat, photos, uploads) or are created together in conversation.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set DATABASE_URL, PRIMARY_API_KEY, WhatsApp and/or Telegram

# schema
python scripts/bootstrap_db.py
# or: alembic upgrade head

# terminal 1 — webhooks
uvicorn wax.api.main:app --host 0.0.0.0 --port 8000

# terminal 2 — tutor worker
python -m wax.workers.main
```

Point WhatsApp / Telegram webhooks at:

- `POST /webhooks/whatsapp`
- `POST /webhooks/telegram`

Health: `GET /health` · `GET /ready` · `GET /health/detail`

## Architecture (short)

Webhook accepts fast → durable Work → worker runs tutor (memory + tools) → delivery → memory extract.

See `docs/architecture.md` and `docs/BLUEPRINT_ALIGNMENT.md`.

## Philosophy

Deterministic software guarantees reality (DB, queue, delivery).  
Intelligence interprets and decides.  
Memory is the product continuity — not a chatbot history dump.
