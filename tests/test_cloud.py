"""
Tests for the serverless mode (remitwatch.cloud): the store kept in a state folder, time-boxed cycles that continue in a
fresh run, the journal of status changes, the dashboard's copy, packets as files, the lock and the setup checks.
A local folder stands in for Dropbox (tests.fakes.FakeDropbox). Everything here is synthetic.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import sqlite3
import sys
import time
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import cloud  # noqa: E402
from remitwatch.state import Conflict, DropboxState, LocalState  # noqa: E402
from tests.fakes import FakeDropbox  # noqa: E402
from tests.test_features import claim, clean_visits, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

DATA = "Trizetto Data"


def visits_with_downcodes(n_down: int = 3) -> list[str]:
    """Anthem Medicare Advantage: enough clean 99214 and 99213 visits for a baseline, then level-of-care reductions."""
    claims = clean_visits(1, 25, "99214", "223.65", "127.80", cfi="16") + clean_visits(100, 25, "99213", "156.66", "76.09", cfi="16")
    claims += [claim(200 + i, svc("99214", "223.65", "76.09", "76.09", "CAS*CO*186*51.71~CAS*CO*45*95.85~"), cfi="16") for i in range(n_down)]
    return claims


def write_remits(folder, claims: list[str], prefix: str = "r", per_file: int = 10, start_ctrl: int = 1) -> list[str]:
    os.makedirs(folder, exist_ok=True)
    names = []
    for n, i in enumerate(range(0, len(claims), per_file)):
        name = f"{prefix}{n:03d}.RMT"
        with open(os.path.join(folder, name), "wb") as fh:
            fh.write(remit("".join(claims[i:i + per_file]), "100.00", seg_count=99, isa_ctrl=f"{start_ctrl + n:09d}", pay_date="20260415"))
        names.append(name)
    return names


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fake Dropbox with a data folder, the state kept inside it the way the deployment keeps it, and clean settings."""
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "ALERT_WEBHOOK_URL", "REPORT_TO", "CRON_SECRET",
              "REMITWATCH_STATE_DIR", "ANTHROPIC_API_KEY", "VERCEL", "VERCEL_ENV", "VERCEL_PROJECT_PRODUCTION_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    monkeypatch.setenv("DROPBOX_ROOT_PATH", f"/{DATA}")
    root = tmp_path / "dbx"
    client = FakeDropbox(str(root))
    state = DropboxState(client, "/RemitWatch-state")
    return root, client, state


def cycle(state, client, **kw):
    kw.setdefault("budget_s", 600)
    return cloud.run_cloud_cycle(state, client, email=False, log=lambda *a: None, **kw)


def raw_conn(state) -> sqlite3.Connection:
    store, existed = cloud.load_raw(state)
    assert existed
    return store.conn


# --------------------------------------------------------------------------- the cycle

def test_first_load_then_nothing_new_then_one_new_file(box):
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    out = cycle(state, client)
    assert out["ingest"]["remits"] == 6 and out["ingest"]["mode"] == "full" and out["detect"]["findings"] >= 3
    assert out["more"] is False and out["serving"]["gz_bytes"] > 0 and out["store"]["gz_bytes"] > 0 and out["memory_mb"]["peak"]
    names = {m["name"] for m in state.list()}
    assert {cloud.RAW, cloud.SERVING, cloud.STATUS, cloud.CURSOR} <= names and cloud.LOCK not in names
    assert json.loads(state.get(cloud.STATUS))["finished"]
    conn = raw_conn(state)
    assert conn.execute("SELECT COUNT(*) FROM findings WHERE signal='DOWNCODE_LEVEL_REDUCED'").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM dropbox_seen").fetchone()[0] == 6
    # files keep their own names in the store (the link step reads them), not the names of the scratch copies
    assert sorted(r[0] for r in conn.execute("SELECT source_name FROM transactions")) == [f"r{n:03d}.RMT" for n in range(6)]
    # the compact store carries no secondary indexes: they would double what has to be held in memory
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND name LIKE 'ix_%' AND tbl_name='lines'").fetchone()[0] == 0

    # nothing new: no download of data, no detection, and neither the store nor the dashboard's copy is uploaded again
    revs = (state.meta(cloud.RAW)["rev"], state.meta(cloud.SERVING)["rev"], state.get(cloud.CURSOR))
    uploads = client.calls.get("upload", 0)
    time.sleep(0.01)
    again = cycle(state, client)
    assert again["ingest"]["mode"] == "delta" and again["ingest"]["downloaded"] == 0 and "detect" not in again
    assert again["more"] is False and again["changed"] is False and "store" not in again and "serving" not in again
    assert (state.meta(cloud.RAW)["rev"], state.meta(cloud.SERVING)["rev"], state.get(cloud.CURSOR)) == revs
    assert client.calls.get("upload", 0) - uploads == 2            # the lock and the status
    status = json.loads(state.get(cloud.STATUS))
    assert status["started"] == again["started"] and status["last_change"] == out["finished"]
    assert status["totals"] == {"remit_files": 6, "findings": out["detect"]["findings"], "newest_payment": "2026-04-15"}

    # one new remittance: only that file is fetched, detection runs again and the dashboard's copy changes
    rev = state.meta(cloud.SERVING)["rev"]
    time.sleep(0.01)
    write_remits(root / DATA, [claim(300, svc("99214", "223.65", "76.09", "76.09", "CAS*CO*186*51.71~CAS*CO*45*95.85~"), cfi="16")], prefix="new", start_ctrl=50)
    third = cycle(state, client)
    assert third["ingest"]["remits"] == 1 and third["ingest"]["downloaded"] == 1 and third["detect"]["findings"] >= 4
    assert state.meta(cloud.SERVING)["rev"] != rev
    assert raw_conn(state).execute("SELECT COUNT(*) FROM findings WHERE signal='DOWNCODE_LEVEL_REDUCED'").fetchone()[0] == 4


