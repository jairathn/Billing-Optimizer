"""
Fixes from the review of the dashboard's API, the command line and the journal (remitwatch.api, remitwatch.cli,
remitwatch.actions): the server closed without a password, the TriZetto test that never sends a refused password again,
stale items out of the deadline figures, the Hidden count and a hide rule applied again, the let-go rule for claims
nobody answered, who an unanswered claim is about, note names with names off, the note lookups, the backup and the move
from Vercel. Every patient, claim, number and amount is made up.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import sqlite3

import pytest

from tests.test_cloud import box  # noqa: F401  (a fake Dropbox with the state folder in it)
from tests.test_features import build_store, claim, clean_visits, svc
from tests.test_feeds import sftp  # noqa: F401  (the stand-in TriZetto)
from tests.test_workable import open_claims  # noqa: F401  (claims EMA shows sent that no payer answered)

XHR = {"X-Requested-With": "remitwatch"}
PW = "a-long-site-passphrase-1"
NA, REJ = "CB0000501C017", "CB0000505C017"          # sent and never answered; rejected before it reached the payer


def basic(user: str, pw: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def _clean_env(monkeypatch) -> None:
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_DIR", "TRIZETTO_SFTP_USER",
              "TRIZETTO_SFTP_PASSWORD", "TRIZETTO_SFTP_SERVERLESS", "MODMED_CLIENT_ID", "MODMED_PRIVATE_KEY", "MODMED_CLIENT_SECRET",
              "SRFAX_ACCESS_ID", "SRFAX_ACCESS_PWD", "ANTHROPIC_API_KEY", "VERCEL", "VERCEL_ENV", "CRON_SECRET"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """The AWS site: a server's store with clean visits and two visits denied for medical necessity."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch.detect import Detector
    denied = lambda i: claim(i, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~", dos="20261101"), dos="20261101")   # noqa: E731
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [denied(300), denied(301)], pay_date="20261110")
    Detector(store, run_id="t").run()
    store.close()
    db = str(tmp_path / "s.sqlite")
    api.ensure_server_schema(db)
    _clean_env(monkeypatch)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_DB", db)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setattr(api, "USERNAME", "biller")
    monkeypatch.setattr(api, "SHOW_PHI", False)
    monkeypatch.setattr(api, "_FAILED", {})
    return type("Server", (), {"c": TestClient(api.app), "api": api, "db": db, "tmp": tmp_path})


