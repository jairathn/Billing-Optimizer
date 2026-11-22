"""
Auto-deploy on the server (deploy/auto-deploy.sh, deploy/install-auto-deploy.sh, deploy/systemd/): the server follows
main by itself. Each test runs the real scripts against real git repositories (a bare "origin" and the server's
checkout), with stand-ins for docker, curl and systemctl. The docker stand-in keeps what a real one would: which image
each name points at, which image each running container was made from, and which Caddy file Caddy loaded. Nothing here
touches the machine's own docker, systemd, /usr/local or /var.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("git") and shutil.which("flock")), reason="needs bash, git and flock")

FAKE_DOCKER = r'''#!/bin/bash
# a stand-in for docker. Every call is recorded in $FAKE/calls. It keeps the image each name points at (tag_app,
# tag_caddy), the container of each service as "<id> <image>" (ctr_app, ctr_caddy; <svc>_stopped when stopped) and the
# Caddy file Caddy loaded (caddy_loaded). Files in $FAKE make steps fail or change: fail_build, fail_pull_base, fail_up,
# unhealthy (the app does not answer), busy (keys of the locks of runs going in the app; busy_left counts the checks
# before they end), stale (locks of runs that ended), caddy_update (the image Caddy's name gets on the next pull).
echo "$*" >> "$FAKE/calls"
get() { cat "$FAKE/$1" 2>/dev/null; }
up() {   # service: made again when missing, stopped, or on another image than its name points at now
  local s=$1 img cur
  img=$(get "tag_$s"); cur=$(get "ctr_$s")
  if [ -z "$cur" ] || [ "${cur#* }" != "$img" ] || [ -f "$FAKE/${s}_stopped" ]; then
    echo "$s-$(date +%s%N) $img" > "$FAKE/ctr_$s"
    rm -f "$FAKE/${s}_stopped"
    [ "$s" = caddy ] && cp deploy/Caddyfile "$FAKE/caddy_loaded"
  fi
  return 0
}
case "$1 $2" in
  "compose build")
    git rev-parse HEAD >> "$FAKE/built"
    [ -f "$FAKE/fail_build" ] && { echo "build broke"; exit 1; }
    [ "${3:-}" = --pull ] && [ -f "$FAKE/fail_pull_base" ] && { echo "registry unreachable"; exit 1; }
    echo "app-$(git rev-parse HEAD)" > "$FAKE/tag_app"; exit 0 ;;
  "compose pull")
    [ -f "$FAKE/caddy_update" ] && { cp "$FAKE/caddy_update" "$FAKE/tag_caddy"; rm -f "$FAKE/caddy_update"; }
    exit 0 ;;
  "compose run")
    case "$*" in *"caddy validate"*) grep -q INVALID deploy/Caddyfile && { echo "Error: adapting config: bad"; exit 1; }; echo "Valid configuration" ;; esac
    exit 0 ;;
  "compose up")
    [ -f "$FAKE/fail_up" ] && exit 1
    up app; up caddy; exit 0 ;;
  "compose stop")
    touch "$FAKE/${3}_stopped"; exit 0 ;;
  "compose ps")
    svc=${!#}; all=0
    for a in "$@"; do [ "$a" = -a ] && all=1; done
    cur=$(get "ctr_$svc")
    if [ -n "$cur" ] && { [ "$all" = 1 ] || [ ! -f "$FAKE/${svc}_stopped" ]; }; then echo "${cur%% *}"; fi
    exit 0 ;;
  "compose restart")
    cp deploy/Caddyfile "$FAKE/caddy_loaded"; exit 0 ;;
  "compose exec")
    { [ -f "$FAKE/app_stopped" ] || [ -z "$(get ctr_app)" ]; } && exit 1
    code=$7; what=${8:-}
    case "$code" in
      *robots.txt*) [ -f "$FAKE/unhealthy" ] && exit 1; exit 0 ;;
      *"SELECT key, value FROM meta"*)
        if [ "$what" = busy ] && [ -f "$FAKE/busy" ]; then
          if [ -f "$FAKE/busy_left" ]; then
            n=$(get busy_left)
            if [ "$n" -le 0 ]; then rm -f "$FAKE/busy" "$FAKE/busy_left"; exit 0; fi
            echo $((n - 1)) > "$FAKE/busy_left"
          fi
          cat "$FAKE/busy"
        elif [ "$what" = clear ] && [ -f "$FAKE/stale" ]; then
          tee -a "$FAKE/cleared" < "$FAKE/stale"; rm -f "$FAKE/stale"
        fi
        exit 0 ;;
    esac
    exit 0 ;;
  "inspect --format")
    svc=${3%% *}; cur=$(get "ctr_$svc")
    [ "${cur%% *}" = "$4" ] || exit 1
    echo "$svc $([ "$svc" = app ] && echo remitwatch-app || echo caddy:2) ${cur#* }"; exit 0 ;;
esac
case "$1" in
  tag) [ "$3" = remitwatch-app ] && echo "$2" > "$FAKE/tag_app"; [ "$3" = caddy:2 ] && echo "$2" > "$FAKE/tag_caddy"; exit 0 ;;
esac
exit 0
'''
FAKE_CURL = r'''#!/bin/bash
# a stand-in for curl, for the check through Caddy: the site answers when Caddy runs a file that proxies and the app answers
echo "$*" >> "$FAKE/curl"
[ -n "$(cat "$FAKE/ctr_caddy" 2>/dev/null)" ] || exit 7
[ -f "$FAKE/unhealthy" ] && exit 22
grep -q NOPROXY "$FAKE/caddy_loaded" 2>/dev/null && exit 22
exit 0
'''
FAKE_SYSTEMCTL = r'''#!/bin/bash
echo "$*" >> "$FAKE/systemctl"
case "$1" in is-enabled) [ -f "$FAKE/timer_on" ] && exit 0; exit 1 ;; enable) touch "$FAKE/timer_on" ;; disable) rm -f "$FAKE/timer_on" ;; esac
exit 0
'''


def sh(*args, cwd=None, env=None, check=True, input=None):
    r = subprocess.run(list(args), cwd=cwd, env=env, capture_output=True, text=True, input=input, timeout=120)
    if check and r.returncode != 0:
        raise AssertionError(f"{args} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


class Box:
    """A bare origin, a developer's clone that pushes to it, the server's checkout at /opt/remitwatch's place, and the
    places the installer writes to (the units, the copy of the deploy script, the state)."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.origin, self.dev, self.dir = tmp / "origin.git", tmp / "dev", tmp / "opt-remitwatch"
        self.state, self.log, self.fake, self.units, self.lib = tmp / "state", tmp / "deploy.log", tmp / "fake", tmp / "units", tmp / "lib"
        for d in (self.fake, self.units, tmp / "bin"):
            d.mkdir()
        for name, text in (("docker", FAKE_DOCKER), ("systemctl", FAKE_SYSTEMCTL), ("curl", FAKE_CURL)):
            (tmp / "bin" / name).write_text(text)
            (tmp / "bin" / name).chmod(0o755)
        self.env = dict(os.environ, PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", FAKE=str(self.fake), REMITWATCH_DIR=str(self.dir),
                        REMITWATCH_DEPLOY_STATE=str(self.state), REMITWATCH_DEPLOY_LOG=str(self.log), REMITWATCH_SYSTEMD_DIR=str(self.units),
                        REMITWATCH_DEPLOY_LIB=str(self.lib), REMITWATCH_DEPLOY_POLL="0", REMITWATCH_DEPLOY_HEALTH_WAIT="0",
                        REMITWATCH_DEPLOY_IDLE_WAIT="0",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.org", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.org")
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        sh("git", "clone", "-q", str(self.origin), str(self.dev), env=self.env)
        sh("git", "checkout", "-q", "-b", "main", cwd=self.dev, env=self.env)
        for rel in ("deploy/auto-deploy.sh", "deploy/install-auto-deploy.sh", "deploy/systemd/remitwatch-deploy.service",
                    "deploy/systemd/remitwatch-deploy.timer", "deploy/Caddyfile", "docker-compose.yml", "Dockerfile"):
            os.makedirs(self.dev / os.path.dirname(rel), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), self.dev / rel)
        (self.dev / "remitwatch").mkdir()
        (self.dev / "remitwatch" / "api.py").write_text("x = 1\n")
        (self.dev / "docs").mkdir()
        (self.dev / "docs" / "SERVER_RUNBOOK.md").write_text("runbook\n")
        (self.dev / "README.md").write_text("readme\n")
        self.first = self.commit("first")
        sh("git", "clone", "-q", "-b", "main", str(self.origin), str(self.dir), env=self.env)

    def boot(self):
        """What first boot does before the installer: `docker compose up -d --build` from the checkout."""
        self.write("tag_app", f"app-{self.head}\n")
        self.write("tag_caddy", "caddy-1\n")
        self.write("ctr_app", f"app-0 app-{self.head}\n")
        self.write("ctr_caddy", "caddy-0 caddy-1\n")
        shutil.copy(self.dir / "deploy" / "Caddyfile", self.fake / "caddy_loaded")

    def commit(self, msg, files=None):
        for rel, text in (files or {}).items():
            p = self.dev / rel
            os.makedirs(p.parent, exist_ok=True)
            p.write_text(text)
        sh("git", "add", "-A", cwd=self.dev, env=self.env)
        sh("git", "commit", "-q", "--allow-empty", "-m", msg, cwd=self.dev, env=self.env)
        sh("git", "push", "-q", "origin", "main", cwd=self.dev, env=self.env)
        return sh("git", "rev-parse", "HEAD", cwd=self.dev).stdout.strip()

    def run(self, *args, check=False):
        """One tick, as the timer runs it: the copy the installer made, not the checkout's file."""
        return sh("bash", str(self.lib / "auto-deploy.sh"), *args, env=self.env, check=check)

    def install(self, *args, check=True):
        return sh("bash", str(self.dir / "deploy" / "install-auto-deploy.sh"), *args, env=self.env, check=check)

    def read(self, name, default=""):
        p = self.fake / name
        return p.read_text() if p.exists() else default

    def write(self, name, text):
        (self.fake / name).write_text(text)

    @property
    def running(self):
        """The commit whose image the app's container was made from."""
        return self.read("ctr_app").split()[1].removeprefix("app-") if self.read("ctr_app") else ""

    @property
    def marker(self):
        p = self.state / "deployed"
        return p.read_text().strip() if p.exists() else ""

    @property
    def head(self):
        return sh("git", "rev-parse", "HEAD", cwd=self.dir).stdout.strip()

    def log_lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def last(self):
        return self.log_lines()[-1].split("\t")

    def calls(self):
        return self.read("calls").splitlines()


