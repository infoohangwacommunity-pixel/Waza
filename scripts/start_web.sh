#!/usr/bin/env bash
# Web entrypoint — resolves PORT from the environment (Railway injects it).
# Never pass a literal "$PORT" string to uvicorn.
set -euo pipefail

PORT="${PORT:-8080}"
# Strip accidental literal "$PORT" if a broken startCommand leaked it
if [[ "$PORT" == '$PORT' || "$PORT" == '${PORT}' ]]; then
  PORT=8080
fi

exec bash scripts/startup.sh uvicorn wax.api.main:app --host 0.0.0.0 --port "${PORT}"
