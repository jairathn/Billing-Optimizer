"""Keeping the lists current: remittances straight from TriZetto's SFTP, a run when the dashboard opens on old data, no
appeal faxed while the remittances are behind, and a reason hidden for a payer (Alex: "we know which payers don't pay
that")."""
from __future__ import annotations

import os
import time

import pytest

from remitwatch import trizetto
from tests.fake_sftp import FakeSFTP
from tests.test_features import NPI, build_store, claim, clean_visits, svc
from tests.test_remitwatch import remit

XHR = {"X-Requested-With": "remitwatch"}


def _remits(root, n: int = 2, start: int = 500, pay_date: str = "20261118", folder: str = "remits") -> list[str]:
    """Remittance files the way TriZetto writes them, in a folder of the SFTP account."""
    d = os.path.join(root, folder)
    os.makedirs(d, exist_ok=True)
    names = []
    for i in range(n):
        body = remit(claim(start + i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20261101"), dos="20261101"),
                     "117.60", seg_count=99, isa_ctrl=f"{start + i:09d}", pay_date=pay_date)
        name = f"remit_{start + i}.RMT"
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(body)
        names.append(name)
    return names


@pytest.fixture()
def sftp(tmp_path):
    root = tmp_path / "trizetto"
    root.mkdir()
    f = FakeSFTP(str(root))
    yield f
    f.stop()


def _ingest_into(store):
    from remitwatch.cloud import _ingest_batch
    return lambda items: _ingest_batch(store, items, lambda *a: None)


def test_remittances_are_read_from_trizetto_once_and_nothing_there_is_touched(tmp_path, sftp):
    from remitwatch.store import Store
    _remits(sftp.root, 2)
    with open(os.path.join(sftp.root, "remits", "batch.999"), "wb") as fh:          # an acknowledgement: not read
        fh.write(b"ISA*00*...")
    with open(os.path.join(sftp.root, "notes.pdf"), "wb") as fh:
        fh.write(b"%PDF-")
    s = trizetto.Settings.from_env(sftp.env())
    store = Store(str(tmp_path / "s.sqlite"))
    out = trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=s, log=lambda *a: None)
    assert out["listed"] == 2 and out["new"] == 2 and out["downloaded"] == 2 and out["remits"] == 2 and out["errors"] == 0
    assert out["hostkey"].startswith("SHA256:")                   # kept: from now on only this key is trusted
    assert store.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    assert sorted(sftp.reads) == ["/remits/remit_500.RMT", "/remits/remit_501.RMT"] and sftp.writes == []
    # the next run reads nothing again
    again = trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=s, log=lambda *a: None)
    assert again["new"] == 0 and again["downloaded"] == 0 and len(sftp.reads) == 2
    # a new file, and one TriZetto wrote again (a new size and time), are read; the same payment is stored once
    _remits(sftp.root, 1, start=502)
    p = os.path.join(sftp.root, "remits", "remit_500.RMT")
    with open(p, "ab") as fh:
        fh.write(b"\n")
    os.utime(p, (time.time() + 60, time.time() + 60))
    third = trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=s, log=lambda *a: None)
    assert third["new"] == 2 and third["remits"] >= 1
    assert store.conn.execute("SELECT COUNT(*) FROM transactions WHERE superseded=0").fetchone()[0] == 3
    store.close()


def test_a_server_with_another_key_or_a_wrong_password_gets_nothing(tmp_path, sftp):
    from remitwatch.store import Store
    _remits(sftp.root, 1)
    store = Store(str(tmp_path / "s.sqlite"))
    trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=trizetto.Settings.from_env(sftp.env()), log=lambda *a: None)
    other = FakeSFTP(sftp.root, key=1)          # someone else answering at TriZetto's address
    try:
        with pytest.raises(trizetto.TriZettoError, match="not the one seen before"):
            trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=trizetto.Settings.from_env(other.env()), log=lambda *a: None)
        assert other.logins == []               # the password was never sent to it
        # a key given in the settings is the only one trusted, even on a first run
        pinned = trizetto.Settings.from_env(other.env(TRIZETTO_SFTP_HOSTKEY="SHA256:not-this-one"))
        with pytest.raises(trizetto.TriZettoError, match="TRIZETTO_SFTP_HOSTKEY"):
            with trizetto.Session(pinned):
                pass
    finally:
        other.stop()
    sftp.refuse_password = True
    with pytest.raises(trizetto.TriZettoError, match="Site ID"):
        trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=trizetto.Settings.from_env(sftp.env()), log=lambda *a: None)
    store.close()


