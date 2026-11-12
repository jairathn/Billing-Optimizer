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

ANN, BOB, GROUP = "1111111111", "2222222222", "1821130295"      # a PA, a physician, and the group NPI the fixture pays
PW = "correct-horse-battery-7"


def bill(n: int) -> str:
    return f"CB{n:07d}"


def clp(n: int, lines: str, *, status: str = "1", who: str = "ann", patient: tuple = None, cfi: str = "12", suffix: str = "C017") -> str:
    """One claim loop. `who` picks how the payer named the rendering provider."""
    last, first = patient or (f"PATIENT{n:03d}", "PAT")
    render = {
        "ann": f"NM1*82*1*ALPHA*ANN****XX*{ANN}~", "bob": f"NM1*82*1*BRAVO*BOB****XX*{BOB}~",
        "group_named_ann": f"NM1*82*1*ALPHA*ANN****XX*{GROUP}~", "group": f"NM1*82*2*TEST DERMATOLOGY ASSOCIATES*****XX*{GROUP}~",
        "name_only_ann": "NM1*82*1*ANN ALPHA PA~", "none": "",
    }[who]
    return f"CLP*{bill(n)}{suffix}*{status}*100.00*80.00*0.00*{cfi}*PC{n}{status}*11~NM1*QC*1*{last}*{first}****MI*M{n:06d}~{render}{lines}"


def svc(code: str, charge: str, paid: str, allowed: str | None, dos: str, *, lcn: str | None = None, units: int = 1, hpi: str | None = None, cas: str = "") -> str:
    out = f"SVC*HC:{code}*{charge}*{paid}**{units}~DTM*472*{dos}~{cas}"
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
                                                 "Q4316", "J3301", "96372", "96910", "G2211", "10060")] == \
        ["new_visit", "est_visit", "biopsy", "destruction_premalignant", "destruction_benign", "destruction_malignant", "mohs", "excision", "repair",
         "pathology", "radiation", "radiation", "skin_substitute", "drug", "injection", "phototherapy", "visit_addon", "other"]


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


def test_overview_trend_detail_and_the_months_that_are_complete(built):
    conn = built.conn
    ov = activity.overview(conn, "2026-03", "2026-04")
    ann = next(r for r in ov["providers"] if r["provider_id"] == ANN)
    assert ann["name"] == "Alpha, Ann" and ann["clinic_days"] == 2 and ann["days_any"] == 4 and ann["patients"] == 14
    assert ann["per_day"] == {"median": 6.0, "p25": 6.0, "p75": 6.0, "mean": 6.0, "max": 6}
    assert ann["new_visits"] == 1 and ann["levels"] == {"99203": 1, "99213": 11, "99214": 1} and ann["est_high_share"] == round(1 / 12, 4)
    assert ann["biopsied_per_100_patients"] == round(100 / 14, 1) and ann["lesions_per_biopsied_patient"] == 3.0
    assert ann["path_generated_cents"] == 18000 and ann["allowed_per_patient"] == round(ann["clinic_allowed_cents"] / 14)
    assert [f["family"] for f in ann["families"]][:3] == ["est_visit", "biopsy", "new_visit"]          # largest allowed first
    assert [r["provider_id"] for r in ov["unassigned"]] == ["group"] and ov["unassigned_share"] == round(1 / 18, 4)
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
    assert days[("Bravo, Bob", "2026-03-02")]["patients"] == 0 and days[("Bravo, Bob", "2026-03-02")]["path_only"] == 1
    late = days[("Alpha, Ann", "2026-04-10")]
    assert (late["patients"], late["insured_patients"], late["loose_lines"], late["pulled"]) == (7, 6, 1, "2026-04-10")

    rc = activity.recent(conn)
    assert rc["ema"]["exports"] == 2 and rc["ema"]["exports_with_bill_dates"] == 1 and rc["ema"]["last_bill_created"] == "2026-03-30"
    ann = next(p for p in rc["providers"] if p["provider"] == "Alpha, Ann")
    # nine in ten of her bills exist within six days. 2 to 5 March were pulled almost four weeks later and count;
    # 27 March (pulled three days later) and 10 April (pulled the same day) wait instead of being read as light days.
    assert ann["lag"] == {"median": 3, "p75": 5, "p90": 6, "bills": 23, "basis": "every bill in the exports"} and ann["hold_days"] == 6
    assert ann["clinic_days"] == 4 and ann["days_waiting"] == 2 and ann["patients_per_day"] == 5.5 and ann["insured_share"] == round(21 / 22, 4)
    assert ann["raw_days"] == 6 and ann["raw_patients_per_day"] == 5.0 and ann["new_per_day"] == round(1 / 4, 1)
    assert ann["remit_patients_per_day"] == round((6 + 6 + 1 + 0) / 4, 1)
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
