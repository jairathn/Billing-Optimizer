"""
Auto-deploy on the server (deploy/auto-deploy.sh, deploy/install-auto-deploy.sh, deploy/systemd/): the server follows
main by itself. Each test runs the real scripts against real git repositories (a bare "origin" and the server's
checkout), with a stand-in for docker that records what it was asked to do and can be made to fail, and a stand-in for
systemctl. Nothing here touches the machine's own docker, systemd or /var.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("git") and shutil.which("flock")), reason="needs bash, git and flock")

FAKE_DOCKER = r'''#!/bin/bash
# a stand-in for docker: records each call; FAKE/fail_build, FAKE/fail_up, FAKE/unhealthy make those steps fail
echo "$*" >> "$FAKE/calls"
case "$1 $2" in
  "compose build") git rev-parse HEAD >> "$FAKE/built"; [ -f "$FAKE/fail_build" ] && { echo "build broke"; exit 1; }; exit 0 ;;
  "compose up") [ -f "$FAKE/fail_up" ] && exit 1; git rev-parse HEAD > "$FAKE/running"; date +%s%N > "$FAKE/app_id"; exit 0 ;;
  "compose ps") cat "$FAKE/app_id" 2>/dev/null; exit 0 ;;
  "compose restart") exit 0 ;;
  "compose exec")
    code=$7
    case "$code" in
      *robots.txt*) [ -f "$FAKE/unhealthy" ] && exit 1; exit 0 ;;
      *DELETE*) [ "$(cat "$FAKE/lock_token" 2>/dev/null)" = "$8" ] && rm -f "$FAKE/lock_token"; echo "$8" >> "$FAKE/cleared"; exit 0 ;;
      *pipeline:lock*)
        if [ -f "$FAKE/lock_left" ]; then n=$(cat "$FAKE/lock_left"); if [ "$n" -le 0 ]; then rm -f "$FAKE/lock_token"; else echo $((n - 1)) > "$FAKE/lock_left"; fi; fi
        cat "$FAKE/lock_token" 2>/dev/null || echo ""; exit 0 ;;
    esac ;;
  "image prune") exit 0 ;;
esac
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
    """A bare origin, a developer's clone that pushes to it, and the server's checkout at /opt/remitwatch's place."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.origin, self.dev, self.dir = tmp / "origin.git", tmp / "dev", tmp / "opt-remitwatch"
        self.state, self.log, self.fake, self.units = tmp / "state", tmp / "deploy.log", tmp / "fake", tmp / "units"
        for d in (self.fake, self.units, tmp / "bin"):
            d.mkdir()
        (tmp / "bin" / "docker").write_text(FAKE_DOCKER)
        (tmp / "bin" / "systemctl").write_text(FAKE_SYSTEMCTL)
        for f in ("docker", "systemctl"):
            (tmp / "bin" / f).chmod(0o755)
        self.env = dict(os.environ, PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", FAKE=str(self.fake), REMITWATCH_DIR=str(self.dir),
                        REMITWATCH_DEPLOY_STATE=str(self.state), REMITWATCH_DEPLOY_LOG=str(self.log), REMITWATCH_SYSTEMD_DIR=str(self.units),
                        REMITWATCH_DEPLOY_POLL="0", REMITWATCH_DEPLOY_HEALTH_WAIT="0", REMITWATCH_DEPLOY_IDLE_WAIT="0",
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
        return sh("bash", str(self.dir / "deploy" / "auto-deploy.sh"), *args, env=self.env, check=check)

    def install(self, *args, check=True):
        return sh("bash", str(self.dir / "deploy" / "install-auto-deploy.sh"), *args, env=self.env, check=check)

    def read(self, name, default=""):
        p = self.fake / name
        return p.read_text() if p.exists() else default

    @property
    def marker(self):
        p = self.state / "deployed"
        return p.read_text().strip() if p.exists() else ""

    @property
    def head(self):
        return sh("git", "rev-parse", "HEAD", cwd=self.dir).stdout.strip()

    def log_lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def calls(self):
        return self.read("calls").splitlines()


@pytest.fixture()
def box(tmp_path):
    b = Box(tmp_path)
    b.install("--mark-current")          # as at first boot: the image was just built from the checkout
    return b


def test_switching_it_on_installs_the_timer_pointed_at_this_checkout(box):
    service = (box.units / "remitwatch-deploy.service").read_text()
    timer = (box.units / "remitwatch-deploy.timer").read_text()
    assert f"ExecStart=/bin/bash {box.dir}/deploy/auto-deploy.sh" in service and f"WorkingDirectory={box.dir}" in service
    assert "User=root" in service and "Type=oneshot" in service and "Requires=docker.service" in service
    assert "OnCalendar=*:0/5" in timer and "WantedBy=timers.target" in timer
    assert box.read("systemctl").splitlines() == ["daemon-reload", "enable --now remitwatch-deploy.timer"]
    assert box.marker == box.first and (box.state / "branch").read_text().strip() == "main"
    assert oct(box.state.stat().st_mode & 0o777) == "0o700"
    if shutil.which("systemd-analyze"):         # systemd's own reading of the two files
        r = sh("systemd-analyze", "verify", str(box.units / "remitwatch-deploy.timer"), check=False)
        assert "Failed to parse" not in r.stderr and "Unknown key" not in r.stderr and "Unknown section" not in r.stderr, r.stderr


def test_nothing_new_on_main_does_nothing(box):
    assert box.run().returncode == 0
    assert box.calls() == [] and box.log_lines() == []


def test_a_new_commit_is_built_first_and_swapped_in_only_then(box):
    new = box.commit("code", {"remitwatch/api.py": "x = 2\n"})
    r = box.run()
    assert r.returncode == 0, r.stdout + r.stderr
    calls = box.calls()
    assert calls.index("compose build") < calls.index("compose up -d")             # build first, swap second
    assert box.read("built").split() == [new] and box.read("running").strip() == new
    assert box.marker == new and box.head == new
    (line,) = box.log_lines()
    t, frm, to, result, detail = line.split("\t")
    assert t.endswith("Z") and frm == f"from={box.first[:12]}" and to == f"to={new[:12]}" and result == "deployed" and "1 file(s) changed" in detail
    assert "compose restart caddy" not in calls                                    # Caddy's file did not change
    assert box.run().returncode == 0 and len(box.log_lines()) == 1                  # and the next tick has nothing to do


def test_a_failed_build_leaves_the_running_site_alone_and_is_not_retried_in_a_loop(box, monkeypatch):
    (box.fake / "fail_build").touch()
    bad = box.commit("broken", {"remitwatch/api.py": "x = (\n"})
    r = box.run()
    assert r.returncode == 1
    assert "compose up -d" not in box.calls() and not (box.fake / "running").exists()     # the running containers were not touched
    assert box.marker == box.first and box.head == box.first                              # the files are put back too
    assert box.log_lines()[-1].split("\t")[3] == "build-failed" and "left as it was" in box.log_lines()[-1]
    out = sorted((box.state / "builds").iterdir())
    assert len(out) == 1 and "build broke" in out[0].read_text()
    # the next ticks try again, then hourly after three failures
    box.run()
    box.run()
    assert len(box.log_lines()) == 3 and box.read("calls").count("compose build") == 3
    r = box.run()
    assert "tried again an hour after" in r.stdout and box.read("calls").count("compose build") == 3 and len(box.log_lines()) == 3
    sha, count, when, *_ = (box.state / "failed").read_text().split()
    assert sha == bad and count == "3"
    (box.state / "failed").write_text(f"{sha} 3 {int(when) - 3601}\n")                  # an hour later
    box.run()
    assert box.read("calls").count("compose build") == 4
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
    assert "compose build" in box.calls() and box.marker == code


def test_a_change_to_caddys_file_restarts_caddy(box):
    box.commit("caddy", {"deploy/Caddyfile": "example.org {\n}\n"})
    box.run()
    calls = box.calls()
    assert calls.index("compose up -d") < calls.index("compose restart caddy")


def test_a_running_data_cycle_is_waited_for_and_one_ended_by_the_swap_does_not_block_the_next(box):
    # a cycle that finishes while the deploy waits: nothing to clear
    (box.fake / "lock_token").write_text("tok1")
    (box.fake / "lock_left").write_text("2")
    box.env["REMITWATCH_DEPLOY_IDLE_WAIT"] = "60"
    box.commit("one", {"remitwatch/api.py": "x = 4\n"})
    box.run()
    assert box.read("cleared") == "" and box.log_lines()[-1].split("\t")[3] == "deployed"
    # one still running when the wait is over: the swap ends it, and its lock goes with it
    (box.fake / "lock_left").unlink()
    (box.fake / "lock_token").write_text("tok2")
    box.env["REMITWATCH_DEPLOY_IDLE_WAIT"] = "0"
    box.commit("two", {"remitwatch/api.py": "x = 5\n"})
    box.run()
    assert box.read("cleared").split() == ["tok2"] and not (box.fake / "lock_token").exists()
    assert "was ended by the swap" in box.log_lines()[-1]


def test_a_site_that_does_not_answer_is_rolled_back_and_held(box):
    (box.fake / "unhealthy").touch()
    bad = box.commit("starts but does not answer", {"remitwatch/api.py": "x = 6\n"})
    r = box.run()
    assert r.returncode == 1
    assert box.read("built").split() == [bad, box.first]                         # the new one, then the previous one again
    assert box.read("running").strip() == box.first and box.marker == box.first and box.head == box.first
    assert box.log_lines()[-1].split("\t")[3] == "unhealthy" and "rolled back to" in box.log_lines()[-1]
    (box.fake / "unhealthy").unlink()
    r = box.run()
    assert "not deployed again by itself" in r.stdout and box.read("built").split() == [bad, box.first]
    assert box.run("--force").returncode == 0 and box.marker == bad                 # a person can still send it


def test_files_changed_by_hand_on_the_server_are_never_overwritten(box):
    (box.dir / "deploy" / "Caddyfile").write_text("hand edit\n")
    box.commit("code", {"remitwatch/api.py": "x = 7\n"})
    assert box.run().returncode == 1
    assert box.calls() == [] and (box.dir / "deploy" / "Caddyfile").read_text() == "hand edit\n"
    assert box.log_lines()[-1].split("\t")[3] == "refused" and "changed by hand" in box.log_lines()[-1]


def test_one_deploy_at_a_time(box):
    box.commit("code", {"remitwatch/api.py": "x = 8\n"})
    box.state.mkdir(exist_ok=True)
    with open(box.state / "lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        r = box.run()
    assert r.returncode == 0 and "is running" in r.stdout and box.calls() == []


def test_force_and_status(box):
    assert box.run("--force").returncode == 0 and box.read("calls").count("compose build") == 1
    st = box.run("--status").stdout
    assert f"Running:  {box.first}" in st and "Last deploys" in st and "deployed" in st


def test_switching_it_on_by_hand_deploys_mains_newest_now_and_off_again(tmp_path):
    b = Box(tmp_path)
    new = b.commit("newer than the checkout", {"remitwatch/api.py": "x = 9\n"})
    r = b.install()
    assert "Auto-deploy is on" in r.stdout and b.marker == new and b.read("running").strip() == new
    assert b.read("systemctl").splitlines()[-1] == "enable --now remitwatch-deploy.timer"
    r = b.install("--off")
    assert "Auto-deploy is off" in r.stdout and b.read("systemctl").splitlines()[-1] == "disable --now remitwatch-deploy.timer"
    assert b.install("--bogus", check=False).returncode == 2


def test_configure_sh_offers_it_and_holds_the_lock_while_it_runs(tmp_path):
    b = Box(tmp_path)
    shutil.copytree(os.path.join(ROOT, "deploy", "aws"), b.dir / "deploy" / "aws")
    (b.dir / ".env").write_text("SITE_ADDRESS='remitwatch.example.com'\n")
    r = sh("bash", str(b.dir / "deploy" / "aws" / "configure.sh"), env=b.env, input="\n" * 60, check=False)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert "Auto-deploy is on" in r.stdout and b.marker == b.first and "enable --now remitwatch-deploy.timer" in b.read("systemctl")
    # run again: it is on already, and the record of what runs follows the fresh build
    r = sh("bash", str(b.dir / "deploy" / "aws" / "configure.sh"), env=b.env, input="\n" * 60, check=False)
    assert r.returncode == 0 and "On: each new commit on main is deployed by itself" in r.stdout


def test_the_first_boot_of_a_rebuilt_server_switches_it_on():
    text = open(os.path.join(ROOT, "deploy", "aws", "remitwatch.yaml")).read()
    boot = text[text.index("UserData:"):text.index("  Server:")]
    assert "bash deploy/install-auto-deploy.sh --mark-current" in boot
    assert boot.index("docker compose up -d --build") < boot.index("install-auto-deploy.sh --mark-current")
    assert "util-linux" in boot                                            # flock
    line = next(ln for ln in boot.splitlines() if "--mark-current" in ln)
    assert "${" not in line                                                 # nothing CloudFormation would substitute
