"""
Tests for the provider view (remitwatch.activity): who rendered a visit, how repeated remittance lines fold into one
service, what counts as a patient seen, panels and return rates, the EMA side with its capture rule, the API and the
dashboard's copy. Every remittance, name and number here is made up.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import activity  # noqa: E402
from remitwatch.detect import Detector, run_detection  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

ANN, BOB, GROUP = "1111111111", "2222222222", "1777777772"      # a PA, a physician, and the group NPI the fixture pays
PW = "correct-horse-battery-7"


def bill(n: int) -> str:
    return f"CB{n:07d}"


def clp(n: int, lines: str, *, status: str = "1", who: str = "ann", patient: tuple = None, cfi: str = "12", suffix: str = "C017", pcn: str | None = None) -> str:
    """One claim loop. `who` picks how the payer named the rendering provider."""
    last, first = patient or (f"PATIENT{n:03d}", "PAT")
    render = {
        "ann": f"NM1*82*1*ALPHA*ANN****XX*{ANN}~", "bob": f"NM1*82*1*BRAVO*BOB****XX*{BOB}~",
        "group_named_ann": f"NM1*82*1*ALPHA*ANN****XX*{GROUP}~", "group": f"NM1*82*2*TEST DERMATOLOGY ASSOCIATES*****XX*{GROUP}~",
        "name_only_ann": "NM1*82*1*ANN ALPHA PA~", "none": "",
    }[who]
    return f"CLP*{pcn or bill(n) + suffix}*{status}*100.00*80.00*0.00*{cfi}*PC{n}{status}*11~NM1*QC*1*{last}*{first}****MI*M{n:06d}~{render}{lines}"


def svc(code: str, charge: str, paid: str, allowed: str | None, dos: str | None, *, lcn: str | None = None, units: int = 1, hpi: str | None = None,
        cas: str = "", dtm: str = "472") -> str:
    out = f"SVC*HC:{code}*{charge}*{paid}**{units}~" + (f"DTM*{dtm}*{dos}~" if dos else "") + cas
    if lcn:
        out += f"REF*6R*{lcn}~"
    if hpi:
        out += f"REF*HPI*{hpi}~"
    return out + (f"AMT*B6*{allowed}~" if allowed is not None else "")


def visit(n: int, dos: str, code: str = "99213", who: str = "ann", allowed: str = "90.00", **kw) -> str:
    return clp(n, svc(code, "150.00", f"{float(allowed) * 0.8:.2f}", allowed, dos, lcn=f"L{n}"), who=who, **kw)


FILES = [
    # (payment date, payer, claims)
    ("20260320", "KY MEDICARE", [
        # 1: a new patient with a biopsy of three lesions
        clp(1, svc("99203", "200.00", "88.00", "110.00", "20260302", lcn="L1a") + svc("11102", "180.00", "80.00", "100.00", "20260302", lcn="L1b")
            + svc("11103", "120.00", "48.00", "60.00", "20260302", lcn="L1c", units=2), cfi="MB"),
        # 2: paid, later reversed and paid again for less
        clp(2, svc("99214", "200.00", "104.00", "130.00", "20260302", lcn="L2"), cfi="MB"),
        # 3: paid, later a duplicate of the claim is denied
        visit(3, "20260302", cfi="MB"),
        # 4: the payer names the group as rendering provider and Ann by name
        visit(4, "20260302", who="group_named_ann", cfi="MB"),
        # 5: no rendering provider on the claim, a provider reference on the line
        clp(5, svc("99213", "150.00", "72.00", "90.00", "20260302", lcn="L5", hpi=ANN), who="none", cfi="MB"),
        # 6: one person, two bills on one day
        visit(6, "20260302", patient=("TWICE", "TINA"), cfi="MB"),
        clp(60, svc("17110", "120.00", "56.00", "70.00", "20260302", lcn="L60"), patient=("TWICE", "TINA"), cfi="MB"),
        # 7: a procedure with no visit code
        clp(7, svc("17000", "110.00", "48.00", "60.00", "20260303", lcn="L7"), cfi="MB"),
        # the slides from patient 1, read by the physician on a bill of their own
        clp(101, svc("88305", "300.00", "144.00", "180.00", "20260302", lcn="L101", units=3), who="bob", patient=("PATIENT001", "PAT"), cfi="MB"),
        # radiation only
        clp(102, svc("77401", "90.00", "40.00", "50.00", "20260303", lcn="L102"), who="bob", cfi="MB"),
    ]),
    ("20260322", "ANTHEM", [
        visit(9, "20260303", who="group"),          # only the group on the remittance; the EMA export knows the clinician
        visit(10, "20260303", who="group"),         # nobody knows
        visit(11, "20260303"), visit(12, "20260303"), visit(13, "20260303"), visit(14, "20260303", who="name_only_ann"),
        visit(15, "20260304"),
        visit(201, "20260302", who="bob"), visit(202, "20260302", who="bob"),
    ]),
    ("20260325", "MUTUAL OF OMAHA", [
        # the supplement for bill 1: group as rendering provider, no line control numbers, same charges
        clp(1, svc("99203", "200.00", "22.00", None, "20260302") + svc("11102", "180.00", "20.00", None, "20260302")
            + svc("11103", "120.00", "12.00", None, "20260302", units=2), status="2", who="group", suffix="C024"),
    ]),
    ("20260405", "KY MEDICARE", [
        clp(2, svc("99214", "-200.00", "-104.00", None, "20260302", lcn="L2"), status="22", cfi="MB"),
        clp(2, svc("99214", "200.00", "72.00", "90.00", "20260302", lcn="L2"), cfi="MB"),
        clp(3, svc("99213", "150.00", "0.00", None, "20260302", lcn="L3", cas="CAS*OA*18*150.00~"), status="4", cfi="MB"),
    ]),
    ("20260501", "KY MEDICARE", [
        # patient 1 has the cancer treated by the physician and comes back to Ann
        clp(103, svc("17311", "900.00", "480.00", "600.00", "20260410", lcn="L103"), who="bob", patient=("PATIENT001", "PAT"), cfi="MB"),
        visit(16, "20260420", patient=("PATIENT001", "PAT"), cfi="MB"),
    ]),
]


def write_files(folder, files=FILES) -> list[str]:
    paths = []
    for i, (paid, payer, claims) in enumerate(files):
        p = folder / f"r{i:02d}.RMT"
        p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer[:5].replace(" ", "X"), seg_count=99, isa_ctrl=f"{i + 1:09d}", pay_date=paid))
        paths.append(str(p))
    return paths


def ema_row(i: int, source: str, d: str, provider: str, cpt: str, *, patient=None, bill_no=None, insured=True, created=None, item=True, role="Physician Assistant",
            charge=15000, units=1.0, location="North Office") -> dict:
    return {"row_fingerprint": f"fp-{source}-{i}", "family": "charge_lines", "source_sha256": source, "source_name": source, "row_index": i,
            "service_date": d, "bill_created": created, "provider": provider, "provider_role": role, "patient_id": patient,
            "bill_id": bill(bill_no) if bill_no and created else None, "claim_id": (bill(bill_no) + "C017") if bill_no and insured else None,
            "bill_item_id": f"item-{bill_no}-{cpt}" if item and bill_no else None, "cpt": cpt, "units": units, "charges_cents": charge,
            "activity_type": "Charge", "location": location}


def ema_rows() -> list[dict]:
    rows, i = [], 0
    full = "Encounter & Charge Lines full.xlsx"
    # 2 March: six patients for Ann (five insured, one self-pay); her bills are written one to six days after the visit
    for n, lag in ((1, 1), (2, 2), (3, 4), (4, 5), (5, 6)):
        rows.append(ema_row(i := i + 1, full, "2026-03-02", "Alpha, Ann", "99203" if n == 1 else "99213", patient=f"P{n}", bill_no=n, created=f"2026-03-0{2 + lag}"))
    rows.append(ema_row(i := i + 1, full, "2026-03-02", "Alpha, Ann", "11102", patient="P1", bill_no=1, created="2026-03-03"))
    rows.append(ema_row(i := i + 1, full, "2026-03-02", "Alpha, Ann", "99213", patient="P900", bill_no=900, insured=False, created="2026-03-03"))
    # 3 March: five patients, one of them the bill whose remittance names only the group
    for n in (9, 11, 12, 13, 14):
        rows.append(ema_row(i := i + 1, full, "2026-03-03", "Alpha, Ann", "99213", patient=f"P{n}", bill_no=n, created="2026-03-05"))
    # 4 and 5 March: five and six patients
    for d, numbers in (("2026-03-04", range(500, 505)), ("2026-03-05", range(510, 516))):
        for k, n in enumerate(numbers):
            rows.append(ema_row(i := i + 1, full, d, "Alpha, Ann", "99213", patient=f"P{n}", bill_no=n, created=f"2026-03-{int(d[-2:]) + min(2 + k, 6):02d}"))
    # the slides, under the physician: not a patient seen
    rows.append(ema_row(i := i + 1, full, "2026-03-02", "Bravo, Bob", "88305", patient="P1", bill_no=101, created="2026-03-04", role="Doctor", units=3.0))
    # the export was pulled on 30 March: a visit from the 27th is in it, most of that day's bills are not written yet
    rows.append(ema_row(i := i + 1, full, "2026-03-27", "Alpha, Ann", "99213", patient="P300", bill_no=300, created="2026-03-30"))
    # a thin weekly export: claim ids only, no patient, no bill date. It repeats 3 March and adds a day pulled as it ended.
    thin = "Encounter Charge Line Report thin.xlsx"
    for n in (9, 11, 12, 13, 14):
        rows.append(ema_row(i := i + 1, thin, "2026-03-03", "Alpha, Ann", "99213", bill_no=n, item=False))
    for n in range(400, 406):
        rows.append(ema_row(i := i + 1, thin, "2026-04-10", "Alpha, Ann", "99213", bill_no=n, item=False))
    rows.append(ema_row(i := i + 1, thin, "2026-04-10", "Alpha, Ann", "99213", item=False))          # self-pay visit: no id of any kind
    rows.append(ema_row(i := i + 1, thin, "2026-04-10", "Alpha, Ann", "J3301", item=False))          # self-pay product line: cannot be tied to a visit
    return rows


@pytest.fixture
def built(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    stats = store.ingest_835_paths(write_files(tmp_path))
    assert stats["errors"] == 0 and stats["ingested"] == len(FILES)
    store.ingest_ema_rows(ema_rows())
    summary = activity.build_activity(store, log=lambda *a: None)
    assert summary["people"] == 2
    return store


def day(conn, who: str, d: str) -> dict:
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM provider_days WHERE provider_id=? AND service_date=?", (who, d)).fetchone()
    return dict(r) if r else {}


def test_names_as_payers_write_them():
    assert activity.name_tokens("RIVERS III", "JOAN P") == (("RIVERS", "JOAN"), ("P",))
    assert activity.name_tokens("RIVERSIII", "MD") == (("RIVERS",), ())
    assert activity.name_tokens("Stone, Maria") == (("STONE", "MARIA"), ())
    assert activity.is_org("NORTHSIDE DERMATOLOGY ASSOCI") and not activity.is_org("ALPHA ANN")
    people = {"1": {"surname": "ALPHA", "given": "ANN", "name": "Alpha, Ann"}, "2": {"surname": "BRAVO", "given": "BOB", "name": "Bravo, Bob"},
              "3": {"surname": "ALPHA", "given": "ZED", "name": "Alpha, Zed"}}
    assert activity.match_person(people, ("ALPHA", "ANN")) == "1" and activity.match_person(people, ("ANN", "ALPHA")) == "1"
    assert activity.match_person(people, ("ALPHA",), ("Z",)) == "3"
    assert activity.match_person(people, ("ALPHA",)) is None                   # two people share the surname
    assert activity.match_person(people, ("BRAVO",)) == "2"                    # a surname alone is enough when it is unique
    assert activity.match_person(people, ("BRAVO", "SMITH")) is None           # a surname next to someone else's first name is not
    assert activity.match_person(people, ("NOBODY", "ANN")) is None
    assert activity.clean_code("11102 59") == "11102" and activity.clean_code(None) == ""
    assert [activity.service_family(c) for c in ("99203", "99215", "11103", "17000", "17110", "17262", "17311", "11402", "13132", "88305", "77401", "G6012",
                                                 "Q4316", "J3301", "11900", "96372", "96910", "G2211", "99024", "69100", "10060")] == \
        ["new_visit", "est_visit", "biopsy", "destruction_premalignant", "destruction_benign", "destruction_malignant", "mohs", "excision", "repair",
         "pathology", "radiation", "radiation", "skin_substitute", "drug", "injection", "therapy_injection", "phototherapy", "visit_addon", "postop",
         "biopsy", "other"]


def test_who_rendered_each_visit_and_what_counts_as_a_patient(built):
    conn = built.conn
    people, groups = activity.identify_people(conn)
    assert set(people) == {ANN, BOB} and groups == {GROUP}
    assert people[ANN]["surname"] == "ALPHA" and people[ANN]["given"] == "ANN"
    info = activity.info(conn)
    # NPI on the claim, the name beside the group's NPI or on its own, the reference on the line, the EMA export, and nobody
    assert info["attribution"] == {"npi": 16, "name": 2, "line_ref": 1, "ema": 1, "group": 1}

    d = day(conn, ANN, "2026-03-02")
    assert (d["patients"], d["visits"], d["new_visits"], d["est_visits"], d["proc_only"]) == (6, 6, 1, 5, 0)      # two bills for one person: one patient
    assert (d["biopsy_patients"], d["biopsy_lesions"]) == (1, 3.0)
    assert d["path_generated_cents"] == 18000                              # the slides, billed by the physician, credited apart
    # allowed: the newest primary decision. 1: 110+100+60, 2: 90 after the reversal, 3: 90 (a later duplicate denial changes nothing),
    # 4: 90, 5: 90, 6: 90 + 70
    assert d["allowed_cents"] == 27000 + 9000 + 9000 + 9000 + 9000 + 16000 and d["clinic_allowed_cents"] == d["allowed_cents"]
    # paid: every insurer payment. 1: 216 primary + 54 supplement, 2: 104 - 104 + 72, 3 to 6: 72 each, plus 56 for the second bill
    assert d["paid_cents"] == 27000 + 7200 + 7200 + 7200 + 7200 + 7200 + 5600
    assert d["items"] == 9                                                 # the supplement's three lines folded into the primary's three

    d3 = day(conn, ANN, "2026-03-03")
    assert (d3["patients"], d3["visits"], d3["proc_only"]) == (6, 5, 1)    # five visits (one found through EMA, one by name) and a procedure
    assert day(conn, "group", "2026-03-03")["patients"] == 1               # the visit nobody could be named for is kept, apart
    b = day(conn, BOB, "2026-03-02")
    assert (b["patients"], b["path_only"], b["radiation"]) == (2, 1, 0) and b["clinic_allowed_cents"] == 18000 and b["allowed_cents"] == 36000
    assert day(conn, BOB, "2026-03-03") == dict(day(conn, BOB, "2026-03-03"), patients=0, radiation=1, clinic_allowed_cents=0, allowed_cents=5000)

    conn.row_factory = sqlite3.Row
    svc_rows = {(r["provider_id"], r["code"]): dict(r) for r in conn.execute("SELECT * FROM provider_services WHERE month='2026-03'")}
    assert svc_rows[(ANN, "11103")]["units"] == 2.0 and svc_rows[(ANN, "11103")]["items"] == 1
    assert svc_rows[(ANN, "99214")]["allowed_cents"] == 9000 and svc_rows[(ANN, "99214")]["paid_cents"] == 7200 and svc_rows[(ANN, "99214")]["denied"] == 0
    assert svc_rows[(ANN, "99213")]["items"] == 10 and svc_rows[(ANN, "99213")]["denied"] == 0
    assert svc_rows[(BOB, "88305")]["family"] == "pathology" and svc_rows[(BOB, "88305")]["units"] == 3.0
    mix = {(r["provider_id"], r["payer_kind"]): dict(r) for r in conn.execute("SELECT * FROM provider_payers WHERE month='2026-03'")}
    assert mix[(ANN, "medicare")]["seen"] == 8 and mix[(ANN, "medicare")]["biopsied"] == 1 and mix[(ANN, "commercial")]["seen"] == 6
    roster = {r["provider_id"]: dict(r) for r in conn.execute("SELECT * FROM provider_roster")}
    assert roster[ANN]["name"] == "Alpha, Ann" and roster[ANN]["tier"] == "nonphysician" and roster[BOB]["tier"] == "physician"
    assert roster["group"]["kind"] == "group"


def test_panels_new_patient_returns_and_what_the_biopsy_found(built):
    conn = built.conn
    conn.row_factory = sqlite3.Row
    co = {(r["provider_id"], r["quarter"]): dict(r) for r in conn.execute("SELECT * FROM provider_cohorts")}
    q1 = co[(ANN, "2026-Q1")]
    # patient 1: new on 2 March, back twice within 90 days (the physician's surgery, then Ann), biopsied, cancer treated within 120 days
    assert (q1["new_patients"], q1["back_90"], q1["back_365"], q1["back_same"], q1["return_visits"]) == (1, 1, 1, 1, 2)
    assert (q1["biopsied"], q1["treated_120"]) == (1, 1)
    panels = {(r["provider_id"], r["month"]): dict(r) for r in conn.execute("SELECT * FROM provider_panels")}
    assert panels == {}                    # a panel needs twelve months of remittances behind it; this store has five


def test_patients_treated_for_a_skin_cancer_and_not_seen_since_are_counted_under_who_saw_them_last(tmp_path):
    files = [
        ("20240220", "KY MEDICARE", [
            clp(1, svc("17311", "900.00", "480.00", "600.00", "20240201", lcn="A1"), who="bob", patient=("GONE", "GARY"), cfi="MB"),
            clp(2, svc("11642", "400.00", "160.00", "200.00", "20240201", lcn="A2"), who="bob", patient=("BACK", "BETH"), cfi="MB"),
            clp(3, svc("17262", "300.00", "120.00", "150.00", "20240201", lcn="A3"), who="bob", patient=("LATER", "LEO"), cfi="MB"),
            visit(4, "20240201", who="bob", patient=("NEVER", "NED")),                     # no cancer: not part of this count
        ]),
        ("20240320", "KY MEDICARE", [visit(5, "20240301", patient=("GONE", "GARY"), cfi="MB")]),       # last seen by Ann, a month after surgery
        ("20251220", "KY MEDICARE", [visit(6, "20251201", patient=("BACK", "BETH"), cfi="MB")]),       # came back within the year
        ("20260301", "KY MEDICARE", [
            visit(7, "20260215", patient=("LATER", "LEO"), cfi="MB"),      # seen again, after the day the count is taken
            clp(8, svc("77401", "90.00", "40.00", "50.00", "20260210", lcn="A8"), who="bob", patient=("RAY", "RITA"), cfi="MB"),      # in treatment now
        ]),
    ]
    store = Store(str(tmp_path / "s.sqlite"))
    store.ingest_835_paths(write_files(tmp_path, files))
    activity.build_activity(store, log=lambda *a: None)
    store.conn.row_factory = sqlite3.Row
    rows = {r["provider_id"]: dict(r) for r in store.conn.execute("SELECT * FROM provider_recall")}
    # counted 60 days before the newest payment (31 December 2025). Gary: last seen March 2024 by Ann, overdue 12 and 18 months.
    # Beth: seen December 2025. Leo: surgery February 2024 and nothing for two years, but seen in February 2026: a visit after the
    # count date still counts, so he is not overdue. Rita: radiation in February 2026 is contact, and nobody has seen her for a visit.
    assert rows[ANN] == {"provider_id": ANN, "cancer_patients": 3, "overdue_12": 1, "overdue_18": 1, "as_of": "2025-12-31"}
    assert rows["unattributed"]["cancer_patients"] == 1 and rows["unattributed"]["overdue_12"] == 0
    assert BOB not in rows
    ov = activity.overview(store.conn)
    assert ov["recall"]["overdue_12"] == 1 and ov["recall"]["cancer_patients"] == 4 and ov["recall"]["as_of"] == "2025-12-31"
    # the list does not depend on the months chosen: a clinician with no visit in the window still has patients to call back
    assert [(r["provider_id"], r["name"], r["cancer_patients"], r["overdue_12"], r["overdue_18"]) for r in ov["recall"]["by_provider"]] == \
        [(ANN, "Alpha, Ann", 3, 1, 1), ("unattributed", "No clinician on the remittance", 1, 0, 0)]
    assert activity.overview(store.conn, "2026-02", "2026-02")["recall"]["by_provider"] == ov["recall"]["by_provider"]
    store.close()


def test_overview_trend_detail_and_the_months_that_are_complete(built):
    conn = built.conn
    ov = activity.overview(conn, "2026-03", "2026-04")
    ann = next(r for r in ov["providers"] if r["provider_id"] == ANN)
    assert ann["name"] == "Alpha, Ann" and ann["clinic_days"] == 2 and ann["days_any"] == 4 and ann["patients"] == 14
    assert ann["per_day"] == {"median": 6.0, "p25": 6.0, "p75": 6.0, "mean": 6.0, "max": 6}
    assert ann["new_visits"] == 1 and ann["levels"] == {"99203": 1, "99213": 11, "99214": 1}
    assert ann["est_high_share"] is None and ann["new_high_share"] is None            # twelve visits are too few to quote a share
    assert ann["biopsied_per_100_patients"] == round(100 / 14, 1) and ann["lesions_per_biopsied_patient"] == 3.0
    assert ann["path_generated_cents"] == 18000 and ann["allowed_per_patient"] == round(ann["clinic_allowed_cents"] / 14)
    assert [f["family"] for f in ann["families"]][:3] == ["est_visit", "biopsy", "new_visit"]          # largest allowed first
    assert [r["provider_id"] for r in ov["unassigned"]] == ["group"] and ov["unassigned_share"] == round(1 / 18, 6)
    assert ov["clinic_day_min"] == activity.CLINIC_DAY_MIN and ov["from"] == "2026-03" and ov["to"] == "2026-04"
    bob = next(r for r in ov["providers"] if r["provider_id"] == BOB)
    assert bob["path_only"] == 1 and bob["radiation"] == 1 and bob["patients"] == 3 and bob["clinic_days"] == 0 and bob["per_day"]["median"] is None

    cov = ov["coverage"]
    assert cov["first_payment"] == "2026-03-20" and cov["last_payment"] == "2026-05-01"
    assert [m["month"] for m in cov["months"]] == ["2026-04"]              # March began before the payments did: not a covered month
    assert activity.month_completeness("2026-01", "2026-05-01", []) == 1.0 and activity.month_completeness("2026-04", "2026-05-01", []) < 0.5
    cdf = [min(1.0, d / 30) for d in range(151)]
    assert activity.month_completeness("2026-03", "2026-05-01", cdf) == 1.0 and 0.4 < activity.month_completeness("2026-04", "2026-05-01", cdf) < 0.6

    tr = activity.trend(conn, "month")
    assert tr["by"] == "month" and [p["period"] for p in tr["periods"]] == ["2026-04"]
    det = activity.detail(conn, ANN, "2026-03", "2026-04")
    assert [m["month"] for m in det["months"]] == ["2026-03", "2026-04"] and det["months"][0]["clinic_days"] == 2
    assert [w["clinic_days"] for w in det["weekdays"]][:2] == [1, 1] and det["histogram"] == [{"from": 5, "days": 2}]
    assert det["codes"][0]["code"] == "99213" and {p["payer_kind"] for p in det["payers"]} == {"medicare", "commercial"}
    assert activity.detail(conn, "nobody") is None


def test_ema_side_counts_a_day_only_when_the_export_could_hold_it(built):
    conn = built.conn
    conn.row_factory = sqlite3.Row
    days = {(r["provider"], r["service_date"]): dict(r) for r in conn.execute("SELECT * FROM provider_ema_days")}
    a = days[("Alpha, Ann", "2026-03-02")]
    assert (a["patients"], a["insured_patients"], a["visits"], a["new_visits"], a["biopsy_lesions"]) == (6, 5, 6, 1, 1.0)
    assert a["provider_id"] == ANN and a["pulled"] == "2026-03-30" and a["location"] == "North Office"
    b = days[("Alpha, Ann", "2026-03-03")]
    assert b["patients"] == 5                                   # the thin export repeats the same five bills: counted once
    assert days[("Bravo, Bob", "2026-03-02")]["patients"] == 0 and days[("Bravo, Bob", "2026-03-02")]["other_contacts"] == 1
    late = days[("Alpha, Ann", "2026-04-10")]
    assert (late["patients"], late["insured_patients"], late["loose_lines"], late["pulled"]) == (7, 6, 1, "2026-04-10")

    rc = activity.recent(conn)
    assert rc["ema"]["exports"] == 2 and rc["ema"]["exports_with_bill_dates"] == 1 and rc["ema"]["last_bill_created"] == "2026-03-30"
    ann = next(p for p in rc["providers"] if p["provider"] == "Alpha, Ann")
    # nine in ten of her bills exist within six days. 2 to 5 March were pulled almost four weeks later and count;
    # 27 March (pulled three days later) and 10 April (pulled the same day) wait instead of being read as light days.
    assert ann["lag"] == {"median": 3, "p75": 5, "p90": 6, "bills": 23, "basis": "every bill for an office visit in the exports", "at_least": False}
    assert ann["hold_days"] == 6 and (ann["counted_from"], ann["counted_to"]) == ("2026-03-02", "2026-03-05") and ann["named_share"] == 1.0
    assert ann["clinic_days"] == 4 and ann["days_waiting"] == 2 and ann["patients_per_day"] == 5.5 and ann["insured_share"] == round(21 / 22, 6)
    assert ann["raw_days"] == 6 and ann["raw_patients_per_day"] == 5.0 and ann["new_per_day"] == round(1 / 4, 1)
    assert ann["remit_patients_per_day"] == round((6 + 6 + 1 + 0) / 4, 1) and ann["remit_days"] == 4 and ann["remit_share"] is None      # too few to say
    assert (rc["counted_from"], rc["counted_to"]) == ("2026-03-02", "2026-03-05")
    assert ann["offices"] == [{"name": "North Office", "share": 1.0}] and ann["tier"] == "nonphysician"
    assert activity._short_names({"North Dermatology Associates", "South Dermatology Associates", "Laser Center"}) == \
        {"North Dermatology Associates": "North", "South Dermatology Associates": "South", "Laser Center": "Laser Center"}


def test_nothing_about_a_patient_is_stored(built):
    conn = built.conn
    words = ("PATIENT0", "TWICE", "TINA", "M0000")
    for t in activity.TABLES:
        for row in conn.execute(f"SELECT * FROM {t}"):
            text = " ".join(str(v) for v in row)
            assert not any(w in text.upper() for w in words), (t, text)
    assert not any(w in conn.execute("SELECT value FROM meta WHERE key=?", (activity.INFO_KEY,)).fetchone()[0].upper() for w in words)


def test_the_same_tables_whatever_order_the_files_are_read_in(tmp_path):
    dumps = []
    for name, order in (("a", FILES), ("b", list(reversed(FILES)))):
        folder = tmp_path / name
        folder.mkdir()
        store = Store(str(folder / "s.sqlite"))
        paths = write_files(folder, FILES)
        store.ingest_835_paths(paths if order is FILES else list(reversed(paths)))
        store.ingest_ema_rows(ema_rows() if order is FILES else list(reversed(ema_rows())))
        activity.build_activity(store, log=lambda *a: None)
        dumps.append({t: sorted(tuple(r) for r in store.conn.execute(f"SELECT * FROM {t}")) for t in activity.TABLES})
        store.close()
    assert dumps[0] == dumps[1]


def test_built_after_detection_rebuilt_after_an_upgrade_and_never_fatal(tmp_path, monkeypatch):
    db = str(tmp_path / "s.sqlite")
    store = Store(db)
    store.ingest_835_paths(write_files(tmp_path))
    assert activity.stale(store.conn) is True and activity.status(store.conn)["ready"] is False
    store.close()
    run_detection(db)                                                      # the server pipeline's entry point builds the tables too
    store = Store(db)
    assert activity.stale(store.conn) is False and activity.status(store.conn) == dict(activity.status(store.conn), ready=True, error=None)
    assert store.conn.execute("SELECT COUNT(*) FROM provider_days").fetchone()[0] > 0
    # an upgrade that changes how the tables are built makes them stale, and the old layout is dropped, not read
    monkeypatch.setattr(activity, "ACTIVITY_VERSION", "test-next")
    assert activity.stale(store.conn) is True
    assert activity.refresh(store, log=lambda *a: None)["people"] == 2 and activity.stale(store.conn) is False
    # a fault while building is recorded where the dashboard can show it, and the caller carries on
    monkeypatch.setattr(activity, "identify_people", lambda conn: (_ for _ in ()).throw(RuntimeError("boom")))
    out = activity.refresh(store, log=lambda *a: None)
    assert out == {"error": "RuntimeError: boom"} and activity.status(store.conn)["error"] == "RuntimeError: boom"
    monkeypatch.undo()
    assert Detector(store, run_id="again").run() is not None               # detection itself is untouched by any of it
    store.close()


def test_api_and_the_dashboard_copy(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from remitwatch import api, cloud
    db = str(tmp_path / "s.sqlite")
    store = Store(db)
    store.ingest_835_paths(write_files(tmp_path))
    store.close()
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc, auth = TestClient(api.app), ("oda", PW)
    assert tc.get("/api/activity/overview").status_code == 401
    before = tc.get("/api/activity/overview", auth=auth).json()
    assert before["ready"] is False and "next analysis run" in before["message"]
    run_detection(db)
    ov = tc.get("/api/activity/overview?from=2026-03&to=2026-04", auth=auth).json()
    assert ov["ready"] is True and [r["provider_id"] for r in ov["providers"]] == [ANN, BOB] and ov["built_at"]
    assert tc.get("/api/activity/overview?from=March", auth=auth).status_code == 422
    for month in ("2026-13", "2026-00", "2026-1", "２０２６-03"):        # months that do not exist, and digits that are not plain digits
        assert tc.get("/api/activity/overview", params={"from": month}, auth=auth).status_code == 422, month
        assert tc.get(f"/api/activity/provider/{ANN}", params={"to": month}, auth=auth).status_code == 422, month
    cov = tc.get("/api/coverage", auth=auth).json()
    assert cov["ready"] is True and cov["gaps"] == [] and cov["months"] and tc.get("/api/coverage").status_code == 401
    assert tc.get("/api/activity/trend?by=week", auth=auth).status_code == 422 and tc.get("/api/activity/trend", auth=auth).json()["by"] == "quarter"
    assert tc.get("/api/activity/recent", auth=auth).json()["providers"] == []          # no EMA export in this store
    assert tc.get(f"/api/activity/provider/{ANN}?from=2026-03&to=2026-04", auth=auth).json()["provider"]["name"] == "Alpha, Ann"
    assert tc.get("/api/activity/provider/nobody", auth=auth).status_code == 404
    assert tc.get("/api/activity/provider/a%20b", auth=auth).status_code == 404
    text = json.dumps([tc.get(p, auth=auth).json() for p in ("/api/activity/overview", "/api/activity/trend", f"/api/activity/provider/{ANN}")]).upper()
    assert "PATIENT0" not in text and "TWICE" not in text

    # the dashboard's copy (serverless mode) carries the provider tables and nothing else new
    store = Store(db)
    path = str(tmp_path / "serving.sqlite")
    cloud.build_serving(store.conn, path)
    copy = sqlite3.connect(path)
    for t in activity.TABLES:
        assert copy.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == store.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], t
    # thirteen here, not fourteen: this store has no EMA export, so the visit whose remittance names only the group stays unassigned
    assert activity.status(copy)["ready"] is True and activity.overview(copy, "2026-03", "2026-04")["providers"][0]["patients"] == 13
    copy.close()
    store.close()


# --------------------------------------------------------------------------- one service per billed line, whoever reports it
SEED = ("20200110", "ANTHEM", [visit(9000 + i, "20200102", patient=(f"SEEDA{i}", "X")) for i in range(3)]
        + [visit(9100 + i, "20200102", who="bob", patient=(f"SEEDB{i}", "X")) for i in range(3)])
D = "20260302"
MEDICARE_PAID = clp(1, svc("99213", "150.00", "72.00", "90.00", D, lcn="50000001") + svc("11102", "180.00", "80.00", "100.00", D, lcn="50000002")
                    + svc("11103", "120.00", "48.00", "60.00", D, lcn="50000003", units=2), cfi="MB", status="19")


def fold(tmp_path, name: str, files: list) -> tuple[dict, dict, set, dict]:
    """A store from a few remittances (plus enough plain claims for both clinicians to be known as people): Ann's one day in
    2026, her services by code, the kinds of payer her encounters were filed under, and the build's own record."""
    folder = tmp_path / name
    folder.mkdir()
    store = Store(str(folder / "s.sqlite"))
    assert store.ingest_835_paths(write_files(folder, [SEED] + list(files)))["errors"] == 0
    activity.build_activity(store, log=lambda *a: None)
    store.conn.row_factory = sqlite3.Row
    days = [dict(r) for r in store.conn.execute("SELECT * FROM provider_days WHERE service_date >= '2026-01-01' AND provider_id=?", (ANN,))]
    svcs = {r["code"]: dict(r) for r in store.conn.execute("SELECT * FROM provider_services WHERE month >= '2026-01' AND provider_id=?", (ANN,))}
    kinds = {r["payer_kind"] for r in store.conn.execute("SELECT payer_kind FROM provider_payers WHERE month >= '2026-01' AND provider_id=?", (ANN,))}
    info = activity.info(store.conn)
    store.close()
    assert len(days) == 1, days
    return days[0], svcs, kinds, info


