#!/bin/bash
# Auto-deploy: this server follows the repository's main branch by itself, as the Vercel site did.
#
# remitwatch-deploy.timer runs this every 5 minutes, as root. What it runs is the copy deploy/install-auto-deploy.sh put
# in /usr/local/lib/remitwatch, never the file in the checkout, so a commit cannot change what runs here as root. A new
# version of this script takes effect only when a person runs the installer again (--update); the log says when a
# deploy brings one. Each run fetches the branch and, when its newest commit is not the one this server runs, deploys it:
#   - only docs changed (docs/ and *.md): the files are checked out; nothing is rebuilt or restarted;
#   - otherwise build first, swap second: the files are checked out, Caddy's image is refreshed, `docker compose build
#     --pull` runs, and Caddy (the image about to run) checks its file. Only when all of that succeeds, and no data cycle
#     or fax run is going in the app, does `docker compose up -d` replace the running containers. It waits up to 5
#     minutes for a run to end; after that it puts everything back and a later check tries again. Caddy is restarted
#     when its file changed. The site must then answer through Caddy within 3 minutes. If it does not, the previous
#     images and files are put back and started again, and the new commit is held (not tried again by itself). A commit
#     is recorded as deployed only once the site answers.
#   - a failed build, or a Caddy file Caddy cannot load, puts the files and images back and leaves the running site
#     alone. It is logged and the next tick tries again; after three failures of the same commit, once an hour.
#   - files changed by hand in the checkout stop it, and so does a commit that would overwrite a file git does not track
#     there (.env included): nothing is overwritten, and the log says so once.
#   - while a person keeps the app stopped (docker compose stop app), nothing is deployed.
# Every attempt is one line in /var/log/remitwatch-deploy.log: time, from-commit, to-commit, result, detail. The build's
# own output is kept in /var/lib/remitwatch-deploy/builds/ (the last 30), the commit running in
# /var/lib/remitwatch-deploy/deployed. Images and build cache nothing uses any more are pruned after each attempt.
#
#   auto-deploy.sh             one check (what the timer runs)
#   auto-deploy.sh --force     deploy the branch's newest commit now, even when it is the one running, was held, or the
#                              app is stopped
#   auto-deploy.sh --status    the commit running, the branch's newest, the timer, the last deploys
#   auto-deploy.sh --idle [S]  wait up to S seconds (default 300) until no data cycle or fax run is going in the app;
#                              exit 1 if one still is (configure.sh and the nightly system update use it)
#
# The whole script is read before it runs (main below).
set -uo pipefail

STATE=${REMITWATCH_DEPLOY_STATE:-/var/lib/remitwatch-deploy}
HERE=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)
if [ -n "${REMITWATCH_DIR:-}" ]; then
  DIR=$REMITWATCH_DIR
elif [ -d "$HERE/.git" ] && [ -f "$HERE/deploy/auto-deploy.sh" ]; then
  DIR=$HERE                                         # run from a checkout (configure.sh, or by hand)
else
  DIR=$(cat "$STATE/dir" 2>/dev/null || true)       # the installed copy: the checkout the installer named
  DIR=${DIR:-/opt/remitwatch}
fi
LIB=${REMITWATCH_DEPLOY_LIB:-/usr/local/lib/remitwatch}
LOG=${REMITWATCH_DEPLOY_LOG:-/var/log/remitwatch-deploy.log}
DOCKER=${REMITWATCH_DOCKER:-docker}
IDLE_WAIT=${REMITWATCH_DEPLOY_IDLE_WAIT:-300}       # seconds to wait for a data cycle or fax run to end before the swap
HEALTH_WAIT=${REMITWATCH_DEPLOY_HEALTH_WAIT:-180}   # seconds the new site has to answer through Caddy
POLL=${REMITWATCH_DEPLOY_POLL:-10}
RETRY_AFTER=${REMITWATCH_DEPLOY_RETRY_AFTER:-3600}  # after three failed builds of one commit, try it this often
STEP=$(( POLL > 0 ? POLL : 1 ))
BUSY=""

