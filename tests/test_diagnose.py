"""
Tests for remitwatch.diagnose, the diagnostic bundle: every part is there (manifests, events, errors with their stacks,
self-checks, repairs, decision logs of example items, the store's layout, the settings' names, REPRODUCE.md), the
commands REPRODUCE.md gives exist, and no byte of the bundle holds a patient's name, birth date, member id, record
number, claim id or check number, nor an exact date of one item. Built from a server's store and logs folder and from
a copy of a serverless state folder (the command line). Invented data only.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import cloud, diagnose, pipeline, repair, runlog  # noqa: E402
from remitwatch.state import LocalState  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_runlog import FIRST, LAST, MEMBER, MRN, PLANTED, UNI, denial_files  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "TRIZETTO_SFTP_USER", "SRFAX_ACCESS_ID",
              "VERCEL", "REMITWATCH_STATE_DIR", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    saved = set(runlog.SCRUB.names)
    yield
    runlog.SCRUB.names = saved
    runlog.set_clock(None)
    runlog._CURRENT.set(None)
    diagnose.PARTS.clear()


@pytest.fixture
def server_world(tmp_path, monkeypatch):
    """A server that ran two cycles: the second closed the planted patient's denial on a later payment, contained an
    error carrying the planted values, held a file and recorded a failing self-check with an example item."""
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    feed1, feed2 = tmp_path / "feed1", tmp_path / "feed2"
    p1, p2 = denial_files(feed1, paid_later=True)
    os.makedirs(feed2)
    os.replace(p2, feed2 / "r002.RMT")
    monkeypatch.setenv("SRFAX_ACCESS_PWD", "s3cret-fax-passw0rd")
    rl = runlog.start_run("cycle", trigger="test", db_path=db, host="server")
    with rl.step("cycle"):
        pipeline.run_cycle(db, sync=False, local=str(feed1), email=False, report=False, log=rl.log)
    store = Store(db)
    rl.finish(conn=store.conn)
    before = runlog.snapshot_items(store.conn)
    fid = next(iter(before.rows))
    store.close()
    rl = runlog.start_run("cycle", trigger="test", db_path=db, host="server")
    with rl.step("cycle"):
        pipeline.run_cycle(db, sync=False, local=str(feed2), email=False, report=False, log=rl.log)
    with rl.step("ema_waiting", contain=True, inputs={"file": "abcdef0123456789"}):
        raise ValueError(f"bad row for {FIRST} {LAST} born 02/29/1960, member {MEMBER}, MRN {MRN}")
    store = Store(db)
    rl.set("decisions", runlog.record_decisions(store.conn, rl.run_id, before))
    rl.set("invariants", {"held": False, "failing": ["closures_have_proof"],
                          "checks": [{"id": "closures_have_proof", "status": "fail", "severity": "critical", "n": 1, "examples": [fid]},
                                     {"id": "one_state", "status": "ok", "examples": []}]})
    reg = repair.registry_for(rl, conn=store.conn)
    reg.failed("unreadable_file", "production:abcdef0123456789", None, reason="held: the file was cut short", hold=True)
    reg.record("unreadable_file", "production:abcdef0123456789", "held", after={"status": "held"},
               next_step=repair.REMEDIES["unreadable_file"].next_step)
    reg.save()
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('sync:cursor', 'AAHopaque-cursor-value')")
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', 'ab' || hex(randomblob(31)))")
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('pipeline:lock', ?)",
                       (json.dumps({"started": "2026-11-22T10:00:00+00:00", "heartbeat": "2026-11-22T10:05:00+00:00", "pid": 4242,
                                    "host": "a-host-name", "token": "tok"}),))
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('custom:note', ?)", (f"{LAST} {FIRST} 1960-02-29",))
    store.conn.commit()
    rl.finish(conn=store.conn)
    salt = store.conn.execute("SELECT value FROM meta WHERE key='ema:key_salt'").fetchone()[0]
    store.close()
    return {"db": db, "fid": fid, "run_id": rl.run_id, "salt": salt}


def members(res) -> dict[str, bytes]:
    return diagnose.unpack(res["bytes"] if "bytes" in res else open(res["path"], "rb").read())


def test_a_server_bundle_has_every_part_and_nothing_that_identifies_a_patient(server_world):
    res = diagnose.build_bundle(db_path=server_world["db"], days=2, known=PLANTED)
    files = members(res)
    names = set(files)
    for part in diagnose.BUNDLE_PARTS:
        assert any(n == part or (part.endswith("/") and n.startswith(part)) for n in names), part
    assert len(res["runs"]) == 2 and res["scan"]["masked_on_second_pass"] == 0
    # errors: the contained error with its stack and where, the message scrubbed
    errs = json.loads(files["errors.json"])["errors"]
    e = next(x for x in errs if x["type"] == "ValueError")
    assert e["stack"] and e["where"].startswith("tests/test_diagnose.py:") and e["step"] == "ema_waiting" and e["contained"] is True
    assert any(f["file"] == "tests/test_diagnose.py" and f["function"] == "server_world" for f in e["stack"])   # not masked as a path
    # the failing check's example item with its decision log, dates cut to the month
    ex = json.loads(files["examples/closures_have_proof.json"])
    item = ex["items"][0]
    assert item["finding_id"] == server_world["fid"] and item["decisions"][0]["rule"] == "paid_later:later_payment"
    assert re.fullmatch(r"\d{4}-\d{2}", item["appeal_deadline"])                # the month of its deadline, not the day
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b(?!T)", files["examples/closures_have_proof.json"].decode())
    # repairs, decisions, store layout, meta values only where safe, settings by name
    rep = json.loads(files["repairs.json"])
    assert rep["open_problems"][0]["target"] == "production:abcdef0123456789" and rep["recent"][0]["outcome"] == "held"
    dec = json.loads(files["decisions.json"])
    assert {"kind": "closed", "rule": "paid_later:later_payment", "n": 1} in dec["by_kind_and_rule"]
    st = json.loads(files["store.json"])
    assert st["tables"]["findings"]["rows"] >= 1 and "work_state" in st["tables"]["findings"]["columns"]
    meta = st["meta"]
    assert set(meta["ema:key_salt"]) == {"length", "sha"} and set(meta["sync:cursor"]) == {"length", "sha"}
    assert meta["pipeline:lock"]["lock"] == {"started": "2026-11-22T10:00:00+00:00", "heartbeat": "2026-11-22T10:05:00+00:00"}
    assert set(meta["custom:note"]) == {"length", "sha"} and meta["schema_version"] == 3
    settings = json.loads(files["settings.json"])
    assert "SRFAX_ACCESS_PWD" in settings["in_force_at_last_run"]
    whole = b"".join(files.values()).decode("utf-8", errors="ignore")
    for secret in ("s3cret-fax-passw0rd", server_world["salt"], "AAHopaque", "a-host-name", "CB0000300", "TRACE0000"):
        assert secret not in whole, secret
    assert diagnose.scan_bytes(files, PLANTED) == [] and diagnose.scan_bytes({"zip": res["bytes"]}, PLANTED) == []
    for n, b in files.items():
        assert "—" not in b.decode("utf-8"), n


def test_one_run_only(server_world):
    files = members(diagnose.build_bundle(db_path=server_world["db"], run_id=server_world["run_id"]))
    assert [n for n in files if n.startswith("runs/")] == [f"runs/{server_world['run_id']}.json"]
    bundle = json.loads(files["bundle.json"])
    assert bundle["run"] == server_world["run_id"] and [r["run_id"] for r in bundle["runs"]] == [server_world["run_id"]]


def test_reproduce_gives_commands_that_exist_and_never_touch_the_live_feeds(server_world):
    text = members(diagnose.build_bundle(db_path=server_world["db"], days=2))["REPRODUCE.md"].decode()
    from remitwatch import cli
    for m in re.finditer(r"remitwatch\.cli(?:\s+--db\s+\S+)?\s+([a-z0-9-]+)((?:\s+--[a-z-]+)*)", text):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
            cli.main([m.group(1), "--help"])
        for flag in re.findall(r"--[a-z-]+", m.group(2)):
            assert flag in out.getvalue(), (m.group(1), flag)
    for switch_off in ("REMITWATCH_FAX_SENDER=none", "TRIZETTO_SFTP_USER=", "DROPBOX_REFRESH_TOKEN=", "--skip-sync", "--no-email"):
        assert switch_off in text
    assert "run_cloud_cycle(LocalState(" in text and "python -m remitwatch.diagnose --state-dir" in text
    assert "SQLite 3.40.0" in text and "SQLite 3.45" in text


def test_a_part_another_unit_registers_is_scrubbed_and_a_failing_one_is_named(server_world):
    diagnose.register_part("selfcheck", lambda conn: {"audit": {"gaps": 2, "example": f"patient {FIRST} {LAST} 1960-02-29"},
                                                      "finding_id": "0123456789abcdef01234567"})
    diagnose.register_part("archive", lambda conn: 1 / 0)
    files = members(diagnose.build_bundle(db_path=server_world["db"], days=2, known=PLANTED))
    bundle = json.loads(files["bundle.json"])
    assert bundle["scan"]["masked_on_second_pass"] == 0                         # the first pass did the scrubbing
    sc = json.loads(files["selfcheck.json"])
    assert sc["audit"]["gaps"] == 2 and sc["finding_id"] == "0123456789abcdef01234567" and "1960" not in files["selfcheck.json"].decode()
    assert json.loads(files["archive.json"])["error"]["type"] == "ZeroDivisionError"


def test_a_planted_word_that_slips_past_the_scrubber_stops_the_bundle(server_world, monkeypatch):
    diagnose.register_part("leak", lambda conn: {"x": 1})
    monkeypatch.setattr(diagnose, "_dump", lambda v: json.dumps(v) + f" {UNI}")
    with pytest.raises(RuntimeError, match="planted word"):
        diagnose.build_bundle(db_path=server_world["db"], days=2, known=[UNI])


def test_a_bundle_from_a_copy_of_a_serverless_state_folder_on_the_command_line(tmp_path):
    from tests.fakes import FakeDropbox
    from remitwatch.state import DropboxState
    root = tmp_path / "dbx"
    client = FakeDropbox(str(root))
    state = DropboxState(client, "/RemitWatch-state")
    denial_files(root / "Trizetto Data")
    os.environ["DROPBOX_ROOT_PATH"] = "/Trizetto Data"
    try:
        for _ in range(3):
            rl = runlog.start_run("cycle", trigger="test", state=state, host="serverless")
            with rl.step("cycle"):
                out = cloud.run_cloud_cycle(state, client, budget_s=600, email=False, log=rl.log)
            rl.finish(out=out)
            if not out.get("more"):
                break
    finally:
        os.environ.pop("DROPBOX_ROOT_PATH", None)
    copy = str(root / "RemitWatch-state")              # the state folder, as a local copy of it would be
    assert LocalState(copy).meta(cloud.RAW)
    out_zip = str(tmp_path / "bundle.zip")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert diagnose.main(["--state-dir", copy, "--days", "2", "--out", out_zip]) == 0
    said = json.loads(buf.getvalue())
    assert said["bundle"] == out_zip and said["runs"] >= 1
    files = diagnose.unpack(open(out_zip, "rb").read())
    assert any(n.startswith("runs/") for n in files) and any(n.startswith("events/") for n in files)
    assert json.loads(files["store.json"])["tables"]["findings"]["rows"] >= 1
    assert diagnose.scan_bytes(files, [LAST, FIRST, MEMBER]) == []
    assert oct(os.stat(out_zip).st_mode & 0o777) == "0o600"


def test_the_module_runs_from_the_command_line_on_a_server(server_world, tmp_path):
    out_zip = str(tmp_path / "cli.zip")
    r = subprocess.run([sys.executable, "-m", "remitwatch.diagnose", "--db", server_world["db"], "--days", "2", "--out", out_zip],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert json.loads(r.stdout)["bundle"] == out_zip and os.path.getsize(out_zip) > 1000
    r = subprocess.run([sys.executable, "-m", "remitwatch.diagnose", "--db", str(tmp_path / "none.sqlite")], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "No store" in r.stderr


def test_the_default_place_is_beside_the_store_never_the_repository(tmp_path):
    p = diagnose.default_out(str(tmp_path / "data" / "remitwatch.sqlite"), None)
    assert p.startswith(str(tmp_path / "data" / "diagnostics")) and p.endswith(".zip")
    assert diagnose.default_out(None, str(tmp_path / "copy" / "state")).startswith(str(tmp_path / "copy" / "diagnostics"))
