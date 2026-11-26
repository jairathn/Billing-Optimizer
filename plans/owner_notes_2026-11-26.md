# For the owner: what the work found that needs your call (2026-11-26, kept current)

Aggregates only. The items behind each number are on the site; the sources are in
`plans/research/codes_rarely_paid_2026-11-26.md` once the decisions unit commits it.

## 1. Codes a payer almost never pays (your decision 4): most of it cannot be paid

You asked to keep pursuing these and find out why the payers refuse them. The research (2026-11-26, every payer rule
read from the payer's own policy or CPT, each fetched twice) covers 25 payer and code pairs: 968 open items, $209,966,
of which 491 items ($154,318) sit on Work now.

| Verdict | Items | Dollars |
|---|---|---|
| Not payable under the payer's published rule, whatever the note says | 867 | $184,505 |
| Fixable on the claim (enrollment, referral, modifiers, drug details) | 75 | $25,419 |
| Unclear (the payer's rule is not public; ask the payer) | 26 | $43 |

The big ones:
- Hyperthermia (77600) billed with the 2026 surface radiation codes (77436 to 77439). CPT 2026 says not to report them
  together. Anthem bundles or recodes it (206 items, $73,283); Optum's VA network denies it as not covered (101 items,
  $49,705, plus 45 items, $20,172, that depend on the VA referral). Medicare still pays the pair in 2026. That is a
  compliance question for your coding advisor, not money to chase: a payment for codes CPT says not to report together
  can be an overpayment to refund.
- G6001 (image guidance) with superficial radiation at UnitedHealthcare, Medicare Advantage and commercial (550 items,
  $61,330): prior authorization was required through 2025 and UHC's policy called it not medically necessary for
  superficial skin cancer treatment until 2026-03-01. G6001 ended in 2026; the 2026 code is 77439, once per course,
  with 77437 or 77438. Before letting these go, check whether an approved authorization listed G6001.
- G2211: not paid by Anthem or UnitedHealthcare commercial plans at all; Medicare and UHC Medicare Advantage do not pay
  it when the visit carries modifier 25 for a procedure.
- Kentucky and Indiana Medicaid denials for enrollment, affiliation and taxonomy (fixable on the claim).
- Humana Medicare Advantage G0127 needs Q7, Q8 or Q9 and the class findings in the note.

My recommendation: accept the "not payable" patterns on the policy page (they leave Work now with their reason kept),
keep the 75 fixable items on Work now, change the billing going forward (77439 instead of G6001; no 77600 with
77436 to 77439 for payers that follow CPT; no G2211 to Anthem and UHC commercial), and have the coding advisor look at
the Medicare 77600 payments. Until you say so, every one of them stays on Work now as you asked.

## 2. Things to set up or check outside the code
- Alert mail: the Vercel site has no mail set up, so its 7 alerts (the swing alarm among them) were never mailed. The
  AWS server is the site that sends mail: check its SMTP settings.
- The live remittance feed: nothing newer than the 11-16 to 11-20 batch has arrived in Dropbox since 11-23.
- The Vercel site grows toward a hard memory ceiling of about 1 GB for its store; at today's growth it reaches it in
  about a year. With your decision 1 (Vercel analyses only when someone presses Run, AWS runs every day) the AWS site
  is the one the billers use.

## 3. Found by the review of every change since 11-23 (being verified, then fixed before the release)
- Indiana Medicaid's first-filing limit is set to 365 days; the program publishes 180 days from the date of service.
- 36 small recoupments ($7,886) name a claim whose payment is not on file and show up nowhere; 15 second-payer shares
  taken back in full ($522) have no item.
- Details: `plans/review_2026-11-26.md`.

## 4. Two things in the public repository's history (your call)
- The live server's address was written in two docs from 2026-11-21 and is on main. It is gone from the files now, with
  a test so it cannot come back, but it stays in git history. Rewriting main's history would mean force-pushing main
  and every branch. My recommendation: leave history alone; the server's protection is its login (closed by default),
  not its address. If you want the address out of circulation, the practical step is a new address for the server,
  and then TriZetto's allow list (if one is set) needs the new one.
- A staff member's first name was in a plan file on the working branches (not on main). It is replaced now, but it is
  in those branches' history and will reach main's history at the release unless that history is rewritten first. My
  recommendation: accept it (a first name, no patient data).

## 5. Bill the patient (your decision 2): three cases where the law or a contract says no

Built as you asked: 188 patient-group denials (plan exclusion, missing referral, coverage ended) now stay on Work now
with "Bill the patient $X", X being the share the payer put on the patient, less what EMA shows the patient already
paid; "Billed the patient" and "Done" are one click each. Measured on the 11-24 data: 140 items on Work now, $15,243 to
bill. Three groups need your call, and my recommendation for each:
- Medicaid members (21 items, all a missing referral, Humana's Medicaid plans): federal Medicaid rules require the
  practice to take Medicaid's payment as payment in full, and a missing referral is the practice's to fix, so these
  members should not get a bill. Recommendation: for Medicaid members the step becomes "Get a retroactive referral and
  send a corrected claim; if the plan will not take one, mark it Done", never a patient bill. I will build it that way
  unless you say otherwise.
- A second payer answered with a contractual adjustment only (9 items, 3 of them Medicaid): the contract puts that
  amount on the practice, so billing the patient would break it. Recommendation: these go straight to Done with the
  reason shown (today the step warns first).
- Age: 93 of the items had been expired and came back; their services go back to 2023 ($6,912 of patient share).
  Recommendation: bill the 2026 and 2025 services; for older ones, decide as a batch (many practices write those off
  rather than send a patient a two-year-old bill).