git() { command git -c safe.directory="$DIR" -C "$DIR" "$@"; }
compose() { (cd "$DIR" && "$DOCKER" compose "$@"); }
in_app() { compose exec -T app python -c "$@" 2>/dev/null; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# The locks of the app's runs, read inside its container: the data cycle's (pipeline:lock) and any other run's that
# keeps one the same way, such as the fax run (a row of the store's meta table whose key ends in ":lock", with the
# holder's "pid" and the time it "started"). "busy" prints the key of each lock whose holder is a process running in the
# container now. A process that started after the lock was taken does not count: it only got the same number. A lock
# with no pid counts while its heartbeat is under 15 minutes old. "clear" removes the locks whose holder is gone (a run
# that ended with its container), so that the next run is not held up for hours.
read -r -d '' LOCKS_PY <<'PY'
import json, os, sqlite3, sys
from datetime import datetime, timezone


def began(pid):
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            if b"remitwatch" not in f.read():
                return None
        with open(f"/proc/{int(pid)}/stat") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat") as f:
            boot = next(int(ln.split()[1]) for ln in f if ln.startswith("btime"))
        return boot + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, TypeError, IndexError, StopIteration):
        return None


def when(held, *keys):
    for k in keys:
        try:
            return datetime.fromisoformat(held[k]).timestamp()
        except (KeyError, TypeError, ValueError):
            pass
    return None


def live(held):
    if held.get("pid"):
        start, took = began(held["pid"]), when(held, "started", "heartbeat")
        return start is not None and (took is None or start <= took + 5)
    beat = when(held, "heartbeat", "started")
    return beat is not None and datetime.now(timezone.utc).timestamp() - beat < 900


db = os.environ.get("REMITWATCH_DB", "/data/remitwatch.sqlite")
if os.path.exists(db) and os.path.getsize(db) > 0:
    c = sqlite3.connect(db, timeout=30)
    try:
        rows = c.execute("SELECT key, value FROM meta WHERE key LIKE '%:lock'").fetchall()
    except sqlite3.Error:
        rows = []
    for key, value in rows:
        try:
            held = json.loads(value)
        except (TypeError, ValueError):
            held = None
        held = held if isinstance(held, dict) else {}
        if sys.argv[1] == "busy" and live(held):
            print(key)
        elif sys.argv[1] == "clear" and held.get("pid") and not live(held):
            c.execute("DELETE FROM meta WHERE key=? AND value=?", (key, value))
            c.commit()
            print(key)
PY

busy_now() { in_app "$LOCKS_PY" busy | paste -sd ' ' -; }
clear_ended() { in_app "$LOCKS_PY" clear | paste -sd ' ' -; }

wait_idle() {    # seconds: 0 once no data cycle or fax run is going in the app; 1 if one still is then (BUSY names it)
  local waited=0
  BUSY=$(busy_now)
  while [ -n "$BUSY" ]; do
    [ "$waited" -ge "$1" ] && return 1
    sleep "$POLL"
    waited=$((waited + STEP))
    BUSY=$(busy_now)
  done
  return 0
}

record() {      # result detail: one line in the log, and in the journal
  printf '%s\tfrom=%s\tto=%s\t%s\t%s\n' "$(now)" "${FROM:0:12}" "${TO:0:12}" "$1" "$2" >> "$LOG"
  echo "deploy ${FROM:0:12} -> ${TO:0:12}: $1. $2"
}

