#!/bin/bash
# Switch RemitWatch's auto-deploy on (or off) on this server: a systemd timer that, every 5 minutes, deploys the newest
# commit on main by itself (deploy/auto-deploy.sh; docs/SERVER_RUNBOOK.md, "Automatic deploys").
#
# The timer runs a copy of deploy/auto-deploy.sh that this installs in /usr/local/lib/remitwatch (root's only), never
# the file in the checkout: a commit on main cannot change what runs here as root. A new version of the script, or of
# the timer's files, takes effect only when a person runs this again; the deploy log says when a deploy brought one.
#
#   sudo /opt/remitwatch/deploy/install-auto-deploy.sh                  switch it on, and deploy main's newest commit now
#   sudo /opt/remitwatch/deploy/install-auto-deploy.sh --mark-current   switch it on; the commit checked out is the one
#                                                                       running (just built: first boot, configure.sh)
#   sudo /opt/remitwatch/deploy/install-auto-deploy.sh --update         install the checkout's version of the deploy
#                                                                       script and the timer's files; deploys nothing
#   sudo /opt/remitwatch/deploy/install-auto-deploy.sh --off            switch it off (its log and state stay)
set -euo pipefail
DIR=${REMITWATCH_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
STATE=${REMITWATCH_DEPLOY_STATE:-/var/lib/remitwatch-deploy}
UNITS=${REMITWATCH_SYSTEMD_DIR:-/etc/systemd/system}
LIB=${REMITWATCH_DEPLOY_LIB:-/usr/local/lib/remitwatch}
LOG=${REMITWATCH_DEPLOY_LOG:-/var/log/remitwatch-deploy.log}
mode=${1:-now}
git() { command git -c safe.directory="$DIR" -C "$DIR" "$@"; }

install_files() {   # the deploy script, copied out of the checkout, and the timer's files, pointed at that copy
  local tmp f
  if ! bash -n "$DIR/deploy/auto-deploy.sh"; then
    echo "deploy/auto-deploy.sh in $DIR does not parse: nothing was installed, and the copy that runs stays as it was."
    exit 1
  fi
  install -d -m 755 "$LIB"
  install -m 700 "$DIR/deploy/auto-deploy.sh" "$LIB/auto-deploy.sh.new"
  mv -f "$LIB/auto-deploy.sh.new" "$LIB/auto-deploy.sh"     # a check running now keeps the file it opened
  install -d -m 700 "$STATE"
  echo "$DIR" > "$STATE/dir"                                # the checkout the copy deploys
  tmp=$(mktemp)
  for f in remitwatch-deploy.service remitwatch-deploy.timer; do
    sed -e "s#/usr/local/lib/remitwatch#$LIB#g" -e "s#/opt/remitwatch#$DIR#g" "$DIR/deploy/systemd/$f" > "$tmp"
    install -m 644 "$tmp" "$UNITS/$f"
  done
  rm -f "$tmp"
  systemctl daemon-reload
}

case "$mode" in
  now|--mark-current|--off|--update) ;;
  --units-only) mode=--update ;;                # the older name, which an older deploy script calls by itself
  *) echo "usage: $0 [--mark-current | --update | --off]"; exit 2 ;;
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
# flock keeps two deploys (or a deploy and configure.sh) apart; curl checks the site through Caddy after a swap
for need in flock:util-linux curl:curl-minimal; do
  if ! command -v "${need%%:*}" >/dev/null 2>&1; then
    dnf -y install "${need#*:}" >/dev/null 2>&1 || apt-get install -y "${need%%:*}" >/dev/null 2>&1 || true
    command -v "${need%%:*}" >/dev/null 2>&1 || { echo "${need%%:*} is missing and could not be installed: auto-deploy needs it."; exit 1; }
  fi
done
install_files
if [ "$mode" = --update ]; then
  echo "Installed the deploy script and the timer's files of $(git rev-parse --short HEAD) ($LIB/auto-deploy.sh). The next check uses them."
  exit 0
fi
branch=$(git symbolic-ref --quiet --short HEAD || echo main)
echo "$branch" > "$STATE/branch"
first=0
if [ "$mode" = --mark-current ]; then
  git rev-parse HEAD > "$STATE/deployed"
else
  echo "Deploying the newest commit on origin/$branch now (build first, swap second; a few minutes)…"
  bash "$LIB/auto-deploy.sh" --force || first=1
fi
systemctl enable --now remitwatch-deploy.timer
cat <<DONE

Auto-deploy is on. Every 5 minutes this server looks at origin/$branch and deploys a new commit by itself: docs-only
changes are checked out without a rebuild; anything else is built first and swapped in only when the build succeeds.
  What runs, and the last deploys:  sudo bash $LIB/auto-deploy.sh --status
  The log (one line per deploy):    sudo tail -n 20 $LOG
  Switch it off:                    sudo bash $DIR/deploy/install-auto-deploy.sh --off
DONE
if [ "$first" = 1 ]; then
  echo
  echo "The first deploy did not finish (the running site was left as it was): see sudo tail -n 5 $LOG. The timer tries again."
  exit 1
fi
