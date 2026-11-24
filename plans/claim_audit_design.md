# Working every claim: the independent audit (next release's gate)

The owner's bar (2026-11-23): before the detection release, every claim is worked and traced; not one item a biller opens
may turn out to be already worked or already paid, no money the practice is owed may be missing from the list, and nothing
may be closed that should not be. Every failure is catalogued, traced to its cause in the code, fixed with a failing test
first, and the audit is run again until it finds none.

## The auditor is not the detector

A second, independent program decides, from the data alone, what is true of every unit of service; RemitWatch's items are
then compared with that truth. Written apart from detect.py (no shared helpers beyond the parsers), so a mistake in the
detector is not repeated in the check.

Unit of service: (the bill, that is the claim id without its resubmission suffix; procedure code; component: global, 26 or
TC; date of service; unit index up to the units billed). Everything that happened to one unit, from every source:

- Billed: 837 lines (each send: original, replacement 7, void 8, with the BHT date) and EMA's charge lines.
- Answered: every 835 line for the unit across all claim ids of the bill, in order: payer, CLP02 (1/19 primary, 2/3
  secondary, 4 denied, 22 reversal), paid, allowed, every CAS (group, CARC, amount), RARCs; reversal pairs (a 22 line
  negating an earlier processing); PLB take-backs (WO/FB), including references that embed a claim id.
- Posted: EMA Production postings (payments, adjustments, write-offs, transfers, by date and user or rule) and the A/R
  balance by responsible party.
- Worked: RemitWatch's own history (statuses, faxes, notes) and EMA's (claim status, resubmission after a denial: a newer
  837 for the bill is a resubmission).

Truth for each unit, after reversals: paid in full; paid short of the payer's own rate; denied and still standing (with
its reason and window); moved to the patient; waiting on a secondary; never answered (past the payer's usual answer time,
with no remittance of that payer missing from the feed); voided; written off (by whom: a person, an owner policy, EMA's
posting rule); worked and waiting (resubmitted or appealed after the denial); past its window.

## What counts as a failure

- An open item on a unit that is paid, moved to the patient, voided, or settled as secondary: "already done".
- An open To-do item on a unit that was resubmitted or appealed after the denial: "already worked" (it is waiting).
- Two open items on one unit: "duplicate".
- A unit denied, short-paid or never answered, inside its window, with no open item and no owner's decision: "money left
  on the table".
- An item closed as paid on a unit that is not paid: "closed but should not be".
- An item whose dollars differ from the unit's truth by more than a cent per unit: "wrong amount".

## How it runs

1. Build the full store: the live Vercel image after it has read every 837 (one file from the state folder), plus the
   EMA A/R and Production reports (`/Trizetto Data/Trizetto/AR and Collections Reports from EMA/`).
2. Run the auditor over every unit; write the truth table and the failure list in the private scratch folder (patient
   data never leaves it); print counts and dollars by failure kind.
3. For every failure kind: trace examples by hand down to the raw 835/837 lines and the EMA rows; find the rule in the code
   that produced it; write a failing test with invented data built from the traced pattern; fix; break and restore.
4. Run the auditor again; repeat until it finds no failure. Then the release gates (PLAN.md) and the release.

The hand-trace of the catch-up (`plans/catchup_trace_2026-11-23.md`) already names the first failure kinds and their
causes: duplicates across resubmissions and reversals, services paid before the denial, secondary processing that
settles a COB denial, take-backs whose reference embeds the claim id, the 26/TC split and units in item sizes, answers to
voided claims, and claims sent (837) but never answered with no item at all.