@pytest.fixture()
def box(tmp_path):
    b = Box(tmp_path)
    b.boot()
    b.install("--mark-current")          # as at first boot: the image was just built from the checkout
    return b


def test_switching_it_on_installs_the_timer_pointed_at_a_copy_outside_the_checkout(box):
    service = (box.units / "remitwatch-deploy.service").read_text()
    timer = (box.units / "remitwatch-deploy.timer").read_text()
    assert f"ExecStart=/bin/bash {box.lib}/auto-deploy.sh" in service and f"WorkingDirectory={box.dir}" in service
    assert str(box.dir / "deploy" / "auto-deploy.sh") not in service
    assert "User=root" in service and "Type=oneshot" in service and "Requires=docker.service" in service
    assert "OnCalendar=*:0/5" in timer and "WantedBy=timers.target" in timer
    assert box.read("systemctl").splitlines() == ["daemon-reload", "enable --now remitwatch-deploy.timer"]
    assert box.marker == box.first and (box.state / "branch").read_text().strip() == "main"
    assert oct(box.state.stat().st_mode & 0o777) == "0o700"
    copy = box.lib / "auto-deploy.sh"
    assert copy.read_bytes() == (box.dir / "deploy" / "auto-deploy.sh").read_bytes() and oct(copy.stat().st_mode & 0o777) == "0o700"
    assert (box.state / "dir").read_text().strip() == str(box.dir)
    if shutil.which("systemd-analyze"):         # systemd's own reading of the two files
        r = sh("systemd-analyze", "verify", str(box.units / "remitwatch-deploy.timer"), check=False)
        assert "Failed to parse" not in r.stderr and "Unknown key" not in r.stderr and "Unknown section" not in r.stderr, r.stderr


