#!/usr/bin/env bash
# WAX Prep container entrypoint: migrate → verify schema → start app.
# Fail-closed: any step failure exits non-zero and the process does not start.
set -euo pipefail

# Railway/Nixpacks images often expose python3 but not python.
if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "FATAL: neither python3 nor python found on PATH" >&2
  exit 127
fi
export PYTHON
# Some tools still invoke "python" — provide a shim when missing
if ! command -v python >/dev/null 2>&1; then
  mkdir -p /tmp/wax-bin
  ln -sf "$(command -v python3)" /tmp/wax-bin/python
  export PATH="/tmp/wax-bin:${PATH}"
fi

# Seed STT models from image into volume if volume is empty
if [[ -x scripts/seed_models.sh ]]; then
  bash scripts/seed_models.sh || echo "seed_models: non-fatal failure"
fi

echo "=== WAX DATABASE MIGRATION START ==="

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "FATAL: DATABASE_URL is not set" >&2
  exit 1
fi

# Safe target metadata only (never print password / full URL)
$PYTHON - <<'PY'
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

$PYTHON -m alembic upgrade head

echo "=== WAX DATABASE MIGRATION COMPLETE ==="

echo "=== WAX DATABASE SCHEMA VERIFY START ==="
$PYTHON scripts/verify_schema.py
echo "=== WAX DATABASE SCHEMA VERIFY COMPLETE ==="

echo "=== WAX CAPABILITY PROBE ==="
$PYTHON scripts/verify_capabilities.py || true

echo "=== WAX APPLICATION START ==="
exec "$@"
