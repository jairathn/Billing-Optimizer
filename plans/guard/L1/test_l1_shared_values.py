"""GUARD L1, "one input moves a shared value": failing tests on invented data, run against the frozen snapshot.

    cd <this folder> && PYTHONDONTWRITEBYTECODE=1 L1_SNAP=<snap> python3 -m pytest -q -p no:cacheprovider test_l1_shared_values.py

Each test states what a sane system does when ONE row, file or record is odd, and names the shared value it would move.
Every name, number, claim id, NPI and date below is invented. Nothing is written outside pytest's tmp_path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

SNAP = os.environ.get("L1_SNAP") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "snap")
sys.path.insert(0, SNAP)
os.environ.setdefault("REMITWATCH_ALLOW_OPEN", "1")
for k in list(os.environ):          # nothing real is reachable from these tests
    if k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "ANTHROPIC_", "SMTP_", "MODMED_")):
        del os.environ[k]

import pytest  # noqa: E402

from remitwatch.store import Store  # noqa: E402
from tests.test_features import build_store, clean_visits, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402
from tests.test_workable import charge  # noqa: E402

TODAY = "2026-11-23"
NPI = "1888888882"


def _claim(i: int, lines: str, *, npi: str = NPI, status: str = "1", cfi: str = "12", dos: str = "20260401") -> str:
    """One invented CLP loop, with its own rendering NPI."""
    return (f"CLP*CB{i:07d}C017*{status}*500.00*100.00*0.00*{cfi}*PC{i}*11~NM1*QC*1*SAMPLE*PAT****MI*M{i:08d}~"
            f"NM1*82*2*TEST PROVIDER*****XX*{npi}~REF*CE*PPO~DTM*232*{dos}~{lines}")


def _ingest(store: Store, tmp_path, name: str, data: bytes) -> None:
    p = tmp_path / name
    p.write_bytes(data)
    store.ingest_835_paths([str(p)])
    store.mark_superseded()


def _plb_only(pay_date: str, ctrl: str, payer: str = "HUMANA", payer_id: str = "61101") -> bytes:
    """A remittance that carries only a provider adjustment (no claim): the kind a payer sends between real ones."""
    return remit("", "71.59", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=ctrl, pay_date=pay_date,
                 plb="PLB*1777777772*20261231*WO:REF0001*-71.59~")


def _three_payer_store(tmp_path, pay_date: str = "20260901") -> Store:
    """Three payers that pay every week, all of whose remittances stop on the same day (the feed broke)."""
    s = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60"), pay_date=pay_date, payer="ANTHEM")
    s.close()
    s = build_store(tmp_path, clean_visits(101, 25, "99214", "223.65", "110.00"), pay_date=pay_date, payer="UNITED HEALTHCARE")
    s.close()
    return build_store(tmp_path, clean_visits(201, 25, "99214", "223.65", "105.00"), pay_date=pay_date, payer="AETNA")


# --------------------------------------------------------------------------------------- L1-1: the remittance feed date
def test_one_newer_file_from_one_payer_does_not_make_three_stopped_payers_current(tmp_path):
    """Anthem, UHC and Aetna stop on Sept 1. One adjustment-only Humana remittance dated Nov 20 arrives. A claim sent to
    Anthem on Sept 21 cannot be called unanswered: nothing after Sept 1 from Anthem (or from any payer that holds the volume)
    is on file. Shared value: MAX(payment_date) over every payer (detect.run_ema_open_claims `newest`)."""
    from remitwatch.detect import Detector
    s = _three_payer_store(tmp_path)
    charge(s.conn, "x", "CB0000700C017", "2026-09-20", "2026-09-21")
    charge(s.conn, "y", "CB0000701C017", "2026-07-20", "2026-07-21")
    s.conn.commit()
    _ingest(s, tmp_path, "plb_only.RMT", _plb_only("20261120", "000000777"))
    Detector(s, run_id="t", today=TODAY).run()
    chased = [r[0] for r in s.conn.execute("SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER' "
                                           "AND COALESCE(stale,0)=0 ORDER BY 1")]
    assert chased == ["CB0000701C017"], f"chased now: {chased}"


def test_a_remittance_dated_in_the_future_does_not_wipe_out_unanswered_claims(tmp_path):
    """A vendor test file (or a typo) paid 2099-12-31 must not move 'the newest remittance'. Shared value: the same MAX,
    which also sets `since` (newest - 1830 days): every claim served before 2094 drops out of the pass."""
    from remitwatch.detect import Detector
    s = _three_payer_store(tmp_path)
    charge(s.conn, "y", "CB0000701C017", "2026-07-20", "2026-07-21")
    s.conn.commit()
    Detector(s, run_id="t1", today=TODAY).run()
    before = [r[0] for r in s.conn.execute("SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0")]
    assert before == ["CB0000701C017"]
    _ingest(s, tmp_path, "vendor_test.RMT", _plb_only("20991231", "000000778"))
    Detector(s, run_id="t2", today=TODAY).run()
    after = [r[0] for r in s.conn.execute("SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0")]
    assert after == ["CB0000701C017"], "the unanswered claim vanished after one file dated 2099 arrived"


def test_a_one_day_bundle_after_a_gap_does_not_bridge_the_gap(tmp_path):
    """Residual risk even under hotfix 2's rule (H8: a volume-weighted median of each group's LATEST date): Anthem, UHC and
    Aetna stop on Sept 1; after six weeks one clearinghouse file dated Nov 20 arrives holding one day's remittances of all
    three. Each group's latest date jumps to Nov 20 though Sept 2 to Nov 19 never arrived, so a claim sent Sept 21 becomes
    'never answered'. A payer is current through a day only when its weeks up to that day are on file."""
    from remitwatch.detect import Detector
    s = _three_payer_store(tmp_path)
    charge(s.conn, "x", "CB0000700C017", "2026-09-20", "2026-09-21")
    s.conn.commit()
    co = "CAS*CO*45*106.05~"
    bundle = b"".join(remit(_claim(950 + i, svc("99214", "223.65", "117.60", "117.60", co, dos="20261110"), dos="20261110"), "117.60",
                            payer=p, payer_id=pid, seg_count=99, isa_ctrl=f"00000080{i}", pay_date="20261120")
                      for i, (p, pid) in enumerate((("ANTHEM", "ANTHM"), ("UNITED HEALTHCARE", "87726"), ("AETNA", "60054"))))
    _ingest(s, tmp_path, "one_day_bundle.RMT", bundle)
    Detector(s, run_id="t", today=TODAY).run()
    chased = [r[0] for r in s.conn.execute("SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER' AND COALESCE(stale,0)=0")]
    assert "CB0000700C017" not in chased, "one day of remittances after a six-week gap made the gap's claims 'never answered'"


def test_stale_feed_alert_survives_one_file_from_a_fourth_payer(tmp_path):
    """The stale-feed alarm (alerts.AlertEngine.asof = MAX(payment_date)) stays on while the payers that hold the volume
    are 83 days behind, whatever one small payer sent."""
    from remitwatch.alerts import AlertEngine
    s = _three_payer_store(tmp_path)
    assert "STALE_FEED" in {a["kind"] for a in AlertEngine(s.conn, today=TODAY).evaluate().values()}
    _ingest(s, tmp_path, "plb_only.RMT", _plb_only("20261120", "000000779"))
    kinds = {a["kind"] for a in AlertEngine(s.conn, today=TODAY).evaluate().values()}
    assert "STALE_FEED" in kinds, f"the stale-feed alert cleared after one small file: {sorted(kinds)}"


def test_stale_feed_alert_is_not_cleared_by_a_future_dated_file(tmp_path):
    from remitwatch.alerts import AlertEngine
    s = _three_payer_store(tmp_path)
    _ingest(s, tmp_path, "vendor_test.RMT", _plb_only("20991231", "000000780"))
    eng = AlertEngine(s.conn, today=TODAY)
    kinds = {a["kind"] for a in eng.evaluate().values()}
    assert eng.asof <= TODAY, f"the alerts' as-of date is {eng.asof}, after today"
    assert "STALE_FEED" in kinds


def test_blindness_banner_stays_on_after_one_small_file(tmp_path):
    """Owner rule 3 ('blindness is loud'): selfcheck.blindness reads the 835 feed's age from MAX(payment_date)."""
    from remitwatch import selfcheck
    s = _three_payer_store(tmp_path)
    assert selfcheck.blindness(s.conn, today=TODAY, save=False)["blind"]
    _ingest(s, tmp_path, "plb_only.RMT", _plb_only("20261120", "000000781"))
    out = selfcheck.blindness(s.conn, today=TODAY, save=False)
    assert out["blind"], "every page's blindness banner went away after one adjustment-only file from a small payer"


