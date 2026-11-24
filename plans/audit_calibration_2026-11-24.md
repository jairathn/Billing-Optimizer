# The every-claim auditor checked by hand (2026-11-24)

Calibration of `plans/tools/claim_audit.py` (rules A15) before its verdicts are allowed to change RemitWatch. Aggregates
only: counts, dollars, codes, payer groups. The traced cases, with their store keys, are in the session's private notes.

## 1. Method

196 of the auditor's verdicts were traced by hand from the raw rows: every 835 processing of the whole bill (all claim
ids), its lines, CAS, RARCs, reversals, same-check copies, PLB entries naming the bill, every 837 send, EMA's weekly rows,
EMA's Production postings and A/R row for the line, and the same patient's same code and day on other bills. The sample
was spread over payer groups (about 40) and service months (2026-01 to 2026-11, plus 16 older services). Population sizes
were then measured with the auditor's own objects.

## 2. Results

| auditor verdict | traced | right | wrong | can't tell | what goes wrong |
|---|---|---|---|---|---|
| CORRECT open | 14 | 8 | 5 | 1 | primary paid off the 835 feed (3), no cap at the charge (1), appeal noted in EMA's A/R (1) |
| CORRECT closed | 12 | 12 | 0 | 0 | |
| ALREADY_DONE: paid | 16 | 15 | 1 | 0 | a duplicate delivery's line mapped by position to another code |
| ALREADY_DONE: moved to the patient | 13 | 10 | 3 | 0 | a secondary's PR-96 governs; PR-242 and PR-22 are not the patient's balance |
| ALREADY_DONE: settled by the secondary | 12 | 10 | 2 | 0 | a $0 secondary answer settles a bundling or timely-filing denial |
| ALREADY_DONE: voided | 12 | 12 | 0 | 0 | |
| ALREADY_WORKED | 12 | 7 | 5 | 0 | resend to another payer counted as work; $0 answers to a corrected claim dropped as copies |
| DUPLICATE | 14 | 13 | 1 | 0 | duplicate with no keeper on a not-payable unit |
| CLOSED_WRONGLY | 15 | 8 | 7 | 0 | a global settled by 26 + TC paid on other bills (6); SVC06 modifier fallback (1) |
| WRONG_AMOUNT open | 27 | 11 | 13 | 3 | rate evidence (half-rate MPPR, cross-product rate, 85% tier), cross-payer answer, duplicate denial |
| WRONG_AMOUNT closed | 12 | 10 | 2 | 0 | recovery counts only the first payer's payment |
| LEFT_ON_TABLE | 24 | 2 | 18 | 4 | off-feed answers (7 of 8 never-answered), empty SVC05 units, rates, not-payable codes |
| UNDECIDED | 13 | 13 | 0 | 0 | honest; 6 decidable from EMA's Production ledger |
| total | 196 | 131 | 57 | 8 | |

Trust now: ALREADY_DONE, DUPLICATE, CORRECT closed, WRONG_AMOUNT closed, UNDECIDED. Not yet: LEFT_ON_TABLE,
CLOSED_WRONGLY, WRONG_AMOUNT open, ALREADY_WORKED, CORRECT open.

Pass 1's money left on the table ($140,258 in scope) is mostly not owed: of 1,150 never-answered units ($79,507), EMA's
Production ledger shows an insurance payment on 837 ($59,946) and an adjudication or a void on 61 more; only 173 ($12,143) are still
open in EMA. All 213 "fewer units answered" units ($14,220) sit on lines whose charge reconciles in full (SVC05 empty). Of
$27,008 short, about $7,774 rests on sound rate evidence. What may be real: the 173 open never-answered units, the $7,774
short, part of the 1,600 denied units ($14,937, about 1,150 of them $0.01 placeholder lines), and $823 taken back.

## 3. Rule fixes for the auditor (rules A16), largest first

1. **Read EMA's Production ledger** before calling a unit never answered, no feed, secondary only, or denied by a
   secondary. EMA posts payer answers from checks that are not 835s on file (Optum/VA, Humana MA, UHC MA, Medicare
   secondaries). Insurance payment means paid off the feed; a payer adjustment only means adjudicated; voided means
   voided. Also: the weekly export's Payment rows often carry the amount outside `insurance_payments_cents`, and
   "Resolved/Other" counts as answered. Find EMA's line by the bill link, else the patient (837 subscriber to 835 member
   to EMA person), else the only unmatched line with that day, code and charge.
