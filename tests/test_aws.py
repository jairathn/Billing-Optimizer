"""Moving to a server of the practice's own (deploy/aws): the one-time move of the Vercel site's data, the fax line looked
after by the server's own runs, the settings script, and the AWS template."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest

from tests.test_cloud import box  # noqa: F401  (the fixture: a fake Dropbox holding the Vercel site's state folder)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_vercel_sites_data_moves_to_the_server_once(box, tmp_path, monkeypatch, capsys):
    from remitwatch import api, cli, cloud
    from remitwatch.notes import get_packet
    from tests.test_cloud import DATA, cycle, visits_with_downcodes, write_remits
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))       # the same folder, read as files
    served = cloud.ServingCopy().connection()
    fid = served.execute("SELECT finding_id FROM findings WHERE signal='DOWNCODE_LEVEL_REDUCED' ORDER BY finding_id").fetchone()[0]
    served.close()
    # work recorded on the Vercel site since its last run (still in the journal), a packet built there, the owner's
    # Practice figures, the record of which alert was mailed, and a saved weekly summary
    cloud.record_action(state, {"kind": "status", "finding_id": fid, "status": "appealed", "notes": "faxed by hand", "actor": "biller"})
    cloud.record_action(state, {"kind": "payer_fax", "payer_group": "Anthem / BCBS", "purpose": "appeal", "number": "8005550142", "actor": "biller"})
    cloud.record_action(state, {"kind": "lesson", "record": {"lesson_key": "L1", "code": "99214", "gaps": ["no second problem"], "tips": []}})
    pid = cloud.save_packet(state, {"packet_id": "abc123", "finding_id": fid, "created_at": "2026-11-20T10:00:00+00:00", "mode": "ai",
                                    "subject": "Request for reconsideration", "letter_full": "Dear payer, SAMPLE PAT", "letter_redacted": "Dear payer, S.P.",
                                    "analysis": {"recommendation": "appeal"}, "evidence": "835", "attachments": ["note"]}, b"%PDF-1.4 packet")
    state.put(api.ASSUMPTIONS, b'{"scenarios":{"base":{"days":4}},"saved_at":"2026-11-19T10:00:00+00:00"}')
    raw = cloud.load_raw(state)[0].conn
    alert = raw.execute("SELECT alert_id, first_seen FROM alerts ORDER BY alert_id LIMIT 1").fetchone()
    state.put(cloud.MARKS, json.dumps({"alerts": {alert[0]: ["2026-11-18T09:00:00+00:00", alert[1]]} if alert else {},
                                       "last_report": "2026-11-17T08:00:00+00:00"}).encode())
    state.put("reports/remitwatch-20261117-0800.md", b"# Weekly summary")
    db = str(tmp_path / "server" / "remitwatch.sqlite")
    assert cli.main(["--db", db, "import-cloud"]) == 0
    out = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert out["journal_applied"] == 3 and out["packets_moved"] == 1 and out["reports_moved"] >= 1 and out["practice_figures"] is True
    assert out["remits"] == 6 and out["remittance_files"] == 6 and out["findings"] >= 3 and out["payer_fax_numbers"] == 1 and out["lessons"] == 1
    assert out["alerts_marked_mailed"] == (1 if alert else 0) and out["last_report"] == "2026-11-17T08:00:00+00:00"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT status, notes FROM findings WHERE finding_id=?", (fid,)).fetchone() == ("appealed", "faxed by hand")
    assert conn.execute("SELECT number FROM payer_fax").fetchone()[0] == "8005550142"
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    # where the Vercel site had got to in the Dropbox folder: the server carries on from there, no file fetched again
    assert meta.get("sync:cursor") and meta["pipeline:last_report"] == "2026-11-17T08:00:00+00:00"
    assert json.loads(meta[api.ASSUMPTIONS_KEY])["scenarios"]["base"]["days"] == 4
    if alert:
        assert conn.execute("SELECT notified_at FROM alerts WHERE alert_id=?", (alert[0],)).fetchone()[0] == "2026-11-18T09:00:00+00:00"
    # the server's schema: the indexes the serverless copy leaves out are back, and the dashboard's own tables
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0] > 10
    assert {"hide_rules", "faxes", "appeal_packets"} <= {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert open(os.path.join(tmp_path, "server", "reports", "remitwatch-20261117-0800.md")).read() == "# Weekly summary"
    conn = sqlite3.connect(db)
    p = get_packet(conn, pid, with_pdf=True, reveal=True)
    assert p["pdf"] == b"%PDF-1.4 packet" and p["letter"] == "Dear payer, SAMPLE PAT" and p["analysis"]["recommendation"] == "appeal"
    assert get_packet(conn, pid)["letter"] == "Dear payer, S.P."
    conn.close()
    # proven: the Vercel site's state and the server's store are the same, table by table
    assert cli.main(["--db", db, "compare-cloud"]) == 0
    report = capsys.readouterr().out
    assert "DIFFERENT" not in report and "findings (every row)" in report and json.loads(report[report.index("{"):])["different"] == 0
    # and the proof is real: one decision changed on the server is caught
    conn = sqlite3.connect(db)
    conn.execute("UPDATE findings SET status='dismissed' WHERE finding_id=?", (fid,))
    conn.commit()
    conn.close()
    assert cli.main(["--db", db, "compare-cloud"]) == 2
    assert "findings (every row)" in capsys.readouterr().out
    # a second move would overwrite what the server has done since: only with --force
    assert cli.main(["--db", db, "import-cloud"]) == 1
    assert "--force" in capsys.readouterr().out
    assert cli.main(["--db", db, "import-cloud", "--force"]) == 0
    capsys.readouterr()
    assert cli.main(["--db", db, "compare-cloud"]) == 0


def test_without_dropbox_settings_the_move_says_what_is_missing(tmp_path, monkeypatch, capsys):
    from remitwatch import cli
    for k in ("REMITWATCH_STATE_DIR", "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert cli.main(["--db", str(tmp_path / "s.sqlite"), "import-cloud"]) == 1
    assert "DROPBOX_REFRESH_TOKEN" in capsys.readouterr().out


def test_the_servers_own_runs_look_after_the_fax_line(tmp_path, monkeypatch, capsys):
    from remitwatch import cli, fax
    from tests.fake_fax import FakeLine
    from tests.test_fax import _store
    db, _npi = _store(tmp_path)
    line = FakeLine()
    monkeypatch.setattr(fax, "provider", lambda env=None: line)
    monkeypatch.setattr(fax, "configured", lambda env=None: True)
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "TRIZETTO_SFTP_USER", "SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)
    line.receive("in-1", b"%PDF-1.4 a payer's letter")
    monkeypatch.setattr(fax, "classify", lambda pdf, client=None: {"document_type": "other", "decision": "none", "claim_numbers": [],
                                                                    "summary": "A notice.", "service_dates": []})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert cli.main(["--db", db, "cycle", "--skip-sync", "--no-email", "--report-dir", str(tmp_path / "r")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["fax"]["refresh"]["received"] == 1 and out["fax"]["follow_up"]["sent"] == 0


FAKE_DOCKER = r"""#!/bin/bash
# a stand-in for docker: records each call (python given with -c is named by what it does) and the commit each build is
# given ($LOGDIR/build_commits: REMITWATCH_COMMIT, "<unset>" when none), answers whether the store holds data
# ($LOGDIR/store: data or empty), and fails the move when $LOGDIR/import_fails exists
args=()
for a in "$@"; do
  case "$a" in
    *"SELECT 1 FROM transactions"*) args+=("<store-state>") ;;
    *"DELETE FROM meta WHERE key LIKE"*) args+=("<clear-locks>") ;;
    *"SELECT key, value FROM meta"*) args+=("<run-locks>") ;;
    *"import password_days"*) args+=("<password-days>") ;;
    *$'\n'*) args+=("<code>") ;;
    *) args+=("$a") ;;
  esac
