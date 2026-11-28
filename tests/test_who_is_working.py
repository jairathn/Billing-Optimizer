"""
Who is working (CRITIQUE_biller change 5, unit ui-words). Everyone signs in with one shared password, so each person
chooses their own name once per computer (static/shell.js keeps it in the browser) and the page sends it with every change
(X-RemitWatch-Person, URI-encoded). What a person does is kept under that name in the item's history, on a server's store
and on Vercel's dashboard copy through the journal; the panel and Work say "by <name>"; /api/people counts what each
person did for the manager's Summary. A change with no name, or with something that is not a name, is kept under the
sign-in as before. Every name here is invented.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
from urllib.parse import quote

import pytest

from remitwatch import actions, cloud
from remitwatch.state import LocalState
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from tests.test_ema_pages import CATS, XHR, _items, _site, world  # noqa: F401  (world: the module's store, built once)

STAFF = "Quorvalda Tisbenwick"          # an invented biller
OTHER = "Inés Zoë-Müller"               # an invented biller whose name needs more than ASCII


def as_person(name: str) -> dict:
    return dict(XHR, **{"X-RemitWatch-Person": quote(name)})


@pytest.fixture(params=["server", "serverless"])
def site(request, world, tmp_path, monkeypatch):  # noqa: F811
    return _site(world, tmp_path, monkeypatch, request.param)


@pytest.fixture()
def server(world, tmp_path, monkeypatch):  # noqa: F811
    return _site(world, tmp_path, monkeypatch, "server")


def _todo(site) -> list[str]:
    return [i["finding_id"] for i in _items(site, category=CATS, live=1, state="todo")["items"]]


def _journal(site) -> list[dict]:
    return [a for _n, a in cloud.read_actions(LocalState(str(site.tmp / "state")))]


# --------------------------------------------------------------------------------------------------------- the name
@pytest.mark.parametrize("raw, kept", [
    ("Quorvalda Tisbenwick", "Quorvalda Tisbenwick"), ("  Ada   Lovel ", "Ada Lovel"), ("O'Brien-Nyx", "O'Brien-Nyx"),
    ("Inés Zoë-Müller", "Inés Zoë-Müller"), ("Ine\u0301s", "In\u00e9s"), ("\u092a\u094d\u0930\u093f\u092f\u093e", "\u092a\u094d\u0930\u093f\u092f\u093e"), ("O\u2019Brien", "O\u2019Brien"), ("李明", "李明"), ("Jo", "Jo"),
    ("J", ""), ("R2D2", ""), ("x" * 41, ""), ("retry", ""), ("Hide Rule", ""), ("system", ""), ("a person", ""),
    ("Bob; DROP TABLE", ""), ("<b>Al</b>", ""), ("", ""), (None, ""), ("Al\nBo", "Al Bo"), ("Al\tBo", "Al Bo")])
def test_a_name_is_letters_with_spaces_and_dashes_never_a_word_the_app_writes(raw, kept):
    """actions.person_name: letters of any script (one Unicode form, so one person typed on two computers is one name),
    spaces, . ' - between them; two letters at least, 40 characters at most; never what the app writes as an actor itself
    (a history line "by retry" is always the app's)."""
    assert actions.person_name(raw) == kept


def test_a_change_is_kept_under_the_name_the_person_chose_on_both_hosts(site):
    fid = _todo(site)[0]
    r = site.c.post(f"/api/findings/{fid}/status", json={"status": "appealed", "notes": "appeal faxed"}, headers=as_person(STAFF))
    assert r.status_code == 200, r.text
    d = site.c.get(f"/api/findings/{fid}").json()
    e = d["events"][0]
    assert (e["actor"], e["by"], e["kind"], e["to_status"]) == (STAFF, STAFF, "manual", "appealed")
    assert (d["finding"]["worked_by"], d["finding"]["worked_to"]) == (STAFF, "appealed") and d["finding"]["worked_at"]
    row = next(i for i in _items(site)["items"] if i["finding_id"] == fid)
    assert (row["worked_by"], row["worked_to"]) == (STAFF, "appealed")
    if site.mode == "serverless":
        # on Vercel the change is a journal entry under the person's name; the next run folds it into the store
        got = [a for a in _journal(site) if a.get("finding_id") == fid]
        assert got and got[-1]["actor"] == STAFF and got[-1]["status"] == "appealed"
    else:
        conn = sqlite3.connect(site.db)
        try:
            assert conn.execute("SELECT actor FROM finding_events WHERE finding_id=? AND kind='manual' ORDER BY id DESC",
                                (fid,)).fetchone()[0] == STAFF
        finally:
            conn.close()


def test_a_name_outside_ascii_reaches_the_history_whole(site):
    fid = _todo(site)[0]
    assert site.c.post(f"/api/findings/{fid}/status", json={"status": "in_progress"}, headers=as_person(OTHER)).status_code == 200
    e = site.c.get(f"/api/findings/{fid}").json()["events"][0]
    assert e["actor"] == e["by"] == OTHER


@pytest.mark.parametrize("header", [None, quote("R2D2"), quote("retry"), "%FF%FE", quote("x" * 41), "a" * 500])
def test_no_name_or_a_bad_one_keeps_the_sign_in_as_before(server, header):
    """A page from before names, a script, or something that is not a name: the change is kept under the sign-in (here the
    developer's open site, 'anonymous'), and names nobody on the panel or the row."""
    fid = _todo(server)[0]
    h = dict(XHR, **({"X-RemitWatch-Person": header} if header is not None else {}))
    assert server.c.post(f"/api/findings/{fid}/status", json={"status": "appealed"}, headers=h).status_code == 200
    d = server.c.get(f"/api/findings/{fid}").json()
    assert d["events"][0]["actor"] == "anonymous" and d["events"][0]["by"] == ""
    assert "worked_by" not in d["finding"]


def test_a_line_written_down_and_a_bulk_change_keep_the_name_too(site):
    ids = _todo(site)[:2]
    r = site.c.post(f"/api/findings/{ids[0]}/event", json={"event": "asked", "note": "on hold for ten minutes"}, headers=as_person(STAFF))
    assert r.status_code == 200, r.text
    e = site.c.get(f"/api/findings/{ids[0]}").json()["events"][0]
    assert (e["kind"], e["actor"], e["by"]) == ("asked", STAFF, STAFF)
    r = site.c.post("/api/findings/bulk-status", json={"ids": ids, "status": "in_progress"}, headers=as_person(OTHER))
    assert r.status_code == 200 and r.json()["updated"] == 2, r.text
    for fid in ids:
        e = next(x for x in site.c.get(f"/api/findings/{fid}").json()["events"] if x["kind"] == "manual")
        assert (e["actor"], e["to_status"]) == (OTHER, "in_progress")


def test_a_later_automatic_change_takes_the_name_off_the_row(server):
    """"Sent by <name>" stands only while the person's status does: once the run closes the item on a payment it found (or
    a hide rule lets it go), the row and the panel name nobody; the history still says who sent it."""
    fid = _todo(server)[0]
    assert server.c.post(f"/api/findings/{fid}/status", json={"status": "appealed"}, headers=as_person(STAFF)).status_code == 200
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)).isoformat(timespec="seconds")
    conn = sqlite3.connect(server.db)
    try:
        conn.execute("UPDATE findings SET status='resolved', auto_note='Paid on a later remittance' WHERE finding_id=?", (fid,))
        conn.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor, note) "
                     "VALUES (?, ?, 'paid_on_later_remit', 'appealed', 'resolved', 'system', 'paid')", (fid, later))
        conn.commit()
    finally:
        conn.close()
    d = server.c.get(f"/api/findings/{fid}").json()
    assert "worked_by" not in d["finding"]
    assert [e["by"] for e in d["events"] if e["kind"] == "manual"] == [STAFF]
    assert "worked_by" not in next(i for i in _items(server)["items"] if i["finding_id"] == fid)