2. **Empty SVC05 means the billed units.** When the 835 line gives no units and its CAS reconciles the whole charge, the
   units paid are the billed units; "fewer units" needs a units reason code or an unreconciled charge.
3. **Link global, 26 and TC across bills.** A global is paid when both 26 and TC stand paid for the patient, code and day
   on any bill; a 26 or TC is paid when the global is. (The practice rebills a denied global 88305/88304 as 26 + TC.)
4. **The governing answer is the primary payer's.** Decide the bill's primary (the family that paid or assigned PR first,
   or whose answer others follow with OA-23; the 837's billed payer when on file). Its answers set the primary state; a
   later answer from another family sets the secondary's state only (273 units in scope were governed by the wrong payer).
5. **Rate evidence.** Observe rates only on full-rate lines (drop lines at about half of another mode with a 20% share or
   more); break full/half ties to the full amount; no rate across product classes (MA, commercial, Medicaid); cap the
   expected amount at the billed charge per unit; allowed = charge minus CO/OA/PI when AMT*B6 equals paid with a PR share;
   no rate for unlisted codes (J3490); add-on codes do not make a line "multiple procedure".
6. **Not payable.** Add CARC 246 and B10 when the payer never pays the code; G2211 beside an E/M with modifier 25 at
   Medicare and plans that follow CMS is a correct denial (paid 761 times, never beside a -25 E/M; all 141 such denied).
7. **PR-22, 242, 27, 227, 31 are denials to work** (coordination of benefits, network, eligibility), not the patient's
   balance, unless EMA shows the patient billed or paid. The allowed per unit divides by the unpaid units only.
8. **A duplicate denial defers to the payer's previous answer**, whatever its kind (55 units in scope); CO-151 with M86
   likewise.
9. **Same-check copies only.** Collapse processings as copies only when they share the check; later deliveries collapse
   only for paid processings (85 later $0 answers were dropped).
10. **Work.** A resend works a denial when it goes to the denying payer (any claim id) or, after a coordination,
    wrong-payer or eligibility denial, to another payer. EMA's A/R notes and Production claim actions count as work.
11. **Map items to units by content** (code, modifiers, charge, units, day), not by line position; claim each evidence
    line's units.
12. **Verdicts.** DUPLICATE only when another item holds the unit; not-payable units count as done; a bill's take-back
    items are compared with its PLB net; no WRONG_AMOUNT between two estimates; a denial line's own allowed amount beats a
    mode; recovery counts every payer's payment of the line after the denial.
13. **Smaller.** "Settled by the secondary" only after a coordination denial or a secondary that allowed or paid; the
    take-back window runs from the PLB date; MA15 split-claim notices answer only the services they list; SVC06 with no
    modifiers means the billed code had none; a modifier changed between resubmissions is one service; $0.01 placeholder
    lines are ignored; a person-closed item's recovered amount is checked against the payments.

## 4. Two rules for the owner

**The payer's own rate** stays the yardstick (same payer, product, setting, year). A provider-level rate is used only when
it is a recognised tier: at least 10 payments, at least 60% share, and a ratio to the payer's mode of 1.00 or the NP/PA
85% (checked against the provider's type), never a multiple-procedure half. An unexplained provider-level discount (0.90,
0.81, 0.73 ... of the payer's mode: 133 items, $4,469) is listed once per payer and provider as a contract or
credentialing review, with its dollars, not absorbed. 114 items ($3,253) paid at 85% for a provider whose own mode is 100%
are short, not "the tier". Recommended; applied unless the owner says otherwise.

**Codes a payer never pays.** 99024 is never payable. G2211 at commercial payers that do not recognise it (Anthem
commercial paid 4% of 3,041, UMR 0 of 251, UHC commercial 0 of 215) is not payable whatever the CARC, except
authorization, eligibility, enrolment, timely-filing and missing-information denials. At Medicare and plans that follow
CMS, G2211 is decided per claim: beside an E/M with -25 it is a correct denial; without -25 a CO-234 is worth reviewing.
Anthem MA and Humana MA pay G2211 (Humana about 80%). Recommended; applied unless the owner says otherwise.

## 5. What neither tool tracks yet

- The secondary's or coinsurance balance after a primary payment (still open in EMA on several traced bills).
- EMA write-offs of denials that could still be worked (they should be marked written off, by rule or by a person).
- Payers outside the 835 feed (Optum/VA Community Care, some Humana MA checks): EMA is their only source.