def test_home_screen_feed_health_stays_red_after_one_small_file(tmp_path):
    """insights.data_health ('Payments are current') and the header read the same single date."""
    from remitwatch import insights
    from remitwatch.detect import Detector
    s = _three_payer_store(tmp_path)
    _ingest(s, tmp_path, "plb_only.RMT", _plb_only("20261120", "000000782"))
    Detector(s, run_id="t", today=TODAY).run()
    feed = next(h for h in insights.brief(s.conn, TODAY)["health"] if h["key"] == "feed")
    assert not feed["ok"], f"the home screen says: {feed['title']}"


def test_fax_stale_hold_counts_the_feed_not_one_file(tmp_path, monkeypatch):
    """api._data_behind_days (the 14-day hold on appeals and second requests) is the age of MAX(payment_date)."""
    s = _three_payer_store(tmp_path)
    _ingest(s, tmp_path, "plb_only.RMT", _plb_only("20261120", "000000783"))
    s.close()
    from remitwatch import api
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "s.sqlite"))
    behind = api._data_behind_days()
    assert behind is not None and behind > api.STALE_APPEAL_DAYS, \
        f"the fax hold sees the remittances as {behind} days old; Anthem, UHC and Aetna stopped on Sept 1"


# --------------------------------------------------------------------------------------- L1-9: the provider tier
def test_one_line_from_a_new_npi_does_not_relabel_every_physician(tmp_path):
    """build_baselines: top = MAX over NPIs of each NPI's usual Medicare amount, with no minimum count; a provider whose
    usual amount is under 92% of the top is 'nonphysician'. One line from a new NPI (another locality, a mis-keyed amount)
    at 10% above the physician rate makes every physician a nonphysician."""
    from remitwatch.detect import Detector
    medicare = dict(cfi="MB", dos="20260401")
    phys = [_claim(300 + i, svc("99213", "150.00", "100.00", "100.00", "CAS*CO*45*50.00~"), npi="1111111116", **medicare)
            for i in range(30)]
    npp = [_claim(400 + i, svc("99213", "150.00", "85.00", "85.00", "CAS*CO*45*65.00~"), npi="1222222224", **medicare)
           for i in range(30)]
    s = build_store(tmp_path, phys + npp, pay_date="20260501", payer="KY MEDICARE")
    Detector(s, run_id="t1", today=TODAY).run()
    tiers = dict(s.conn.execute("SELECT npi, tier FROM provider_tiers"))
    assert tiers.get("1111111116") == "physician" and tiers.get("1222222224") == "nonphysician", tiers
    odd = _claim(500, svc("99213", "150.00", "110.00", "110.00", "CAS*CO*45*40.00~"), npi="1333333332", **medicare)
    _ingest(s, tmp_path, "one_odd_line.RMT", remit(odd, "110.00", payer="KY MEDICARE", seg_count=99, isa_ctrl="000000790",
                                                    pay_date="20260502"))
    Detector(s, run_id="t2", today=TODAY).run()
    tiers = dict(s.conn.execute("SELECT npi, tier FROM provider_tiers"))
    assert tiers.get("1111111116") == "physician", f"one line from another NPI relabelled the physician: {tiers}"