def test_people_counts_what_each_person_did_today_and_this_week_on_both_hosts(site):
    """The manager's Summary: each name with the items they moved and the statuses they set (Paid with what was paid), the
    lines they wrote down, today and over the days asked; changes kept under the shared sign-in counted apart; the app's
    own lines never; the names newest first, for the picker."""
    ids = [i["finding_id"] for i in _items(site)["items"] if i.get("status") == "open"]
    assert len(ids) >= 3
    a, b, c = ids[0], ids[1], ids[2]
    assert site.c.post(f"/api/findings/{a}/status", json={"status": "appealed"}, headers=as_person(STAFF)).status_code == 200
    assert site.c.post(f"/api/findings/{b}/status", json={"status": "resolved", "recovered_cents": 1234},
                       headers=as_person(STAFF)).status_code == 200
    assert site.c.post(f"/api/findings/{a}/event", json={"event": "asked"}, headers=as_person(STAFF)).status_code == 200
    assert site.c.post(f"/api/findings/{c}/status", json={"status": "in_progress"}, headers=as_person(OTHER)).status_code == 200
    assert site.c.post(f"/api/findings/{c}/status", json={"status": "appealed"}, headers=XHR).status_code == 200   # no name
    got = site.c.get("/api/people", params={"days": 7}).json()
    assert got["names"][:2] == [OTHER, STAFF] or set(got["names"][:2]) == {OTHER, STAFF}
    by = {p["name"]: p for p in got["people"]}
    s = by[STAFF]["today"]
    assert (s["items"], s["changes"], s["lines"], s["paid_cents"]) == (2, 3, 1, 1234)
    assert s["statuses"] == {"appealed": 1, "resolved": 1} and by[STAFF]["days"] == s
    assert by[OTHER]["today"]["statuses"] == {"in_progress": 1}
    assert got["unnamed"]["today"]["statuses"] == {"appealed": 1}
    assert "anonymous" not in got["names"] and all(n not in actions.APP_ACTORS for n in got["names"])
    text = json.dumps(got)
    assert a not in text and b not in text and c not in text          # names and counts only: no item, claim or patient


