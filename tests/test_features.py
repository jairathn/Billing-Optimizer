"""
Tests for detection 2.0, alerts, the pipeline, the breakdown and progress endpoints and the note-based appeal builder.
Everything here is synthetic: invented claim numbers, names and amounts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import notes  # noqa: E402
from remitwatch.alerts import AlertEngine, poisson_sf, run_alerts  # noqa: E402
from remitwatch.appeals import build_packet  # noqa: E402
from remitwatch.codes import appeal_rule, pos_class  # noqa: E402
from remitwatch.detect import Detector, ensure_schema  # noqa: E402
from remitwatch.pipeline import LAST_KEY, LOCK_KEY, acquire_lock, release_lock, run_cycle  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from remitwatch.x12_837 import DDL as DDL_837  # noqa: E402
from remitwatch.x12_837 import parse_837_bytes  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

NPI = "1888888882"


def claim(i: int, lines: str, status: str = "1", cfi: str = "12", pos: str = "11", dos: str = "20260401", plan: str = "PPO") -> str:
    """One synthetic CLP loop. `lines` is the SVC part; the claim totals are not checked by these tests."""
    return (f"CLP*CB{i:07d}C017*{status}*500.00*100.00*0.00*{cfi}*PC{i}*{pos}~NM1*QC*1*SAMPLE*PAT****MI*M{i:08d}~"
            f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~REF*CE*{plan}~DTM*232*{dos}~{lines}")


def svc(code: str, charge: str, paid: str, allowed: str | None, cas: str = "", dos: str = "20260401", orig: str = "", mods: str = "") -> str:
    out = f"SVC*HC:{code}{':' + mods if mods else ''}*{charge}*{paid}**1{'*HC:' + orig if orig else ''}~DTM*472*{dos}~{cas}"
    return out + (f"AMT*B6*{allowed}~" if allowed else "")


def build_store(tmp_path, claims: list[str], pay_date: str = "20260415", payer: str = "ANTHEM") -> Store:
    store = Store(str(tmp_path / "s.sqlite"))
    paths = []
    for n, chunk in enumerate([claims[i:i + 20] for i in range(0, len(claims), 20)]):
        p = tmp_path / f"r{len(list(tmp_path.glob('*.RMT'))):04d}.RMT"
        p.write_bytes(remit("".join(chunk), "100.00", payer=payer, seg_count=99, isa_ctrl=f"{len(list(tmp_path.glob('*.RMT'))) + 1:09d}", pay_date=pay_date))
        paths.append(str(p))
    store.ingest_835_paths(paths)
    return store


def clean_visits(start: int, n: int, code: str, charge: str, allowed: str, **kw) -> list[str]:
    co = f"{float(charge) - float(allowed):.2f}"
    return [claim(start + i, svc(code, charge, allowed, allowed, f"CAS*CO*45*{co}~"), **kw) for i in range(n)]


# --------------------------------------------------------------------------- reference rules

def test_appeal_windows_and_place_of_service():
    assert appeal_rule("Anthem / BCBS", "commercial")["days"] == 730 and appeal_rule("Anthem / BCBS", "commercial")["verified"]
    assert appeal_rule("Anthem / BCBS Medicare Advantage", "medicare_advantage")["days"] == 120
    assert appeal_rule("Anthem / BCBS Medicaid", "medicaid")["days"] == 90
    assert appeal_rule("Anthem / BCBS", "commercial", "FEDERAL EMPLOYEE PROGRAM")["days"] == 180
    assert appeal_rule("UMR", "commercial")["days"] == 365
    assert appeal_rule("Medicare KY (CGS J15)", "medicare")["days"] == 120
    assert appeal_rule("Some Small Plan", "commercial")["days"] == 180
    assert pos_class("24") == "facility" and pos_class("11") == "office" and pos_class(None) == "office"


def test_837_claim_date_comes_from_the_lines():
    data = (
        "ISA*00*          *00*          *ZZ*999999999      *ZZ*TRIZETTO       *261005*1200*^*00501*000000101*0*P*:~GS*HC*999999999*TRIZETTO*20261005*1200*101*X*005010X222A1~"
        "ST*837*0001*005010X222A1~BHT*0019*00*123*20261005*1200*CH~HL*1**20*1~NM1*85*2*TEST DERMATOLOGY*****XX*1777777772~"
        "HL*2*1*22*0~SBR*P*18*GRP1******CI~NM1*IL*1*SAMPLE*PAT****MI*M00000001~NM1*PR*2*ANTHEM*****PI*00660~"
        "CLM*CB0000001C017*223.65***11:B:1*Y*A*Y*Y~LX*1~SV1*HC:99214*223.65*UN*1***1~DTP*472*D8*20261002~LX*2~SV1*HC:17110*177.56*UN*1***1~DTP*472*D8*20261001~SE*14*0001~GE*1*101~IEA*1*000000101~"
    ).encode()
    r = parse_837_bytes(data)
    assert r["claims"][0]["service_date"] == "2026-10-01"       # earliest line date


# --------------------------------------------------------------------------- detection 2.0

def test_level_of_care_reduction_keeps_the_code_and_facility_rate_is_not_underpaid(tmp_path):
    claims = clean_visits(1, 25, "99214", "223.65", "127.80", cfi="16") + clean_visits(100, 25, "99213", "156.66", "76.09", cfi="16")
    # the Anthem Medicare Advantage pattern: billed code kept, CO-186 takes the difference, allowed equals the 99213 rate
    claims.append(claim(200, svc("99214", "223.65", "76.09", "76.09", "CAS*CO*186*51.71~CAS*CO*45*95.85~"), cfi="16"))
    # a facility-setting visit paid at the lower facility rate with no adjustment reason: not an underpayment
    claims.append(claim(201, svc("99214", "223.65", "61.26", "61.26", "CAS*CO*45*162.39~"), cfi="16", pos="24"))
    # an office visit quietly allowed below the usual rate: still an underpayment
    claims.append(claim(202, svc("99214", "223.65", "110.00", "110.00", "CAS*CO*45*113.65~"), cfi="16"))
    store = build_store(tmp_path, claims)
    summary = Detector(store, run_id="t").run()
    assert summary.get("DOWNCODE_LEVEL_REDUCED") == 1
    f = store.query("SELECT * FROM findings WHERE signal='DOWNCODE_LEVEL_REDUCED'")[0]
    assert f["procedure_code"] == "99214" and f["shortfall_cents"] == 5171 and f["expected_cents"] == 12780
    assert f["code_pair"] == "99214 at 99213 rate" and f["level_drop"] == 1 and f["carc"] == "186"
    assert f["payer_group"] == "Anthem / BCBS Medicare Advantage" and f["plan"] == "PPO" and f["pos_class"] == "office"
    assert "120 days" in f["deadline_basis"] and f["appeal_deadline"] == "2026-08-13"
    under = store.query("SELECT patient_control_number, pos_class FROM findings WHERE signal='UNDERPAID_VS_OWN_RATE'")
    assert [u["patient_control_number"] for u in under] == ["CB0000202C017"]
    rates = store.query("SELECT pos_class, mode_cents FROM payer_rates WHERE code='99214'")
    assert {(r["pos_class"], r["mode_cents"]) for r in rates} == {("office", 12780)}      # one facility line is not a baseline
    exp = store.query("SELECT SUM(lines) n FROM exposure WHERE code='99214'")[0]["n"]
    assert exp == 28


def test_denied_visit_with_a_lower_level_paid_in_its_place_is_a_line_swap(tmp_path):
    claims = clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(100, 25, "99213", "156.66", "76.15")
    claims.append(claim(200, svc("99214", "223.65", "0.00", None, "CAS*CO*186*223.65~") + svc("99213", "156.66", "76.15", "76.15", "CAS*CO*45*80.51~")))
    claims.append(claim(201, svc("99214", "223.65", "0.00", None, "CAS*CO*150*223.65~")))
    claims.append(claim(202, svc("17311", "900.00", "0.00", None, "CAS*CO*150*900.00~")))
    store = build_store(tmp_path, claims)
    summary = Detector(store, run_id="t").run()
    assert summary.get("DOWNCODE_LINE_SWAP") == 1 and summary.get("DOWNCODE_LEVEL_DENIED") == 1 and summary.get("DENIED_LEVEL_OF_SERVICE") == 1
    swap = store.query("SELECT * FROM findings WHERE signal='DOWNCODE_LINE_SWAP'")[0]
    assert swap["expected_cents"] == 11760 and swap["shortfall_cents"] == 11760 - 7615
    denied = store.query("SELECT * FROM findings WHERE signal='DOWNCODE_LEVEL_DENIED'")[0]
    assert denied["code_pair"] == "99214 denied for level" and "two years" in denied["deadline_basis"] and denied["appeal_deadline"] == "2028-04-14"


def test_claim_file_shows_a_higher_level_than_the_remit(tmp_path):
    claims = clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(100, 25, "99213", "156.66", "76.15")
    claims.append(claim(200, svc("99213", "156.66", "76.15", "76.15", "CAS*CO*45*80.51~")))     # looks billed and paid as level 3
    store = build_store(tmp_path, claims)
    store.conn.executescript(DDL_837)
    store.conn.execute("INSERT INTO lines_837 (line_837_key, claim_837_key, patient_control_number, line_seq, procedure_code, charge_cents, units, service_date) "
                       "VALUES ('k1','c1','CB0000200C017',1,'99214',22365,1,'2026-04-01')")
    store.conn.commit()
    summary = Detector(store, run_id="t").run()
    assert summary.get("DOWNCODE_VS_BILLED") == 1
    f = store.query("SELECT * FROM findings WHERE signal='DOWNCODE_VS_BILLED'")[0]
    assert f["billed_code"] == "99214" and f["code_pair"] == "99214→99213" and f["shortfall_cents"] == 11760 - 7615
    assert json.loads(f["evidence"])["billed_source"] == "837 claim file"


def test_work_recorded_by_people_survives_reruns_and_later_payment_is_logged(tmp_path):
    claims = clean_visits(1, 25, "99214", "223.65", "117.60")
    claims.append(claim(200, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~")))
    store = build_store(tmp_path, claims)
    Detector(store, run_id="r1").run()
    f = store.query("SELECT finding_id, status FROM findings WHERE signal='DENIED_MEDICAL_NECESSITY'")[0]
    store.conn.execute("UPDATE findings SET status='appealed', appealed_at='2026-04-20T00:00:00+00:00', notes='sent by fax' WHERE finding_id=?", (f["finding_id"],))
    # a finding the rules will no longer produce, but that someone is working
    store.conn.execute("""INSERT INTO findings (finding_id, signal, category, status, shortfall_cents, payer_group) VALUES ('legacy1','OLD_SIGNAL','denial','in_progress',5000,'Anthem / BCBS')""")
    store.conn.commit()
    Detector(store, run_id="r2").run()
    again = store.query("SELECT * FROM findings WHERE finding_id=?", (f["finding_id"],))[0]
    assert again["status"] == "appealed" and again["appealed_at"].startswith("2026-04-20") and again["notes"] == "sent by fax"
    legacy = store.query("SELECT status, stale FROM findings WHERE finding_id='legacy1'")[0]
    assert legacy["status"] == "in_progress" and legacy["stale"] == 1
    # the appeal works: a later remittance pays the line
    p = tmp_path / "later.RMT"
    p.write_bytes(remit(claim(200, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")), "117.60", seg_count=30, isa_ctrl="000000777", pay_date="20260601"))
    store.ingest_835_paths([str(p)])
    Detector(store, run_id="r3").run()
    won = store.query("SELECT * FROM findings WHERE finding_id=?", (f["finding_id"],))[0]
    assert won["status"] == "resolved" and won["recovered_cents"] == 11760 and won["recovered_date"] == "2026-06-01" and won["appealed_at"]
    assert won["notes"] == "sent by fax" and "later remittance" in won["auto_note"] and won["prev_status"] == "appealed"
    ev = store.query("SELECT kind, from_status, to_status, amount_cents FROM finding_events WHERE finding_id=?", (f["finding_id"],))
    assert [(e["kind"], e["from_status"], e["to_status"], e["amount_cents"]) for e in ev] == [("paid_on_later_remit", "appealed", "resolved", 11760)]
    assert store.query("SELECT COUNT(DISTINCT run_id) n FROM progress_snapshots")[0]["n"] == 3
    # the paying remittance is withdrawn: the finding goes back to exactly where the biller left it
    store.conn.execute("UPDATE transactions SET superseded=1 WHERE payment_date='2026-06-01'")
    store.conn.commit()
    Detector(store, run_id="r4").run()
    back = store.query("SELECT * FROM findings WHERE finding_id=?", (f["finding_id"],))[0]
    assert back["status"] == "appealed" and back["notes"] == "sent by fax" and back["auto_note"] is None and back["recovered_cents"] is None


def test_work_follows_the_line_when_new_data_changes_the_signal(tmp_path):
    claims = clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(100, 25, "99213", "156.66", "76.15")
    # two level 3 lines charged at the level 4 fee: the charge fingerprint calls them downcodes
    claims += [claim(200 + i, svc("99213", "223.65", "76.15", "76.15", "CAS*CO*45*147.50~")) for i in range(2)]
    store = build_store(tmp_path, claims)
    assert Detector(store, run_id="r1").run().get("DOWNCODE_CHARGE") == 2
    a, b = [r["finding_id"] for r in store.query("SELECT finding_id FROM findings WHERE signal='DOWNCODE_CHARGE' ORDER BY patient_control_number")]
    store.conn.execute("UPDATE findings SET status='appealed', appealed_at='2026-04-20T00:00:00+00:00' WHERE finding_id=?", (a,))
    store.conn.execute("UPDATE findings SET status='dismissed' WHERE finding_id=?", (b,))
    # the claim files arrive and prove what was billed: the same two lines now carry a different signal and id
    store.conn.executescript(DDL_837)
    store.conn.executemany("INSERT INTO lines_837 (line_837_key, claim_837_key, patient_control_number, line_seq, procedure_code, charge_cents, units, service_date) VALUES (?,?,?,?,?,?,?,?)",
                           [(f"k{i}", f"c{i}", f"CB{200 + i:07d}C017", 1, "99214", 22365, 1, "2026-04-01") for i in range(2)])
    store.conn.commit()
    assert Detector(store, run_id="r2").run().get("DOWNCODE_VS_BILLED") == 2
    rows = store.query("SELECT signal, status, stale, appealed_at FROM findings WHERE category='downcoding' ORDER BY patient_control_number")
    assert [(r["signal"], r["status"], r["stale"]) for r in rows] == [("DOWNCODE_VS_BILLED", "appealed", 0), ("DOWNCODE_VS_BILLED", "dismissed", 0)]
    assert rows[0]["appealed_at"].startswith("2026-04-20")


def test_no_double_counting_for_swaps_secondary_remits_and_reprocessed_reductions(tmp_path):
    claims = clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(100, 25, "99213", "156.66", "76.15")
    # line swap whose replacement line also carries the level 4 charge and the original code
    claims.append(claim(200, svc("99214", "223.65", "0.00", None, "CAS*CO*150*223.65~") + svc("99213", "223.65", "76.15", "76.15", "CAS*CO*45*147.50~", orig="99214")))
    # a primary downcode and the supplement plan echoing the same code change on its own remittance
    claims.append(claim(201, svc("99213", "223.65", "76.15", "76.15", "CAS*CO*45*147.50~", orig="99214")))
    claims.append(claim(201, svc("99213", "223.65", "10.00", "76.15", "CAS*OA*23*66.15~", orig="99214"), status="2"))
    # a level-of-care reduction that the payer reprocessed and paid in full a month later
    claims.append(claim(202, svc("99214", "223.65", "76.15", "76.15", "CAS*CO*186*41.45~CAS*CO*45*106.05~")))
    store = build_store(tmp_path, claims)
    p = tmp_path / "later.RMT"
    p.write_bytes(remit(claim(202, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~")), "117.60", seg_count=30, isa_ctrl="000000888", pay_date="20260520"))
    store.ingest_835_paths([str(p)])
    Detector(store, run_id="t").run()
    by = {}
    for r in store.query("SELECT signal, patient_control_number pcn, status, shortfall_cents FROM findings WHERE category='downcoding'"):
        by.setdefault(r["pcn"], []).append((r["signal"], r["status"], r["shortfall_cents"]))
    assert by["CB0000200C017"] == [("DOWNCODE_LINE_SWAP", "open", 11760 - 7615)]
    assert by["CB0000201C017"] == [("DOWNCODE_SVC06", "open", 11760 - 7615)]
    assert by["CB0000202C017"] == [("DOWNCODE_LEVEL_REDUCED", "resolved", 4145)]


def test_denominators_keep_every_line_when_one_payer_has_two_kinds(tmp_path):
    claims = clean_visits(1, 30, "99214", "223.65", "117.60", cfi="CI") + clean_visits(100, 5, "99214", "223.65", "117.60", cfi="CH")
    store = build_store(tmp_path, claims, payer="HUMANA")
    Detector(store, run_id="t").run()
    assert store.query("SELECT SUM(lines) n FROM exposure")[0]["n"] == 35
    assert store.query("SELECT SUM(lines) n FROM exposure_daily")[0]["n"] == 35
    assert store.query("SELECT SUM(n) n FROM rate_points")[0]["n"] == 35


# --------------------------------------------------------------------------- alerts

def alert_store(tmp_path) -> sqlite3.Connection:
    store = Store(str(tmp_path / "a.sqlite"))
    conn = store.conn
    ensure_schema(conn)
    conn.execute("INSERT INTO transactions (transaction_key, payment_date, superseded) VALUES ('t1', '2026-10-09', 0)")
    conn.execute("INSERT INTO files (sha256, kind, ingested_at) VALUES ('f1', '835', '2026-10-09T00:00:00+00:00')")
    conn.execute("INSERT INTO providers (npi, name, tier, lines) VALUES ('1111111111', 'TARGET PA', 'nonphysician', 100), ('2222222222', 'OTHER MD', 'physician', 100)")
    conn.commit()
    return conn


_SEQ = [0]


def add_findings(conn, n, day, payer="Anthem / BCBS", signal="DOWNCODE_SVC06", npi="2222222222", cents=2500, status="open", deadline=None):
    base = _SEQ[0]
    _SEQ[0] += n
    conn.executemany("""INSERT INTO findings (finding_id, signal, category, status, payment_date, month, payer_group, shortfall_cents, rendering_provider_npi,
                        billed_code, line_key, actionable, appeal_deadline) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     [(f"f{base + i}", signal, "downcoding", status, day, day[:7], payer, cents, npi, "99214", f"l{base + i}", "appeal", deadline) for i in range(n)])


