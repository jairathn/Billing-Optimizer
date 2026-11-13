"""Third review, area: routine-rate patterns (SECOND_RATE). Invented data only."""
from __future__ import annotations

import json
import sys

import os  # noqa: E402
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from remitwatch.actions import apply_status  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from tests.test_features import build_store  # noqa: E402
from tests.test_review_rates import _plan_pattern, api_client, claim, findings  # noqa: E402


import pytest  # noqa: E402


@pytest.mark.xfail(strict=True, reason="known limit: an individual NPI whose remittances never carry a first name looks like a group's; "
                   "telling them apart needs the national NPI registry (every rendering NPI in the archive does carry one somewhere)")
def test_an_individual_npi_sent_as_a_non_person_is_called_the_pa_rate(tmp_path):
    """R1 again, with the payer writing the rendering provider as an entity (NM1*82*2) with her own NPI and her name in
    NM103, a way some payers fill the loop. One physician (no Medicare visits yet, tier unknown): 25 visits at $117.60 and
    6 at $100.00 (85%), all on her NPI. The same clinician is paid the usual rate, so the PA explanation cannot hold."""
    claims = [claim(100 + i, "99214", "223.65", "117.60", prov="2*ALEX DOCTOR MD") for i in range(25)]
    claims += [claim(200 + i, "99214", "223.65", "100.00", prov="2*ALEX DOCTOR MD") for i in range(6)]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    rows = findings(store)
    print([(r["signal"], r["provider_tier"], r["shortfall_cents"]) for r in rows])
    for r in rows:
        if r["signal"] == "SECOND_RATE":
            print("NOTE:", r["evidence"]["note"])
    npis = {r[0] for r in store.query("SELECT DISTINCT rendering_provider_npi FROM claims")}
    assert len(npis) == 1          # one NPI on every visit, paid both amounts
    assert [r["signal"] for r in rows] == ["UNDERPAID_VS_OWN_RATE"] * 6, \
        "six visits by the clinician paid $117.60 on 25 others are folded into 'the rate for a PA or NP' and leave the appeals"
    store.close()


def test_continuations_worked_dismissed_and_reopened_keep_every_line_in_view(tmp_path):
    """P0 appealed; four new lines form continuation 1, which is dismissed (the page's button: no note); three more form
    continuation 2; continuation 1 is reopened. Every line must be in exactly one current finding, and the open ones open."""
    store = _plan_pattern(tmp_path, range(200, 206))
    Detector(store, run_id="one").run()
    [p0] = findings(store, "signal='SECOND_RATE'")
    apply_status(store.conn, p0["finding_id"], "appealed", "Sent", actor="biller")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [claim(300 + i, "99214", "223.65", "90.00", plan="HMO GOLD", dos="20260901") for i in range(4)], pay_date="20260920")
    Detector(store, run_id="two").run()
    [p1] = [r for r in findings(store, "signal='SECOND_RATE'") if r["finding_id"] != p0["finding_id"]]
    apply_status(store.conn, p1["finding_id"], "dismissed", None, actor="manager")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [claim(400 + i, "99214", "223.65", "90.00", plan="HMO GOLD", dos="20261001") for i in range(3)], pay_date="20261020")
    Detector(store, run_id="three").run()
    apply_status(store.conn, p1["finding_id"], "open", None, actor="manager")
    store.conn.commit()
    Detector(store, run_id="four").run()
    rows = findings(store, "signal='SECOND_RATE'")
    seen = {}
    for r in rows:
        print((r["finding_id"][:6], r["status"], r["stale"], r["evidence"].get("lines"), r["evidence"].get("continues", "")[:6], r["title"][-60:]))
        if not r["stale"]:
            for c in r["evidence"]["claims"]:
                seen.setdefault(c[0], []).append(r["status"])
    every = [f"CB{i:07d}C017" for i in list(range(200, 206)) + list(range(300, 304)) + list(range(400, 403))]
    assert all(len(seen.get(c, [])) == 1 for c in every), seen
    assert all(seen[c] == ["appealed"] for c in every[:6]) and all(seen[c] == ["open"] for c in every[6:]), seen
    store.close()


