"""
The API and the worklist pages over EMA's reports and the item states (unit C1 of push 2): the new finding columns, the
state and EMA filters (counts, tiles and lists agree), the list sorted by what working an item is worth, an item's
story, verdict and next step beside its panel, the claim view's story, the bulk appeal list (unit B's rule, what it leaves
out and why), the one-item send that never appeals a paid, credited, reprocessed or waiting line, a person's decision on a
held EMA report, the owner's write-off policy in the journal, hide rules at payer x reason x code x modifier x payer's
reason code, the page's own code in node, and every route on both hosts: a server's store and Vercel's dashboard copy.

Every patient, claim, check, payer and amount is invented (tests/ema_fixtures.py). The 835 side is written straight into
the store's remittance tables; the EMA side goes through the real readers, the posting ledger and its refresh; the
Detector runs as the cycle runs it, on a fixed day.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import shutil
import sqlite3
import subprocess

import pytest

from remitwatch import ema_ledger as el
from remitwatch import ema_reports as er
from remitwatch.detect import Detector
from remitwatch.store import Store
from tests import ema_fixtures as F

D = dt.date
P = F.PAYERS
SVC = D(2026, 3, 2)
S = SVC.isoformat()
TODAY = "2026-05-03"
SALT_HEX = "cd" * 32
XHR = {"X-Requested-With": "remitwatch"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "remitwatch", "static")
NODE = shutil.which("node")
NEW_COLS = ("written_off_source", "written_off_kind", "written_off_when", "ema_writeoff_evidence", "ema_link", "ema_status", "ema_status_src",
            "ema_as_of", "ema_balance_cents", "ema_worked_on", "ema_worked_how", "ema_outcome", "ema_outcome_on", "source", "work_state",
            "follow_up_on", "state_basis", "appeal_block", "legal_deadline", "expected_value_cents", "recovery_rate", "recovery_basis")


# ------------------------------------------------------------------------------------------------- the invented world
_TX = itertools.count(1)
_N = itertools.count(1)


class Log(list):
    def __call__(self, *a):
        self.append(" ".join(str(x) for x in a))


def _remit(store, *, trace, paid_on, claims, payer="BLUE CROSS TEST", status="1"):
    """One 835 payment written straight into the store's tables. claims: dicts with pcn and lines [(svc, code, charge $,
    paid $, [(group, reason, $), ...])]."""
    c = store.conn
    tk = f"TX{next(_TX):05d}"
    total = sum(round(ln[3] * 100) for cl in claims for ln in cl["lines"])
    c.execute("INSERT INTO transactions (transaction_key, trace_number, payment_date, payer_name, superseded, claim_paid_total_cents, "
              "payment_amount_cents) VALUES (?,?,?,?,0,?,?)", (tk, trace, paid_on, payer, total, total))
    for i, cl in enumerate(claims):
        ck = f"{tk}-{i}"
        c.execute("INSERT INTO claims (claim_key, transaction_key, patient_control_number, patient_last, patient_first, patient_id, payment_date, "
                  "trace_number, statement_from, claim_status_code, charge_cents, paid_cents, payer_name, crossover_carrier_name) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ck, tk, cl["pcn"], cl.get("last", "TESTPATIENT"), cl.get("first", "ALPHA"), "MEMBER000001", paid_on, trace, cl["lines"][0][0],
                   cl.get("status", status), sum(round(ln[2] * 100) for ln in cl["lines"]), sum(round(ln[3] * 100) for ln in cl["lines"]), payer,
                   cl.get("crossover")))
        for j, ln in enumerate(cl["lines"]):
            svc, code, chg, paid = ln[:4]
            lk = f"{ck}-{j}"
            c.execute("INSERT INTO lines (line_key, claim_key, transaction_key, procedure_code, service_date, charge_cents, paid_cents, "
                      "patient_control_number, payment_date, payer_name, claim_status_code) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (lk, ck, tk, code, svc, round(chg * 100), round(paid * 100), cl["pcn"], paid_on, payer, cl.get("status", status)))
            for g, r, amt in (ln[4] if len(ln) > 4 else []):
                c.execute("INSERT INTO adjustments (transaction_key, claim_key, line_key, level, group_code, reason_code, amount_cents) "
                          "VALUES (?,?,?,?,?,?,?)", (tk, ck, lk, "line", g, r, round(amt * 100)))
    c.commit()
    return tk


def _charge(link, cpt="99213", amount=150, patient=0, svc=SVC, posted=None, **kw):
    return F.production_row(link=link, cpt=cpt, charges=amount, patient=patient, svc=svc, posted=posted or svc, **kw)


def _pay(link, check, amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 3, 31), payer=P["bcbs"], **kw):
    return F.production_row(link=link, cpt=cpt, ins_paid=amount, check=check, patient=patient, svc=svc, posted=posted, payer=payer, **kw)


def _adj(link, reason, amount, cpt="99213", patient=0, svc=SVC, posted=D(2026, 3, 31), payer=P["bcbs"], **kw):
    return F.production_row(link=link, cpt=cpt, reason=reason, patient=patient, svc=svc, posted=posted, payer=payer, noncontractual=amount, **kw)


def build_world(path: str, tmp) -> None:
    """A server's store after one cycle: a denial EMA wrote off the day it posted (auto write-off, work now), a claim paid
    in EMA on a check no 835 carries, a claim still in EMA's print batch (never sent), an 835 payment EMA never posted,
    a claim the payer sent on to another carrier (waiting), and a denial only EMA's postings show."""
    global _TX
    _TX = itertools.count(1)
    store = Store(path)
    er.ensure_schema(store.conn)
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)", (SALT_HEX,))
    store.conn.commit()
    lg = el.Ledger.open(os.path.join(os.path.dirname(path), "ema_ledger.sqlite"))
    try:
        _remit(store, trace=F.check_no(1), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(1), "lines": [(S, "99213", 150, 0, [("CO", "197", 150)])]}])
        _remit(store, trace="777000111", paid_on="2026-04-01",
               claims=[{"pcn": F.claim_id(7), "last": "SAMPLE", "first": "BRAVO", "lines": [(S, "99214", 200, 120)]}])
        _remit(store, trace=F.check_no(3), paid_on="2026-03-28",
               claims=[{"pcn": F.claim_id(3), "last": "SAMPLE", "first": "CHARLIE", "lines": [(S, "99214", 200, 0, [("CO", "B11", 200)])]}])
        rows = [_charge(F.link_no(1)), _pay(F.link_no(1), F.check_no(1), 0), _adj(F.link_no(1), "CO-197", 150),
                _pay(F.link_no(1), "PAPER-1", 90, posted=D(2026, 4, 20)),
                _charge(F.link_no(2), patient=1, payer=P["commercial"], primary=P["commercial"]),
                _adj(F.link_no(2), "CO-50", 150, patient=1, posted=D(2026, 4, 10), payer=P["commercial"], primary=P["commercial"]),
                _charge(F.link_no(99), cpt="99212", patient=3, svc=D(2026, 3, 20), posted=D(2026, 5, 1), status="Payment Pending")]
        ar = [F.ar_row(claim=F.claim_id(4), link=F.link_no(4), status="Batched for Printing", current=60, aging=None, patient=3, svc=SVC),
              F.ar_row(claim=F.claim_id(1), link=F.link_no(1), status="Denied", denial=150, current=0, aging=D(2026, 4, 25), svc=SVC)]
        p = F.write(str(tmp / f"ar{next(_N)}.xlsx"), F.ar_xlsx(ar))
        assert er.ingest_file(store, p, via="test", log=Log())["status"] == "current"
        p = F.write(str(tmp / f"prod{next(_N)}.csv"), F.production_csv(rows))
        assert er.ingest_file(store, p, via="test", ledger=lg, log=Log())["status"] == "current"
        el.refresh(store, lg, parts="AB", log=Log())
        det = Detector(store, run_id="c1", today=TODAY)
        det.run()
        assert det.ema_errors == [], det.ema_errors
    finally:
        lg.close()
        store.close()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """The store file built once for the module; each test works on its own copy."""
    old = {k: os.environ.get(k) for k in ("REMITWATCH_MODE", "REMITWATCH_EMA_STRICT")}
    os.environ["REMITWATCH_MODE"] = "server"
    os.environ["REMITWATCH_EMA_STRICT"] = "1"
    saved = er.CLAIM_HOST_SUFFIX
    er.CLAIM_HOST_SUFFIX = F.HOST_SUFFIX
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_CLOSES"):
        os.environ.pop(k, None)
    try:
        d = tmp_path_factory.mktemp("c1world")
        path = str(d / "store.sqlite")
        build_world(path, d)
        yield path
    finally:
        er.CLAIM_HOST_SUFFIX = saved
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _site(world, tmp_path, monkeypatch, mode: str):
    """The dashboard over a copy of the world: on a server (its store, ledger beside it) or on Vercel (the dashboard's copy
    in a local state folder, as the run saves it)."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch import cloud
    from remitwatch.state import LocalState
    srv = tmp_path / "server"
    srv.mkdir()
    db = str(srv / "store.sqlite")
    shutil.copy(world, db)
    shutil.copy(os.path.join(os.path.dirname(world), "ema_ledger.sqlite"), str(srv / "ema_ledger.sqlite"))
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_DIR", "SRFAX_ACCESS_ID", "SRFAX_ACCESS_PWD",
              "ANTHROPIC_API_KEY", "MODMED_CLIENT_ID", "VERCEL", "VERCEL_ENV", "CRON_SECRET", "REMITWATCH_EMA_LEDGER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setattr(api, "SHOW_PHI", False)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "_day", lambda off: (D.fromisoformat(TODAY) + dt.timedelta(days=off)).isoformat())
    api.ensure_server_schema(db)
    if mode == "serverless":
        state_dir = tmp_path / "state"
        st = LocalState(str(state_dir))
        conn = sqlite3.connect(db)
        try:
            cloud.save_serving(st, conn)
        finally:
            conn.close()
        monkeypatch.setenv("REMITWATCH_MODE", "serverless")
        monkeypatch.setenv("REMITWATCH_STATE_DIR", str(state_dir))
        monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    else:
        monkeypatch.setenv("REMITWATCH_MODE", "server")
    return type("Site", (), {"c": TestClient(api.app), "api": api, "db": db, "mode": mode, "tmp": tmp_path})


@pytest.fixture(params=["server", "serverless"])
def site(request, world, tmp_path, monkeypatch):
    return _site(world, tmp_path, monkeypatch, request.param)


@pytest.fixture()
def server(world, tmp_path, monkeypatch):
    return _site(world, tmp_path, monkeypatch, "server")


def _items(site, **params):
    p = {"status": "all", "limit": 2000}
    p.update(params)
    r = site.c.get("/api/findings", params=p)
    assert r.status_code == 200, r.text
    return r.json()


def _store(site):
    return sqlite3.connect(site.db)


# ---------------------------------------------------------------------------------------------- columns and filters
def test_the_lists_carry_the_new_columns_and_a_copy_without_them_still_answers(site):
    got = _items(site)
    assert got["total"] >= 6 and got["items"]
    for col in NEW_COLS:
        assert col in got["items"][0], col
    by = {i["signal"]: i for i in got["items"]}
    assert {"DENIED_AUTH", "EMA_DENIED", "NOT_SENT", "REMIT_NOT_POSTED", "FORWARDED_TO_CARRIER"} <= set(by)
    assert by["EMA_DENIED"]["source"] == "ema" and by["DENIED_AUTH"]["work_state"] in ("work_now", "done", "waiting")
    assert all(i["expected_value_cents"] is None for i in got["items"])        # the run writes it later (unit G): NULL until then


def test_each_state_counts_what_its_list_holds_and_the_states_add_up(site):
    """Counts equal lists: every state tile of a page is the count line of the list it opens, and the six states hold
    every live item once (a waiting item past its follow-up day is in both Waiting and Follow-ups due)."""
    cats = "denial,bundling,downcoding,underpayment"
    desk = site.c.get("/api/desk", params={"category": cats, "tileset": "state"}).json()
    t = desk["tiles"]
    for name, st, dl in site.api.STATE_TILES:
        p = {"category": cats, "live": 1, "state": st, **({"deadline": dl} if dl else {})}
        lst = _items(site, **p)
        assert (lst["total"], lst["shortfall_cents"] or 0) == (t[name]["n"], t[name]["cents"]), name
    six = sum(_items(site, category=cats, live=1, state=s)["total"] for s in site.api.STATES)
    assert six == _items(site, category=cats, live=1)["total"]
    assert "valid" not in t and "all" in t                                    # tileset=state: the state tiles and the whole count only


def test_the_state_filter_reads_a_person_status_where_the_run_gave_none(server):
    """A store whose items have no state yet (saved before the states, or a run whose states step failed) still lists its
    open work as work now; nothing drops off the lists for want of a state."""
    conn = _store(server)
    conn.execute("UPDATE findings SET work_state=NULL, follow_up_on=NULL")
    conn.commit()
    conn.close()
    open_n = _items(server, live=1, status="open,in_progress")["total"]
    assert open_n and _items(server, live=1, status="open,in_progress", state="todo")["total"] == open_n


def test_follow_ups_due_and_todo_take_the_sites_day(site):
    conn = _store(site)
    fid = conn.execute("SELECT finding_id FROM findings WHERE signal='FORWARDED_TO_CARRIER'").fetchone()[0]
    conn.close()
    item = next(i for i in _items(site)["items"] if i["finding_id"] == fid)
    assert item["work_state"] == "waiting" and item["follow_up_on"] > TODAY
    due = {i["finding_id"] for i in _items(site, state="followup_due")["items"]}
    todo = {i["finding_id"] for i in _items(site, state="todo")["items"]}
    assert fid not in due and fid not in todo
    # the day passes: the follow-up is due, and the item is back in the work list (both lists, one count each)
    site.api._day = lambda off: (D.fromisoformat(item["follow_up_on"]) + dt.timedelta(days=off)).isoformat()
    assert fid in {i["finding_id"] for i in _items(site, state="followup_due")["items"]}
    assert fid in {i["finding_id"] for i in _items(site, state="todo")["items"]}
    assert fid in {i["finding_id"] for i in _items(site, state="work_now,followup_due")["items"]}
    assert _items(site, state="no_such_state")["total"] == 0


def test_each_ema_filter_is_the_rule_on_the_items_own_columns(site):
    conn = _store(site)
    want = {
        "written_off": "COALESCE(written_off_cents,0) > 0", "contractual": "written_off_kind = 'contractual'",
        "paid_other": "ema_outcome = 'paid_after_other'", "ema_only": "source = 'ema'", "worked": "ema_worked_on IS NOT NULL",
        "auto_written_off": "ema_outcome = 'auto_written_off'", "on_ar": "ema_link IN (SELECT link FROM ema_ar WHERE link IS NOT NULL)"}
    for name, cond in want.items():
        n = conn.execute(f"SELECT COUNT(*) FROM findings WHERE {cond}").fetchone()[0]
        assert _items(site, ema=name)["total"] == n, name
    assert _items(site, ema="ema_only")["total"] >= 1 and _items(site, ema="on_ar")["total"] >= 1
    # unworked: the run's own rule (ema_checks.unworked_sql) over the day it measured to, so Today's number is the list's
    through = json.loads(conn.execute("SELECT value FROM meta WHERE key='ema:unworked'").fetchone()[0])["data_through"]
    from remitwatch.ema_checks import unworked_sql
    sql, params = unworked_sql(through)
    assert _items(site, ema="unworked")["total"] == conn.execute(f"SELECT COUNT(*) FROM findings WHERE {sql}", params).fetchone()[0]
    conn.close()
    assert _items(site, ema="nothing_like_this")["total"] == 0
    assert _items(site, ema_outcome="auto_written_off")["total"] == _items(site, ema="auto_written_off")["total"]
    assert _items(site, source="ema")["total"] == _items(site, ema="ema_only")["total"]
    assert _items(site, work_state="waiting")["total"] == _items(site, state="waiting")["total"]


def test_counts_tiles_and_lists_agree_under_the_ema_filter(site):
    cats = "denial,bundling,downcoding,underpayment"
    for ema in ("written_off", "ema_only", "auto_written_off", "worked"):
        d = site.c.get("/api/desk", params={"category": cats, "ema": ema, "state": "todo", "status": "all"}).json()
        lst = _items(site, category=cats, live=1, ema=ema, state="todo")
        assert d["scope"]["n"] == lst["total"], ema
        assert d["tiles"]["todo"]["n"] == lst["total"], ema
        counts = site.c.get("/api/worklist/counts", params={"category": cats, "live": 1, "ema": ema, "state": "todo", "status": "all"}).json()
        assert counts["n"] == lst["total"], ema


def test_the_list_sorts_by_what_working_an_item_is_worth_then_by_its_date(server):
    conn = _store(server)
    ids = [r[0] for r in conn.execute("SELECT finding_id FROM findings ORDER BY finding_id")]
    conn.close()
    plain = [i["finding_id"] for i in _items(server, sort="value")["items"]]
    by_dollars = [i["finding_id"] for i in sorted(_items(server)["items"], key=lambda i: -(i["shortfall_cents"] or -1))]
    assert [x for x in plain if x] and set(plain) == set(by_dollars)
    # the run's expected value (unit G's column) when it writes one: dollars x the pattern's recovery rate
    conn = _store(server)
    conn.execute("ALTER TABLE findings ADD COLUMN expected_value_cents INTEGER")
    conn.execute("ALTER TABLE findings ADD COLUMN recovery_basis TEXT")
    for n, fid in enumerate(ids):
        conn.execute("UPDATE findings SET expected_value_cents=?, recovery_basis=? WHERE finding_id=?", (n * 100, f"basis {n}", fid))
    conn.commit()
    conn.close()
    got = _items(server, sort="value")["items"]
    assert [i["finding_id"] for i in got] == list(reversed(ids))
    assert got[0]["recovery_basis"] == f"basis {len(ids) - 1}"


# ------------------------------------------------------------------------------------------- the panel and the claim
def test_an_items_story_verdict_and_next_step_come_beside_its_panel(site):
    for it in _items(site)["items"]:
        r = site.c.get(f"/api/findings/{it['finding_id']}/explain")
        assert r.status_code == 200, (it["signal"], r.text[:300])
        x = r.json()
        ex = x["explain"]
        assert {"story", "verdict", "next_step", "state"} <= set(ex)
        assert ex["verdict"]["sentence"] and ex["verdict"]["state"] in ("work_now", "waiting", "done", "accepted", "expired", "cant_check")
        nx = ex["next_step"]
        assert set(nx["buttons"]) <= {"build_packet", "attach_note", "find_note", "upload_note", "fax_payer", "copy_instructions", "mark_sent",
                                      "log_outcome", "mark_posted", "mark_billed", "accept_pattern", "let_go", "reopen", "request_era", "open_in_ema"}
        assert "explain_error" not in x
        if it.get("ema_link") is not None:                              # an item on an EMA claim: what EMA's reports hold on it
            assert isinstance(x["ema_block"], dict) and int(it["ema_link"]) in x["ema_block"]["links"], it["signal"]
        blob = json.dumps(x)
        for secret in F.SECRET_STRINGS:                 # names, record numbers and notes never leave the server in the story
            assert secret not in blob, secret
        if site.mode == "serverless" and x["ema_block"] and x["ema_block"]["links"]:
            assert x["ema_block"]["postings"] is None and x["ema_block"]["postings_note"]
    # the panel itself is unchanged and fast: the story only on request
    fid = _items(site)["items"][0]["finding_id"]
    base = site.c.get(f"/api/findings/{fid}").json()
    assert "explain" not in base and "finding" in base
    assert "explain" in site.c.get(f"/api/findings/{fid}", params={"story": 1}).json()
    assert site.c.get("/api/findings/nothing0000000000000000/explain").status_code == 404


def test_on_the_server_the_story_lists_emas_postings_from_the_ledger(server):
    conn = _store(server)
    fid = conn.execute("SELECT finding_id FROM findings WHERE signal='DENIED_AUTH'").fetchone()[0]
    conn.close()
    x = server.c.get(f"/api/findings/{fid}/explain").json()
    assert x["explain"]["story"]["mode"] == "server"
    assert x["ema_block"]["postings"] and x["ema_block"]["postings_note"] is None
    assert any(r["source"] == "production" for r in x["explain"]["story"]["rows"])


def test_a_story_that_cannot_be_told_leaves_the_panel_working_and_says_why(site, monkeypatch, capsys):
    from remitwatch import story
    monkeypatch.setattr(story, "explain", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    fid = _items(site)["items"][0]["finding_id"]
    x = site.c.get(f"/api/findings/{fid}/explain").json()
    assert "explain" not in x and "could not be put together (RuntimeError)" in x["explain_error"]
    assert site.c.get(f"/api/findings/{fid}").status_code == 200
    assert f"story failed for item {fid}" in capsys.readouterr().err


def test_the_claim_view_carries_the_claims_story_and_emas_block(site):
    v = site.c.get("/api/claims/view", headers={"X-Claim": F.claim_id(1)}).json()
    assert v["story"]["rows"] and "balance" in v["story"] and v["ema_block"]["links"]
    assert isinstance(v["ema"], list)                                   # the EMA export rows stay where they were
    assert all(f.get("work_state") for f in v["findings"] if f["via"] == "line")


# --------------------------------------------------------------------------------------------------- appealing in bulk
def _bulk_world(server):
    """Two denials a letter settles, open, to work now, inside their windows: one clean, one the payer paid since."""
    conn = _store(server)
    conn.execute("""UPDATE findings SET status='open', work_state='work_now', appeal_block=NULL, actionable='appeal', category='denial',
                    signal='DENIED_MEDICAL_NECESSITY', appeal_deadline='2027-01-01', stale=0 WHERE signal='DENIED_AUTH'""")
    fid = conn.execute("SELECT finding_id FROM findings WHERE signal='DENIED_MEDICAL_NECESSITY'").fetchone()[0]
    row = conn.execute("SELECT * FROM findings WHERE finding_id=?", (fid,)).fetchone()
    cols = [d[0] for d in conn.execute("SELECT * FROM findings LIMIT 0").description]
    twin = dict(zip(cols, row), finding_id="paidtwin00000000000000aa", appeal_block="paid", work_state="done")
    conn.execute(f"INSERT INTO findings ({','.join(twin)}) VALUES ({','.join('?' * len(twin))})", tuple(twin.values()))
    waiting = dict(twin, finding_id="waitingtwin0000000000aaa", appeal_block="waiting", work_state="waiting")
    conn.execute(f"INSERT INTO findings ({','.join(waiting)}) VALUES ({','.join('?' * len(waiting))})", tuple(waiting.values()))
    # still work now, but the payer reprocessed the line after this answer: never in bulk
    redo = dict(twin, finding_id="reprocessedtwin000000aaa", appeal_block="reprocessed", work_state="work_now")
    conn.execute(f"INSERT INTO findings ({','.join(redo)}) VALUES ({','.join('?' * len(redo))})", tuple(redo.values()))
    conn.commit()
    conn.close()
    return fid


def test_the_bulk_list_is_unit_bs_rule_and_says_what_it_leaves_out_and_why(server, monkeypatch):
    monkeypatch.setenv("REMITWATCH_BULK_APPEAL", "1")
    fid = _bulk_world(server)
    a = server.c.get("/api/appeals/appealable").json()
    assert [i["finding_id"] for i in a["items"]] == [fid]
    lo = a["left_out"]
    assert lo["paid"] == 1 and lo["waiting"] == 1 and lo["reprocessed"] == 1 and lo["line"].startswith("Left out: ")
    assert "paid 1" in lo["line"] and "waiting 1" in lo["line"] and "reprocessed 1" in lo["line"]
    reasons = {x["finding_id"]: x["reason"] for x in lo["items"]}
    assert reasons["paidtwin00000000000000aa"] == "paid" and reasons["waitingtwin0000000000aaa"] == "waiting"
    assert reasons["reprocessedtwin000000aaa"] == "reprocessed"
    # the switch off (the default on the live site): nothing in bulk, with why
    monkeypatch.setenv("REMITWATCH_BULK_APPEAL", "0")
    off = server.c.get("/api/appeals/appealable").json()
    assert off["items"] == [] and off["paused"]


def test_a_bulk_send_never_faxes_an_item_the_rule_or_its_next_step_leaves_out(server, monkeypatch):
    monkeypatch.setenv("REMITWATCH_BULK_APPEAL", "1")
    _bulk_world(server)
    for fid, word in (("paidtwin00000000000000aa", "paid"), ("waitingtwin0000000000aaa", "waits on the payer")):
        r = server.c.post("/api/appeals/send", json={"finding_id": fid, "bulk": True}, headers=XHR).json()
        assert r["status"] == "held" and r["code"] == "left_out" and word in r["reason"], r
    # one item a person opens: the same never, with the reason
    r = server.c.post("/api/appeals/send", json={"finding_id": "paidtwin00000000000000aa"}, headers=XHR).json()
    assert r["status"] == "held" and r["code"] == "blocked" and "paid" in r["reason"]


def test_a_bulk_send_checks_the_items_own_next_step_too(server, monkeypatch):
    monkeypatch.setenv("REMITWATCH_BULK_APPEAL", "1")
    fid = _bulk_world(server)
    from remitwatch import story
    monkeypatch.setattr(story, "next_step", lambda *a, **k: {"appeal_ok": False, "appeal_block": "reprocessed", "action": "x"})
    r = server.c.post("/api/appeals/send", json={"finding_id": fid, "bulk": True}, headers=XHR).json()
    assert r["status"] == "held" and r["code"] == "left_out" and "reprocessed" in r["reason"]


def test_a_denial_only_ema_shows_is_held_until_its_eob_is_in_hand(site):
    fid = next(i["finding_id"] for i in _items(site)["items"] if i["signal"] == "EMA_DENIED")
    r = site.c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    from remitwatch.ema_checks import NO_REMITTANCE_MESSAGE
    assert r["status"] == "held" and r["code"] == "no_remittance" and r["reason"] == NO_REMITTANCE_MESSAGE


def test_remittances_behind_hold_a_fax_unless_emas_report_vouches_for_the_item(server, monkeypatch):
    """W5: with the 835s stale the send asks ema_checks.fax_vouch; a vouched item goes on, one EMA cannot vouch for is
    held as stale with EMA's own reason."""
    fid = _bulk_world(server)
    monkeypatch.setattr(server.api, "STALE_APPEAL_DAYS", 1)
    from remitwatch import ema_checks as ec
    monkeypatch.setattr(ec, "fax_vouch", lambda conn, fs, oc, today=None: {f["finding_id"]: {"vouched": False, "code": "paid_after",
                                                                                               "message": "EMA shows a payment."} for f in fs})
    r = server.c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    assert r["code"] == "stale" and r["vouch"] == "paid_after" and "EMA shows a payment." in r["reason"]
    monkeypatch.setattr(ec, "fax_vouch", lambda conn, fs, oc, today=None: {f["finding_id"]: {"vouched": True, "code": None,
                                                                                               "message": "EMA vouches."} for f in fs})
    r = server.c.post("/api/appeals/send", json={"finding_id": fid}, headers=XHR).json()
    assert r["code"] != "stale"                                         # vouched: it goes on to the next check