def test_archive_is_read_once_and_loose_copies_are_not_downloaded(box):
    root, client, state = box
    names = write_remits(root / DATA / "Trizetto", visits_with_downcodes())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in names:
            zf.write(root / DATA / "Trizetto" / n, f"Trizetto/{n}")
    (root / DATA / "Trizetto.zip").write_bytes(buf.getvalue())
    write_remits(root / DATA / "week", [claim(300, svc("99213", "156.66", "76.09", "76.09", "CAS*CO*45*80.57~"), cfi="16")], prefix="w", start_ctrl=70)
    out = cycle(state, client)
    assert out["ingest"]["from_archive"] == 6 and out["ingest"]["remits"] == 7
    assert out["ingest"]["downloaded"] == 1          # the six loose copies of the archive's files were recognised by their hash
    conn = raw_conn(state)
    assert conn.execute("SELECT value FROM meta WHERE key='cloud:zip_done'").fetchone()
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 7


@pytest.mark.parametrize("older_name, newer_name", [("a.RMT", "b.RMT"), ("b.RMT", "a.RMT")])
def test_the_newer_file_wins_a_repeated_payment_whatever_order_they_are_read_in(box, older_name, newer_name):
    root, client, state = box
    folder = root / DATA
    os.makedirs(folder)
    first = remit("".join(clean_visits(1, 2, "99213", "156.66", "76.09", cfi="16")), "152.18", seg_count=99, isa_ctrl="000000007", pay_date="20260415")
    again = remit("".join(clean_visits(1, 3, "99213", "156.66", "76.09", cfi="16")), "228.27", seg_count=99, isa_ctrl="000000007", pay_date="20260415")
    (folder / older_name).write_bytes(first)
    (folder / newer_name).write_bytes(again)
    os.utime(folder / older_name, (1_776_000_000, 1_776_000_000))
    os.utime(folder / newer_name, (1_776_500_000, 1_776_500_000))
    cycle(state, client)
    rows = raw_conn(state).execute("SELECT source_name, superseded FROM transactions ORDER BY source_name").fetchall()
    assert {r[0]: r[1] for r in rows} == {older_name: 1, newer_name: 0}


def test_a_run_out_of_time_saves_what_it_has_and_the_next_run_continues(box, monkeypatch):
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes(), per_file=1)       # 53 files
    store, _ = cloud.load_raw(state)
    from remitwatch.alerts import ALERTS_DDL
    from remitwatch.detect import ensure_schema
    ensure_schema(store.conn)
    store.conn.executescript(ALERTS_DDL + cloud.CLOUD_DDL)
    # the deadline has already passed: everything is listed, nothing is taken, and the Dropbox position is not advanced
    first = cloud.sync_and_ingest(store, client, state, deadline=time.time() - 1, log=lambda *a: None)
    assert first["remaining"] == 53 and first["downloaded"] == 0 and "cursor" not in first
    second = cloud.sync_and_ingest(store, client, state, deadline=time.time() + 600, log=lambda *a: None)
    assert second["remaining"] == 0 and second["remits"] == 53 and second["cursor"]
    assert state.get(cloud.CURSOR) is None           # the position is written by the cycle, after the store is saved
    store.close()

    # a large batch hands detection to a fresh run: the first saves and asks to be continued, the second detects
    monkeypatch.setattr(cloud, "DEFER_DETECT_AFTER_FILES", 5)
    one = cycle(state, client)
    assert one["ingest"]["remits"] == 53 and one["deferred"] == "detection" and "detect" not in one
    assert one["more"] is True and one["progress"] is True and "serving" in one
    two = cycle(state, client)
    assert two["ingest"]["downloaded"] == 0 and two["detect"]["findings"] >= 3 and two["more"] is False
    assert raw_conn(state).execute("SELECT 1 FROM meta WHERE key='cloud:detect_pending'").fetchone() is None


