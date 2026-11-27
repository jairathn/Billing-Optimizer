# fx-people (fix wave F5: people and decision 2's follow-ups): progress

Branch `wip/fx-people`, worktree `$S/tk/wt/fx-people`, private folder `$S/tk/units/fx-people/` (mode 700).
Brief: `plans/briefs/2026-11-26_fixwave.md` section F5; rules `plans/briefs/2026-11-26_common.md`.
Runner: `$S/tk/units/fx-people/bin/t.sh [python] -- <pytest args>` (clean environment, run from the worktree).

## State
- Started 2026-11-27 ~02:00 UTC from the integration tip (59f6822 now; no batch-1 merge on it yet: no
  "Merge wip/fx-837", "Merge wip/fx-ledger" or "Merge wip/fx-exports" in its log at 06:00 UTC).
- Ported the reviewers' and verifiers' tests into `tests/test_fx_people.py` (+ `tests/fx_people_xp.js`, the panel's page
  logic in a node vm). On the tip: 36 failed, 4 passed (the passes are controls: JSON true sends, an unpaid line's $0
  refused, and the whole send without a copy refresh).

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
- Decision 2's follow-ups (owner notes section 5), commit 6673b6f:
  - Medicaid members are never billed for a missing referral: `states.medicaid_member` (group kind, the 835 lines' own
    groups with CLP06, payer and plan names through `codes.normalize_payer`, 'Medcaid' read as Medicaid after the
    deadlines merge: tested with the misspelt name). Step: "Get a retroactive referral and send a corrected claim; if
    the plan will not take one, mark it Done", by the plan's date; past every window: expired, never billed. A
    remittance that names no program (commercial or unnamed group, no plan, filing indicator HM / ZZ / 11 / blank):
    "Check the member's coverage before any bill goes out" (the state basis and the step), never a bill on a guess.
  - A later payer that answered with a contractual adjustment only (nothing paid, nothing on the member): done, rule
    `next_payer_contractual`, the reason shown and its remittance as the proof (the verdict names its row); Results
    counts it as "Settled by another payer". The step offers Done (and, when EMA still shows a balance, to take it off
    as that payer's contractual adjustment).
  - The service year ends the bill-the-patient and can't-check steps; the lists' More filters have a Service year
    picker (desk.js `serviceYears`; findings.year is the service year). The older services' batch decision is the
    owner's: nothing in the code decides it.
  - Small edits outside F5's functions (said in the commit): `states._decide`'s patient-group denial branch only (the
    settled check, the Medicaid and unclear cases; route_of untouched), `story._next` (bill_patient and done branches),
    `story._verdict` (done branch for the new rule), `story._cant_check_patient_step`. Four tests in test_decisions.py /
    test_decisions_review.py locked the replaced behaviour: updated to the owner's follow-up, renamed where the name
    said the old rule.
- Every fix broken on purpose and restored in a copy of the tree (`$U/br`): people fixes and 15 decision-2 breaks, each
  failing its test.

## Half done
- F3a-routed needs (the 837 label in the story, the never-answered page by send week): waiting for "Merge wip/fx-837" on
  the integration tip (not there at 03:40 UTC).

## Next command
- `git fetch origin && git log origin/claude/ecstatic-fermi-orfcgo --oneline --grep 'Merge wip/fx-'`: when batch 1 is
  merged, merge the tip, run `$U/bin/t.sh -- -q tests/test_fx_people.py tests/test_decisions.py
  tests/test_decisions_review.py tests/test_story.py tests/test_states.py tests/test_desk.py tests/test_hotfix2_pages.py`,
  then build the F3a-routed needs (tests in `plans/progress/fx-837_needs_tests.py`).

## Numbers measured
Private world copy (store of 11-24, as of 2026-11-24), integration tip 59f6822 against this branch (6673b6f + the net
amount fix), `$U/compare.py` on the two snapshots; aggregates only.
- 18,810 live items on both. 9 change state, all DENIED_PLAN_EXCLUSION of Medicare KY (CGS J15), $581.32: 8 work now
  (bill the patient, $568.06) and 1 can't check ($13.26) -> done, rule next_payer_contractual (the owner's count was 9).
  Their later payers: KY Medicaid 3 ($134.86), Medicare supplement carriers 4 ($349.90), Anthem / BCBS 1 ($83.30), an
  indemnity carrier 1 ($13.26). Work now 6,714 -> 6,706 ($2,037,736.83 -> $2,037,168.77); can't check 43 -> 42; done
  6,941 -> 6,950. Nothing appears or vanishes.
- 5 keep their state with a new basis and step: PR-288 for Medicaid members (Humana's Medicaid plan, CLP06 MC), $1,187.76:
  "bill the patient" -> "never a bill to the patient; get a retroactive referral and send a corrected claim" (deadlines
  from Jun to Nov 2027). Of the 24 open PR-288 items, 22 are Medicaid members and none is told to bill the patient; 0
  remittances are "unclear" on the bill path.
- Bill-the-patient items by service year (the steps now name it; the batch decision is the owner's): work now 2025: 25
  ($2,112.35), 2026: 46 ($4,915.74); can't check 2023: 7 ($244.31), 2024: 35 ($2,868.63).
- A17 classes before and after: identical (0 of 234 summary keys and 0 finding verdicts differ; the 14 items are
  ALREADY_DONE / MOVED_TO_PATIENT in A17 on both). Work now open: CORRECT 4,969 ($778,108.31), ALREADY_DONE 2,821
  ($775,581.27), ALREADY_WORKED 91 ($27,749.75), DUPLICATE 213 ($199,494.70), WRONG_AMOUNT 2,063 ($129,738.01),
  UNDECIDED 1,283; closed wrongly 5 ($147.79); left on the table (in scope): DENIED 401 ($28,939.69), NEVER_ANSWERED 114
  ($17,568.16), SHORT 270 ($8,829.69), WORKED_WAITING 7 ($253.90).
- Hand trace: all 14 moved items from the raw rows (`$U/hand_trace.md`): right 9, unclear 5 (the Medigap and indemnity
  carriers' CO answers: see open question 3), wrong 0 after one fix (a later answer with CO-45 -$127.81 and CO-B7
  $142.11 on a $14.30 line showed $142.11; the reason now shows the net and every CO code, and only a contractual
  adjustment with no other group settles it: re-decided on the world, 9 of 9 show the item's own amount).
- The people fixes move no item in detection (pages and api only).

## Needs from others
Failing tests in `plans/progress/fx-people_needs_tests.py` (not collected by the suite; run it by path).
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