@pytest.fixture()
def chase(open_claims, monkeypatch):  # noqa: F811
    """The dashboard on open_claims' store: two claims nobody answered (one rejected before the payer), and ordinary items."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    open_claims.close()
    db = os.path.join(os.path.dirname(open_claims.db_path), "s.sqlite")
    api.ensure_server_schema(db)
    _clean_env(monkeypatch)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setattr(api, "SHOW_PHI", False)
    conn = sqlite3.connect(db)
    ids = dict(conn.execute("SELECT patient_control_number, finding_id FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER')"))
    other = conn.execute("SELECT finding_id FROM findings WHERE signal NOT IN ('NO_ANSWER','REJECTED_PRE_PAYER') AND status='open' "
                         "ORDER BY finding_id LIMIT 1").fetchone()[0]
    conn.close()
    return type("Chase", (), {"c": TestClient(api.app), "api": api, "db": db, "na": ids[NA], "rej": ids[REJ], "other": other})


def _status(db: str, fid: str) -> str:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------- the server is closed without a password
def test_a_server_with_no_password_serves_nothing_until_one_is_set(server, monkeypatch):
    c, api = server.c, server.api
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)          # the AWS site, not a developer's machine
    assert c.get("/api/findings").status_code == 503 and c.get("/api/summary").status_code == 503
    assert c.post("/api/uploads", params={"name": "x.csv"}, content=b"a,b", headers=XHR).status_code == 503
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert c.get("/setup").status_code == 200                           # it says only what is missing
    locked = c.get("/api/setup/status").json()
    assert locked["locked"] and [x["key"] for x in locked["checks"]] == ["password"] and locked["checks"][0]["detail"] == "not set"
    assert "configure.sh" in locked["checks"][0]["fix"] and "Vercel" not in locked["checks"][0]["fix"]
    # a developer's own machine opts in to running open; Vercel stays closed as before
    monkeypatch.setenv("REMITWATCH_ALLOW_OPEN", "1")
    assert c.get("/api/findings").status_code == 200
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN")
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    assert api.password_problem() == "not set" and not api.open_allowed()
    assert "Vercel" in api._password_fix("not set")


def test_a_short_password_already_in_use_keeps_the_server_open_to_its_staff_and_is_flagged(server, monkeypatch, capsys):
    c, api = server.c, server.api
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)
    monkeypatch.setattr(api, "PASSWORD", "short-pw1")                   # nine characters: what an older setup may have
    monkeypatch.setenv("REMITWATCH_PASSWORD", "short-pw1")
    assert c.get("/api/findings").status_code == 401
    assert c.get("/api/findings", headers=basic("biller", "short-pw1")).status_code == 200
    check = next(x for x in c.get("/api/setup/status", headers=basic("biller", "short-pw1")).json()["checks"] if x["key"] == "password")
    assert check["ok"] is True and check["warn"] is True and "shorter than 12" in check["detail"] and "configure.sh" in check["fix"]
    api.log_password_state()
    err = capsys.readouterr().err
    assert "WARNING" in err and "shorter than 12" in err and "short-pw1" not in err
    # no password at all: the log says the site serves nothing
    monkeypatch.setattr(api, "PASSWORD", "")
    api.log_password_state()
    assert "serves no data" in capsys.readouterr().err
    # a long one: nothing to say
    monkeypatch.setattr(api, "PASSWORD", PW)
    assert api.password_warning() is None
    api.log_password_state()
    assert capsys.readouterr().err == ""
    # the same short password on Vercel keeps that site locked, as before
    monkeypatch.setattr(api, "PASSWORD", "short-pw1")
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    assert api.password_problem() == "shorter than 12 characters"
    # a password no login can carry is no password on either host
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setattr(api, "PASSWORD", "pässword-long-enough")
    assert "browser cannot send" in api.password_problem()


# --------------------------------------------------------------------------- the TriZetto test and a refused password
def test_the_trizetto_test_never_sends_a_refused_password_again(server, sftp, monkeypatch):  # noqa: F811
    c = server.c
    for k, v in sftp.env().items():
        monkeypatch.setenv(k, v)
    sftp.refuse_password = True
    first = c.post("/api/trizetto/test", headers=XHR).json()
    assert first["ok"] is False and first["refused"] and len(sftp.logins) == 1
    assert first["held"]["kind"] == "password"                          # kept with its kind: Today says "refused the password"
    for _ in range(3):              # a person presses Test again and again: TriZetto sees nothing more, the page says until when
        again = c.post("/api/trizetto/test", headers=XHR).json()
        assert again["ok"] is False and again["held"]["retry_at"] and "Not tried now" in again["steps"][-1]["detail"]
    assert len(sftp.logins) == 1
    conn = sqlite3.connect(server.db)
    held = json.loads(conn.execute("SELECT value FROM meta WHERE key='trizetto:signin_refused'").fetchone()[0])
    conn.close()
    assert held["at"] == first["held"]["at"]                            # the presses did not renew the hold either
    # a new password is tried at once, and a sign-in that works ends the hold
    sftp.password, sftp.refuse_password = "pw-456-new", False
    monkeypatch.setenv("TRIZETTO_SFTP_PASSWORD", "pw-456-new")
    ok = c.post("/api/trizetto/test", headers=XHR).json()
    assert len(sftp.logins) == 2 and "held" not in ok and any(s["step"] == "Sign-in" and s["ok"] for s in ok["steps"])


def test_a_refusal_recorded_on_vercel_keeps_its_kind():
    from remitwatch import trizetto
    from remitwatch.actions import apply
    conn = sqlite3.connect(":memory:")
    s = trizetto.Settings(user="RW-LOGIN", password="pw-123", host="sftp.example.org")
    apply(conn, {"kind": "trizetto_hold", "op": "set", "who": trizetto.credential_tag(s), "detail": "refused",
                 "refused_kind": "password", "at": trizetto._now()})
    held = trizetto.refusal(conn, s)
    assert held and held["kind"] == "password" and "refused this user name and password" in held["text"]


def test_vercel_does_not_say_it_reads_trizetto_and_the_key_address_names_the_hosts_own_settings(server, monkeypatch):
    c, api = server.c, server.api
    monkeypatch.setenv("TRIZETTO_SFTP_USER", "RW-LOGIN")
    monkeypatch.setenv("TRIZETTO_SFTP_PASSWORD", "pw-123")
    assert c.get("/api/meta").json()["trizetto"] is True                # the server reads it
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    assert api._trizetto_here() is False                                # Vercel does not, unless told to
    monkeypatch.setenv("TRIZETTO_SFTP_SERVERLESS", "1")
    assert api._trizetto_here() is True
    # ModMed's key address with no key set: where to set it depends on the host
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    r = c.get("/.well-known/jwks.json")
    assert r.status_code == 404 and "configure.sh" in r.json()["detail"] and "Vercel" not in r.json()["detail"]
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    r = c.get("/.well-known/jwks.json")
    assert r.status_code == 404 and "Vercel" in r.json()["detail"]


# --------------------------------------------------------------------------- stale items are in no total
def test_an_item_the_rules_no_longer_find_is_out_of_the_deadline_figures(server):
    c, db = server.c, server.db

    def due() -> dict:
        return {b["bucket"]: (b["n"], b["shortfall_cents"]) for b in c.get("/api/summary").json()["deadlines"]}
    before = due()
    conn = sqlite3.connect(db)
    fid, cents = conn.execute("""SELECT finding_id, shortfall_cents FROM findings WHERE status='open' AND appeal_deadline IS NOT NULL
                                 AND actionable IN ('appeal','correct_and_resubmit') AND signal != 'SECOND_RATE'
                                 ORDER BY finding_id LIMIT 1""").fetchone()
    # a person noted it, then the payer answered: the rules no longer find it, and it is kept only for its notes
    conn.execute("UPDATE findings SET stale=1, notes='Corrected in EMA and sent again' WHERE finding_id=?", (fid,))
    conn.commit()
    conn.close()
    after = due()
    assert sum(n for n, _ in after.values()) == sum(n for n, _ in before.values()) - 1
    assert sum(x or 0 for _, x in after.values()) == sum(x or 0 for _, x in before.values()) - cents


# --------------------------------------------------------------------------- hidden reasons
def test_hidden_counts_only_what_the_rule_hid_and_show_again_brings_back(server):
    c, db = server.c, server.db
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT finding_id, payer_group, signal FROM findings WHERE signal='DENIED_MEDICAL_NECESSITY' ORDER BY finding_id").fetchall()
    conn.close()
    assert len(rows) == 2
    (a, pg, sig), (b, _, _) = rows
    # a person lets one go by hand; then the reason is hidden for the payer, which takes the other
    assert c.post(f"/api/findings/{b}/status", json={"status": "dismissed", "notes": "the patient paid it"}, headers=XHR).status_code == 200
    n = c.get("/api/hide/preview", params={"payer_group": pg, "signal": sig}).json()["n"]          # shown first (hotfix 2, H7)
    assert c.post("/api/hide", json={"payer_group": pg, "signal": sig, "note": "this payer does not pay these", "confirm": n},
                  headers=XHR).json()["hidden"] == 1
    rule = c.get("/api/hide").json()["rules"]
    assert len(rule) == 1 and rule[0]["n"] == 1                         # the rule's own, not the person's decision
    assert c.post("/api/hide", json={"payer_group": pg, "signal": sig, "remove": True}, headers=XHR).json()["ok"]
    assert _status(db, a) == "open" and _status(db, b) == "dismissed"   # Show again brings back exactly what it counted
    assert c.get("/api/hide").json()["rules"] == []                     # a removed rule is kept, dated, but not listed
    assert c.post("/api/hide", json={"payer_group": pg, "signal": sig, "remove": True}, headers=XHR).status_code == 404


def _hide_store() -> sqlite3.Connection:
    from remitwatch.detect import ensure_schema
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    conn.execute("INSERT INTO findings (finding_id, status, payer_group, signal) VALUES ('F1', 'open', 'Payer', 'DENIED_MEDICAL_NECESSITY')")
    return conn


def test_a_hide_rules_entries_applied_again_in_any_order_change_nothing():
    from remitwatch.actions import apply
    add = {"kind": "hide_rule", "op": "add", "payer_group": "Payer", "signal": "DENIED_MEDICAL_NECESSITY", "actor": "biller",
           "at": "2026-11-01T10:00:00+00:00"}
    rm = dict(add, op="remove", at="2026-11-02T10:00:00+00:00")
    conn = _hide_store()
    status = lambda: conn.execute("SELECT status FROM findings").fetchone()[0]                                    # noqa: E731
    in_force = lambda: conn.execute("SELECT COUNT(*) FROM hide_rules WHERE removed_at IS NULL").fetchone()[0]     # noqa: E731
    hidden = lambda: conn.execute("SELECT COUNT(*) FROM finding_events WHERE kind='hidden'").fetchone()[0]        # noqa: E731
    apply(conn, add)
    assert status() == "dismissed" and in_force() == 1
    apply(conn, rm)
    assert status() == "open" and in_force() == 0
    for entry in (add, rm, add):        # a serverless instance loading a new copy applies what is still in the journal again
        apply(conn, entry)
    assert status() == "open" and in_force() == 0 and hidden() == 1
    apply(conn, dict(add, at="2026-11-03T10:00:00+00:00"))             # hidden again later: in force, and it hides again
    assert status() == "dismissed" and in_force() == 1 and hidden() == 2
    # another instance's copy that gets the remove before the add it follows: the older add is skipped
    other = _hide_store()
    apply(other, rm)
    apply(other, add)
    assert other.execute("SELECT status FROM findings").fetchone()[0] == "open"
    assert other.execute("SELECT COUNT(*) FROM hide_rules WHERE removed_at IS NULL").fetchone()[0] == 0


def test_a_store_from_before_removed_rules_were_kept_still_hides_and_shows_again():
    from remitwatch.actions import apply, apply_hide_rules
    conn = _hide_store()
    conn.execute("CREATE TABLE hide_rules (payer_group TEXT, signal TEXT, actor TEXT, at TEXT, note TEXT, PRIMARY KEY (payer_group, signal))")
    conn.execute("INSERT INTO hide_rules VALUES ('Payer', 'DENIED_MEDICAL_NECESSITY', 'biller', '2026-11-01T10:00:00+00:00', 'never pays')")
    assert apply_hide_rules(conn) == 1 and conn.execute("SELECT status FROM findings").fetchone()[0] == "dismissed"
    apply(conn, {"kind": "hide_rule", "op": "remove", "payer_group": "Payer", "signal": "DENIED_MEDICAL_NECESSITY",
                 "at": "2026-11-02T10:00:00+00:00"})
    assert conn.execute("SELECT status FROM findings").fetchone()[0] == "open" and apply_hide_rules(conn) == 0


# --------------------------------------------------------------------------- claims nobody answered: one at a time, with a note
def test_claims_nobody_answered_are_let_go_one_at_a_time_with_a_note(chase):
    c, db, na, rej, other = chase.c, chase.db, chase.na, chase.rej, chase.other
    for status in ("dismissed", "lost"):
        r = c.post("/api/findings/bulk-status", json={"ids": [other, na], "status": status}, headers=XHR)
        assert r.status_code == 400 and "one at a time" in r.json()["detail"]
        assert _status(db, other) == "open" and _status(db, na) == "open"   # nothing changed, the ordinary one included
    r = c.post("/api/findings/bulk-status", json={"ids": [other, na, rej], "status": "in_progress"}, headers=XHR)
    assert r.status_code == 200 and r.json()["updated"] == 3
    for notes in (None, "", "   "):
        body = {"status": "dismissed"} if notes is None else {"status": "dismissed", "notes": notes}
        r = c.post(f"/api/findings/{na}/status", json=body, headers=XHR)
        assert r.status_code == 400 and "only with a note" in r.json()["detail"] and _status(db, na) == "in_progress"
    assert c.post(f"/api/findings/{rej}/status", json={"status": "lost"}, headers=XHR).status_code == 400
    assert c.post(f"/api/findings/{na}/status", json={"status": "dismissed", "notes": "Paid by the patient at the desk"}, headers=XHR).status_code == 200
    assert c.post(f"/api/findings/{rej}/status", json={"status": "lost", "notes": "Past the payer's limit"}, headers=XHR).status_code == 200
    conn = sqlite3.connect(db)
    notes = conn.execute("SELECT note FROM finding_events WHERE finding_id=? AND kind='manual' ORDER BY id DESC LIMIT 1", (na,)).fetchone()[0]
    conn.close()
    assert notes == "Paid by the patient at the desk" and _status(db, na) == "dismissed" and _status(db, rej) == "lost"
    # an ordinary item is let go with a reason too (review C1: money is let go only by a person with a reason), and an item
    # that is not there is still not found
    r = c.post(f"/api/findings/{other}/status", json={"status": "dismissed"}, headers=XHR)
    assert r.status_code == 400 and "needs a reason" in r.json()["detail"] and _status(db, other) == "in_progress"
    assert c.post(f"/api/findings/{other}/status", json={"status": "dismissed", "notes": "billed to the patient"}, headers=XHR).status_code == 200
    assert c.post("/api/findings/no-such-item/status", json={"status": "dismissed"}, headers=XHR).status_code == 404


# --------------------------------------------------------------------------- who an unanswered claim is about
def test_an_unanswered_claim_shows_its_patient_and_member_id_from_emas_rows(chase, monkeypatch):
    c, db, api = chase.c, chase.db, chase.api
    conn = sqlite3.connect(db)
    conn.execute("UPDATE ema_rows SET policy_number='ZX0000501' WHERE claim_id=?", (NA,))
    conn.execute("UPDATE ema_rows SET policy_number='ZX0000505' WHERE claim_id=?", (REJ,))
    # the same EMA patient on another claim, one a payer answered: its remittance names the patient
    conn.execute("UPDATE claims SET patient_last='RIVERS', patient_first='JO', patient_middle=NULL WHERE patient_control_number='CB0000001C017'")
    conn.execute("INSERT INTO ema_rows (row_fingerprint, family, claim_id, patient_id) VALUES ('x1', 'charge_lines', 'CB0000001C017', 'P00501')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(api, "SHOW_PHI", True)
    items = {i["patient_control_number"]: i for i in c.get("/api/findings?status=all&limit=200").json()["items"]}
    assert items[NA]["patient"] == "RIVERS, JO" and items[NA]["member_id"] == "ZX0000501"
    assert items[REJ]["patient"] == "" and items[REJ]["member_id"] == "ZX0000505"    # no remittance names that patient
    d = c.get(f"/api/findings/{chase.na}").json()
    assert d["claim"] == {"patient": "RIVERS, JO", "member_id": "ZX0000501", "from_ema": True}
    view = c.get("/api/claims/view", headers={"X-Claim": NA}).json()
    assert view["patient"] == "RIVERS, JO" and view["member_id"] == "ZX0000501"
    assert "RIVERS, JO" in c.get("/api/findings.csv?status=all&signal=NO_ANSWER").text
    # names off: initials, as every other item shows, and no member ID anywhere
    monkeypatch.setattr(api, "SHOW_PHI", False)
    items = {i["patient_control_number"]: i for i in c.get("/api/findings?status=all&limit=200").json()["items"]}
    assert items[NA]["patient"] == "JR" and "member_id" not in items[NA]
    assert c.get(f"/api/findings/{chase.na}").json()["claim"] == {"patient": "JR", "from_ema": True}
    assert "claim" not in c.get(f"/api/findings/{chase.rej}").json()         # nothing to show without a name
    assert "ZX0000501" not in c.get("/api/claims/view", headers={"X-Claim": NA}).text


def test_with_names_on_the_search_finds_an_unanswered_claim_by_what_its_row_shows(chase, monkeypatch):
    """A claim no payer answered has no remittance, so its row's name and member ID come from EMA's rows. The search box
    finds it by them too, as it finds any other item by the name and member number its row shows. With names off it does
    not look at names or member numbers at all. Invented patient and numbers."""
    c, db, api = chase.c, chase.db, chase.api
    conn = sqlite3.connect(db)
    conn.execute("UPDATE ema_rows SET policy_number='ZX-0000-501' WHERE claim_id=?", (NA,))
    conn.execute("UPDATE claims SET patient_last='RIVERS', patient_first='JO', patient_middle=NULL WHERE patient_control_number='CB0000001C017'")
    conn.execute("INSERT INTO ema_rows (row_fingerprint, family, claim_id, patient_id) VALUES ('x1', 'charge_lines', 'CB0000001C017', 'P00501')")
    conn.commit()
    conn.close()

    def found(text: str) -> list[str]:
        return [i["patient_control_number"] for i in c.get("/api/findings?status=all&limit=200", headers={"X-Search": text}).json()["items"]]
    monkeypatch.setattr(api, "SHOW_PHI", True)
    for text in ("ZX0000501", "zx-0000-501", "0000501", "rivers jo", "Jo Rivers"):
        assert NA in found(text), text
    assert REJ not in found("rivers jo") and NA not in found("someone else") and NA not in found("ZX0000505")
    monkeypatch.setattr(api, "SHOW_PHI", False)
    assert NA not in found("rivers jo") and NA not in found("ZX0000501")


# --------------------------------------------------------------------------- notes: names, lookups and the batch
@pytest.fixture()
def notes_site(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from tests.test_uploads import make_store
    db = make_store(tmp_path / "server")
    _clean_env(monkeypatch)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.delenv("REMITWATCH_UPLOAD_DIR", raising=False)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setattr(api, "SHOW_PHI", False)
    monkeypatch.setattr(api, "_wake_cycle", lambda trigger="upload": None)
    c = TestClient(api.app)
    fid = c.get("/api/findings?signal=DENIED_MEDICAL_NECESSITY").json()["items"][0]["finding_id"]
    return type("Notes", (), {"c": c, "api": api, "db": db, "fid": fid})


def test_a_files_name_comes_in_a_header_and_is_never_shown_with_names_off(notes_site):
    from tests.test_uploads import NOTE, note_pdf
    c, db = notes_site.c, notes_site.db
    assert c.get("/api/uploads").json()["name_header"] is True           # the page sends the name in X-File-Name from now on
    r = c.post("/api/uploads", content=note_pdf(NOTE), headers=dict(XHR, **{"X-File-Name": "Sample%2C%20Pat%20-%20visit%2011-01.pdf"}))
    assert r.status_code == 200 and "Sample" not in r.text
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT name FROM uploads").fetchone()[0] == "Sample, Pat - visit 11-01.pdf"     # kept as it was sent
    conn.close()
    assert "Sample" not in c.get("/api/uploads").text
    assert c.post("/api/uploads", content=b"x", headers=XHR).status_code == 400                         # no name at all
    # a packet built from a file saved under the patient's name: listed by its kind only
    built = c.post("/api/appeals/build", data={"finding_id": notes_site.fid, "use_ai": "0"},
                   files={"file": ("Sample, Pat - visit 11-01.pdf", note_pdf(NOTE), "application/pdf")}, headers=XHR)
    assert built.status_code == 200, built.text
    assert built.json()["note_name"] == "Visit note.pdf"
    assert [p["note_name"] for p in c.get(f"/api/packets?finding_id={notes_site.fid}").json()] == ["Visit note.pdf"]
    assert c.get(f"/api/packets/{built.json()['packet_id']}").json()["note_name"] == "Visit note.pdf"
    assert "Sample, Pat" not in c.get(f"/api/findings/{notes_site.fid}").text
    notes_site.api.SHOW_PHI = True
    try:
        assert c.get(f"/api/packets?finding_id={notes_site.fid}").json()[0]["note_name"] == "Sample, Pat - visit 11-01.pdf"
    finally:
        notes_site.api.SHOW_PHI = False


def test_the_note_lookups_carry_the_names_switch_the_lookups_reason_and_the_batchs_first_day(notes_site, monkeypatch):
    from remitwatch import modmed, uploads
    api = notes_site.api
    seen: list[dict] = []

    def fake_upload(conn, key, pick=None, reveal=False, src=None):
        seen.append({"where": "upload", "reveal": reveal})
        return {"status": "not_found", "reason": "no uploaded note", "candidates": [], "source": "upload"}

    def fake_ema(mm, key, pick=None, budget=240.0, since=None, reveal=False):
        seen.append({"where": "ema", "reveal": reveal, "since": since})
        return {"status": "ambiguous", "reason": "Only the name agrees with the claim: a person confirms the note in the item.",
                "candidates": [{"id": "d1"}]}
    monkeypatch.setattr(uploads, "find_note", fake_upload)
    monkeypatch.setattr(modmed, "note_for_claim", fake_ema)
    monkeypatch.setattr(modmed, "client", lambda settings=None: object())
    monkeypatch.setattr(modmed.Settings, "configured", lambda self: True)
    monkeypatch.setattr(api, "_data_behind_days", lambda: 0)
    monkeypatch.setattr(api, "_practice_missing", lambda: [])
    monkeypatch.setattr(api, "_payer_number", lambda pg: "8005550142")
    from remitwatch import fax
    monkeypatch.setattr(fax, "provider", lambda env=None: object())
    for show in (False, True):
        monkeypatch.setattr(api, "SHOW_PHI", show)
        seen.clear()
        out = notes_site.c.post("/api/appeals/send", json={"finding_id": notes_site.fid}, headers=XHR).json()
        assert out["status"] == "held" and out["code"] == "ambiguous" and out["reason"].startswith("Only the name agrees"), out
        assert [s["reveal"] for s in seen] == [show, show]
        assert seen[-1]["since"] == "2026-11-01"                         # the first open note-item's date of service
    # the batch marks the item with the lookup's own reason
    r = notes_site.c.post("/api/appeals/from_ema", json={"finding_id": notes_site.fid, "skip_if_packet": True}, headers=XHR).json()
    assert r["status"] == "ambiguous"
    conn = sqlite3.connect(notes_site.db)
    mark = conn.execute("SELECT note FROM finding_events WHERE finding_id=? AND kind='note_lookup'", (notes_site.fid,)).fetchone()[0]
    conn.close()
    assert mark == "Build packets from EMA: Only the name agrees with the claim: a person confirms the note in the item."
    # the line stays in the item's history, and out of Progress's Activity, where ten a press would push out people's work
    assert "note_lookup" in [e["kind"] for e in notes_site.c.get(f"/api/findings/{notes_site.fid}").json()["events"]]
    assert "note_lookup" not in [e["kind"] for e in notes_site.c.get("/api/progress").json()["events"]]


def _send_ready(api, monkeypatch) -> None:
    """Everything appeals_send checks before the note, made ready: remittances current, the practice's details, a fax line
    and the payer's number. Nothing is faxed in these tests: each stops at the note."""
    from remitwatch import fax
    monkeypatch.setattr(api, "_data_behind_days", lambda: 0)
    monkeypatch.setattr(api, "_practice_missing", lambda: [])
    monkeypatch.setattr(api, "_payer_number", lambda pg: "8005550142")
    monkeypatch.setattr(fax, "provider", lambda env=None: object())


