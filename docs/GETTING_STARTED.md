# Getting started

## Requirements

- Python 3.12+
- PostgreSQL
- An OpenAI-compatible API key (or another provider via `PRIMARY_*` settings)

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

- `DATABASE_URL` — async Postgres URL (`postgresql+asyncpg://…`)
- `PRIMARY_API_KEY` / `PRIMARY_MODEL` — intelligence path
- `PUBLIC_BASE_URL` — public origin of this service (webhooks + surfaces)
- Channel tokens as needed (`WHATSAPP_*`, `TELEGRAM_*`)

## Database

```bash
python scripts/bootstrap_db.py
# or: alembic upgrade head && python scripts/verify_schema.py
```

## Run

```bash
uvicorn wax.api.main:app --host 0.0.0.0 --port 8000
python -m wax.workers.main
```

Point WhatsApp or Telegram webhooks at:

- `{PUBLIC_BASE_URL}/webhooks/whatsapp`
- `{PUBLIC_BASE_URL}/webhooks/telegram`

Send a message. You should get a natural reply from the tutor.

## Production

Operator contract for Railway variables, volume, and start commands: [RAILWAY.md](RAILWAY.md).

- Build with the repository `Dockerfile` (Railway: `builder = DOCKERFILE`).
- Mount a durable volume for `WORKSPACE_ROOT` (for example `/data/wax-workspaces`).
- Set `APP_ENV=production` and a strong `SECRET_KEY`.
- Full variable list: `.env.example`.
