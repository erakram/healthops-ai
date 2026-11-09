#!/usr/bin/env bash
# Starts the API and the web app in one container; if either stops, the container stops
# (so the host restarts it instead of serving a half-working app).
set -euo pipefail

# Without a configured secret, make one per start: sessions then last until the next restart.
if [ -z "${JWT_SECRET:-}" ]; then
  JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  export JWT_SECRET
fi

APP_DIR="${APP_DIR:-/app}"
API_PORT="${API_PORT:-8010}"

cd "$APP_DIR/backend"
python -m uvicorn app.main:app --host 127.0.0.1 --port "$API_PORT" &
api=$!

cd "$APP_DIR/web"
HOSTNAME=0.0.0.0 PORT="${PORT:-3000}" node server.js &
web=$!

stop() { kill "$api" "$web" 2>/dev/null || true; }
trap 'stop; exit 0' TERM INT
# Check every few seconds that both are alive (portable: no `wait -n`).
while kill -0 "$api" 2>/dev/null && kill -0 "$web" 2>/dev/null; do
  sleep 3
done
echo "start.sh: the $(kill -0 "$api" 2>/dev/null && echo web app || echo API) stopped; shutting down" >&2
stop
exit 1