# ---------------------------------------------------------------------------------------- held reports, events, policy
def _held(site):
    conn = _store(site)
    fid = conn.execute("SELECT file_id FROM ema_report_files LIMIT 1").fetchone()[0]
    conn.execute("UPDATE ema_report_files SET status='held' WHERE file_id=?", (fid,))
    conn.commit()
    conn.close()
    return fid


def test_a_held_report_is_applied_or_discarded_by_a_person_on_a_server(server, monkeypatch):
    from remitwatch import intake
    kicked = []
    monkeypatch.setattr(intake, "kick", lambda d=None: kicked.append(d) or True)
    fid = _held(server)
    assert server.c.post(f"/api/ema/reports/{fid}/decision", json={"decision": "maybe"}, headers=XHR).status_code == 400
    assert server.c.post("/api/ema/reports/nosuchreport00/decision", json={"decision": "apply"}, headers=XHR).status_code == 404
    assert server.c.post(f"/api/ema/reports/{fid}/decision", json={"decision": "apply"}).status_code == 400     # no XHR header
    r = server.c.post(f"/api/ema/reports/{fid}/decision", json={"decision": "apply"}, headers=XHR)
    assert r.status_code == 200 and r.json()["run_started"] is True and kicked
    conn = _store(server)
    assert conn.execute("SELECT decision FROM ema_report_files WHERE file_id=?", (fid,)).fetchone()[0] == "apply"
    conn.close()
    r = server.c.post(f"/api/ema/reports/{fid}/decision", json={"decision": "discard"}, headers=XHR)
    assert r.status_code == 200
    conn = _store(server)
    assert conn.execute("SELECT status FROM ema_report_files WHERE file_id=?", (fid,)).fetchone()[0] == "discarded"
    conn.close()
    assert server.c.post(f"/api/ema/reports/{fid}/decision", json={"decision": "apply"}, headers=XHR).status_code == 409


