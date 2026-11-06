#!/bin/sh
# serve  -> dashboard on :8000
# weekly -> one hands-off run: Dropbox sync, ingest, detect, report (schedule it weekly)
# cron   -> dashboard plus an in-container weekly schedule (Sunday 03:00 UTC) for hosts without a scheduler
set -e
case "$1" in
  serve)  exec python -m uvicorn remitwatch.api:app --host 0.0.0.0 --port 8000 ;;
  weekly) exec python -m remitwatch.cli weekly ;;
  cron)
    ( while true; do
        now=$(date -u +%u%H)          # weekday (1..7) + hour
        if [ "$now" = "703" ]; then python -m remitwatch.cli weekly || true; sleep 3600; fi
        sleep 600
      done ) &
    exec python -m uvicorn remitwatch.api:app --host 0.0.0.0 --port 8000 ;;
  *) exec "$@" ;;
esac