def test_a_commit_cannot_change_what_runs_as_root(box):
    """The deploy script, its installer and the timer's files come from main, but what root runs is the installed copy:
    a commit that edits them is deployed like any other and runs nothing of its own until a person installs it."""
    script = (box.dev / "deploy" / "auto-deploy.sh").read_text()
    evil = script.replace("main() {\n", 'main() {\n  echo "ran" >> "$FAKE/script_from_commit"\n', 1)
    install = (box.dev / "deploy" / "install-auto-deploy.sh").read_text()
    evil_install = install.replace("mode=${1:-now}\n", 'mode=${1:-now}\necho "ran" >> "$FAKE/installer_from_commit"\n', 1)
    assert evil != script and evil_install != install
    sha = box.commit("tidy the deploy scripts", {"deploy/auto-deploy.sh": evil, "deploy/install-auto-deploy.sh": evil_install,
                                                  "deploy/systemd/remitwatch-deploy.timer":
                                                      (box.dev / "deploy" / "systemd" / "remitwatch-deploy.timer").read_text() + "\n"})
    assert box.run().returncode == 0 and box.marker == sha
    assert "install-auto-deploy.sh --update" in box.last()[4]                 # the log says a person must install it
    box.commit("an unrelated change", {"remitwatch/api.py": "x = 2\n"})
    assert box.run().returncode == 0
    assert not (box.fake / "installer_from_commit").exists() and not (box.fake / "script_from_commit").exists()
    assert (box.lib / "auto-deploy.sh").read_text() == script and "Script:" in box.run("--status").stdout
    assert "is different" in box.run("--status").stdout
    # a person reads it and installs it: from then on that version runs
    box.install("--update")
    assert (box.fake / "installer_from_commit").exists() and (box.lib / "auto-deploy.sh").read_text() == evil
    box.commit("one more", {"remitwatch/api.py": "x = 3\n"})
    box.run()
    assert (box.fake / "script_from_commit").exists()


