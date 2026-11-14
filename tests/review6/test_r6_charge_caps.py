"""Review 6, item 3: CHARGE_BELOW_RATE (detect.Detector.emit_charge_caps) and how the pages treat it. Invented data only."""
from __future__ import annotations

import json
import sqlite3

from tests.review6.r6helpers import build_remits, clm, findings, sv
from remitwatch.detect import Detector, ensure_schema  # noqa: E402


def paid_at_rate(i: int, code: str, charge: str, allowed: str, dos: str = "20260105", **kw) -> str:
    co = f"{float(charge) - float(allowed):.2f}"
    return clm(f"AB{i:07d}C017", "1", charge, allowed, stmt=dos, lines=sv(code, charge, allowed, allowed, cas=f"CAS*CO*45*{co}~", dos=dos), **kw)


def paid_at_charge(i: int, code: str, charge: str, dos: str = "20260105", status: str = "1", **kw) -> str:
    return clm(f"AB{i:07d}C017", status, charge, charge, stmt=dos, lines=sv(code, charge, charge, charge, dos=dos), **kw)


def test_capped_lines_are_also_open_appeals_so_their_money_is_counted_twice(tmp_path):
    """A line paid exactly its charge cannot be appealed (the note on CHARGE_BELOW_RATE says so), yet the same line is
    still an UNDERPAID_VS_OWN_RATE finding with action "appeal" whenever the payer's usual rate is above the charge:
    the rule adds a second finding over the same dollars instead of taking them out of the appeals. The live release
    already had the false appeal; this release adds the same money again to the contract total."""
    rate = [paid_at_rate(i, "88305", "200.00", "150.00") for i in range(1, 26)]                     # usual $150 on 25 lines
    capped = [paid_at_charge(100 + i, "88305", f"{100 + 5 * i}.00") for i in range(5)]               # paid the charge: $100..$120
    store = build_remits(tmp_path, [("20260210", rate + capped)])
    Detector(store, run_id="r6").run()
    (cap,) = findings(store, "CHARGE_BELOW_RATE")
    assert cap["shortfall_cents"] == 20000 and json.loads(cap["evidence"])["lines"] == 5
    capped_keys = {r[0] for r in store.query("SELECT line_key FROM lines WHERE allowed_cents = charge_cents")}
    appeals = [f for f in findings(store, "UNDERPAID_VS_OWN_RATE") if f["line_key"] in capped_keys]
    assert not appeals, (f"{len(appeals)} lines paid exactly their charge are also appeals worth "
                         f"${sum(f['shortfall_cents'] for f in appeals) / 100:.2f}, on top of the ${cap['shortfall_cents'] / 100:.2f} CHARGE_BELOW_RATE finding")
    store.close()


def test_a_payment_taken_back_and_made_again_counts_twice(tmp_path):
    """Three services paid at a $60 charge, each reversed and paid again by the payer, are six 'capped lines' to the rule:
    it ignores the payment ledger (self.standing), so three services clear the five-line minimum (and the $100 floor)."""
    rate = [paid_at_rate(i, "88305", "150.00", "90.00") for i in range(1, 9)]
    first = [paid_at_charge(200 + i, "88305", "60.00") for i in range(3)]
    reversal = [clm(f"AB{200 + i:07d}C017", "22", "-60.00", "-60.00", stmt="20260105",
                    lines=sv("88305", "-60.00", "-60.00", "-60.00", units=-1, dos="20260105")) for i in range(3)]
    again = [paid_at_charge(200 + i, "88305", "60.00") for i in range(3)]
    store = build_remits(tmp_path, [("20260210", rate + first), ("20260310", reversal + again)])
    det = Detector(store, run_id="r6")
    det.run()
    standing_capped = sum(1 for l in det.lines if l.get("allowed_cents") == l.get("charge_cents") and (l.get("paid_cents") or 0) > 0
                          and l["line_key"] in det.standing)
    assert standing_capped == 3, standing_capped          # the ledger knows only three payments stand
    caps = findings(store, "CHARGE_BELOW_RATE")
    assert not caps, f"three services found as {json.loads(caps[0]['evidence'])['lines']} capped lines: {caps[0]['title']}"
    store.close()


