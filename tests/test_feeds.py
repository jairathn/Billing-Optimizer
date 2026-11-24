"""Keeping the lists current: remittances straight from TriZetto's SFTP, what a Dropbox sync hands over (and what it leaves
unread), a run when the dashboard opens on old data, no appeal faxed while the remittances are behind, and a reason hidden
for a payer (the biller: "we know which payers don't pay that")."""
from __future__ import annotations

import json
import os
import re
import threading
import time

import pytest

from remitwatch import trizetto
from tests.test_features import NPI, build_store, claim, clean_visits, svc
from tests.test_remitwatch import remit

XHR = {"X-Requested-With": "remitwatch"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fake_sftp():
    """The stand-in SFTP server (tests/fake_sftp.py) needs paramiko. Imported here, not at the top, so that without
    paramiko only the tests that need a server are skipped, not every test in this file."""
    pytest.importorskip("paramiko")
    from tests import fake_sftp
    return fake_sftp


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
    f = _fake_sftp().FakeSFTP(str(root))
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
    other = _fake_sftp().FakeSFTP(sftp.root, key=1)          # someone else answering at TriZetto's address
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
    with pytest.raises(trizetto.SignInRefused, match="refused the password") as err:
        trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=trizetto.Settings.from_env(sftp.env()), log=lambda *a: None)
    assert err.value.kind == "password" and "Site ID" not in str(err.value)
    store.close()


def test_the_fingerprints_trizetto_gives_are_accepted_however_they_are_written(sftp):
    """A server has one key per type, and TriZetto may give them all, with their type, or in the older MD5 form."""
    with trizetto.Session(trizetto.Settings.from_env(sftp.env())) as ses:
        key = ses.transport.get_remote_server_key()
    sha, md5 = trizetto.fingerprint(key), next(f for f in trizetto.fingerprints_of(key) if f.startswith("MD5:"))
    for given in (f"ED25519 SHA256:AAAA, RSA {sha}", f"{sha}=", md5.upper().replace("MD5:", "MD5:"), md5[4:], f"(ECDSA) SHA256:BBBB; {md5}"):
        with trizetto.Session(trizetto.Settings.from_env(sftp.env(TRIZETTO_SFTP_HOSTKEY=given))) as ses:
            assert ses.key_fp == sha
    with pytest.raises(trizetto.TriZettoError, match="expected SHA256:AAAA or SHA256:BBBB"):
        with trizetto.Session(trizetto.Settings.from_env(sftp.env(TRIZETTO_SFTP_HOSTKEY="SHA256:AAAA, SHA256:BBBB"))):
            pass
    assert trizetto.pinned("ssh-ed25519 255 SHA256:abc= no-fingerprint-here") == ["SHA256:abc"]


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
    # an empty folder on a new login: check the folder TriZetto named, and change nothing in the portal (ModMed's settings)
    told = empty["steps"][-1]["detail"]
    assert empty["ok"] is False and "TRIZETTO_SFTP_DIRS" in told and "in writing" in told
    assert "Do not change the Remittance Advice Settings" in told and "turn on" not in told
    _remits(sftp.root, 2)
    ok = trizetto.check(sftp.env())
    assert ok["ok"] is True and [s["step"] for s in ok["steps"]] == ["Settings", "Server", "Sign-in", "Files", "Read"]
    assert "2 files (2 remittances)" in ok["steps"][3]["detail"] and ok["hostkey"].startswith("SHA256:")
    assert "Site ID" not in ok["steps"][0]["detail"] + ok["steps"][2]["detail"]
    sftp.refuse_password = True
    bad = trizetto.check(sftp.env())
    said = bad["steps"][-1]["detail"]
    assert bad["ok"] is False and bad["refused_kind"] == "password" and "refused the password" in said
    assert "in writing" in said and "RemitWatch login's username, never the Site ID's" in said and "never by email" in said


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
    n = c.get("/api/hide/preview", params={"payer_group": payer, "signal": hidden_sig}).json()["n"]       # shown first (hotfix 2, H7)
    r = c.post("/api/hide", json={"payer_group": payer, "signal": hidden_sig, "note": f"{payer} never pays modifier 57", "confirm": n},
               headers=XHR).json()
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
    who = soon[0]["who"]
    assert "in writing" in who and "RemitWatch login's username" in who and "never the Site ID's" in who and "never by email" in who
    assert "Manage SFTP Password" not in who and "configure.sh" in who
    gone = trizetto_health(conn, "2026-12-05", dict(env, TRIZETTO_SFTP_PASSWORD_DAYS="60"))
    assert gone[0]["title"] == "TriZetto's SFTP password expired on 2026-11-30"
    conn.execute("INSERT INTO meta VALUES (?, ?)", (trizetto.REFUSED_META, json.dumps({"who": "h", "at": "2026-11-20T08:00:00+00:00"})))
    assert [h["key"] for h in trizetto_health(conn, "2026-11-21", env)] == ["trizetto_signin"]
    assert trizetto_health(conn, "2026-11-21", {}) == []                 # no feed set up: nothing to say


def test_a_slow_or_dropped_sign_in_is_the_network_not_a_refused_password(tmp_path, sftp, monkeypatch):
    """A sign-in TriZetto is slow to answer (past the client's timeout) says nothing about the password: no hold, and the next
    run reads the files."""
    from remitwatch.store import Store
    _Server = _fake_sftp()._Server
    _remits(sftp.root, 1)
    store = Store(str(tmp_path / "s.sqlite"))
    slow = {"on": True}
    orig_init, orig_auth = trizetto.Session.__init__, _Server.check_auth_password

    def check_auth_password(self, username, password):
        if slow["on"]:
            time.sleep(3)
        return orig_auth(self, username, password)
    monkeypatch.setattr(trizetto.Session, "__init__", lambda self, s, known=None, timeout=1: orig_init(self, s, known=known, timeout=timeout))
    monkeypatch.setattr(_Server, "check_auth_password", check_auth_password)
    pull = lambda: trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=trizetto.Settings.from_env(sftp.env()), log=lambda *a: None)
    with pytest.raises(trizetto.TriZettoError) as err:
        pull()
    assert not isinstance(err.value, trizetto.SignInRefused) and "Nothing was concluded about the password" in str(err.value)
    assert trizetto.refusal(store.conn) is None
    slow["on"] = False
    assert pull()["remits"] == 1
    store.close()


def test_a_serverless_run_keeps_the_hold_so_a_refused_password_is_sent_once_a_day(tmp_path, monkeypatch):
    from remitwatch import cloud
    from remitwatch.state import DropboxState
    from tests.fakes import FakeDropbox
    root = tmp_path / "trizetto"
    root.mkdir()
    f = _fake_sftp().FakeSFTP(str(root))
    try:
        monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
        for k, v in f.env().items():
            monkeypatch.setenv(k, v)
        _remits(str(root), 2)
        state = DropboxState(FakeDropbox(str(tmp_path / "dbx")), "/RemitWatch-state")
        run = lambda: cloud.run_cloud_cycle(state, None, budget_s=600, email=False, log=lambda *a: None)
        assert run()["trizetto"]["remits"] == 2
        run()
        f.refuse_password = True
        before = len(f.logins)
        outs = [run() for _ in range(4)]                 # Sync now presses, the dashboard opening, the daily run
        assert len(f.logins) - before == 1               # one refused sign-in, then the saved hold keeps the rest off
        assert "error" in outs[0]["trizetto"] and all(o["trizetto"].get("skipped") for o in outs[1:])
    finally:
        f.stop()


