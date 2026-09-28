# WAX Prep

**Open-world AI tutor for students** on WhatsApp and Telegram.

## Architecture

```
Student message
  → Infrastructure (identity, security, Work queue, World lifecycle)
  → AI agent (the brain) reasons about the objective
  → Acts inside the student's secure World and durable state
  → Infrastructure executes, persists, schedules, delivers
  → Observations return to the AI
  → AI continues until the objective is complete
  → Student receives the result
```

- **Infrastructure** provides reality: auth, isolation, persistence, messaging, safe execution, retries.
- **AI** is the agent: it decides what to do, how to teach, what to remember, when to schedule.
- **World** is the student's persistent workspace (files, packages, terminal).
- **No** tool registry, **no** primitive catalogue sent to the model, **no** Context Intelligence, **no** educational workflow engines.

When the AI needs to act, it writes free-form directive blocks (`world`, `memory`, `schedule`, `publish`, `choices`). Infrastructure runs them and returns observations. That is not a tool menu — the model is not choosing from application-defined function schemas.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python scripts/bootstrap_db.py
uvicorn wax.api.main:app --host 0.0.0.0 --port 8000
python -m wax.workers.main
```

Webhooks: `POST /webhooks/whatsapp`, `POST /webhooks/telegram`

**Production:** set a durable `WAX_WORKSPACE_ROOT` (not `/tmp`) so student Worlds survive deploys.

## Philosophy

Infrastructure provides reality. AI provides intelligence.
The World is the AI's hands. There is no tool registry between them.
