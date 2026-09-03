#!/usr/bin/env bash
# App Service startup command. Runs migrations and reference-data seeding, then starts the web server.
set -euo pipefail
cd "$(dirname "$0")"
echo "[startup] applying database migrations"
python -m alembic upgrade head
echo "[startup] seeding reference data"
python -m app.seed
echo "[startup] starting gunicorn"
exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WEB_CONCURRENCY:-2}" \
  --bind "0.0.0.0:${PORT:-8000}" \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
