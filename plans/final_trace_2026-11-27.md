# The release's hand test of August 2026 services (2026-11-27)

What was run: the release's code (330f4b5) on a private copy of the real store as of 2026-11-24, with every 837 file
read again (5,383 files, 158,573 claims, each now carrying its patient's keyed name), then the independent auditor
(plans/tools/claim_audit_a17.py, rules A17: it imports nothing from remitwatch/) on the same copy. The August cut
compares, for every service dated August 2026, what the app lists against what the auditor decides from the raw rows.
Counts and dollars only; the private lists stay in the private folder.

## The run itself

Detection ran in 253 s at 495 MB, with no failed step. Every item has one state. Safe mode stayed off: this copy had no
self-check history, so the first run sets the baseline, as the first run after the deploy will on each host.

## August 2026: the money the payers owe

13,451 units of service. The auditor finds 11,042 paid, 980 moved to the patient, 562 not payable, 431 billed at $0,
10 voided, and 374 owed ($36,549).

- Owed units on an open item: 291.
- Owed units on no item: 85 ($2,694, 7% of the owed dollars). By hand: most are small rate shortfalls the app does not
  list under its plan-level rates and thresholds (SHORT, $950 over 49 units), G2211 denials (tracked as one pattern
  item per payer by design, never per line), duplicate denials restating a payment made on another line (OA-18), and
  denials EMA shows but no 835 carries (CO-97, $409). None is a payment the app calls received that was not.

## August 2026: the work the app lists

The app's items for August services, as this run produced them, against the auditor's verdict:

| State | Auditor agrees | Already done | Amount differs | Cannot decide | Other |
|---|---|---|---|---|---|
| Work now (314) | 158 ($29,236) | 79 ($6,854) | 26 ($1,341) | 48 ($9,181) | 3 |
| Waiting (12) | 8 | 2 | 1 | 0 | 1 |
| Done (85) | 54 | 10 | 21 | 0 | 0 |

- "Cannot decide" are items with no unit of their own (credit balances, remittances EMA did not post, EMA-only
  denials).
- Of the 79 "already done": 43 ($1,218) are second payers never billed, which the auditor does not model (it counts the
  primary's payment), so they are real work; about 13 are patient-group denials, which the owner's decision 2 makes
  "bill the patient"; the rest (8 code changes the auditor calls not payable, 4 bundled lines it calls paid, and a few
  more) are rule differences to look at next.

## What the hand test found and fixed in this release

1. Items closed as "paid on a later remittance" by an answer that netted to nothing: the ledger ignored a negative
   line inside a processed claim, so the payment beside it stood. Now such a line takes that payment back
   (c10f9cf, tests/test_net_zero_later_payment.py). The self-check closure_proof names 33 items closed without a
   payment named; they are not all this cause and stay on the Data tab for a look.
2. Claims chased as never answered while EMA showed them paid: the content bridge chose no EMA claim when several
   patients' visits shared the day, code and fee. It now chooses the one whose patient is the claim's own (330f4b5,
   tests/test_settled_in_ema.py). On the copy, 66 August Work-now items ($21,897) were of this kind. 64 of them were
   items the old live run had listed and the new code no longer produces: the self-check "vanished" keeps such items
   on the worklist with a note until a person accepts the change (below).

## After the deploy: accept the change on the Data tab

The first run on each host compares its items with the old run's. On the copy, 2,260 items the old code listed are no
longer produced ($219,851): 1,584 underpayments (the rates are now each plan's own: fix7c), 366 never-answered and 77
rejected claims (now answered by the 837s and EMA), 108 multiple-procedure items, 73 claim denials and a few more. They
stay on the worklist, marked, until the owner accepts the change on the Data tab ("No open item disappeared without a
reason"). Look at the examples by kind first; accept once the change is intended. Until then they cost biller time.
