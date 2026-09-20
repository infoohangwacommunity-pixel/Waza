web: bash scripts/startup.sh uvicorn wax.api.main:app --host 0.0.0.0 --port ${PORT:-8080}
worker: bash scripts/startup.sh python -m wax.workers.main