def test_a_second_payer_never_makes_a_second_service(tmp_path):
    def supplement(status, numbers, charges, allowed):
        lines = "".join(svc(code, charge, paid, alw, D, lcn=lcn, units=units) for (code, paid, units), lcn, charge, alw in zip(
            (("99213", "18.00", 1), ("11102", "20.00", 1), ("11103", "12.00", 2)), numbers, charges, allowed))
        return ("20260325", "MUTUAL OF OMAHA", [clp(1, lines, status=status, who="group", suffix="C024")])
    same, full = ("50000001", "50000002", "50000003"), ("150.00", "180.00", "120.00")
    cases = {
        "its own line numbers": supplement("2", ("1", "2", "3"), full, (None,) * 3),
        "no numbers and its own charges": supplement("2", (None,) * 3, ("18.00", "20.00", "12.00"), (None,) * 3),
        "calls itself primary, own numbers": supplement("1", ("1", "2", "3"), full, ("18.00", "20.00", "12.00")),
        "calls itself primary, same numbers": supplement("1", same, full, ("18.00", "20.00", "12.00")),
    }
    for i, (what, second) in enumerate(cases.items()):
        day_, svcs, kinds, info = fold(tmp_path, f"c{i}", [("20260320", "KY MEDICARE", [MEDICARE_PAID]), second])
        got = (day_["items"], day_["patients"], day_["visits"], day_["biopsy_lesions"], day_["charge_cents"], day_["allowed_cents"], day_["paid_cents"])
        assert got == (3, 1, 1, 3.0, 45000, 25000, 25000), (what, got)          # Medicare's allowed, both payers' payments, three services
        assert svcs["99213"]["items"] == 1 and svcs["11103"]["units"] == 2.0 and kinds == {"medicare"}, what
        assert info["checks"]["visit_codes_per_visit"] == 1.0, what


