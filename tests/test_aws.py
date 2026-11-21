"""Moving to a server of the practice's own (deploy/aws): the one-time move of the Vercel site's data, the fax line looked
after by the server's own runs, the settings script, and the AWS template."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess

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


def test_the_settings_script_keeps_values_exactly_and_the_key_loads(tmp_path):
    if not shutil.which("bash"):
        pytest.skip("no bash")
    from remitwatch import modmed as M
    repo = tmp_path / "repo"
    (repo / "deploy" / "aws").mkdir(parents=True)
    shutil.copy(os.path.join(HERE, "deploy", "aws", "configure.sh"), repo / "deploy" / "aws" / "configure.sh")
    (repo / ".env").write_text("SITE_ADDRESS='remitwatch.example.com'\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(f'#!/bin/bash\necho "$*" >> "{tmp_path}/docker.log"\n')
    (bindir / "docker").chmod(0o755)
    (bindir / "systemctl").write_text(f'#!/bin/bash\necho "$*" >> "{tmp_path}/systemctl.log"\nexit 1\n')     # auto-deploy not on
    (bindir / "systemctl").chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", REMITWATCH_DEPLOY_STATE=str(tmp_path / "deploy-state"))
    pem = M.generate_key()
    answers = ("\n\n\n\n" "key123\n" "sec$ret#1 x\n" "tok\n\n" "Test Derm\n1 Main St\n\n\n\n\n\n" "\n\n" + pem
               + "\n\n\n\n" "\n\n\n\n\n" "n\n")
    r = subprocess.run(["bash", str(repo / "deploy" / "aws" / "configure.sh")], input=answers, text=True, capture_output=True,
                       env=env, timeout=60)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    vals = {}
    for ln in (repo / ".env").read_text().splitlines():
        k, v = ln.split("=", 1)
        assert v.startswith("'") and v.endswith("'"), ln          # single quotes: nothing in a value is read as a variable
        vals[k] = v[1:-1]
    assert oct((repo / ".env").stat().st_mode & 0o777) == "0o600"
    assert vals["DROPBOX_APP_SECRET"] == "sec$ret#1 x" and vals["DROPBOX_ROOT_PATH"] == "/Trizetto Data" and vals["REMITWATCH_USER"] == "oda"
    assert len(vals["REMITWATCH_PASSWORD"]) == 32 and len(vals["CRON_SECRET"]) == 32 and "SRFAX_ACCESS_ID" not in vals
    assert M.jwks(M.Settings.from_env({"MODMED_PRIVATE_KEY": vals["MODMED_PRIVATE_KEY"]}))["keys"][0] == M.public_jwk(M.load_private_key(pem))
    assert (tmp_path / "docker.log").read_text().splitlines() == ["compose stop app", "compose up -d --build"]
    # run again, pressing Enter everywhere: everything is kept
    before = (repo / ".env").read_text()
    r = subprocess.run(["bash", str(repo / "deploy" / "aws" / "configure.sh")], input="\n" * 40, text=True, capture_output=True,
                       env=env, timeout=60)
    assert r.returncode == 0 and (repo / ".env").read_text() == before


def test_the_aws_template_passes_awss_own_checks():
    cfnlint = pytest.importorskip("cfnlint")
    _ = cfnlint
    r = subprocess.run(["cfn-lint", os.path.join(HERE, "deploy", "aws", "remitwatch.yaml")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


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
        import time
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
