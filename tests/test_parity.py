"""The dashboard on a server shows what it shows on Vercel: the same data served both ways (the Vercel site's state folder,
and a server's store moved from it with import-cloud), every page's API compared answer for answer."""
from __future__ import annotations

import base64
import json

import pytest

from tests.test_cloud import box  # noqa: F401  (the fixture: a fake Dropbox holding the Vercel site's state folder)

PW = "correct-horse-battery-7"
XHR = {"X-Requested-With": "remitwatch"}
# what every page asks for (index.html, today.js, desk.js, features.js, faxes.js, providers.js and the calculator)
PAGES = [
    "/api/summary", "/api/summary?status=all", "/api/findings", "/api/findings?status=all&limit=500",
    "/api/findings?category=downcoding&status=open,in_progress&deadline=valid&sort=deadline&limit=20",
    "/api/findings?category=denial&status=all&sort=shortfall&limit=50", "/api/findings?status=appealed&deadline=followup&sort=sent",
    "/api/worklist/counts", "/api/worklist/counts?category=downcoding,bundling,denial,underpayment&status=open,in_progress&deadline=valid",
    "/api/brief", "/api/desk?category=downcoding", "/api/desk?category=denial", "/api/desk?category=bundling", "/api/desk?category=underpayment",
    "/api/desk?category=downcoding,bundling,denial,underpayment&status=open,in_progress&deadline=valid",
    "/api/payers", "/api/providers", "/api/codes", "/api/volume", "/api/trends", "/api/trends?category=downcoding",
    "/api/rates?min_n=1", "/api/rates/matrix?year=2026", "/api/breakdown?dim=payer", "/api/breakdown?dim=provider&dim2=code_pair&family=downcoding",
    "/api/coverage", "/api/progress", "/api/alerts", "/api/alerts?status=all", "/api/fees/payers",
    "/api/hide", "/api/faxes/payers", "/api/lessons", "/api/appeals/appealable", "/api/appeals/ema_queue",
    "/api/activity/overview", "/api/activity/trend", "/api/activity/recent", "/api/activity/calculator",
]


def _strip(x):
    """Leave out what names the host and the moment (the copy's build time, a run's own timings), nothing about the data."""
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in ("mode", "built", "generated_at", "runs", "sync")}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