def test_a_name_only_match_is_held_with_the_lookups_own_words(notes_site, monkeypatch):
    from tests.test_uploads import NOTE, note_pdf
    c = notes_site.c
    assert c.post("/api/uploads", content=note_pdf(NOTE), headers=dict(XHR, **{"X-File-Name": "a.pdf"})).status_code == 200
    _send_ready(notes_site.api, monkeypatch)
    out = c.post("/api/appeals/send", json={"finding_id": notes_site.fid}, headers=XHR).json()
    assert out["status"] == "held" and out["code"] == "ambiguous" and out["reason"].startswith("Only the name agrees with the claim."), out


def test_two_patients_of_one_name_are_told_apart_only_where_names_are_shown(notes_site, monkeypatch):
    """Two notes for the visit's date under the claim's patient name, with two patient IDs: with names off nothing tells the
    two apart, so the note is attached by hand; with names on each candidate says who it is, and a person's pick builds."""
    from tests.test_uploads import NOTE, note_pdf
    c, api = notes_site.c, notes_site.api
    other = [NOTE[0], "Patient: Pat Sample    DOB: 03/04/1970    Patient ID: EMA99", *NOTE[2:]]
    for name, lines in (("a.pdf", NOTE), ("b.pdf", other)):
        assert c.post("/api/uploads", content=note_pdf(lines), headers=dict(XHR, **{"X-File-Name": name})).status_code == 200
    r = c.post("/api/appeals/from_ema", json={"finding_id": notes_site.fid, "use_ai": False}, headers=XHR).json()
    assert r["status"] == "ambiguous" and r["candidates"] == [] and "attach it to the item" in r["reason"], r
    monkeypatch.setattr(api, "SHOW_PHI", True)
    r = c.post("/api/appeals/from_ema", json={"finding_id": notes_site.fid, "use_ai": False}, headers=XHR).json()
    assert r["status"] == "ambiguous" and sorted(x["patient"]["birth_date"] for x in r["candidates"]) == ["1960-01-02", "1970-03-04"], r
    pick = next(x for x in r["candidates"] if x["patient"]["birth_date"] == "1970-03-04")
    built = c.post("/api/appeals/from_ema", json={"finding_id": notes_site.fid, "document_id": pick["id"], "use_ai": False}, headers=XHR).json()
    assert built["status"] == "built", built