def add_exposure(conn, day, lines, payer="Anthem / BCBS", npi="2222222222", scope="em4"):
    conn.execute("INSERT OR REPLACE INTO exposure_daily VALUES (?,?,?,?,?,?)", (day, payer, "commercial", npi, scope, lines))


def test_poisson_tail():
    assert poisson_sf(0, 3.0) == 1.0 and poisson_sf(5, 0) == 0.0
    assert abs(poisson_sf(1, 1.0) - (1 - 2.718281828 ** -1)) < 1e-6
    assert poisson_sf(8, 0.3) < 1e-6


def test_alerts_ramp_resume_clear_and_return(tmp_path):
    conn = alert_store(tmp_path)
    # baseline six months: 2 downcodes in 1,000 visits; last four weeks: 8 in 150
    add_exposure(conn, "2026-06-15", 1000); add_exposure(conn, "2026-10-01", 150)
    add_findings(conn, 2, "2026-06-15"); add_findings(conn, 8, "2026-10-01")
    # a second payer that was quiet for the whole baseline and has started again
    add_exposure(conn, "2026-06-15", 400, payer="Aetna"); add_exposure(conn, "2026-10-02", 60, payer="Aetna")
    add_findings(conn, 1, "2025-11-01", payer="Aetna"); add_findings(conn, 3, "2026-10-02", payer="Aetna")
    conn.commit()
    out = run_alerts(conn, today="2026-10-10")
    kinds = {(r["kind"], r["payer_group"]) for r in conn.execute("SELECT kind, payer_group FROM alerts WHERE status='new'")}
    assert ("RAMP", "Anthem / BCBS") in kinds and ("RESUMED", "Aetna") in kinds and out["new"] >= 2
    ramp = dict(conn.execute("SELECT * FROM alerts WHERE kind='RAMP'").fetchone())
    assert ramp["n_recent"] == 8 and abs(ramp["metric"] - 8 / 150) < 1e-9 and abs(ramp["baseline"] - 0.002) < 1e-9
    # acknowledged alerts stay acknowledged while the pattern lasts
    conn.execute("UPDATE alerts SET status='acknowledged' WHERE kind='RAMP'"); conn.commit()
    assert run_alerts(conn, today="2026-10-10")["new"] == 0
    assert conn.execute("SELECT status FROM alerts WHERE kind='RAMP'").fetchone()[0] == "acknowledged"
    # the pattern stops: the alert clears itself
    conn.execute("DELETE FROM findings WHERE payment_date='2026-10-01'"); conn.commit()
    run_alerts(conn, today="2026-10-10")
    assert conn.execute("SELECT status FROM alerts WHERE kind='RAMP'").fetchone()[0] == "cleared"
    # and when it comes back it is raised as new again
    add_findings(conn, 9, "2026-10-05"); conn.commit()
    run_alerts(conn, today="2026-10-10")
    again = conn.execute("SELECT status, notified_at FROM alerts WHERE kind='RAMP'").fetchone()
    assert again[0] == "new" and again[1] is None