def test_on_vercel_a_decision_goes_through_the_journal_and_clears_the_ledgers_tries(world, tmp_path, monkeypatch):
    from remitwatch import actions, cloud
    srv = tmp_path / "w"
    srv.mkdir()
    db0 = str(srv / "store.sqlite")
    shutil.copy(world, db0)
    conn = sqlite3.connect(db0)
    fid = conn.execute("SELECT file_id FROM ema_report_files LIMIT 1").fetchone()[0]
    conn.execute("UPDATE ema_report_files SET status='held' WHERE file_id=?", (fid,))
    conn.commit()
    conn.close()
    shutil.copy(db0, world + ".held")
    try:
        site = _site(world + ".held", tmp_path, monkeypatch, "serverless")
        cleared, runs = [], []
        monkeypatch.setattr(el.LedgerHandle, "clear_attempt", lambda self, f: cleared.append(f))
        monkeypatch.setattr(cloud, "trigger_cycle", lambda base, trigger="x", **k: runs.append(trigger) or True)
        r = site.c.post(f"/api/ema/reports/{fid}/decision", json={"decision": "apply"}, headers=XHR)
        assert r.status_code == 200 and cleared == [fid] and runs == ["decision"]
        entries = cloud.read_actions(cloud.SERVING_COPY.state())
        assert [a for _n, a in entries if a["kind"] == "ema_report_decision"][0]["file_id"] == fid
        # the journal entry replayed on the store makes the same decision
        conn = sqlite3.connect(db0)
        conn.row_factory = sqlite3.Row
        assert actions.apply(conn, {"kind": "ema_report_decision", "file_id": fid, "decision": "apply", "actor": "x"})
        assert conn.execute("SELECT decision FROM ema_report_files WHERE file_id=?", (fid,)).fetchone()[0] == "apply"
        conn.close()
    finally:
        os.remove(world + ".held")


