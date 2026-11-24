# The next fix batch (written 2026-11-24 16:10 UTC)

These are open points the fixers found but did not fix (fix4, fix5, fix6 and fix837), plus the leftovers the auditor (A16)
still finds on the Work now list. Each one needs a failing test from invented data on both store kinds, a measurement on a
private copy of the real data, and a hand-check of what moved. Work them in the order below, grouping them so that
parallel fixers do not edit the same functions.

## 1. Determinism (first: everything else is measured against it)
Two runs of the same code on fresh copies of the same store must list the same items. Today they do not:
- the SECONDARY_NOT_BILLED counts swing (826, 503, 825 across three runs; 690 against 424 already done);
- fix5 reproduced it with a control run;
- fix5 and fix837 both suspect the ledger summary's keep-dirty flag and the ledger refresh after detection, which changes
  which EMA lines are kept.

Make runs repeatable on both hosts. Prove it with two runs, compared item by item. Then look for any other source of
run-to-run change: set and dict order, ORDER BY missing before LIMIT or before a first-wins choice, the time of day.

## 2. Leftovers on Work now that the auditor calls already done or already worked
The probes are `plans/tools/a16/residual_probe.py` and `offfeed_probe.py`. Run them again on the integrated code first.
- Items worth $0 on Work now: placeholder lines, and $0 charges. Leave them out and name them for what they are.
- Denials paid on an 835 but routed to 'note' or 'check_first' instead of done: about 20.
- Voided claims still listed: 6, plus 7 TAKEBACK items on voided claims ($386). Voids in EMA and the 837 are not read
  for takebacks.
- C6 ignores the patient's copay when it compares a payment with half of what the item should bring. Example: an Anthem
  MA 99212 paid in full with the copay stays listed.
- The Medicaid catch-all ('Medicaid (other)') is still read as the same payer as the state's Medicaid in C6's same-payer
  reading and in the older paid-before branch. fix6 fixed it only in its own rule.
- "Already worked, waiting" (about 20): an appeal noted on the first A/R, routed to "call". Decide by hand whether calling
  is real work or a repeat, and set the rule.

## 3. Amounts (after fix4 is merged; its leftovers join this list)
- A claim item is sized without its units (example: $98 where the units say $196).
- A claim sent to a payer as its secondary is priced at that payer's full rate: the 837's SBR01 and the claim filing
  indicator are not read.
- A supplement that answers with claim status 1 is counted as a primary payment.
- The share named by a second payer's claim-level denial may be more than Medicaid's own rate.

## 4. Takebacks and reversals
- states.paid_proof sees reversals, but not PLB or J1 takebacks in general.
- 27 reversals under another payer id stay unpaired: Wausau and UMR use different names, so their ids never join.
- A J3490 whole-charge CO-45 from Anthem stays listed, because the payer allowed the code once.

## 5. The 837 side and full visibility
- Export claims dated only by the visit day get no send-day twins. A print-batch duplicate made after the visit was paid
  stays listed.
- A dependent's 837 carries the subscriber's id.
- story.py shows no 837 rows. Every item's story should show the send (837), each answer (835), EMA's postings and the
  open balance, reconciled to the cent.
- Unanswered claims are not grouped by send week (the rest of T-N13).

## 6. Owner decisions that change rules (see HANDOFF section 3b)
- Rule 8: patient-group denials.
- The recovered amount when a payer denies as primary and pays later as secondary. The two auditors count it
  differently (about 400 recoveries); fix5's choice, the secondary's share counted, matches A16.
- Owner rules 4.1 and 4.2 (codes a payer never pays: 20 code-change items, $7,150).
