# Alembic revision chain fix (009)

## Bug

`009_channel_link_challenges.py` declared `revision = "009"` while
`010_worlds.py` had `down_revision = "009_channel_link_challenges"`.

Alembic could not build the graph (`KeyError: '009_channel_link_challenges'`),
so later migrations (including `surfaces`, `publications`, `principal_workloads`)
never applied.

## Fix

`009` revision id is now **`009_channel_link_challenges`**, matching `010`.

Full chain (head = `017_principal_workloads`):

`001` → … → `008` → `009_channel_link_challenges` → `010_worlds` → … → `017_principal_workloads`

## Railway recovery

1. Deploy current `main` (not the stale `3b99405` snapshot alone without this fix).
2. Run `alembic upgrade head` (startup / release command).
3. If `alembic_version` was manually stamped as the old id `009`:

```sql
UPDATE alembic_version SET version_num = '009_channel_link_challenges'
WHERE version_num = '009';
```

Then `alembic upgrade head` again.

Do **not** create tables by hand; let Alembic apply 009–017.