def test_work_done_outside_remitwatch_is_a_line_in_the_items_history(site):
    fid = _items(site)["items"][0]["finding_id"]
    before = len(site.c.get(f"/api/findings/{fid}").json()["events"])
    assert site.c.post(f"/api/findings/{fid}/event", json={"event": "posted", "note": "check posted"}, headers=XHR).status_code == 200
    ev = site.c.get(f"/api/findings/{fid}").json()["events"]
    assert len(ev) == before + 1 and ev[0]["kind"] == "posted" and ev[0]["note"] == "Posted in EMA: check posted"
    assert site.c.post(f"/api/findings/{fid}/event", json={"event": "deleted"}, headers=XHR).status_code == 400
    assert site.c.post("/api/findings/nothing0000000000000000/event", json={"event": "posted"}, headers=XHR).status_code == 404


def test_the_owners_policy_recorded_on_vercel_is_applied_from_the_journal_newest_first():
    from remitwatch import actions
    from remitwatch.ema_checks import POLICY_KEY
    conn = sqlite3.connect(":memory:")
    pat = "Blue Cross Test Plan|99213||CO-197"
    assert actions.apply(conn, {"kind": "ema_policy", "pattern": pat, "decision": "accept", "actor": "owner", "at": "2026-05-02T00:00:00+00:00"})
    # an older entry read after a newer one, and a replay, change nothing
    assert actions.apply(conn, {"kind": "ema_policy", "pattern": pat, "decision": "pursue", "actor": "x", "at": "2026-05-01T00:00:00+00:00"})
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key=?", (POLICY_KEY,)).fetchone()[0])[pat]["decision"] == "accept"
    assert actions.apply(conn, {"kind": "ema_policy", "pattern": pat, "decision": "accept", "actor": "owner", "at": "2026-05-02T00:00:00+00:00"})
    pol = json.loads(conn.execute("SELECT value FROM meta WHERE key=?", (POLICY_KEY,)).fetchone()[0])
    assert pol == {pat: {"decision": "accept", "by": "owner", "at": "2026-05-02T00:00:00+00:00"}}
    assert actions.apply(conn, {"kind": "ema_policy", "pattern": pat, "decision": "pursue", "actor": "x", "at": "2026-05-03T00:00:00+00:00"})
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key=?", (POLICY_KEY,)).fetchone()[0])[pat]["decision"] == "pursue"
    assert actions.apply(conn, {"kind": "ema_policy", "pattern": pat, "decision": "drop"})            # unknown decision: kept, ignored
    assert actions.apply(conn, {"kind": "ema_policy", "pattern": "", "decision": "accept"})