def test_a_keyboard_style_sign_in_is_judged_the_same_way_and_a_refused_file_service_holds_nothing(tmp_path, sftp, monkeypatch):
    """Many SFTP servers ask for the password as a typed answer: slow there is still the network, not a refusal. And a
    sign-in that works but whose file service is refused ends a hold: the password is fine."""
    import paramiko

    from remitwatch.store import Store
    _Server = _fake_sftp()._Server
    _remits(sftp.root, 1)
    store = Store(str(tmp_path / "s.sqlite"))
    slow = {"on": True}

    def check_auth_interactive(self, username, submethods):
        self._user = username
        self.fake.logins.append(username)
        return paramiko.InteractiveQuery("", "", ("Password: ", False))

    def check_auth_interactive_response(self, responses):
        if slow["on"]:
            time.sleep(3)
        ok = self._user == self.fake.user and responses and responses[0] == self.fake.password and not self.fake.refuse_password
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED
    monkeypatch.setattr(_Server, "get_allowed_auths", lambda self, username: "keyboard-interactive")
    monkeypatch.setattr(_Server, "check_auth_password", lambda self, username, password: paramiko.AUTH_FAILED)
    monkeypatch.setattr(_Server, "check_auth_interactive", check_auth_interactive, raising=False)
    monkeypatch.setattr(_Server, "check_auth_interactive_response", check_auth_interactive_response, raising=False)
    orig_init = trizetto.Session.__init__
    monkeypatch.setattr(trizetto.Session, "__init__", lambda self, s, known=None, timeout=1: orig_init(self, s, known=known, timeout=timeout))
    pull = lambda: trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=trizetto.Settings.from_env(sftp.env()), log=lambda *a: None)
    with pytest.raises(trizetto.TriZettoError) as err:
        pull()
    assert not isinstance(err.value, trizetto.SignInRefused) and trizetto.refusal(store.conn, trizetto.Settings.from_env(sftp.env())) is None
    slow["on"] = False
    sftp.refuse_password = True
    with pytest.raises(trizetto.SignInRefused) as err:          # refused by the typed-answer route too: held
        pull()
    held = trizetto.refusal(store.conn, trizetto.Settings.from_env(sftp.env()))
    assert held is not None and err.value.kind == held["kind"] == "interactive"     # the sign-in, not "the password"
    # TriZetto switches the login on but refuses the file service: the sign-in works, so the hold ends
    sftp.refuse_password = False
    monkeypatch.setattr(_Server, "check_channel_request", lambda self, kind, chanid: paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)
    store.conn.execute("UPDATE meta SET value=json_set(value, '$.at', '2000-01-01T00:00:00+00:00') WHERE key=?", (trizetto.REFUSED_META,))
    with pytest.raises(trizetto.FileServiceRefused):
        pull()
    assert trizetto.refusal(store.conn, trizetto.Settings.from_env(sftp.env())) is None and store.conn.execute("SELECT 1 FROM meta WHERE key=?", (trizetto.REFUSED_META,)).fetchone() is None
    out = trizetto.check(env=sftp.env())
    assert [(x["step"], x["ok"]) for x in out["steps"]][-2:] == [("Sign-in", True), ("Files", False)]
    store.close()


def test_the_setup_test_holds_and_releases_through_the_journal_on_a_serverless_host(tmp_path):
    import sqlite3

    from remitwatch import actions
    from remitwatch.insights import password_days
    conn = sqlite3.connect(str(tmp_path / "m.sqlite"))
    assert actions.apply(conn, {"kind": "trizetto_hold", "op": "set", "who": "tag1", "detail": "refused", "at": "2026-11-21T10:00:00+00:00"})
    assert "tag1" in conn.execute("SELECT value FROM meta WHERE key=?", (trizetto.REFUSED_META,)).fetchone()[0]
    assert actions.apply(conn, {"kind": "trizetto_hold", "op": "clear"}) is True
    assert conn.execute("SELECT 1 FROM meta WHERE key=?", (trizetto.REFUSED_META,)).fetchone() is None
    assert [password_days(v) for v in ("90", "90 days", "8 weeks", "6 months", "1 year", "annually", "never", "")] == [90, 90, 56, 180, 365, 365, 0, 0]


def test_two_unclear_sign_ins_in_a_row_hold_for_six_hours(tmp_path, sftp, monkeypatch):
    """A server that hangs up instead of saying no must not get the password every hour: the second unclear sign-in in a
    row holds the feed for 6 hours, and Today says what happened (not that TriZetto refused it)."""
    from remitwatch.insights import trizetto_health
    from remitwatch.store import Store
    _Server = _fake_sftp()._Server
    _remits(sftp.root, 1)
    store = Store(str(tmp_path / "s.sqlite"))
    orig_init, orig_auth = trizetto.Session.__init__, _Server.check_auth_password

    def slow(self, username, password):
        time.sleep(3)
        return orig_auth(self, username, password)
    monkeypatch.setattr(_Server, "check_auth_password", slow)
    monkeypatch.setattr(trizetto.Session, "__init__", lambda self, s, known=None, timeout=1: orig_init(self, s, known=known, timeout=timeout))
    settings = trizetto.Settings.from_env(sftp.env())
    pull = lambda: trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + 60, settings=settings, log=lambda *a: None)
    for _ in range(2):
        with pytest.raises(trizetto.SignInUnclear):
            pull()
    held = trizetto.refusal(store.conn, settings)
    assert held and held["unclear"] and pull()["skipped"].startswith("The sign-in to TriZetto did not finish twice in a row")
    assert [h["title"][:44] for h in trizetto_health(store.conn, "2026-11-21", sftp.env())] == ["TriZetto's sign-in did not finish twice in a"]
    store.close()


# --------------------------------------------------------------------------- review fixes: the feed's sign-in and its limits
def _pull(store, env: dict, secs: float = 60):
    return trizetto.pull(store.conn, _ingest_into(store), deadline=time.time() + secs, settings=trizetto.Settings.from_env(env),
                         log=lambda *a: None)


def test_a_server_that_stops_answering_cannot_hold_the_run_past_its_deadline(tmp_path, sftp, monkeypatch):
    """Signed in, then TriZetto stops answering (a folder listing or a file that never comes): a watchdog closes the line at
    the pull's deadline, the run goes on with what was read by then, and the next run reads the rest."""
    from remitwatch.store import Store
    fs = _fake_sftp()
    _remits(sftp.root, 3)
    store = Store(str(tmp_path / "s.sqlite"))
    release = threading.Event()
    stall = {"list": False, "read": ""}
    orig_list, orig_read = fs._SFTP.list_folder, fs._Handle.read

    def list_folder(self, path):
        if stall["list"]:
            release.wait(60)
        return orig_list(self, path)

    def read(self, offset, length):
        if stall["read"] and self.filename.endswith(stall["read"]):
            release.wait(60)
        return orig_read(self, offset, length)
    monkeypatch.setattr(fs._SFTP, "list_folder", list_folder)
    monkeypatch.setattr(fs._Handle, "read", read)
    try:
        stall["list"] = True
        t0 = time.time()
        out = _pull(store, sftp.env(), secs=2)
        assert time.time() - t0 < 2 + trizetto.WATCHDOG_GRACE + 4, "the stalled listing held the run past its deadline"
        assert "stopped answering" in out["error"] and out["downloaded"] == 0 and out["remaining"] == 0
        assert out["hostkey"] and out["seen_changed"]           # what changed before the stall is reported, so it is saved
        stall["list"], stall["read"] = False, "remit_501.RMT"
        t0 = time.time()
        out = _pull(store, sftp.env(), secs=2)
        assert time.time() - t0 < 2 + trizetto.WATCHDOG_GRACE + 4, "the stalled read held the run past its deadline"
        # the files not read are counted, so a serverless cycle carries on with them as it does after its deadline
        assert out["remits"] == 1 and "1 of 3 new files were read" in out["error"] and out["remaining"] == 2
    finally:
        release.set()
    stall["read"] = ""
    out = _pull(store, sftp.env())
    assert out["remits"] == 2 and "error" not in out
    store.close()


