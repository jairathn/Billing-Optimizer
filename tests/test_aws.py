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
    from remitwatch import cli, cloud
    from remitwatch.notes import get_packet
    from tests.test_cloud import DATA, cycle, visits_with_downcodes, write_remits
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))       # the same folder, read as files
    served = cloud.ServingCopy().connection()
    fid = served.execute("SELECT finding_id FROM findings WHERE signal='DOWNCODE_LEVEL_REDUCED' ORDER BY finding_id").fetchone()[0]
    served.close()
    # work recorded on the Vercel site since its last run (still in the journal), and a packet built there
    cloud.record_action(state, {"kind": "status", "finding_id": fid, "status": "appealed", "notes": "faxed by hand", "actor": "biller"})
    cloud.record_action(state, {"kind": "payer_fax", "payer_group": "Anthem / BCBS", "purpose": "appeal", "number": "8005550142", "actor": "biller"})
    pid = cloud.save_packet(state, {"packet_id": "abc123", "finding_id": fid, "created_at": "2026-11-20T10:00:00+00:00", "mode": "ai",
                                    "subject": "Request for reconsideration", "letter_full": "Dear payer, SAMPLE PAT", "letter_redacted": "Dear payer, S.P.",
                                    "analysis": {"recommendation": "appeal"}, "evidence": "835", "attachments": ["note"]}, b"%PDF-1.4 packet")
    db = str(tmp_path / "server" / "remitwatch.sqlite")
    assert cli.main(["--db", db, "import-cloud"]) == 0
    out = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert out["journal_applied"] == 2 and out["packets"] == 1 and out["transactions"] == 6 and out["findings"] >= 3
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT status, notes FROM findings WHERE finding_id=?", (fid,)).fetchone() == ("appealed", "faxed by hand")
    assert conn.execute("SELECT number FROM payer_fax").fetchone()[0] == "8005550142"
    # where the Vercel site had got to in the Dropbox folder: the server carries on from there, no file fetched again
    assert conn.execute("SELECT value FROM meta WHERE key='sync:cursor'").fetchone() is not None
    # the server's schema: the indexes the serverless copy leaves out are back
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0] > 10
    conn.close()
    conn = sqlite3.connect(db)
    p = get_packet(conn, pid, with_pdf=True, reveal=True)
    assert p["pdf"] == b"%PDF-1.4 packet" and p["letter"] == "Dear payer, SAMPLE PAT" and p["analysis"]["recommendation"] == "appeal"
    assert get_packet(conn, pid)["letter"] == "Dear payer, S.P."
    conn.close()
    # a second move would overwrite what the server has done since: only with --force
    assert cli.main(["--db", db, "import-cloud"]) == 1
    assert "--force" in capsys.readouterr().out
    assert cli.main(["--db", db, "import-cloud", "--force"]) == 0


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
    pem = M.generate_key()
    answers = ("\n\n\n\n" "key123\n" "sec$ret#1 x\n" "tok\n\n" "Test Derm\n1 Main St\n\n\n\n\n\n" "\n\n" + pem
               + "\n\n\n\n" "\n\n\n\n\n" "n\n")
    r = subprocess.run(["bash", str(repo / "deploy" / "aws" / "configure.sh")], input=answers, text=True, capture_output=True,
                       env=dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}"), timeout=60)
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
                       env=dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}"), timeout=60)
    assert r.returncode == 0 and (repo / ".env").read_text() == before


def test_the_aws_template_passes_awss_own_checks():
    cfnlint = pytest.importorskip("cfnlint")
    _ = cfnlint
    r = subprocess.run(["cfn-lint", os.path.join(HERE, "deploy", "aws", "remitwatch.yaml")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