def test_meta_says_reports_upload_on_both_hosts_and_whether_decisions_are_kept(site):
    m = site.c.get("/api/meta").json()
    assert m["report_uploads"] is True and m["decisions"] is site.api._route_here("/api/findings/probe/decisions")
    assert m["uploads"] is (site.mode == "server")


# --------------------------------------------------------------------------------- hide rules at the finer grain
def _hide_store(tmp_path):
    """A store with an old hide_rules table (keyed by payer and signal), one rule in force, and three open denials of one
    payer: two codes, one reason."""
    conn = sqlite3.connect(str(tmp_path / "h.sqlite"))
    conn.executescript("""
        CREATE TABLE findings (finding_id TEXT PRIMARY KEY, payer_group TEXT, signal TEXT, procedure_code TEXT, original_procedure_code TEXT,
                               billed_code TEXT, code_category TEXT, modifiers TEXT, carc TEXT, status TEXT, stale INTEGER, status_changed_at TEXT,
                               shortfall_cents INTEGER);
        CREATE TABLE finding_events (id INTEGER PRIMARY KEY, finding_id TEXT, at TEXT, kind TEXT, from_status TEXT, to_status TEXT, amount_cents INTEGER,
                                     actor TEXT, note TEXT);
        CREATE TABLE hide_rules (payer_group TEXT, signal TEXT, actor TEXT, at TEXT, note TEXT, removed_at TEXT, PRIMARY KEY (payer_group, signal));
        INSERT INTO hide_rules VALUES ('Plan A', 'DENIED_AUTH', 'biller', '2026-01-01T00:00:00+00:00', 'never pays', NULL);
        INSERT INTO findings VALUES ('a1', 'Plan A', 'DENIED_NONCOVERED', '77436', NULL, NULL, 'radiation', '', '96', 'open', 0, NULL, 100);
        INSERT INTO findings VALUES ('a2', 'Plan A', 'DENIED_NONCOVERED', '99213', NULL, NULL, 'em', '25', '96', 'open', 0, NULL, 100);
        INSERT INTO findings VALUES ('a3', 'Plan A', 'DENIED_AUTH', '77436', NULL, NULL, 'radiation', '', '197', 'open', 0, NULL, 100);
        INSERT INTO findings VALUES ('a4', 'Plan A', 'NO_ANSWER', '77436', NULL, NULL, 'radiation', '', NULL, 'open', 0, NULL, 100);
    """)
    conn.commit()
    return conn


