#!/bin/bash
# Auto-deploy: this server follows the repository's main branch by itself, as the Vercel site did.
#
# remitwatch-deploy.timer runs this every 5 minutes, as root, from /opt/remitwatch (deploy/install-auto-deploy.sh sets
# it up). Each run fetches the branch and, when its newest commit is not the one this server runs, deploys it:
#   - only docs changed (docs/ and *.md): the files are checked out; nothing is rebuilt or restarted;
#   - otherwise build first, swap second: the files are checked out and `docker compose build` runs. Only when the build
#     succeeds does `docker compose up -d` replace the running containers, after waiting (up to 20 minutes) for a data
#     cycle that is running to finish. Caddy is restarted when its file changed. The site must answer within 3 minutes,
#     or the previous commit is built and started again and the new one is held (not tried again by itself).
#   - a failed build puts the files back as they were and leaves the running site alone. It is logged and the next tick
#     tries again; after three failures of the same commit, once an hour.
#   - files changed by hand in /opt/remitwatch stop it: nothing is overwritten; the log says so.
# Every attempt is one line in /var/log/remitwatch-deploy.log: time, from-commit, to-commit, result, detail. The build's
# own output is kept in /var/lib/remitwatch-deploy/builds/ (the last 30), the commit running in
# /var/lib/remitwatch-deploy/deployed.
#
#   auto-deploy.sh            one check (what the timer runs)
#   auto-deploy.sh --force    deploy the branch's newest commit now, even when it is the one running or was held
#   auto-deploy.sh --status   the commit running, the branch's newest, the timer, the last deploys
#
# The whole script is read before it runs (main below), so a deploy that changes this file cannot disturb the run.
set -uo pipefail

