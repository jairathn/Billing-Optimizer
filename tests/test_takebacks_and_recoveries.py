"""Money a payer took back, reversed or paid later, counted right (trace findings T-I1, T-I5, T-C5 and T-C6).

T-I1: a recoupment (PLB WO, or FB) whose reference holds the claim id among other text (the payer's own claim number, a
letter, a date) takes back that claim's payment, and the closures resting on that payment reopen; a balance the payer
carried forward and recovered later is taken once. T-I5: a reversal of a version that is not on file cancels only a
payment of its own amount, and is counted for the Data tab. T-C5/T-C6: a closure records the money its own units brought
after the denial, less what stood before it, and names the remittances that paid them.

Everything here is invented: names, member numbers, claim ids, payer claim numbers, traces and amounts. Each test runs on
both kinds of store: the server's file and the serverless image held in memory."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.review3.r3helpers import clm, sv, visit_denied_line, visit_paid, visit_reversed  # noqa: E402
from tests.test_features import clean_visits  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

OTHER = "ASH*RO****MI*W55500304"


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


class World:
    """One test store of either kind, fed remittance files one at a time. The compact kind is the serverless image, held in
    memory: it stays open from file to file."""

    def __init__(self, tmp_path, kind: str):
        self.tmp, self.n = tmp_path, 0
        self.store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)

    def remit(self, claims: list[str], pay_date: str, plb: str = "", payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
        self.n += 1
        p = self.tmp / f"r{self.n:04d}.RMT"
        p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{self.n:09d}",
                            pay_date=pay_date, plb=plb))
        self.store.ingest_835_paths([str(p)])

    def run(self, run_id: str) -> Detector:
        det = Detector(self.store, run_id=run_id)
        det.run()
        return det

    def items(self, where: str = "1=1") -> dict:
        return {f"{r['patient_control_number']}:{r['procedure_code'] or ''}:{r['signal']}": r for r in self.rows(where)}

    def rows(self, where: str = "1=1") -> list[dict]:
        return [dict(r) for r in self.store.query(f"SELECT * FROM findings WHERE {where} ORDER BY COALESCE(modifiers, '')")]

    def events(self, fid: str) -> list[tuple]:
        return [(r["kind"], r["from_status"], r["to_status"]) for r in
                self.store.query("SELECT kind, from_status, to_status FROM finding_events WHERE finding_id=? ORDER BY id", (fid,))]


def standing(det: Detector, pcn: str) -> int:
    return sum(det.standing.get(l["line_key"], 0) for l in det.lines if l["patient_control_number"] == pcn)


def denied_then_paid(w: World) -> None:
    """Bill 300: C017's visit is denied (missing information) on 2026-04-15; the corrected claim C024 is paid $117.60 on
    2026-05-20 (payer claim number PC300B), which closes the denial."""
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line("CB0000300C017")], "20260415")
    w.remit([visit_paid("CB0000300C024", payer_ctl="PC300B")], "20260520")


# --------------------------------------------------------------------------- T-I1: the claim id inside the reference
@pytest.mark.parametrize("reference", [
    "WO:QCB0000300C024/77700000000012",                 # a letter, the claim id, the payer's claim number
    "WO:123456789012345    CB0000300C024",             # the payer's internal number, then the claim id
    "WO:87654321 CB0000300C024",                        # a short number, then the claim id
    "WO:PC300B-CB0000300C024-0001-01-01",               # the payer's claim number, the claim id and a date
    "WO:CB0000300C024                      -12A345678901",
    "FB:1234567890 CB0000300C024",                      # a balance withheld (FB) naming the claim the same way
])
def test_a_recoupment_whose_reference_holds_the_claim_id_takes_the_payment_back_and_reopens_its_closure(tmp_path, kind, reference):
    w = World(tmp_path, kind)
    denied_then_paid(w)
    w.run("r1")
    item = w.items()["CB0000300C017:99214:DENIED_MISSING_INFO"]
    assert item["status"] == "resolved" and item["recovered_cents"] == 11760
    # 2026-08-03: the payer recoups the whole payment from a check that pays another patient
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", plb=f"PLB*1777777772*20261231*{reference}*117.60~")
    det = w.run("r2")
    assert standing(det, "CB0000300C024") == 0, f"the recoupment {reference.split(':')[0]} naming C024 inside its reference was not applied"
    item = w.items()["CB0000300C017:99214:DENIED_MISSING_INFO"]
    assert item["status"] == "open" and item["auto_note"] is None and item["recovered_cents"] is None, \
        f"still closed on a payment the payer took back: {item['auto_note']}"
    assert w.events(item["finding_id"])[-1] == ("reopened", "resolved", "open")
    w.store.close()


def test_a_recoupment_from_the_same_payer_under_another_payer_id_takes_the_payment_back(tmp_path, kind):
    """C024 was paid by ANTHEM under one payer id; the recoupment naming C024 comes on a remittance from ANTHEM under
    another of its payer ids. It is the same payer: the payment is taken back. Another payer's recoupment naming the claim
    takes nothing."""
    w = World(tmp_path, kind)
    denied_then_paid(w)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", payer="BRIGHTPATH HEALTH", payer_id="BRGHT",
            plb="PLB*1777777772*20261231*WO:QCB0000300C024/77700000000012*117.60~")
    det = w.run("r1")
    assert standing(det, "CB0000300C024") == 11760, "another payer's recoupment took the claim's payment"
    w.remit([visit_paid("CB0000901C017", who=OTHER, payer_ctl="PC901")], "20260810", payer_id="00660",
            plb="PLB*1777777772*20261231*WO:QCB0000300C024/77700000000012*117.60~")
    det = w.run("r2")
    assert standing(det, "CB0000300C024") == 0, "the payer's recoupment under its other payer id was not applied"
    assert w.items()["CB0000300C017:99214:DENIED_MISSING_INFO"]["status"] == "open"
    w.store.close()


def test_a_recoupment_from_the_same_payer_id_under_another_name_takes_the_payment_back(tmp_path, kind):
    """C024 was paid by ANTHEM; the recoupment naming C024 comes under the same payer id with another name (its plan's).
    Same payer: the payment is taken back."""
    w = World(tmp_path, kind)
    denied_then_paid(w)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803", payer="ANTHEM CARE PLAN",
            plb="PLB*1777777772*20261231*WO:QCB0000300C024/77700000000012*117.60~")
    det = w.run("r1")
    assert standing(det, "CB0000300C024") == 0, "the payer's recoupment under its plan's name was not applied"
    w.store.close()


def test_a_balance_carried_under_one_payer_id_and_recovered_under_another_is_taken_once(tmp_path, kind):
    """2026-06-10: the payer reverses C024's $117.60 and carries $27.60 forward naming C024 (FB, negative) under one payer
    id; 2026-06-12 it pays C024 again, $90.00, under another of its ids; 2026-06-24 it recovers the $27.60 naming C024 (WO)
    under that other id. One payer, one balance: the re-payment stands."""
    w = World(tmp_path, kind)
    denied_then_paid(w)
    w.remit([visit_reversed("CB0000300C024", payer_ctl="PC300B")], "20260610", plb="PLB*1777777772*20261231*FB:123456789012345    CB0000300C024*-27.60~")
    w.remit([visit_paid("CB0000300C024", paid="90.00", payer_ctl="PC300C")], "20260612", payer_id="00660")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260624", payer_id="00660",
            plb="PLB*1777777772*20261231*WO:123456789012345    CB0000300C024*27.60~")
    det = w.run("r1")
    assert standing(det, "CB0000300C024") == 9000, "the recovery of the carried balance took the re-payment under the other id"
    w.store.close()


@pytest.mark.parametrize("offset", ["CS", "72"])
def test_a_recoupment_the_same_remittance_offsets_under_the_claims_name_takes_nothing(tmp_path, kind, offset):
    """2026-08-03: the payer lists a recoupment of C024's $117.60 (WO) and, in the same remittance, an adjustment of
    -$117.60 naming the same claim (CS, or 72, an authorized return): the two cancel, no money was taken, the denial stays
    closed. A CS or 72 entry alone never starts a takeback."""
    w = World(tmp_path, kind)
    denied_then_paid(w)
    ref = "PC300B-CB0000300C024-0001-01-01"
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260803",
            plb=f"PLB*1777777772*20261231*WO:{ref}*117.60*{offset}:{ref}*-117.60~")
    det = w.run("r1")
    assert standing(det, "CB0000300C024") == 11760, "a recoupment its own remittance offset was taken back"
    assert w.items()["CB0000300C017:99214:DENIED_MISSING_INFO"]["status"] == "resolved"
    w.store.close()
    (tmp_path / "alone").mkdir()
    w = World(tmp_path / "alone", kind)
    denied_then_paid(w)
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260817", plb=f"PLB*1777777772*20261231*{offset}:{ref}*50.00~")
    det = w.run("r2")
    assert standing(det, "CB0000300C024") == 11760, f"a {offset} entry alone started a takeback"
    w.store.close()


def test_a_balance_carried_forward_under_the_claims_name_and_recovered_later_is_taken_once(tmp_path, kind):
    """2026-06-10: the payer reverses C024's $117.60 and pays it again at $90.00 in the same remittance; the $27.60 it is
    owed is carried forward (FB, negative) naming the claim, and recovered on 2026-06-24 (WO, positive) naming it the same
    way. The re-payment stands: the two entries are one balance, which the reversal already took."""
    w = World(tmp_path, kind)
    denied_then_paid(w)
    w.remit([visit_reversed("CB0000300C024", payer_ctl="PC300B"), visit_paid("CB0000300C024", paid="90.00", payer_ctl="PC300C")], "20260610",
            plb="PLB*1777777772*20261231*FB:123456789012345    CB0000300C024*-27.60~")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260624",
            plb="PLB*1777777772*20261231*WO:123456789012345    CB0000300C024*27.60~")
    det = w.run("r1")
    assert standing(det, "CB0000300C024") == 9000
    w.store.close()


def test_a_reversals_balance_recovered_under_the_claims_name_is_not_taken_again(tmp_path, kind):
    """2026-06-10: the payer reverses C024's $117.60 and pays it again at $40.00; the $77.60 it is owed is carried forward
    under its own number, not the claim's (FB, negative), and recovered on 2026-06-17 under the claim's name (WO). The WO is
    that reversal's money: the $40.00 re-payment stands."""
    w = World(tmp_path, kind)
    denied_then_paid(w)
    w.remit([visit_reversed("CB0000300C024", payer_ctl="PC300B"), visit_paid("CB0000300C024", paid="40.00", payer_ctl="PC300C")], "20260610",
            plb="PLB*1777777772*20261231*FB:900000000000001*-77.60~")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260617",
            plb="PLB*1777777772*20261231*WO:PC300B-CB0000300C024-0001-01-01*77.60~")
    det = w.run("r1")
    assert standing(det, "CB0000300C024") == 4000, "the recovery of the reversal's balance was taken from the re-payment too"
    w.store.close()