def test_a_fee_already_raised_above_the_rate_is_still_a_fee_to_raise(tmp_path):
    """The charge for the code was $75 until March and $150 from April; the payer's rate is $90 all year. The lines before
    the change were paid the old charge, and the finding still says to raise the fee, which was already done. Nothing on
    it can be appealed or acted on, yet it stays open in the totals for good."""
    old = [paid_at_charge(300 + i, "88305", "75.00", dos=f"202601{10 + i:02d}") for i in range(8)]
    new = [paid_at_rate(400 + i, "88305", "150.00", "90.00", dos=f"202604{10 + i:02d}") for i in range(10)]
    store = build_remits(tmp_path, [("20260510", old + new)])
    Detector(store, run_id="r6").run()
    caps = findings(store, "CHARGE_BELOW_RATE")
    stale = [f for f in caps if "Raise your fee" in json.loads(f["evidence"])["note"]]
    assert not stale, (f"{stale[0]['title']}: the newest lines are already charged $150, above the $90 rate, "
                       f"yet the finding asks for a fee raise and adds ${stale[0]['shortfall_cents'] / 100:.2f} to the open totals")
    store.close()


def test_lines_the_rule_must_leave_alone_are_left_alone(tmp_path):
    """Fine: lines with more than one unit, a secondary payer's lines, lines with a pricing modifier, a token charge, and
    facility lines measured against office lines all stay out."""
    rate = [paid_at_rate(i, "88305", "150.00", "90.00") for i in range(1, 9)]
    multi = [clm(f"AB{500 + i:07d}C017", "1", "150.00", "150.00", stmt="20260105",
                 lines=sv("88305", "150.00", "150.00", "150.00", units=2)) for i in range(6)]                 # two units at $75 each
    second = [paid_at_charge(600 + i, "88305", "75.00", status="2") for i in range(6)]                       # a secondary payer
    modded = [clm(f"AB{700 + i:07d}C017", "1", "75.00", "75.00", stmt="20260105",
                  lines=sv("88305", "75.00", "75.00", "75.00", mods="26")) for i in range(6)]                 # professional component
    token = [paid_at_charge(800 + i, "88305", "4.00") for i in range(30)]                                     # $4: a token charge
    facility = [paid_at_charge(900 + i, "88305", "75.00", pos="22") for i in range(6)]                       # no facility-rate lines
    store = build_remits(tmp_path, [("20260210", rate + multi + second + modded + token + facility)])
    Detector(store, run_id="r6").run()
    assert findings(store, "CHARGE_BELOW_RATE") == []
    store.close()


def test_home_screen_fee_levers_leave_out_dismissed_findings_and_show_their_years(tmp_path):
    """insights.fee_levers (b8c3146) summed every CHARGE_BELOW_RATE finding, a dismissed one included. Now a let-go
    finding is left out, and each code carries the years its open findings cover (the page prints them)."""
    from remitwatch import insights
    conn = sqlite3.connect(str(tmp_path / "f.sqlite"))
    ensure_schema(conn)
    for fid, year, status, cents in (("a", "2026", "open", 10000), ("b", "2023", "open", 50000), ("c", "2026", "dismissed", 70000)):
        conn.execute("INSERT INTO findings (finding_id, signal, category, actionable, status, year, shortfall_cents, payer_group, procedure_code, title) "
                     "VALUES (?, 'CHARGE_BELOW_RATE', 'contract', 'raise_fee', ?, ?, ?, 'Anthem / BCBS', '88305', 't')", (fid, status, year, cents))
    conn.commit()
    out = insights.fee_levers(conn)
    assert out["charge_cap_cents"] == 60000, f"fee lever total {out['charge_cap_cents']} includes a dismissed finding"
    (code,) = out["charge_caps"]
    assert (code["code"], code["first_year"], code["year"], code["cents"]) == ("88305", "2023", "2026", 60000)
    conn.close()