def test_alerts_provider_singled_out_rate_cut_deadline_and_stale_feed(tmp_path):
    conn = alert_store(tmp_path)
    add_exposure(conn, "2026-09-01", 20, npi="1111111111"); add_exposure(conn, "2026-09-01", 300, npi="2222222222")
    add_findings(conn, 6, "2026-09-01", npi="1111111111"); add_findings(conn, 1, "2026-09-01", npi="2222222222")
    # usual allowed for 99213 was $76.64 for six months and is $70.01 in the last 60 days
    conn.execute("INSERT INTO rate_points VALUES ('2026-05-01','UHC Medicare Advantage','medicare_advantage','99213','physician','office',7664,60)")
    conn.execute("INSERT INTO rate_points VALUES ('2026-09-15','UHC Medicare Advantage','medicare_advantage','99213','physician','office',7001,30)")
    add_findings(conn, 4, "2026-07-01", payer="Humana", signal="DENIED_AUTH", cents=40000, deadline="2026-10-25")
    conn.commit()
    e = AlertEngine(conn, today="2026-10-10")
    active = {a["kind"]: a for a in e.evaluate().values()}
    assert "TARGET PA" in active["PROVIDER_TARGETED"]["title"] and "6 of 20" in active["PROVIDER_TARGETED"]["title"]
    assert "99213 $76.64 to $70.01" in active["RATE_CUT"]["title"] and active["RATE_CUT"]["payer_group"] == "UHC Medicare Advantage"
    assert active["DEADLINE"]["n_recent"] == 4 and active["DEADLINE"]["cents_recent"] == 160000
    assert "STALE_FEED" not in active                          # newest payment is yesterday
    late = AlertEngine(conn, today="2026-11-12").evaluate()
    assert any(a["kind"] == "STALE_FEED" for a in late.values())


