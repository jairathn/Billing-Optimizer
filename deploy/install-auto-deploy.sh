#!/bin/bash
# Switch RemitWatch's auto-deploy on (or off) on this server: a systemd timer that, every 5 minutes, deploys the newest
# commit on main by itself (deploy/auto-deploy.sh; docs/SERVER_RUNBOOK.md, "Automatic deploys").
#
#   sudo /opt/remitwatch/deploy/install-auto-deploy.sh                  switch it on, and deploy main's newest commit now
#   sudo /opt/remitwatch/deploy/install-auto-deploy.sh --mark-current   switch it on; the commit checked out is the one
#                                                                       running (just built: first boot, configure.sh)
#   sudo /opt/remitwatch/deploy/install-auto-deploy.sh --off            switch it off (its log and state stay)
#   sudo /opt/remitwatch/deploy/install-auto-deploy.sh --units-only     reinstall the timer's files (auto-deploy does this)
set -euo pipefail
DIR=${REMITWATCH_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
STATE=${REMITWATCH_DEPLOY_STATE:-/var/lib/remitwatch-deploy}
UNITS=${REMITWATCH_SYSTEMD_DIR:-/etc/systemd/system}
LOG=${REMITWATCH_DEPLOY_LOG:-/var/log/remitwatch-deploy.log}
mode=${1:-now}
git() { command git -c safe.directory="$DIR" -C "$DIR" "$@"; }

units() {       # the unit files, pointed at this checkout
  local tmp
  tmp=$(mktemp)
  sed "s#/opt/remitwatch#$DIR#g" "$DIR/deploy/systemd/remitwatch-deploy.service" > "$tmp"
  install -m 644 "$tmp" "$UNITS/remitwatch-deploy.service"
  sed "s#/opt/remitwatch#$DIR#g" "$DIR/deploy/systemd/remitwatch-deploy.timer" > "$tmp"
  install -m 644 "$tmp" "$UNITS/remitwatch-deploy.timer"
  rm -f "$tmp"
  systemctl daemon-reload
}

case "$mode" in
  now|--mark-current|--off|--units-only) ;;
  *) echo "usage: $0 [--mark-current | --off | --units-only]"; exit 2 ;;
esac
if [ "$(id -u)" != 0 ] && [ -z "${REMITWATCH_SYSTEMD_DIR:-}" ]; then
  echo "Run it as root: sudo $0 $*"
  exit 1
fi
if [ "$mode" = --off ]; then
  systemctl disable --now remitwatch-deploy.timer || true
  echo "Auto-deploy is off: new commits on main are not deployed until it is switched on again. Its log: $LOG"
  exit 0
fi
if [ "$mode" = --units-only ]; then
  units
  exit 0
fi
if ! command -v flock >/dev/null 2>&1; then         # the lock that keeps two deploys (or a deploy and configure.sh) apart
  dnf -y install util-linux >/dev/null 2>&1 || apt-get install -y util-linux >/dev/null 2>&1 || true
  command -v flock >/dev/null 2>&1 || { echo "flock is missing and could not be installed (util-linux): auto-deploy needs it."; exit 1; }
fi
install -d -m 700 "$STATE"
branch=$(git symbolic-ref --quiet --short HEAD || echo main)
echo "$branch" > "$STATE/branch"
units
first=0
if [ "$mode" = --mark-current ]; then
  git rev-parse HEAD > "$STATE/deployed"
else
  echo "Deploying the newest commit on origin/$branch now (build first, swap second; a few minutes)…"
  bash "$DIR/deploy/auto-deploy.sh" --force || first=1
fi
systemctl enable --now remitwatch-deploy.timer
cat <<DONE

Auto-deploy is on. Every 5 minutes this server looks at origin/$branch and deploys a new commit by itself: docs-only
changes are checked out without a rebuild; anything else is built first and swapped in only when the build succeeds.
  What runs, and the last deploys:  sudo bash $DIR/deploy/auto-deploy.sh --status
  The log (one line per deploy):    sudo tail -n 20 $LOG
  Switch it off:                    sudo bash $DIR/deploy/install-auto-deploy.sh --off
DONE
if [ "$first" = 1 ]; then
  echo
  echo "The first deploy did not finish (the running site was left as it was): see sudo tail -n 5 $LOG. The timer tries again."
  exit 1
fi