def test_a_host_that_holds_the_line_without_speaking_ssh_is_a_failed_step_not_a_page_error(tmp_path, monkeypatch):
    """A proxy, or a host and port that point at a service that waits for the client to speak first, takes the connection
    and never sends SSH's greeting. paramiko then gives up without saying so. The /setup test shows a failed Connection
    step, not "request failed (500)", and the scheduled pull fails as a TriZetto error with nothing sent. Invented settings."""
    _fake_sftp()            # paramiko, or skip
    import base64
    import socket

    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch.store import Store
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(8)
    held: list = []
    threading.Thread(target=lambda: [held.append(ls.accept()) for _ in range(8)], daemon=True).start()
    monkeypatch.setattr(trizetto.Session.__init__, "__defaults__", (None, 2))      # wait 2 seconds for the greeting, not 30
    env = {"TRIZETTO_SFTP_HOST": "127.0.0.1", "TRIZETTO_SFTP_PORT": str(ls.getsockname()[1]), "TRIZETTO_SFTP_USER": "RWLOGIN",
           "TRIZETTO_SFTP_PASSWORD": "pw-invented-0001"}
    try:
        got = trizetto.check(env)
        assert got["ok"] is False and got["steps"][-1]["step"] == "Connection", got
        assert "did not complete the SSH handshake within 2 s; nothing was sent" in got["steps"][-1]["detail"]
        store = Store(str(tmp_path / "s.sqlite"))
        with pytest.raises(trizetto.TriZettoError, match="did not complete the SSH handshake"):
            _pull(store, env, secs=30)
        store.close()
        # the route answers with its steps
        db = str(tmp_path / "api.sqlite")
        Store(db).close()
        api.ensure_server_schema(db)
        for k in ("REMITWATCH_MODE", "VERCEL", "REMITWATCH_ALLOW_OPEN"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(api, "DB_PATH", db)
        monkeypatch.setattr(api, "PASSWORD", "a-long-test-password-1")
        monkeypatch.setattr(api, "USERNAME", "staff")
        auth = {"Authorization": "Basic " + base64.b64encode(b"staff:a-long-test-password-1").decode(), "X-Requested-With": "remitwatch"}
        r = TestClient(api.app, raise_server_exceptions=False).post("/api/trizetto/test", headers=auth)
        assert r.status_code == 200 and r.json()["steps"][-1]["step"] == "Connection", r.text[:300]
    finally:
        ls.close()
        for c, _ in held:
            c.close()


@pytest.mark.parametrize("cause, says_wait", [(TimeoutError(), True), (ConnectionResetError(104, "reset"), False)])
def test_a_silent_host_reads_the_same_whichever_clock_fires_first(monkeypatch, cause, says_wait):
    """A host that never speaks SSH is caught by start_client's own clock or, a moment earlier under load, by paramiko's
    banner clock (an SSHException raised from a timeout). Both are the same situation and give the same message, so the
    test above cannot pass or fail on the machine's load. A banner error from anything else (the host hung up) keeps the
    general message. Invented settings; paramiko's handshake is replaced, the socket is real."""
    paramiko = pytest.importorskip("paramiko")
    import socket

    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(1)

    def start_client(self, event=None, timeout=None):
        try:
            raise cause
        except OSError as exc:      # what paramiko's _check_banner does: the read's error becomes the context
            raise paramiko.SSHException("Error reading SSH protocol banner" + str(exc))

    monkeypatch.setattr(paramiko.Transport, "start_client", start_client)
    s = trizetto.Settings.from_env({"TRIZETTO_SFTP_HOST": "127.0.0.1", "TRIZETTO_SFTP_PORT": str(ls.getsockname()[1]),
                                    "TRIZETTO_SFTP_USER": "RWLOGIN", "TRIZETTO_SFTP_PASSWORD": "pw-invented-0001"})
    try:
        with pytest.raises(trizetto.TriZettoError) as got:
            trizetto.Session(s, timeout=2)._handshake(set())
    finally:
        ls.close()
    if says_wait:
        assert "did not complete the SSH handshake within 2 s; nothing was sent." in str(got.value)
    else:
        assert str(got.value) == "127.0.0.1 did not complete the SSH handshake (SSHException)."


def test_a_hold_says_its_times_in_plain_words():
    """The hold's times read "2026-11-23 19:32 UTC", never "2026-11-23 19:32+00:00 UTC"."""
    text = trizetto.held_text({"at": "2026-11-22T19:32:05+00:00", "retry_at": "2026-11-23T19:32+00:00", "kind": "password"})
    assert "on 2026-11-22 19:32 UTC" in text and "until 2026-11-23 19:32 UTC" in text and "+00:00" not in text
    # a time with another offset is said in UTC
    assert "until 2026-11-23 19:32 UTC" in trizetto.held_text({"at": "2026-11-22T19:32:05+00:00", "retry_at": "2026-11-23T14:32-05:00"})


def test_the_deadline_is_checked_before_each_file(tmp_path, sftp, monkeypatch):
    """A run that reaches its deadline mid-way stops before the next file, not at the end of a batch of 40."""
    from remitwatch.store import Store
    fs = _fake_sftp()
    _remits(sftp.root, 3)
    store = Store(str(tmp_path / "s.sqlite"))
    deadline = time.time() + 3
    orig_read = fs._Handle.read
    first = {"done": False}

    def read(self, offset, length):          # the first file takes until just past the deadline
        if not first["done"]:
            first["done"] = True
            time.sleep(max(0.0, deadline + 0.3 - time.time()))
        return orig_read(self, offset, length)
    monkeypatch.setattr(fs._Handle, "read", read)
    out = trizetto.pull(store.conn, _ingest_into(store), deadline=deadline, settings=trizetto.Settings.from_env(sftp.env()),
                        log=lambda *a: None)
    assert (out["downloaded"], out["remaining"]) == (1, 2) and "error" not in out
    assert store.conn.execute("SELECT COUNT(*) FROM trizetto_seen").fetchone()[0] == 1
    store.close()


def test_a_notice_before_the_password_prompt_does_not_take_the_password(tmp_path, sftp, monkeypatch):
    """A typed-answer sign-in may start with a notice that asks nothing (RFC 4256). The password goes to the first real
    question, once; no empty password is sent and nothing is held."""
    import paramiko

    from remitwatch.store import Store
    fs = _fake_sftp()
    _remits(sftp.root, 1)
    got = []

    def check_auth_interactive(self, username, submethods):
        self._user, self._round = username, 0
        self.fake.logins.append(username)
        return paramiko.InteractiveQuery("Notice", "Authorized use only.")

    def check_auth_interactive_response(self, responses):
        self._round += 1
        got.append(["<the password>" if r == self.fake.password else r for r in responses])
        if self._round == 1:
            return paramiko.InteractiveQuery("", "", ("Password: ", False))
        ok = self._user == self.fake.user and responses and responses[0] == self.fake.password
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED
    monkeypatch.setattr(fs._Server, "get_allowed_auths", lambda self, username: "keyboard-interactive")
    monkeypatch.setattr(fs._Server, "check_auth_password", lambda self, username, password: paramiko.AUTH_FAILED)
    monkeypatch.setattr(fs._Server, "check_auth_interactive", check_auth_interactive, raising=False)
    monkeypatch.setattr(fs._Server, "check_auth_interactive_response", check_auth_interactive_response, raising=False)
    store = Store(str(tmp_path / "s.sqlite"))
    out = _pull(store, sftp.env())
    assert out["remits"] == 1 and got == [[], ["<the password>"]]
    assert store.conn.execute("SELECT 1 FROM meta WHERE key=?", (trizetto.REFUSED_META,)).fetchone() is None
    store.close()


def test_today_says_what_trizetto_refused_and_asks_for_the_fix_in_writing(tmp_path, sftp, monkeypatch):
    """Today follows docs/TRIZETTO_REQUEST.md: press Test once, then ask TriZetto in writing to reset the RemitWatch login's
    password; change it only on a page that shows that login's username, never the Site ID's (ModMed may use it); never by
    email. It says "refused the password" only when TriZetto refused a password sent by the password method."""
    import paramiko
    from datetime import datetime, timezone

    from remitwatch.insights import trizetto_health
    from remitwatch.store import Store
    fs = _fake_sftp()
    _remits(sftp.root, 1)
    today = datetime.now(timezone.utc).date().isoformat()
    sftp.refuse_password = True
    n = [0]

    def refused(**server) -> dict:
        n[0] += 1
        store = Store(str(tmp_path / f"s{n[0]}.sqlite"))
        with monkeypatch.context() as m:
            for name, fn in server.items():
                m.setattr(fs._Server, name, fn, raising=False)
            with pytest.raises(trizetto.SignInRefused):
                _pull(store, sftp.env())
        items = trizetto_health(store.conn, today, sftp.env())
        store.close()
        assert [i["key"] for i in items] == ["trizetto_signin"]
        return items[0]

    item = refused()
    assert item["title"].startswith("TriZetto refused the feed's password on")
    who = item["who"]
    for said in ("Test the TriZetto connection", "once", "in writing", "Contact Us", "reset the RemitWatch login's password",
                 "RemitWatch login's username, never the Site ID's", "never by email", "configure.sh", "ModMed"):
        assert said in who, said
    assert "Manage SFTP Password" not in who and "set a new SFTP password" not in who
    # asked as a typed answer and refused: TriZetto refused the sign-in (it may not have been asking for the password)
    item = refused(get_allowed_auths=lambda self, username: "keyboard-interactive",
                   check_auth_interactive=lambda self, username, submethods: paramiko.InteractiveQuery("", "", ("Password: ", False)),
                   check_auth_interactive_response=lambda self, responses: paramiko.AUTH_FAILED)
    assert item["title"].startswith("TriZetto refused the feed's sign-in on") and "password on" not in item["title"]
    # a server that takes no password for the login: a new password would not help, so nobody is sent to reset one
    item = refused(get_allowed_auths=lambda self, username: "publickey")
    assert "would not take a password" in item["title"] and "reset" not in item["who"] and "in writing" in item["who"]


def test_the_setup_test_does_not_send_a_held_password_again_unless_told_to(tmp_path, sftp):
    """While a refused password is held, the /setup test says so and does not sign in with it again (that is how a login gets
    locked); told to (override), it tries once. A new password is tried at once."""
    from remitwatch.store import Store
    _remits(sftp.root, 1)
    store = Store(str(tmp_path / "s.sqlite"))
    sftp.refuse_password = True
    with pytest.raises(trizetto.SignInRefused):
        _pull(store, sftp.env())
    before = len(sftp.logins)
    out = trizetto.check(sftp.env(), conn=store.conn)
    last = out["steps"][-1]
    assert len(sftp.logins) == before and out["held"]["kind"] == "password" and "refused" not in out
    assert last["step"] == "Sign-in" and last["ok"] is False
    assert last["detail"].startswith("Not tried now. TriZetto refused this user name and password on") and " until " in last["detail"]
    out = trizetto.check(sftp.env(), conn=store.conn, override=True)
    assert len(sftp.logins) == before + 1 and out["refused_kind"] == "password"
    sftp.refuse_password, sftp.password = False, "a-new-password-from-trizetto"
    out = trizetto.check(sftp.env(TRIZETTO_SFTP_PASSWORD="a-new-password-from-trizetto"), conn=store.conn)
    assert len(sftp.logins) == before + 2 and ("Sign-in", True) in [(x["step"], x["ok"]) for x in out["steps"]]
    store.close()


def _serving_conn(tmp_path, state):
    """The dashboard's copy as the serverless site reads it."""
    import gzip
    import sqlite3

    from remitwatch import cloud
    path = tmp_path / f"serving-{time.time_ns()}.sqlite"
    path.write_bytes(gzip.decompress(state.get(cloud.SERVING)))
    return sqlite3.connect(str(path))


def test_a_new_password_that_works_ends_the_hold_in_the_saved_store_and_on_today(tmp_path, monkeypatch):
    """Serverless: a hold for a refused password is saved; once a new password works, the next run saves the store without
    it, even with no new remittance, so Today stops telling anyone to reset the password."""
    from remitwatch import cloud
    from remitwatch.insights import trizetto_health
    from remitwatch.state import DropboxState
    from tests.fakes import FakeDropbox
    root = tmp_path / "trizetto"
    root.mkdir()
    f = _fake_sftp().FakeSFTP(str(root))
    try:
        monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
        for k, v in f.env().items():
            monkeypatch.setenv(k, v)
        _remits(str(root), 2)
        state = DropboxState(FakeDropbox(str(tmp_path / "dbx")), "/RemitWatch-state")
        run = lambda: cloud.run_cloud_cycle(state, None, budget_s=600, email=False, log=lambda *a: None)    # noqa: E731
        held_in_store = lambda: cloud.load_raw(state)[0].conn.execute(                                       # noqa: E731
            "SELECT 1 FROM meta WHERE key=?", (trizetto.REFUSED_META,)).fetchone() is not None
        today = lambda: trizetto_health(_serving_conn(tmp_path, state), time.strftime("%Y-%m-%d"), f.env())  # noqa: E731
        assert run()["trizetto"]["remits"] == 2
        f.refuse_password = True
        assert "error" in run()["trizetto"] and held_in_store() and [h["key"] for h in today()] == ["trizetto_signin"]
        f.refuse_password, f.password = False, "the-new-one-from-trizetto"
        monkeypatch.setenv("TRIZETTO_SFTP_PASSWORD", f.password)
        out = run()
        assert out["trizetto"]["new"] == 0 and "error" not in out["trizetto"]
        assert not held_in_store() and today() == []
    finally:
        f.stop()


def test_the_dashboards_copy_carries_the_servers_key_after_a_first_pull_with_nothing_new(tmp_path, monkeypatch):
    """Serverless: the /setup test reads the server's key from the dashboard's copy. A first pull that finds only files read
    from Dropbox before still makes the copy again, so the test never trusts another key."""
    from remitwatch import cloud
    from remitwatch.state import DropboxState
    from tests.fakes import FakeDropbox
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "TRIZETTO_SFTP_USER", "TRIZETTO_SFTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    body = remit("".join(clean_visits(1, 25, "99214", "223.65", "117.60")), "100.00", seg_count=99, pay_date="20261110")
    data = tmp_path / "dbx" / "Trizetto Data"
    data.mkdir(parents=True)
    (data / "r1.RMT").write_bytes(body)
    state = DropboxState(FakeDropbox(str(tmp_path / "state")), "/RemitWatch-state")
    run = lambda: cloud.run_cloud_cycle(state, FakeDropbox(str(tmp_path / "dbx")), budget_s=600, email=False, log=lambda *a: None)  # noqa: E731
    run()
    root = tmp_path / "trizetto" / "remits"
    root.mkdir(parents=True)
    (root / "r1.RMT").write_bytes(body)                 # TriZetto holds the same file
    f = _fake_sftp().FakeSFTP(str(tmp_path / "trizetto"))
    try:
        for k, v in f.env().items():
            monkeypatch.setenv(k, v)
        out = run()
        assert out["trizetto"]["hostkey"] and out["trizetto"]["remits"] == 0
        kept = _serving_conn(tmp_path, state).execute("SELECT value FROM meta WHERE key=?", (trizetto.HOSTKEY_META,)).fetchone()
        assert kept and kept[0] == out["trizetto"]["hostkey"]
    finally:
        f.stop()


def test_a_serverless_host_reads_trizetto_only_when_switched_on(tmp_path, sftp, monkeypatch):
    """Both sites run the same code, but only one should sign in with the login: each keeps its own hold and its own
    password reminder. A serverless host (Vercel) reads TriZetto only with TRIZETTO_SFTP_SERVERLESS=1; the server always does."""
    import sqlite3

    from remitwatch import cloud
    from remitwatch.insights import trizetto_health
    from remitwatch.state import DropboxState
    from tests.fakes import FakeDropbox
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "REPORT_TO", "TRIZETTO_SFTP_SERVERLESS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    for k, v in sftp.env(TRIZETTO_SFTP_PASSWORD_SET="2026-01-01", TRIZETTO_SFTP_PASSWORD_DAYS="30").items():
        monkeypatch.setenv(k, v)
    _remits(sftp.root, 1)
    state = DropboxState(FakeDropbox(str(tmp_path / "dbx")), "/RemitWatch-state")
    run = lambda: cloud.run_cloud_cycle(state, None, budget_s=600, email=False, log=lambda *a: None)    # noqa: E731
    out = run()
    assert "trizetto" not in out and sftp.connections == 0 and trizetto.configured() is False
    test = trizetto.check()
    assert test["not_here"] and "TRIZETTO_SFTP_SERVERLESS=1" in test["steps"][0]["detail"] and sftp.connections == 0
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    assert trizetto_health(conn, "2026-12-25") == []       # the password reminder is the server's to give
    line = {c["key"]: c for c in cloud.setup_status(probe=False)["checks"]}["trizetto"]
    assert line["ok"] is None and "does not read TriZetto" in line["detail"] and "in Vercel" in line["fix"]
    monkeypatch.setenv("TRIZETTO_SFTP_SERVERLESS", "1")
    assert run()["trizetto"]["remits"] == 1 and trizetto.configured() is True
    assert [h["key"] for h in trizetto_health(conn, "2026-12-25")] == ["trizetto_password"]
    assert "in Vercel" in trizetto_health(conn, "2026-12-25")[0]["who"]
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.delenv("TRIZETTO_SFTP_SERVERLESS")
    assert trizetto.configured() is True and "configure.sh" in trizetto_health(conn, "2026-12-25")[0]["who"]


def test_the_hold_keeps_a_salted_slow_hash_not_a_quick_one():
    """A copy of the store (a backup, the dashboard's copy) must not let anyone test password guesses quickly."""
    import hashlib
    s = trizetto.Settings(user="rw-login", password="short", host="sftp.example.test", port=22)
    a, b = trizetto.credential_tag(s), trizetto.credential_tag(s)
    assert a != b and trizetto.same_credentials(a, s) and trizetto.same_credentials(b, s)
    assert not trizetto.same_credentials(a, trizetto.Settings(user="rw-login", password="other", host="sftp.example.test", port=22))
    fast = hashlib.sha256("sftp.example.test\x0022\x00rw-login\x00short".encode()).hexdigest()
    assert a.startswith(("scrypt$", "pbkdf2$")) and fast[:16] not in a and fast not in a
    assert not trizetto.same_credentials(fast[:16], s) and not trizetto.same_credentials("", s)


def test_a_host_key_setting_that_cannot_be_read_stops_before_connecting(tmp_path, sftp):
    """TRIZETTO_SFTP_HOSTKEY holding something other than fingerprints (a whole key, 'SHA256 abc' with a space) is not taken
    as 'no key given': the feed stops before it connects, so nothing is sent to whoever answers."""
    from remitwatch.store import Store
    store = Store(str(tmp_path / "s.sqlite"))
    for bad in ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7xyz", "SHA256 abcdefghijk", "SHA1:0123456789"):
        with pytest.raises(trizetto.TriZettoError, match="no fingerprint that can be read"):
            _pull(store, sftp.env(TRIZETTO_SFTP_HOSTKEY=bad))
        out = trizetto.check(sftp.env(TRIZETTO_SFTP_HOSTKEY=bad))
        assert [(x["step"], x["ok"]) for x in out["steps"]] == [("Settings", False)]
    assert sftp.connections == 0 and sftp.logins == []
    store.close()


def test_a_listed_fingerprint_of_any_key_type_is_found(tmp_path):
    """A server has one key per type and offers its favourite first. A fingerprint TriZetto gave for another of its key
    types is still found (each type is asked for before the key is called wrong), and so is the key kept on first use after
    TriZetto adds a key type. No password goes to a key that matches nothing."""
    from remitwatch.store import Store
    fs = _fake_sftp()
    root = tmp_path / "trizetto"
    root.mkdir()
    _remits(str(root), 1)
    rsa = trizetto.fingerprint(fs.host_key(0))
    both = fs.FakeSFTP(str(root), ecdsa=True)          # offers ECDSA first, then RSA
    try:
        for given in (f"RSA {rsa}", rsa):
            with trizetto.Session(trizetto.Settings.from_env(both.env(TRIZETTO_SFTP_HOSTKEY=given))) as ses:
                assert ses.key_fp == rsa
        with pytest.raises(trizetto.TriZettoError, match="not the one set in TRIZETTO_SFTP_HOSTKEY"):
            with trizetto.Session(trizetto.Settings.from_env(both.env(TRIZETTO_SFTP_HOSTKEY="SHA256:neither-of-them"))):
                pass
        assert len(both.logins) == 2
    finally:
        both.stop()
    store = Store(str(tmp_path / "s.sqlite"))
    only_rsa = fs.FakeSFTP(str(root))
    try:
        assert _pull(store, only_rsa.env())["hostkey"] == rsa
    finally:
        only_rsa.stop()
    both = fs.FakeSFTP(str(root), ecdsa=True)           # TriZetto adds a key type; its RSA key is unchanged
    try:
        out = _pull(store, both.env())
        assert out["listed"] == 1 and out["new"] == 0 and "hostkey" not in out
    finally:
        both.stop()
    store.close()


def test_the_stand_in_server_records_anything_that_would_change_its_files(tmp_path, sftp):
    """The read-only tests say `writes == []`: that only means something if every kind of change is recorded."""
    import paramiko
    _remits(sftp.root, 1)
    t = paramiko.Transport(("127.0.0.1", sftp.port))
    t.connect(username=sftp.user, password=sftp.password)
    c = paramiko.SFTPClient.from_transport(t)
    p = "/remits/remit_500.RMT"
    calls = (lambda: c.chmod(p, 0o600), lambda: c.utime(p, (1, 1)), lambda: c.mkdir("/new"), lambda: c.rmdir("/remits"),
             lambda: c.open("/copy.RMT", "wb"), lambda: c.symlink(p, "/link"), lambda: c.posix_rename(p, "/moved"),
             lambda: c.remove(p), lambda: c.rename(p, "/renamed"))
    for call in calls:
        with pytest.raises(OSError):
            call()
    t.close()
    assert [w[0] for w in sftp.writes] == ["chattr", "chattr", "mkdir", "rmdir", "open for writing", "symlink", "posix_rename",
                                          "remove", "rename"]
    assert os.path.exists(os.path.join(sftp.root, "remits", "remit_500.RMT"))


def test_how_long_the_password_lasts_is_read_however_trizetto_writes_it():
    """A number goes with the unit right after it, a day count wins, and what cannot be read is said, not taken as 'never'."""
    from remitwatch.insights import password_days, trizetto_health
    said = {"365 days (1 year)": 365, "180 days (6 months)": 180, "every 90 days, or 3 months": 90, "90 days / 3 months": 90,
            "1 yr": 365, "1y": 365, "six months": 180, "sixty days": 60, "semi-annually": 182, "twice a year": 182,
            "quarterly": 90, "90 calendar days": 90, "1 (one) year": 365, "8 wks": 56, "Passwords expire every 60 days.": 60,
            "It does not expire": 0, "0": 0, "": 0, "never": 0, "biannually": None, "ask TriZetto": None,
            "12 months (365 days)": 365, "1 year": 365, "2 years": 730}
    assert {k: password_days(k) for k in said} == said
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    env = {"TRIZETTO_SFTP_USER": "rw-login", "TRIZETTO_SFTP_PASSWORD": "x", "TRIZETTO_SFTP_PASSWORD_SET": "2026-01-01"}
    soon = trizetto_health(conn, "2026-12-25", dict(env, TRIZETTO_SFTP_PASSWORD_DAYS="365 days (1 year)"))
    assert [(h["key"], h["due"]) for h in soon] == [("trizetto_password", "2027-01-01")]
    unread = trizetto_health(conn, "2026-12-25", dict(env, TRIZETTO_SFTP_PASSWORD_DAYS="ask TriZetto"))
    assert [h["key"] for h in unread] == ["trizetto_password"] and "could not be read" in unread[0]["title"]


def test_setup_and_the_settings_file_follow_the_written_trizetto_request():
    """No phone call, no change in TriZetto's portal (Remittance Advice Settings, the Site ID's password), RemitWatch's own
    login entered with configure.sh; and the settings file gives each block its own heading."""
    page = open(os.path.join(ROOT, "remitwatch", "static", "setup.html"), encoding="utf-8").read()
    panel = page[page.index('id="tzp"'):page.index('id="faxp"')]
    for gone in ("Call TriZetto", "turn on <b>Auto Generate", "(the Site ID)", "\u2014"):
        assert gone not in panel, gone
    for said in ("docs/TRIZETTO_REQUEST.md", "No phone call", "Change nothing in TriZetto's portal", "configure.sh",
                 "TRIZETTO_SFTP_DIRS", "TRIZETTO_SFTP_HOSTKEY", "never by email", "RemitWatch login's username"):
        assert said in panel, said
    heading, under = "", {}
    for line in open(os.path.join(ROOT, ".env.example"), encoding="utf-8"):
        if line.startswith("# ----"):
            heading = line
        m = re.match(r"([A-Z][A-Z0-9_]*)=", line)
        if m:
            under[m.group(1)] = heading
    assert all("TriZetto" in under[k] for k in under if k.startswith("TRIZETTO_"))
    assert all("Fax" in under[k] for k in under if k.startswith("SRFAX_"))
    assert all("Letterhead" in under[k] for k in under if k.startswith("PRACTICE_"))
    assert all("ModMed" in under[k] for k in under if k.startswith("MODMED_"))
    assert "Appeal packets" in under["REMITWATCH_NOTE_MODEL"] and "Appeal packets" in under["APPEAL_EMAIL_TO"]
    assert "Site ID; TriZetto support" not in open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()


def test_the_setup_checks_give_the_steps_for_the_host_they_run_on(tmp_path, monkeypatch):
    """The server is told about its .env and a restart, Vercel about its settings and a redeploy; a TriZetto login with no
    expiry date is flagged, since no reminder can come before the password expires."""
    from remitwatch import cloud
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SRFAX_CALLER_ID", "SRFAX_SENDER_EMAIL",
              "TRIZETTO_SFTP_PASSWORD_SET", "TRIZETTO_SFTP_PASSWORD_DAYS", "TRIZETTO_SFTP_SERVERLESS", "REMITWATCH_STATE_DIR",
              "REMITWATCH_FAX_SENDER", "VERCEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_DB", str(tmp_path / "s.sqlite"))
    monkeypatch.setenv("SRFAX_ACCESS_ID", "100")
    monkeypatch.setenv("SRFAX_ACCESS_PWD", "x")
    monkeypatch.setenv("REMITWATCH_PASSWORD", "a-dashboard-password-1")
    monkeypatch.setenv("REMITWATCH_OWNER_PASSWORD", "short")
    monkeypatch.setenv("TRIZETTO_SFTP_USER", "rw-login")
    monkeypatch.setenv("TRIZETTO_SFTP_PASSWORD", "x")
    checks = lambda: {c["key"]: c for c in cloud.setup_status(probe=False)["checks"]}       # noqa: E731
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    by = checks()
    assert ".env" in by["fax"]["fix"] and "redeploy" not in by["fax"]["fix"]
    assert ".env" in by["owner_password"]["fix"] and "Vercel" not in by["owner_password"]["fix"]
    assert by["trizetto"]["ok"] is True and "No reminder comes before the password expires" in by["trizetto"]["detail"]
    monkeypatch.setenv("TRIZETTO_SFTP_PASSWORD_SET", "2026-11-01")
    monkeypatch.setenv("TRIZETTO_SFTP_PASSWORD_DAYS", "90 days")
    assert checks()["trizetto"]["detail"] == "set: press Test the TriZetto connection below"
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    by = checks()
    assert "in Vercel" in by["owner_password"]["fix"]
    assert by["trizetto"]["ok"] is None and "the server does" in by["trizetto"]["detail"]
    # the Vercel site that sends the faxes (REMITWATCH_FAX_SENDER=serverless) is told about Vercel's settings for the line
    monkeypatch.setenv("REMITWATCH_FAX_SENDER", "serverless")
    assert "in Vercel" in checks()["fax"]["fix"] and "redeploy" in checks()["fax"]["fix"]