def test_the_installer_keeps_the_running_copy_when_the_new_script_does_not_parse(box):
    good = (box.lib / "auto-deploy.sh").read_text()
    broken = good.replace("main() {", "main() { if [ ; then", 1)
    box.commit("refactor the checker", {"deploy/auto-deploy.sh": broken, "remitwatch/api.py": "x = 10\n"})
    assert box.run().returncode == 0                                           # deployed by the copy that runs
    r = box.install("--update", check=False)
    assert r.returncode == 1 and "does not parse" in r.stdout and (box.lib / "auto-deploy.sh").read_text() == good
    fix = box.commit("fix the checker", {"deploy/auto-deploy.sh": good, "remitwatch/api.py": "x = 11\n"})
    assert box.run().returncode == 0 and box.marker == fix                     # the fix still deploys


def test_an_older_deploy_script_hands_over_to_the_copy_by_itself(box):
    """An older checker ran the commit's install-auto-deploy.sh --units-only whenever deploy/systemd/ changed: that name
    still works, and installs the copy and the units that point at it."""
    for f in ("remitwatch-deploy.service",):
        (box.units / f).write_text((box.units / f).read_text().replace(f"{box.lib}/auto-deploy.sh", f"{box.dir}/deploy/auto-deploy.sh"))
    (box.lib / "auto-deploy.sh").unlink()
    box.install("--units-only")
    assert f"ExecStart=/bin/bash {box.lib}/auto-deploy.sh" in (box.units / "remitwatch-deploy.service").read_text()
    assert (box.lib / "auto-deploy.sh").exists() and "enable --now" not in box.read("systemctl").splitlines()[-1]


def test_nothing_new_on_main_does_nothing(box):
    assert box.run().returncode == 0
    assert box.calls() == [] and box.log_lines() == []


def test_a_new_commit_is_built_first_and_swapped_in_only_then(box):
    new = box.commit("code", {"remitwatch/api.py": "x = 2\n"})
    r = box.run()
    assert r.returncode == 0, r.stdout + r.stderr
    calls = box.calls()
    i = {c: n for n, c in enumerate(calls)}
    assert i["compose pull --quiet caddy"] < i["compose build --pull"] < i["compose up -d"]            # build first, swap second
    validate = next(n for n, c in enumerate(calls) if "caddy validate" in c)
    assert i["compose build --pull"] < validate < i["compose up -d"]                                   # Caddy checks its file before
    assert box.read("built").split() == [new] and box.running == new
    assert box.marker == new and box.head == new
    assert "image prune -f" in calls and calls.index("image prune -f") > i["compose up -d"]            # old images go, after the check
    assert "localhost:443:127.0.0.1" in box.read("curl")                                              # checked through Caddy
    (line,) = box.log_lines()
    t, frm, to, result, detail = line.split("\t")
    assert t.endswith("Z") and frm == f"from={box.first[:12]}" and to == f"to={new[:12]}" and result == "deployed" and "1 file(s) changed" in detail
    assert "compose restart caddy" not in calls                                    # Caddy's file did not change
    assert box.run().returncode == 0 and len(box.log_lines()) == 1                  # and the next tick has nothing to do


def test_caddys_image_and_the_base_image_are_refreshed_and_the_old_ones_pruned(box):
    box.write("caddy_update", "caddy-2\n")
    box.commit("code", {"remitwatch/api.py": "x = 2\n"})
    assert box.run().returncode == 0
    assert box.read("ctr_caddy").split()[1] == "caddy-2" and box.last()[3] == "deployed"
    # the registry cannot be reached: the build falls back to the images already here, and says so
    box.write("fail_pull_base", "")
    new = box.commit("more", {"remitwatch/api.py": "x = 3\n"})
    assert box.run().returncode == 0 and box.running == new and "base image could not be refreshed" in box.last()[4]


