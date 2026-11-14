"""
Tests for the strategy lab after review: its arithmetic and its page's saving logic (tests/lab_review_check.js, run with
node), and the practice figures endpoint (/api/activity/assumptions): owner's password required, versions so two
windows cannot overwrite each other, and only plain figures stored. Every clinician, figure and amount here is made up.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

NODE = shutil.which("node")
PW, OWNER = "correct-horse-battery-7", "owner-staple-orbit-42"
DOC, IDLE = "1000000001", "1000000003"
URL = "/api/activity/assumptions"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_lab_arithmetic_and_saving():
    out = subprocess.run([NODE, os.path.join(ROOT, "tests", "lab_review_check.js")], capture_output=True, text=True, check=True, cwd=ROOT)
    res = json.loads(out.stdout)
    failed = {k: v[1] for k, v in res.items() if not v[0]}
    assert len(res) >= 50 and not failed, failed


def _asc_d(codes):
    """The ASC part of the calculator's payload for these codes, from Medicare's published tables (the PFS and the ASC
    fee schedule; no practice figure): the ASC's rates by year as calculator.py builds them, and _asc_payload's site,
    Indiana amounts, ranked codes and add-ons; with Medicare's Kentucky amounts for the Kentucky option."""
    from remitwatch import calculator
    from remitwatch.ascfs import ASC, WAGE_INDEX, asc_amount
    from remitwatch.mpfs import MPFS
    asc = {y: {c: [ASC[y][c][0], ASC[y][c][2], {o: asc_amount(y, o, c) for o in WAGE_INDEX.get(y, {})}] for c in sorted(codes) if c in ASC.get(y, {})}
           for y in ASC}
    asc.update(calculator._asc_payload(set(codes), {"mohs": [], "excision": []}, 0))
    asc["medicare_ky"] = {c: {y: list(MPFS[y]["KY"][c]) for y in MPFS if MPFS[y].get("KY", {}).get(c)} for c in sorted(codes)}
    return {"constants": {"mppr_share": calculator.MPPR_SHARE}, "asc": asc, "mppr": [], "add_on": []}


