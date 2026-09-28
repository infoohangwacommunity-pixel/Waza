# Migration policy

## Graph

```
001_reality  (down_revision = None)  ← sole head
```

One baseline file: `alembic/versions/001_reality_baseline.py`.  
It creates the full current schema from `wax/db/models.py` via `Base.metadata.create_all`.

## Fresh database

```bash
export DATABASE_URL=postgresql+asyncpg://...
alembic upgrade head
python scripts/verify_schema.py
```

`alembic upgrade head` creates every table. Nothing else is required.

## Existing database already at head

`alembic upgrade head` is a no-op. Startup still runs it, then `verify_schema.py`.

## Future schema changes

After this baseline, every change is an explicit Alembic revision:

```python
# alembic/versions/002_add_example.py
def upgrade():
    op.add_column("memories", sa.Column("example", sa.String(40), nullable=True))

def downgrade():
    op.drop_column("memories", "example")
```

Rules:

1. Use explicit DDL (`op.create_table`, `op.add_column`, indexes, constraints).
2. Do not rely on `create_all` to add columns to tables that already exist.
3. Do not put historical revision recovery into application startup.

## Production startup

```
alembic upgrade head
python scripts/verify_schema.py
exec <app>
```

Startup does not inspect or rewrite old revision IDs.

## Dev bootstrap (optional)

`python scripts/bootstrap_db.py` calls `create_all` for local experimentation.  
Production always uses Alembic.