def test_the_fax_line_on_setup_says_which_site_sends(tmp_path, monkeypatch):
    """The fax line can be set up on both sites, and only the site REMITWATCH_FAX_SENDER names sends. /setup's fax line says
    so on the other one: who sends, that this site sends nothing, and nothing to fix. A value that names no site pauses
    faxing on that site, so it is a fault, with the fix for the host it runs on."""
    from remitwatch import cloud
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_DIR", "VERCEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_DB", str(tmp_path / "s.sqlite"))
    for k, v in {"SRFAX_ACCESS_ID": "100", "SRFAX_ACCESS_PWD": "invented-fax-pw", "SRFAX_CALLER_ID": "2705550101",
                 "SRFAX_SENDER_EMAIL": "fax-notices@example.com"}.items():
        monkeypatch.setenv(k, v)

    def line(mode: str, sender: str | None = None):
        monkeypatch.setenv("REMITWATCH_MODE", mode)
        if sender is None:
            monkeypatch.delenv("REMITWATCH_FAX_SENDER", raising=False)
        else:
            monkeypatch.setenv("REMITWATCH_FAX_SENDER", sender)
        out = cloud.setup_status(probe=False)
        return next(c for c in out["checks"] if c["key"] == "fax"), out["fax"]
    row, fx = line("server")                            # the server sends by default
    assert row["ok"] is True and row["detail"].startswith("set: faxes go out from 2705550101")
    assert fx == {"configured": True, "sends": True, "not_here": None}
    row, fx = line("serverless")                        # the Vercel site with the same settings sends nothing, and says so
    assert row["ok"] is None and row["fix"] == "" and fx["sends"] is False and fx["configured"] is True
    assert row["detail"] == ("set, but faxes are sent by the practice's server, not by this site (REMITWATCH_FAX_SENDER=server). "
                             "This site shows them and sends nothing.")
    assert fx["not_here"] == "Faxes are sent by the practice's server, not by this site (REMITWATCH_FAX_SENDER=server). This site shows them and sends nothing."
    row, fx = line("server", "serverless")              # named the other way round: the server sends nothing
    assert row["ok"] is None and "sent by the Vercel site, not by this site" in row["detail"] and fx["sends"] is False
    assert line("serverless", "serverless")[0]["ok"] is True
    row, _ = line("server", "none")                     # paused on purpose: said, nothing to fix
    assert row["ok"] is None and row["detail"].startswith("set, but faxing is paused") and row["fix"] == ""
    row, _ = line("server", "servr")                    # a typo pauses faxing too: a fault, fixed in the server's .env
    assert row["ok"] is False and row["detail"].endswith("It says 'servr', which names no site.")
    assert row["fix"].startswith("Set REMITWATCH_FAX_SENDER in the server's .env to server") and row["fix"].endswith("(docker compose up -d).")
    row, _ = line("serverless", "Vercel")
    assert row["ok"] is False and row["fix"].startswith("Set REMITWATCH_FAX_SENDER in Vercel") and row["fix"].endswith("Then redeploy.")
    monkeypatch.delenv("SRFAX_ACCESS_ID")
    row, fx = line("serverless", "servr")               # no fax line here: nothing to say about who sends
    assert row["ok"] is None and row["detail"].startswith("not set") and fx == {"configured": False, "sends": False, "not_here": None}