done
echo "${args[*]}" >> "$LOGDIR/docker.log"
case "${args[*]}" in "compose build"*) printf '%s\n' "${REMITWATCH_COMMIT-<unset>}" >> "$LOGDIR/build_commits" ;; esac
for ((i = 1; i <= $#; i++)); do              # python given with -c: the app's own code runs as it would in its image
  if [ "${!i}" = -c ]; then
    j=$((i + 1)); code=${!j}; shift "$j"
    case "$code" in *"remitwatch.insights import password_days"*) PYTHONPATH="$REPO" exec "$PY" -c "$code" "$@" ;; esac
    break
  fi
done
case "${args[*]}" in
  *"<store-state>"*) cat "$LOGDIR/store" 2>/dev/null ;;
  *import-cloud*) [ -f "$LOGDIR/import_fails" ] && { echo "Dropbox could not be reached."; exit 1; } ;;
esac
exit 0
"""

SECRET_PROMPT = re.compile(r"\s+([A-Z][A-Z0-9_]+), (?:set|not set|new)")
TEXT_PROMPT = re.compile(r"\s+([A-Z][A-Z0-9_]+) \[")


def _drive(script, cwd, env, answers, timeout=90):
    """Run a script in a terminal, as a person would: wait for each question, answer it by the setting's name (Enter when
    `answers` has none; a list answers the same question again; REPLACE answers whether to make a new dashboard password,
    MOVE and AUTODEPLOY the other yes-or-no questions), and type a secret only once the terminal has stopped showing what
    is typed, as it does for every secret. A script still waiting at `timeout` (a question nobody answers) is ended.
    Returns the exit code and everything the terminal showed."""
    import pty
    import select
    import signal
    import termios
    queues = {k: (list(v) if isinstance(v, list) else [v]) for k, v in answers.items()}
    pid, fd = pty.fork()
    if pid == 0:                                                    # the child: the script, in the terminal
        os.chdir(cwd)
        os.execvpe("bash", ["bash", script], env)
    screen, pending, status = b"", b"", None
    deadline = time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            screen += chunk
            pending += chunk
            continue
        text = pending.decode(errors="replace")
        last = text.rsplit("\n", 1)[-1]
        secret = False
        if text.endswith("It is not shown:\r\n"):
            name, secret = SECRET_PROMPT.match(text.rstrip("\r\n").rsplit("\n", 1)[-1]).group(1), True
        elif last.endswith(": ") and SECRET_PROMPT.match(last):
            name, secret = SECRET_PROMPT.match(last).group(1), True
        elif last.endswith("]: ") and TEXT_PROMPT.match(last):
            name = TEXT_PROMPT.match(last).group(1)
        elif last.endswith("[Y/n] "):
            name = "MOVE" if "Move the store" in last else "AUTODEPLOY"
        elif last.endswith("[y/N] "):
            name = "REPLACE"
        else:
            done, st = os.waitpid(pid, os.WNOHANG)
            if done:
                status = st
                break
            continue
        queue = queues.get(name)
        answer = queue.pop(0) if queue else ""
        if secret:
            until = time.time() + 3
            while termios.tcgetattr(fd)[3] & termios.ECHO and time.time() < until:
                time.sleep(0.01)
        os.write(fd, (answer + "\n").encode())
        pending = b""
    if status is None and time.time() >= deadline:                  # still waiting: end it, or reading on never returns
        os.kill(pid, signal.SIGKILL)
    while True:                                                     # what is left to show
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        screen += chunk
    os.close(fd)
    if status is None:
        _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), screen.decode(errors="replace")


def _configure_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "deploy" / "aws").mkdir(parents=True)
    for rel in ("deploy/aws/configure.sh", "deploy/auto-deploy.sh"):
        shutil.copy(os.path.join(HERE, rel), repo / rel)
    (repo / ".env").write_text("SITE_ADDRESS='remitwatch.example.com'\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(FAKE_DOCKER)
    (bindir / "systemctl").write_text(f'#!/bin/bash\necho "$*" >> "{tmp_path}/systemctl.log"\nexit 1\n')     # auto-deploy not on
    for f in ("docker", "systemctl"):
        (bindir / f).chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", REMITWATCH_DEPLOY_STATE=str(tmp_path / "deploy-state"),
               LOGDIR=str(tmp_path), TERM="dumb", REMITWATCH_DEPLOY_POLL="0", REPO=HERE, PY=sys.executable)
    return repo, env


def _env_values(repo):
    vals = {}
    for ln in (repo / ".env").read_text().splitlines():
        k, v = ln.split("=", 1)
        assert v.startswith("'") and v.endswith("'"), ln          # single quotes: nothing in a value is read as a variable
        vals[k] = v[1:-1]
    return vals


def test_the_settings_script_keeps_values_exactly_shows_no_secret_and_the_key_loads(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("no bash")
    from remitwatch import modmed as M
    repo, env = _configure_repo(tmp_path)
    (tmp_path / "store").write_text("empty\n")
    pem = M.generate_key()
    secrets = {"DROPBOX_APP_SECRET": "sec$ret#1 xQ7Z", "DROPBOX_REFRESH_TOKEN": "tok-PROBE-K2W8", "ANTHROPIC_API_KEY": "sk-PROBE-J4V6",
               "TRIZETTO_SFTP_PASSWORD": "Tz-PROBE-H3X5", "SMTP_PASS": "Mail-PROBE-G8N1", "ALERT_WEBHOOK_URL": "https://hooks.example.org/PROBE-F9M3"}
    answers = dict(secrets, DROPBOX_APP_KEY="key123", PRACTICE_NAME="Test Derm", PRACTICE_ADDRESS="1 Main St",
                   MODMED_PRIVATE_KEY=pem.strip(), TRIZETTO_SFTP_PASSWORD_DAYS="90 days (3 months)", TRIZETTO_SFTP_PORT="2222",
                   REMITWATCH_SHOW_PHI="1", MOVE="y", AUTODEPLOY="n")
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, answers)
    assert code == 0, screen[-2500:]
    vals = _env_values(repo)
    assert oct((repo / ".env").stat().st_mode & 0o777) == "0o600"
    assert vals["DROPBOX_APP_SECRET"] == "sec$ret#1 xQ7Z" and vals["DROPBOX_ROOT_PATH"] == "/Trizetto Data" and vals["REMITWATCH_USER"] == "oda"
    assert len(vals["REMITWATCH_PASSWORD"]) == 32 and len(vals["CRON_SECRET"]) == 32 and "SRFAX_ACCESS_ID" not in vals
    assert vals["TRIZETTO_SFTP_PASSWORD_DAYS"] == "90" and vals["TRIZETTO_SFTP_PORT"] == "2222" and vals["REMITWATCH_SHOW_PHI"] == "1"
    assert M.jwks(M.Settings.from_env({"MODMED_PRIVATE_KEY": vals["MODMED_PRIVATE_KEY"]}))["keys"][0] == M.public_jwk(M.load_private_key(pem))
    # nothing typed or pasted for a secret is on the screen, nor the scheduler's secret; the dashboard password once
    body = [ln for ln in pem.strip().splitlines() if "-----" not in ln]
    for v in list(secrets.values()) + body + [vals["CRON_SECRET"]]:
        assert v not in screen, v
    assert screen.count(vals["REMITWATCH_PASSWORD"]) == 1 and "shown this once" in screen
    calls = (tmp_path / "docker.log").read_text().splitlines()
    assert calls.index("compose build") < calls.index("compose stop app")             # built while the site still runs
    assert calls.index("compose exec -T app python -c <run-locks> busy") < calls.index("compose stop app")
    assert calls.index("compose stop app") < calls.index("compose run --rm --no-deps -T app python -c <clear-locks>")
    assert "compose run --rm --no-deps app python -m remitwatch.cli import-cloud" in calls and calls[-1] == "compose up -d"
    # run again on a server that has its own data now, pressing Enter everywhere: everything is kept, nothing is moved,
    # and no secret is hinted at
    before = (repo / ".env").read_text()
    (tmp_path / "store").write_text("data\n")
    (tmp_path / "docker.log").write_text("")
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, {})
    assert code == 0 and (repo / ".env").read_text() == before
    assert "Already moved" in screen and "Move the store" not in screen and "--force" not in screen
    calls = (tmp_path / "docker.log").read_text().splitlines()
    assert not any("import-cloud" in c for c in calls)
    assert "\u2026" not in screen                                          # the old hint: "set (\u2026" and the last four
    for name in secrets:
        assert f"{name}, set" in screen and vals[name][-4:] not in screen, name
    for name in ("REMITWATCH_PASSWORD", "CRON_SECRET", "MODMED_PRIVATE_KEY"):
        assert f"{name}, set" in screen and vals[name] not in screen, name


def test_the_move_is_offered_only_while_the_store_is_empty_and_never_points_at_force(tmp_path):
    repo, env = _configure_repo(tmp_path)
    (repo / ".env").write_text("SITE_ADDRESS='remitwatch.example.com'\nDROPBOX_REFRESH_TOKEN='tok'\n")
    (tmp_path / "store").write_text("empty\n")
    (tmp_path / "import_fails").write_text("")
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, {"MOVE": "", "AUTODEPLOY": "n"})
    assert code == 0 and "Move the store" in screen and "Not moved" in screen and "still empty" in screen
    assert "Run this script again to retry" not in screen and "--force" not in screen
    # an unreadable store: nothing is moved, and the advice is to look, not to force
    (tmp_path / "store").write_text("unreadable\n")
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, {})
    assert code == 0 and "could not be read" in screen and "--force" not in screen and "Move the store" not in screen


def test_passwords_the_site_would_refuse_are_asked_for_again(tmp_path):
    repo, env = _configure_repo(tmp_path)
    (tmp_path / "store").write_text("data\n")
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, {
        "REMITWATCH_PASSWORD": ["short1", "a-long-enough-pass-1"],
        "REMITWATCH_OWNER_PASSWORD": ["a-long-enough-pass-1", "another-long-pass-2"],
        "SMTP_PORT": ["five-eight-seven", "587"], "REMITWATCH_SHOW_PHI": ["yes", "1"], "AUTODEPLOY": "n"})
    assert code == 0, screen[-1500:]
    vals = _env_values(repo)
    assert vals["REMITWATCH_PASSWORD"] == "a-long-enough-pass-1" and vals["REMITWATCH_OWNER_PASSWORD"] == "another-long-pass-2"
    assert vals["SMTP_PORT"] == "587" and vals["REMITWATCH_SHOW_PHI"] == "1"
    for why in ("must be 12 characters or more", "must differ from the dashboard password", "must be a number", "must be 1 or 0"):
        assert why in screen, why
    assert "short1" not in screen and "a-long-enough-pass-1" not in screen


def test_a_short_dashboard_password_in_use_is_kept_with_a_warning(tmp_path):
    """The server still accepts a short dashboard password it already has (remitwatch.api.password_warning), so that an
    update never locks the staff out. configure.sh says it is easy to guess. Enter keeps it, since a new one changes
    every biller's login; a new one is made only when the person says yes, and one typed must still be 12 or more."""
    repo, env = _configure_repo(tmp_path)
    (tmp_path / "store").write_text("data\n")
    (repo / ".env").write_text("SITE_ADDRESS='remitwatch.example.com'\nREMITWATCH_PASSWORD='shortpw'\n")
    script = str(repo / "deploy" / "aws" / "configure.sh")
    for answer in ("", "n"):                                               # Enter, or no: kept as it is
        code, screen = _drive(script, str(repo), env, {"REPLACE": answer, "AUTODEPLOY": "n"})
        assert code == 0, screen[-1500:]
        assert "is shorter than 12 characters. The site still accepts it, but it is easy to guess." in screen
        assert "Make a new one now?" in screen and "Kept: everyone signs in with it as before." in screen
        assert "cannot be used" not in screen and "shown this once" not in screen and "shortpw" not in screen
        assert _env_values(repo)["REMITWATCH_PASSWORD"] == "shortpw"
    # yes: a short one typed is asked for again, and Enter then makes one, shown once
    code, screen = _drive(script, str(repo), env, {"REPLACE": "y", "REMITWATCH_PASSWORD": ["short2", ""], "AUTODEPLOY": "n"})
    assert code == 0, screen[-1500:]
    new = _env_values(repo)["REMITWATCH_PASSWORD"]
    assert "REMITWATCH_PASSWORD, new, Enter makes one" in screen
    assert "must be 12 characters or more. Type it again, or press Enter to have one made." in screen
    assert len(new) == 32 and screen.count(new) == 1 and "short2" not in screen
    assert "Broken pipe" not in screen                                     # making one leaves no noise on the screen
    # from then on there is nothing to say about it
    code, screen = _drive(script, str(repo), env, {"AUTODEPLOY": "n"})
    assert code == 0 and "Make a new one now?" not in screen and _env_values(repo)["REMITWATCH_PASSWORD"] == new


