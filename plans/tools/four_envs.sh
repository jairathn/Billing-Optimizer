#!/bin/bash
# The four environments CLAUDE.md requires, one after another, then the two node checks.
# usage: four_envs.sh <tree> <tag>; set PY_NEWEST (venv with upgraded libraries), PY312 (3.12/SQLite 3.45), PY311 (3.11.1/SQLite 3.40.0)
HERE=$(cd "$(dirname "$0")" && pwd); TREE=$1; TAG=$2; OUT=${RW_RUNS:-/tmp/rw-runs}
for env in "py3:python3" "newest:${PY_NEWEST:?}" "py312:${PY312:?}" "py311:${PY311:?}"; do
  "$HERE/shard.sh" "$TREE" "${env#*:}" "${TAG}_${env%%:*}" 3
done
(cd "$TREE" && node tests/calc_model_check.js > "$OUT/${TAG}_node_calc.log" 2>&1; node tests/lab_review_check.js > "$OUT/${TAG}_node_lab.log" 2>&1)
for n in py3 newest py312 py311; do echo "== $n"; cat "$OUT/${TAG}_$n/summary.txt" "$OUT/${TAG}_$n/failures.txt"; done
for f in "$OUT/${TAG}_node_calc.log" "$OUT/${TAG}_node_lab.log"; do echo "== $f: $(grep -o '":\[false' "$f" | wc -l) false, $(grep -o '":\[true' "$f" | wc -l) true"; done
# On 3.11.1 only failures from the missing sqlite3.Connection.serialize are allowed: check with
#   grep -h -E "^E  " $OUT/${TAG}_py311/shard_*.log | grep -v "no attribute 'serialize'"