def test_lock_one_cycle_at_a_time_and_a_dead_run_is_taken_over(tmp_path, monkeypatch):
    monkeypatch.delenv("REMITWATCH_MAX_SECONDS", raising=False)
    state = LocalState(str(tmp_path / "state"))
    token = cloud.acquire(state)
    assert token and cloud.acquire(state) is None
    assert cloud.run_cloud_cycle(state, None, email=False) == {"skipped": "another cycle is running"}
    assert cloud.lock_state(state)[0] and cloud.lock_state(state)[1] is None
    cloud.release(state, "someone-else")                     # only the cycle that took the lock can release it
    assert state.meta(cloud.LOCK) is not None
    cloud.release(state, token)
    assert state.meta(cloud.LOCK) is None and cloud.lock_state(state) == (None, None)
    # a lock older than any run can be: reported as a run that died, and taken over by the next one
    token = cloud.acquire(state)
    old = time.time() - cloud.lock_stale_seconds() - 5
    os.utime(os.path.join(state.root, cloud.LOCK), (old, old))
    running, stalled = cloud.lock_state(state)
    assert running is None and stalled and stalled["since"]
    new = cloud.acquire(state)
    assert new and new != token
    cloud.release(state, token)                              # the dead run's token no longer opens it
    assert state.meta(cloud.LOCK) is not None
    cloud.release(state, new)
    with pytest.raises(Conflict):
        state.put("x", b"1")
        state.put("x", b"2", mode="add")
    assert cloud.lock_stale_seconds() >= 360


def test_webhook_check_ignores_the_apps_own_writes(box):
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    assert cloud.has_new_files(state, client) is False           # before the first complete sync, notifications start nothing
    cycle(state, client)
    assert json.loads(state.get(cloud.CURSOR))["root"] == f"/{DATA}"
    assert cloud.has_new_files(state, client) is False
    state.put("status.json", b"{}")                              # the app writing to its own folder
    cloud.record_action(state, {"kind": "alert_ack", "alert_id": "x"})
    (root / DATA / "notes.pdf").write_bytes(b"%PDF")             # a file the app does not read
    assert cloud.has_new_files(state, client) is False
    time.sleep(0.01)
    write_remits(root / DATA, [claim(400, svc("99213", "156.66", "76.09", "76.09", "CAS*CO*45*80.57~"), cfi="16")], prefix="late", start_ctrl=90)
    assert cloud.has_new_files(state, client) is True


# --------------------------------------------------------------------------- the journal and the dashboard's copy

def test_status_changes_show_at_once_survive_and_reach_the_next_run(box, monkeypatch):
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))      # the same folder, read as local files
    here, elsewhere = cloud.ServingCopy(), cloud.ServingCopy()
    c = here.connection()
    fid = c.execute("SELECT finding_id FROM findings WHERE signal='DOWNCODE_LEVEL_REDUCED' ORDER BY finding_id").fetchone()[0]
    assert c.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "open"
    assert json.loads(c.execute("SELECT value FROM meta WHERE key='cache:meta'").fetchone()[0])["remits"]["n"] == 6
    c.close()
    elsewhere.connection().close()

    here.record({"kind": "status", "finding_id": fid, "status": "appealed", "notes": "sent 4/20", "actor": "biller"})
    row = lambda sc: sc.connection().execute("SELECT status, notes, appealed_at FROM findings WHERE finding_id=?", (fid,)).fetchone()   # noqa: E731
    assert tuple(row(here))[:2] == ("appealed", "sent 4/20") and row(here)[2]
    assert len(state.list(cloud.ACTIONS)) == 1
    assert row(elsewhere)[0] == "open"                 # another instance has not looked at the journal yet
    elsewhere.ensure(force=True)
    assert row(elsewhere)[0] == "appealed"
    # reading the journal again does not apply an entry twice
    here.ensure(force=True)
    elsewhere.ensure(force=True)
    events = here.connection().execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual'", (fid,)).fetchone()[0]
    assert events == 1

    # the next run folds the journal into the store, publishes a new copy and clears the journal
    rev = state.meta(cloud.SERVING)["rev"]
    time.sleep(0.01)
    out = cycle(state, client)
    assert out["actions_folded"] == 1 and "detect" not in out and state.list(cloud.ACTIONS) == []
    assert state.meta(cloud.SERVING)["rev"] != rev
    conn = raw_conn(state)
    assert conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "appealed"
    assert conn.execute("SELECT COUNT(*) FROM finding_events WHERE finding_id=? AND kind='manual'", (fid,)).fetchone()[0] == 1
    fresh = cloud.ServingCopy()
    assert row(fresh)[0] == "appealed"
    old_path = here.path
    here.ensure(force=True)                            # picks up the new copy under a new file name; the old file is removed
    assert here.path != old_path and not os.path.exists(old_path) and row(here)[0] == "appealed"

    # detection run again keeps the work recorded by people
    out = cycle(state, client, force_detect=True)
    assert out["detect"]["findings"] >= 3
    assert raw_conn(state).execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "appealed"


