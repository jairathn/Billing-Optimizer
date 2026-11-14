"""Review 6, item 1: the corrected-claim clock (codes.CORRECTED_RULES, codes.corrected_rule, the new branch in
detect.Detector._emit). Invented data only."""
from __future__ import annotations

import json

from tests.review6.r6helpers import build_remits, clm, findings, sv
from remitwatch.appeals import build_packet  # noqa: E402
from remitwatch.codes import corrected_rule  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402


# --------------------------------------------------------------------------- start date

def test_whole_claim_denial_with_no_statement_date_starts_a_date_of_service_clock_at_the_remittance(tmp_path):
    """Anthem Medicare Advantage counts a corrected claim from the date of service. A whole claim denied for missing
    information (CO-16) whose remittance carries the service date on its line (DTM*472) but no claim statement date
    (DTM*232), which is how professional remittances usually look, gets its clock started at the remittance instead:
    the date shown is later than the payer's limit by the time the payer took to deny."""
    c = clm("AB0000001C017", "4", "150.00", "0.00", cfi="16", stmt=None, claim_extra="CAS*CO*16*150.00~",
            lines=sv("99213", "150.00", "0.00", dos="20260105", rem="LQ*HE*N290~"))
    store = build_remits(tmp_path, [("20260310", [c])], payer="ANTHEM")
    Detector(store, run_id="r6").run()
    (f,) = findings(store, "CLAIM_DENIED_UNPAID")
    assert f["actionable"] == "correct_and_resubmit" and "date of service" in f["deadline_basis"]
    # the line says 2026-01-05; 365 days from it is 2027-01-05
    assert f["appeal_deadline"] == "2027-01-05", (
        f"deadline {f['appeal_deadline']} is 365 days from the remittance (2026-03-10), not from the date of service; "
        f"the finding shows no date of service at all: {f['service_date']!r}")
    store.close()


def test_undated_line_of_a_multi_day_claim_starts_a_date_of_service_clock_at_the_remittance(tmp_path):
    """A line reported with a service period (DTM*150/151) over several days has no service date of its own, and
    l.get('statement_from') is never set on a Line, so the clock falls to the remittance date. The earliest date of the
    period is the conservative start."""
    c = clm("AB0000002C017", "1", "150.00", "0.00", cfi="16", stmt=("20260105", "20260107"),
            lines=sv("99213", "150.00", "0.00", period=("20260105", "20260107"), cas="CAS*CO*16*150.00~", rem="LQ*HE*N822~"))
    store = build_remits(tmp_path, [("20260310", [c])], payer="ANTHEM")
    Detector(store, run_id="r6").run()
    (f,) = findings(store, "DENIED_MISSING_MODIFIER")
    assert f["payer_group"] == "Anthem / BCBS Medicare Advantage" and "date of service" in f["deadline_basis"]
    assert f["appeal_deadline"] == "2027-01-05", f"deadline {f['appeal_deadline']}: the clock started at the remittance date"
    store.close()


# --------------------------------------------------------------------------- MA130

def test_medicare_ma130_reported_at_claim_level_is_missed_by_line_findings(tmp_path):
    """Medicare reports MA130 (unprocessable, no appeal rights, send a new claim) in the claim's MOA segment as often as
    on the line. Line findings pass only the line's own remarks to corrected_rule, so a claim-level MA130 falls to the
    reopening rule: the date is a year from the remittance instead of a year from the date of service, and the label
    tells the biller to ask for a reopening, which an unprocessable claim does not have."""
    c = clm("AB0000003C017", "1", "150.00", "0.00", cfi="MB", stmt="20260105", moa="MA130",
            lines=sv("99213", "150.00", "0.00", dos="20260105", cas="CAS*CO*16*150.00~", rem="LQ*HE*N290~"))
    store = build_remits(tmp_path, [("20260210", [c])], payer="KY MEDICARE", payer_id="15004")
    Detector(store, run_id="r6").run()
    (f,) = findings(store, "DENIED_MISSING_INFO")
    assert f["actionable"] == "correct_and_resubmit" and f["payer_group"] == "Medicare KY (CGS J15)"
    assert "MA130" in f["deadline_basis"] and f["appeal_deadline"] == "2027-01-05", (
        f"claim-level MA130 ignored: {f['appeal_deadline']} / {f['deadline_basis'][:90]}")
    store.close()


