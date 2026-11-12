#!/bin/sh
# serve  -> dashboard on :8000. With REMITWATCH_SYNC_INTERVAL_MINUTES set it also pulls from Dropbox on that
#           interval, and at once when Dropbox calls the webhook. This is the normal way to run it.
# cycle  -> one hands-off run and exit: Dropbox sync, ingest, detect, alerts, notify (report when due)
# weekly -> the same with the summary report forced (kept for existing schedules)
# cron   -> same as serve (older deployments used this name)
set -e
case "$1" in
  serve|cron) exec python -m uvicorn remitwatch.api:app --host 0.0.0.0 --port 8000 --workers 1 ;;
  cycle)      exec python -m remitwatch.cli cycle ;;
  weekly)     exec python -m remitwatch.cli weekly ;;
  *) exec "$@" ;;
esac