def test_the_setup_page_and_the_settings_file_name_the_one_fax_sender():
    """The fax panel says which site sends and shows why this one does not; .env.example lists REMITWATCH_FAX_SENDER with
    the fax line's settings and says what each value means."""
    page = open(os.path.join(ROOT, "remitwatch", "static", "setup.html"), encoding="utf-8").read()
    panel = page[page.index('id="faxp"'):page.index('id="load"')]
    for said in ("<code>REMITWATCH_FAX_SENDER</code>", "<code>server</code>", "<code>serverless</code>", "<code>none</code> pauses faxing",
                 "sends nothing", 'id="fxWhere"'):
        assert said in panel, said
    assert "\u2014" not in panel and "const fxWhere = s.fax && s.fax.not_here;" in page and "esc(fxWhere)" in page
    lines = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read().splitlines()
    at = lines.index("REMITWATCH_FAX_SENDER=server")
    assert "Fax line" in next(ln for ln in reversed(lines[:at]) if ln.startswith("# ----"))
    above = " ".join(ln for ln in lines[at - 3:at] if ln.startswith("#"))
    for said in ("server (the default)", "serverless: the Vercel site", "none: faxing is paused", "sends nothing"):
        assert said in above, said


def test_the_setup_page_says_where_each_value_goes_on_either_host():
    """The same page serves the server and the Vercel site. The passwords it makes and the Dropbox token it fetches are
    put in the server's settings with configure.sh, or in Vercel's: never "into Vercel" alone."""
    page = open(os.path.join(ROOT, "remitwatch", "static", "setup.html"), encoding="utf-8").read()
    for panel_id, end in (('id="gen"', 'id="connect"'), ('id="tokStep"', "</li>")):
        at = page.index(panel_id)
        text = page[at:page.index(end, at)]
        assert "configure.sh" in text and "Vercel" in text and "redeploy" in text, panel_id
    assert "Copy each into Vercel" not in page and "In Vercel add an environment variable" not in page