def test_reversals_denials_and_two_payers_that_both_decide(tmp_path):
    paid = clp(1, svc("99214", "200.00", "104.00", "130.00", D, lcn="50000001"), cfi="MB", status="19")
    omaha = lambda amount, status: clp(1, svc("99214", "200.00" if status != "22" else "-200.00", amount, None, D, lcn="50000001"),      # noqa: E731
                                       status=status, who="group", suffix="C024")
    # a supplement reverses its own payment and pays again: what Medicare allowed is untouched
    day_, svcs, _k, _i = fold(tmp_path, "a", [("20260320", "KY MEDICARE", [paid]), ("20260325", "MUTUAL OF OMAHA", [omaha("26.00", "2")]),
                                              ("20260410", "MUTUAL OF OMAHA", [omaha("-26.00", "22"), omaha("26.00", "2")])])
    assert (day_["items"], day_["allowed_cents"], day_["paid_cents"]) == (1, 13000, 13000)
    # paid, then reversed, then denied by the same payer: denied, nothing allowed, nothing paid
    day_, svcs, _k, _i = fold(tmp_path, "b", [
        ("20260320", "KY MEDICARE", [clp(1, svc("99214", "200.00", "104.00", "130.00", D, lcn="50000001"), cfi="MB")]),
        ("20260410", "KY MEDICARE", [clp(1, svc("99214", "-200.00", "-104.00", None, D, lcn="50000001"), cfi="MB", status="22"),
                                     clp(1, svc("99214", "200.00", "0.00", None, D, lcn="50000001", cas="CAS*CO*50*200.00~"), cfi="MB", status="4")])])
    assert (day_["allowed_cents"], day_["paid_cents"], svcs["99214"]["denied"]) == (0, 0, 1)
    # the wrong contractor pays, the right one pays, the wrong one takes its money back and denies: the right one's decision stands
    wrong, right = "IN MEDICARE", "KY MEDICARE"
    day_, svcs, kinds, _i = fold(tmp_path, "c", [
        ("20260320", wrong, [clp(1, svc("99214", "200.00", "100.00", "125.00", D, lcn="50000001"), cfi="MB")]),
        ("20260325", right, [clp(1, svc("99214", "200.00", "104.00", "130.00", D, lcn="50000001"), cfi="MB")]),
        ("20260410", wrong, [clp(1, svc("99214", "-200.00", "-100.00", None, D, lcn="50000001"), cfi="MB", status="22"),
                             clp(1, svc("99214", "200.00", "0.00", None, D, lcn="50000001", cas="CAS*CO*109*200.00~"), cfi="MB")])])
    assert (day_["items"], day_["allowed_cents"], day_["paid_cents"], svcs["99214"]["denied"], day_["place"]) == (1, 13000, 10400, 0, "KY")
    # a payer that pays without saying what it allowed: at least what it paid
    day_, _s, _k, _i = fold(tmp_path, "d", [("20260320", "ANTHEM", [clp(1, svc("99213", "150.00", "72.00", None, D, lcn="50000001"))])])
    assert (day_["allowed_cents"], day_["paid_cents"]) == (7200, 7200)


