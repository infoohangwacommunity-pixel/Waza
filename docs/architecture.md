# WAX Prep Architecture

## Philosophy

Software provides durable mechanisms. Intelligence interprets and decides.
No hardcoded educational modes, subjects, exams, or curricula as application logic.

## Channels

WhatsApp and Telegram only for messaging. Channel adapters never own tutoring logic.

## Core loop

Inbound webhook → accept + idempotent persist → Work queued → Worker claims →
Context assembly → Tutor intelligence (+ tools) → Delivery → Memory extract

## Memory

Multi-level, confidence, provenance, expiry, consolidation, hybrid retrieval.
Failure is isolated from the learner-facing response.

## Tools (general)

schedule_followup, create_artifact, run_python, present_choices, inspect_memories, manage_goal

## Reliability

Durable work, delivery retries, orphan recovery, scheduled action wakeups,
provider fallback, webhook signature verification.

## Explicitly absent

- Cost/token budgets that refuse learners
- Subject/Exam/Quiz mode engines
- Fixed reminder clock rules as product logic
- Web channel (messaging focus: WhatsApp + Telegram)