alert() {       # result detail: a failure also goes to the practice's chat webhook (ALERT_WEBHOOK_URL in .env), so it is
                # seen without a shell on the server. Commits and results only, nothing about patients.
  local url text
  url=$(grep -m1 '^ALERT_WEBHOOK_URL=' "$DIR/.env" 2>/dev/null | cut -d= -f2- | sed "s/^['\"]//; s/['\"]\$//")
  [ -n "$url" ] || return 0
  text=$(printf 'RemitWatch server, automatic deploy of %s: %s. %s' "${TO:0:12}" "$1" "$2" | tr -d '\000-\037' | sed 's/\\/\\\\/g; s/"/\\"/g')
  # the address is the secret: curl reads it on its standard input, never from its command line
  printf 'url = "%s"\n' "$url" | curl -fsS -o /dev/null --max-time 10 -H 'Content-Type: application/json' \
    --data "{\"text\": \"$text\"}" -K - >/dev/null 2>&1 || true
}

failed() {      # result detail [hold]: logged, and counted against this commit for the retry rule; a hold stays on the
                # commit until a deploy of it succeeds (a forced try that fails keeps it). The first failure of a commit,
                # and a hold, also go to the alert webhook.
  local sha="" count=0 when=0 hold=""
  [ -f "$STATE/failed" ] && read -r sha count when hold < "$STATE/failed"
  if [ "$sha" = "$TO" ]; then count=$((count + 1)); else count=1; hold=""; fi
  [ -n "${3:-}" ] && hold=$3
  echo "$TO $count $(date +%s) $hold" > "$STATE/failed"
  record "$1" "$2"
  if [ "$count" = 1 ] || [ -n "${3:-}" ]; then alert "$1" "$2"; fi
}

holding() {     # whether this commit is to be left alone for now (a rollback, or three failed builds within the hour)
  local sha="" count=0 when=0 hold=""
  [ -f "$STATE/failed" ] || return 1
  read -r sha count when hold < "$STATE/failed"
  [ "$sha" = "$TO" ] || return 1
  if [ "$hold" = hold ]; then
    echo "${TO:0:12} was rolled back; it is not deployed again by itself (push a fix, or run $0 --force)."
    return 0
  fi
  if [ "${count:-0}" -ge 3 ] && [ $(( $(date +%s) - ${when:-0} )) -lt "$RETRY_AFTER" ]; then
    echo "${TO:0:12} failed to build ${count} times; it is tried again an hour after the last try."
    return 0
  fi
  return 1
}

checkout() { git checkout -q -f -B "$BRANCH" "$1"; }

hand_changes() {  # prints why the checkout must not be touched: files changed by hand, or files the commit would
                  # overwrite that git does not track here (a local override, or .env itself)
  local dirty clobber
  dirty=$(git status --porcelain --untracked-files=no | cut -c4- | head -n 5 | paste -sd ' ' -)
  if [ -n "$dirty" ]; then
    echo "files in $DIR were changed by hand ($dirty); nothing was deployed or overwritten. Put them back (git checkout -- .) or commit the change to main"
    return 0
  fi
  clobber=$(LC_ALL=C comm -12 <(git ls-tree -r --name-only "$TO" | LC_ALL=C sort) <(git ls-files --others | LC_ALL=C sort) | head -n 5 | paste -sd ' ' -)
  if [ -n "$clobber" ]; then
    echo "${TO:0:12} would overwrite files in $DIR that git does not track ($clobber); nothing was deployed or overwritten. Move them out of the way (one may hold settings) and a later check deploys"
    return 0
  fi
  return 1
}

site_host() {   # the address Caddy serves: SITE_ADDRESS in .env (Caddy's own default, as docker-compose.yml sets it, is localhost)
  local h
  h=$(grep -m1 '^SITE_ADDRESS=' "$DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d "\"' \r")
  echo "${h:-localhost}"
}

answers() {     # the app answers in its container, and the site answers through Caddy, as a visitor reaches it: HTTPS on
                # this host's port 443. The certificate is not judged here: renewing it is Caddy's job, and a rollback
                # would not change it.
  local host
  in_app 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/robots.txt", timeout=5).read()' || return 1
  host=$(site_host)
  curl -fsSk -o /dev/null --max-time 10 --resolve "$host:443:127.0.0.1" "https://$host/robots.txt" 2>/dev/null
}