DIR=${REMITWATCH_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
STATE=${REMITWATCH_DEPLOY_STATE:-/var/lib/remitwatch-deploy}
LOG=${REMITWATCH_DEPLOY_LOG:-/var/log/remitwatch-deploy.log}
DOCKER=${REMITWATCH_DOCKER:-docker}
IDLE_WAIT=${REMITWATCH_DEPLOY_IDLE_WAIT:-1200}      # seconds to wait for a running data cycle before the swap
HEALTH_WAIT=${REMITWATCH_DEPLOY_HEALTH_WAIT:-180}   # seconds the new site has to answer
POLL=${REMITWATCH_DEPLOY_POLL:-10}
RETRY_AFTER=${REMITWATCH_DEPLOY_RETRY_AFTER:-3600}  # after three failed builds of one commit, try it this often

git() { command git -c safe.directory="$DIR" -C "$DIR" "$@"; }
compose() { (cd "$DIR" && "$DOCKER" compose "$@"); }
in_app() { compose exec -T app python -c "$@" 2>/dev/null; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

record() {      # result detail: one line in the log, and in the journal
  printf '%s\tfrom=%s\tto=%s\t%s\t%s\n' "$(now)" "${FROM:0:12}" "${TO:0:12}" "$1" "$2" >> "$LOG"
  echo "deploy ${FROM:0:12} -> ${TO:0:12}: $1. $2"
}

failed() {      # result detail [hold]: logged, and counted against this commit for the retry rule
  local sha="" count=0 when=0 hold=""
  [ -f "$STATE/failed" ] && read -r sha count when hold < "$STATE/failed"
  if [ "$sha" = "$TO" ]; then count=$((count + 1)); else count=1; fi
  echo "$TO $count $(date +%s) ${3:-}" > "$STATE/failed"
  record "$1" "$2"
}

holding() {     # whether this commit is to be left alone for now (a rollback, or three failed builds within the hour)
  local sha="" count=0 when=0 hold=""
  [ -f "$STATE/failed" ] || return 1
  read -r sha count when hold < "$STATE/failed"
  [ "$sha" = "$TO" ] || return 1
  if [ "$hold" = hold ]; then
    echo "${TO:0:12} was rolled back; it is not deployed again by itself (push a fix, or run auto-deploy.sh --force)."
    return 0
  fi
  if [ "${count:-0}" -ge 3 ] && [ $(( $(date +%s) - ${when:-0} )) -lt "$RETRY_AFTER" ]; then
    echo "${TO:0:12} failed to build ${count} times; it is tried again an hour after the last try."
    return 0
  fi
  return 1
}

checkout() { git checkout -q -f -B "$BRANCH" "$1"; }

cycle_token() {  # the lock of a data cycle running in the app, or nothing (remitwatch.pipeline keeps it in the store)
  in_app 'import json, os, sqlite3
try:
    c = sqlite3.connect(os.environ.get("REMITWATCH_DB", "/data/remitwatch.sqlite"), timeout=5)
    r = c.execute("SELECT value FROM meta WHERE key=?", ("pipeline:lock",)).fetchone()
    print(json.loads(r[0]).get("token", "") if r else "")
except Exception:
    print("")'
}

clear_lock() {   # the lock of a cycle the swap ended (its process went with the old container), so the next cycle can run
  in_app 'import os, sqlite3, sys
c = sqlite3.connect(os.environ.get("REMITWATCH_DB", "/data/remitwatch.sqlite"), timeout=30)
c.execute("DELETE FROM meta WHERE key=? AND value LIKE ?", ("pipeline:lock", "%\"token\": \"" + sys.argv[1] + "\"%"))
c.commit()' "$1"
}

wait_idle() {    # prints the lock of a cycle still running after IDLE_WAIT, or nothing
  local waited=0 tok
  tok=$(cycle_token)
  while [ -n "$tok" ] && [ "$waited" -lt "$IDLE_WAIT" ]; do
    sleep "$POLL"
    waited=$((waited + POLL))
    tok=$(cycle_token)
  done
  echo "$tok"
}

healthy() {
  local waited=0
  until in_app 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/robots.txt", timeout=5).read()'; do
    [ "$waited" -ge "$HEALTH_WAIT" ] && return 1
    sleep "$POLL"
    waited=$((waited + POLL))
  done
}

rollback() {     # result buildlog: the previous commit built and started again; the new one held
  if [ "$FROM" != none ] && checkout "$FROM" && compose build >>"$2" 2>&1 && compose up -d >>"$2" 2>&1; then
    failed "$1" "rolled back to ${FROM:0:12}, which runs again; the new commit is held. Output: $2" hold
  else
    failed "$1" "the rollback did not finish: the site may be down; run docker compose ps. Output: $2" hold
  fi
}

prune_builds() {
  ls -1t "$STATE/builds" 2>/dev/null | tail -n +31 | while IFS= read -r f; do rm -f "$STATE/builds/$f"; done
}

status() {
  BRANCH=$(cat "$STATE/branch" 2>/dev/null || true); BRANCH=${BRANCH:-main}
  local run newest
  run=$(cat "$STATE/deployed" 2>/dev/null || true)
  newest=$(git rev-parse --verify --quiet "origin/$BRANCH" || true)
  echo "Running:  ${run:-unknown} $( [ -n "$run" ] && git log -1 --format='(%cs) %s' "$run" 2>/dev/null)"
  echo "Newest:   ${newest:-unknown} on origin/$BRANCH as of the last fetch $( [ -n "$newest" ] && git log -1 --format='(%cs) %s' "$newest" 2>/dev/null)"
  if systemctl is-enabled --quiet remitwatch-deploy.timer 2>/dev/null; then
    echo "Timer:    on; next check $(systemctl show -p NextElapseUSecRealtime --value remitwatch-deploy.timer 2>/dev/null)"
  else
    echo "Timer:    off (sudo $DIR/deploy/install-auto-deploy.sh switches it on)"
  fi
  [ -f "$STATE/failed" ] && echo "Failing:  $(cat "$STATE/failed")  (commit, failures, last try, hold)"
  echo "Last deploys ($LOG):"
  tail -n 10 "$LOG" 2>/dev/null || echo "  none yet"
}

main() {
  local mode=${1:-check} changed note="" t0=$SECONDS blog before after tok
  case "$mode" in
    --status) status; return 0 ;;
    check|--force) ;;
    *) echo "usage: $0 [--force | --status]"; return 2 ;;
  esac
  mkdir -p "$STATE/builds" && chmod 700 "$STATE"
  exec 9>"$STATE/lock"
  flock -n 9
  case $? in
    0) ;;
    1) echo "A deploy, or configure.sh, is running: this check is skipped."; return 0 ;;
    *) echo "flock is missing (dnf -y install util-linux): nothing can be deployed safely until it is there."; return 1 ;;
  esac
  BRANCH=$(cat "$STATE/branch" 2>/dev/null || true); BRANCH=${BRANCH:-main}
  if ! git fetch --quiet origin "$BRANCH" 2>"$STATE/fetch.err"; then
    echo "git fetch failed (the next check tries again): $(tail -n 3 "$STATE/fetch.err")"
    return 1
  fi
  TO=$(git rev-parse --verify --quiet "origin/$BRANCH^{commit}") || { echo "origin/$BRANCH is not there"; return 1; }
  FROM=$(cat "$STATE/deployed" 2>/dev/null || true)
  if [ "$mode" != --force ]; then
    [ "$TO" = "$FROM" ] && return 0
    holding && return 0
  fi
  FROM=${FROM:-none}
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    failed refused "files in $DIR were changed by hand (git status lists them); nothing was deployed or overwritten. Put them back (git checkout -- .) or commit the change to main"
    return 1
  fi
  if [ "$FROM" != none ] && git cat-file -e "$FROM^{commit}" 2>/dev/null; then
    changed=$(git diff --name-only "$FROM" "$TO")
  else
    changed="(everything)"
  fi
  if [ "$mode" != --force ] && { [ -z "$changed" ] || ! grep -qvE '^docs/|\.md$' <<<"$changed"; }; then
    checkout "$TO" || { failed checkout-failed "git could not check out ${TO:0:12}"; return 1; }
    echo "$TO" > "$STATE/deployed"
    rm -f "$STATE/failed"
    record docs-only "$( [ -z "$changed" ] && echo "no file changed" || echo "$(wc -l <<<"$changed") docs file(s) changed"); checked out, nothing rebuilt or restarted"
    return 0
  fi
  blog="$STATE/builds/$(date -u +%Y%m%dT%H%M%SZ)-${TO:0:12}.log"
  if ! checkout "$TO"; then
    failed checkout-failed "git could not check out ${TO:0:12}"
    return 1
  fi
  if ! compose build >"$blog" 2>&1; then
    [ "$FROM" != none ] && checkout "$FROM"
    failed build-failed "the running site was left as it was and the files put back; the next check tries again. Output: $blog"
    prune_builds
    return 1
  fi
  before=$(compose ps -q app 2>/dev/null)
  tok=$(wait_idle)
  if ! compose up -d >>"$blog" 2>&1; then
    rollback up-failed "$blog"
    return 1
  fi
  after=$(compose ps -q app 2>/dev/null)
  if [ -n "$tok" ] && [ "$after" != "$before" ]; then
    clear_lock "$tok"
    note="; a data cycle still running after ${IDLE_WAIT}s was ended by the swap (the next one starts by itself)"
  fi
  if [ "$changed" = "(everything)" ] || grep -qx 'deploy/Caddyfile' <<<"$changed"; then
    compose restart caddy >>"$blog" 2>&1 || note="$note; Caddy did not restart (docker compose restart caddy)"
  fi
  if ! healthy; then
    rollback unhealthy "$blog"
    return 1
  fi
  echo "$TO" > "$STATE/deployed"
  rm -f "$STATE/failed"
  "$DOCKER" image prune -f >/dev/null 2>&1 || true
  if grep -q '^deploy/systemd/' <<<"$changed"; then
    bash "$DIR/deploy/install-auto-deploy.sh" --units-only >>"$blog" 2>&1 || note="$note; the timer's own files did not reinstall"
  fi
  record deployed "$((SECONDS - t0))s; $( [ "$changed" = "(everything)" ] && echo "first deploy by this checker" || echo "$(wc -l <<<"$changed") file(s) changed")$note. Output: $blog"
  prune_builds
  return 0
}

main "$@"; exit $?
