# needs_D.md: what unit D (states and story) needs from other units, and how to call it

Final version after the two reviews and the fixes (fixer of unit D, 2026-11-23). Files of unit D: remitwatch/states.py,
remitwatch/story.py, tests/test_states.py, tests/test_story.py. Unit INT may edit them from now on. No identifiers below:
case numbers, payers, codes and dollar amounts only.

## How to call unit D

### Unit B (detect.py, at save time)
- Once per detection run, before the states: `states.save_response_days(conn)`.
- Then ONE batch call for every item you save: `st = states.states_of(conn, items, outcomes=oc, today=today)`. Store per
  item: `work_state`, `follow_up_on`, `state_basis` (300 characters at most already) and `appeal_block`.
- `appeal_block` is now set for every work-now item whose next step is not an appeal, so B's bulk SQL
  (`appeal_block IS NULL`) and `story.next_step(...)['appeal_ok']` agree. On the integrated world, of 300 sampled items the
  bulk SQL takes, 299 have appeal_ok True (route appeal or rate). Values: paid, credit, reprocessed, waiting, answered,
  call, note, follow_up, decided, likely_paid, check_first, settled_in_ema, patient_paid, corrected, cob, eob_first, bill,
  post, owner, fees. NULL: an appeal or a rate request may go out.
- Timing on the integrated world: states_of over all 17,161 items takes 49 to 52 s at 220 MB; the whole detection run
  161 to 191 s at a 907 MB peak.
- New rules an answer can carry (`rule`): open_late (open only under a later window: the payer's dispute window, the
  corrected-claim limit or a Medicare reopening; `late_window` names it), expired (the day after the last window: no
  grace, orchestrator decision 1), credit_unposted_takeback, possible_overpayment, settled_in_ema, patient_paid_co,
  decided_no_appeal, note_unread, ema_follow_up, person_billed_paid, person_reopened, payment_never_arrived,
  forwarded_late, cant_check_carrier, work_answered (with `answer_window`), check_first.
- Detection's outcome hand-overs that the states read: `person_reopened` and `rejected_payment` (needs_B 0a.2, done),
  `evidence.informational` on REMIT_NOT_POSTED (needs_B 0a.1, done), `evidence.archive_rule` (B2), `legal_deadline`.

### Unit C (api.py and the pages)
- One call per drawer: `story.explain(conn, finding_id, ledger=lg, today=None)` -> `{'story', 'verdict', 'next_step',
  'state'}`.
- `next_step` keys now include: `pattern` (the 4-part policy key POST /api/ema/policy takes, whenever `accept_pattern`
  is a button), `expected_basis` (what the dollar figure rests on: "RemitWatch's estimate: ...", "the 835's allowed
  amount", "what the denial took in EMA ..."), `claim_url` with the `open_in_ema` button (REMITWATCH_EMA_CLAIM_URL or the
  prefix learned from the reports; 'off' turns it off), `clocks` (every window the state expires by), `late_window`.