def test_a_failed_build_leaves_the_running_site_alone_and_is_not_retried_in_a_loop(box):
    box.write("fail_build", "")
    box.write("caddy_update", "caddy-2\n")                  # pulled before the build: put back with it
    bad = box.commit("broken", {"remitwatch/api.py": "x = (\n"})
    r = box.run()
    assert r.returncode == 1
    assert "compose up -d" not in box.calls() and box.running == box.first                 # the running containers were not touched
    assert box.marker == box.first and box.head == box.first                              # the files are put back too
    assert box.read("tag_caddy").strip() == "caddy-1" and box.read("tag_app").strip() == f"app-{box.first}"   # and the image names
    assert box.last()[3] == "build-failed" and "left as it was" in box.log_lines()[-1]
    out = sorted((box.state / "builds").iterdir())
    assert len(out) == 1 and "build broke" in out[0].read_text()
    # the next ticks try again, then hourly after three failures
    box.run()
    box.run()
    tries = lambda: box.calls().count("compose build --pull")       # noqa: E731
    assert len(box.log_lines()) == 3 and tries() == 3
    r = box.run()
    assert "tried again an hour after" in r.stdout and tries() == 3 and len(box.log_lines()) == 3
    sha, count, when, *_ = (box.state / "failed").read_text().split()
    assert sha == bad and count == "3"
    (box.state / "failed").write_text(f"{sha} 3 {int(when) - 3601}\n")                  # an hour later
    box.run()
    assert tries() == 4
    # a fix on main deploys at once
    (box.fake / "fail_build").unlink()
    fixed = box.commit("fix", {"remitwatch/api.py": "x = 3\n"})
    assert box.run().returncode == 0 and box.marker == fixed and not (box.state / "failed").exists()


def test_docs_only_changes_are_checked_out_without_a_rebuild(box):
    new = box.commit("docs", {"docs/SERVER_RUNBOOK.md": "runbook 2\n", "README.md": "readme 2\n"})
    assert box.run().returncode == 0
    assert box.calls() == [] and box.marker == new and box.head == new
    (line,) = box.log_lines()
    assert line.split("\t")[3] == "docs-only" and "2 docs file(s) changed" in line
    # a change beside docs rebuilds
    code = box.commit("both", {"docs/x.md": "x\n", "Dockerfile": "FROM scratch\n"})
    box.run()
    assert "compose build --pull" in box.calls() and box.marker == code


def test_a_change_to_caddys_file_restarts_caddy_with_it(box):
    box.commit("caddy", {"deploy/Caddyfile": "example.org {\n}\n"})
    box.run()
    calls = box.calls()
    assert calls.index("compose up -d") < calls.index("compose restart caddy")
    assert box.read("caddy_loaded") == "example.org {\n}\n" and box.last()[3] == "deployed"


def test_a_caddy_file_caddy_cannot_load_never_reaches_caddy(box):
    """Checked by Caddy itself (`caddy validate`, with the image about to run) before the swap: refused like a failed
    build, with the files put back, so Caddy, the only thing listening on 80 and 443, never gets it."""
    before = box.read("caddy_loaded")
    bad = box.commit("caddy typo", {"deploy/Caddyfile": "{$SITE_ADDRESS} {\n\treverse_proxy app:8000 INVALID\n"})
    r = box.run()
    assert r.returncode == 1 and box.last()[3] == "caddy-refused" and "Caddy cannot load" in box.last()[4]
    assert "compose up -d" not in box.calls() and "compose restart caddy" not in box.calls()
    assert box.marker == box.first and box.head == box.first and box.read("caddy_loaded") == before
    sha, count, *_ = (box.state / "failed").read_text().split()
    assert sha == bad and count == "1"                                        # counted like a failed build


def test_a_site_that_does_not_answer_through_caddy_is_rolled_back_to_the_images_that_ran(box):
    """Caddy loads its new file but no longer passes requests to the app: the app's own check passes, the check through
    Caddy does not. The images and files that ran before come back (not rebuilt), Caddy is restarted on the old file, and
    the commit is held, never recorded as deployed."""
    before = box.read("caddy_loaded")
    bad = box.commit("caddy and app", {"deploy/Caddyfile": "{$SITE_ADDRESS} {\n\trespond NOPROXY\n}\n", "remitwatch/api.py": "x = 3\n"})
    r = box.run()
    assert r.returncode == 1 and box.last()[3] == "unhealthy" and "through Caddy" in box.last()[4] and "rolled back to" in box.last()[4]
    assert box.read("built").split() == [bad]                                 # the previous commit is not rebuilt
    assert box.running == box.first and box.read("tag_app").strip() == f"app-{box.first}"
    assert box.marker == box.first and box.head == box.first
    calls = box.calls()
    assert calls[-1] != "compose restart caddy" and calls.count("compose restart caddy") == 2
    assert box.read("caddy_loaded") == before                                  # Caddy runs the old file again
    assert "answers again" in box.last()[4]


