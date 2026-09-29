# Database baseline

Source of truth: `wax/db/models.py`  
Migration: `alembic/versions/001_reality_baseline.py` (sole head)

## Tables

principals, interface_identities, conversations, messages, inbound_events,
worlds, memories, artifacts, scheduled_actions, works, executions, deliveries,
interactions, surfaces (and revision / session / event / ai_request),
principal_workloads

All student-owned rows use `principal_id → principals.id ON DELETE CASCADE`.

See [MIGRATIONS.md](MIGRATIONS.md) for upgrade and reset steps.