def _node_cases(d, cases):
    """LabModel.ascCase for each [codes, opts, prices] ('ky': the Mohs and the clinic's closure at Kentucky's office amounts,
    the ASC's closure at Indiana's facility amounts, as the strategy prices a Kentucky case)."""
    js = ("const LM=require(process.argv[1]);const d=JSON.parse(process.argv[2]);const out=JSON.parse(process.argv[3]).map(([codes,o,p])=>{"
          "if(p==='ky')o.price=(c,w)=>{const t=w==='asc'?d.asc.medicare:d.asc.medicare_ky;const r=(t[c]||{})['2027'];return r?r[w==='asc'?1:0]:null;};"
          "return LM.ascCase(d,'2027','mohs',codes,o);});process.stdout.write(JSON.stringify(out));")
    out = subprocess.run([NODE, "-e", js, os.path.join(ROOT, "remitwatch", "static", "lab_model.js"), json.dumps(d), json.dumps(cases)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_asc_case_2027_indiana_amounts():
    """Mohs with one extra stage and the closure the same day, the closure in the Newburgh ASC against the clinic, at
    Medicare's 2027 Indiana amounts: what the practice gains a case owning the ASC (and the surgeon alone, for 13132)."""
    want = {"12052": 12995, "13132": 12693, "13152": 29103, "14040": 89335, "15260": 79595}
    d = _asc_d(set(want) | {"17311", "17312"})
    got = _node_cases(d, [[f"{c} 17311", {"stages": 1}, None] for c in want])
    owner = {c: round(v["B"] + v["fee"] - v["A"]) for c, v in zip(want, got)}
    assert owner == want
    assert round(got[1]["B"] - got[1]["A"]) == -9128                      # 13132, the practice not owning the ASC


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_asc_case_kentucky_to_newburgh():
    """A Kentucky clinic's Mohs (one extra stage) with its closure sent to the Newburgh ASC the same day: the clinic at
    Kentucky's office amounts, the ASC's closure at Indiana's facility amount plus the ASC's fee, the 50% cut across the
    two claims (each line ranked at its own amount) or, with the switch off, each claim on its own. Worked out here from
    the tables on their own."""
    from remitwatch.ascfs import asc_amount
    from remitwatch.mpfs import MPFS
    codes = ["14040", "15260", "13132"]
    d = _asc_d(set(codes) | {"17311", "17312"})
    ky, ind = MPFS["2027"]["KY"], MPFS["2027"]["IN"]
    half = lambda v: int(v * 0.5 + 0.5)                                   # half a line, to the cent
    for c in codes:
        m, cl, st = ky["17311"][0], ky[c][0], ky["17312"][0]
        a = max(m, cl) + half(min(m, cl)) + st                             # all in the Kentucky clinic
        f = ind[c][1]
        b = max(m, f) + half(min(m, f)) + st                               # the closure in the ASC, cut across the claims
        apart = m + f + st
        fee = asc_amount("2027", "newburgh", c)
        got, off = _node_cases(d, [[f"{c} 17311", {"stages": 1}, "ky"], [f"{c} 17311", {"stages": 1, "cross": False}, "ky"]])
        assert (round(got["A"]), round(got["B"]), round(got["fee"]), round(off["B"])) == (a, b, fee, apart), c


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from remitwatch import api
    path = str(tmp_path / "figures.sqlite")
    import sqlite3
    sqlite3.connect(path).close()
    monkeypatch.setattr(api, "DB_PATH", path)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "OWNER_PASSWORD", OWNER)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    api._FAILED.clear()
    api._OWNER_FAILED.clear()
    return TestClient(api.app, raise_server_exceptions=False), path


AUTH = ("oda", PW)
OWNER_H = {"X-Owner-Password": OWNER}
XHR = {"X-Requested-With": "remitwatch", **OWNER_H}


def post(tc, body, version=None, raw=None):
    h = dict(XHR)
    if version:
        h["X-Figures-Version"] = version
    if raw is not None:
        return tc.post(URL, auth=AUTH, headers={**h, "Content-Type": "application/json"}, content=raw)
    return tc.post(URL, auth=AUTH, headers=h, json=body)


def test_no_owner_password_no_figures(client, monkeypatch):
    from remitwatch import api
    tc, _ = client
    monkeypatch.setattr(api, "OWNER_PASSWORD", "")
    r = tc.get(URL, auth=AUTH)
    assert r.status_code == 403 and "REMITWATCH_OWNER_PASSWORD" in r.json()["detail"]
    assert post(tc, {"scenarios": []}).status_code == 403
    assert tc.get(URL).status_code == 401                         # the login is still asked for first


def test_versions_stop_a_stale_save(client):
    tc, _ = client
    assert tc.get(URL, auth=AUTH, headers=OWNER_H).json() == {"assumptions": None}
    first = post(tc, {"comp": {DOC: {"type": "pct", "pct": 0.35}}, "scenarios": []})
    assert first.status_code == 200 and first.json()["version"]
    v1 = first.json()["version"]
    got = tc.get(URL, auth=AUTH, headers=OWNER_H).json()
    assert got["version"] == v1 and got["assumptions"]["comp"][DOC]["pct"] == 0.35
    # a window that read nothing, or read an older version, is refused and told to read again
    assert post(tc, {"scenarios": []}).status_code == 409
    second = post(tc, {"comp": {DOC: {"type": "pct", "pct": 0.35}, IDLE: {"type": "base", "base": 200000}}, "scenarios": []}, v1)
    assert second.status_code == 200 and second.json()["version"] != v1
    stale = post(tc, {"scenarios": []}, v1)
    assert stale.status_code == 409 and "another window" in stale.json()["detail"]
    assert tc.get(URL, auth=AUTH, headers=OWNER_H).json()["assumptions"]["comp"][IDLE]["base"] == 200000


@pytest.mark.parametrize("raw", [
    b'{"minutes":{"mohs_case":"\\"><img src=x onerror=alert(1)>"}}',
    b'{"minutes":{"mohs_case":-5}}',
    b'{"minutes":{"mohs_case":NaN}}',
    b'{"costs":{"per_clinic_day":Infinity}}',
    b'{"costs":{"per_clinic_day":1e400}}',
    b'{"session_minutes":true}',
    b'{"comp":"x"}',
    b'{"comp":{"1000000001":{"type":"pct","pct":1.5}}}',
    b'{"comp":{"1000000001":{"type":"bonus"}}}',
    b'{"comp":{"<b>":{"type":"pct","pct":0.3}}}',
    b'{"scenarios":{"a":1}}',
    b'{"scenarios":[{"name":"","who":"mohs"}]}',
    b'{"scenarios":[{"name":"a","who":"../x"}]}',
    b'{"scenarios":[{"name":"a","who":"mohs","x":{"mohs_cases":NaN}}]}',
    b'{"scenarios":[{"name":"a","who":"mohs","chosen":{"stage":{"on":true,"params":{"share":"x"}}}}]}',
    b'{"other":1}',
    b'{"__proto__":{"polluted":1}}',
    b'{"a":' + b"[" * 100000 + b"]" * 100000 + b"}",
    b"[1,2]",
])
def test_only_plain_figures_are_stored(client, raw):
    tc, _ = client
    r = post(tc, None, raw=raw)
    assert r.status_code == 400, (raw[:80], r.text)
    assert tc.get(URL, auth=AUTH, headers=OWNER_H).json() == {"assumptions": None}


def test_scenario_limits(client):
    tc, _ = client
    one = {"name": "x" * 100, "who": "mohs", "x": {"year": "2027", "q": {"commercial": 0.6}, "corrected": {"Plan A": True}},
           "chosen": {"stage": {"on": True, "params": {"share": 0.01}}}, "saved_at": "2026-07-01T00:00:00Z"}
    assert post(tc, {"scenarios": [dict(one, name="y" * 101)]}).status_code == 400
    assert post(tc, {"scenarios": [dict(one, name=f"s{i}") for i in range(201)]}).status_code in (400, 413)
    assert post(tc, {"scenarios": [one]}).status_code == 200


def test_figures_saved_before_the_checks_still_read(client):
    tc, path = client
    import sqlite3
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", ("calculator:assumptions", '{"minutes":{"mohs_case":NaN,"biopsy":4},"costs":{"per_stage":Infinity}}'))
    c.commit()
    r = tc.get(URL, auth=AUTH, headers=OWNER_H)
    assert r.status_code == 200
    body = r.json()
    assert body["assumptions"]["minutes"] == {"mohs_case": None, "biopsy": 4} and body["assumptions"]["costs"]["per_stage"] is None and body["version"]
    # and can be saved over with that version
    assert post(tc, {"minutes": {"mohs_case": 30, "biopsy": 4}}, body["version"]).status_code == 200
    c.execute("UPDATE meta SET value='not json' WHERE key='calculator:assumptions'")
    c.commit()
    c.close()
    bad = tc.get(URL, auth=AUTH, headers=OWNER_H).json()
    assert bad["assumptions"] is None and bad["error"] and bad["version"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_what_the_page_saves_the_server_takes(client):
    """The page cleans what it read before saving it back (LabModel.clean): what it sends always passes the server's
    checks, so figures saved before the checks existed never block a save."""
    tc, _ = client
    messy = {"minutes": {"mohs_case": "20", "biopsy": -1, "layered": 12}, "costs": {"per_stage": 1e308}, "session_minutes": "x",
             "comp": {DOC: {"type": "pct", "pct": 0.35, "junk": 1}, "bad id": {"type": "pct"}},
             "scenarios": [{"name": "Keep", "who": "mohs", "x": {"mohs_cases": 5, "deep": {"a": {"b": {"c": 1}}}}, "chosen": {"stage": {"on": True, "params": {"share": 0.2, "x": "y"}}}},
                           {"name": 3, "who": "mohs"}], "extra": {"a": 1}}
    js = ("const LM=require(process.argv[1]);process.stdout.write(JSON.stringify(LM.clean(JSON.parse(process.argv[2]))));")
    out = subprocess.run([NODE, "-e", js, os.path.join(ROOT, "remitwatch", "static", "lab_model.js"), json.dumps(messy)],
                         capture_output=True, text=True, check=True)
    cleaned = json.loads(out.stdout)
    r = post(tc, cleaned)
    assert r.status_code == 200, r.text
    got = tc.get(URL, auth=AUTH, headers=OWNER_H).json()["assumptions"]
    assert got["minutes"]["layered"] == 12 and got["minutes"]["mohs_case"] is None and got["comp"][DOC] == {
        "type": "pct", "pct": 0.35, "per_wrvu": None, "base": None, "threshold": None, "above_pct": None}
    assert [s["name"] for s in got["scenarios"]] == ["Keep"] and got["scenarios"][0]["chosen"]["stage"]["params"] == {"share": 0.2}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_scenario_saved_after_a_box_is_emptied_is_taken(client):
    """A box with no default, emptied, is no setting: the page sends a new scenario through LabModel.cleanScenario, so a
    setting left not entered never reaches the server, which takes only numbers."""
    tc, _ = client
    raw = {"name": "After an emptied box", "who": "mohs", "x": {"year": "2027", "visits": 20},
           "chosen": {"asc": {"on": True, "params": {"to_asc": 0.5, "own": None}}, "other_surgeon": {"on": False, "params": {"cost": None}}},
           "saved_at": "2026-07-01T00:00:00Z"}
    assert post(tc, {"scenarios": [raw]}).status_code == 400
    js = "const LM=require(process.argv[1]);process.stdout.write(JSON.stringify(LM.cleanScenario(JSON.parse(process.argv[2]))));"
    out = subprocess.run([NODE, "-e", js, os.path.join(ROOT, "remitwatch", "static", "lab_model.js"), json.dumps(raw)],
                         capture_output=True, text=True, check=True)
    cleaned = json.loads(out.stdout)
    r = post(tc, {"scenarios": [cleaned]})
    assert r.status_code == 200, r.text
    got = tc.get(URL, auth=AUTH, headers=OWNER_H).json()["assumptions"]["scenarios"][0]
    assert got["chosen"] == {"asc": {"on": True, "params": {"to_asc": 0.5}}, "other_surgeon": {"on": False, "params": {}}}


def test_state_folder_saves_only_over_the_version_read(client, tmp_path, monkeypatch):
    """Serverless: the figures live in the state folder; a write goes through only if nobody wrote in between."""
    from remitwatch import api
    from remitwatch.state import Conflict, LocalState
    tc, _ = client
    st = LocalState(str(tmp_path / "state"))
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setattr(api, "_cloud_state", lambda: (None, st))
    first = post(tc, {"costs": {"per_stage": 25}})
    assert first.status_code == 200
    v1 = first.json()["version"]
    assert tc.get(URL, auth=AUTH, headers=OWNER_H).json()["version"] == v1
    assert post(tc, {"costs": {"per_stage": 30}}).status_code == 409
    assert post(tc, {"costs": {"per_stage": 30}}, v1).status_code == 200
    # someone writes between the check and the write: the store refuses the conditional write, and so does the route
    v2 = tc.get(URL, auth=AUTH, headers=OWNER_H).json()["version"]
    real_put = st.put

    def racing_put(name, data, mode="overwrite", rev=None):
        real_put(name, b'{"costs":{"per_stage":99}}')
        return real_put(name, data, mode=mode, rev=rev)
    monkeypatch.setattr(st, "put", racing_put)
    assert post(tc, {"costs": {"per_stage": 31}}, v2).status_code == 409
    monkeypatch.setattr(st, "put", real_put)
    assert json.loads(st.get(api.ASSUMPTIONS))["costs"]["per_stage"] == 99
    assert issubclass(Conflict, Exception)


# --------------------------------------------------------------------------- re-check fixes: the figures endpoint

def _full_scenario(i: int, payers: int = 24) -> dict:
    """A scenario as large as the page saves one: the whole calculator plan with every contract ticked, a target and the
    indexing for each payer, and every strategy set."""
    groups = [f"Plan {k:02d} Medicare Advantage" for k in range(payers)]
    x = {"year": "2027", "days_per_week": 3.6, "weeks_per_year": 46.5, "mohs_cases": 6.25, "mohs_hn": 0.66, "stages": 1.47, "fg_share": 0.49,
         "lay_mohs": 0.3, "excisions": 0.8, "lay_exc": 0.78, "visits": 14.2, "new_share": 0.18, "est_high": 0.31, "bx_lesions": 3.4,
         "q": {"commercial": 0.59, "medicare": 0.5, "medicare_advantage": 0.54, "supplement": 0.5, "other": 0.3, "medicaid": 0},
         "corrected": {g: True for g in groups}, "targets": {g: 1.05 for g in groups}, "indexed": {g: True for g in groups},
         "fix_mode": "contract", "target_pct": 1.0, "include_ancillary": True, "include_path": True, "cut_add_ons": True}
    chosen = {f"strategy_{k}": {"on": True, "params": {f"p{j}": 0.25 + j for j in range(6)}} for k in range(14)}
    return {"name": f"Scenario {i}: every contract at Medicare, the ASC plan and more", "who": "mohs", "x": x, "chosen": chosen,
            "saved_at": "2026-11-14T12:00:00.000Z"}


def test_two_hundred_full_scenarios_fit_under_a_hard_cap(client):
    """The page lets the owner keep 200 scenarios: 200 as large as the page makes them, with the figures and pay plans, fit
    under the server's cap with room to spare, so the page's own limit is the one met; the cap still holds."""
    from remitwatch import api
    tc, _ = client
    body = {"minutes": {"mohs_case": 45, "biopsy": 6}, "costs": {"per_clinic_day": 3000}, "session_minutes": 480,
            "comp": {f"10000000{k:02d}": {"type": "wrvu", "per_wrvu": 52.5, "threshold": 6000, "above_pct": 0.4} for k in range(20)},
            "scenarios": [_full_scenario(i) for i in range(api.MAX_SCENARIOS)]}
    size = len(json.dumps(body).encode())
    assert 3500 * api.MAX_SCENARIOS < size < 0.6 * api.MAX_ASSUMPTIONS_BYTES, size
    r = post(tc, body)
    assert r.status_code == 200, r.text[:200]
    got = tc.get(URL, auth=AUTH, headers=OWNER_H).json()
    assert len(got["assumptions"]["scenarios"]) == api.MAX_SCENARIOS
    # one more scenario is the page's and the server's own limit, not the size
    over = post(tc, dict(body, scenarios=body["scenarios"] + [_full_scenario(999)]), got["version"])
    assert over.status_code == 400 and "at most 200" in over.json()["detail"]
    assert post(tc, None, got["version"], raw=b"{" + b" " * api.MAX_ASSUMPTIONS_BYTES + b"}").status_code == 413


@pytest.mark.parametrize("raw", [
    b'{"scenarios":[{"name":"ab\\ud83d","who":"mohs"}]}',                       # a lone surrogate in a name
    b'{"scenarios":[{"name":"a","who":"mohs","x":{"label":"\\udc00x"}}]}',      # ...in a plan's text
    b'{"scenarios":[{"name":"a","who":"mohs","x":{"\\ud83d":1}}]}',             # ...in a key
    b'{"scenarios":[{"name":"a","who":"mohs","saved_at":"2026\\ud800"}]}',      # ...in the date
])
def test_text_the_server_could_not_send_back_is_refused(client, raw):
    tc, _ = client
    r = post(tc, None, raw=raw)
    assert r.status_code == 400, r.text
    assert tc.get(URL, auth=AUTH, headers=OWNER_H).json() == {"assumptions": None}


def test_names_are_measured_as_the_page_measures_them_and_old_bad_text_still_reads(client):
    tc, path = client
    one = {"who": "mohs", "x": {}, "chosen": {}}
    # 50 emoji are 100 UTF-16 units, as the page counts (and cuts) a name: accepted; 51 are 102: refused
    assert post(tc, {"scenarios": [dict(one, name="\U0001F600" * 50)]}).status_code == 200
    v = tc.get(URL, auth=AUTH, headers=OWNER_H).json()["version"]
    assert post(tc, {"scenarios": [dict(one, name="\U0001F600" * 51)]}, v).status_code == 400
    assert post(tc, {"scenarios": [dict(one, name="a", saved_at="\U0001F600" * 21)]}, v).status_code == 400
    # a lone surrogate saved before the checks existed: the figures still read (escaped), so the page can repair them
    import sqlite3
    c = sqlite3.connect(path)
    c.execute("UPDATE meta SET value=? WHERE key='calculator:assumptions'", ('{"scenarios":[{"name":"ab\\ud83d","who":"mohs"}]}',))
    c.commit()
    c.close()
    r = tc.get(URL, auth=AUTH, headers=OWNER_H)
    assert r.status_code == 200 and r.json()["assumptions"]["scenarios"][0]["name"] == "ab\ud83d" and r.json()["version"]
    assert post(tc, {"scenarios": [dict(one, name="ab")]}, r.json()["version"]).status_code == 200


@pytest.mark.parametrize("where", [
    lambda n: {"session_minutes": n}, lambda n: {"minutes": {"mohs_case": n}}, lambda n: {"costs": {"per_stage": n}},
    lambda n: {"comp": {DOC: {"type": "wrvu", "per_wrvu": n}}},
    lambda n: {"scenarios": [{"name": "a", "who": "mohs", "x": {"visits": n}}]},
    lambda n: {"scenarios": [{"name": "a", "who": "mohs", "chosen": {"stage": {"on": True, "params": {"share": n}}}}]},
])
def test_an_integer_past_a_floats_range_is_refused_not_an_error(client, where):
    tc, _ = client
    body = json.dumps(where("HUGE")).replace('"HUGE"', "1" + "0" * 400).encode()          # 1e400 written as an integer
    r = post(tc, None, raw=body)
    assert r.status_code == 400, (body[:60], r.status_code, r.text[:200])
    assert tc.get(URL, auth=AUTH, headers=OWNER_H).json() == {"assumptions": None}
    assert post(tc, None, raw=json.dumps(where(10 ** 300)).encode()).status_code in (200, 400)       # within range: an answer, never a 500


def test_the_state_folder_takes_one_of_two_saves_from_one_version(tmp_path):
    """Serverless on a local state folder: saves from the same revision at the same moment, one wins and the others are
    told it changed (Conflict); none fails, and the winner's figures are what is stored."""
    import threading
    from remitwatch.state import Conflict, LocalState
    st = LocalState(str(tmp_path / "state"))
    name = "calculator/assumptions.json"
    st.put(name, b"{}", mode="add")
    for rnd in range(25):
        rev = st.meta(name)["rev"]
        go, won, lost, errors = threading.Barrier(8), [], [], []

        def save(i):
            data = json.dumps({"round": rnd, "writer": i}).encode()
            go.wait()
            try:
                st.put(name, data, mode="update", rev=rev)
                won.append(data)
            except Conflict:
                lost.append(i)
            except Exception as exc:          # noqa: BLE001
                errors.append(repr(exc))
        threads = [threading.Thread(target=save, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors and len(won) == 1 and len(lost) == 7, (rnd, errors, len(won))
        assert st.get(name) == won[0] and st.meta(name)["rev"] != rev
    # a second file of the same size written in the same instant still has a revision of its own; no temporary or lock
    # file is listed
    a = st.meta(name)["rev"]
    st.put(name, st.get(name))
    assert st.meta(name)["rev"] != a and [m["name"] for m in st.list()] == [name]
    with pytest.raises(Conflict):
        st.put(name, b"{}", mode="add")
    with pytest.raises(Conflict):
        st.put(name, b"{}", mode="update", rev=a)


def test_the_figures_route_on_a_state_folder_gives_one_save_and_no_errors(client, tmp_path, monkeypatch):
    """The same through the route: eight windows save from one version at once; one is saved, the others are told to read
    the figures again (409), and nobody gets a 500."""
    import threading
    from remitwatch import api
    from remitwatch.state import LocalState
    tc, _ = client
    st = LocalState(str(tmp_path / "state"))
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setattr(api, "_cloud_state", lambda: (None, st))
    assert post(tc, {"costs": {"per_stage": 1}}).status_code == 200
    for rnd in range(6):
        v = tc.get(URL, auth=AUTH, headers=OWNER_H).json()["version"]
        go, codes = threading.Barrier(8), []

        def save(i):
            go.wait()
            codes.append(post(tc, {"costs": {"per_stage": 100 * rnd + i + 2}}, v).status_code)
        threads = [threading.Thread(target=save, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(codes) == [200] + [409] * 7, (rnd, codes)
