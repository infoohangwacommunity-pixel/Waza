# Migrations

## Policy

One baseline:

```
001_reality  (down_revision = None)
```

File: `alembic/versions/001_reality_baseline.py`  
Creates the full current schema from `wax/db/models.py`.

## Fresh database

```bash
export DATABASE_URL=postgresql+asyncpg://…
alembic upgrade head
python scripts/verify_schema.py
```

## Existing database at head

`alembic upgrade head` is a no-op. Startup still runs it, then verifies schema.

## Future changes

Add a new numbered revision with explicit DDL (`op.add_column`, indexes, …).  
Do not rely on `create_all` to alter tables that already exist.

## Production startup

```
alembic upgrade head
python scripts/verify_schema.py
exec <app>
```

## Ownership

Student-owned tables cascade from `principals.id`.  
Schema truth is always `wax/db/models.py`.

## Intentional reset

```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```

Then `alembic upgrade head`.
