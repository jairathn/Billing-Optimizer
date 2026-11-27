# fx-people (fix wave F5: people and decision 2's follow-ups): progress

Branch `wip/fx-people`, worktree `$S/tk/wt/fx-people`, private folder `$S/tk/units/fx-people/` (mode 700).
Brief: `plans/briefs/2026-11-26_fixwave.md` section F5; rules `plans/briefs/2026-11-26_common.md`.
Runner: `$S/tk/units/fx-people/bin/t.sh [python] -- <pytest args>` (clean environment, run from the worktree).

## State
- Started 2026-11-27 ~02:00 UTC from the integration tip 59f6822. Merged the tip again at fe9ad71 (batch 1: "Merge
  wip/fx-837", "Merge wip/fx-exports", "Merge wip/fx-ledger") in 39f5c8c; the tip has not moved since (06:00 UTC).
- Every item of the F5 section is built: people-1..6, decision 2's three follow-ups, F3a's two routed needs and F1's
  routed need. Each fix was broken on purpose in a copy of the tree and restored (each break fails its test).

## Done
- people-1 (high): push 2's panel. `xpOutcome`'s Paid uses `paidCents` through `askPaid`: an empty prompt with the
  disputed amount as a hint only, "1,250.00" and "$1,250.00" read as printed, $0 and bad input asked again (0 taken only
  where the item brings no new money: `paidZeroOk`, the same rule as the server). #xpoSave / #xpdSave and the next
  step's buttons have a busy guard and the walk guard (`xpWalkGuarded`). `tests/hotfix2_page.js` drives push 2's panel
  (it no longer holds /explain back for that section) at 1400 and 390 px (`tests/test_hotfix2_pages.py`, VW).
- people-2: the check-first hold is skipped only by JSON true (`body.get("checked") is not True`).
- people-3: the $0 Paid exemption is decided in api.py by the item's work (`_status_now` gives signal, rule and whether
  the remittance is informational): route post / credit, rules post_payment and credit_unposted_takeback, signal
  REMIT_NOT_POSTED (not informational, not a call). A line the run found paid before it was listed takes $0; a line paid
  after it was listed is told the amount the remittance on file shows (PAID_LATER); no message steers a paid line to
  Lost or Let go (live proof from `states.state_of`, `_paid_on_file`).
- people-4: serverless, `_sent_once_faxed` records Sent for the heir (`actions.heir_of`); `_record_status` follows heirs
  and journals for them; a status that reaches no item is an error, logged through the scrubber, and the person is told
  (SENT_LOST); the server's path the same.
- people-5: /api/findings/bulk-status refuses "resolved" (400, BULK_PAID: one item at a time, with the amount paid).
- people-6: the event route answers an old id with `_not_found` (409 changed with the heir, as the status route);
  Copy the steps / Copy the ERA request say when their history line did not land.
