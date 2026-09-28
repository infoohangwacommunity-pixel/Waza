# WAX Prep

**Open-world AI tutor for students** on WhatsApp and Telegram.

## Architecture

```
Student → Channel → Infrastructure (identity, security, Work, World lifecycle)
                 → AI / Tutor (the brain)
                 → Student World (persistent files, packages, terminal)
                 → primitives (memory, schedule, publish, interaction)
                 → Delivery
```

- **Infrastructure** enforces reality: auth, isolation, persistence, messaging, retries.
- **AI** decides: what to remember, retrieve, process, create, teach, schedule.
- **World** is the student's isolated persistent execution environment.
- **No** Context Intelligence gate, **no** automatic context assembler, **no** specialized educational workflow tools.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # DATABASE_URL, PRIMARY_*, WhatsApp/Telegram, WORKSPACE_ROOT

python scripts/bootstrap_db.py
# terminal 1
uvicorn wax.api.main:app --host 0.0.0.0 --port 8000
# terminal 2
python -m wax.workers.main
```

Webhooks: `POST /webhooks/whatsapp`, `POST /webhooks/telegram`

**Production:** set a durable `WAX_WORKSPACE_ROOT` (not `/tmp`) so student Worlds persist across deploys.

## Primitives available to the AI

- Memory: search, get, create, update, supersede, forget
- World: discover, exec, acquire, files
- Time: now, schedule, cancel, list_scheduled
- Interaction: present_choices
- Publish: publish / update_surface / revoke_surface
- Preferences: set_preference

## Philosophy

Infrastructure provides reality. AI provides intelligence.