def test_dashboard_copy_is_empty_but_working_before_the_first_load_and_without_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_DIR"):
        monkeypatch.delenv(k, raising=False)
    sc = cloud.ServingCopy()
    assert sc.state() is None
    assert sc.connection().execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
    assert sc.status() == {"configured": False}
    with pytest.raises(RuntimeError):
        sc.record({"kind": "alert_ack", "alert_id": "x"})
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(tmp_path / "state"))
    assert sc.state() is not None
    assert sc.connection().execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
    st = sc.status()
    assert st["configured"] and st["last_cycle"] is None and st["has_data"] is False and st["running"] is None


def test_packets_are_kept_as_files(tmp_path):
    state = LocalState(str(tmp_path / "state"))
    pid = cloud.save_packet(state, {"packet_id": "abc123", "finding_id": "F1", "created_at": "2026-11-12T10:00:00+00:00", "mode": "template",
                                    "subject": "Appeal", "note_name": "note.pdf", "letter_full": "Patient: SAMPLE PAT", "letter_redacted": "Patient: [name]"},
                            b"%PDF-1.4 packet")
    assert pid == "F1.abc123"
    assert cloud.get_packet(state, pid)["letter"] == "Patient: [name]"
    assert cloud.get_packet(state, pid, reveal=True)["letter"] == "Patient: SAMPLE PAT"
    assert cloud.get_packet(state, pid, with_pdf=True)["pdf"] == b"%PDF-1.4 packet"
    assert [p["packet_id"] for p in cloud.list_packets(state, "F1")] == [pid] and cloud.list_packets(state, "F2") == []
    cloud.mark_packet_emailed(state, pid, "billing@example.com")
    assert cloud.list_packets(state, "F1")[0]["emailed_to"] == "billing@example.com"
    for bad in ("../../lock", "F1/../x.y", "nodot", ""):
        assert cloud.get_packet(state, bad) is None


# --------------------------------------------------------------------------- the web layer in serverless mode

@pytest.fixture
def site(box, monkeypatch):
    """The API in serverless mode over a loaded state folder, with a password and a scheduler secret."""
    from fastapi.testclient import TestClient

    from remitwatch import api
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setenv("CRON_SECRET", "s3cret-value-for-the-scheduler")
    monkeypatch.setenv("REMITWATCH_PASSWORD", "pw")
    monkeypatch.setattr(api, "PASSWORD", "pw")
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    auth = {"Authorization": "Basic " + base64.b64encode(b"oda:pw").decode()}
    return TestClient(api.app), auth, api, state


def test_site_is_closed_until_a_password_is_set(site, monkeypatch):
    tc, auth, api, _state = site
    monkeypatch.setattr(api, "PASSWORD", "")
    assert tc.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert tc.get("/setup").status_code == 200
    assert tc.get("/api/summary").status_code == 503 and tc.get("/api/findings").status_code == 503
    locked = tc.get("/api/setup/status").json()
    assert locked["locked"] and [c["key"] for c in locked["checks"]] == ["password"] and "webhook_url" not in locked
    assert tc.get("/api/cron/cycle").status_code == 401                 # the scheduler address is never open
    assert tc.post("/api/setup/dropbox-token", json={"code": "x"}, headers={"X-Requested-With": "remitwatch"}).status_code == 503
    monkeypatch.setenv("REMITWATCH_ALLOW_OPEN", "1")                    # a developer's own machine may opt in to running open
    assert tc.get("/api/summary").status_code == 200
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN")
    monkeypatch.setattr(api, "PASSWORD", "pw")
    assert tc.get("/api/summary").status_code == 401 and tc.get("/setup").status_code == 401
    assert tc.get("/api/summary", headers=auth).status_code == 200 and tc.get("/setup", headers=auth).status_code == 200
    h = tc.get("/api/health").json()
    assert h["mode"] == "serverless" and h["runtime"]["in_memory_store"] is True and h["selftest"]["ok"] is True
    assert tc.get("/robots.txt").text.startswith("User-agent: *\nDisallow: /")