def test_a_refused_password_is_not_tried_again_so_the_login_is_never_locked(tmp_path, sftp):
    """TriZetto locks a login after repeated failures, and the login may be the one ModMed uses: a refused user name and
    password are not tried again for a day, a new password is tried at once, and a sign-in that works clears the hold."""
    import json
    from datetime import datetime, timedelta, timezone

    from remitwatch.store import Store
    _remits(sftp.root, 1)
    store = Store(str(tmp_path / "s.sqlite"))
    pull = lambda env: trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=trizetto.Settings.from_env(env), log=lambda *a: None)
    sftp.refuse_password = True
    with pytest.raises(trizetto.SignInRefused):
        pull(sftp.env())
    assert len(sftp.logins) == 1
    for _ in range(3):                                          # every cycle after it: no sign-in at all
        out = pull(sftp.env())
        assert out["skipped"].startswith("TriZetto refused this user name and password") and out["downloaded"] == 0
    assert len(sftp.logins) == 1
    raw = store.conn.execute("SELECT value FROM meta WHERE key=?", (trizetto.REFUSED_META,)).fetchone()[0]
    assert sftp.password not in raw                             # only a hash of the settings is kept
    # a day later the same password is tried once (TriZetto may have turned SFTP on since); refused again, held again
    held = json.loads(raw)
    held["at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
    store.conn.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(held), trizetto.REFUSED_META))
    with pytest.raises(trizetto.SignInRefused):
        pull(sftp.env())
    assert len(sftp.logins) == 2 and pull(sftp.env())["skipped"]
    # a new password is tried at once; once a sign-in works, the hold is gone
    sftp.refuse_password = False
    sftp.password = "the-new-one-from-the-portal"
    out = pull(sftp.env(TRIZETTO_SFTP_PASSWORD="the-new-one-from-the-portal"))
    assert len(sftp.logins) == 3 and out["remits"] == 1 and "skipped" not in out
    assert store.conn.execute("SELECT 1 FROM meta WHERE key=?", (trizetto.REFUSED_META,)).fetchone() is None
    store.close()


def test_the_setup_check_walks_every_step(sftp):
    assert trizetto.check({})["steps"][0]["ok"] is False
    empty = trizetto.check(sftp.env())
    assert empty["ok"] is False and "Auto Generate Remittances" in empty["steps"][-1]["detail"]
    _remits(sftp.root, 2)
    ok = trizetto.check(sftp.env())
    assert ok["ok"] is True and [s["step"] for s in ok["steps"]] == ["Settings", "Server", "Sign-in", "Files", "Read"]
    assert "2 files (2 remittances)" in ok["steps"][3]["detail"] and ok["hostkey"].startswith("SHA256:")
    sftp.refuse_password = True
    bad = trizetto.check(sftp.env())
    assert bad["ok"] is False and "Site ID" in bad["steps"][-1]["detail"]


def test_a_serverless_run_reads_trizetto_without_dropbox(tmp_path, sftp, monkeypatch):
    from remitwatch import cloud
    from tests.fakes import FakeDropbox
    from remitwatch.state import DropboxState
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "ALERT_WEBHOOK_URL", "REPORT_TO", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    for k, v in sftp.env().items():
        monkeypatch.setenv(k, v)
    # enough clean visits for a baseline, and one denial, all from TriZetto
    d = os.path.join(sftp.root, "remits")
    os.makedirs(d)
    claims = clean_visits(1, 25, "99214", "223.65", "117.60") + [claim(300, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))]
    for n, i in enumerate(range(0, len(claims), 10)):
        with open(os.path.join(d, f"r{n}.RMT"), "wb") as fh:
            fh.write(remit("".join(claims[i:i + 10]), "100.00", seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date="20261110"))
    state = DropboxState(FakeDropbox(str(tmp_path / "dbx")), "/RemitWatch-state")
    out = cloud.run_cloud_cycle(state, None, budget_s=600, email=False, log=lambda *a: None)
    assert out["trizetto"]["remits"] == 3 and "error" not in out["trizetto"] and out["detect"]["findings"] >= 1
    conn = cloud.load_raw(state)[0].conn
    assert conn.execute("SELECT COUNT(*) FROM trizetto_seen").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM findings WHERE signal='DENIED_MEDICAL_NECESSITY'").fetchone()[0] == 1
    # the next run finds nothing new and saves nothing
    again = cloud.run_cloud_cycle(state, None, budget_s=600, email=False, log=lambda *a: None)
    assert again["trizetto"]["new"] == 0 and "detect" not in again