def test_a_redelivered_remittance_moves_an_appealed_patterns_claims_into_a_new_open_one(tmp_path, monkeypatch):
    """TriZetto re-delivers the remittance that carried the six HMO GOLD claims (same trace number, corrected content):
    the store keeps the newer copy and its lines have new keys. The appealed pattern keeps no line; the six claims come
    back as a new open finding titled 'paid since an earlier finding at this amount was worked'."""
    from tests.test_remitwatch import remit
    store = _plan_pattern(tmp_path, range(200, 206))
    Detector(store, run_id="one").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    apply_status(store.conn, p["finding_id"], "appealed", "Sent the six claims", actor="biller")
    store.conn.commit()
    trace = store.query("SELECT DISTINCT t.trace_number FROM transactions t JOIN claims c ON c.transaction_key=t.transaction_key "
                        "WHERE c.patient_control_number='CB0000200C017'")[0][0]
    print("trace of the six claims:", trace)
    # the file that carried them: claims 120-124 and the six, ISA control 4 (tests/test_features.build_store numbering)
    rows = [claim(120 + i, "99214", "223.65", "117.60") for i in range(5)] + [claim(i, "99214", "223.65", "90.00", plan="HMO GOLD") for i in range(200, 206)]
    path = tmp_path / "zz_redelivered.RMT"
    corrected = "".join(rows).replace("*PC120*", "*PC120A*")         # the payer corrected one claim's reference
    path.write_bytes(remit(corrected, "100.00", seg_count=99, isa_ctrl=trace.replace("TRACE", ""), pay_date="20260415"))
    import os
    import time
    os.utime(path, (time.time() + 3600, time.time() + 3600))       # delivered later
    from remitwatch.store import Store
    store = Store(str(tmp_path / "s.sqlite"))
    store.ingest_835_paths([str(path)])
    print("superseded:", store.mark_superseded())
    Detector(store, run_id="two").run()
    rows = findings(store, "signal='SECOND_RATE'")
    for r in rows:
        print((r["status"], r["stale"], r["evidence"].get("lines"), r["title"][-70:]))
    six = {f"CB{i:07d}C017" for i in range(200, 206)}
    reborn = [r for r in rows if r["status"] == "open" and not r["stale"] and six <= {c[0] for c in r["evidence"]["claims"]}]
    assert not reborn, "the six claims the biller appealed are a new open finding 'paid since an earlier finding at this amount was worked'; the appeal is on a stale row"
    store.close()


def test_an_appealed_rate_change_pattern_drops_out_once_the_new_rate_is_the_commonest(tmp_path, monkeypatch):
    """Humana pays 99214 at $117.60 on 40 visits through April, then $100.00 from May: a rate-change pattern, which the
    biller disputes (appealed). Humana keeps paying $100.00; by September more 2026 visits are at $100.00 than at $117.60,
    so $100.00 becomes the 'usual' rate. The appealed pattern keeps none of its lines (they are no longer below the usual
    rate) and goes stale, out of every total, although 'a routine-rate finding someone worked keeps exactly the lines it
    had when they worked it, whatever the rules now say about its amount or reason'."""
    from tests.test_resolution import cl
    store = build_store(tmp_path, [cl(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)], payer="KY MEDICARE")
    store.close()
    store = build_store(tmp_path, [cl(100 + i, "99214", "223.65", "117.60", cfi="16", dos="20260115") for i in range(40)], payer="HUMANA", pay_date="20260201")
    store.close()
    store = build_store(tmp_path, [cl(200 + i, "99214", "223.65", "100.00", cfi="16", dos="20260505") for i in range(6)], payer="HUMANA", pay_date="20260520")
    Detector(store, run_id="one").run()
    [p] = findings(store, "signal='SECOND_RATE'")
    print("one:", p["title"], "|", p["evidence"]["note"][:90])
    apply_status(store.conn, p["finding_id"], "appealed", "Disputed the May rate cut with the Humana rep", actor="biller")
    store.conn.commit()
    store.close()
    store = build_store(tmp_path, [cl(300 + i, "99214", "223.65", "100.00", cfi="16", dos="20260901") for i in range(40)], payer="HUMANA", pay_date="20260915")
    Detector(store, run_id="two").run()
    rows = findings(store, "category IN ('underpayment','contract')")
    for r in rows:
        print("two:", (r["signal"], r["status"], r["stale"], r["shortfall_cents"], r["evidence"].get("lines")))
    store.close()
    c = api_client(tmp_path, monkeypatch)
    appealed = [x for x in c.get("/api/progress").json()["pipeline"] if x["status"] == "appealed"]
    print("Progress pipeline, appealed:", appealed)
    kept = [r for r in rows if r["finding_id"] == p["finding_id"]]
    assert kept and not kept[0]["stale"] and kept[0]["evidence"]["lines"] == 6, \
        "the appealed rate dispute is stale and out of the totals once the new rate is the commonest; Progress shows nothing appealed"