def test_dashboard_reads_the_copy_and_changes_go_through_the_journal(site):
    tc, auth, _api, state = site
    xhr = dict(auth, **{"X-Requested-With": "remitwatch"})
    meta = tc.get("/api/meta", headers=auth).json()
    assert meta["mode"] == "serverless" and meta["remits"]["n"] == 6 and meta["lines"]["n"] == 53
    listing = tc.get("/api/findings?signal=DOWNCODE_LEVEL_REDUCED", headers=auth).json()
    assert listing["total"] == 3
    fid = listing["items"][0]["finding_id"]
    detail = tc.get(f"/api/findings/{fid}", headers=auth).json()
    assert detail["claim"]["patient"] == "PS" and "patient_last" not in detail["claim"] and len(detail["lines"]) == 1 and detail["remit"]
    assert tc.get("/api/breakdown?dim=payer&family=downcoding", headers=auth).json()["rows"][0]["lines"] == 53
    assert tc.post(f"/api/findings/{fid}/status", json={"status": "appealed", "notes": "faxed"}, headers=xhr).json() == {"ok": True}
    assert tc.get(f"/api/findings/{fid}", headers=auth).json()["finding"]["status"] == "appealed"
    assert len(state.list(cloud.ACTIONS)) == 1
    assert tc.post("/api/findings/nope/status", json={"status": "appealed"}, headers=xhr).status_code == 404
    others = [i["finding_id"] for i in listing["items"][1:]]
    assert tc.post("/api/findings/bulk-status", json={"ids": others + ["nope"], "status": "dismissed"}, headers=xhr).json()["updated"] == 2
    assert tc.get("/api/findings?signal=DOWNCODE_LEVEL_REDUCED&status=dismissed", headers=auth).json()["total"] == 2
    csv_text = tc.get("/api/findings.csv?status=all", headers=auth).text
    assert csv_text.splitlines()[0].startswith("finding_id,") and len(csv_text.splitlines()) > 3
    st = tc.get("/api/sync/status", headers=auth).json()
    assert st["mode"] == "serverless" and st["has_data"] and st["cron_ready"] and st["last_cycle"]["finished"] and not st["running"]
    assert st["memory_mb"]["peak"]


def test_scheduler_address_runs_one_cycle_and_needs_the_secret(site, monkeypatch):
    tc, auth, api, state = site
    assert tc.get("/api/cron/cycle").status_code == 401
    assert tc.get("/api/cron/cycle", headers={"Authorization": "Bearer wrong"}).status_code == 401
    calls = []
    monkeypatch.setattr(cloud, "trigger_cycle", lambda base, trigger="webhook", delay=0, **kw: calls.append((base, trigger, delay)) or True)
    cloud.record_action(state, {"kind": "alert_ack", "alert_id": "none"})
    out = tc.get("/api/cron/cycle", headers={"Authorization": "Bearer s3cret-value-for-the-scheduler"}).json()
    assert out["trigger"] == "cron" and out["actions_folded"] == 1 and out["more"] is False and calls == []
    assert out["ingest"] == {"skipped": "Dropbox credentials are not configured"}
    # the button starts a run through the same address, on the site's own public name
    xhr = dict(auth, **{"X-Requested-With": "remitwatch"})
    assert tc.post("/api/sync/run", headers=xhr).json() == {"started": True}
    assert calls == [("http://testserver", "button", 0)]
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "remitwatch.example.com")
    tc.post("/api/sync/run", headers=xhr)
    assert calls[-1][0] == "https://remitwatch.example.com"
    monkeypatch.setattr(cloud, "trigger_cycle", lambda *a, **k: False)
    assert tc.post("/api/sync/run", headers=xhr).status_code == 400
    # a logged-in person may also call it, but only with the header a cross-site page cannot send
    assert tc.post("/api/cron/cycle", headers=auth).status_code == 400
    assert tc.post("/api/cron/cycle", headers=xhr).status_code == 200


def test_webhook_is_verified_and_only_starts_a_run_for_new_files(site, monkeypatch):
    tc, _auth, api, _state = site
    monkeypatch.setenv("DROPBOX_APP_SECRET", "appsecret")
    assert tc.get("/api/dropbox/webhook?challenge=abc123").text == "abc123"
    body = b'{"list_folder": {"accounts": ["dbid:x"]}}'
    seen = []
    monkeypatch.setattr(api, "_webhook_work", lambda origin: seen.append(origin))
    assert tc.post("/api/dropbox/webhook", content=body, headers={"X-Dropbox-Signature": "bad"}).status_code == 403
    assert seen == []
    sig = hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()
    assert tc.post("/api/dropbox/webhook", content=body, headers={"X-Dropbox-Signature": sig}).text == "ok"
    assert seen == ["http://testserver"]
    # a check that hangs must not hold the reply back past Dropbox's limit, and an error in it is not Dropbox's problem
    monkeypatch.setattr(api, "_webhook_work", lambda origin: (_ for _ in ()).throw(RuntimeError("boom")))
    assert tc.post("/api/dropbox/webhook", content=body, headers={"X-Dropbox-Signature": sig}).status_code == 200