# --------------------------------------------------------------------------- the dashboard and the appeals
@pytest.fixture()
def site(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api_mod
    from remitwatch.detect import Detector
    denied = lambda i: claim(i, svc("99214:57", "223.65", "0.00", None, "CAS*CO*97*223.65~", dos="20261101"), dos="20261101")   # noqa: E731
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60")
                        + [claim(200, svc("17311", "900.00", "600.00", "600.00", "CAS*CO*45*300.00~", dos="20261101"), dos="20261101")]
                        + [denied(300), denied(301)]
                        + [claim(400, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20261101"), dos="20261101")], pay_date="20261110")
    Detector(store, run_id="t").run()
    store.close()
    monkeypatch.setattr(api_mod, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    for k in ("TRIZETTO_SFTP_USER", "TRIZETTO_SFTP_PASSWORD", "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    return TestClient(api_mod.app), api_mod, tmp_path


def _items(c, signal):
    return c.get(f"/api/findings?status=all&signal={signal}&limit=50").json()["items"]


def test_a_reason_hidden_for_a_payer_leaves_the_lists_and_stays_off(site):
    import sqlite3

    from remitwatch.detect import Detector
    from remitwatch.store import Store
    c, api_mod, tmp_path = site
    hidden_sig = next(r["signal"] for r in c.get("/api/findings?status=all&limit=200").json()["items"] if r["patient_control_number"] == "CB0000300C017")
    items = _items(c, hidden_sig)
    assert len(items) == 2 and {i["status"] for i in items} == {"open"}
    payer = items[0]["payer_group"]
    # someone already started one of them: theirs stays
    worked = next(i for i in items if i["patient_control_number"] == "CB0000301C017")["finding_id"]
    assert c.post(f"/api/findings/{worked}/status", json={"status": "in_progress"}, headers=XHR).status_code == 200
    assert c.post("/api/hide", json={"payer_group": payer, "signal": "NOPE"}, headers=XHR).status_code == 404
    assert c.post("/api/hide", json={"payer_group": payer, "signal": hidden_sig}).status_code == 400          # cross-site
    r = c.post("/api/hide", json={"payer_group": payer, "signal": hidden_sig, "note": f"{payer} never pays modifier 57"}, headers=XHR).json()
    assert r == {"ok": True, "hidden": 1}
    st = {i["patient_control_number"]: i["status"] for i in _items(c, hidden_sig)}
    assert st == {"CB0000300C017": "dismissed", "CB0000301C017": "in_progress"}
    d = c.get(f"/api/findings/{next(i['finding_id'] for i in _items(c, hidden_sig) if i['status'] == 'dismissed')}").json()
    assert d["events"][0]["kind"] == "hidden" and d["events"][0]["note"] == f"{payer} never pays modifier 57"
    rules = c.get("/api/hide").json()["rules"]
    assert len(rules) == 1 and rules[0]["n"] == 1 and rules[0]["label"]
    # a new denial of that kind from that payer is hidden by the next analysis; the person's item still stands
    store = build_store(tmp_path, [claim(302, svc("99214:57", "223.65", "0.00", None, "CAS*CO*97*223.65~", dos="20261102"), dos="20261102")], pay_date="20261112")
    Detector(store, run_id="t2").run()
    store.close()
    st = {i["patient_control_number"]: i["status"] for i in _items(c, hidden_sig)}
    assert st == {"CB0000300C017": "dismissed", "CB0000301C017": "in_progress", "CB0000302C017": "dismissed"}
    # shown again: the ones it hid come back, the person's is as they left it
    assert c.post("/api/hide", json={"payer_group": payer, "signal": hidden_sig, "remove": True}, headers=XHR).json()["ok"] is True
    st = {i["patient_control_number"]: i["status"] for i in _items(c, hidden_sig)}
    assert st == {"CB0000300C017": "open", "CB0000301C017": "in_progress", "CB0000302C017": "open"}
    assert c.get("/api/hide").json()["rules"] == []
    # and nothing it did counts as work people did today
    conn = sqlite3.connect(str(tmp_path / "s.sqlite"))
    assert conn.execute("SELECT COUNT(*) FROM finding_events WHERE kind='manual' AND actor='hide rule'").fetchone()[0] == 0
    conn.close()
    _ = Store


def test_no_appeal_is_faxed_while_the_remittances_are_behind(site, monkeypatch):
    c, api_mod, _ = site
    fid = next(i["finding_id"] for i in c.get("/api/findings?status=all&limit=200").json()["items"] if i["patient_control_number"] == "CB0000400C017")
    monkeypatch.setattr(api_mod, "STALE_APPEAL_DAYS", 3)        # the newest payment here is ten days old
    r = c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    assert r["status"] == "held" and r["code"] == "stale" and "days old" in r["reason"]
    # told it is still unpaid, the next check speaks (here: the practice's details are missing)
    r = c.post("/api/appeals/send", json={"finding_id": fid, "stale_ok": True}, headers=XHR).json()
    assert r["code"] != "stale"


def test_opening_the_dashboard_on_old_data_starts_a_run(site, monkeypatch):
    c, api_mod, _ = site
    assert c.post("/api/sync/freshen", headers=XHR).json() == {"started": False, "reason": "no feed is set up"}
    monkeypatch.setenv("TRIZETTO_SFTP_USER", "SITE123")
    monkeypatch.setenv("TRIZETTO_SFTP_PASSWORD", "pw")
    started = []
    import remitwatch.pipeline as P
    monkeypatch.setattr(P, "run_once_in_background", lambda db, trigger="button": started.append(trigger))
    r = c.post("/api/sync/freshen", headers=XHR).json()
    assert r["started"] is True and started == ["freshen"]
    # a run a few minutes ago: nothing to do
    monkeypatch.setattr(api_mod, "_last_pull_hours", lambda: 0.5)
    assert c.post("/api/sync/freshen", headers=XHR).json()["started"] is False and started == ["freshen"]
    assert c.post("/api/sync/freshen").status_code == 400          # only the site's own pages start it
    assert c.get("/api/meta").json()["trizetto"] is True


def test_the_data_checks_warn_before_the_trizetto_password_expires_and_while_a_sign_in_is_held(tmp_path):
    import json
    import sqlite3

    from remitwatch.insights import trizetto_health
    conn = sqlite3.connect(str(tmp_path / "m.sqlite"))
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    env = {"TRIZETTO_SFTP_USER": "SITE", "TRIZETTO_SFTP_PASSWORD": "x", "TRIZETTO_SFTP_PASSWORD_SET": "2026-10-01"}
    assert trizetto_health(conn, "2026-11-21", dict(env, TRIZETTO_SFTP_PASSWORD_DAYS="365")) == []
    soon = trizetto_health(conn, "2026-11-21", dict(env, TRIZETTO_SFTP_PASSWORD_DAYS="60"))
    assert [(h["key"], h["due"], h["days_left"]) for h in soon] == [("trizetto_password", "2026-11-30", 9)]
    assert "Manage SFTP Password" in soon[0]["who"]
    gone = trizetto_health(conn, "2026-12-05", dict(env, TRIZETTO_SFTP_PASSWORD_DAYS="60"))
    assert gone[0]["title"] == "TriZetto's SFTP password expired on 2026-11-30"
    conn.execute("INSERT INTO meta VALUES (?, ?)", (trizetto.REFUSED_META, json.dumps({"who": "h", "at": "2026-11-20T08:00:00+00:00"})))
    assert [h["key"] for h in trizetto_health(conn, "2026-11-21", env)] == ["trizetto_signin"]
    assert trizetto_health(conn, "2026-11-21", {}) == []                 # no feed set up: nothing to say