def test_a_site_that_does_not_answer_is_rolled_back_and_held(box):
    box.write("unhealthy", "")
    bad = box.commit("starts but does not answer", {"remitwatch/api.py": "x = 6\n"})
    r = box.run()
    assert r.returncode == 1
    assert box.read("built").split() == [bad]                                 # the new one only: the old image is put back
    assert box.running == box.first and box.marker == box.first and box.head == box.first
    assert box.last()[3] == "unhealthy" and "rolled back to" in box.log_lines()[-1] and "still does not answer" in box.log_lines()[-1]
    (box.fake / "unhealthy").unlink()
    r = box.run()
    assert "not deployed again by itself" in r.stdout and box.read("built").split() == [bad]
    assert box.run("--force").returncode == 0 and box.marker == bad and box.running == bad     # a person can still send it


def test_a_forced_try_that_fails_keeps_the_commit_held(box):
    box.write("unhealthy", "")
    box.commit("starts but does not answer", {"remitwatch/api.py": "x = 6\n"})
    box.run()
    (box.fake / "unhealthy").unlink()
    box.write("fail_build", "")                              # someone forces it while the build cannot work
    assert box.run("--force").returncode == 1
    (box.fake / "fail_build").unlink()
    assert (box.state / "failed").read_text().split()[3] == "hold"
    n = box.calls().count("compose up -d")
    r = box.run()                                            # an ordinary tick, nobody asked
    assert "not deployed again by itself" in r.stdout and box.calls().count("compose up -d") == n


def test_an_app_stopped_on_purpose_stays_stopped(box):
    """The restore and the cutover stop the app; a deploy would start it again."""
    box.write("app_stopped", "")
    new = box.commit("code", {"remitwatch/api.py": "x = 42\n"})
    r = box.run()
    assert r.returncode == 0 and "app is stopped" in r.stdout
    assert box.calls() == [] and box.log_lines() == [] and box.marker == box.first
    (box.fake / "app_stopped").unlink()
    assert box.run().returncode == 0 and box.marker == new


def test_files_changed_by_hand_on_the_server_are_never_overwritten_and_logged_once(box):
    (box.dir / "deploy" / "Caddyfile").write_text("hand edit\n")
    box.commit("code", {"remitwatch/api.py": "x = 7\n"})
    assert box.run().returncode == 1
    assert box.calls() == [] and (box.dir / "deploy" / "Caddyfile").read_text() == "hand edit\n"
    assert box.last()[3] == "refused" and "changed by hand" in box.last()[4] and "deploy/Caddyfile" in box.last()[4]
    for _ in range(4):
        r = box.run()
    assert len(box.log_lines()) == 1 and "waits" in r.stdout and "build" not in r.stdout     # not a failed build
    assert not (box.state / "failed").exists() and "Waiting:" in box.run("--status").stdout
    sh("git", "checkout", "--", ".", cwd=box.dir)                             # put back: deployed by itself
    assert box.run().returncode == 0 and box.last()[3] == "deployed"


def test_a_commit_that_would_overwrite_a_file_git_does_not_track_here_is_refused(box):
    """A local override put on the server by hand, or .env itself: `git checkout -f` would replace either."""
    (box.dir / "docker-compose.override.yml").write_text("# put here by hand on the server\n")
    box.commit("add an override", {"docker-compose.override.yml": "services: {app: {privileged: true}}\n"})
    assert box.run().returncode == 1 and box.last()[3] == "refused" and "docker-compose.override.yml" in box.last()[4]
    assert (box.dir / "docker-compose.override.yml").read_text() == "# put here by hand on the server\n" and box.calls() == []
    (box.dir / "docker-compose.override.yml").unlink()
    (box.dir / ".env").write_text("SECRET='kept'\n")
    sh("git", "-C", str(box.dev), "add", "-f", ".env", env=box.env)          # a commit that carries an .env
    (box.dev / ".env").write_text("SECRET='from the commit'\n")
    sh("git", "add", "-f", ".env", cwd=box.dev, env=box.env)
    box.commit("oops, an env file")
    assert box.run().returncode == 1 and ".env" in box.last()[4] and (box.dir / ".env").read_text() == "SECRET='kept'\n"