def test_odd_lines_one_number_for_a_claim_a_split_line_missing_dates_short_ids(tmp_path):
    # one control number on every line of the claim: still three services
    day_, svcs, _k, _i = fold(tmp_path, "a", [("20260320", "ANTHEM", [clp(1, svc("99213", "150.00", "72.00", "90.00", D, lcn="AB17")
                                                                         + svc("11102", "180.00", "80.00", "100.00", D, lcn="AB17")
                                                                         + svc("11103", "120.00", "48.00", "60.00", D, lcn="AB17", units=2))])])
    assert (day_["items"], day_["visits"], day_["biopsy_lesions"], day_["allowed_cents"]) == (3, 1, 3.0, 25000)
    # a billed line the payer split in two under its number (two units paid, one denied): one service of three units
    day_, svcs, _k, _i = fold(tmp_path, "b", [("20260320", "KY MEDICARE", [clp(1, svc("99213", "150.00", "72.00", "90.00", D, lcn="50000001")
                                                                              + svc("11103", "120.00", "48.00", "60.00", D, lcn="50000003", units=2)
                                                                              + svc("11103", "60.00", "0.00", None, D, lcn="50000003", units=1, cas="CAS*CO*151*60.00~"),
                                                                              cfi="MB")])])
    assert (svcs["11103"]["items"], svcs["11103"]["units"], svcs["11103"]["allowed_cents"], svcs["11103"]["charge_cents"]) == (1, 3.0, 6000, 18000)
    # a line dated only by a service period, and a line with no date at all on a bill with one date: both belong to that visit
    day_, svcs, _k, info = fold(tmp_path, "c", [("20260320", "ANTHEM", [clp(1, svc("99213", "150.00", "72.00", "90.00", D, lcn="50000001", dtm="150")
                                                                           + svc("17000", "110.00", "48.00", "60.00", None, lcn="50000002"))])])
    assert (day_["service_date"], day_["items"], day_["visits"], day_["proc_only"], info["undated_services"]) == ("2026-03-02", 2, 1, 0, 0)
    # a short patient control number is still one bill: paid, reversed and paid again is one visit, not two
    short = lambda status, charge, paid, allowed: clp(1, svc("99213", charge, paid, allowed, D, lcn="50000001"), status=status, pcn="A1234")      # noqa: E731
    day_, svcs, _k, _i = fold(tmp_path, "d", [("20260320", "ANTHEM", [short("1", "150.00", "72.00", "90.00")]),
                                              ("20260410", "ANTHEM", [short("22", "-150.00", "-72.00", None), short("1", "150.00", "80.00", "100.00")])])
    assert (day_["patients"], day_["items"], day_["allowed_cents"], day_["paid_cents"]) == (1, 1, 10000, 8000)