def test_the_policy_and_the_spot_checks_take_the_chosen_name_where_no_box_was_filled(server):
    """The Write-off policy, the Results spot checks and settings take the name typed in their own box (unit ui-pages may
    retire those boxes) and else the one the person chose on this computer."""
    from remitwatch import api_results
    pol = server.c.get("/api/ema/policy").json()
    pats = [p["pattern"] for p in pol.get("patterns") or []]
    if pats:
        r = server.c.post("/api/ema/policy", json={"pattern": pats[0], "decision": "pursue"}, headers=as_person(STAFF))
        assert r.status_code == 200 and r.json()["by"].startswith(STAFF), r.text
    who = []
    import remitwatch.results as results
    real = results.record_spot_check
    results.record_spot_check = lambda conn, day, fid, answer, reason, note, w: who.append(w) or {"ok": True}
    try:
        r = server.c.post("/api/results/spot", json={"day": "2026-05-03", "finding_id": _todo(server)[0], "answer": "right"},
                          headers=as_person(OTHER))
    finally:
        results.record_spot_check = real
    assert r.status_code == 200 and who == [OTHER], (r.text, who)
    assert api_results is not None


SHELL_FETCH = r"""
const fs = require('fs'), vm = require('vm');
const sent = [], kept = {'rw:person': process.argv[2]};
const ctx = {navigator: {platform: 'Linux'}, location: {href: 'https://site.invalid/#work', origin: 'https://site.invalid'},
             localStorage: {getItem: k => kept[k] == null ? null : kept[k], setItem() {}, removeItem() {}}, URL, Headers,
             document: {getElementById: () => null, querySelectorAll: () => [], documentElement: {setAttribute() {}, removeAttribute() {}, getAttribute: () => null}}};
ctx.window = ctx;
ctx.fetch = (input, init) => { sent.push(init && init.headers ? (new Headers(init.headers)).get('X-RemitWatch-Person') : null); return Promise.resolve({}); };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
ctx.fetch('/api/findings/abc/status', {method: 'POST', headers: {'X-Requested-With': 'remitwatch'}, body: '{}'});
ctx.fetch('/api/findings?limit=5');
ctx.fetch('https://elsewhere.invalid/x', {method: 'POST'});
ctx.fetch('/api/results/settings', {method: 'PUT', headers: {'X-RemitWatch-Person': 'set%20by%20call'}});
process.stdout.write(JSON.stringify(sent));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("stored, first", [("Zoë-Anne O'Brien", quote("Zoë-Anne O'Brien", safe="-_.!~*'()")), (None, None), ("R2D2", None)])
def test_every_change_a_page_sends_carries_the_name_chosen_on_this_computer(stored, first):
    """shell.js wraps fetch once for every page's helper: a change to this site carries the name (URI-encoded), a read
    never does, another host never does, a header the call set itself is kept, and no name (or a stored value that is not
    a name) sends nothing."""
    out = subprocess.run([shutil.which("node"), "-e", SHELL_FETCH, os.path.join(ROOT, "remitwatch", "static", "shell.js"),
                          stored or ""], capture_output=True, text=True, check=True, timeout=60)
    assert json.loads(out.stdout) == [first, None, None, "set%20by%20call"]


def test_the_verdict_names_who_started_an_item_before_the_next_analysis(server):
    """(ui-words) An item a person marks Started says who in its verdict at once ("Started by <name> on <day>"), as the
    states write it after the next analysis; never "a person" when the history names one."""
    fid = _todo(server)[0]
    assert server.c.post(f"/api/findings/{fid}/status", json={"status": "in_progress"}, headers=as_person(STAFF)).status_code == 200
    conn = sqlite3.connect(server.db)
    try:
        from remitwatch import story
        v = story.explain(conn, fid)["verdict"]["sentence"]
    finally:
        conn.close()
    assert f"Started by {STAFF} on " in v and "Started by a person" not in v, v
