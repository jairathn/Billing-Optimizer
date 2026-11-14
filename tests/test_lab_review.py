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