def test_a_recoupment_after_a_reversal_whose_balance_was_settled_takes_the_repayment(tmp_path, kind):
    """2026-06-10: the payer reverses C024 alone and carries the $117.60 forward under the claim's name (WO, negative);
    2026-06-24 it pays C024 again and recovers the carried balance (WO, positive, same name). 2026-07-22 it recoups $117.60
    once more naming C024: that takes the re-payment. Nothing stands paid on the visit, and the denial reopens."""
    w = World(tmp_path, kind)
    denied_then_paid(w)
    w.run("r1")
    w.remit([visit_reversed("CB0000300C024", payer_ctl="PC300B")], "20260610",
            plb="PLB*1777777772*20261231*WO:87654321 CB0000300C024*-117.60~")
    w.remit([visit_paid("CB0000300C024", payer_ctl="PC300C")], "20260624", plb="PLB*1777777772*20261231*WO:87654321 CB0000300C024*117.60~")
    w.remit([visit_paid("CB0000900C017", who=OTHER, payer_ctl="PC900")], "20260722",
            plb="PLB*1777777772*20261231*WO:87654321 CB0000300C024*117.60~")
    det = w.run("r2")
    assert standing(det, "CB0000300C024") == 0, "the July recoupment of the re-payment was not applied"
    assert w.items()["CB0000300C017:99214:DENIED_MISSING_INFO"]["status"] == "open"
    w.store.close()


