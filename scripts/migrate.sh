#!/usr/bin/env bash
# Run database migrations (must have resolvable DATABASE_URL host).
set -euo pipefail
echo "Running alembic upgrade head..."
alembic upgrade head
echo "Migrations complete."