def test_what_a_day_holds_office_state_visits_without_a_procedure_and_contacts_that_are_not_patients(tmp_path):
    ky = [clp(1, svc("99214", "200.00", "104.00", "130.00", D, lcn="L1"), cfi="MB"),                                  # established, no procedure, high level
          clp(2, svc("99213", "150.00", "72.00", "90.00", D, lcn="L2"), cfi="MB"),                                    # established, no procedure
          clp(3, svc("99214", "200.00", "104.00", "130.00", D, lcn="L3a") + svc("11102", "180.00", "80.00", "100.00", D, lcn="L3b"), cfi="MB"),
          clp(4, svc("99024", "0.00", "0.00", None, D, lcn="L4"), cfi="MB"),                                          # a post-operative check only
          clp(5, svc("96910", "80.00", "40.00", "50.00", D, lcn="L5"), cfi="MB"),                                     # a light-box session only
          clp(101, svc("88305", "100.00", "48.00", "60.00", D, lcn="L101"), who="bob", patient=("PATIENT003", "PAT"), cfi="MB")]
    folder = tmp_path / "s"
    folder.mkdir()
    store = Store(str(folder / "s.sqlite"))
    store.ingest_835_paths(write_files(folder, [SEED, ("20260320", "KY MEDICARE", ky),
                                                ("20260321", "IN MEDICARE", [visit(6, "20260303", cfi="MB"), visit(7, "20260303", cfi="MB")]),
                                                ("20260322", "ANTHEM", [visit(8, "20260304"), visit(9, "20260304")])]))
    activity.build_activity(store, log=lambda *a: None)
    d = day(store.conn, ANN, "2026-03-02")
    assert (d["patients"], d["visits"], d["est_visits"], d["est_alone"], d["est_alone_high"], d["other_contacts"]) == (3, 3, 3, 2, 1, 2)
    assert (d["biopsy_patients"], d["path_read_patients"], d["path_generated_cents"], d["place"]) == (1, 1, 6000, "KY")
    assert day(store.conn, ANN, "2026-03-03")["place"] == "IN" and day(store.conn, ANN, "2026-03-04")["place"] is None      # no Medicare claim that day
    assert next(r for r in activity.overview(store.conn, "2026-03", "2026-03")["providers"] if r["provider_id"] == ANN)["clinic_days_no_state"] == 0
    ov = activity.overview(store.conn, "2026-03", "2026-03")
    ann = next(r for r in ov["providers"] if r["provider_id"] == ANN)
    assert ann["other_contacts"] == 2 and ann["patients"] == 7 and ann["path_read_share"] is None and ann["est_alone_high_share"] is None    # too few to quote a share
    assert [p["place"] for p in activity.detail(store.conn, ANN, "2026-03", "2026-03")["places"]] == []                # no clinic day of five yet
    store.close()