# --------------------------------------------------------------------------------------- L1-10: the practice's fee 'now'
def test_one_future_service_date_does_not_hide_every_fee_below_rate_item(tmp_path):
    """detect._newest_fees: since = MAX(service_date of every line) - 90 days. One line with a service date a year ahead
    (a typo) leaves no line 'recent', so every CHARGE_BELOW_RATE item is dropped as 'nothing charged below the rate lately'."""
    from remitwatch.detect import Detector
    uncapped = [_claim(600 + i, svc("99213", "200.00", "150.00", "150.00", "CAS*CO*45*50.00~", dos="20260901"), dos="20260901")
                for i in range(6)]
    capped = [_claim(700 + i, svc("99213", "120.00", "120.00", "120.00", "", dos="20260905"), dos="20260905") for i in range(6)]
    s = build_store(tmp_path, uncapped + capped, pay_date="20261001", payer="ANTHEM")
    Detector(s, run_id="t1", today=TODAY).run()
    n0 = s.conn.execute("SELECT COUNT(*) FROM findings WHERE signal='CHARGE_BELOW_RATE' AND COALESCE(stale,0)=0").fetchone()[0]
    assert n0 == 1, "the invented world should show one fee below Anthem's rate"
    typo = _claim(800, svc("17110", "177.56", "91.31", "109.57", "CAS*CO*45*67.99~", dos="20271015"), dos="20271015")
    _ingest(s, tmp_path, "typo.RMT", remit(typo, "91.31", payer="UNITED HEALTHCARE", seg_count=99, isa_ctrl="000000791",
                                            pay_date="20261002"))
    Detector(s, run_id="t2", today=TODAY).run()
    n1 = s.conn.execute("SELECT COUNT(*) FROM findings WHERE signal='CHARGE_BELOW_RATE' AND COALESCE(stale,0)=0").fetchone()[0]
    assert n1 == 1, "one line dated a year ahead hid the fee-below-rate item"


