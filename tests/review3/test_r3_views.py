"""Third review, area: the deadline views, the API and the page. Invented data only."""
from __future__ import annotations

import csv
import os
import io
import json
import sys
from datetime import date, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from remitwatch.detect import Detector  # noqa: E402
from remitwatch.report import build_report  # noqa: E402
from tests.test_features import build_store  # noqa: E402
from tests.test_review_rates import api_client, claim, findings  # noqa: E402

f8 = lambda d: d.strftime("%Y%m%d")      # noqa: E731


def _mostly_expired_pattern(tmp_path):
    """Aetna (180-day window): usual $700 for 17311 on 25 January visits; $400 on 7 visits paid 200 days ago (windows
    closed) and on 1 paid 20 days ago (open). Progress counts $300 live and $2,100 expired (tests/test_review_rates D3)."""
    today = date.today()
    old_pay, new_pay = today - timedelta(days=200), today - timedelta(days=20)
    old_dos, new_dos = old_pay - timedelta(days=14), new_pay - timedelta(days=14)
    assert old_dos.year == new_dos.year
    jan = date(old_dos.year, 1, 2)
    store = build_store(tmp_path, [claim(i, "99214", "223.65", "127.80", cfi="MB", dos=f8(jan)) for i in range(1, 26)], payer="KY MEDICARE",
                        pay_date=f8(jan + timedelta(days=14)))
    store.close()
    store = build_store(tmp_path, [claim(100 + i, "17311", "1200.00", "700.00", dos=f8(jan)) for i in range(25)], payer="AETNA",
                        pay_date=f8(jan + timedelta(days=14)))
    store.close()
    store = build_store(tmp_path, [claim(200 + i, "17311", "1200.00", "400.00", dos=f8(old_dos)) for i in range(7)], payer="AETNA", pay_date=f8(old_pay))
    store.close()
    store = build_store(tmp_path, [claim(300, "17311", "1200.00", "400.00", dos=f8(new_dos))], payer="AETNA", pay_date=f8(new_pay))
    Detector(store, run_id="t").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    return store, p


def test_the_weekly_reports_still_appealable_table_counts_closed_windows(tmp_path, monkeypatch):
    store, p = _mostly_expired_pattern(tmp_path)
    text = build_report(store, detail=True)
    section = text.split("## Open, still appealable, by payer")[1].split("##")[0]
    print(section)
    store.close()
    prog = api_client(tmp_path, monkeypatch).get("/api/progress").json()
    print("Progress live:", prog["live"], "expired:", prog["expired"])
    assert prog["live"]["cents"] == 30000                                  # control: Progress counts the one claim still open
    assert "$2,400" not in section, "the weekly report lists $2,400 as still appealable for Aetna; $2,100 of it is past its filing windows"


def test_the_breakdowns_still_appealable_column_counts_closed_windows(tmp_path, monkeypatch):
    store, p = _mostly_expired_pattern(tmp_path)
    store.close()
    c = api_client(tmp_path, monkeypatch)
    rows = c.get("/api/breakdown?dim=payer&family=underpayment").json()["rows"]
    print([(r["k1"], r["cents"], r["open_cents"], r["live_cents"]) for r in rows])
    [aetna] = [r for r in rows if r["k1"] == "Aetna"]
    assert aetna["live_cents"] <= 30000, f"'Still appealable' shows ${aetna['live_cents'] / 100:,.2f} for Aetna; $300.00 is"


def test_the_weekly_report_says_fewer_findings_are_due_than_it_capped(tmp_path):
    """45 one-off denials due within 45 days plus one routine-rate pattern with a claim due: the emailed report used to
    say '40+ findings are due'; now it says '41 findings are due'."""
    today = date.today()
    pay = today - timedelta(days=150)           # Aetna 180 days: due in 30 days
    dos = pay - timedelta(days=10)
    jan = date(dos.year, 1, 2)
    store = build_store(tmp_path, [claim(1 + i, "99214", "223.65", "127.80", cfi="MB", dos=f8(jan)) for i in range(25)], payer="KY MEDICARE",
                        pay_date=f8(jan + timedelta(days=14)))
    store.close()
    store = build_store(tmp_path, [claim(100 + i, "17311", "1200.00", "700.00", dos=f8(jan)) for i in range(25)], payer="AETNA",
                        pay_date=f8(jan + timedelta(days=14)))
    store.close()
    denied = [claim(400 + i, "17311", "1200.00", "0.00", paid="0.00", dos=f8(dos)).replace("CAS*CO*45*1200.00~", "CAS*CO*50*1200.00~")
              for i in range(45)]
    store = build_store(tmp_path, [claim(200 + i, "17311", "1200.00", "400.00", dos=f8(dos)) for i in range(6)] + denied, payer="AETNA", pay_date=f8(pay))
    Detector(store, run_id="t").run()
    n_due = store.query("""SELECT COUNT(*) FROM findings WHERE status='open' AND actionable IN ('appeal','correct_and_resubmit')
                           AND appeal_deadline BETWEEN date('now') AND date('now','+45 days')""")[0][0]
    assert n_due == 45 and findings(store, "signal='SECOND_RATE'")
    text = build_report(store)
    line = [x for x in text.splitlines() if "findings are due" in x]
    print(line)
    assert line and line[0].startswith(("46 ", "40+")), f"{n_due} one-offs and a pattern are due; the report says: {line[0]}"
    store.close()


