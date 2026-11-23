#!/bin/bash
# Run the whole suite in N parallel shards (largest files spread first). usage: shard.sh <tree> <python> <label> [N]
# Logs, a summary and the failures go to $RW_RUNS/<label>/ (default /tmp/rw-runs).
TREE=$1; PY=$2; LABEL=$3; N=${4:-3}
OUT=${RW_RUNS:-/tmp/rw-runs}/$LABEL; rm -rf "$OUT"; mkdir -p "$OUT"
cd "$TREE" || exit 1
for v in $(env | grep -o -E '^(SRFAX|DROPBOX|TRIZETTO|ANTHROPIC|SMTP|REMITWATCH|MODMED|CRON)_[A-Z_]*' | sort -u); do unset "$v"; done
ls tests/test_*.py tests/review*/test_*.py | xargs wc -l | grep -v total | sort -nr | awk -v n="$N" -v o="$OUT" '{print $2 > (o "/files_" (NR-1)%n)}'
for i in $(seq 0 $((N-1))); do
  ( timeout 5400 "$PY" -m pytest -q -p no:cacheprovider -o addopts="" $(cat "$OUT/files_$i") > "$OUT/shard_$i.log" 2>&1; echo "exit=$?" >> "$OUT/shard_$i.log" ) &
done
wait
for i in $(seq 0 $((N-1))); do tail -n 3 "$OUT/shard_$i.log" | grep -E 'passed|failed|error|exit=' ; done > "$OUT/summary.txt"
grep -h -E '^(FAILED|ERROR) ' "$OUT"/shard_*.log | sort > "$OUT/failures.txt"
echo DONE >> "$OUT/summary.txt"