def test_setup_checklist_reports_what_is_missing(site, monkeypatch):
    tc, auth, _api, _state = site
    s = tc.get("/api/setup/status", headers=auth).json()
    by = {c["key"]: c for c in s["checks"]}
    assert by["password"]["ok"] and by["cron_secret"]["ok"] and by["dropbox_app"]["ok"] is False and by["runtime"]["ok"]
    assert s["ready"] is False and s["connect_needed"] is False and "authorize_url" not in s
    assert s["webhook_url"] == "http://testserver/api/dropbox/webhook" and s["has_data"] and s["last_cycle"]["finished"]
    monkeypatch.setenv("CRON_SECRET", "short")
    by = {c["key"]: c for c in tc.get("/api/setup/status", headers=auth).json()["checks"]}
    assert by["cron_secret"]["ok"] is False and "16 characters" in by["cron_secret"]["detail"]
    monkeypatch.setenv("DROPBOX_APP_KEY", "key123")
    monkeypatch.setenv("DROPBOX_APP_SECRET", "sec")
    s = tc.get("/api/setup/status", headers=auth).json()
    assert s["connect_needed"] is True and "client_id=key123" in s["authorize_url"] and "token_access_type=offline" in s["authorize_url"]
    xhr = dict(auth, **{"X-Requested-With": "remitwatch"})
    assert tc.post("/api/setup/dropbox-token", json={"code": ""}, headers=xhr).status_code == 400
    from remitwatch.dropbox_sync import DropboxClient
    monkeypatch.setattr(DropboxClient, "exchange_code", staticmethod(lambda k, s_, c: {"refresh_token": "RT", "scope": "account_info.read files.metadata.read files.content.read"}))
    got = tc.post("/api/setup/dropbox-token", json={"code": "abc"}, headers=xhr)
    assert got.json() == {"refresh_token": "RT", "missing_permissions": ["files.content.write"]} and got.headers["cache-control"] == "no-store"


def test_missing_permission_is_reported_in_plain_words():
    from remitwatch.dropbox_sync import DropboxAuthError, DropboxClient

    class Reply:
        status_code = 401
        headers: dict = {}
        text = json.dumps({"error_summary": "missing_scope/..", "error": {".tag": "missing_scope", "required_scope": "files.content.write"}})

    class Session:
        calls = 0

        def post(self, *a, **k):
            Session.calls += 1
            return Reply()

    c = DropboxClient("k", "s", "r", session=Session())
    c._token, c._token_exp = "t", time.time() + 3600
    with pytest.raises(DropboxAuthError) as err:
        c.upload("/x", b"1")
    assert "files.content.write" in str(err.value) and Session.calls == 1        # no retry: it cannot succeed


# --------------------------------------------------------------------------- what an independent review found

def test_an_unreadable_file_is_remembered_and_starts_nothing_again(box, monkeypatch):
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    time.sleep(0.01)
    (root / DATA / "Claim Status 11-2-26.xlsx").write_bytes(b"this is not a spreadsheet")
    position, store_rev = state.get(cloud.CURSOR), state.meta(cloud.RAW)["rev"]
    assert cloud.has_new_files(state, client) is True
    out = cycle(state, client)
    assert out["ingest"]["errors"] == 1 and out["ingest"]["failed"] == ["Claim Status 11-2-26.xlsx (could not be read)"]
    assert out["ingest"]["download_errors"] == 0 and "detect" not in out and out["more"] is False
    # the position moved past it and the store remembers it, so neither the next run nor a notification asks for it again
    assert state.get(cloud.CURSOR) != position and state.meta(cloud.RAW)["rev"] != store_rev
    assert cloud.has_new_files(state, client) is False
    downloads = client.calls["download"]
    again = cycle(state, client)
    assert again["ingest"]["downloaded"] == 0 and again["ingest"]["errors"] == 0
    assert client.calls["download"] - downloads <= 8                 # the store and a few small state files, never the bad file
    # a file far larger than any remittance is not read at all
    monkeypatch.setattr(cloud, "MAX_FILE_BYTES", 200)
    time.sleep(0.01)
    write_remits(root / DATA, clean_visits(500, 3, "99213", "156.66", "76.09", cfi="16"), prefix="huge", start_ctrl=120)
    big = cycle(state, client)
    assert big["ingest"]["failed"] == ["huge000.RMT (too large)"] and big["ingest"]["downloaded"] == 0 and big["ingest"]["remits"] == 0
    assert cloud.has_new_files(state, client) is False


def test_a_download_that_may_work_later_holds_the_position_and_one_that_cannot_does_not(box):
    import requests
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    time.sleep(0.01)
    write_remits(root / DATA, [claim(300, svc("99214", "223.65", "76.09", "76.09", "CAS*CO*186*51.71~CAS*CO*45*95.85~"), cfi="16")], prefix="late", start_ctrl=50)
    position = state.get(cloud.CURSOR)
    client.broken = {"late000": requests.exceptions.ConnectionError("connection dropped")}
    out = cycle(state, client)
    assert out["ingest"]["download_errors"] == 1 and out["ingest"]["remits"] == 0 and state.get(cloud.CURSOR) == position
    client.broken = {}
    out = cycle(state, client)
    assert out["ingest"]["remits"] == 1 and out["detect"]["findings"] >= 4 and state.get(cloud.CURSOR) != position
    # Dropbox refusing the file itself is final: it is noted, remembered and passed over
    time.sleep(0.01)
    write_remits(root / DATA, clean_visits(600, 1, "99213", "156.66", "76.09", cfi="16"), prefix="locked", start_ctrl=60)
    client.broken = {"locked000": RuntimeError("download id:x failed 409: path/restricted_content/")}
    position = state.get(cloud.CURSOR)
    out = cycle(state, client)
    assert out["ingest"]["failed"] == ["locked000.RMT (cannot be downloaded)"] and out["ingest"]["download_errors"] == 0
    assert state.get(cloud.CURSOR) != position and cloud.has_new_files(state, client) is False


