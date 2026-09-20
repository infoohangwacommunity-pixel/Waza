#!/usr/bin/env bash
# WAX Prep container entrypoint: migrate → verify schema → start app.
# Fail-closed: any step failure exits non-zero and the process does not start.
set -euo pipefail

echo "=== WAX DATABASE MIGRATION START ==="

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "FATAL: DATABASE_URL is not set" >&2
  exit 1
fi

# Safe target metadata only (never print password / full URL)
python - <<'PY'
import os
from sqlalchemy.engine import make_url
raw = os.environ.get("DATABASE_URL", "")
try:
    u = make_url(raw)
    print(
        "migration_target "
        f"driver={u.drivername} host={u.host} port={u.port or 5432} "
        f"database={u.database} user_set={bool(u.username)}"
    )
except Exception as e:
    print(f"migration_target parse_error={e}")
PY

python -m alembic upgrade head

echo "=== WAX DATABASE MIGRATION COMPLETE ==="

echo "=== WAX DATABASE SCHEMA VERIFY START ==="
python scripts/verify_schema.py
echo "=== WAX DATABASE SCHEMA VERIFY COMPLETE ==="

echo "=== WAX APPLICATION START ==="
exec "$@"
