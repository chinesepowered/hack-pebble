#!/usr/bin/env bash
# Restart the Capy Care server if it stops answering. Runs until killed; start it detached:
#   setsid nohup scripts/watchdog.sh >/dev/null 2>&1 </dev/null &
cd "$(dirname "$0")/.." || exit 1
mkdir -p .logs
while true; do
  if ! curl -s -m 10 http://127.0.0.1:8000/api/state >/dev/null; then
    sleep 20  # a server that is starting up gets a grace period
    if ! curl -s -m 10 http://127.0.0.1:8000/api/state >/dev/null; then
      echo "$(date -u +%FT%TZ) server not answering; restarting" >> .logs/watchdog.log
      scripts/serve.sh >> .logs/watchdog.log 2>&1
      sleep 90
    fi
  fi
  sleep 30
done