def _big_pattern(tmp_path):
    """Aetna 99214: usual $117.60 on 560 January visits. From March, $100.00 (a rate change): 500 visits paid 215 days ago
    (windows closed) and 30 visits paid 10 days ago (windows open for 170 more days)."""
    today = date.today()
    old_pay, new_pay = today - timedelta(days=215), today - timedelta(days=10)
    old_dos, new_dos = old_pay - timedelta(days=15), new_pay - timedelta(days=15)
    jan = date(old_dos.year, 1, 5)
    assert jan < old_dos and old_dos.year == new_dos.year
    store = build_store(tmp_path, [claim(10000 + i, "99214", "223.65", "117.60", dos=f8(jan)) for i in range(560)], payer="AETNA",
                        pay_date=f8(jan + timedelta(days=15)))
    store.close()
    store = build_store(tmp_path, [claim(20000 + i, "99214", "223.65", "100.00", dos=f8(old_dos)) for i in range(500)], payer="AETNA", pay_date=f8(old_pay))
    store.close()
    store = build_store(tmp_path, [claim(30000 + i, "99214", "223.65", "100.00", dos=f8(new_dos)) for i in range(30)], payer="AETNA", pay_date=f8(new_pay))
    Detector(store, run_id="t").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    return store, p


def test_a_pattern_of_530_claims_lists_none_of_the_30_still_appealable(tmp_path, monkeypatch):
    store, p = _big_pattern(tmp_path)
    store.close()
    ev = p["evidence"]
    live_ids = {f"CB{30000 + i:07d}C017" for i in range(30)}
    print("lines:", ev["lines"], "| claims listed:", len(ev["claims"]), "| deadlines:", ev["deadlines"], "| note:", ev["note"][:80])
    assert ev["lines"] == 530
    in_evidence = live_ids & {c[0] for c in ev["claims"]}
    c = api_client(tmp_path, monkeypatch)
    text = c.get("/api/findings.csv?category=underpayment").text
    row = [r for r in csv.DictReader(io.StringIO(text)) if r["signal"] == "SECOND_RATE"][0]
    listed = row["claims"].split("; ")
    in_csv = {x.split(" (")[0] for x in listed} & live_ids
    print("CSV claims listed:", len(listed), "| still-open claims in the CSV:", len(in_csv), "| in the drawer:", len(in_evidence))
    assert in_csv == live_ids and in_evidence == live_ids, \
        "the export and the drawer list the 500 oldest claims, every one past its window, and none of the 30 that can still be raised"


def test_the_drawer_of_a_530_claim_pattern_shows_only_passed_windows(tmp_path):
    """The page's own drawerExtras (features.js) on the evidence detection wrote, in node."""
    import os
    import re
    import shutil
    import subprocess
    if not shutil.which("node"):
        import pytest
        pytest.skip("node is not installed")
    store, p = _big_pattern(tmp_path)
    store.close()
    f = {"signal": "SECOND_RATE", "evidence": p["evidence"], "appeal_deadline": p["appeal_deadline"], "deadline_basis": p["deadline_basis"]}
    res = subprocess.run(["node", os.path.join(REPO, "tests", "review_assets", "drawer_eval.js"), date.today().isoformat() + "T15:00:00Z",
                          json.dumps([{"f": f}])], capture_output=True, text=True, env=dict(os.environ, TZ="America/New_York"), timeout=60)
    assert res.returncode == 0, res.stderr
    html = json.loads(res.stdout)["out"][0]["html"]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    rows = re.findall(r"<tr><td>(CB\d+C017)</td>", html)
    passed = text.count("(passed)")
    print(text[:330])
    print("claim rows:", len(rows), "| marked passed:", passed)
    assert passed < len(rows), "the drawer says 30 lines are still inside their windows and lists 500 claims, every one '(passed)'"


def test_the_drawer_offers_an_appeal_packet_for_sequestration(tmp_path, monkeypatch):
    """The appeal button is now hidden only for SECOND_RATE. A claimless info finding (sequestration, MIPS) gets an
    'Appeal packet' that asks the payer to 'reprocess and pay' the sequestration."""
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB").replace("CAS*CO*45*95.85~", "CAS*CO*45*95.85~CAS*CO*253*2.56~") for i in range(1, 26)]
    store = build_store(tmp_path, med, payer="KY MEDICARE")
    Detector(store, run_id="t").run()
    rows = {r["signal"]: r for r in store.query("SELECT finding_id, signal, actionable, claim_key FROM findings")}
    print({k: (v["actionable"], v["claim_key"]) for k, v in rows.items()})
    store.close()
    seq = rows["SEQUESTRATION"]
    page = open(os.path.join(REPO, "remitwatch", "static", "index.html")).read()
    # the drawer offers the letter only where one exists: a claim, a takeback or a payment fee
    assert "const letter = f.claim_key || ['TAKEBACK','FEE_SKIM'].includes(f.signal) ? `<button id=\"btnAppeal\">" in page
    c = api_client(tmp_path, monkeypatch)
    pkt = c.get(f"/api/appeal/{seq['finding_id']}")
    print(pkt.status_code, pkt.json().get("subject"), "|", (pkt.json().get("letter") or "")[-260:])
    assert seq["actionable"] == "none" and not (pkt.status_code == 200 and "reprocess and pay" in pkt.json()["letter"]), \
        "a sequestration line (actionable: none) offers an appeal letter asking the payer to reprocess and pay it"
