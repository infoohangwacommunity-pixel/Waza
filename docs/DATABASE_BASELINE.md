# WAX database — single baseline

## Active migration graph

```
001_reality  (down_revision = None)  ← sole head
```

Schema is created from `wax/db/models.py` via `Base.metadata.create_all`.
No historical educational / graph / tool-execution archaeology.

## From zero

```bash
export DATABASE_URL=postgresql+asyncpg://...
python scripts/repair_alembic_state.py
alembic upgrade head
python scripts/verify_schema.py
```

## Existing Railway DB

Deploy runs `repair_alembic_state.py` which stamps `001_reality` when
alembic_version still points at deleted revisions (`001_waza_baseline` … `007`).
Then `upgrade head` is a no-op.

Intentional wipe: DROP SCHEMA public CASCADE; CREATE SCHEMA public; redeploy.

## Ownership

principal_id → principals.id ON DELETE CASCADE on student-owned tables.
Goals are Memory rows, not a goals table.

## Forbidden tables

goals, memory_episodes, memory_links, tool_executions, activities,
assessments*, publications, learning_events, concepts, evidence, hypotheses