def test_the_ready_queue_keeps_only_items_the_batch_can_build(notes_site, monkeypatch):
    api = notes_site.api
    monkeypatch.setattr(api, "_note_ready", lambda items: {i["finding_id"]: "ambiguous" for i in items})
    assert api.appeals_ema_queue(limit=20, ready=1, _="t")["items"] == []
    monkeypatch.setattr(api, "_note_ready", lambda items: {i["finding_id"]: "found" for i in items})
    assert notes_site.fid in [i["finding_id"] for i in api.appeals_ema_queue(limit=20, ready=1, _="t")["items"]]


# --------------------------------------------------------------------------- the backup
def test_a_backup_is_whole_or_absent_and_one_at_a_time(server, monkeypatch):
    from remitwatch import cli
    db = server.db
    dest = server.tmp / "backups"
    dest.mkdir()
    # what a copy cut short by a deploy left behind: the raw copy, its WAL, a gzip not finished, and the old code's raw copy
    for name in ("remitwatch-20261120-010101.sqlite.part", "remitwatch-20261120-010101.sqlite.part-wal",
                 "remitwatch-20261120-010101.sqlite.gz.part", "remitwatch-20261119-010101.sqlite"):
        (dest / name).write_bytes(b"cut short")
    (dest / "remitwatch-20261118-010101.sqlite.gz").write_bytes(gzip.compress(b"an older whole copy"))
    (dest / "notes.txt").write_text("not ours")
    path = cli.backup_store(db, str(dest), keep=7)
    names = sorted(os.listdir(dest))
    assert names == sorted(["notes.txt", "remitwatch-20261118-010101.sqlite.gz", os.path.basename(path)]), names
    copy = server.tmp / "restored.sqlite"
    copy.write_bytes(gzip.open(path).read())
    c = sqlite3.connect(str(copy))
    assert c.execute("SELECT COUNT(*) FROM findings").fetchone()[0] > 0
    assert c.execute("SELECT COUNT(*) FROM meta WHERE key='backup:lock'").fetchone()[0] == 0       # the copy carries no lock
    c.close()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM meta WHERE key='backup:lock'").fetchone()[0] == 0    # released
    # while one copy is being made, under a lock the deploy waits for, another is refused (and the daily one skipped)
    token = cli._take_backup_lock(db)
    held = json.loads(conn.execute("SELECT value FROM meta WHERE key='backup:lock'").fetchone()[0])
    assert held["pid"] == os.getpid() and held["started"] and held["token"] == token
    import socket
    assert held["host"] == socket.gethostname()         # the container it runs in, as the cycle's lock says it
    with pytest.raises(cli.BackupBusy):
        cli.backup_store(db, str(dest), keep=7)
    assert "skipped" in cli._daily_backup(db)
    assert cli.main(["--db", db, "backup", "--dir", str(dest)]) == 1
    cli._drop_backup_lock(db, token)
    # a lock left by a copy that died an hour ago is taken over
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('backup:lock', ?)",
                 (json.dumps({"token": "old", "started": "2020-01-01T00:00:00+00:00", "pid": 1}),))
    conn.commit()
    conn.close()
    assert os.path.exists(cli.backup_store(db, str(dest), keep=7))