def test_no_swap_while_a_cycle_or_a_fax_run_is_going(box):
    # going when the check starts: nothing is built, nothing logged
    box.write("busy", "fax:lock\n")
    box.commit("one", {"remitwatch/api.py": "x = 4\n"})
    r = box.run()
    assert r.returncode == 0 and "fax:lock" in r.stdout and box.calls().count("compose build --pull") == 0 and box.log_lines() == []
    # it ends while the deploy waits after its build: then the swap
    box.write("busy_left", "2")                              # the check before the build, then the wait sees it end
    box.env["REMITWATCH_DEPLOY_IDLE_WAIT"] = "60"
    one = box.marker
    box.run()
    assert box.last()[3] == "deployed" and box.marker != one
    # one that starts during the build and is still going after the wait: nothing is swapped, all is put back
    box.write("busy_left", "1")
    box.write("busy", "pipeline:lock\n")
    (box.fake / "busy_left").write_text("1")
    box.env["REMITWATCH_DEPLOY_IDLE_WAIT"] = "0"
    before = box.marker
    calls = len(box.calls())
    box.write("busy_left", "99")
    box.commit("two", {"remitwatch/api.py": "x = 5\n"})
    (box.fake / "busy").unlink()                             # free at the check before the build...
    os.environ_backup = None
    script = box.lib / "auto-deploy.sh"
    hooked = script.read_text().replace('BLOG="$STATE/builds/', 'printf "pipeline:lock\\n" > "$FAKE/busy"; BLOG="$STATE/builds/', 1)
    script.write_text(hooked)                                # ...and going from the build on
    r = box.run()
    assert box.last()[3] == "waiting" and "pipeline:lock" in box.last()[4] and box.marker == before and box.head == before
    assert "compose up -d" not in box.calls()[calls:] and box.running == before


def test_the_lock_of_a_run_that_ended_is_cleared_after_the_swap(box):
    box.write("stale", "pipeline:lock\n")
    box.commit("code", {"remitwatch/api.py": "x = 4\n"})
    box.run()
    assert box.read("cleared").split() == ["pipeline:lock"] and "was cleared" in box.last()[4]


