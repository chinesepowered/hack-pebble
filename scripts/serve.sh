#!/usr/bin/env bash
# Start the Capy Care server detached from this shell (survives tool and terminal timeouts). Idempotent.
cd "$(dirname "$0")/.." || exit 1
mkdir -p .logs
if curl -s -m 5 http://127.0.0.1:8000/api/state >/dev/null; then
  echo "already running"
  exit 0
fi
pkill -9 -f "uvicorn capycare.app" 2>/dev/null
sleep 1
setsid nohup .venv/bin/uvicorn capycare.app:app --host 127.0.0.1 --port 8000 >> .logs/server.log 2>&1 < /dev/null &
echo "started server pid $!"
