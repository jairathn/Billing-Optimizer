#!/bin/bash
# Run a test command with the feed variables unset (common brief). Usage: t.sh <python> <pytest args...>
# Runs from the root of the checkout that holds this file.
for k in $(env | grep -oE '^(SRFAX_|DROPBOX_|TRIZETTO_|SMTP_|MODMED_|REMITWATCH_)[A-Za-z0-9_]*'); do unset "$k"; done
unset ANTHROPIC_BASE_URL
cd "$(dirname "$0")/../../.." || exit 1
py="$1"; shift
exec "$py" -m pytest "$@"