@pytest.fixture()
def both(box, tmp_path, monkeypatch):
    """One set of practice data, served as the Vercel site serves it and as a server serves it after the move."""
    from fastapi.testclient import TestClient

    from remitwatch import api, cli, cloud
    from tests.test_cloud import DATA, cycle, visits_with_downcodes
    from tests.test_features import claim, clean_visits, svc
    from tests.test_remitwatch import remit
    root, client, state = box
    claims = visits_with_downcodes(4) + clean_visits(400, 25, "99214", "223.65", "117.60") + [
        claim(500 + i, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~")) for i in range(3)] + [
        claim(600, svc("99214:57", "223.65", "0.00", None, "CAS*CO*97*223.65~")), claim(601, svc("99214", "223.65", "110.00", "110.00", "CAS*CO*45*113.65~"))]
    (root / DATA).mkdir(parents=True, exist_ok=True)
    for n, i in enumerate(range(0, len(claims), 10)):
        (root / DATA / f"r{n:03d}.RMT").write_bytes(remit("".join(claims[i:i + 10]), "100.00", seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date="20261110"))
    cycle(state, client)
    monkeypatch.setenv("REMITWATCH_STATE_DIR", str(root / "RemitWatch-state"))
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    auth = dict(XHR, Authorization="Basic " + base64.b64encode(f"oda:{PW}".encode()).decode())
    tc = TestClient(api.app)
    # people's work on the Vercel site, through its own pages: a status, a hidden reason, a payer's fax number
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    monkeypatch.setattr(cloud, "SERVING_COPY", cloud.ServingCopy())
    items = tc.get("/api/findings?status=all&limit=500", headers=auth).json()["items"]
    denial = next(i for i in items if i["signal"] == "DENIED_MEDICAL_NECESSITY")
    assert tc.post(f"/api/findings/{denial['finding_id']}/status", json={"status": "in_progress", "notes": "called payer"}, headers=auth).status_code == 200
    fifty7 = next(i for i in items if (i["signal"] or "").startswith("EM_BUNDLED_MOD57"))
    assert tc.post("/api/hide", json={"payer_group": fifty7["payer_group"], "signal": fifty7["signal"], "note": "never pays 57"}, headers=auth).status_code == 200
    assert tc.post("/api/faxes/payer", json={"payer_group": denial["payer_group"], "number": "(800) 555-0142"}, headers=auth).status_code == 200
    # the move
    db = str(tmp_path / "server" / "remitwatch.sqlite")
    assert cli.main(["--db", db, "import-cloud"]) == 0

    def vercel(path):
        monkeypatch.setenv("REMITWATCH_MODE", "serverless")
        return tc.get(path, headers=auth)

    def server(path):
        monkeypatch.setenv("REMITWATCH_MODE", "server")
        monkeypatch.setattr(api, "DB_PATH", db)
        return tc.get(path, headers=auth)
    return vercel, server, items


def test_every_page_answers_the_same_on_the_server(both):
    vercel, server, items = both
    differ = []
    for path in PAGES + [f"/api/findings/{i['finding_id']}" for i in items[:12]]:
        a, b = vercel(path), server(path)
        assert a.status_code == 200, (path, a.status_code, a.text[:200])
        assert b.status_code == 200, (path, b.status_code, b.text[:200])
        if _strip(a.json()) != _strip(b.json()):
            differ.append(path)
    assert differ == [], differ


def test_the_meta_line_and_the_work_people_did_are_the_same(both):
    vercel, server, items = both
    a, b = vercel("/api/meta").json(), server("/api/meta").json()
    for k in ("remits", "lines", "files", "show_phi", "modmed", "fax", "trizetto"):
        assert a.get(k) == b.get(k), k
    assert a["mode"] == "serverless" and b["mode"] == "server"
    # the status, the hidden reason and the fax number recorded on the Vercel site are on the server
    st = {i["finding_id"]: i["status"] for i in server("/api/findings?status=all&limit=500").json()["items"]}
    assert "in_progress" in st.values() and "dismissed" in st.values()
    assert len(server("/api/hide").json()["rules"]) == 1
    assert server("/api/faxes/payers").json()["payers"][0]["numbers"]["appeal"]["number"] == "(800) 555-0142"


def test_with_the_fax_line_and_the_trizetto_login_on_both_sites_only_one_acts(both, monkeypatch):
    """After the move both sites hold the same work, so both would find the same follow-ups due. With SRFax and the
    TriZetto login set on both (the setup steps copy every setting across), exactly one site faxes and signs in to
    TriZetto: by default the server. The other shows its Faxes page, says why it sends nothing, and never signs in, so
    no fax goes out twice and TriZetto never sees two sign-ins on one login."""
    from remitwatch import fax, trizetto
    vercel, server, _items = both
    for k, v in {"SRFAX_ACCESS_ID": "12345", "SRFAX_ACCESS_PWD": "invented-fax-pw", "TRIZETTO_SFTP_USER": "invented-user",
                 "TRIZETTO_SFTP_PASSWORD": "invented-sftp-pw"}.items():
        monkeypatch.setenv(k, v)
    for k in ("REMITWATCH_FAX_SENDER", "TRIZETTO_SFTP_SERVERLESS"):
        monkeypatch.delenv(k, raising=False)

    def acts():
        """Per site: whether its Faxes page says it sends, whether its own runs get a fax line, whether it reads TriZetto."""
        out = {}
        for name, get in (("vercel", vercel), ("server", server)):
            page = get("/api/faxes").json()
            out[name] = (page["sends"], fax.provider() is not None, trizetto.configured())
            assert page["configured"] and (page["sends"] or page["not_here"]), name     # one that does not send says why
        return out
    assert acts() == {"vercel": (False, False, False), "server": (True, True, True)}
    # the practice can name the Vercel site instead: then the server stops faxing (one sender either way)
    monkeypatch.setenv("REMITWATCH_FAX_SENDER", "serverless")
    got = acts()
    assert got["vercel"][:2] == (True, True) and got["server"][:2] == (False, False)
    monkeypatch.setenv("REMITWATCH_FAX_SENDER", "none")                 # faxing paused everywhere
    assert [v[:2] for v in acts().values()] == [(False, False), (False, False)]