# --------------------------------------------------------------------------- what a Dropbox sync hands over
# An A/R report and a production report as EMA lays them out (column names only; every value is invented). Neither names
# a claim, a bill or a bill item the way the three weekly exports do.
AR_COLS = ["A/R Claim ID", "Responsible Party", "A/R Balance Type", "Current A/R ($) (Aging)", "Service Date", "Coverage Type",
           "Claim Link", "Claim Status", "CPT/Product", "Primary Payer", "Patient Name", "Denial Amount", "Last Billing Note"]
PRODUCTION_COLS = ["Posted Date", "Service Date", "CPT/Product", "Check Number", "Claim Link", "Insurance Payments", "Patient Payments",
                   "Contractual Adjustments", "Adjustment Reason Code", "Payer", "Charges", "Non- Contractual Adjustments",
                   "Patient Adjustments", "Patient Name", "Patient DOB"]


def _ar_rows() -> list[list]:
    import datetime as dt
    return [[9000001, "Insurance", "Debit", 223.65, dt.datetime(2026, 10, 1), "Primary", "https://example.ema.test/claim/1", "Denied",
             "99214", "Anthem Blue Cross", "SAMPLE, PAT", 223.65, "sent again"],
            [9000002, "Patient", "Debit", 40.00, dt.datetime(2026, 10, 2), "Primary", "https://example.ema.test/claim/2", "Paid",
             "99213", "Anthem Blue Cross", "SAMPLE, ROW", None, None]]