# --------------------------------------------------------------------------------------- L1-11: Production freshness
def _report_files(conn: sqlite3.Connection, rows: list[dict]) -> None:
    from remitwatch import ema_reports as er
    er.ensure_schema(conn)
    for r in rows:
        cols = ",".join(r)
        conn.execute(f"INSERT INTO ema_report_files ({cols}) VALUES ({','.join('?' * len(r))})", tuple(r.values()))
    conn.commit()


def test_one_small_production_file_does_not_make_two_months_of_postings_fresh(tmp_path):
    """ema_reports.coverage: production_as_of = MAX(as_of) over the current boxes, and freshness() calls Production fresh
    from it. A big service-date export through Sept 20 plus a two-day posted-date export (Nov 21-22): no box holds a
    posting from Sept 21 to Nov 20, yet Production reads 'fresh as of Nov 22', which lets G8 (unanswered_claims) judge
    every claim due by Nov 22 and states.py treat every NO_ANSWER item as checkable."""
    from remitwatch import ema_checks as ec
    from remitwatch import states
    conn = sqlite3.connect(str(tmp_path / "s.sqlite"))
    _report_files(conn, [
        dict(file_id="big", report="production", status="current", scope="service", svc_lo="2025-01-01", svc_hi="2026-08-31",
             posted_lo="2025-01-03", posted_hi="2026-09-20", as_of="2026-09-20"),
        dict(file_id="two_days", report="production", status="current", scope="posted", svc_lo="2025-06-01",
             svc_hi="2026-11-20", posted_lo="2026-11-21", posted_hi="2026-11-22", as_of="2026-11-22")])
    fr = ec.freshness(conn, TODAY)
    ctx = {"boxes": fr["boxes"]}
    problems = []
    if fr["production_fresh"]:
        problems.append(f"Production reads fresh as of {fr['production_as_of']}")
    if states._covered(ctx, "2026-10-15"):
        problems.append("a service of Oct 15 reads as covered by Production though no box holds its Oct-Nov postings")
    assert not problems, "; ".join(problems)


# --------------------------------------------------------------------------------------- L1-12: the A/R snapshot date
def test_one_row_dated_ahead_does_not_make_an_old_ar_export_current(tmp_path, monkeypatch):
    """iter_ar: as_of = MAX(service date, aging date), capped at today. An A/R pulled Oct 20 (every aging date on or before
    it) with one row whose service date is typed a few weeks ahead reads as an A/R of today: it then outranks a newer
    A/R, and freshness() calls the A/R current (EMA_STALE and the blindness banner stay quiet)."""
    from remitwatch import ema_reports as er
    from tests import ema_fixtures as fx
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", fx.HOST_SUFFIX)
    rows = [fx.ar_row(claim=fx.claim_id(i), link=fx.link_no(i), aging=fx.D(2026, 10, 20) - fx.dt.timedelta(days=i % 20),
                      svc=fx.D(2026, 10, 1)) for i in range(1, 31)]
    rows.append(fx.ar_row(claim=fx.claim_id(31), link=fx.link_no(31), aging=fx.D(2026, 10, 19), svc=fx.D(2026, 12, 15)))
    p = tmp_path / "AR Report.csv"
    p.write_bytes(fx.ar_csv(rows))
    rec = er.recognize(str(p), p.name)
    acct: dict = {}
    conn = sqlite3.connect(":memory:")
    er.ensure_schema(conn)
    list(er.iter_ar(str(p), rec, salt=er.key_salt(conn), account=acct, today=TODAY))
    assert acct["as_of"] <= "2026-10-20", f"the A/R pulled Oct 20 is dated {acct['as_of']} because of one row"


