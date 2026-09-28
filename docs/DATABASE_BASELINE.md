# Database baseline

## Active graph

```
001_reality  (down_revision = None)  ← sole head
```

Schema source of truth: `wax/db/models.py`.  
Baseline migration: `alembic/versions/001_reality_baseline.py`.

## Ownership

`principal_id → principals.id ON DELETE CASCADE` on student-owned tables.  
Goals are Memory rows (`memory_type`), not a separate goals table.

## Forbidden tables

These must not exist (retired architecture):

goals, memory_episodes, memory_links, tool_executions, activities,
assessments*, publications, learning_events, concepts, evidence, hypotheses

Enforced by `scripts/verify_schema.py`.

## Intentional wipe

```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```

Then redeploy (`alembic upgrade head`).
