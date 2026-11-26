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
- Built for decision 1: the Vercel site has no daily run any more (its fax follow-up run stays); the Data tab has a
  "Run the analysis now" button, and every page says the site analyses only on a press. Measured on a copy of Vercel's
  own store: a first press took about 3.5 minutes (the site gives an analysis 4 of the 5 minutes Vercel allows a run;
  when that is not enough, the analysis carries on in a next run by itself).
- One call I made so that it fits: the Vercel site reads EMA's A/R report only, not the Production report. With
  Production in its store, an analysis did not fit one Vercel run in time or memory (5 tries, all out of time). The AWS
  server reads both, so its lists are the complete ones; the Vercel pages will say that they show the A/R only and can
  differ from the server's. Tell me if you want Production on Vercel too: it would need the settled EMA lines kept off
  that site's store first.

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

## 6. Payer rates: what the plan-level rates found (fix7c)
- UnitedHealthcare Medicare Advantage paid the January 2026 visits on its 2025 schedule and never reprocessed them: one
  repricing item asks it to (18 lines, $110). Humana Medicare Advantage halves the Mohs when the repair goes on a
  separate surgery-center claim: 49 days are listed now ($2,030 on the 7 days where both were cut).
- UnitedHealthcare Kentucky paid PA and NP visits in full from January 2025 until late October 2026; since then some are
  paid at 85 percent. If UHC changed the contract, the rule needs that date; if it did not, those lines are underpaid.
  Worth one call to UHC provider relations.
- One UMR employer plan paid a nurse practitioner's 99214s at 53 percent of the plan's full rate: listed as underpaid,
  $312.80 against the full rate. The same plan pays PAs 85.7 percent; if it pays NPs the same way, the true gap is about
  $217. The answer is in the plan's fee terms (one question to UMR).

## 7. Payer deadlines, now from each payer's own manual or the law (deadlines unit)
Every window the research found wrong now says what its source says, with the source's words on the item. On the 11-24
data: 10 Kentucky Medicaid items came back from expired to Work now ($991: a new claim within 12 months of the service,
since Kentucky's fee-for-service program takes no appeal of an ordinary denial); 19 items turned out to be past their
payer's real last day ($1,140, mostly Anthem's Federal Employee Program); 158 items have an earlier due date, 195 a later
one. Six things only you know, the dates assume the first answer:
1. Is the practice contracted with every Medicare Advantage plan it bills, and a participating Ambetter provider?
   (Non-contracted providers get far shorter appeal windows: 60 to 90 days.)
2. What dispute period does the local Blue plan give providers for Federal Employee Program claims? (6 months is used.)
3. Are the Humana Medicaid plans on the remittances Kentucky's or Indiana's? (Kentucky's 120 days is used.)
4. A second payer's share past its deadline: listed as lost, or only counted? (Counted today: 131 shares, $4,449.)
5. Kentucky Medicaid crossovers (6 months from Medicare's decision): worth computing? (Named, not computed.)
6. Aetna's fully insured plans allow 2 years in Kentucky and Indiana, but the remittance cannot tell fully insured from
   self-funded, so 180 days is used.