# --------------------------------------------------------------------------- the move from Vercel
def test_the_move_from_vercel_does_not_leave_what_people_did_behind_without_a_word(box, tmp_path, monkeypatch, capsys):  # noqa: F811
    from remitwatch import cli, cloud
    from tests.test_cloud import DATA, cycle, visits_with_downcodes, write_remits
    root, client, state = box
    write_remits(root / DATA, visits_with_downcodes())
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    # a status recorded on the Vercel site for an item its store does not hold yet
    cloud.record_action(state, {"kind": "status", "finding_id": "f" * 24, "status": "appealed", "notes": "faxed by hand", "actor": "biller"})
    db = str(tmp_path / "server" / "remitwatch.sqlite")
    assert cli.main(["--db", db, "import-cloud"]) == 1
    out = capsys.readouterr().out
    assert "still waits for an item" in out and "f" * 24 in out and "status appealed" in out and "faxed by hand" not in out
    assert "--drop-waiting" in out and not os.path.exists(db)          # nothing written
    assert cli.main(["--db", db, "import-cloud", "--drop-waiting"]) == 0
    moved = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert moved["journal_waiting"] == 1 and len(moved["journal_left_behind"]) == 1 and "waiting" not in moved
    # the proof says so too: the entry is on neither store
    assert cli.main(["--db", db, "compare-cloud"]) == 2
    report = capsys.readouterr().out
    assert "journal entries still waiting" in report and "DIFFERENT" in report