def test_medicare_ma130_on_the_line_or_comma_joined_is_matched(tmp_path):
    """Fine: MA130 among the line's remarks (stored comma-joined, e.g. 'N822,MA130') picks the date-of-service rule."""
    assert corrected_rule("Medicare KY (CGS J15)", "medicare", "N822,MA130")["clock"] == "dos"
    assert corrected_rule("Railroad Medicare", "medicare", "MA130")["clock"] == "dos"
    assert corrected_rule("Medicare Supplement carriers", "supplement", "MA130") is None
    c = clm("AB0000004C017", "1", "150.00", "0.00", cfi="MB", stmt="20260105",
            lines=sv("99213", "150.00", "0.00", dos="20260105", cas="CAS*CO*16*150.00~", rem="LQ*HE*N822~LQ*HE*MA130~"))
    store = build_remits(tmp_path, [("20260210", [c])], payer="KY MEDICARE", payer_id="15004")
    Detector(store, run_id="r6").run()
    (f,) = findings(store, "DENIED_MISSING_MODIFIER")
    assert f["rarc"] == "N822,MA130" and f["appeal_deadline"] == "2027-01-05" and "MA130" in f["deadline_basis"]
    store.close()


def test_medicare_documentation_denial_is_not_a_clerical_reopening(tmp_path):
    """The reopening clock is applied to every Medicare fix finding. A line denied because documentation is missing or
    deficient (CO-250 with N463 'missing support data', likewise CO-226/251/252) is not a clerical error: a reopening
    takes no documentation (CGS reopening job aid, December 2025; myCGS: 'Documentation is not reviewed'), so the
    remedy is a redetermination with the documents, 120 days from the remittance, which is what the live release
    showed. The new date is 245 days later than that window."""
    c = clm("AB0000006C017", "1", "150.00", "0.00", cfi="MB", stmt="20260105",
            lines=sv("11102", "150.00", "0.00", dos="20260105", cas="CAS*CO*250*150.00~", rem="LQ*HE*N463~"))
    store = build_remits(tmp_path, [("20260210", [c])], payer="KY MEDICARE", payer_id="15004")
    Detector(store, run_id="r6").run()
    (f,) = findings(store, "DENIED_MISSING_INFO")
    assert f["actionable"] == "correct_and_resubmit" and f["carc"] == "250"
    assert f["appeal_deadline"] <= "2026-06-10", (
        f"documentation denial dated {f['appeal_deadline']} as: {f['deadline_basis'][:110]}")
    store.close()


# --------------------------------------------------------------------------- the packet for a Medicare fix

def test_medicare_fix_packet_says_what_its_deadline_says(tmp_path):
    """The finding's date is now a reopening (one year from the remittance) and its basis says Medicare Part B takes no
    corrected claims. The packet built for the same finding still asks for a redetermination, cites '42 CFR 405.940 ...
    submitted within 120 days', and its instructions say to submit a corrected claim with frequency code 7. Used after
    day 120, which the date now invites, the letter states something untrue and the request is untimely."""
    c = clm("AB0000005C017", "1", "150.00", "0.00", cfi="MB", stmt="20260105",
            lines=sv("99213", "150.00", "0.00", dos="20260105", cas="CAS*CO*16*150.00~", rem="LQ*HE*N822~"))
    store = build_remits(tmp_path, [("20260210", [c])], payer="KY MEDICARE", payer_id="15004")
    Detector(store, run_id="r6").run()
    (f,) = findings(store, "DENIED_MISSING_MODIFIER")
    assert f["appeal_deadline"] == "2027-02-10" and "reopening" in f["deadline_basis"]
    f["evidence"] = json.loads(f["evidence"] or "{}")
    p = build_packet({"finding": f, "claim": {}, "remit": {}})
    contradictions = []
    if "redetermination" in p["process"]:
        contradictions.append("process: " + p["process"])
    if "within 120 days" in p["letter"]:
        contradictions.append("letter cites a 120-day redetermination")
    if "corrected claim" in (p["corrected_claim_instructions"] or "") and "frequency code 7" in p["corrected_claim_instructions"]:
        contradictions.append("instructions: " + p["corrected_claim_instructions"])
    assert not contradictions, f"deadline {p['deadline']} ({p['deadline_basis'][:70]}...) but " + " | ".join(contradictions)
    store.close()