def test_an_old_hide_rule_keeps_hiding_exactly_what_it_did_after_the_table_gains_the_finer_grain(tmp_path):
    from remitwatch import actions
    conn = _hide_store(tmp_path)
    assert actions.apply_hide_rules(conn) == 1                         # the table is brought to five parts, the rule kept
    conn.commit()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(hide_rules)")]
    assert {"code", "modifier", "reason"} <= set(cols)
    assert conn.execute("SELECT payer_group, signal, code, modifier, reason, note FROM hide_rules").fetchall() == [
        ("Plan A", "DENIED_AUTH", "*", "*", "*", "never pays")]
    assert dict(conn.execute("SELECT finding_id, status FROM findings")) == {"a1": "open", "a2": "open", "a3": "dismissed", "a4": "open"}
    actions.ensure_hide_rules(conn)                                    # a second time changes nothing
    assert conn.execute("SELECT COUNT(*) FROM hide_rules").fetchone()[0] == 1


def test_a_rule_by_code_hides_that_code_only_and_removing_it_reopens_only_what_no_other_rule_covers(tmp_path):
    from remitwatch import actions
    conn = _hide_store(tmp_path)
    actions.apply(conn, {"kind": "hide_rule", "op": "add", "payer_group": "Plan A", "signal": "*", "code": "77436", "actor": "b",
                         "note": "radiation codes", "at": "2026-02-01T00:00:00+00:00"})
    st = dict(conn.execute("SELECT finding_id, status FROM findings"))
    assert st == {"a1": "dismissed", "a2": "open", "a3": "dismissed", "a4": "open"}     # never a claim nobody answered
    # the old payer x signal rule still covers a3: removing the code rule reopens a1 only
    actions.apply(conn, {"kind": "hide_rule", "op": "remove", "payer_group": "Plan A", "signal": "*", "code": "77436", "actor": "b",
                         "at": "2026-02-02T00:00:00+00:00"})
    assert dict(conn.execute("SELECT finding_id, status FROM findings")) == {"a1": "open", "a2": "open", "a3": "dismissed", "a4": "open"}
    # the journal applied again, in any order, changes nothing
    actions.apply(conn, {"kind": "hide_rule", "op": "add", "payer_group": "Plan A", "signal": "*", "code": "77436", "actor": "b",
                         "note": "radiation codes", "at": "2026-02-01T00:00:00+00:00"})
    assert dict(conn.execute("SELECT finding_id, status FROM findings"))["a1"] == "open"
    # a rule by modifier and by the payer's reason code
    actions.apply(conn, {"kind": "hide_rule", "op": "add", "payer_group": "Plan A", "signal": "DENIED_NONCOVERED", "modifier": "25", "reason": "CO-96",
                         "actor": "b", "at": "2026-02-03T00:00:00+00:00"})
    assert dict(conn.execute("SELECT finding_id, status FROM findings")) == {"a1": "open", "a2": "dismissed", "a3": "dismissed", "a4": "open"}