def test_the_store_is_saved_before_the_dashboard_copy_and_a_copy_left_behind_is_rebuilt(box, monkeypatch):
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    rev = state.meta(cloud.SERVING)["rev"]
    assert json.loads(state.get(cloud.SERVING_MARK))["data_rev"] == "1"
    time.sleep(0.01)
    write_remits(root / DATA, [claim(300, svc("99214", "223.65", "76.09", "76.09", "CAS*CO*186*51.71~CAS*CO*45*95.85~"), cfi="16")], prefix="new", start_ctrl=50)
    real = cloud.save_serving
    monkeypatch.setattr(cloud, "save_serving", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("upload failed")))
    with pytest.raises(RuntimeError):
        cycle(state, client)
    # the run died after the store was saved: the record has the new finding, the dashboard still shows the old copy
    assert raw_conn(state).execute("SELECT COUNT(*) FROM findings WHERE signal='DOWNCODE_LEVEL_REDUCED'").fetchone()[0] == 4
    assert state.meta(cloud.SERVING)["rev"] == rev and json.loads(state.get(cloud.STATUS))["error"].startswith("RuntimeError")
    assert state.meta(cloud.LOCK) is None
    monkeypatch.setattr(cloud, "save_serving", real)
    out = cycle(state, client)      # nothing new to read, but the copy is behind the store: it is rebuilt
    assert out["ingest"]["remits"] == 0 and "detect" not in out and "serving" in out and "store" not in out
    assert state.meta(cloud.SERVING)["rev"] != rev and json.loads(state.get(cloud.SERVING_MARK))["data_rev"] == "2"
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    assert cloud.ServingCopy().connection().execute("SELECT COUNT(*) FROM findings WHERE signal='DOWNCODE_LEVEL_REDUCED'").fetchone()[0] == 4
    assert "serving" not in cycle(state, client)        # and not rebuilt again after that


def test_a_status_for_a_finding_the_store_does_not_hold_is_kept_not_dropped(box):
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    fid = raw_conn(state).execute("SELECT finding_id FROM findings WHERE signal='DOWNCODE_LEVEL_REDUCED' LIMIT 1").fetchone()[0]
    cloud.record_action(state, {"kind": "status", "finding_id": fid, "status": "appealed", "actor": "biller"})
    kept = cloud.record_action(state, {"kind": "status", "finding_id": "not-in-the-store-yet", "status": "appealed", "actor": "biller"})
    cloud.record_action(state, {"kind": "bulk_status", "ids": [fid, "also-missing"], "status": "in_progress", "actor": "biller"})
    old = f"{cloud.ACTIONS}/20200101T000000000000-abc123.json"       # recorded long ago and never matched: given up
    state.put(old, json.dumps({"kind": "status", "finding_id": "gone-for-good", "status": "lost", "at": "2020-01-01T00:00:00+00:00"}).encode())
    out = cycle(state, client)
    assert out["actions_folded"] == 1 and out["actions_waiting"] == 3
    left = sorted(m["name"] for m in state.list(cloud.ACTIONS))
    assert len(left) == 2 and kept in left and old not in left
    assert client.calls.get("delete_batch", 0) >= 1                  # cleared in one call, not one call per entry
    assert raw_conn(state).execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0] == "in_progress"
    assert cloud.expired(old) and not cloud.expired(kept)


def test_alert_mail_and_the_weekly_report_go_out_once(box, monkeypatch):
    from remitwatch import report
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    sent: list[str] = []
    working = {"on": False}

    def fake_email(text, subject="RemitWatch summary"):
        if not working["on"]:
            return False                                             # mail is not set up yet
        sent.append(subject)
        return True
    monkeypatch.setattr(report, "email_report", fake_email)
    run = lambda: cloud.run_cloud_cycle(state, client, budget_s=600, email=True, log=lambda *a: None)      # noqa: E731
    first = run()
    assert first["alerts"]["new"] >= 1 and first["notified"]["sent"] == 0 and sent == []
    working["on"] = True                                             # mail is set up a day later
    second = run()
    assert second["notified"]["sent"] >= 1 and len(sent) == 1 and sent[0].startswith("RemitWatch alert")
    assert "store" not in second                                     # an idle run: what was mailed is kept in its own small file
    marks = json.loads(state.get(cloud.MARKS))
    assert marks["alerts"] and marks["last_report"]
    for _ in range(3):
        again = run()
        assert again["notified"] == {"sent": 0}
    assert len(sent) == 1                                            # four idle runs later, still one mail
    # the report is due again after a week, and then goes out once
    marks["last_report"] = "2026-01-01T00:00:00+00:00"
    state.put(cloud.MARKS, json.dumps(marks).encode())
    run()
    run()
    assert [s for s in sent if "summary" in s.lower()] == ["RemitWatch summary"] and len(sent) == 2