# --------------------------------------------------------------------------- T-I5: a reversal of a version not on file
def test_a_reversal_of_a_version_not_on_file_takes_nothing_from_another_payment_and_is_counted(tmp_path, kind):
    """C024 is paid $117.60 on 2026-05-20. On 2026-06-10 the payer reverses $80.00 of the visit: a version paid in a
    remittance that is not on file. The $117.60 stands, the denial stays closed, and the Data tab counts the reversal (one
    claim, $80.00): the service is later than the payer's first remittance on file, so that remittance is missing."""
    from remitwatch.insights import data_health
    w = World(tmp_path, kind)
    denied_then_paid(w)
    w.remit([visit_reversed("CB0000300C024", paid="80.00", payer_ctl="PC300X")], "20260610")
    det = w.run("r1")
    assert standing(det, "CB0000300C024") == 11760, "a reversal of $80.00 took money from the $117.60 payment"
    assert w.items()["CB0000300C017:99214:DENIED_MISSING_INFO"]["status"] == "resolved"
    health = {h["key"]: h for h in data_health(w.store.conn, "2026-06-10", "2026-06-12")}
    got = health.get("reversal_not_on_file")
    assert got and got["claims"] == 1 and got["cents"] == 8000 and not got["ok"], got
    w.store.close()