def test_the_hide_page_takes_and_lists_the_finer_grain_and_refuses_a_whole_payer(site):
    c = site.c
    item = next(i for i in _items(site)["items"] if i["signal"] == "DENIED_AUTH")
    pg = item["payer_group"]
    assert c.post("/api/hide", json={"payer_group": pg, "note": "x"}, headers=XHR).status_code == 400          # a whole payer
    assert c.post("/api/hide", json={"payer_group": pg, "signal": "NO_ANSWER"}, headers=XHR).status_code == 400
    assert c.post("/api/hide", json={"payer_group": pg, "signal": "DENIED_AUTH", "code": "<script>"}, headers=XHR).status_code == 400
    assert c.post("/api/hide", json={"payer_group": pg, "signal": "DENIED_AUTH", "code": "00000"}, headers=XHR).status_code == 404
    r = c.post("/api/hide", json={"payer_group": pg, "signal": "DENIED_AUTH", "code": item["procedure_code"], "reason": "CO-197", "note": "never pays"},
               headers=XHR)
    assert r.status_code == 200, r.text
    rules = c.get("/api/hide").json()["rules"]
    mine = [x for x in rules if x["code"] == item["procedure_code"]]
    assert mine and mine[0]["reason"] == "CO-197" and item["procedure_code"] in mine[0]["label"]
    assert c.post("/api/hide", json={"payer_group": pg, "signal": "DENIED_AUTH", "code": item["procedure_code"], "reason": "CO-197", "remove": True},
                  headers=XHR).status_code == 200
    assert "patterns" in c.get("/api/hide", params={"patterns": 1}).json()


