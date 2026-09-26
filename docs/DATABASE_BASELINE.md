# Waza database baseline

## Active migration graph

```
001_waza_baseline  (down_revision = None)  ← sole head
```

Historical files live in `alembic/versions_archive_pre_baseline/` and are **not** loaded by Alembic.

## Clean rebuild (intentional data wipe)

Keep the Railway Postgres **service** and **volume**. Only reset schema/data:

1. Stop Worker (and pause Web if needed).
2. Connect to the **correct** Railway Postgres (`DATABASE_URL`).
3. Run:

```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO public;
GRANT ALL ON SCHEMA public TO CURRENT_USER;
```

4. Deploy Web (runs `scripts/startup.sh` → `alembic upgrade head`).
5. Confirm:

```sql
SELECT version_num FROM alembic_version;
-- 001_waza_baseline

SELECT count(*) FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
```

6. Start Worker.

## Principals

Current model uses `display_name`, **not** `name`. Application code must not query `principals.name`.

## Future migrations

```text
alembic revision -m "some_change"
# down_revision = "001_waza_baseline"
alembic upgrade head
```