def _production_rows() -> list[list]:
    import datetime as dt
    return [[dt.datetime(2026, 10, 9), dt.datetime(2026, 10, 1), "99214", "CHK000123", "https://example.ema.test/claim/1", 117.60, 0.0,
             106.05, "CO-45", "Anthem Blue Cross", 223.65, 0.0, 0.0, "SAMPLE, PAT", dt.datetime(1960, 1, 1)]]


def _remit_bytes(i: int, pay_date: str = "20261118") -> bytes:
    return remit(claim(i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20261101"), dos="20261101"),
                 "117.60", seg_count=99, isa_ctrl=f"{i:09d}", pay_date=pay_date)


def _sha(path) -> str:
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_an_ar_and_a_production_report_in_the_folder_are_read_as_reports_and_never_as_exports(tmp_path):
    """EMA's A/R and Production reports have a Claim Status or money columns but no Claim ID, Bill ID or Bill Item
    Identifier. The router recognises them first and hands them to the report reader (remitwatch.ema_reports): no row of
    theirs goes to the weekly-export table. The A/R is read at once; the Production report waits for the posting ledger
    (none is open here). A weekly export beside them loads as before, and the A/R handed over again changes nothing."""
    from remitwatch import dropbox_sync
    from remitwatch.store import Store
    from tests.test_uploads import STATUS_COLS, as_csv, status_rows, xlsx
    ar = tmp_path / "AR 2026.xlsx"
    ar.write_bytes(xlsx("A/R Report", AR_COLS, _ar_rows()))
    prod = tmp_path / "Production 2026.csv"
    prod.write_bytes(as_csv(PRODUCTION_COLS, _production_rows()))
    weekly = tmp_path / "Claim & Claim Status.xlsx"
    weekly.write_bytes(xlsx("Claim & Claim Status", STATUS_COLS, status_rows()))
    store = Store(str(tmp_path / "s.sqlite"))
    said: list[str] = []
    out = dropbox_sync.ingest_new_files(store, [str(ar), str(prod), str(weekly)], log=lambda *a: said.append(" ".join(map(str, a))))
    assert (out["ema"], out["skipped"], out["errors"], out["failed"]) == (2, 0, 0, [])
    assert [(r["report"], r["status"]) for r in out["ema_reports"]] == [("ar_report", "current"), ("production", "waiting")]
    rows = lambda sql: [tuple(r) for r in store.conn.execute(sql)]       # noqa: E731
    assert rows("SELECT family, source_name, COUNT(*) FROM ema_rows GROUP BY 1, 2") == [("claim_status", "Claim & Claim Status.xlsx", 1)]
    assert rows("SELECT report, status FROM ema_report_files ORDER BY report") == [("ar_report", "current"), ("production", "waiting")]
    assert rows("SELECT COUNT(*) FROM ema_ar") == [(2,)]
    assert store.file_seen(_sha(weekly)) and {r[0] for r in rows("SELECT file_id FROM ema_report_files")} == {_sha(ar), _sha(prod)}
    assert not any("EMA ERROR" in s for s in said), said
    # handed over again (a full listing on a new mirror, a re-read of the folder): 'already', nothing changes
    again = dropbox_sync.ingest_new_files(store, [str(ar)], log=lambda *a: None)
    assert again["ema"] == 0 and [r["status"] for r in again["ema_reports"]] == ["already"]
    assert rows("SELECT COUNT(*) FROM ema_rows") == [(1,)] and rows("SELECT COUNT(*) FROM ema_ar") == [(2,)]
    assert rows("SELECT COUNT(*) FROM ema_report_files") == [(2,)]


def test_an_ar_report_queued_by_an_older_upload_page_is_read_as_a_report(tmp_path):
    """Before the Upload page handed EMA's reports to the intake folder it queued them as exports. The cycle reads such a
    queued file as the report it is: the catalog row carries the upload's SHA-256, and nothing goes to the weekly-export
    table."""
    from remitwatch import uploads
    from remitwatch.store import Store
    from tests.test_uploads import xlsx
    store = Store(str(tmp_path / "s.sqlite"))
    uploads.ensure_schema(store.conn)
    data = xlsx("A/R Report", AR_COLS, _ar_rows())
    p = tmp_path / "uploads" / "exports" / "AR 2026.xlsx"
    p.parent.mkdir(parents=True)
    p.write_bytes(data)
    sha = _sha(p)
    store.conn.execute("INSERT INTO uploads (upload_id, stored_sha, kind, name, path, size, uploaded_at, uploaded_by, status, summary) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)", (sha, sha, "ema", "AR 2026.xlsx", str(p), len(data), "2026-11-20T10:00:00+00:00",
                                                        "staff", "queued", json.dumps({"kind": "ema", "report": "Claim & Claim Status"})))
    store.conn.commit()
    res = uploads.ingest_queued(store, log=lambda *a: None)
    assert res["ema"] == 1 and res["uploads"] == 1
    assert [tuple(r) for r in store.conn.execute("SELECT file_id, report, status FROM ema_report_files")] == [(sha, "ar_report", "current")]
    assert store.conn.execute("SELECT COUNT(*) FROM ema_rows").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0] == 2


def _dropbox(files: dict, stop_listing_after: int | None = None, reset_delta_after: int | None = None, full_fails: bool = False):
    """The Dropbox folder sync() lists (tests/test_sync.py's stand-in): the listing can stop after so many entries (an
    outage), the saved position can be dropped part-way through a delta listing (Dropbox's "reset"), and the full listing
    can fail."""
    from tests.test_sync import FakeClient

    class Folder(FakeClient):
        def _cut(self, after: int | None, exc: Exception):
            for n, e in enumerate(self._entries()):
                if after is not None and n == after:
                    raise exc
                yield e

        def list_folder(self, path, recursive=True):
            self.calls.append("list_folder")
            if full_fails:
                raise RuntimeError("Dropbox files/list_folder failed 503: busy")
            yield from self._cut(stop_listing_after, RuntimeError("Dropbox files/list_folder/continue failed 503: busy"))
            self.last_cursor = "cursor-1"

        def list_continue(self, cursor):
            self.calls.append("list_continue:" + cursor)
            yield from self._cut(reset_delta_after, RuntimeError('Dropbox files/list_folder/continue failed 409: {"error_summary": "reset/"}'))
            self.last_cursor = "cursor-2"
    return Folder(files)


