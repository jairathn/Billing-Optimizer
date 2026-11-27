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
- Measurement of the branch on a private world copy (running: `bash $U/measure.sh br <worktree> keep`); the tip's run
  is in `$U/snap_tip.sqlite` and `$U/a17_tip/`.
- F3a-routed needs (the 837 label in the story, the never-answered page by send week): waiting for "Merge wip/fx-837" on
  the integration tip.

## Next command
- `git fetch origin && git log origin/claude/ecstatic-fermi-orfcgo --oneline --grep 'Merge wip/fx-'`: when batch 1 is
  merged, merge the tip, run `$U/bin/t.sh -- -q tests/test_fx_people.py tests/test_decisions.py
  tests/test_decisions_review.py tests/test_story.py tests/test_states.py tests/test_desk.py tests/test_hotfix2_pages.py`,
  then build the F3a-routed needs (tests in `plans/progress/fx-837_needs_tests.py`).

## Numbers measured
(after the branch run)

## Needs from others

## Open questions (for the owner)
1. A Medicaid member's other patient-group reasons (plan exclusion, coverage ended, before coverage): the decision
   covers the missing referral only, so they are billed as decided. Medicaid rules restrict billing a member for a
   non-covered service too (prior written notice in most states). Should they follow the same rule?
2. A later payer that paid part, took the rest off as contractual and put nothing on the patient: still billed with the
   caution "if its answer settled the service, do not bill: press Done". Should it go to Done like the
   contractual-only answer?