- `story['reconciles']` is True, False (a gap with its amount and cause) or None ("It cannot be checked to the cent on
  this site": the dashboard's copy cannot see patient money the server keeps). Show None as neither green nor red.
- An expired item's step offers only Log what happened: it left the list on its own and counts as lost to the deadline
  (no Let go, so Results does not count it as a person's decision).
- The dashboard's copy keeps every remittance of the bill (cloud.SERVING_BUILD 8); its note now says so (story.COPY_NOTE).

## Requests (open after the fixes)

### B (detect.py, codes.py)
1. codes.payer_group_kind: an 835 payer named "... CARE PLAN" with the Medicaid filing indicator maps to "Humana Medicare
   Advantage Medicaid", while the same plan's first answer maps to "Humana Medicaid" (C14). The states read the later
   answer as another payer's. D now keeps the corrected-claim clock open for an answer a corrected claim can fix, so C14
   reads work now until the plan's 365 days; the grouping is still worth fixing.
2. Fold repeat listings of one service (B2 hook): C59's six older listings (four read "work its answer", two expired),
   C60's four superseded listings (expired) and C32's superseded MA130 item are still separate items.
3. MPPR_EXCESS on a correctly paid second procedure (C28 and C58, both the 13121 of a Mohs day): D's step already says
   "check first: the second procedure, a surgery-center day; let it go if that explains it", and keeps it out of every
   bulk appeal. Detection should price the second procedure and the facility rate so these are not listed.
4. After the current detection two case items are gone with no successor by line, claim or bill: C18's DENIED_AUTH item
   (the truth: a real denial worth a 10-minute check) and C40's open 99214-57 item. The other six gone ids were done or
   duplicates (correct to drop).
5. needs_B 0a.3 to 0a.7 are in: forwarded claims expire, the items' own deadlines and legal deadlines are read, EMA_DENIED
   items valued after the first payer's payment are never "paid before" proof, and SECONDARY_NOT_BILLED from the 835 alone
   works (the second payer's or the patient's money closes it).

### G (selfcheck.py, tests/test_selfcheck.py)
1. `states.EXPIRE_GRACE_DAYS` is 0 (orchestrator decision 1) and `states._decide` now uses it. test_selfcheck's
   `test_an_unworked_item_past_every_window_and_its_grace_is_caught` still expects a 30-day grace ("F-grace") and fails in
   every environment; with no grace, F-grace is past its window and caught.
2. `check_past_window` should read the item's last window, not only appeal_deadline and legal_deadline:
   `states.last_window(f, f['appeal_deadline'], f['deadline_basis'], states.windows(f, ..., fx))` gives the payer's
   dispute window, the corrected-claim limit and a Medicare reopening (one year for a clerical remark such as M51), or
   read the stored `work_state` ('expired') and the state's `closed_on`. Otherwise it reports items that are open under a
   later window (an Anthem two-year dispute, C59's reopening) as past every window.

### SWEEP or A1 (cloud.SERVING_TABLES)
1. Host parity: the copy keeps whole bills now, and on 199 stratified items the copy's state matches the server's 199 of
   199 and its route 198 of 199. The one difference is an MPPR_EXCESS item: the server sees the patient's other claim of
   the same day (the day's highest-valued procedure) and says "check first"; the copy, which keeps only the item's bill,
   asks for the usual rate. Keep the patient's other claims on the item's date of service (same member, same day).
   The stored appeal_block keeps that item out of the bulk appeal on both hosts meanwhile.

### R (results.py)
1. `_pair_reversals` now pairs as `results._pair_group` does: the same payer by name counts when the filing indicator
   moved the claim to another of its products, the same group preferred after the reasons and the amount (needs_R 8,
   done; test_a_reversal_under_another_product_of_the_same_payer_pairs_with_the_answer_it_cancels). The interface names
   needs_R 8 lists are kept.

### G and C (one voice for the dollar figure)
1. needs_G 8.1: `next_step['expected_cents']` comes from `story._expected` (the shortfall, $0 when the shortfall is $0,
   capped by the charge, the payer's allowed amount, or what an EMA-only denial took in EMA) with `expected_basis`. If the
   worklist sorts by a stored `expected_value_cents`, store the same figure at save (B) or read `next_step` (C) so the
   drawer and the list agree. Not changed by the fixer.

### F (docs)
1. README layout: name states.py and story.py with one line each (unchanged from the first round).

## Answers to other units' requests aimed at D
- needs_C1 F.2 2(a) `claim_url` and `open_in_ema`: done (the template's placeholder is {claim}; only 6 to 12 digit claim
  numbers). 2(b) a person's newer status: the stored state is explained and a person's status wins in states; the page's
  own line is fine.
- needs_C1 2.1 `next_step.pattern`: done. 2.2 route stored: done through appeal_block (above). 2.4 C24 "decided not to
  appeal" note: done (decided_no_appeal, out of every bulk appeal). C37's "nobody has worked it": done (the note's call
  is work). "Funds's": done.
- needs_C1 F.2 7 (hold route blocks at send): the next step never offers a fax for a blocked route; holding a direct API
  call is the API's (C) decision.
- needs_B 0a.1 (informational remittance) and 0a.2 (a person reopened an automatic closure): done, tested and measured.
