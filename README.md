# WAX Prep

Open-world AI tutor for students on WhatsApp and Telegram.

## How it works

Infrastructure provides reality. AI provides intelligence.

A student message becomes durable Work. The AI receives the objective and recent conversation, decides what to do, and may act through the student’s World (files, packages, terminal), durable state, schedules, temporary web surfaces, or channel interactions. Infrastructure executes those requests safely, returns observations, and delivers the final reply.

```
Messaging → identity → Work → AI → World / state / schedule / surface
         → observation → AI → delivery
```

| Piece | Role |
|-------|------|
| **Infrastructure** | Identity, security, isolation, persistence, Work, retries, delivery |
| **AI** | Interpretation, teaching, memory decisions, planning |
| **World** | Persistent per-student workspace |
| **Memory** | Durable AI-owned state (search, create, update, supersede, forget) |
| **Work** | Durable execution unit for each turn |
| **Scheduler** | Stores time; wakes Work when due |
| **Messaging** | WhatsApp / Telegram in and out |

When the AI needs infrastructure to act, it writes a short fenced block (`world`, `state`, `time`, `publish`, `interact`). That is a thin bridge to reality — not an application menu of tools.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set DATABASE_URL, PRIMARY_API_KEY, …

python scripts/bootstrap_db.py
uvicorn wax.api.main:app --host 0.0.0.0 --port 8000
# other terminal:
python -m wax.workers.main
```

Webhooks: `POST /webhooks/whatsapp`, `POST /webhooks/telegram`.

Production: use the Dockerfile, a durable `WORKSPACE_ROOT` (not `/tmp`), and settings from `.env.example`.

See [docs/architecture.md](docs/architecture.md) and [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).