- Decision 2's follow-ups (owner notes section 5), commit 6673b6f and the net-amount fix:
  - Medicaid members are never billed for a missing referral: `states.medicaid_member` (group kind, the 835 lines' own
    groups with CLP06, payer and plan names through `codes.normalize_payer`, 'Medcaid' read as Medicaid after the
    deadlines merge: tested with the misspelt name). Step: "Get a retroactive referral and send a corrected claim; if
    the plan will not take one, mark it Done", by the plan's date; past every window: expired, never billed. A
    remittance that names no program (commercial or unnamed group, no plan, filing indicator HM / ZZ / 11 / blank):
    "Check the member's coverage before any bill goes out" (the state basis and the step), never a bill on a guess.
  - A later payer that answered with a contractual adjustment only (nothing paid, nothing on the member, no other
    adjustment group; the amount is the net of its CO adjustments): done, rule `next_payer_contractual`, the reason shown
    and its remittance as the proof (the verdict names its row); Results counts it as "Settled by another payer". The
    step offers Done (and, when EMA still shows a balance, to take it off as that payer's contractual adjustment).
  - The service year ends the bill-the-patient and can't-check steps; the lists' More filters have a Service year
    picker (desk.js `serviceYears`; findings.year is the service year). The older services' batch decision is the
    owner's: nothing in the code decides it.
  - Small edits outside F5's functions (said in the commit): `states._decide`'s patient-group denial branch only (the
    settled check, the Medicaid and unclear cases; route_of untouched), `story._next` (bill_patient and done branches),
    `story._verdict` (done branch for the new rule), `story._cant_check_patient_step`. Four tests in test_decisions.py /
    test_decisions_review.py locked the replaced behaviour: updated to the owner's follow-up, renamed where the name
    said the old rule.
- Routed from F1's fix round (41a40b4): a TAKEBACK item for a second payer's share taken back in full (evidence
  second_payer) gets the rule bill_patient with the basis "<group> paid the patient's share as second payer ($X), then
  took it back on <day>: bill the patient, or dispute the takeback" and its own step
  (`story._second_share_taken_back_step`: Mark billed; Mark sent while the dispute is open; the posting step). Past the
  takeback's window it is still the patient's to bill, without the dispute. A Medicaid plan's share is never its
  member's: rule fees, "a Medicaid member is not billed for it", expired past the window. F1's need test passes
  (`plans/progress/fx-ledger_needs/need_second_payer_share_step.py`, 4 passed). The FEES branch of `states._decide` was
  F5's for this change only (the brief); route_of untouched.
- Routed from F3a (217e6fd): features.js XP_SOURCE labels '837' "837 claim file" (F3a's need test passes). GET
  /api/never-answered (behind the login; the store or the dashboard's copy): the waiting claims soonest due first with
  payer, send and due days and billed amounts (first 200 of the meta's list), and `insights.unanswered_weeks`' table
  with the weeks that stand out. desk.js shows both as panels on the never-answered claims' list; a stand-out week is
  marked with what to do (the clearinghouse's acceptance report for that week; send again what never reached the
  payers). db97018: plain tables in a scroll box (headings kept at phone width), weeks newest first, the note names the
  weeks that stand out.
- Found by the hand trace of F1's routed change, fixed (8f24c5c, ad889b0):
  - the step offered Mark billed, which records a claim sent: after the next run the item said "Follow up with <the
    supplement> on the appeal or claim marked Sent". It now offers Billed the patient, and a Billed the patient on such an
    item waits for the patient's payment in EMA (states._billed_patient, as a patient-group denial's; the person's Sent
    branch of `_decide`, one condition: `second_share_back(f)`; patient_share gives it the takeback's cents).
  - "bill the patient" only as far as the first payer's standing answer left the amount on the patient
    (`states.first_payer_answer`): a first payer that denied the service as the practice's (a CO reason) or whose answer
    is not on file leaves the takeback's own work with the reason, never a bill on a guess.

## Half done
- Nothing of F5's list. The full suite (python3, and Python 3.11.1 with SQLite 3.40.0) ran on the final tree; results
  in the final report and below once they end.

## Next command
- `git fetch origin && git log origin/claude/ecstatic-fermi-orfcgo --oneline -3`: if the tip moved, merge it, run
  `$U/bin/t.sh -- -q tests/test_fx_people.py tests/test_fx_ledger.py tests/test_decisions.py
  tests/test_decisions_review.py tests/test_states.py tests/test_story.py tests/test_desk.py tests/test_insights.py`,
  then `python3 -m pytest -q plans/progress/fx-people_needs_tests.py` (2 fail until F8 and F1 change).

## Numbers measured
Private world copy (store of 11-24, as of 2026-11-24), integration tip fe9ad71 against this branch (ad889b0, every fix
in), `$U/compare.py` and `$U/basis.py` on the two snapshots; aggregates only. (The first measurement, 59f6822 against
6673b6f, gave the same decision-2 moves.)
- 18,710 live items on both; nothing appears or vanishes. 19 change state or rule:
  - 9 DENIED_PLAN_EXCLUSION of Medicare KY (CGS J15), $581.32: 8 work now (bill the patient, $568.06) and 1 can't
    check ($13.26) -> done, rule next_payer_contractual. Their later payers: KY Medicaid 3 ($134.86), Medicare
    supplement carriers 4 ($349.90), Anthem / BCBS 1 ($83.30), an indemnity carrier 1 ($13.26).
  - 10 TAKEBACK of a second payer's share (F1's routed change), work now fees -> work now bill_patient, $305.99: Humana
    9 ($281.49), Humana Medicare Advantage 1 ($24.50).
- 6 keep their state with a new basis: 5 PR-288 of Medicaid members (Humana's Medicaid plan, CLP06 MC), $1,187.76
  ("bill the patient" -> "never a bill to the patient; get a retroactive referral and send a corrected claim"), and 1
  TAKEBACK of a second payer (Anthem / BCBS, $63.64) whose first payer's standing answer is a CO-150 denial: it keeps
  the takeback's own work, the reason now named.
- Work now 6,743 -> 6,735 ($2,040,969.48 -> $2,040,401.42); can't check 43 -> 42 ($3,126.20 -> $3,112.94); done
  6,936 -> 6,945 ($1,541,783.44 -> $1,542,364.76); waiting, expired, accepted unchanged.
- Bill-the-patient items by service year (the steps name it; the batch decision is the owner's): patient-group
  denials on work now 2025: 25 ($2,112.35), 2026: 46 ($4,915.74); can't check 2023: 7 ($244.31), 2024: 35
  ($2,868.63); second payers' shares on work now 2024: 7 ($220.92), 2025: 3 ($85.07), 2026: 1 ($156.64, an earlier
  rule's). Of the 24 open PR-288 items, 22 are Medicaid members and none is told to bill the patient.
- A17 classes before and after: A17_CLASSES
- Hand trace: all 25 moved items from the raw rows (`$U/hand_trace2.md`; `$U/hand_trace.md` for the 14 of decision
  2, same ids, raw rows and states, 13 of 14 bases identical and the 14th the net-amount fix): right 19, unclear 6 (the
  5 of open question 3 and 4, and the Anthem item's FB question for F1, open question 5), wrong 0. Before ad889b0 the
  Anthem item was wrong (told to bill the patient though its first payer left nothing on the patient). The first
  trace of the Medicare Advantage item looked at its own claim ID only and called its first payer missing: the first
  payer's answer (an Anthem PPO, PR-3 25.00) is on another submission of the same bill, which `first_payer_answer`
  reads; it is right.
- The people fixes, the service year and the never-answered page move no item in detection (pages and api only).

## Needs from others
Failing tests in `plans/progress/fx-people_needs_tests.py` (not collected by the suite; run it by path:
`python3 -m pytest -q plans/progress/fx-people_needs_tests.py`; 2 fail, as they should until the owners change).
- F1 (detect.py `_emit_taken_back`, the TAKEBACK title): a second payer's takeback is titled "...: bill the patient, or
  dispute the takeback" for every claim status 2 or 3. The state now says so only when the first payer's standing answer
  left the amount on the patient (`states.first_payer_answer`); on the world copy 1 of the 11 ($63.64) keeps a title
  that says bill the patient while its state says find out why. Test:
  `test_a_second_payers_takeback_title_says_bill_the_patient_only_when_the_first_payer_left_it_on_the_patient`.
- F8 (codes.py): Humana's Medicaid plan is one plan under two payer groups. With CLP06 MC, "HUMANA" is "Humana
  Medicaid" but the 835 name the rules read as Humana's Medicare Advantage brand becomes "Humana Medicare Advantage
  Medicaid" (kind medicaid and windows right; the group name splits its piles and patterns, and the Medicaid step names
  "Humana Medicare Advantage Medicaid"). On the world copy 20 of the 22 open missing-referral items of Medicaid members
  carry the second name and 2 the first. Test: `test_a_humana_plan_filed_as_medicaid_is_one_group_whatever_its_835_name`.

## Open questions (for the owner)
1. A Medicaid member's other patient-group reasons (plan exclusion, coverage ended, before coverage): the decision
   covers the missing referral only, so they are billed as decided. Medicaid rules restrict billing a member for a
   non-covered service too (prior written notice in most states). Should they follow the same rule?
2. A later payer that paid part, took the rest off as contractual and put nothing on the patient: still billed with the
   caution "if its answer settled the service, do not bill: press Done". Should it go to Done like the
   contractual-only answer?
3. Five of the nine "contractual adjustment only" answers ($363.16) come from Medicare supplement or indemnity carriers
   (CO-96 non-covered, CO-45), which usually have no contract with the practice. The decision ("the contract puts that
   amount on the practice") sends them to Done as built; if the practice has no contract with those carriers, Medicare's
   PR-49 may still let the practice bill the patient. Keep them Done, or only for Medicaid and contracted plans?
4. One of the nine (Anthem, $83.30) answered CO-97 (bundled into another service). Done is right for the patient's
   side; a corrected claim with a modifier may still recover it from Anthem, and that later line has no item of its own.
5. (For F1, a ledger question, not a need: no failing test because the right reading is not certain.) Finding
   48db43b8e7b52d16bfe8c545 (Anthem / BCBS, $63.64, second payer): the recoupment read as its takeback is two pairs of
   FB (forwarding balance) entries, negative on two remittances of 2025-11 and the same amounts positive on one of
   2026-04, after Anthem reversed and re-paid the line itself. FB pairs move a negative balance from one check to a later
   one; whether the 63.64 payment was taken back at all (or only the reversals already on file settled in cash) is
   F1's reading. The state no longer bills the patient for it either way (its first payer answered CO-150).
6. After Billed the patient, a second payer's share waits for the patient's payment two monthly statements
   (PATIENT_WAIT_DAYS, 60 days), as decision 2's patient-group denials do. Same wait for both?