def test_the_commonest_plan_explains_lines_it_is_paid_the_usual_rate_on(tmp_path):
    """Anthem's commonest plan, PPO, is on 18 of the 25 visits paid the usual $117.60 (72 percent). Six PPO visits of
    the same physician are paid $90.00. The plan reason only asks that the plan be 25 points more common among the low
    lines than among the usual ones (100 against 72), so 'the plan PPO' explains six visits the PPO pays $117.60 for
    most of the time, and they leave the appeals."""
    med = [claim(i, "99214", "223.65", "127.80", cfi="MB") for i in range(1, 26)]        # the clinician is a physician
    store = build_store(tmp_path, med, payer="KY MEDICARE")
    store.close()
    claims = [claim(100 + i, "99214", "223.65", "117.60", plan="PPO") for i in range(18)]
    claims += [claim(120 + i, "99214", "223.65", "117.60", plan="HMO") for i in range(7)]
    claims += [claim(200 + i, "99214", "223.65", "90.00", plan="PPO") for i in range(6)]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    rows = findings(store, "payer_group='Anthem / BCBS' AND category IN ('underpayment','contract') AND COALESCE(stale,0)=0")
    print([(r["signal"], r["provider_tier"], r["shortfall_cents"]) for r in rows])
    for r in rows:
        if r["signal"] == "SECOND_RATE":
            print("NOTE:", r["evidence"]["note"])
    assert [r["signal"] for r in rows] == ["UNDERPAID_VS_OWN_RATE"] * 6, \
        "a plan paid the usual rate on 72 percent of the usual-rate visits is given as the reason six of its visits were paid less"
    store.close()


def test_the_office_most_visits_are_in_explains_lines_it_is_paid_the_usual_rate_on(tmp_path):
    """The same for the office reason: 14 of the 25 usual-rate visits are on Kentucky days (56 percent); six visits paid
    $90.00 are all on Kentucky days. 100 against 56 clears the 40-point margin, so 'Kentucky visits' explains them."""
    ky_days = ["20260302", "20260303", "20260304"]
    in_days = ["20260310", "20260311", "20260312"]
    med = [claim(1 + i, "99214", "223.65", "127.80", cfi="MB", dos=d) for i, d in enumerate(ky_days)]
    med += [claim(10 + i, "99214", "223.65", "127.80", cfi="MB", dos="20260105") for i in range(5)]
    store = build_store(tmp_path, med, payer="KY MEDICARE")
    store.close()
    store = build_store(tmp_path, [claim(20 + i, "99214", "223.65", "121.00", cfi="MB", dos=d) for i, d in enumerate(in_days)], payer="IN MEDICARE")
    store.close()
    claims = [claim(100 + i, "99214", "223.65", "117.60", dos=ky_days[i % 3]) for i in range(14)]
    claims += [claim(120 + i, "99214", "223.65", "117.60", dos=in_days[i % 3]) for i in range(11)]
    claims += [claim(200 + i, "99214", "223.65", "90.00", dos=ky_days[i % 3]) for i in range(6)]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    rows = findings(store, "payer_group='Anthem / BCBS' AND category IN ('underpayment','contract') AND COALESCE(stale,0)=0")
    print([(r["signal"], r["provider_tier"], r["shortfall_cents"]) for r in rows])
    for r in rows:
        if r["signal"] == "SECOND_RATE":
            print("NOTE:", r["evidence"]["note"], "| by office:", r["evidence"]["by_office_state"])
    assert [r["signal"] for r in rows] == ["UNDERPAID_VS_OWN_RATE"] * 6, \
        "the office where most usual-rate visits happen is given as the reason six of its visits were paid less"
    store.close()