def test_panels_keep_established_patients_apart_and_count_only_a_first_biopsy(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    activity.ensure_schema(store.conn)
    journeys = {
        ("OLD", "A"): [("2024-03-10", ANN, False, False), ("2025-03-10", ANN, False, False)],       # established, came back to Ann
        ("NEW", "B"): [("2024-05-10", ANN, True, False)],                                           # new in the first year, never back
        ("NEW", "C"): [("2024-06-10", ANN, True, False), ("2025-06-10", BOB, False, False)],        # new in the first year, back to Bob
        ("EST", "D"): [("2024-02-10", ANN, False, False)],                                          # established, not seen again
        ("BX", "E"): [("2025-01-10", ANN, False, True), ("2025-02-10", ANN, False, True), ("2025-09-10", ANN, False, True)],
    }
    treated = {("BX", "E"): ["2025-03-01", "2025-10-01"]}
    activity.build_panels(store.conn, journeys, treated, "2024-01-01", "2025-12-31")
    store.conn.row_factory = sqlite3.Row
    rows = {(r["provider_id"], r["month"]): dict(r) for r in store.conn.execute("SELECT * FROM provider_panels")}
    # December 2025 looks back on 2025 against 2024. Ann saw four people in 2024; two were seen again (one by Bob), one by her.
    # Leaving out the two who were new to her that year: two established, one kept.
    assert rows[(ANN, "2025-12")] == {"provider_id": ANN, "month": "2025-12", "panel": 2, "prior_panel": 4, "kept": 2, "kept_same": 1,
                                      "prior_est": 2, "kept_est": 1}
    assert rows[(BOB, "2025-12")]["panel"] == 1 and rows[(BOB, "2025-12")]["prior_panel"] == 0
    assert rows[(ANN, "2024-12")]["panel"] == 4 and rows[(ANN, "2024-12")]["prior_panel"] is None        # nothing to look back on yet
    co = {(r["provider_id"], r["quarter"]): dict(r) for r in store.conn.execute("SELECT * FROM provider_cohorts")}
    # the second biopsy follows the first by a month, the third follows a treated cancer: only the first is counted, and it found one
    assert (co[(ANN, "2025-Q1")]["biopsied"], co[(ANN, "2025-Q1")]["treated_120"]) == (1, 1) and (ANN, "2025-Q3") not in co
    q2 = co[(ANN, "2024-Q2")]
    assert (q2["new_patients"], q2["back_90"], q2["back_365"], q2["back_same"], q2["return_visits"]) == (2, 0, 1, 0, 1)
    store.close()


def gap_files(skip=(16, 17), weeks=24, start=None, also_skip_a_year_before=False):
    """A payer that pays thirty visits every Wednesday, two weeks after they happened, a second that pays sixty, and a
    fund that pays forty-five every other week (its empty weeks are normal)."""
    from datetime import date, timedelta
    files, n = [], 0
    monday = start or date(2025, 1, 6)
    for w in range(weeks):
        paid = (monday + timedelta(days=7 * w + 2)).strftime("%Y%m%d")
        dos = (monday + timedelta(days=7 * w - 14)).strftime("%Y%m%d")
        if w not in skip and not (also_skip_a_year_before and (w + 52) in skip):
            files.append((paid, "ANTHEM", [visit(n := n + 1, dos) for _ in range(30)]))
        files.append((paid, "HUMANA", [visit(n := n + 1, dos) for _ in range(60)]))
        if w % 2 == 0:
            files.append((paid, "UMWA HEALTH & RETIREMENT FUNDS", [visit(n := n + 1, dos) for _ in range(45)]))
    return files


def test_weeks_a_payer_went_missing_are_found_and_measured_by_month_of_service(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    assert store.ingest_835_paths(write_files(tmp_path, gap_files()))["errors"] == 0
    activity.build_activity(store, log=lambda *a: None)
    meta = activity.info(store.conn)
    gaps, holes = meta["gaps"], meta["gap_months"]
    assert len(gaps) == 1 and gaps[0]["payer"] == "ANTHEM" and (gaps[0]["from"], gaps[0]["to"], gaps[0]["weeks"]) == ("2025-04-28", "2025-05-11", 2)
    assert gaps[0]["seen"] == 0 and gaps[0]["usual"] == 60
    # the two missing payments were for the visits of 14 and 21 April: sixty of April's claims, and no other month's.
    # It is measured against the payer's usual share of everyone else's claims, so it lands near sixty, not on it.
    assert list(gaps[0]["missing_by_month"]) == ["2025-04"] and 54 <= gaps[0]["missing"] <= 68
    assert list(holes) == ["2025-04"] and 0.1 < holes["2025-04"] < 0.2              # 60 of the 450 or so that should be there
    cov = activity.coverage(store.conn)
    by = {m["month"]: m for m in cov["months"]}
    assert by["2025-04"]["complete"] < 0.9 and by["2025-04"]["missing_to_gaps"] > 0.1
    assert by["2025-03"]["missing_to_gaps"] == 0 and by["2025-05"]["missing_to_gaps"] == 0 and cov["gaps"] == gaps
    assert "2025-04" not in (cov["default_from"] or "") and cov["last_complete_month"] != "2025-04"
    store.close()


def test_a_week_that_is_thin_every_year_and_a_week_made_up_later_are_not_gaps(tmp_path):
    from datetime import date
    # the same two weeks are empty in both years: the season, not a lost file
    store = Store(str(tmp_path / "a.sqlite"))
    (tmp_path / "a").mkdir()
    files = gap_files(skip=(68, 69), weeks=80, start=date(2024, 1, 1), also_skip_a_year_before=True)
    assert store.ingest_835_paths(write_files(tmp_path / "a", files))["errors"] == 0
    activity.build_activity(store, log=lambda *a: None)
    # the first time it happens there is no year to compare with, so it is reported; the second time it is the season
    assert [g["from"] for g in activity.info(store.conn)["gaps"]] == ["2024-04-22"]
    assert all(m.startswith("2024") for m in activity.info(store.conn)["gap_months"])
    store.close()
    # a week with nothing, and the week after it with double: the payer was late, nothing is missing
    store = Store(str(tmp_path / "b.sqlite"))
    (tmp_path / "b").mkdir()
    files = gap_files(skip=(16,))
    late = [visit(5000 + i, "20250414") for i in range(30)]                 # the visits the skipped week would have paid
    files.append(("20250507", "ANTHEM", late))
    assert store.ingest_835_paths(write_files(tmp_path / "b", files))["errors"] == 0
    activity.build_activity(store, log=lambda *a: None)
    assert activity.info(store.conn)["gaps"] == []
    assert all(m["missing_to_gaps"] == 0 for m in activity.coverage(store.conn)["months"])
    store.close()


def test_one_payer_under_two_names_is_one_payer_and_a_reversal_cancels_the_decision_it_matches(tmp_path):
    # the payer adjusts its first decision (pays again for less, no reversal), then takes the first one back: the second stands
    first = clp(1, svc("99214", "200.00", "104.00", "130.00", D, lcn="50000001"), cfi="MB")
    second = clp(1, svc("99214", "200.00", "80.00", "100.00", D, lcn="50000001"), cfi="MB")
    back = clp(1, svc("99214", "-200.00", "-104.00", "-130.00", D, lcn="50000001"), cfi="MB", status="22")
    day_, svcs, _k, _i = fold(tmp_path, "a", [("20260320", "KY MEDICARE", [first]), ("20260327", "KY MEDICARE", [second]), ("20260410", "KY MEDICARE", [back])])
    assert (day_["items"], day_["allowed_cents"], day_["paid_cents"], svcs["99214"]["denied"]) == (1, 10000, 8000, 0)
    # the reversal arrives under another name of the same payer id: it still cancels that payer's decision
    files = write_files_with_ids(tmp_path, "b", [
        ("20260320", "UMR", "39026", [clp(1, svc("99213", "150.00", "72.00", "90.00", D, lcn="50000001"))]),
        ("20260410", "WAUSAU", "39026", [clp(1, svc("99213", "-150.00", "-72.00", "-90.00", D, lcn="50000001"), status="22"),
                                         clp(1, svc("99213", "150.00", "0.00", None, D, lcn="50000001", cas="CAS*CO*29*150.00~"), status="4")])])
    store = Store(str(tmp_path / "b.sqlite"))
    assert store.ingest_835_paths(files)["errors"] == 0
    activity.build_activity(store, log=lambda *a: None)
    store.conn.row_factory = sqlite3.Row
    r = dict(store.conn.execute("SELECT * FROM provider_services WHERE code='99213' AND month='2026-03'").fetchone())
    assert (r["items"], r["allowed_cents"], r["paid_cents"], r["denied"]) == (1, 0, 0, 1)
    store.close()


def write_files_with_ids(tmp_path, name: str, files: list) -> list[str]:
    """Remittance files whose payer id is given, not made from the name (plus the seed that makes both clinicians people)."""
    folder = tmp_path / name
    folder.mkdir()
    out = write_files(folder, [SEED])
    for i, (paid, payer, payer_id, claims) in enumerate(files):
        p = folder / f"ID{i:03d}-{payer.replace(' ', '')}.RMT"
        p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{900 + i:09d}", pay_date=paid))
        out.append(str(p))
    return out


def test_units_the_patients_share_and_a_single_medicare_claim(tmp_path):
    # a payer that writes 0 for the units billed beside the units it paid: three lesions, not one
    zero_units = (f"SVC*HC:11102*180.00*80.00**1*HC:11102*0~DTM*472*{D}~REF*6R*50000002~AMT*B6*100.00~"
                  f"SVC*HC:11103*120.00*48.00**2*HC:11103*0~DTM*472*{D}~REF*6R*50000003~AMT*B6*60.00~")
    day_, svcs, _k, _i = fold(tmp_path, "a", [("20260320", "ANTHEM", [clp(1, svc("99213", "150.00", "72.00", "90.00", D, lcn="50000001") + zero_units)])])
    assert (day_["biopsy_lesions"], svcs["11103"]["units"], day_["place"]) == (3.0, 2.0, None)
    # the payer pays nothing and says the patient owes it all (deductible), with no allowed amount: allowed, not denied
    owed = clp(1, svc("99213", "150.00", "0.00", None, D, lcn="50000001", cas="CAS*CO*45*60.00~CAS*PR*1*90.00~"), cfi="MB")
    day_, svcs, _k, _i = fold(tmp_path, "b", [("20260320", "KY MEDICARE", [owed])])
    assert (day_["allowed_cents"], day_["paid_cents"], svcs["99213"]["denied"], day_["place"]) == (9000, 0, 0, "KY")     # and one Medicare claim tells the state
    # two contractors on one day, one claim each: no state
    day_, _s, _k, _i = fold(tmp_path, "c", [("20260320", "KY MEDICARE", [visit(1, D, cfi="MB")]), ("20260321", "IN MEDICARE", [visit(2, D, cfi="MB")])])
    assert day_["place"] is None and day_["patients"] == 2
    info = _i
    assert info["checks"]["services_paid_over_allowed"] == 0 and info["checks"]["services_paid_over_allowed_share"] == 0


def test_export_pull_times_voids_and_exports_by_transaction_date(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    rows, i = [], 0
    early, later, posted = "Encounter & Charges early.xlsx", "Encounter & Charges later.xlsx", "Charges by Transaction Date.xlsx"
    for source in (early, later):                       # the later export repeats the same five charges and nothing else
        for n in range(1, 6):
            rows.append(ema_row(i := i + 1, source, "2026-03-02", "Alpha, Ann", "99213", patient=f"P{n}", bill_no=n, created="2026-03-03"))
    void = dict(ema_row(i := i + 1, early, "2026-03-02", "Alpha, Ann", "99213", patient="P1", bill_no=1, created="2026-03-03"), activity_type="Void Payment")
    gone = dict(ema_row(i := i + 1, early, "2026-03-02", "Alpha, Ann", "99213", patient="P2", bill_no=2, created="2026-03-03"), activity_type="Void Charge")
    for k, d in enumerate(("2026-03-09", "2026-03-10", "2026-03-11")):      # charges posted on one day for three days of service
        for n in range(5):
            rows.append(dict(ema_row(i := i + 1, posted, d, "Alpha, Ann", "99213", patient=f"Q{k}{n}", bill_no=100 + 10 * k + n, created="2026-03-20"),
                             transaction_date="2026-03-20"))
    store.ingest_ema_rows(rows + [void, gone])
    store.record_export(early, "sha-a", "charge_lines", "2026-03-04T15:00:00Z", 5)
    store.record_export(later, "sha-b", "charge_lines", "2026-04-08T15:00:00Z", 5)
    store.record_export(posted, "sha-c", "charge_lines", "2026-04-08T16:00:00Z", 15)
    activity.build_activity(store, log=lambda *a: None)
    store.conn.row_factory = sqlite3.Row
    days = {r["service_date"]: dict(r) for r in store.conn.execute("SELECT * FROM provider_ema_days")}
    # the later export moved the day's pull date although every one of its rows was a repeat; a voided payment leaves the
    # charge alone, a voided charge removes it
    assert (days["2026-03-02"]["patients"], days["2026-03-02"]["pulled"]) == (4, "2026-04-08")
    # the export by transaction date cannot vouch for any day: the days only it holds are unknown, not light and not waiting
    assert sorted(days) == ["2026-03-02"]
    meta = activity.info(store.conn)["ema"]
    assert meta["exports"] == 3 and meta["exports_with_a_creation_time"] == 3 and meta["last_pulled"] == "2026-04-08"
    assert (meta["first_service"], meta["last_service"]) == ("2026-03-02", "2026-03-02")
    assert activity.recent(store.conn)["providers"] == []                 # four patients on one day is not a clinician's week
    store.close()


def test_export_creation_time_is_read_from_the_workbook_and_kept_at_ingestion(tmp_path):
    import datetime

    import openpyxl

    from remitwatch.dropbox_sync import ingest_new_files
    from remitwatch.ema_exports import export_created
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Service Date", "Primary Provider", "Claim ID", "CPT/Product", "Units", "Charges", "Activity Type", "Bill Item Identifier", "Payer", "Location"])
    ws.append(["2026-03-02", "Alpha, Ann", "CB0000001C017", "99213", 1, 150.0, "Charge", 7000001, "Blue Cross", "North Office"])
    wb.properties.created = datetime.datetime(2026, 3, 30, 14, 5, 6)
    p = tmp_path / "Encounter & Charge Lines.xlsx"
    wb.save(p)
    assert export_created(str(p)) == "2026-03-30T14:05:06Z" and export_created(str(tmp_path / "missing.xlsx")) is None
    (tmp_path / "not-a-workbook.xlsx").write_bytes(b"nothing")
    assert export_created(str(tmp_path / "not-a-workbook.xlsx")) is None
    store = Store(str(tmp_path / "s.sqlite"))
    assert ingest_new_files(store, [str(p)], log=lambda *a: None)["ema"] == 1
    assert [tuple(r) for r in store.conn.execute("SELECT source_name, family, created_at, rows FROM ema_sources")] == \
        [("Encounter & Charge Lines.xlsx", "charge_lines", "2026-03-30T14:05:06Z", 1)]
    activity.build_activity(store, log=lambda *a: None)
    assert store.conn.execute("SELECT pulled FROM provider_ema_days").fetchone()[0] == "2026-03-30"
    store.close()


def test_tables_left_behind_by_a_run_without_time_are_rebuilt_by_the_next(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    store.ingest_835_paths(write_files(tmp_path))
    assert activity.refresh(store, log=lambda *a: None)["people"] == 2 and activity.stale(store.conn) is False
    activity.mark_pending(store.conn)                   # what a detection run does when it changes the store
    store.conn.commit()
    assert activity.stale(store.conn) is True
    activity.refresh(store, log=lambda *a: None)
    assert activity.stale(store.conn) is False
    store.close()


def test_the_provider_figures_can_be_kept_for_the_owner(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from remitwatch import api, cloud
    db = str(tmp_path / "s.sqlite")
    store = Store(db)
    denied = [("20260324", "ANTHEM", [clp(301 + i, svc("99214", "200.00", "0.00", None, "20260302", lcn=f"L30{i}", cas="CAS*CO*50*200.00~")) for i in range(2)])]
    store.ingest_835_paths(write_files(tmp_path, FILES + denied))          # two denied visits, so there are findings to break down
    store.close()
    run_detection(db)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "_FAILED", {})
    monkeypatch.setattr(api, "_OWNER_FAILED", {})
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    tc, login, key = TestClient(api.app), ("oda", PW), "a-second-long-password-9"
    owner = {"X-Owner-Password": key}
    routes = ("/api/activity/overview", "/api/activity/trend", "/api/activity/recent", f"/api/activity/provider/{ANN}")
    by_clinician = ("/api/breakdown?dim=provider&family=denial", f"/api/breakdown?dim=payer&family=denial&provider={ANN}", "/api/breakdown?dim=payer&dim2=provider&family=denial")
    lines = lambda path, **kw: [(r["lines"], r["rate"]) for r in tc.get(path, auth=login, **kw).json()["rows"]]      # noqa: E731

    # with no owner password, everyone who can sign in sees it all, and a header nobody asked for changes nothing
    assert tc.get("/api/meta", auth=login).json()["providers"] == {"locked": False}
    assert all(tc.get(r, auth=login).status_code == 200 for r in routes)
    assert all(tc.get(r, auth=login, headers={"X-Owner-Password": "anything"}).status_code == 200 for r in routes)
    open_lines = lines(by_clinician[0])
    assert open_lines and all(n for n, _rate in open_lines) and tc.get(by_clinician[0], auth=login).json()["rates_locked"] is False
    assert tc.get(by_clinician[1], auth=login).json()["total_lines"] == open_lines[0][0]         # a filter gives one clinician's lines as the total

    monkeypatch.setattr(api, "OWNER_PASSWORD", key)
    assert tc.get("/api/meta", auth=login).json()["providers"] == {"locked": True}
    # the login alone: every provider route refuses with 403 (never 401, which would make a browser drop its login) ...
    for r in routes:
        got = tc.get(r, auth=login)
        assert got.status_code == 403 and "owner's password" in got.json()["detail"] and "www-authenticate" not in got.headers, r
        assert tc.get(r).status_code == 401 and tc.get(r, headers=owner).status_code == 401, r        # and the owner's password alone is not a login
    # ... the rest of the site is as before, but lines billed per clinician, and the rate that gives them back, are left out
    assert tc.get("/api/summary", auth=login).status_code == 200 and tc.get("/api/coverage", auth=login).status_code == 200
    for path in by_clinician:
        res = tc.get(path, auth=login).json()
        assert res["rates_locked"] is True and res["total_lines"] is None and res["rows"] and all(r["lines"] is None and r["rate"] is None for r in res["rows"]), path
        assert all(r["n"] for r in res["rows"])                                    # the findings themselves stay
    by_payer = tc.get("/api/breakdown?dim=payer&family=denial", auth=login).json()
    assert by_payer["rates_locked"] is False and any(r["lines"] for r in by_payer["rows"])
    # grouped by something else, a cell that is four fifths one clinician's lines is that clinician's volume by another name:
    # Ann has 16 of the 19 lines billed by her provider type and every level 4 visit, so those two cells are left out too
    for path, shown in (("/api/breakdown?dim=tier&family=denial", 19), ("/api/breakdown?dim=billed_code&family=denial", 4)):
        res = tc.get(path, auth=login).json()
        assert res["rates_locked"] is True and [(r["lines"], r["rate"]) for r in res["rows"]] == [(None, None)] and res["total_lines"] == 26, path
        assert [r["lines"] for r in tc.get(path, auth=login, headers=owner).json()["rows"]] == [shown], path
    assert [r["lines"] for r in tc.get("/api/breakdown?dim=month&family=denial", auth=login).json()["rows"]] == [23]       # 16 of 23: not hers alone
    # with the owner's password beside the login everything opens; the page tries a password with one light call
    assert all(tc.get(r, auth=login, headers=owner).status_code == 200 for r in routes)
    assert tc.get("/api/activity/unlock", auth=login, headers=owner).json() == {"ok": True} and tc.get("/api/activity/unlock", auth=login).status_code == 403
    assert lines(by_clinician[0], headers=owner) == open_lines
    # asking without it is not a guess; a wrong one is, on any route, and ten of them close the owner's door only
    for _ in range(api.FAIL_LIMIT + 3):
        assert tc.get(routes[0], auth=login).status_code == 403
    assert tc.get(routes[0], auth=login, headers=owner).status_code == 200
    for i in range(api.FAIL_LIMIT):
        route = (routes[0], by_clinician[0])[i % 2]
        got = tc.get(route, auth=login, headers={"X-Owner-Password": f"guess-{i}"})
        assert (got.status_code == 403 and got.json()["detail"] == "That is not the owner's password.") if i % 2 == 0 else got.json()["rates_locked"], i
    assert tc.get(routes[0], auth=login, headers=owner).status_code == 429
    assert tc.get(by_clinician[0], auth=login, headers=owner).json()["rates_locked"] is True
    assert tc.get("/api/summary", auth=login).status_code == 200                   # the billers are not locked out by it
    monkeypatch.setattr(api, "_OWNER_FAILED", {})
    assert tc.get(routes[0], auth=login, headers=owner).status_code == 200
    # ten wrong logins do not stop the owner's password from being right afterwards, and the other way round
    assert api._FAILED == {}

    # an owner's password that cannot be used closes the figures to everyone and says why, in the API and on /setup alike
    for bad, why in (("short", "shorter than 12"), (PW, "the same as the dashboard password"), ("pässword-long-enough", "browser cannot send"),
                     ("a-second-long-password-9 ", "space at one end")):
        monkeypatch.setattr(api, "OWNER_PASSWORD", bad)
        monkeypatch.setenv("REMITWATCH_OWNER_PASSWORD", bad)
        monkeypatch.setenv("REMITWATCH_PASSWORD", PW)
        for headers in ({}, {"X-Owner-Password": bad.encode("ascii", "ignore").decode()}):
            got = tc.get(routes[0], auth=login, headers=headers)        # the reason is on /setup, not handed to whoever asks
            assert got.status_code == 503 and "setup page" in got.json()["detail"] and why not in got.json()["detail"], (bad, got.status_code)
        assert tc.get(by_clinician[0], auth=login).json()["rates_locked"] is True
        assert tc.get("/api/summary", auth=login).status_code == 200
        check = next(c for c in cloud.setup_status(probe=False)["checks"] if c["key"] == "owner_password")
        assert check["ok"] is False and why in check["detail"], (bad, check)
    monkeypatch.setenv("REMITWATCH_OWNER_PASSWORD", key)
    assert next(c for c in cloud.setup_status(probe=False)["checks"] if c["key"] == "owner_password")["ok"] is True
    monkeypatch.delenv("REMITWATCH_OWNER_PASSWORD")
    assert next(c for c in cloud.setup_status(probe=False)["checks"] if c["key"] == "owner_password")["ok"] is None

    # a site with no dashboard password (a developer's machine) still asks for the owner's, and still slows guesses
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.setattr(api, "OWNER_PASSWORD", key)
    monkeypatch.setattr(api, "_OWNER_FAILED", {})
    assert tc.get("/api/summary").status_code == 200 and tc.get(routes[0]).status_code == 403 and tc.get(routes[0], headers=owner).status_code == 200
    for i in range(api.FAIL_LIMIT):
        assert tc.get(routes[0], headers={"X-Owner-Password": f"guess-{i}"}).status_code == 403
    assert tc.get(routes[0], headers=owner).status_code == 429


def test_several_readers_at_once_take_turns(tmp_path, monkeypatch):
    """The readers are slow when they interleave, so the API lets one run at a time."""
    import threading
    import time

    from fastapi.testclient import TestClient

    from remitwatch import api
    db = str(tmp_path / "s.sqlite")
    store = Store(db)
    store.ingest_835_paths(write_files(tmp_path))
    store.close()
    run_detection(db)
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", "")
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    inside, most = [0], [0]
    real = activity.overview

    def slow(conn, *args):
        inside[0] += 1
        most[0] = max(most[0], inside[0])
        time.sleep(0.05)
        try:
            return real(conn, *args)
        finally:
            inside[0] -= 1
    monkeypatch.setattr(activity, "overview", slow)
    tc = TestClient(api.app)
    threads = [threading.Thread(target=lambda: tc.get("/api/activity/overview")) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert most[0] == 1



def test_ema_patients_are_typed_like_remittance_patients_and_a_sliver_of_a_day_is_not_the_day(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    full, sliver = "Encounter & Charges week.xlsx", "Encounter & Charges one day.xlsx"
    rows, i = [], 0
    for n in range(1, 7):                               # six patients with a visit
        rows.append(dict(ema_row(i := i + 1, full, "2026-03-02", "Alpha, Ann", "99213", patient=f"P{n}", bill_no=n, created="2026-03-03"), transaction_date="2026-03-03"))
    for n, code in ((7, "99024"), (8, "96910"), (9, "88305")):          # a post-operative check, a light-box session, a slide: none is a patient seen
        rows.append(dict(ema_row(i := i + 1, full, "2026-03-02", "Alpha, Ann", code, patient=f"P{n}", bill_no=n, created="2026-03-03"), transaction_date="2026-03-03"))
    # a later export holding one late charge for that day (posted the same day it was pulled): it does not move the day's pull date
    rows.append(dict(ema_row(i := i + 1, sliver, "2026-03-02", "Alpha, Ann", "17110", patient="P1", bill_no=1, created="2026-03-20"), transaction_date="2026-03-20"))
    store.ingest_ema_rows(rows)
    store.record_export(full, "sha-a", "charge_lines", "2026-03-04T15:00:00Z", 9)
    store.record_export(sliver, "sha-b", "charge_lines", "2026-03-20T15:00:00Z", 1)
    activity.build_activity(store, log=lambda *a: None)
    store.conn.row_factory = sqlite3.Row
    d = dict(store.conn.execute("SELECT * FROM provider_ema_days").fetchone())
    assert (d["patients"], d["visits"], d["other_contacts"], d["pulled"]) == (6, 6, 3, "2026-03-04")
    store.close()


def test_the_build_does_not_need_the_indexes_the_cloud_store_leaves_out(tmp_path):
    """The store a serverless run holds in memory has no secondary indexes. A query that looks rows up one by one
    without one scans a whole table per row: fine on a small fixture, minutes on the archive. The work the build does
    is counted in SQLite's own steps, on a store with indexes and on one without, and must be about the same."""
    files = [("20260320", "KY MEDICARE", [
        clp(n, svc("99213", "150.00", "0.00", None, "20260302", lcn=f"L{n}", cas="CAS*CO*45*60.00~CAS*PR*1*90.00~"), cfi="MB") for n in range(1, 401)])]

    def steps(compact: bool) -> tuple[int, int]:
        folder = tmp_path / ("compact" if compact else "indexed")
        folder.mkdir()
        store = Store(":memory:", compact=compact)
        assert store.ingest_835_paths(write_files(folder, [SEED] + files))["errors"] == 0
        n = [0]
        store.conn.set_progress_handler(lambda: n.__setitem__(0, n[0] + 1) or 0, 1000)
        activity.build_activity(store, log=lambda *a: None)
        store.conn.set_progress_handler(None, 0)
        allowed = store.conn.execute("SELECT SUM(allowed_cents) FROM provider_days").fetchone()[0]
        store.close()
        return n[0], allowed
    with_indexes, allowed_a = steps(False)
    without, allowed_b = steps(True)
    assert allowed_a == allowed_b and allowed_a >= 400 * 9000           # the patient's share was found either way
    assert without < 3 * with_indexes + 200, (with_indexes, without)