def test_a_dashboard_password_the_site_refuses_is_replaced(tmp_path):
    """One the site cannot accept at all (a character a browser cannot send in a login: remitwatch.api.password_problem)
    leaves the site serving nothing, so nobody signs in with it: configure.sh says so, and Enter makes a new one."""
    repo, env = _configure_repo(tmp_path)
    (tmp_path / "store").write_text("data\n")
    (repo / ".env").write_text("SITE_ADDRESS='remitwatch.example.com'\nREMITWATCH_PASSWORD='p\u00e4ssword-long-enough'\n", encoding="utf-8")
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, {"AUTODEPLOY": "n"})
    assert code == 0, screen[-1500:]
    assert "REMITWATCH_PASSWORD set now cannot be used" in screen and "Make a new one now?" not in screen
    new = _env_values(repo)["REMITWATCH_PASSWORD"]
    assert len(new) == 32 and new.isascii() and screen.count(new) == 1


def test_configure_sh_builds_the_site_with_the_commit_it_runs(tmp_path):
    """configure.sh gives its build the checkout's commit (REMITWATCH_COMMIT: the image keeps it, /api/health shows it),
    and none when files there were changed by hand, since no commit then says what runs."""
    repo, env = _configure_repo(tmp_path)
    (tmp_path / "store").write_text("data\n")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org"]
    for args in (["init", "-q"], ["add", "deploy"], ["commit", "-q", "-m", "the checkout"]):
        subprocess.run(git + args, check=True, capture_output=True)
    head = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    built = lambda: (tmp_path / "build_commits").read_text().splitlines()      # noqa: E731
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, {"AUTODEPLOY": "n"})
    assert code == 0 and built() == [head], screen[-1500:]                    # .env, which it wrote, is not git's
    with open(repo / "deploy" / "auto-deploy.sh", "a") as f:
        f.write("# changed by hand\n")
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, {"AUTODEPLOY": "n"})
    assert code == 0 and built() == [head, ""], screen[-1500:]