def test_a_reversal_of_a_payment_on_file_is_not_counted_as_missing(tmp_path, kind):
    from remitwatch.insights import data_health
    w = World(tmp_path, kind)
    denied_then_paid(w)
    w.remit([visit_reversed("CB0000300C024", payer_ctl="PC300B")], "20260610")
    det = w.run("r1")
    assert standing(det, "CB0000300C024") == 0
    assert "reversal_not_on_file" not in {h["key"] for h in data_health(w.store.conn, "2026-06-10", "2026-06-12")}
    w.store.close()


# --------------------------------------------------------------------------- T-C5 / T-C6: the recovery and its note
def two_specimens_denied(w: World) -> None:
    """Two specimens of 88305 on C017 (the second with modifier 59), both denied on 2026-04-15."""
    w.remit(clean_visits(1, 25, "88305", "150.00", "70.00"), "20260301")
    w.remit([clm("CB0000300C017", "1", "300.00", "0.00", sv("88305", "150.00", "0.00", 1, "CAS*CO*16*150.00~")
                 + sv("88305", "150.00", "0.00", 1, "CAS*CO*16*150.00~", mods="59"))], "20260415")


def paid_specimen(mods: str = "", paid: str = "70.00") -> str:
    co = f"{150 - float(paid):.2f}"
    return sv("88305", "150.00", paid, 1, f"CAS*CO*45*{co}~", allowed=paid, mods=mods)


def test_two_lines_of_one_code_paid_on_the_corrected_claim_each_record_their_own_recovery(tmp_path, kind):
    w = World(tmp_path, kind)
    two_specimens_denied(w)
    w.remit([clm("CB0000300C024", "1", "300.00", "140.00", paid_specimen() + paid_specimen("59"), payer_ctl="PC301")], "20260520")
    w.run("r1")
    rows = w.rows("category='denial' AND patient_control_number='CB0000300C017'")
    assert len(rows) == 2 and all(r["status"] == "resolved" for r in rows), [(r["status"], r["auto_note"]) for r in rows]
    assert sorted(r["recovered_cents"] for r in rows) == [7000, 7000], "the second line paid on the same claim records $0"
    w.store.close()


def test_each_closure_names_the_remittance_that_paid_its_own_unit(tmp_path, kind):
    """The first specimen is paid on 2026-05-20, the second on 2026-06-17 (another remittance). Each item records $70.00
    and names the remittance that paid its unit: the second, not the earliest of the two."""
    w = World(tmp_path, kind)
    two_specimens_denied(w)
    w.remit([clm("CB0000300C024", "1", "150.00", "70.00", paid_specimen(), payer_ctl="PC301")], "20260520")
    w.remit([clm("CB0000300C031", "1", "150.00", "70.00", paid_specimen("59"), payer_ctl="PC302")], "20260617")
    w.run("r1")
    rows = w.rows("category='denial' AND patient_control_number='CB0000300C017'")
    assert [r["status"] for r in rows] == ["resolved", "resolved"]
    assert [r["recovered_cents"] for r in rows] == [7000, 7000], [r["recovered_cents"] for r in rows]
    dates = [r["recovered_date"] for r in rows]
    assert sorted(dates) == ["2026-05-20", "2026-06-17"], dates
    assert sorted("2026-06-17" in (r["auto_note"] or "") for r in rows) == [False, True], [r["auto_note"] for r in rows]
    assert sorted(json.loads(r["evidence"])["paid_later"]["payment_date"] for r in rows) == ["2026-05-20", "2026-06-17"]
    w.store.close()