healthy() {
  local waited=0
  until answers; do
    [ "$waited" -ge "$HEALTH_WAIT" ] && return 1
    sleep "$POLL"
    waited=$((waited + STEP))
  done
}

images_now() {  # "service image-name image-id" for the app and Caddy as they run now
  local s c
  for s in app caddy; do
    c=$(compose ps -q "$s" 2>/dev/null | head -n 1)
    [ -n "$c" ] && "$DOCKER" inspect --format "$s {{.Config.Image}} {{.Image}}" "$c" 2>/dev/null
  done
}

put_back() {    # the files, and each image name, as they were before this attempt
  local s name id
  checkout "$BACK" >>"$BLOG" 2>&1
  while read -r s name id; do
    [ -n "$id" ] && "$DOCKER" tag "$id" "${name%@*}" >>"$BLOG" 2>&1
  done <<<"$PREV"
}

rollback() {    # result why: after a swap that failed, the previous images and files started again; the new commit held
  local how
  put_back
  if compose up -d >>"$BLOG" 2>&1 && compose restart caddy >>"$BLOG" 2>&1 && healthy; then
    how="rolled back to ${BACK:0:12}, which answers again; the new commit is held"
  else
    how="rolled back to ${BACK:0:12}, but the site still does not answer: see docker compose ps and docker compose logs"
  fi
  failed "$1" "$2; $how. Output: $BLOG" hold
  prune
}

prune() {       # images no container uses and no name points at (the ones a swap replaced or a rollback dropped), build
                # cache no build has used for 30 days, and build logs beyond the last 30: the disk does not fill with old
                # versions, and the next build still finds this one's layers
  "$DOCKER" image prune -f >/dev/null 2>&1 || true
  "$DOCKER" builder prune -af --filter until=720h >/dev/null 2>&1 || true
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
    echo "Timer:    off (sudo bash $DIR/deploy/install-auto-deploy.sh switches it on)"
  fi
  if [ ! -f "$LIB/auto-deploy.sh" ]; then
    echo "Script:   none installed in $LIB (the installer puts it there)"
  elif cmp -s "$LIB/auto-deploy.sh" "$DIR/deploy/auto-deploy.sh"; then
    echo "Script:   $LIB/auto-deploy.sh, the same as the checkout's"
  else
    echo "Script:   $LIB/auto-deploy.sh; the checkout's deploy/auto-deploy.sh is different. Read it, then put it to work:" \
         "sudo bash $DIR/deploy/install-auto-deploy.sh --update"
  fi
  [ -f "$STATE/failed" ] && echo "Failing:  $(cat "$STATE/failed")  (commit, failures, last try, hold)"
  [ -f "$STATE/refused" ] && echo "Waiting:  $(cat "$STATE/refused") waits for files changed by hand in $DIR (see the log)"
  echo "Last deploys ($LOG):"
  tail -n 10 "$LOG" 2>/dev/null || echo "  none yet"
}