def test_a_password_lifetime_in_trizettos_words_is_read_by_the_apps_own_rules(tmp_path):
    """configure.sh stores a number of days, read by remitwatch.insights.password_days (the rule the data checks use),
    and asks again for words that rule cannot read instead of storing 0, which would turn the reminder off."""
    repo, env = _configure_repo(tmp_path)
    (tmp_path / "store").write_text("data\n")
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, {
        "TRIZETTO_SFTP_PASSWORD_DAYS": ["bi-annually", "365 days (1 year)"], "AUTODEPLOY": "n"})
    assert code == 0, screen[-1500:]
    assert "could not be read as a number of days" in screen and "noted as 365 days" in screen
    assert _env_values(repo)["TRIZETTO_SFTP_PASSWORD_DAYS"] == "365"
    code, screen = _drive(str(repo / "deploy" / "aws" / "configure.sh"), str(repo), env, {
        "TRIZETTO_SFTP_PASSWORD_DAYS": "six months", "AUTODEPLOY": "n"})
    assert code == 0 and _env_values(repo)["TRIZETTO_SFTP_PASSWORD_DAYS"] == "180"


def test_configure_sh_python_finds_data_and_clears_run_locks(tmp_path):
    """The python configure.sh runs in the app's image, against a real store."""
    text = open(os.path.join(HERE, "deploy", "aws", "configure.sh")).read()
    state = re.search(r"^STORE_STATE='(.*?)'$", text, re.S | re.M).group(1)
    clear = re.search(r"^CLEAR_LOCKS='(.*?)'$", text, re.S | re.M).group(1)
    db = tmp_path / "remitwatch.sqlite"
    env = dict(os.environ, REMITWATCH_DB=str(db))
    run = lambda code: subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30).stdout.strip()  # noqa: E731
    assert run(state) == "empty" and not db.exists()
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    c.commit()
    assert run(state) == "empty"                                  # a store with no remittances table yet
    c.execute("CREATE TABLE transactions (id INTEGER)")
    c.commit()
    assert run(state) == "empty"
    c.execute("INSERT INTO transactions VALUES (1)")
    c.executemany("INSERT INTO meta VALUES (?, ?)", [("pipeline:lock", "{}"), ("fax:lock", "{}"), ("sync:cursor", "x")])
    c.commit()
    assert run(state) == "data"
    run(clear)
    assert [k for (k,) in c.execute("SELECT key FROM meta")] == ["sync:cursor"]


