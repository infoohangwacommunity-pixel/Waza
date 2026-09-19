# Migration policy

## Rules
1. **All future schema changes** must use explicit Alembic DDL (`op.create_table`, `op.add_column`, indexes, constraints).
2. **005_ensure_schema** is a **compatibility bridge** only (`create_all(checkfirst=True)`). It creates missing *tables*; it does **not** evolve columns on existing tables.
3. Never rely on `create_all` to add a new column to a table that already exists in production.
4. 001/002 may use checkfirst for greenfield bootstraps; treat 003+ as the audit trail for incremental change.

## Adding a column
```python
# alembic/versions/00N_....py
def upgrade():
    op.add_column("memories", sa.Column("new_field", sa.String(40), nullable=True))
```