def _names(paths) -> list[str]:
    return [os.path.basename(p) for p in paths]


def test_a_sync_that_stops_part_way_hands_back_what_it_already_downloaded(tmp_path):
    """Dropbox stops answering after two remittances were downloaded. The saved position does not move, so the next
    listing names them again, finds them in the mirror and calls them unchanged: the failed sync hands them over on its
    exception (new_files), for the cycle to read at once."""
    from remitwatch.dropbox_sync import sync
    from remitwatch.store import Store
    files = {f"/Trizetto Data/Trizetto/remit_{i}.RMT": _remit_bytes(i) for i in (500, 501, 502)}
    store = Store(str(tmp_path / "s.sqlite"))
    kw = {"root_path": "/Trizetto Data", "data_dir": str(tmp_path / "mirror"), "log": lambda *a: None}
    with pytest.raises(RuntimeError) as err:
        sync(store, client=_dropbox(files, stop_listing_after=2), **kw)
    got = err.value.new_files
    assert _names(got) == ["remit_500.RMT", "remit_501.RMT"] and all(os.path.exists(p) for p in got)
    assert store.conn.execute("SELECT value FROM meta WHERE key='sync:cursor'").fetchone() is None
    after = sync(store, client=_dropbox(files), **kw)
    assert after["unchanged"] == 2 and _names(after["new_files"]) == ["remit_502.RMT"]


def test_the_sync_command_reads_what_a_failed_sync_downloaded(tmp_path, monkeypatch, capsys):
    """`remitwatch.cli sync` by hand, with Dropbox failing after two new remittances came down: the two are read before
    the command fails with Dropbox's error, so they are not left in the mirror for good (the next listing calls them
    unchanged). With --no-ingest nothing is read."""
    import argparse
    import sqlite3

    from remitwatch import cli, dropbox_sync
    from remitwatch.store import Store
    for k, v in {"DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s", "DROPBOX_REFRESH_TOKEN": "r", "DROPBOX_ROOT_PATH": "/Trizetto Data",
                 "REMITWATCH_DATA_DIR": str(tmp_path / "mirror")}.items():
        monkeypatch.setenv(k, v)
    files = {f"/Trizetto Data/Trizetto/remit_{i}.RMT": _remit_bytes(i) for i in (500, 501, 502)}
    monkeypatch.setattr(dropbox_sync, "DropboxClient", lambda *a, **kw: _dropbox(files, stop_listing_after=2))
    count = lambda db: sqlite3.connect(db).execute("SELECT COUNT(*) FROM transactions").fetchone()[0]     # noqa: E731
    asked = str(tmp_path / "asked.sqlite")          # --no-ingest: nothing is read
    Store(asked).close()
    with pytest.raises(RuntimeError):
        cli.cmd_sync(argparse.Namespace(db=asked, full=False, no_ingest=True))
    assert count(asked) == 0
    monkeypatch.setenv("REMITWATCH_DATA_DIR", str(tmp_path / "mirror2"))
    db = str(tmp_path / "s.sqlite")
    Store(db).close()
    with pytest.raises(RuntimeError):
        cli.cmd_sync(argparse.Namespace(db=db, full=False, no_ingest=False))
    assert count(db) == 2 and "reading the 2 file(s) it downloaded first" in capsys.readouterr().err
    monkeypatch.setattr(dropbox_sync, "DropboxClient", lambda *a, **kw: _dropbox(files))
    assert cli.cmd_sync(argparse.Namespace(db=db, full=False, no_ingest=False)) == 0 and count(db) == 3


def test_the_servers_cycle_reads_in_the_same_cycle_what_a_failed_sync_downloaded(tmp_path, monkeypatch):
    """The hourly cycle (pipeline.run_cycle, as the server runs it) with the real sync: Dropbox fails after two new
    remittances came down. Both are read and analysed in that cycle, the cycle still reports the Dropbox failure, and the
    next cycle reads only the one file it had not reached."""
    from remitwatch import dropbox_sync
    from tests.test_uploads import cycle, make_store
    db = make_store(tmp_path / "feed")
    for k, v in {"DROPBOX_APP_KEY": "k", "DROPBOX_APP_SECRET": "s", "DROPBOX_REFRESH_TOKEN": "r", "DROPBOX_ROOT_PATH": "/Trizetto Data",
                 "REMITWATCH_DATA_DIR": str(tmp_path / "mirror")}.items():
        monkeypatch.setenv(k, v)
    for k in ("TRIZETTO_SFTP_USER", "TRIZETTO_SFTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    files = {f"/Trizetto Data/Trizetto/remit_{i}.RMT": _remit_bytes(i, pay_date=f"202611{i - 480}") for i in (500, 501, 502)}
    monkeypatch.setattr(dropbox_sync, "DropboxClient", lambda *a, **kw: _dropbox(files, stop_listing_after=2))
    first = cycle(db)
    assert first["ingest"]["remits"] == 2 and first["error"].startswith("Dropbox:") and "detect" in first, first
    monkeypatch.setattr(dropbox_sync, "DropboxClient", lambda *a, **kw: _dropbox(files))
    second = cycle(db)
    assert second["ingest"]["remits"] == 1 and not second.get("error"), second
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        days = [r[0] for r in conn.execute("SELECT payment_date FROM transactions WHERE payment_date >= '2026-11-20' ORDER BY 1")]
    finally:
        conn.close()
    assert days == ["2026-11-20", "2026-11-21", "2026-11-22"]


def test_a_dropped_position_lists_the_folder_again_and_keeps_what_the_delta_had_downloaded(tmp_path):
    """Dropbox drops the saved position part-way through a delta listing: the whole folder is listed again in the same
    run. The file the delta listing had already downloaded is still handed over, though the full listing finds it in the
    mirror; and when the full listing fails as well, it is on the exception."""
    from remitwatch.dropbox_sync import sync
    from remitwatch.store import Store
    files = {f"/Trizetto Data/Trizetto/remit_{i}.RMT": _remit_bytes(i) for i in (600, 601)}
    kw = {"root_path": "/Trizetto Data", "log": lambda *a: None}
    store = Store(str(tmp_path / "s.sqlite"))
    store.conn.execute("INSERT INTO meta (key, value) VALUES ('sync:cursor', 'cursor-0')")
    store.conn.commit()
    client = _dropbox(files, reset_delta_after=1)
    st = sync(store, client=client, data_dir=str(tmp_path / "m1"), **kw)
    assert [c for c in client.calls if c != "download"] == ["list_continue:cursor-0", "list_folder"] and st["mode"] == "full"
    assert _names(st["new_files"]) == ["remit_600.RMT", "remit_601.RMT"] and st["downloaded"] == 2 and st["unchanged"] == 1
    assert store.conn.execute("SELECT value FROM meta WHERE key='sync:cursor'").fetchone()[0] == "cursor-1"
    other = Store(str(tmp_path / "t.sqlite"))
    other.conn.execute("INSERT INTO meta (key, value) VALUES ('sync:cursor', 'cursor-0')")
    other.conn.commit()
    with pytest.raises(RuntimeError) as err:
        sync(other, client=_dropbox(files, reset_delta_after=1, full_fails=True), data_dir=str(tmp_path / "m2"), **kw)
    assert _names(err.value.new_files) == ["remit_600.RMT"]


def test_these_tests_load_without_paramiko():
    """Without paramiko, only the tests that need the stand-in SFTP server are skipped; the file itself still loads."""
    import subprocess
    import sys
    code = "import sys; sys.modules['paramiko'] = None; import tests.test_feeds as t; print(len([n for n in dir(t) if n.startswith('test_')]))"
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and int(r.stdout.strip()) > 20, r.stderr[-2000:]