# --------------------------------------------------------------------------------------- L1-13: EMA's newest export
def _ema_row(conn, fp, sha, name, claim, family, *, status=None, sent=None, dos="2026-09-14", created=None, act=None,
             cpt="99214", cents=22365, payer="Anthem Blue Cross"):
    conn.execute("""INSERT INTO ema_rows (row_fingerprint, family, source_sha256, source_name, row_index, claim_id, activity_type,
                    bill_item_id, cpt, service_date, bill_created, charges_cents, insurance_payments_cents, payer, payer_id,
                    patient_id, provider, claim_status, claim_submission_date)
                    VALUES (?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,'Provider, Test',?,?)""",
                 (fp, family, sha, name, claim, act, fp, cpt, dos, created, cents, 0 if act else None, payer, "00660",
                  f"P{claim[4:9]}", status, sent))


def test_an_old_export_saved_again_today_does_not_turn_newer_statuses_back(tmp_path):
    """run_ema_open_claims ranks the weekly exports by the workbook's own creation stamp (ema_sources.created_at). The
    Oct 12 status export says the claim was paid; the Sept 17 export, opened and saved again on Nov 20, says 'Payment
    Pending'. The claim must not come back as 'never answered'."""
    from remitwatch.detect import Detector
    s = _three_payer_store(tmp_path, pay_date="20261110")
    c = s.conn
    _ema_row(c, "ch1", "sha_charges", "Encounter 9-14-26.xlsx", "CB0000901C017", "charge_lines", act="Charge",
             created="2026-09-15", sent="2026-09-15")
    _ema_row(c, "old1", "sha_old", "Claim & Claim Status 9-14-26 cleaned.xlsx", "CB0000901C017", "claim_status",
             status="Payment Pending", sent="2026-09-15")
    _ema_row(c, "new1", "sha_new", "Claim Status Report 9-14-26 to 10-12-26.xlsx", "CB0000901C017", "claim_status",
             status="Resolved/Paid", sent="2026-09-15")
    s.record_export("Encounter 9-14-26.xlsx", "sha_charges", "charge_lines", "2026-09-17T14:00:00Z", 1)
    s.record_export("Claim Status Report 9-14-26 to 10-12-26.xlsx", "sha_new", "claim_status", "2026-10-12T21:00:00Z", 1)
    s.record_export("Claim & Claim Status 9-14-26 cleaned.xlsx", "sha_old", "claim_status", "2026-11-20T09:00:00Z", 1)
    c.commit()
    Detector(s, run_id="t", today=TODAY).run()
    chased = [r[0] for r in c.execute("SELECT patient_control_number FROM findings WHERE signal IN ('NO_ANSWER','REJECTED_PRE_PAYER') "
                                      "AND COALESCE(stale,0)=0")]
    assert chased == [], f"a re-saved September export turned the October 'paid' back into work: {chased}"


# --------------------------------------------------------------------------------------- the class: one feed-date function
SHARED_DATE = __import__("re").compile(r"\b(MAX|MIN)\(\s*(?:\w+\.)?(payment_date|posted|last_posted|as_of|ingested_at)\s*\)",
                                        __import__("re").I)
OWNER = {"feeds.py"}        # the one module the guard lets compute store-wide data dates (to be written)


def store_wide_date_sites(root: str) -> list[str]:
    """file:line of every SQL MAX/MIN over a data date that is not grouped (per payer, claim or file): a store-wide value."""
    import re
    hits = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py") or name in OWNER:
            continue
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            text = fh.read()
        for m in SHARED_DATE.finditer(text):
            stmt = re.split(r'"""|"\)|"\s*,\s*\(|"\s*\)', text[m.end(): m.end() + 400])[0]
            if re.search(r"GROUP\s+BY|WHERE\s+[\w.]*(file_id|claim_key|link|finding_id)\s*=", stmt, re.I):
                continue
            site = f"{name}:{text.count(chr(10), 0, m.start()) + 1}"
            if site not in hits:
                hits.append(site)
    return hits


def test_every_module_takes_store_wide_data_dates_from_one_owner():
    """The class guard as a source check: no module but the owner computes 'the newest remittance', 'the newest posting'
    or 'the newest file' itself; consumers call the owner's clamped, quorum-based functions. Lists today's sites."""
    hits = store_wide_date_sites(os.path.join(SNAP, "remitwatch"))
    assert not hits, f"{len(hits)} places compute a store-wide data date themselves: {', '.join(hits)}"