def test_the_aws_template_passes_awss_own_checks():
    """AWS's own linter (cfn-lint, in requirements-dev.txt, so it runs in CI): the one installed beside this Python, as a
    virtual environment puts it, else the one on PATH."""
    pytest.importorskip("cfnlint")
    exe = shutil.which("cfn-lint", path=os.path.dirname(sys.executable)) or shutil.which("cfn-lint")
    cmd = [exe] if exe else [sys.executable, "-c", "import sys; from cfnlint.runner import main; sys.exit(main())"]
    r = subprocess.run(cmd + [os.path.join(HERE, "deploy", "aws", "remitwatch.yaml")], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr


def _template():
    return open(os.path.join(HERE, "deploy", "aws", "remitwatch.yaml")).read()


def _resource(text, name):
    start = text.index(f"\n  {name}:")
    nxt = re.search(r"\n  [A-Za-z]+:", text[start + 3:])
    return text[start:start + 3 + (nxt.start() if nxt else len(text))]


def _units(text, how):
    return dict(re.findall(how + r"(\S+)(?: >/dev/null)? <<'UNIT'\n(.*?)\nUNIT\n", text, re.S))


def test_the_server_and_its_address_outlive_any_stack_update():
    """A stack update must never put an empty server in place of the one holding the data: the server launches from the
    launch template's default version (CloudFormation's own updates of the template never move it), and the server and
    its fixed address are kept, not deleted, whatever the stack does."""
    text = _template()
    server, address = _resource(text, "Server"), _resource(text, "Address")
    assert "Version: !GetAtt LaunchTemplate.DefaultVersionNumber" in server and "LatestVersionNumber" not in text
    for res in (server, address):
        assert "DeletionPolicy: Retain" in res and "UpdateReplacePolicy: Retain" in res
    assert "DeleteOnTermination: false" in _resource(text, "LaunchTemplate")
    aws = open(os.path.join(HERE, "docs", "AWS_SETUP.md")).read()
    assert "## Never update or delete the stack" in aws and "Replacement: True" in aws
    for param in ("InstanceType", "DiskSizeGiB", "GitRef", "SiteAddress"):
        assert f"**{param}**" in aws


def test_system_updates_really_happen_every_night():
    """dnf-automatic on Amazon Linux 2023 stays on the release the image was made from, so it installs nothing: the
    server moves to the latest release itself, every night, as AWS documents, and the runbook sets up the same on a
    server made before."""
    text = _template()
    boot = text[text.index("UserData:"):text.index("\n  Server:")]
    assert "dnf-automatic" not in boot and "automatic security updates" not in text
    made = _units(boot.replace("\n            ", "\n"), "cat > /etc/systemd/system/")
    service, timer = made["remitwatch-os-update.service"], made["remitwatch-os-update.timer"]
    assert "ExecStart=/usr/bin/dnf -y --releasever=latest upgrade" in service
    assert "OnCalendar=" in timer and "WantedBy=timers.target" in timer and "systemctl enable --now remitwatch-os-update.timer" in boot
    assert "${" not in service + timer                                      # nothing CloudFormation would substitute
    runbook = open(os.path.join(HERE, "docs", "SERVER_RUNBOOK.md")).read()
    assert _units(runbook, "sudo tee /etc/systemd/system/") == made           # the runbook's copy for an older server
    assert "disable --now dnf-automatic.timer" in runbook


def test_the_nightly_update_reboots_only_when_needed_and_never_during_a_deploy(tmp_path):
    import fcntl
    service = _units(_template().replace("\n            ", "\n"), "cat > /etc/systemd/system/")["remitwatch-os-update.service"]
    post = re.search(r"^ExecStartPost=(.*)$", service, re.M).group(1)
    state, bindir = tmp_path / "state", tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("dnf", 'echo "dnf $*" >> "$LOG"; exit "$(cat "$NEEDS")"'), ("systemctl", 'echo "systemctl $*" >> "$LOG"'),
                       ("auto-deploy.sh", 'echo "auto-deploy $*" >> "$LOG"')):
        (bindir / name).write_text("#!/bin/bash\n" + body + "\n")
        (bindir / name).chmod(0o755)
    cmd = post.replace("/var/lib/remitwatch-deploy", str(state)).replace("/usr/local/lib/remitwatch/auto-deploy.sh", str(bindir / "auto-deploy.sh"))
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", LOG=str(tmp_path / "log"), NEEDS=str(tmp_path / "needs"))
    log = lambda: (tmp_path / "log").read_text().splitlines() if (tmp_path / "log").exists() else []   # noqa: E731

    (tmp_path / "needs").write_text("0")                                     # nothing needs a reboot
    subprocess.run(["bash", "-c", cmd], env=env, timeout=30)
    assert log() == ["dnf needs-restarting -r"]
    (tmp_path / "needs").write_text("1")                                     # a new kernel: once runs have ended, reboot
    subprocess.run(["bash", "-c", cmd], env=env, timeout=30)
    assert log()[1:] == ["dnf needs-restarting -r", "auto-deploy --idle 3600", "systemctl reboot"]
    with open(state / "lock", "w") as fh:                                   # a deploy is going: no reboot meanwhile
        fcntl.flock(fh, fcntl.LOCK_EX)
        subprocess.run(["bash", "-c", cmd.replace("-w 3600", "-w 1")], env=env, timeout=30)
    assert log()[4:] == ["dnf needs-restarting -r"]                          # and no reboot