def test_a_move_onto_a_server_with_its_own_data_never_points_at_force(server, monkeypatch, capsys):
    from remitwatch import cli
    conn = sqlite3.connect(server.db)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('pipeline:lock', ?)",
                 (json.dumps({"started": cli.time.strftime("%Y-%m-%dT%H:%M:%S+00:00", cli.time.gmtime()), "pid": 4242, "token": "t"}),))
    conn.commit()
    conn.close()
    assert cli.main(["--db", server.db, "import-cloud"]) == 1
    out = capsys.readouterr().out
    assert "already keeps its own data" in out and "--force" not in out


# --------------------------------------------------------------------------- one item sent from its panel (review C1)
from tests.test_fax import PAYER_FAX, site  # noqa: E402,F401  (the fax world: a fake line records what would go out)


def _ready_to_fax(site, monkeypatch, **cols):
    """The rate item of the fax world, its payer's appeal number saved, the remittances current, and its row set to cols."""
    import remitwatch.api as api_mod
    monkeypatch.setattr(api_mod, "_data_behind_days", lambda: 0)
    assert site.c.post("/api/faxes/payer", json={"payer_group": "Anthem / BCBS", "number": PAYER_FAX}, headers=XHR).status_code == 200
    fid = site.ids["UNDERPAID_VS_OWN_RATE"]               # a rate letter: no visit note needed, so nothing else holds it
    conn = sqlite3.connect(site.db)
    conn.execute(f"UPDATE findings SET {', '.join(k + '=?' for k in cols)} WHERE finding_id=?", (*cols.values(), fid))
    conn.commit()
    conn.close()
    return fid


