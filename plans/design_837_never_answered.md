# The 837 as a source of sends: claims sent and never answered (design, 2026-11-24)

The catch-up trace (T-I2) found claims sent (837 on file) that no payer answered and no item lists: 1,986 claims, $927k
billed, 713 of them VA Community Care, on the released code. Detection reads the 837s today only for the billed code of a
downcode (detect.py load: `lines_837`); neither the never-answered pass (`run_ema_open_claims`, from EMA's weekly exports)
nor push 2's states read a send from them (T-I4: 45 already-worked items on Work now are resends EMA's exports do not show).
This is the largest "money left on the table" kind, so it is built test-first and measured on the full 837 history.

## What counts

For each claim id (CLM01) the newest 837 send (BHT04/05, `claims_837.bht_created`). A void (CLM05-3 = 8) as the newest
send ends the claim (T-N5). A replacement (7) is a resend of its bill.

A send is answered when any of these came after it:
- an 835 claim for the same claim id, from the payer the 837 names (by payer id, else by the remittances' votes for the
  name), or a primary processing (CLP02 1, 19, 4, 22) from any payer (the release's T-C2 rule: a secondary-only answer
  leaves the primary's remittance missing, and the item says so);
- an 835 claim for a newer submission of the same bill (its answer is worked from its remittance);
- EMA's postings for the claim (push 2's `ema_checks.claim_answers`: a payment, a $0 answer or a payer's reason posted
  after the send; a status alone never answers).

It is due the payer's usual answer time after the send (push 2's `turnaround_days`, else `NO_ANSWER_DAYS`), and listed
only when that payer's answers reach the due day (the release's CH-8 hold, or EMA's postings past the feed:
`answers_through`). Otherwise it is counted on the Data tab under its payer, never dropped silently.

## One item per claim

A claim EMA's weekly exports already chase (`run_ema_open_claims`) keeps that item; the 837 adds its send date (T-I4: "sent
electronically on <day>") and its lines where EMA has none. A claim only the 837s know is a new NO_ANSWER item (its own id
signal so it never collides), priced like the EMA one: each line at the payer's own rate for its code and component
(26/TC), capped at its billed charge (T-N9). A bill sent again after a denial is the denial's work (already worked: the
item's state is waiting until the resend's answer is due, T-I4), not a second chase.

## Health

- A payer whose 837 claims never get an ERA (VA Community Care through its administrator): one Data-tab line with the
  claims and dollars, and EMA's postings are the source of their answers.
- Weeks where EMA shows sends but no 837 is on file, and weeks of 837s with no EMA sends (T-N13): a hole in either feed.
- Claims never sent (EMA's print batch) are dated against EMA's export day, not the remittance feed (T-N11).

## Tests (invented data, both store kinds) and measurement

Each rule above gets a failing test first: answered by the named payer; by a primary from another payer; secondary-only
(stays, says the primary's remittance is missing); newer submission answered; voided; EMA posting answers it; payer behind
(held and counted); EMA's export already chases it (one item); resend after a denial (waiting, not work); VA without ERAs
(health line). Measured on a private copy of the full store (Vercel's image after it read every 837, plus EMA's reports)
and judged by the auditor (A16), which reads the 837s and EMA's postings independently.