def test_notifications_do_not_restart_a_run_that_failed_or_died(box):
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    time.sleep(0.01)
    write_remits(root / DATA, clean_visits(700, 1, "99213", "156.66", "76.09", cfi="16"), prefix="more", start_ctrl=80)
    assert cloud.webhook_should_start(state, client) is True
    ok = json.loads(state.get(cloud.STATUS))
    state.put(cloud.STATUS, json.dumps(dict(ok, error="OperationalError: boom", finished=cloud._now())).encode())
    assert cloud.webhook_should_start(state, client) is False        # the run just failed: its own writes must not restart it
    state.put(cloud.STATUS, json.dumps(dict(ok, error="OperationalError: boom", finished="2026-01-01T00:00:00+00:00")).encode())
    assert cloud.webhook_should_start(state, client) is True         # long ago: worth another try
    token = cloud.acquire(state)
    assert cloud.webhook_should_start(state, client) is False        # a run is going
    old = time.time() - cloud.lock_stale_seconds() - 5
    os.utime(os.path.join(str(root), "RemitWatch-state", cloud.LOCK), (old, old))
    assert cloud.webhook_should_start(state, client) is False        # a run died: left to the daily run or a person
    cloud.release(state, token)
    # a check that cannot be made is not a reason to start a run, unless Dropbox says the position is gone
    real = client._rpc
    client._rpc = lambda endpoint, body: (_ for _ in ()).throw(RuntimeError("Dropbox files/list_folder/continue failed 500"))
    assert cloud.has_new_files(state, client) is False
    client._rpc = lambda endpoint, body: (_ for _ in ()).throw(RuntimeError("Dropbox files/list_folder/continue failed 409: reset/"))
    assert cloud.has_new_files(state, client) is True
    client._rpc = real


def test_runs_follow_one_another_only_so_many_times_and_only_while_getting_somewhere(site, monkeypatch):
    tc, _auth, _api, _state = site
    bearer = {"Authorization": "Bearer s3cret-value-for-the-scheduler"}
    calls = []
    monkeypatch.setattr(cloud, "trigger_cycle", lambda base, trigger="webhook", delay=0, **kw: calls.append((trigger, delay, kw)) or True)
    monkeypatch.setattr(cloud, "run_cloud_cycle", lambda *a, **k: {"more": True, "progress": True})
    assert tc.get("/api/cron/cycle?hop=3", headers=bearer).json()["continued"] is True
    assert calls == [("continue", 0, {"hop": 4})]
    halted = tc.get(f"/api/cron/cycle?hop={cloud.MAX_HOPS}", headers=bearer).json()
    assert "halted" in halted and "continued" not in halted and len(calls) == 1
    monkeypatch.setattr(cloud, "run_cloud_cycle", lambda *a, **k: {"more": True, "progress": False})
    assert "continued" not in tc.get("/api/cron/cycle", headers=bearer).json() and len(calls) == 1


def test_dropbox_client_asks_again_when_told_to_and_fails_fast_when_asked_to(monkeypatch):
    from remitwatch.dropbox_sync import DropboxClient
    from remitwatch.state import state_from_env

    class Reply:
        def __init__(self, status, text):
            self.status_code, self.text, self.headers = status, text, {}

        def json(self):
            return json.loads(self.text)

    class Session:
        def __init__(self, replies):
            self.replies, self.calls = list(replies), 0

        def post(self, *a, **k):
            self.calls += 1
            return self.replies.pop(0)

    monkeypatch.setattr(time, "sleep", lambda s: None)
    busy = Reply(409, '{"error_summary": "too_many_write_operations/..", "error": {".tag": "too_many_write_operations"}}')
    s = Session([busy, busy, Reply(200, '{"rev": "0123456789ab", "size": 1}')])
    c = DropboxClient("k", "s", "r", session=s)
    c._token, c._token_exp = "t", time.time() + 3600
    assert c.upload("/x", b"1")["rev"] == "0123456789ab" and s.calls == 3
    s = Session([Reply(503, "busy")])
    quick = DropboxClient("k", "s", "r", session=s, attempts=1, timeout=8)
    quick._token, quick._token_exp = "t", time.time() + 3600
    with pytest.raises(RuntimeError):
        quick.get_metadata("/x")
    assert s.calls == 1
    for k, v in (("DROPBOX_APP_KEY", "k"), ("DROPBOX_APP_SECRET", "s"), ("DROPBOX_REFRESH_TOKEN", "r")):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("REMITWATCH_STATE_DIR", raising=False)
    assert state_from_env(fast=True).client.attempts == 1 and state_from_env().client.attempts == 6