# --------------------------------------------------------------------------- pipeline

def test_cycle_ingests_detects_alerts_and_releases_the_lock(tmp_path, monkeypatch):
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "ALERT_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    claims = clean_visits(1, 20, "99214", "223.65", "117.60") + [claim(200, svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))]
    (inbox / "a.RMT").write_bytes(remit("".join(claims), "100.00", seg_count=99))
    db = str(tmp_path / "p.sqlite")
    out = run_cycle(db, sync=True, local=str(inbox), email=False, report=True, report_dir=str(tmp_path / "reports"))
    assert out["sync"] == {"skipped": "Dropbox credentials are not configured"}
    assert out["ingest"]["remits"] == 1 and out["detect"]["findings"] >= 1 and "active" in out["alerts"] and os.path.exists(out["report"])
    store = Store(db)
    assert store.conn.execute("SELECT value FROM meta WHERE key=?", (LOCK_KEY,)).fetchone() is None
    assert json.loads(store.conn.execute("SELECT value FROM meta WHERE key=?", (LAST_KEY,)).fetchone()[0])["finished"]
    report = open(out["report"]).read()
    assert "## Alerts" in report and "## Scoreboard" in report
    # nothing new: no second detection run, and a cycle cannot start while another holds the lock
    again = run_cycle(db, sync=False, local=str(inbox), email=False, report=False)
    assert "detect" not in again and again["ingest"]["remits"] == 0
    token = acquire_lock(store.conn)
    assert token and acquire_lock(store.conn) is None
    assert run_cycle(db, sync=False, email=False) == {"skipped": "another cycle is running"}
    assert release_lock(store.conn, "someone-else") is False            # only the cycle that took the lock can release it
    assert store.conn.execute("SELECT value FROM meta WHERE key=?", (LOCK_KEY,)).fetchone() is not None
    assert release_lock(store.conn, token) is True
    # a lock whose heartbeat stopped three hours ago was left by a crash and is taken over
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (LOCK_KEY, json.dumps({"started": "2020-01-01T00:00:00+00:00", "heartbeat": "2020-01-01T00:00:00+00:00", "pid": 1, "token": "old"})))
    store.conn.commit()
    assert acquire_lock(store.conn)
    release_lock(store.conn)
    store.close()


# --------------------------------------------------------------------------- API