def test_one_payment_is_counted_as_recovered_once_when_two_denials_of_the_unit_close_on_it(tmp_path, kind):
    """The visit is denied on C017 (2026-04-15) and again on the corrected C024 (2026-05-01); C031 is paid on 2026-06-10.
    Both items close on that payment, and its $117.60 is counted as recovered once."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([visit_denied_line("CB0000300C017")], "20260415")
    w.remit([visit_denied_line("CB0000300C024", payer_ctl="PC2")], "20260501")
    w.remit([visit_paid("CB0000300C031", payer_ctl="PC3")], "20260610")
    w.run("r1")
    rows = w.rows("category='denial'")
    assert [r["status"] for r in rows] == ["resolved", "resolved"], [(r["patient_control_number"], r["status"]) for r in rows]
    assert sorted(r["recovered_cents"] for r in rows) == [0, 11760], [r["recovered_cents"] for r in rows]
    assert sum(bool(json.loads(r["evidence"]).get("recovery_counted_elsewhere")) for r in rows) == 1
    w.store.close()


def test_a_recovery_counts_the_items_own_unit_not_another_unit_paid_again(tmp_path, kind):
    """C017: the first specimen is paid $70.00 and the second denied (2026-04-15). On 2026-05-20 the payer reverses the
    claim and pays it again: $70.00 for the first specimen, $55.00 for the second. The second's item recovered $55.00: the
    first specimen's $70.00 stood before the denial."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "88305", "150.00", "70.00"), "20260301")
    first = clm("CB0000300C017", "1", "300.00", "70.00", paid_specimen() + sv("88305", "150.00", "0.00", 1, "CAS*CO*16*150.00~", mods="59"))
    w.remit([first], "20260415")
    back = clm("CB0000300C017", "22", "-300.00", "-70.00", sv("88305", "-150.00", "-70.00", -1, "CAS*CO*45*-80.00~")
               + sv("88305", "-150.00", "0.00", -1, "CAS*CO*16*-150.00~", mods="59"))
    again = clm("CB0000300C017", "1", "300.00", "125.00", paid_specimen() + paid_specimen("59", "55.00"), payer_ctl="PC2")
    w.remit([back, again], "20260520")
    w.run("r1")
    rows = w.rows("category='denial' AND patient_control_number='CB0000300C017' AND modifiers='59'")
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "resolved" and r["recovered_cents"] == 5500, (r["status"], r["recovered_cents"], r["auto_note"])
    w.store.close()


def test_a_downcode_paid_again_in_full_records_what_it_brought_beyond_the_first_payment(tmp_path, kind):
    """99214 paid $76.15 with the level cut (CO-186), 2026-04-15. On 2026-06-10 the payer reverses that payment and pays
    $130.00: the item recovered $53.85, the payment less what stood before it."""
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60"), "20260301")
    w.remit([clm("CB0000202C017", "1", "223.65", "76.15", sv("99214", "223.65", "76.15", 1, "CAS*CO*186*41.45~CAS*CO*45*106.05~",
                                                              allowed="76.15"))], "20260415")
    w.remit([clm("CB0000202C017", "22", "-223.65", "-76.15", sv("99214", "-223.65", "-76.15", -1, "CAS*CO*186*-41.45~CAS*CO*45*-106.05~")),
             clm("CB0000202C017", "1", "223.65", "130.00", sv("99214", "223.65", "130.00", 1, "CAS*CO*45*93.65~", allowed="130.00"),
                 payer_ctl="PC2")], "20260610")
    w.run("r1")
    r = w.items("signal='DOWNCODE_LEVEL_REDUCED'")["CB0000202C017:99214:DOWNCODE_LEVEL_REDUCED"]
    assert r["status"] == "resolved" and r["recovered_cents"] == 5385, (r["recovered_cents"], r["auto_note"])
    w.store.close()


def test_a_whole_claim_denial_paid_later_records_every_listed_codes_payment(tmp_path, kind):
    """C017 is denied whole (CO-27) for 99214 and 17000; a biller works it; C024 pays both codes on 2026-06-10. The closed
    item records both payments ($117.60 and $60.00), not one of them."""
    from remitwatch.actions import apply_status
    w = World(tmp_path, kind)
    w.remit(clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(101, 25, "17000", "150.00", "60.00"), "20260301")
    whole = clm("CB0000300C017", "4", "373.65", "0.00", sv("99214", "223.65", "0.00", 1) + sv("17000", "150.00", "0.00", 1),
                claim_cas="CAS*CO*27*373.65~")
    w.remit([whole], "20260415")
    w.run("r1")
    fid = w.items()["CB0000300C017::CLAIM_DENIED_UNPAID"]["finding_id"]
    apply_status(w.store.conn, fid, "in_progress", "Called the payer: eligibility fixed, sent again", actor="biller")
    w.store.conn.commit()
    w.remit([clm("CB0000300C024", "1", "373.65", "177.60", sv("99214", "223.65", "117.60", 1, "CAS*CO*45*106.05~", allowed="117.60")
                 + sv("17000", "150.00", "60.00", 1, "CAS*CO*45*90.00~", allowed="60.00"), payer_ctl="PC301")], "20260610")
    w.run("r2")
    r = w.items()["CB0000300C017::CLAIM_DENIED_UNPAID"]
    assert r["status"] == "resolved" and r["recovered_cents"] == 17760, (r["recovered_cents"], r["auto_note"])
    w.store.close()
