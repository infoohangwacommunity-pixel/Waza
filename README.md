# WAX PREP

**The tutor that actually knows you.**

WAX Prep is a production-grade AI tutor and personal learning companion designed for messaging platforms (WhatsApp, Telegram).

## Core Philosophy

- No hardcoded educational modes (no JAMB mode, Physics mode, Beginner mode, Quiz mode)
- General intelligent primitives + AI interpretation
- Advanced multi-level memory that grows with the learner
- Durable asynchronous architecture (webhooks acknowledge fast, work is durable)
- Open — no internal cost budgets that reject learners

## Architecture

Webhook → accept + idempotent persist → Work (durable) → Worker → Intelligence + Memory → Delivery

## Quick Start

```bash
cp .env.example .env
pip install -e ".[dev]"
alembic upgrade head
uvicorn wax.api.main:app --host 0.0.0.0 --port 8000
python -m wax.workers.main
```

## Design Rules

1. No hardcoded educational taxonomies
2. Memory is evidence-aware and evolvable
3. Webhooks never wait for AI
4. Important work is durable
5. No internal LLM cost budget deciding whether a learner receives help
6. Secrets never leak to models or logs
7. The system grows with the learner