def _failed_try(db, fid):
    conn = sqlite3.connect(db)
    conn.execute("""INSERT INTO faxes (fax_key, direction, finding_id, payer_group, number, purpose, level, provider, provider_id, status,
                                       pages, error, created_at, updated_at) VALUES ('try-1','out',?,'Anthem / BCBS','8005550142','appeal',1,
                                       'fake','F9999','failed',2,'busy','2026-11-12T10:00:00+00:00','2026-11-12T10:05:00+00:00')""", (fid,))
    conn.commit()
    conn.close()


@pytest.mark.parametrize("block,state", [("paid", "done"), ("credit", "work_now"), ("reprocessed", "work_now"), (None, "done"),
                                         (None, "accepted")])
def test_send_again_never_faxes_a_line_found_paid_credited_reprocessed_or_closed_since(site, monkeypatch, block, state):  # noqa: F811
    """Review C1 (high): "Send the appeal again" (an item marked Sent whose every fax failed) skipped the appeal_block
    check, which ran only while the item was open or started. A failed appeal the run since found paid, credited,
    reprocessed or closed must never go."""
    fid = _ready_to_fax(site, monkeypatch, status="appealed", appealed_at="2026-11-12T10:00:00+00:00", work_state=state, appeal_block=block)
    _failed_try(site.db, fid)
    r = site.c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    assert r["status"] == "held" and r["code"] == "blocked" and site.line.sent == {}, (block, state, r)