# --------------------------------------------------------------------------------------- the proposed guard, as a reference
from datetime import date as _date, timedelta as _td  # noqa: E402

GROUPS = (("ANTHEM", "ANTHM"), ("UNITED HEALTHCARE", "87726"), ("AETNA", "60054"))


def _weekly_store(tmp_path, last: str = "2026-09-01", weeks: int = 8) -> Store:
    """Three payers paying every week for `weeks` weeks up to `last` (three claims a file), then nothing: a feed that broke."""
    s = Store(str(tmp_path / "w.sqlite"))
    paths, n = [], 0
    for w in range(weeks):
        day = (_date.fromisoformat(last) - _td(days=7 * (weeks - 1 - w))).strftime("%Y%m%d")
        for gi, (payer, pid) in enumerate(GROUPS):
            claims = "".join(_claim(1000 + 100 * gi + 3 * w + k, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~",
                                                                     dos=day), dos=day) for k in range(3))
            n += 1
            p = tmp_path / f"wk{n:03d}.RMT"
            p.write_bytes(remit(claims, "352.80", payer=payer, payer_id=pid, seg_count=99, isa_ctrl=f"{n:09d}", pay_date=day))
            paths.append(str(p))
    s.ingest_835_paths(paths)
    s.mark_superseded()
    return s


def _group():
    from remitwatch.codes import normalize_payer
    return lambda name: normalize_payer(name)[0]


def _bundle(day: str, ctrl0: int) -> bytes:
    return b"".join(remit(_claim(1900 + i, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos=day), dos=day), "117.60",
                          payer=p, payer_id=pid, seg_count=99, isa_ctrl=f"{ctrl0 + i:09d}", pay_date=day) for i, (p, pid) in enumerate(GROUPS))


def test_reference_feed_date_holds_against_a_stray_file_a_future_file_and_a_gap_bundle(tmp_path):
    """The proposed rule (reference_feed.coverage / feed_date / judgeable) on the three shapes of one odd input."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import reference_feed as rf
    s = _weekly_store(tmp_path)
    g = _group()
    assert rf.feed_date(s.conn, TODAY, g) == "2026-09-01"
    _ingest(s, tmp_path, "stray.RMT", _plb_only("20261120", "000000901"))
    assert rf.feed_date(s.conn, TODAY, g) == "2026-09-01"
    _ingest(s, tmp_path, "future.RMT", _plb_only("20991231", "000000902", payer="TEST VENDOR", payer_id="99999"))
    assert rf.feed_date(s.conn, TODAY, g) == "2026-09-01"
    _ingest(s, tmp_path, "bundle.RMT", _bundle("20261120", 903))
    cov = rf.coverage(s.conn, TODAY, g)
    assert rf.feed_date(s.conn, TODAY, g) == "2026-09-01"
    assert not rf.judgeable(cov, g("ANTHEM"), "2026-09-21", "2026-11-05"), "the gap's claims stay 'cannot check', not 'unanswered'"
    for k, day in enumerate(("20261127", "20261204")):       # the feed really resumes: two more weeks, every payer
        _ingest(s, tmp_path, f"resume{k}.RMT", _bundle(day, 910 + 3 * k))
    cov = rf.coverage(s.conn, "2026-12-07", g)
    assert rf.feed_date(s.conn, "2026-12-07", g) == "2026-12-04"
    assert not rf.judgeable(cov, g("ANTHEM"), "2026-09-21", "2026-11-05"), "a claim whose wait falls in the hole is never judged"
    assert rf.judgeable(cov, g("ANTHEM"), "2026-07-20", "2026-08-24")


def test_hotfix2_rule_still_bridges_a_gap_with_one_day_of_files(tmp_path):
    """H8's volume-weighted median of LATEST dates is safe against one payer's stray file and a future date, not against one
    day of every payer's files after a gap (a clearinghouse bundle): it says the feed is current through that day."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import reference_feed as rf
    s = _weekly_store(tmp_path)
    g = _group()
    _ingest(s, tmp_path, "stray.RMT", _plb_only("20261120", "000000921"))
    assert rf.h8_feed_date(s.conn, TODAY, g) == "2026-09-01"          # holds
    _ingest(s, tmp_path, "bundle.RMT", _bundle("20261120", 922))
    assert rf.h8_feed_date(s.conn, TODAY, g) == "2026-09-01", "H8's feed date jumped across the six-week hole"