def _caddy_bytes(size):
    """Caddy reads max_size with go-humanize: MB is 1000 x 1000 bytes, MiB is 1024 x 1024."""
    m = re.fullmatch(r"(\d+)\s*([KMG]i?B)", size)
    n, unit = int(m.group(1)), m.group(2)
    return n * {"KB": 10**3, "MB": 10**6, "GB": 10**9, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30}[unit]


def test_caddy_takes_more_than_the_app_does_in_caddys_own_units():
    from remitwatch import uploads
    text = open(os.path.join(HERE, "deploy", "Caddyfile")).read()
    big = re.search(r"request_body @uploads \{\s*max_size (\S+)", text).group(1)
    small = re.search(r"request_body @other \{\s*max_size (\S+)", text).group(1)
    assert _caddy_bytes(big) > uploads.MAX_BYTES, (big, _caddy_bytes(big), uploads.MAX_BYTES)
    assert _caddy_bytes(small) == 20 * 10**6
    runbook = open(os.path.join(HERE, "docs", "SERVER_RUNBOOK.md")).read()
    assert f"{_caddy_bytes(big):,} bytes" in runbook


def test_the_runbook_applies_a_changed_env_by_making_the_app_again():
    """`docker compose restart` keeps the settings a container was made with: a new password or login in .env would not
    take effect. `up -d` makes the app again from .env. Every command runs as root (Docker and .env are root's)."""
    runbook = open(os.path.join(HERE, "docs", "SERVER_RUNBOOK.md")).read()
    block = runbook[runbook.index("## Everyday commands"):runbook.index("## What runs by itself")]
    assert "restart app" not in runbook and re.search(r"sudo docker compose up -d +# after changing \.env", block)
    commands = [ln for ln in block.splitlines() if ln.strip().startswith(("docker", "sudo"))]
    assert commands and all(ln.startswith("sudo ") for ln in commands)