def test_send_again_still_goes_when_it_waits_only_on_its_own_failed_fax(site, monkeypatch):  # noqa: F811
    """The "waiting" its own failed fax's Marked Sent gives it does not stop the second try: that is what sending again is for."""
    fid = _ready_to_fax(site, monkeypatch, status="appealed", appealed_at="2026-11-12T10:00:00+00:00", work_state="waiting", appeal_block="waiting")
    _failed_try(site.db, fid)
    r = site.c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    assert r["status"] == "sent" and len(site.line.sent) == 1, r


@pytest.mark.parametrize("block", ["settled_in_ema", "likely_paid"])
def test_a_line_ema_shows_settled_or_the_archive_shows_likely_paid_goes_only_after_a_person_checked(site, monkeypatch, block):  # noqa: F811
    """Review C1 (medium): the one-item send held only paid, credit, reprocessed and waiting; a line EMA shows settled, or
    the archive shows likely paid (orchestrator decision 4: check first), was faxed. It is held until the person says they
    checked it is still unpaid ("checked"), then it goes."""
    fid = _ready_to_fax(site, monkeypatch, status="open", work_state="work_now", appeal_block=block)
    r = site.c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    assert r["status"] == "held" and r["code"] == "check_first" and r["block"] == block and site.line.sent == {}, r
    r = site.c.post("/api/appeals/send", json={"finding_id": fid, "checked": True}, headers=XHR).json()
    assert r["status"] == "sent" and len(site.line.sent) == 1, r


def test_one_item_inside_the_payers_own_limit_is_sent_from_its_panel(site, monkeypatch):  # noqa: F811
    """Review C1 (high), orchestrator decision 2: an item past its safe date but inside the payer's own limit is appealed
    one at a time from its panel; the send held it as expired on the safe date alone. Past both dates it is held, and the
    message names the date that closed."""
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).date()
    day = lambda n: (today + dt.timedelta(days=n)).isoformat()     # noqa: E731
    fid = _ready_to_fax(site, monkeypatch, status="open", work_state="work_now", appeal_block=None, appeal_deadline=day(-3), legal_deadline=day(-1))
    r = site.c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    assert r["status"] == "held" and r["code"] == "expired" and day(-1) in r["reason"] and "own limit" in r["reason"], r
    conn = sqlite3.connect(site.db)
    conn.execute("UPDATE findings SET legal_deadline=? WHERE finding_id=?", (day(60), fid))
    conn.commit()
    conn.close()
    r = site.c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    assert r.get("code") != "expired" and r["status"] == "sent" and len(site.line.sent) == 1, r


def test_the_note_match_reads_its_body_after_the_sign_in(server, monkeypatch):
    """needs_A2 F.2 (the A2 security review): a Body() parameter is read and parsed before the sign-in, so anyone could
    make the server parse a large body on POST /api/uploads/notes/{id}/match. With a password set and no login, a
    malformed or oversized body is refused by the sign-in (401), never parsed first (422); signed in, a bad body is a 400
    and an oversized one a 413, and a good one is answered as before."""
    monkeypatch.setattr(server.api, "PASSWORD", "a-long-enough-password")
    path = "/api/uploads/notes/" + "a" * 24 + "/match"
    hdr = dict(XHR, **{"Content-Type": "application/json"})
    assert server.c.post(path, content=b"{not json", headers=hdr).status_code == 401
    assert server.c.post(path, content=b"[" * 100_000, headers=hdr).status_code == 401
    me = dict(hdr, **basic("biller", "a-long-enough-password"))
    assert server.c.post(path, content=b"{not json", headers=me).status_code == 400
    assert server.c.post(path, content=b" " * 70_000, headers=me).status_code == 413
    r = server.c.post(path, json={"pick": 0}, headers=dict(XHR, **basic("biller", "a-long-enough-password")))
    assert r.status_code == 400 and "note" in r.json()["detail"].lower()      # no such note: the route itself answers