def test_breakdown_progress_alerts_status_and_webhook(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    claims = clean_visits(1, 25, "99214", "223.65", "127.80", cfi="16") + clean_visits(100, 25, "99213", "156.66", "76.09", cfi="16")
    claims += [claim(200 + i, svc("99214", "223.65", "76.09", "76.09", "CAS*CO*186*51.71~CAS*CO*45*95.85~"), cfi="16") for i in range(3)]
    store = build_store(tmp_path, claims)
    Detector(store, run_id="t").run()
    run_alerts(store.conn, today="2026-04-16")
    store.close()
    import remitwatch.api as api_mod
    import remitwatch.pipeline as pipe

    monkeypatch.setattr(api_mod, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    poked = []
    monkeypatch.setattr(pipe, "run_once_in_background", lambda db, trigger="button": poked.append(trigger))
    monkeypatch.setenv("DROPBOX_APP_SECRET", "s3cret")
    c = TestClient(api_mod.app)
    xhr = {"X-Requested-With": "remitwatch"}

    b = c.get("/api/breakdown?dim=provider&family=downcoding").json()
    row = b["rows"][0]
    assert row["k1"] == NPI and row["label1"] == "TEST PROVIDER" and row["n"] == 3 and row["cents"] == 3 * 5171
    assert row["lines"] == 53 and abs(row["rate"] - 3 / 53) < 1e-4 and b["total_lines"] == 53       # 28 level 4 visits and 25 level 3 visits billed
    m = c.get("/api/breakdown?dim=payer&dim2=month&family=downcoding").json()
    assert m["rows"][0]["k1"] == "Anthem / BCBS Medicare Advantage" and m["rows"][0]["k2"] == "2026-04" and m["denominator_for"] == 2
    assert c.get("/api/breakdown?dim=nope").status_code == 400
    assert c.get("/api/breakdown?dim=code_pair&family=downcoding").json()["rows"][0]["k1"] == "99214 at 99213 rate"
    listed = c.get("/api/findings?signal=DOWNCODE_LEVEL_REDUCED&code_pair=99214%20at%2099213%20rate&status=active").json()
    assert listed["total"] == 3
    fid = listed["items"][0]["finding_id"]

    # state-changing calls need the header a cross-site form cannot send
    assert c.post(f"/api/findings/{fid}/status", json={"status": "appealed"}).status_code == 400
    assert c.post(f"/api/findings/{fid}/status", json={"status": "appealed"}, headers=xhr).json() == {"ok": True}
    assert c.post(f"/api/findings/{fid}/status", json={"status": "resolved", "recovered_cents": 4000}, headers=xhr).status_code == 200
    assert c.post(f"/api/findings/{fid}/status", json={"status": "bogus"}, headers=xhr).status_code == 400
    d = c.get(f"/api/findings/{fid}").json()
    assert d["finding"]["status"] == "resolved" and d["finding"]["recovered_cents"] == 4000 and d["finding"]["appealed_at"]
    assert [e["to_status"] for e in d["events"]] == ["resolved", "appealed"]
    p = c.get("/api/progress").json()
    st = {r["status"]: r for r in p["pipeline"]}
    assert st["resolved"]["recovered_cents"] == 4000 and st["open"]["n"] == 2 and p["events"][0]["to_status"] == "resolved"

    al = c.get("/api/alerts").json()
    assert isinstance(al["items"], list)
    if al["items"]:
        aid = al["items"][0]["alert_id"]
        assert c.post(f"/api/alerts/{aid}/ack", headers=xhr).json() == {"ok": True}
    s = c.get("/api/sync/status").json()
    assert s["dropbox_configured"] is False and s["newest_payment_date"] == "2026-04-15"
    assert c.post("/api/sync/run", headers=xhr).json() == {"started": True} and poked == ["button"]

    # Dropbox webhook: challenge echo, and only correctly signed notifications start a cycle
    r = c.get("/api/dropbox/webhook?challenge=abc123")
    assert r.text == "abc123" and r.headers["content-type"].startswith("text/plain") and r.headers["x-content-type-options"] == "nosniff"
    body = b'{"list_folder": {"accounts": ["dbid:1"]}}'
    assert c.post("/api/dropbox/webhook", content=body, headers={"X-Dropbox-Signature": "bad"}).status_code == 403
    good = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert c.post("/api/dropbox/webhook", content=body, headers={"X-Dropbox-Signature": good}).status_code == 200
    assert poked == ["button", "webhook"]


# --------------------------------------------------------------------------- note-based appeal builder

NOTE = """Patient: Pat Sample   DOB: 01/02/1950   MRN: 445566   Phone: (270) 555-0100   Member ID: M00000200
Seen 05/02/2026 for psoriasis, worsening, not at treatment goal. New 7 mm pearly papule left forearm.
Plan: increase clobetasol 0.05% ointment to twice daily, start calcipotriene. Shave biopsy of the forearm lesion today.
Total time 32 minutes excluding the biopsy."""

ANALYSIS = {
    "visit_summary": "Established patient with worsening psoriasis and a new lesion.",
    "problems": {"level": "moderate", "rationale": "Chronic illness with exacerbation.", "quotes": ["psoriasis, worsening, not at treatment goal"]},
    "data": {"level": "minimal", "rationale": "None reviewed.", "quotes": []},
    "risk": {"level": "moderate", "rationale": "Prescription drug management.", "quotes": ["increase clobetasol 0.05% ointment to twice daily"]},
    "mdm_level": "moderate", "time_documented": "32 minutes", "supported_code": "99214", "supports_billed": "yes",
    "modifier_25": {"relevant": True, "separately_identifiable": "yes", "rationale": "Separate problem.", "quotes": ["psoriasis, worsening"]},
    "procedure_support": "not_applicable", "strengths": ["two elements moderate"], "gaps": [], "recommendation": "appeal",
    "recommendation_reason": "Two of three elements are moderate.",
    "clinical_paragraphs": ["The problems addressed were of moderate complexity. The note records \"psoriasis, worsening, not at treatment goal\"."],
    "documentation_tips": [],
}


class FakeClaude:
    """Stands in for anthropic.Anthropic(): records the request and returns a fixed structured answer."""

    def __init__(self, answer: dict, stop_reason: str = "end_turn"):
        self.answer, self.stop_reason, self.calls, self.messages = answer, stop_reason, [], self

    def stream(self, **kw):
        self.calls.append(kw)
        msg = types.SimpleNamespace(stop_reason=self.stop_reason, model="claude-opus-5-5",
                                    content=[types.SimpleNamespace(type="text", text=json.dumps(self.answer))])

        class Ctx:
            def __enter__(self_inner):
                return types.SimpleNamespace(get_final_message=lambda: msg)

            def __exit__(self_inner, *a):
                return False
        return Ctx()


def detail_fixture() -> tuple[dict, dict]:
    finding = {"finding_id": "synthetic1", "signal": "DOWNCODE_SVC06", "procedure_code": "99213", "original_procedure_code": "99214", "billed_code": "99214",
               "payer_group": "Anthem / BCBS", "payer_kind": "commercial", "payer_name": "ANTHEM", "carc": "186", "carc_text": "Level of care change adjustment",
               "shortfall_cents": 1516, "expected_cents": 7516, "allowed_cents": 6000, "service_date": "2026-05-02", "payment_date": "2026-05-20",
               "patient_control_number": "CB0000200C017", "rendering_provider_npi": NPI, "evidence": {}, "title": "Billed 99214, paid as 99213",
               "appeal_deadline": "2028-05-19", "line_key": "L1"}
    detail = {"finding": finding, "claim": {"payer_claim_control_number": "PC200"}, "remit": {"payer_name": "ANTHEM", "trace_number": "TR1", "payment_date": "2026-05-20"},
              "lines": [{"line_key": "L1", "procedure_code": "99213", "modifiers": '["25"]', "original_procedure_code": "99214", "charge_cents": 22365,
                         "allowed_cents": 6000, "paid_cents": 1000}], "adjustments": [], "remarks": []}
    return detail, {"patient_last": "Sample", "patient_first": "Pat", "patient_id": "M00000200"}


def test_scrub_removes_known_and_patterned_identifiers():
    out = notes.scrub(NOTE, ["Sample", "Pat", "M00000200", "CB0000200C017"])
    for secret in ("Pat Sample", "Sample", "445566", "555-0100", "01/02/1950", "M00000200", "05/02/2026"):
        assert secret not in out
    assert "psoriasis, worsening" in out and "32 minutes" in out
    hard = notes.scrub("Ng, Li seen. ID R 000001 also R000-001. SSN 123 45 6789, phone 5025551212. Level 12 ng/mL. Paid $1234567890.00",
                       ["Ng", "Li", "R000001"])
    for secret in ("Ng,", "Li ", "000001", "R000-001", "123 45 6789", "5025551212"):
        assert secret not in hard
    assert "ng/mL" in hard and "$1234567890.00" in hard
    ids = notes.identifiers_for({"finding": {"patient_control_number": "CB1"}},
                                {"patient_last": "Sample", "member_id_ref": "MEMB999888", "patient_middle": "Quincy", "other_refs": '{"SY": "123456789", "NM1_GB": "OTHERSUB"}'})
    assert {"MEMB999888", "Quincy", "123456789", "OTHERSUB", "CB1"} <= set(ids)


def test_unreadable_or_hostile_uploads_are_refused_quickly():
    import time

    import pytest
    with pytest.raises(ValueError):
        notes.extract_text("note.docx", b"PK\x03\x04 not a real archive")
    t0 = time.time()
    text, kind = notes.extract_text("note.html", ("<html>" + "<script>" * 50000).encode())
    assert kind == "text" and text == "" and time.time() - t0 < 2


def test_packet_from_note_with_review_then_without(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    conn = sqlite3.connect(str(tmp_path / "n.sqlite"))
    detail, raw = detail_fixture()
    fake = FakeClaude(ANALYSIS)
    p = notes.build_from_note(conn, detail, raw, "note.txt", NOTE.encode(), client=fake)
    sent = fake.calls[0]
    assert sent["model"] == "claude-opus-5-5" and sent["output_config"]["format"]["type"] == "json_schema" and "thinking" not in sent
    text = sent["messages"][0]["content"][0]["text"]
    assert "Sample" not in text and "M00000200" not in text and "99214" in text
    assert p["mode"] == "ai" and "psoriasis, worsening, not at treatment goal" in p["letter"] and "[describe" not in p["letter"]
    assert "Claim Payment Reconsideration" in p["letter"] and "KRS 304.17A-730" in p["letter"] and "Indiana Code 27-8-5.7-6.5" in p["letter"]
    # the browser gets placeholders; the stored letter and the PDF carry the name and member number the payer needs
    assert "Sample" not in p["letter"] and "M00000200" not in p["letter"] and "[patient name]" in p["letter"]
    stored = notes.get_packet(conn, p["packet_id"], with_pdf=True)
    assert stored["pdf"][:5] == b"%PDF-" and stored["analysis"]["supported_code"] == "99214" and "Sample" not in stored["letter"]
    full = notes.get_packet(conn, p["packet_id"], reveal=True)["letter"]
    assert "Pat Sample" in full and "Member ID: M00000200" in full
    assert notes.list_packets(conn, "synthetic1")[0]["packet_id"] == p["packet_id"]
    # the review says the note does not carry the level: its arguments never reach the letter
    p2 = notes.build_from_note(conn, detail, raw, "note.txt", NOTE.encode(), client=FakeClaude(dict(ANALYSIS, recommendation="do_not_appeal", supports_billed="no")))
    assert "[describe" in p2["letter"] and "psoriasis, worsening" not in p2["letter"] and p2["analysis"]["recommendation"] == "do_not_appeal"
    # no API key: the packet is still built from the template, and nothing is sent anywhere
    p3 = notes.build_from_note(conn, detail, raw, "", None, pasted_text=NOTE)
    assert p3["mode"] == "template" and "ANTHROPIC_API_KEY" in p3["analysis"]["reason"] and "[describe" in p3["letter"]
    # a refusal or an API failure degrades to the template instead of failing the request
    p4 = notes.build_from_note(conn, detail, raw, "note.txt", NOTE.encode(), client=FakeClaude(ANALYSIS, stop_reason="refusal"))
    assert p4["mode"] == "template" and "failed" in p4["analysis"]["reason"]
    # email needs SMTP and a sane address
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert notes.email_packet(conn, p["packet_id"], "billing@example.org")["sent"] is False
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    assert notes.email_packet(conn, p["packet_id"], "not-an-address")["reason"] == "not a valid email address"
    monkeypatch.setenv("APPEAL_EMAIL_DOMAINS", "practice.example")
    assert "outside the allowed domains" in notes.email_packet(conn, p["packet_id"], "someone@elsewhere.example")["reason"]


def test_letters_for_new_signals_and_statutes():
    detail, _raw = detail_fixture()
    f = dict(detail["finding"], signal="DOWNCODE_LEVEL_REDUCED", procedure_code="99214", original_procedure_code=None, payer_group="Anthem / BCBS Medicare Advantage",
             payer_kind="medicare_advantage", evidence={"paid_like": "99213"})
    letter = build_packet(dict(detail, finding=f))["letter"]
    assert "keeps the code we billed" in letter and "CPT 99213" in letter and "42 CFR 422.101" in letter and "KRS" not in letter
    tb = build_packet({"finding": dict(detail["finding"], signal="TAKEBACK", procedure_code=None, original_procedure_code=None, evidence={"reference": "REF9"},
                                       shortfall_cents=500000)})
    assert "KRS 304.17A-714" in tb["letter"] and "REF9" in tb["letter"]
    fee = build_packet({"finding": dict(detail["finding"], signal="FEE_SKIM", procedure_code=None, original_procedure_code=None)})
    assert "45 CFR 162.1601" in fee["letter"]
    under = build_packet({"finding": dict(detail["finding"], signal="UNDERPAID_VS_OWN_RATE", original_procedure_code=None)})
    assert "KRS 304.17A-708(1)" in under["letter"]


def test_identifiers_stay_on_the_server_when_names_are_hidden(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    denied = ("CLP*CB0000300C017*1*223.65*0.00*0.00*12*PC300*11~NM1*QC*1*SAMPLE*PAT*Q***MI*M00000300~NM1*GB*1*OTHERSUB*ANN~"
              f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~REF*1W*MEMB999888~REF*SY*123456789~DTM*232*20260401~"
              + svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [denied])
    Detector(store, run_id="t").run()
    store.close()
    import remitwatch.api as api_mod

    monkeypatch.setattr(api_mod, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setattr(api_mod, "SHOW_PHI", False)
    c = TestClient(api_mod.app)
    fid = c.get("/api/findings?signal=DENIED_MEDICAL_NECESSITY").json()["items"][0]["finding_id"]
    detail = c.get(f"/api/findings/{fid}")
    for secret in ("SAMPLE", "MEMB999888", "123456789", "OTHERSUB", "M00000300"):
        assert secret not in detail.text
    assert detail.json()["claim"]["patient"] == "PS" and detail.json()["claim"]["payer_claim_control_number"] == "PC300"
    built = c.post("/api/appeals/build", data={"finding_id": fid, "text": "Pat Sample, member MEMB999888. Excision of a 1.2 cm lesion documented."},
                   headers={"X-Requested-With": "remitwatch"})
    assert built.status_code == 200 and built.json()["mode"] == "template"
    for secret in ("SAMPLE", "Pat Sample", "M00000300"):
        assert secret not in built.json()["letter"]
    pid = built.json()["packet_id"]
    assert "Pat Sample" not in c.get(f"/api/packets/{pid}").json()["letter"]
    pdf = c.get(f"/api/packets/{pid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and pdf.headers["cache-control"] == "no-store"
    bad = c.post("/api/appeals/build", data={"finding_id": fid}, files={"file": ("note.docx", b"PK\x03\x04 broken", "application/octet-stream")},
                 headers={"X-Requested-With": "remitwatch"})
    assert bad.status_code == 400 and "Word document" in bad.json()["detail"]
    assert c.post(f"/api/packets/{pid}/email", json={"to": "x@example.org"}, headers={"X-Requested-With": "remitwatch"}).json()["sent"] is False


def patient_store(tmp_path):
    """Six invented patients with one denied visit each, in an order the alphabet would change: a policy holder, a
    dependent on the same member number, two people whose 'member number' is a Social Security number or the old
    Medicare number built on one, one with no number at all, and one whose name a spreadsheet would read as a formula."""
    def denied(i: int, who: str, more: str = "") -> str:
        return (f"CLP*CB{i:07d}C017*1*223.65*0.00*0.00*12*PC{i}*11~NM1*QC*1*{who}~{more}NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
                "DTM*232*20260401~" + svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [
        denied(300, "ORCHARD*LEE*Q***MI*W55500300", "NM1*IL*1*HOLDER*IVY****MI*SUB7770001~NM1*74*1*ORCHARDE*LEA~REF*1W*MEMB999888~"),
        denied(301, "=BIRCH*ANN*Q***MI*W55500301", "NM1*IL*1*=BIRCH*ANN****MI*W55500301~"),        # insured in their own right
        denied(302, "ORCHARD*KIM*J", "NM1*IL*1*ORCHARD*LEE****MI*W55500300~"),
        denied(303, "YEW*IVY*Q***34*999001234"),
        denied(304, "YEW*OTTO*Q***HN*999001234A"),
        denied(305, "ELM*BO*Q")])
    Detector(store, run_id="t").run()
    store.close()


URL = "/api/findings?signal=DENIED_MEDICAL_NECESSITY"
CSV_URL = "/api/findings.csv?signal=DENIED_MEDICAL_NECESSITY"
PATIENT_SECRETS = ("ORCHARD", "BIRCH", "LEE", "KIM", "YEW", "OTTO", "ELM", "W5550030", "HOLDER", "IVY", "SUB7770001", "ORCHARDE", "MEMB999888", "999001234")


def test_names_hidden_means_initials_on_screen_and_nothing_else(tmp_path, monkeypatch):
    import csv
    import io
    from fastapi.testclient import TestClient

    patient_store(tmp_path)
    import remitwatch.api as api_mod

    monkeypatch.setattr(api_mod, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setattr(api_mod, "SHOW_PHI", False)
    ran: list[str] = []
    real_db = api_mod.db

    def traced():
        conn = real_db()
        conn.set_trace_callback(ran.append)
        return conn
    monkeypatch.setattr(api_mod, "db", traced)
    c = TestClient(api_mod.app)
    cols = {k.strip() for k in api_mod.FINDING_COLS.split(",")}

    # every key of every row: the finding and the initials, never a number; the export carries no patient column at all
    items = c.get(URL).json()["items"]
    assert [set(i) - cols for i in items] == [{"patient"}] * 6
    assert sorted(i["patient"] for i in items) == ["A=", "BE", "IY", "KO", "LO", "OY"]
    export = c.get(CSV_URL).text
    assert set(export.splitlines()[0].split(",")) == cols and len(export.splitlines()) == 7
    for page in (c.get(URL).text, export):
        for secret in PATIENT_SECRETS:
            assert secret not in page, secret

    # no spelling of a name or number finds anything, in the address or in the header the page uses, and such a
    # search never reads the claims table (so it cannot be timed either)
    for q in ("ORCHARD", "orchard", "LEE", "ORCHARD, LEE", "ORCHARD, LEE Q", "lee orchard", "orch%", "_RCHARD", "W55500300", "W555", "5500300",
              "W555003__", "W555-00300", "HOLDER", "SUB7770001", "MEMB999888", "ORCHARDE", "999001234", "LO"):
        for ask in ({"params": {"q": q}}, {"headers": {"X-Search": q.replace("%", "%25").replace(" ", "%20").replace(",", "%2C")}}):
            ran.clear()
            assert c.get(URL, **ask).json()["total"] == 0, q
            assert not [s for s in ran if any(k in s for k in (" claims", "patient_last", "patient_first", "patient_middle", "patient_id", "insured_"))], q
            assert c.get(CSV_URL, **ask).text == "", q
    assert c.get(URL, params={"q": "CB0000300"}).json()["total"] == 1                      # the claim number is still searchable
    assert c.get(URL, headers={"X-Search": "CB0000300%20"}).json()["total"] == 1           # and a stray space does not lose it

    # nothing says which findings belong to one person, and no sort key reaches the name
    assert c.get(URL, params={"patient_of": items[0]["claim_key"]}).json()["total"] == 0
    for sort in ("patient", "name", "patient_last", "member_id"):
        assert [i["finding_id"] for i in c.get(URL, params={"sort": sort}).json()["items"]] == [i["finding_id"] for i in items], sort
    assert [r["finding_id"] for r in csv.DictReader(io.StringIO(export))] == [i["finding_id"] for i in items]

    # the page size has a floor as well as a ceiling
    assert [c.get(URL, params=p).status_code for p in ({"limit": -1}, {"limit": 0}, {"limit": 2001}, {"offset": -1})] == [422] * 4
    assert c.get(URL, params={"q": "x" * 60000}).status_code == 200


def test_names_shown_says_who_and_never_shows_a_social_security_number(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    patient_store(tmp_path)
    import remitwatch.api as api_mod

    monkeypatch.setattr(api_mod, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setattr(api_mod, "SHOW_PHI", True)
    c = TestClient(api_mod.app)
    listing = c.get(URL)
    shown = {i["patient_control_number"][:9]: i for i in listing.json()["items"]}
    who = lambda n: (shown[f"CB0000{n}"]["patient"], shown[f"CB0000{n}"]["member_id"])
    assert who(300) == ("ORCHARD, LEE Q", "W55500300") and who(301) == ("=BIRCH, ANN Q", "W55500301")
    assert who(302) == ("ORCHARD, KIM J", "W55500300")              # a dependent: the policy holder's number
    assert who(303) == ("YEW, IVY Q", "") and who(304) == ("YEW, OTTO Q", "") and who(305) == ("ELM, BO Q", "")

    # found by name in any order, as the row prints it, and by member number as a card prints it
    found = lambda q, **kw: c.get(URL, headers={"X-Search": q.replace("%", "%25").replace(" ", "%20").replace(",", "%2C")}, **kw).json()["total"]
    assert [found(q) for q in ("orchard", "lee orchard", "Orchard, L", "orchard,lee", "ORCHARD, LEE Q", "ORCHARD, KIM J", "yew", "ELM, BO Q")] == [2, 1, 1, 1, 1, 1, 2, 1]
    assert [found(q) for q in ("W55500300", "5500300", "W555-00300", "W555 00300", "W55500301")] == [2, 2, 2, 2, 1]
    assert [found(q) for q in ("birch orchard", "orchard zed", "W99999999", "MEMB999888")] == [0] * 4
    assert found("SUB7770001") == 1                                 # the policy holder's number finds the claim as well
    assert c.get(URL, params={"q": "orchard"}).json()["total"] == 2 and found("CB0000300 ") == 1 and found("99214") == 6

    # a Social Security number, or the old Medicare number built on one, is not a member number: not shown, exported or searchable
    export = c.get(CSV_URL).text
    detail = lambda n: c.get(f"/api/findings/{shown[f'CB0000{n}']['finding_id']}")
    for page in (listing.text, export, detail(303).text, detail(304).text):
        assert "999001234" not in page
    assert found("999001234") == 0 and found("999-00-1234") == 0

    # a name lists that person only: not the rest of a family on the same number, and it works without a number
    one_patient = lambda n: sorted(i["patient"] for i in c.get(URL, params={"patient_of": shown[f"CB0000{n}"]["claim_key"]}).json()["items"])
    assert one_patient(300) == ["ORCHARD, LEE Q"] and one_patient(302) == ["ORCHARD, KIM J"]
    assert one_patient(305) == ["ELM, BO Q"] and one_patient(303) == ["YEW, IVY Q"]
    assert c.get(URL, params={"patient_of": "no-such-claim"}).json()["total"] == 0

    # the detail view: who, their number, the insured person when that is someone else, and none of the rest of the claim loop
    d300 = detail(300).json()["claim"]
    assert (d300["patient"], d300["member_id"], d300["insured"]) == ("ORCHARD, LEE Q", "W55500300", "HOLDER, IVY")
    assert set(d300) == set(api_mod.CLAIM_PUBLIC_COLS) | {"patient", "member_id", "insured"}
    for secret in ("MEMB999888", "ORCHARDE", "SUB7770001"):
        assert secret not in detail(300).text
    d302 = detail(302).json()["claim"]
    assert (d302["patient"], d302["member_id"], d302["insured"]) == ("ORCHARD, KIM J", "W55500300", "ORCHARD, LEE")
    assert "insured" not in detail(301).json()["claim"]             # the patient is not listed a second time as the insured

    # the export: the same two columns beside the claim number, and a name must not run as a formula in a spreadsheet
    lines = export.splitlines()
    head = lines[0].split(",")
    at = head.index("patient_control_number")
    assert head[at + 1:at + 3] == ["patient", "member_id"]
    assert any('"ORCHARD, LEE Q",W55500300' in ln for ln in lines)
    assert any('"\'=BIRCH, ANN Q"' in ln for ln in lines) and not any(',"=BIRCH' in ln for ln in lines)


def identifier_store(tmp_path):
    """Invented people whose names and numbers sit close together, and identifiers that are not member numbers."""
    def denied(i: int, who: str, more: str = "") -> str:
        return (f"CLP*CB{i:07d}C017*1*223.65*0.00*0.00*12*PC{i}*11~NM1*QC*1*{who}~{more}NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
                "DTM*232*20260401~" + svc("99214", "223.65", "0.00", None, "CAS*CO*50*223.65~"))
    store = build_store(tmp_path, clean_visits(1, 25, "99214", "223.65", "117.60") + [
        denied(400, "ASPEN*DEL*R***MI*ZZA100200"), denied(401, "ASPEN*DEL*R***MI*ZZA100200"),
        denied(402, "ASPEN*DEL*T***MI*ZZA100299"),                  # same name, another number
        denied(403, "ASPEN*FAY****MI*ZZA100200"),                   # same number, another first name
        denied(404, "BIRCH*DEL*R***MI*ZZA100200"),                  # same number and first name, another last name
        denied(405, "FIR*NED****MR*123456789"),                     # nine digits labelled a record number: can be an SSN
        denied(406, "FIR*OLA****MR*98765432101"),
        denied(407, "PINE*UMA", "NM1*IL*1*PINE*VIC****FI*359876543~"),     # the insured's number is a tax ID
        denied(408, "XYLO*DAN****MI*PX1000"), denied(409, "XYLO*DAN****MI*PX10001"),
        denied(410, "MAPLE*ROY*X***MI*BLANKQ4455"),
        denied(411, "SPRUCE*YAN****MI*SP-4411-22"),                 # the payer wrote the number with dashes
        denied(412, "NUÑEZ*JOSÉ****MI*NU5500"),
        denied(413, "ROWAN*WES*C***34*666005555", "NM1*IL*1*ROWAN*XIA****MI*RW8800123~")])
    Detector(store, run_id="t").run()
    store.close()


def test_member_numbers_and_the_name_click_follow_one_rule(tmp_path, monkeypatch):
    import csv
    import io
    from fastapi.testclient import TestClient

    identifier_store(tmp_path)
    import remitwatch.api as api_mod

    monkeypatch.setattr(api_mod, "DB_PATH", str(tmp_path / "s.sqlite"))
    monkeypatch.setattr(api_mod, "PASSWORD", "")
    monkeypatch.setattr(api_mod, "SHOW_PHI", True)
    c = TestClient(api_mod.app)
    items = c.get(URL).json()["items"]
    by = {i["patient_control_number"][2:9].lstrip("0"): i for i in items}
    export = {r["patient_control_number"][2:9].lstrip("0"): r for r in csv.DictReader(io.StringIO(c.get(CSV_URL).text))}
    shown = {"400": "ZZA100200", "402": "ZZA100299", "403": "ZZA100200", "404": "ZZA100200", "405": "", "406": "98765432101", "407": "",
             "408": "PX1000", "409": "PX10001", "410": "BLANKQ4455", "411": "SP-4411-22", "412": "NU5500", "413": "RW8800123"}
    for n, number in shown.items():          # the list, the export and the detail view agree
        detail = c.get(f"/api/findings/{by[n]['finding_id']}").json()["claim"]
        assert (by[n]["member_id"], export[n]["member_id"], detail.get("member_id", "")) == (number, number, number), n

    names = lambda q: sorted({i["patient_control_number"][2:9].lstrip("0") for i in c.get(URL, headers={"X-Search": q}).json()["items"]})
    # numbers that are not member numbers are never found; the ones shown are found as the payer or a card writes them
    assert [names(q) for q in ("123456789", "359876543", "666005555", "666-00-5555")] == [[]] * 4
    assert names("98765432101") == ["406"] and names("RW8800123") == ["413"]
    assert names("SP-4411-22") == names("SP441122") == names("4411%2022") == ["411"]
    assert names("BLANKQ") == [] and names("4455") == ["410"]          # letters alone never match inside a number
    assert names("nu%C3%B1ez") == names("NU%C3%91EZ") == names("jos%C3%A9") == ["412"]
    # an empty search header leaves the search in the address in force
    assert c.get(URL, params={"q": "aspen"}, headers={"X-Search": "%20"}).json()["total"] == 4

    # a name lists that person only: same last name, first name and number, exactly
    click = lambda n: sorted(i["patient_control_number"][2:9].lstrip("0") for i in c.get(URL, params={"patient_of": by[n]["claim_key"]}).json()["items"])
    assert click("400") == ["400", "401"]
    assert [click(n) for n in ("402", "403", "404", "408", "409")] == [["402"], ["403"], ["404"], ["408"], ["409"]]

    # the letter prints the same number, and a placeholder where the payer sent no member number
    letter = lambda n: c.get(f"/api/appeal/{by[n]['finding_id']}").json()["letter"]
    assert "Member ID: [member ID]" in letter("405") and "123456789" not in letter("405")
    assert "Member ID: [member ID]" in letter("407") and "359876543" not in letter("407")
    assert "Member ID: RW8800123" in letter("413") and "666005555" not in letter("413")


def test_a_packet_is_mailed_only_to_named_domains_on_any_host(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid")
    monkeypatch.delenv("APPEAL_EMAIL_DOMAINS", raising=False)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    out = notes.send_pdf(b"%PDF-1.4", "p1", "someone@outside.example")
    assert out["sent"] is False and "APPEAL_EMAIL_DOMAINS" in out["reason"]


def test_slow_climb_is_caught_by_the_quarter_against_the_year(tmp_path):
    conn = alert_store(tmp_path)
    # a year at 3 per 100, then a quarter at about 11 per 100: each month adds a little, so the four-week rule stays quiet
    for day in ("2025-08-15", "2025-10-15", "2025-12-15", "2026-02-15", "2026-04-15", "2026-06-15"):
        add_exposure(conn, day, 100, payer="Anthem / BCBS Medicare Advantage"); add_findings(conn, 3, day, payer="Anthem / BCBS Medicare Advantage")
    for day, n in (("2026-07-20", 5), ("2026-08-20", 5), ("2026-09-20", 6)):
        add_exposure(conn, day, 50, payer="Anthem / BCBS Medicare Advantage"); add_findings(conn, n, day, payer="Anthem / BCBS Medicare Advantage")
    conn.commit()
    active = AlertEngine(conn, today="2026-10-10").evaluate()
    kinds = {a["kind"] for a in active.values() if a["payer_group"] == "Anthem / BCBS Medicare Advantage"}
    assert kinds == {"RISING"}
    rising = next(a for a in active.values() if a["kind"] == "RISING")
    assert rising["n_recent"] == 16 and "over the last three months" in rising["title"] and abs(rising["baseline"] - 0.03) < 1e-9