# ------------------------------------------------------------------------------------------------ the page in node
PAGE_JS = r"""
const fs = require('fs'), vm = require('vm'), path = require('path');
const read = f => fs.readFileSync(path.join(process.argv[2], f), 'utf8');
const html = read('index.html');
const grab = (src, re) => { const m = src.match(re); if (!m) throw new Error('not found: ' + re); return m[0]; };
const helpers = [/const money = [^\n]*\n/, /const money2 = [^\n]*\n/, /const esc = [^\n]*\n/].map(re => grab(html, re)).join('\n');
const ctx = { console, state: { brief: { today: '2026-05-03', labels: {}, work_types: [] }, worklist: ['a', 'b', 'c'], wlTotal: 3, filt: {} }, $: () => null };
vm.createContext(ctx);
for (const src of [helpers, read('features.js'), read('today.js'), read('desk.js')]) vm.runInContext(src, ctx);
const x = (route, buttons, extra) => ({ explain: { verdict: { state: 'work_now', sentence: 'Denied; nobody worked it.', rows: [2], strength: 'proven' },
  next_step: Object.assign({ route, buttons, action: 'Do it.', who: 'Biller', deadline: '2026-06-01', basis: 'the payer limit', expected_cents: 12345,
    ranked_cents: 6000, recovery: { rate: 0.5, basis: '5 of the 10 earlier items like this were later paid' }, steps: ['one', 'two'] }, extra || {}),
  state: { work_state: 'work_now', legal_deadline: '2026-07-01' }, story: { rows: [] } } });
const f = { finding_id: 'b', payer_group: 'Plan A', signal: 'DENIED_AUTH', ema_outcome: 'auto_written_off', work_state: 'work_now', legal_deadline: '2026-07-01' };
const primary = (route, buttons, packets) => { const h = ctx.xpBoxHtml(f, x(route, buttons), { packets: packets || [] }); return (h.match(/id="xpMain"[^>]*>([^<]*)</) || [])[1] || null; };
const out = {
  appeal_no_packet: primary('appeal', ['build_packet', 'find_note', 'upload_note', 'fax_payer', 'copy_instructions', 'mark_sent', 'let_go']),
  appeal_with_packet: primary('appeal', ['build_packet', 'find_note', 'upload_note', 'fax_payer', 'copy_instructions', 'mark_sent'], [{ packet_id: 'p' }]),
  corrected: primary('corrected', ['copy_instructions', 'mark_sent', 'let_go']),
  call: primary('call', ['copy_instructions', 'log_outcome', 'let_go']),
  post: primary('post', ['mark_posted', 'copy_instructions']),
  done: primary('nothing', ['reopen']),
  box: ctx.xpBoxHtml(f, x('appeal', ['build_packet', 'let_go']), { packets: [] }),
  story: ctx.xpStoryHtml({ rows: [{ n: 1, date: '2026-03-28', source: '835', text: 'Denied <b>', amount_cents: 0, codes: [{ code: 'CO-197', meaning: 'no auth' }] },
                                  { n: 2, date: '2026-03-31', source: 'production', text: 'Written off', amount_cents: 15000, decisive: true }],
                          balance: { text: 'Charge $150.00 = written off $150.00. It reconciles to the cent.' }, reconciles: true, gap_cents: 0, notes: ['Kept on the server.'] }, [2]),
  left_claims: vm.runInContext("leftOutLine(2, ['NO_ANSWER', 'REJECTED_PRE_PAYER'])", ctx),
  left_mixed: vm.runInContext("leftOutLine(2, ['NO_ANSWER', 'CREDIT_BALANCE'])", ctx),
  never: vm.runInContext('[...NEVER_HIDDEN].sort()', ctx),
  defaults: ctx.deskFilter('worklist'),
  older_link: (ctx.state.filt.worklist = { status: 'appealed', deadline: 'followup' }, ctx.deskFilter('worklist')),
  today_link: (ctx.state.filt.worklist = { state: 'todo', status: 'all', deadline: '' }, ctx.deskFilter('worklist')),
  row_waiting: ctx.rowHtml(ctx.state.brief, { finding_id: 'w', signal: 'X', status: 'open', work_state: 'waiting', follow_up_on: '2026-06-10', state_basis: 'Faxed on May 1' }, {}),
  row_done: ctx.rowHtml(ctx.state.brief, { finding_id: 'd', signal: 'X', status: 'open', work_state: 'done', state_basis: 'Paid' }, {}),
  row_auto: ctx.rowHtml(ctx.state.brief, { finding_id: 'a', signal: 'X', status: 'open', work_state: 'work_now', ema_outcome: 'auto_written_off', appeal_deadline: '2026-06-01', legal_deadline: '2026-07-01' }, {}),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    if not NODE:
        pytest.skip("node is not installed")
    script = tmp_path_factory.mktemp("page") / "c1_page.js"
    script.write_text(PAGE_JS, encoding="utf-8")
    res = subprocess.run([NODE, str(script), STATIC], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_the_next_steps_one_main_button_follows_its_route(page):
    assert page["appeal_no_packet"] == "Build the appeal packet"
    assert page["appeal_with_packet"] == "Fax it to the payer"               # a fax only once a packet can be read first
    assert page["corrected"] == "Mark sent" and page["call"] == "Log what happened" and page["post"] == "Mark posted in EMA"
    assert page["done"] == "Reopen"
    box = page["box"]
    assert "Denied; nobody worked it." in box and "Proven: two sources agree" in box and "Payer's limit" in box
    assert "At stake" in box and "about" in box and "Auto write-off" in box and "Work now" in box
    assert 'data-xp="let_go"' in box and "Item 2 of 3" in box and 'id="nextItem"' in box and 'id="prevItem"' in box


def test_the_story_marks_its_decisive_rows_escapes_text_and_shows_the_balance(page):
    s = page["story"]
    assert "Denied &lt;b&gt;" in s and 'data-n="2" class="has-item"' in s and 'data-n="1" class=""' in s
    assert "balances to the cent" in s and "Kept on the server." in s and "EMA posting" in s and "CO-197: no auth" in s


def test_the_worklist_opens_on_work_now_and_older_links_keep_their_meaning(page):
    assert page["defaults"]["state"] == "todo" and page["defaults"]["status"] == "all" and page["defaults"]["sort"] == "value"
    assert page["older_link"]["state"] == "" and page["older_link"]["sort"] == "deadline" and page["older_link"]["status"] == "appealed"
    assert page["today_link"]["state"] == "todo" and page["today_link"]["deadline"] == ""


def test_the_rows_say_when_a_waiting_item_comes_back_and_what_emas_rule_did(page):
    assert "follow up Jun 10, 2026" in page["row_waiting"] and "Faxed on May 1" in page["row_waiting"]
    assert ">Done<" in page["row_done"]
    assert "Auto write-off" in page["row_auto"] and "the payer&#39;s own limit is 2026-07-01" in page["row_auto"]


def test_items_let_go_one_at_a_time_mirror_the_server_and_say_so(page):
    from remitwatch.actions import NEVER_HIDDEN
    assert set(page["never"]) == set(NEVER_HIDDEN)
    assert page["left_claims"] == ("Let go leaves out 2 claims with no answer from the payer or rejected before the payer. "
                                   "Open each one to let it go, with a note.")
    assert page["left_mixed"].startswith("Let go leaves out 2 items that are let go one at a time")


# ------------------------------------------------------------------ an upload finished while a Vercel run was going
def test_an_upload_that_finished_during_a_run_is_read_by_a_run_started_right_after(tmp_path, monkeypatch):
    """On Vercel, an upload finished while a run held the lock asked for a run that was skipped: the run going on ends by
    starting the next one when intake/ still holds a complete pair (A2 end-to-end review), not leaving it for tomorrow."""
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    from remitwatch import cloud, uploads
    from remitwatch.state import LocalState
    st_dir = tmp_path / "state"
    st = LocalState(str(st_dir))
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(st_dir))
    monkeypatch.setenv("CRON_SECRET", "c1-test-secret-value")
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "VERCEL_ENV"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cloud, "run_cloud_cycle", lambda *a, **k: {"more": False})
    started = []
    monkeypatch.setattr(cloud, "trigger_cycle", lambda base, trigger="x", **k: started.append((trigger, k.get("hop"))) or True)
    c = TestClient(api.app)
    auth = {"Authorization": "Bearer c1-test-secret-value"}
    assert c.post("/api/cron/cycle", headers=auth).json().get("continued") is None and started == []      # nothing waiting
    pair = "0123456789abcdef0123456789abcdef__AR.xlsx"
    st.put(f"{uploads.STATE_INTAKE}/{pair}", b"PK fake workbook")
    sidecar = uploads.make_sidecar(name="AR.xlsx", user="biller", sha256="ab" * 32, size=16, gz=False, report_hint="ar_report")
    st.put(f"{uploads.STATE_INTAKE}/{pair}.json", uploads.sidecar_bytes(sidecar))
    out = c.post("/api/cron/cycle", headers=auth).json()
    assert out["continued"] is True and out["continued_for"] == "uploads" and started == [("continue", 1)]
    # the chain still ends: past the most runs in a row, nothing more is started
    started.clear()
    out = c.post(f"/api/cron/cycle?hop={cloud.MAX_HOPS}", headers=auth).json()
    assert started == [] and "continued" not in out