def test_the_runbooks_restore_brings_back_the_copy_and_not_what_it_undoes(tmp_path):
    """A run stopped halfway leaves its last changes in the store's -wal file. The runbook's restore must not let SQLite
    lay them over the restored copy: the copy is what comes back, whole, with no run lock in it."""
    import signal

    from remitwatch.cli import backup_store
    from remitwatch.store import Store
    runbook = open(os.path.join(HERE, "docs", "SERVER_RUNBOOK.md")).read()
    block = runbook[runbook.index("- To restore a copy"):]
    unpack = re.search(r"app sh -c '(.*?)'\n", block).group(1)
    check = re.search(r'app python -c "(.*?)"\n', block).group(1).replace('\\"', '"')
    data = tmp_path / "data"
    data.mkdir()
    db = str(data / "remitwatch.sqlite")
    s = Store(db)                                                          # the app's own store: WAL
    s.conn.execute("CREATE TABLE work (id INTEGER PRIMARY KEY, status TEXT, note TEXT)")
    s.conn.executemany("INSERT INTO work (status, note) VALUES ('open', ?)", [("x" * 200,) for _ in range(200)])
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('pipeline:lock', '{\"pid\": 7}')")
    s.conn.commit()
    s.close()
    copy = backup_store(db, str(data / "backups"), keep=7)                  # the day's copy
    child = subprocess.Popen([sys.executable, "-c", (
        "import sqlite3, sys, time\n"
        "c = sqlite3.connect(sys.argv[1], timeout=60)\n"
        "c.execute(\"UPDATE work SET status='dismissed'\")\n"
        "c.execute('DELETE FROM work WHERE id <= 50')\n"
        "c.commit()\n"
        "print('committed', flush=True)\n"
        "time.sleep(600)\n"), db], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "committed"
    os.kill(child.pid, signal.SIGKILL)                                     # the mistake, and a run stopped halfway
    child.wait()
    assert os.path.getsize(db + "-wal") > 0
    # a mistyped name changes nothing
    bad = subprocess.run(["sh", "-c", unpack.replace("/data", str(data)).replace("<file>", "no-such-copy.sqlite.gz")])
    assert bad.returncode != 0 and os.path.getsize(db + "-wal") > 0
    subprocess.run(["sh", "-c", unpack.replace("/data", str(data)).replace("<file>", os.path.basename(copy))], check=True)
    out = subprocess.run([sys.executable, "-c", check.replace("/data/remitwatch.sqlite", db)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "ok"
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*), SUM(status='dismissed') FROM work").fetchone() == (200, 0)
    assert c.execute("SELECT COUNT(*) FROM meta WHERE key LIKE '%:lock'").fetchone()[0] == 0


def test_a_new_server_answers_before_its_first_run_and_starts_its_scheduler(tmp_path, monkeypatch):
    """Before the first run there is no store: every page must still answer (it used to fail with "no such table"), and
    the scheduler must start, so the first run happens without anyone (it used to wait for a store that never came)."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api_mod
    from remitwatch import pipeline
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    monkeypatch.setattr(api_mod, "DB_PATH", db)
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("DROPBOX_APP_SECRET", "x")
    monkeypatch.delenv("REMITWATCH_SYNC_INTERVAL_MINUTES", raising=False)
    started = []
    monkeypatch.setattr(pipeline.SCHEDULER, "start", lambda path, minutes: started.append((path, minutes)))
    monkeypatch.setattr(pipeline.SCHEDULER, "stop", lambda: None)
    with TestClient(api_mod.app) as c:
        for path in ("/api/meta", "/api/brief", "/api/findings", "/api/desk?category=denial", "/api/worklist/counts", "/api/progress",
                     "/api/hide", "/api/faxes", "/api/lessons", "/api/activity/overview", "/api/summary"):
            assert c.get(path).status_code == 200, path
    # no interval set: still once a day, as Vercel's daily scheduled call
    assert started == [(db, pipeline.DAILY_MINUTES)]


def test_health_says_which_commit_each_site_runs_and_nothing_more(tmp_path, monkeypatch):
    """/api/health answers without a login and says which commit the site runs, in short, so that the server and the
    Vercel site can be compared: the server the one its image was built from (REMITWATCH_COMMIT), Vercel the one it
    built (VERCEL_GIT_COMMIT_SHA). None when the site cannot tell, and never anything but a commit hash."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api_mod
    ours, vercels = "0123456789abcdef0123456789abcdef01234567", "fedcba9876543210fedcba9876543210fedcba98"     # invented
    (tmp_path / "remitwatch.sqlite").write_bytes(b"")
    monkeypatch.setattr(api_mod, "DB_PATH", str(tmp_path / "remitwatch.sqlite"))
    monkeypatch.setattr(api_mod, "PASSWORD", "a-long-site-passphrase-1")     # a closed site: health answers all the same
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_COMMIT", ours)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", vercels)                     # not this site's: the server never reads it
    c = TestClient(api_mod.app)
    assert c.get("/api/summary").status_code == 401
    h = c.get("/api/health")
    assert h.status_code == 200 and h.json()["commit"] == "0123456" and h.json()["mode"] == "server" and h.json()["ok"] is True
    assert set(h.json()) == {"ok", "mode", "version", "commit", "time"}      # the commit is all that is new
    monkeypatch.setenv("REMITWATCH_COMMIT", ours.upper() + "\n")             # a hash in capitals is still the hash
    assert c.get("/api/health").json()["commit"] == "0123456"
    for value in ("", "  ", "abc12", "not-a-commit", ours + "; more", "g" + ours[1:], "x" * 80):
        monkeypatch.setenv("REMITWATCH_COMMIT", value)
        assert c.get("/api/health").json()["commit"] is None, value       # nothing else a setting holds is ever shown
    monkeypatch.delenv("REMITWATCH_COMMIT")
    assert c.get("/api/health").json()["commit"] is None
    # the Vercel site: the commit Vercel built
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_COMMIT", ours)                            # not Vercel's: ignored there
    h = c.get("/api/health").json()
    assert h["mode"] == "serverless" and h["commit"] == "fedcba9"
    assert set(h) == {"ok", "mode", "version", "commit", "runtime", "selftest", "time"}
    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA")
    assert c.get("/api/health").json()["commit"] is None


def test_the_scheduler_runs_on_the_interval_set_and_daily_at_least():
    from remitwatch import pipeline
    calls = []
    real = pipeline.SCHEDULER.start
    pipeline.SCHEDULER.start = lambda path, minutes: calls.append(minutes)
    try:
        env = dict(os.environ)
        for k in ("REMITWATCH_SYNC_INTERVAL_MINUTES", "DROPBOX_APP_SECRET", "DROPBOX_APP_KEY", "DROPBOX_REFRESH_TOKEN", "TRIZETTO_SFTP_USER", "TRIZETTO_SFTP_PASSWORD"):
            os.environ.pop(k, None)
        assert pipeline.start_scheduler_from_env("x") is False                  # nothing to pull from, no interval: nothing to run
        os.environ["REMITWATCH_SYNC_INTERVAL_MINUTES"] = "60"
        assert pipeline.start_scheduler_from_env("x") is True and calls[-1] == 60
        os.environ["REMITWATCH_SYNC_INTERVAL_MINUTES"] = "0"
        os.environ["DROPBOX_APP_SECRET"] = "x"
        assert pipeline.start_scheduler_from_env("x") is True and calls[-1] == pipeline.DAILY_MINUTES
    finally:
        pipeline.SCHEDULER.start = real
        os.environ.clear()
        os.environ.update(env)


def test_backups_are_whole_copies_taken_while_the_site_runs(tmp_path, monkeypatch, capsys):
    import gzip

    from remitwatch import cli
    from tests.test_fax import _store
    db, _npi = _store(tmp_path)
    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    assert cli.main(["--db", db, "backup", "--keep", "2"]) == 0
    path = json.loads(capsys.readouterr().out)["file"]
    restored = tmp_path / "restored.sqlite"
    restored.write_bytes(gzip.open(path).read())
    assert sqlite3.connect(str(restored)).execute("SELECT COUNT(*) FROM findings").fetchone()[0] == n
    assert oct(os.stat(path).st_mode & 0o777) == "0o600" and oct(os.stat(os.path.dirname(path)).st_mode & 0o777) == "0o700"
    # the daily one: once a day, the oldest beyond the number kept removed
    assert "file" in cli._daily_backup(db) and "skipped" in cli._daily_backup(db)
    for i in range(3):
        cli.backup_store(db, keep=2)
        time.sleep(1.1)
    assert len([f for f in os.listdir(os.path.dirname(path)) if f.endswith(".sqlite.gz")]) == 2


def test_setup_on_a_server_checks_what_a_server_needs(tmp_path, monkeypatch):
    from remitwatch import cloud
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_DB", str(tmp_path / "remitwatch.sqlite"))
    monkeypatch.setenv("REMITWATCH_PASSWORD", "a-long-site-passphrase-1")
    for k in ("CRON_SECRET", "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "remitwatch.sqlite").write_bytes(b"")
    out = cloud.setup_status(probe=False)
    checks = {c["key"]: c for c in out["checks"]}
    assert out["mode"] == "server" and "state_folder" not in checks and checks["store"]["ok"] is True
    assert checks["cron_secret"]["ok"] is None and "not needed here" in checks["cron_secret"]["detail"]
    assert checks["limits"]["label"] == "Memory" and "Vercel" not in checks["dropbox_app"]["fix"] and ".env" in checks["dropbox_app"]["fix"]
    # Vercel's own checks are as they were
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    v = {c["key"]: c for c in cloud.setup_status(probe=False)["checks"]}
    assert "state_folder" in v and "store" not in v and "in Vercel" in v["dropbox_app"]["fix"] and v["cron_secret"]["ok"] is False