main() {
  local mode=${1:-check} changed note="" t0=$SECONDS before after why cleared
  case "$mode" in
    --status) status; return 0 ;;
    --idle)
      wait_idle "${2:-$IDLE_WAIT}" && return 0
      echo "Still going in the app after ${2:-$IDLE_WAIT}s: $BUSY"
      return 1 ;;
    check|--force) ;;
    *) echo "usage: $0 [--force | --status | --idle [seconds]]"; return 2 ;;
  esac
  mkdir -p "$STATE/builds" && chmod 700 "$STATE"
  exec 9>"$STATE/lock"
  if [ "$mode" = --force ]; then flock -w 600 9; else flock -n 9; fi
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
    if [ -z "$(compose ps -q app 2>/dev/null)" ] && [ -n "$(compose ps -a -q app 2>/dev/null)" ]; then
      echo "The app is stopped (docker compose stop app): ${TO:0:12} is deployed once it runs again, or now with --force."
      return 0
    fi
  fi
  FROM=${FROM:-none}
  if why=$(hand_changes); then
    if [ "$mode" != --force ] && [ "$(cat "$STATE/refused" 2>/dev/null)" = "$TO" ]; then
      echo "${TO:0:12} waits: $why"           # logged once already
      return 0
    fi
    echo "$TO" > "$STATE/refused"
    record refused "$why"
    alert refused "$why"
    return 1
  fi
  rm -f "$STATE/refused"
  BACK=$(git rev-parse --verify --quiet HEAD)        # what goes back if this attempt fails: the commit running, when known
  if [ "$FROM" != none ] && git cat-file -e "$FROM^{commit}" 2>/dev/null; then
    changed=$(git diff --name-only "$FROM" "$TO")
    BACK=$FROM
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
  if [ "$mode" != --force ] && BUSY=$(busy_now) && [ -n "$BUSY" ]; then
    echo "A run is going in the app ($BUSY): ${TO:0:12} is deployed by a check after it ends."
    return 0
  fi
  BLOG="$STATE/builds/$(date -u +%Y%m%dT%H%M%SZ)-${TO:0:12}.log"
  PREV=$(images_now)
  if ! checkout "$TO"; then
    failed checkout-failed "git could not check out ${TO:0:12}"
    return 1
  fi
  : >"$BLOG"
  if ! compose pull --quiet caddy >>"$BLOG" 2>&1; then
    note="$note; Caddy's image could not be refreshed (the one here was kept)"
  fi
  if ! compose build --pull >>"$BLOG" 2>&1; then
    echo "== the build with fresh base images failed; once more with the ones here" >>"$BLOG"
    if ! compose build >>"$BLOG" 2>&1; then
      put_back
      failed build-failed "the running site was left as it was and the files put back; the next check tries again. Output: $BLOG"
      prune
      return 1
    fi
    note="$note; the base image could not be refreshed (built on the one here)"
  fi
  if ! compose run --rm --no-deps -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >>"$BLOG" 2>&1; then
    put_back
    failed caddy-refused "Caddy cannot load deploy/Caddyfile as of ${TO:0:12} (its own check, with the image about to run); the running site was left as it was and the files put back. Output: $BLOG"
    prune
    return 1
  fi
  if ! wait_idle "$IDLE_WAIT"; then                  # never swap while a run is going: a later check deploys
    put_back
    record waiting "a run ($BUSY) was still going in the app after ${IDLE_WAIT}s: nothing was swapped, the files were put back, and a later check deploys once it ends"
    prune
    return 0
  fi
  before=$(compose ps -q app 2>/dev/null)
  if ! compose up -d >>"$BLOG" 2>&1; then
    rollback up-failed "docker compose up -d failed"
    return 1
  fi
  after=$(compose ps -q app 2>/dev/null)
  if [ "$after" != "$before" ]; then
    cleared=$(clear_ended)
    [ -n "$cleared" ] && note="$note; the lock of a run that had ended ($cleared) was cleared, so the next one starts by itself"
  fi
  if [ "$changed" = "(everything)" ] || grep -qx 'deploy/Caddyfile' <<<"$changed"; then
    compose restart caddy >>"$BLOG" 2>&1          # a Caddy that does not come back fails the check below
  fi
  if ! healthy; then
    rollback unhealthy "the site did not answer within ${HEALTH_WAIT}s (in the app, and through Caddy at https://$(site_host)/robots.txt)"
    return 1
  fi
  echo "$TO" > "$STATE/deployed"
  rm -f "$STATE/failed"
  prune
  if grep -qE '^deploy/(auto-deploy\.sh|install-auto-deploy\.sh|systemd/)' <<<"$changed"; then
    note="$note; it changes the deploy script or its timer, which keep running as they were until a person reads the change and runs sudo bash $DIR/deploy/install-auto-deploy.sh --update"
  fi
  record deployed "$((SECONDS - t0))s; $( [ "$changed" = "(everything)" ] && echo "first deploy by this checker" || echo "$(wc -l <<<"$changed") file(s) changed")$note. Output: $BLOG"
  return 0
}

main "$@"; exit $?