def test_one_deploy_at_a_time(box):
    box.commit("code", {"remitwatch/api.py": "x = 8\n"})
    box.state.mkdir(exist_ok=True)
    with open(box.state / "lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        r = box.run()
    assert r.returncode == 0 and "is running" in r.stdout and box.calls() == []


def test_force_and_status(box):
    assert box.run("--force").returncode == 0 and box.read("calls").count("compose build --pull") == 1
    st = box.run("--status").stdout
    assert f"Running:  {box.first}" in st and "Last deploys" in st and "deployed" in st and "the same as the checkout's" in st


def test_switching_it_on_by_hand_deploys_mains_newest_now_and_off_again(tmp_path):
    b = Box(tmp_path)
    b.boot()
    new = b.commit("newer than the checkout", {"remitwatch/api.py": "x = 9\n"})
    r = b.install()
    assert "Auto-deploy is on" in r.stdout and b.marker == new and b.running == new
    assert b.read("systemctl").splitlines()[-1] == "enable --now remitwatch-deploy.timer"
    r = b.install("--off")
    assert "Auto-deploy is off" in r.stdout and b.read("systemctl").splitlines()[-1] == "disable --now remitwatch-deploy.timer"
    assert b.install("--bogus", check=False).returncode == 2


def test_the_documented_switch_on_command_keeps_a_way_back(tmp_path):
    """The runbook's command notes the commit running before it pulls, so a newest commit that does not answer goes back
    to the files and images that ran."""
    b = Box(tmp_path)
    b.boot()
    runbook = open(os.path.join(ROOT, "docs", "SERVER_RUNBOOK.md")).read()
    cmd = re.search(r"sudo bash -c '(cd /opt/remitwatch && mkdir -p /var/lib/remitwatch-deploy && git rev-parse HEAD > "
                    r"/var/lib/remitwatch-deploy/deployed && git pull --ff-only && bash deploy/install-auto-deploy.sh)'", runbook)
    assert cmd, "the switch-on command in docs/SERVER_RUNBOOK.md"
    b.write("unhealthy", "")
    b.commit("newest on main, does not answer", {"remitwatch/api.py": "x = 99\n"})
    command = cmd.group(1).replace("/opt/remitwatch", str(b.dir)).replace("/var/lib/remitwatch-deploy", str(b.state))
    r = sh("bash", "-c", command, env=b.env, check=False)
    assert r.returncode == 1 and b.last()[3] == "unhealthy" and "rolled back to " + b.first[:12] in b.last()[4]
    assert b.running == b.first and b.head == b.first and b.marker == b.first


def test_configure_sh_offers_it_and_holds_the_lock_while_it_runs(tmp_path):
    b = Box(tmp_path)
    b.boot()
    shutil.copytree(os.path.join(ROOT, "deploy", "aws"), b.dir / "deploy" / "aws")
    (b.dir / ".env").write_text("SITE_ADDRESS='remitwatch.example.com'\n")
    r = sh("bash", str(b.dir / "deploy" / "aws" / "configure.sh"), env=b.env, input="\n" * 80, check=False)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert "Auto-deploy is on" in r.stdout and b.marker == b.first and "enable --now remitwatch-deploy.timer" in b.read("systemctl")
    assert (b.lib / "auto-deploy.sh").exists()
    # run again: it is on already, and the record of what runs follows the fresh build
    r = sh("bash", str(b.dir / "deploy" / "aws" / "configure.sh"), env=b.env, input="\n" * 80, check=False)
    assert r.returncode == 0 and "On: each new commit on main is deployed by itself" in r.stdout


def test_the_first_boot_of_a_rebuilt_server_switches_it_on():
    text = open(os.path.join(ROOT, "deploy", "aws", "remitwatch.yaml")).read()
    boot = text[text.index("UserData:"):text.index("  Server:")]
    assert "bash deploy/install-auto-deploy.sh --mark-current" in boot
    assert boot.index("docker compose up -d --build") < boot.index("install-auto-deploy.sh --mark-current")
    assert "util-linux" in boot                                            # flock
    line = next(ln for ln in boot.splitlines() if "--mark-current" in ln)
    assert "${" not in line                                                 # nothing CloudFormation would substitute


# ---- the locks, read in the app's container (the python the deploy runs there), against a real store and processes ----

def _locks_py():
    text = open(os.path.join(ROOT, "deploy", "auto-deploy.sh")).read()
    return re.search(r"read -r -d '' LOCKS_PY <<'PY'\n(.*?)\nPY\n", text, re.S).group(1)


def test_a_lock_counts_while_its_holder_runs_and_is_cleared_once_it_is_gone(tmp_path):
    db = str(tmp_path / "remitwatch.sqlite")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "remitwatch-cycle-stand-in"])
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])          # not a remitwatch process
    gone = subprocess.Popen([sys.executable, "-c", "pass", "remitwatch"])
    gone.wait()
    try:
        time.sleep(0.2)
        now = datetime.now(timezone.utc)
        iso = lambda d: d.isoformat(timespec="seconds")         # noqa: E731
        rows = {
            "pipeline:lock": {"pid": holder.pid, "started": iso(now), "heartbeat": iso(now), "token": "a"},       # going
            "fax:lock": {"pid": gone.pid, "started": iso(now), "token": "b"},                                     # its run ended
            "old:lock": {"pid": holder.pid, "started": iso(now - timedelta(hours=1))},  # the number, later given to another
            "other:lock": {"pid": other.pid, "started": iso(now)},                                                # not ours
            "manual:lock": {"heartbeat": iso(now)},                                                               # no pid, fresh
            "olden:lock": {"heartbeat": iso(now - timedelta(hours=2))},                                           # no pid, old
            "pipeline:last_cycle": {"pid": holder.pid, "started": iso(now)},                                      # not a lock
        }
        for k, v in rows.items():
            c.execute("INSERT INTO meta VALUES (?, ?)", (k, json.dumps(v)))
        c.commit()
        env = dict(os.environ, REMITWATCH_DB=db)
        busy = sh(sys.executable, "-c", _locks_py(), "busy", env=env).stdout.split()
        assert sorted(busy) == ["manual:lock", "pipeline:lock"]
        cleared = sh(sys.executable, "-c", _locks_py(), "clear", env=env).stdout.split()
        assert sorted(cleared) == ["fax:lock", "old:lock", "other:lock"]
        left = sorted(k for (k,) in c.execute("SELECT key FROM meta"))
        assert left == ["manual:lock", "olden:lock", "pipeline:last_cycle", "pipeline:lock"]
        holder.kill()
        holder.wait()
        assert sh(sys.executable, "-c", _locks_py(), "clear", env=env).stdout.split() == ["pipeline:lock"]
        # no store yet (a new server): nothing, and no store is made
        assert sh(sys.executable, "-c", _locks_py(), "busy", env=dict(env, REMITWATCH_DB=str(tmp_path / "none.sqlite"))).stdout == ""
        assert not (tmp_path / "none.sqlite").exists()
    finally:
        for p in (holder, other):
            p.kill()
            p.wait()
