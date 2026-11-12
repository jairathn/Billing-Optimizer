"""
Tests run on synthetic, de-identified X12 and xlsx fixtures that mirror the
real TriZetto / ModMed layouts. No real patient data is checked in.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import ema_exports  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.fees import FeeTable, is_downcode  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from remitwatch.x12_835 import cents, parse_835_bytes, split_envelopes  # noqa: E402
from remitwatch.x12_837 import parse_837_bytes  # noqa: E402


def remit(claims: str, bpr: str, payer="ANTHEM", payer_id="ANTHM", seg_count=20, isa_ctrl="000000001", plb: str = "", pay_date="20251124") -> bytes:
    body = (
        f"ISA*00*          *00*          *ZZ*{payer_id:<15}*ZZ*3LDA           *260904*0909*^*00501*{isa_ctrl:>9}*0*P*:~"
        f"GS*HP*{payer_id}*3LDA*20260904*0909*1*X*005010X221A1~ST*835*000000001~"
        f"BPR*I*{bpr}*C*ACH*CCP*01*061000052*DA*123*999**01*042100175*DA*555*{pay_date}~TRN*1*TRACE{isa_ctrl}*1350781558~"
        f"REF*EV*FEP~DTM*405*20251120~N1*PR*{payer}~N3*1 MAIN ST~N4*CINCINNATI*OH*45209~REF*2U*{payer_id}~PER*BL*PORTAL*TE*8005551212~"
        f"N1*PE*TEST DERMATOLOGY*XX*1821130295~N3*2 MAIN ST~N4*OWENSBORO*KY*42303~REF*TJ*311488176~LX*1~"
        f"{claims}{plb}SE*{seg_count}*000000001~GE*1*1~IEA*1*{isa_ctrl}~"
    )
    return body.encode()


CLAIM_DOWNCODE = (
    "CLP*CB00TEST01C017*1*205.80*77.60*40.00*12*PAYERCLAIM1*11~NM1*QC*1*DOE*JANE****MI*R000001~NM1*82*2*TEST PROVIDER MD*****XX*1215042429~"
    "REF*CE*PPO~DTM*050*20251114~DTM*232*20251111~SVC*HC:99213*205.80*77.60**1*HC:99214~DTM*472*20251111~CAS*CO*45*88.20~CAS*PR*3*40.00~REF*6R*1001~AMT*B6*117.60~"
)
CLAIM_BUNDLED = (
    "CLP*CB00TEST02C017*1*401.21*91.31**12*PAYERCLAIM2*11~NM1*QC*1*ROE*RICHARD****MI*R000002~NM1*82*2*TEST PROVIDER MD*****XX*1215042429~DTM*232*20260407~"
    "SVC*HC:99214:25*223.65*0.00**1~DTM*472*20260407~CAS*PI*B10*223.65~REF*6R*2001~"
    "SVC*HC:17110*177.56*91.31**1~DTM*472*20260407~CAS*PR*2*18.26~CAS*CO*45*67.99~REF*6R*2002~AMT*B6*109.57~"
)


def test_parse_basic_remit_balances():
    data = remit(CLAIM_DOWNCODE + CLAIM_BUNDLED, "168.91", seg_count=41, plb="PLB*311488176*20261231*CS:ZELIS TRANSACTION FEE*1.50~")
    r = parse_835_bytes(data, source="test.RMT")
    assert r.counts()["transactions"] == 1
    assert r.counts()["claims"] == 2
    assert r.counts()["lines"] == 3
    t = r.transactions[0]
    assert t["payer_name"] == "ANTHEM" and t["payer_id"] == "ANTHM" and t["payee_npi"] == "1821130295"
    assert t["payment_amount_cents"] == 16891 and t["payment_date"] == "2025-11-24"
    assert t["plb_total_cents"] == 150
    # BPR02 (168.91) = CLP04 total (77.60 + 91.31 = 168.91) - PLB (1.50) -> not balanced on purpose by 1.50
    assert t["claim_paid_total_cents"] == 16891
    assert t["balanced"] is False
    c = r.claims[0]
    assert c["patient_control_number"] == "CB00TEST01C017" and c["claim_status_code"] == "1"
    assert c["balanced"] is True
    line = r.lines[0]
    assert line["procedure_code"] == "99213" and line["original_procedure_code"] == "99214" and line["code_changed"] is True
    assert line["allowed_cents"] == 11760 and line["line_control_number"] == "1001" and line["service_date"] == "2025-11-11"
    b10 = [a for a in r.adjustments if a["reason_code"] == "B10"]
    assert b10 and b10[0]["amount_cents"] == 22365 and b10[0]["level"] == "line"
    assert r.plb[0]["reason_code"] == "CS" and r.plb[0]["reference"] == "ZELIS TRANSACTION FEE" and r.plb[0]["amount_cents"] == 150
    assert r.problems == []


def test_multiple_envelopes_and_isa_inside_names():
    one = remit(CLAIM_DOWNCODE, "77.60", payer="LOUISA HEALTH", payer_id="LISA1", seg_count=29, isa_ctrl="000000001")
    two = remit(CLAIM_DOWNCODE, "77.60", payer="ISA MUTUAL", payer_id="ISAXX", seg_count=29, isa_ctrl="000000002")
    r = parse_835_bytes(one + b"\n" + two)
    assert r.counts()["transactions"] == 2
    assert {t["payer_name"] for t in r.transactions} == {"LOUISA HEALTH", "ISA MUTUAL"}
    assert len(split_envelopes((one + two).decode())) == 2


def test_cents_never_floats():
    assert cents("293.16") == 29316 and cents("-41.41") == -4141 and cents("0.01") == 1 and cents("") is None and cents("5") == 500


def test_fee_table_implies_original_code():
    ft = FeeTable(min_share=0.02, min_count=3)
    for _ in range(10):
        ft.observe("2026", "99214", 22365, 1, False)
        ft.observe("2026", "99213", 15666, 1, False)
        ft.observe("2026", "99213", 8952, 1, False)
    ft.finalize()
    assert ft.implied_original("2026", "99213", 22365) == "99214"
    assert ft.implied_original("2026", "99213", 15666) is None
    assert ft.implied_original("2026", "99214", 22365) is None
    assert is_downcode("99214", "99213") and not is_downcode("99213", "99214") and not is_downcode("99214", "17110")


def test_store_ingest_dedup_and_detect(tmp_path):
    db = tmp_path / "t.sqlite"
    p1 = tmp_path / "a.RMT"
    p1.write_bytes(remit(CLAIM_DOWNCODE + CLAIM_BUNDLED, "168.91", seg_count=41))
    p2 = tmp_path / "b.RMT"
    # same 835 (same ST..SE content) re-delivered inside a new interchange envelope: different file hash, same transaction
    p2.write_bytes(remit(CLAIM_DOWNCODE + CLAIM_BUNDLED, "168.91", seg_count=41).replace(b"*000000001*0*P*", b"*000000009*0*P*").replace(b"IEA*1*000000001", b"IEA*1*000000009"))
    p3 = tmp_path / "c.RMT"
    p3.write_bytes(remit(CLAIM_DOWNCODE + CLAIM_BUNDLED, "168.91", seg_count=41))  # byte-identical file, skipped by hash
    # baseline remits so the detector has rates: 25 clean 99214 lines and 25 clean 99213 lines
    for i in range(25):
        clean = (f"CLP*CB00CLEAN{i:02d}C017*1*223.65*117.60*0.00*12*PC{i}*11~NM1*QC*1*DOE*JOHN****MI*R{i:06d}~NM1*82*2*TEST PROVIDER MD*****XX*1215042429~DTM*232*20260401~"
                 f"SVC*HC:99214*223.65*117.60**1~DTM*472*20260401~CAS*CO*45*106.05~REF*6R*3{i:03d}~AMT*B6*117.60~"
                 f"CLP*CB00CLEAN{i:02d}B017*1*156.66*76.15*0.00*12*PD{i}*11~NM1*QC*1*DOE*JOHN****MI*R{i:06d}~NM1*82*2*TEST PROVIDER MD*****XX*1215042429~DTM*232*20260401~"
                 f"SVC*HC:99213*156.66*76.15**1~DTM*472*20260401~CAS*CO*45*80.51~REF*6R*4{i:03d}~AMT*B6*76.15~")
        (tmp_path / f"c{i}.RMT").write_bytes(remit(clean, "193.75", seg_count=38, isa_ctrl=f"{100 + i:09d}"))
    store = Store(str(db))
    stats = store.ingest_835_paths([str(p) for p in sorted(tmp_path.glob("*.RMT"))])
    assert stats["files"] == 28 and stats["errors"] == 0
    assert stats["skipped_seen"] == 1  # c.RMT is byte-identical to a.RMT
    assert stats["duplicate_transactions"] == 1  # b.RMT carries the same 835 in a new envelope
    assert store.query("SELECT COUNT(*) n FROM transactions")[0]["n"] == 26
    det = Detector(store, run_id="test")
    summary = det.run()
    assert summary.get("DOWNCODE_SVC06") == 1
    assert summary.get("EM_BUNDLED_MOD25") == 1
    f = store.query("SELECT * FROM findings WHERE signal='DOWNCODE_SVC06'")[0]
    assert f["original_procedure_code"] == "99214" and f["procedure_code"] == "99213"
    assert f["expected_cents"] == 11760 and f["shortfall_cents"] == 0  # payer's usual 99214 rate equals what it allowed here
    b = store.query("SELECT * FROM findings WHERE signal='EM_BUNDLED_MOD25'")[0]
    assert b["expected_cents"] == 11760 and b["carc"] == "B10" and b["appeal_deadline"] is not None
    # statuses survive a re-run
    store.conn.execute("UPDATE findings SET status='appealed' WHERE signal='DOWNCODE_SVC06'")
    store.conn.commit()
    Detector(store, run_id="test2").run()
    assert store.query("SELECT status FROM findings WHERE signal='DOWNCODE_SVC06'")[0]["status"] == "appealed"


def test_ema_export_loader(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Service Date", "Primary Provider", "Claim ID", "CPT/Product", "Modifiers", "Units", "Charges", "Payments", "Activity Type", "Bill Item Identifier", "Payer", "Location"])
    ws.append(["2026-09-14", "Doe, Jane", "CB00EJJG6C017", "99214", "25", 1, 223.65, 0, "Charge", 7032635, "Blue Cross and Blue Shield", "Owensboro"])
    ws.append(["2026-09-14", "Doe, Jane", "CB00EJJG6C017", "99214", "-", 0, 0, 63.89, "Payment", 7032635, "Blue Cross and Blue Shield", "Owensboro"])
    p = tmp_path / "Encounter & Charges 9-14-26.xlsx"
    wb.save(p)
    rows = list(ema_exports.iter_rows(str(p)))
    assert len(rows) == 2 and rows[0]["family"] == "charge_lines"
    assert rows[0]["cpt"] == "99214" and rows[0]["modifier_1"] == "25" and rows[0]["charges_cents"] == 22365 and rows[0]["service_date"] == "2026-09-14"
    assert rows[1]["payments_cents"] == 6389 and rows[1]["modifiers"] is None
    assert rows[0]["row_fingerprint"] != rows[1]["row_fingerprint"]
    assert ema_exports.split_carc("CO-45") == ("CO", "45") and ema_exports.split_carc("PR-1") == ("PR", "1") and ema_exports.split_carc("CO-") == ("CO", None)


def test_837_parser():
    data = (
        "ISA*00*          *00*          *ZZ*311488176      *ZZ*TRIZETTO       *261005*1200*^*00501*000000101*0*P*:~GS*HC*311488176*TRIZETTO*20261005*1200*101*X*005010X222A1~"
        "ST*837*0001*005010X222A1~BHT*0019*00*123*20261005*1200*CH~NM1*41*2*TEST DERM*****46*311488176~PER*IC*BILLING*TE*2705551212~NM1*40*2*TRIZETTO*****46*TZ~"
        "HL*1**20*1~NM1*85*2*TEST DERMATOLOGY*****XX*1821130295~N3*2 MAIN~N4*OWENSBORO*KY*42303~REF*EI*311488176~"
        "HL*2*1*22*0~SBR*P*18*GRP1******CI~NM1*IL*1*DOE*JANE****MI*R000001~NM1*PR*2*ANTHEM*****PI*00660~"
        "CLM*CB00TEST01C017*223.65***11:B:1*Y*A*Y*Y~HI*ABK:L409~NM1*82*1*PROVIDER*TEST****XX*1215042429~"
        "LX*1~SV1*HC:99214:25*223.65*UN*1***1~DTP*472*D8*20261001~REF*6R*5001~LX*2~SV1*HC:17110*177.56*UN*1***1~DTP*472*D8*20261001~REF*6R*5002~SE*25*0001~GE*1*101~IEA*1*000000101~"
    ).encode()
    r = parse_837_bytes(data, source="PROFESSIONAL_Claims_x.txt")
    assert len(r["claims"]) == 1 and len(r["lines"]) == 2
    c = r["claims"][0]
    assert c["patient_control_number"] == "CB00TEST01C017" and c["total_charge_cents"] == 22365 and c["frequency_code"] == "1" and c["payer_name"] == "ANTHEM"
    l = r["lines"][0]
    assert l["procedure_code"] == "99214" and l["modifiers"] == "25" and l["charge_cents"] == 22365 and l["service_date"] == "2026-10-01" and l["line_control_number"] == "5001"
    assert r["lines"][1]["procedure_code"] == "17110"


def test_api_smoke(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    db = tmp_path / "api.sqlite"
    store = Store(str(db))
    p = tmp_path / "a.RMT"
    p.write_bytes(remit(CLAIM_DOWNCODE + CLAIM_BUNDLED, "168.91", seg_count=41))
    store.ingest_835_paths([str(p)])
    Detector(store, run_id="t").run()
    store.close()
    import remitwatch.api as api_mod

    monkeypatch.setattr(api_mod, "DB_PATH", str(db))
    monkeypatch.setattr(api_mod, "PASSWORD", "pw")
    client = TestClient(api_mod.app)
    assert client.get("/api/summary").status_code == 401
    auth = ("oda", "pw")
    s = client.get("/api/summary", auth=auth).json()
    assert any(r["signal"] == "DOWNCODE_SVC06" for r in s["by_signal"])
    items = client.get("/api/findings?category=downcoding", auth=auth).json()["items"]
    assert items and items[0]["original_procedure_code"] == "99214"
    det = client.get(f"/api/findings/{items[0]['finding_id']}", auth=auth).json()
    assert det["claim"]["patient"] == "JD" and "patient_last" not in det["claim"]
    pk = client.get(f"/api/appeal/{items[0]['finding_id']}", auth=auth).json()
    assert "99214" in pk["letter"] and "DOE" not in pk["letter"] and pk["attachments"]
    assert client.get("/api/findings.csv?category=downcoding", auth=auth).status_code == 200
    assert client.get("/", auth=auth).status_code == 200


CLAIM_BUNDLED_REWORKED = (
    "CLP*CB00TEST02C024*1*401.21*208.91**12*PAYERCLAIM2B*11~NM1*QC*1*ROE*RICHARD****MI*R000002~NM1*82*2*TEST PROVIDER MD*****XX*1215042429~DTM*232*20260407~"
    "SVC*HC:99214:25*223.65*117.60**1~DTM*472*20260407~CAS*CO*45*106.05~REF*6R*2001~AMT*B6*117.60~"
    "SVC*HC:17110*177.56*91.31**1~DTM*472*20260407~CAS*PR*2*18.26~CAS*CO*45*67.99~REF*6R*2002~AMT*B6*109.57~"
)


def test_later_remit_resolves_finding_and_ema_handling_is_attached(tmp_path):
    db = tmp_path / "r.sqlite"
    store = Store(str(db))
    (tmp_path / "a.RMT").write_bytes(remit(CLAIM_DOWNCODE + CLAIM_BUNDLED, "168.91", seg_count=41, isa_ctrl="000000001", pay_date="20260501"))
    # the corrected claim (new suffix C024) paid the E/M a month later
    (tmp_path / "b.RMT").write_bytes(remit(CLAIM_BUNDLED_REWORKED, "208.91", seg_count=29, isa_ctrl="000000002", pay_date="20260601"))
    store.ingest_835_paths([str(tmp_path / "a.RMT"), str(tmp_path / "b.RMT")])
    # EMA rows: the billed charge line and how the biller handled the B10 adjustment
    store.ingest_ema_rows([
        {"row_fingerprint": "ema-charge-1", "family": "charge_lines", "source_name": "Encounter & Charges 4-7-26.xlsx", "row_index": 1,
         "claim_id": "CB00TEST02C017", "bill_item_id": "7000001", "service_date": "2026-04-07", "cpt": "99214", "modifiers": '["25"]',
         "modifier_1": "25", "units": 1.0, "charges_cents": 22365, "activity_type": "Charge", "patient_id": "P1", "provider": "Doe, Jane",
         "payer": "Blue Cross and Blue Shield", "claim_status": "Resolved/Paid"},
        {"row_fingerprint": "ema-era-1", "family": "era_lines", "source_name": "ERA & Payment Lines 4-26.xlsx", "row_index": 1,
         "claim_id": "CB00TEST02C017", "service_date": "2026-04-07", "cpt": "99214", "carc": "PI-B10", "carc_group": "PI", "carc_code": "B10",
         "adjustment_status": "WRITE_OFF", "adjustment_subtype": "Contractual", "adjustments_cents": 22365, "payments_cents": 0,
         "transaction_date": "2026-05-03"},
    ])
    from remitwatch.link import build_links

    stats = build_links(store, log=lambda *_a: None)
    assert stats["states"].get("exact_composite") == 1
    link = store.query("SELECT * FROM line_links WHERE state='exact_composite'")[0]
    assert link["bill_item_id"] == "7000001" and link["ema_cpt"] == "99214"
    Detector(store, run_id="t").run()
    f = store.query("SELECT * FROM findings WHERE signal='EM_BUNDLED_MOD25'")[0]
    assert f["ema_handling"] == "WRITE_OFF"
    ev = __import__("json").loads(f["evidence"])
    assert ev["ema_link"] == "exact_composite" and "WRITE_OFF" in ev["ema_handling"][0]
    assert f["status"] == "resolved" and "later remittance" in f["auto_note"] and f["notes"] is None and ev["paid_later"]["payment_date"] == "2026-06-01"
    # the downcode on the other claim has no later payment and stays open
    assert store.query("SELECT status FROM findings WHERE signal='DOWNCODE_SVC06'")[0]["status"] == "open"
    # a dismissed finding is never re-opened or auto-resolved
    store.conn.execute("UPDATE findings SET status='dismissed' WHERE signal='EM_BUNDLED_MOD25'")
    store.conn.commit()
    Detector(store, run_id="t2").run()
    assert store.query("SELECT status FROM findings WHERE signal='EM_BUNDLED_MOD25'")[0]["status"] == "dismissed"


def test_fingerprint_ignores_control_numbers():
    a = remit(CLAIM_DOWNCODE, "77.60", seg_count=29, isa_ctrl="000000001")
    b = a.replace(b"ST*835*000000001~", b"ST*835*000000777~").replace(b"SE*29*000000001~", b"SE*29*000000777~")
    fa = parse_835_bytes(a).transactions[0]["content_fingerprint"]
    fb = parse_835_bytes(b).transactions[0]["content_fingerprint"]
    assert fa == fb


def test_auto_resolution_is_reversible(tmp_path):
    db = tmp_path / "rv.sqlite"
    store = Store(str(db))
    (tmp_path / "a.RMT").write_bytes(remit(CLAIM_BUNDLED, "91.31", seg_count=25, isa_ctrl="000000001", pay_date="20260501"))
    (tmp_path / "b.RMT").write_bytes(remit(CLAIM_BUNDLED_REWORKED, "208.91", seg_count=29, isa_ctrl="000000002", pay_date="20260601"))
    store.ingest_835_paths([str(tmp_path / "a.RMT"), str(tmp_path / "b.RMT")])
    Detector(store, run_id="t1").run()
    assert store.query("SELECT status FROM findings WHERE signal='EM_BUNDLED_MOD25'")[0]["status"] == "resolved"
    # the later payment disappears (for example its remit was superseded): the finding must come back
    store.conn.execute("UPDATE transactions SET superseded=1 WHERE payment_date='2026-06-01'")
    store.conn.commit()
    Detector(store, run_id="t2").run()
    f = store.query("SELECT status, notes FROM findings WHERE signal='EM_BUNDLED_MOD25'")[0]
    assert f["status"] == "open" and f["notes"] is None
