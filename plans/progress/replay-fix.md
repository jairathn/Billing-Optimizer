# Unit replay-fix: progress

Branch `wip/replay-fix` (worktree `$S/tk/wt/replay-fix`), from the integration tip 7b2aeb0. Private folder
`$S/tk/units/replay-fix` (mode 700). The two code bugs the month-by-month replay confirmed (their verdicts are in
`$S/tk/replay/replay_finish_result.json`, result.verdicts; the first verdict there, one claim keyed two ways, is gapfix
G8's, not this unit's).

## The two bugs and the fixes (remitwatch/selfcheck.py)

1. posted_money fell falsely when a payer name changed group (`check_posted`). The marks are per (service month, closed
   posting month, payer GROUP, measure); the groups followed the name rules of now (learned aliases,
   REMITWATCH_EMA_PAYER_ALIASES, a release's rules), so a moved name's money read as a fall of its old group and the
   critical 'hold' check put the app in safe mode (the replay's August: 93 cells, $477,532.67 of marks, 597 new items
   held). Fix: the marks record the group each EMA payer name had when they were made (meta `selfcheck:posted_groups`,
   `K_POSTED_GROUPS`, names with money in the window only). Each name's money is compared under that group (exact, also
   when a real loss is read in the same run as the regroup); once nothing is missing the marks move to the groups of now
   (cells the move changed start again from what they hold; others keep their mark). While a loss is outstanding the old
   groups stay, and names met for the first time take their group of now. Marks an earlier version made (no record):
   `_posted_by_month` sums each (service month, posted month, measure) across groups; a month whose total did not fall by
   more than $1 starts again under the groups of now, one that fell keeps its marks (the validated option 1, used only
   for that transition). Text and CHECKS what-to-check name the cause; `info.regrouped` holds counts only.
   Deviation from the verdict's option 1, on purpose: option 1 alone overstates a real loss read with a regroup (the
   invented case: $330 reported for a $130 loss; the replay's data: $477,532.67 reported for a planted $66,412.17 loss).
2. A held run still saved the vanished check's facts (`after_detection`). Fix: `_save_facts` runs only when the run is
   not held (beside `_set_baseline`, `_update_track`); a held run keeps the last good run's facts and input position and
   adds, with `_add_facts`, only the fingerprints of the new items it still lists (found on its own data; the verdict's
   optional refinement).

## State

- [x] tests/test_replay_selfcheck.py (46 tests; 34 failed on the tip, 6 passed: the no-change controls and the held
      run's new items, which the tip handled by its bug)
- [x] both fixes; the new tests pass
- [x] break and restore: groups ignored -> 25 fail; a held run saves its facts -> 12 fail; no facts for a held run's new
      items -> 3 fail; earlier marks not summed by month -> 2 fail; restored exactly (`git diff` equal to the saved diff)
- [x] real-data reproduction, read only (below)
- [x] docs/TROUBLESHOOTING.md: posted_money and items kept after a held run (section 3)
- [x] selfcheck tests in the four environments (13 files, 748 tests): python3 748 passed; newest libraries 748 passed;
      3.12/SQLite 3.45 748 passed; 3.11.1/SQLite 3.40 641 passed, 27 skipped, 80 failed or errors, every one
      `AttributeError: ... no attribute 'serialize'` (that build cannot serialize a store image; Vercel can), none in
      test_replay_selfcheck.py (31 passed, 15 skipped there) or test_selfcheck.py
- [x] full suite, python3 (shard.sh, 3 shards): 4,359 passed, 29 skipped, 1 xfailed, 0 failed
- [x] full suite, 3.11.1/SQLite 3.40 (shard.sh): 4,142 passed, 29 skipped, 1 xfailed, 217 failed or errors, all 217 the
      serialize limitation (the many-requests self-test among them), as on the integration tip
- [x] docs test with the private word list: 40 passed
- [x] pushed wip/replay-fix at each milestone

## Next command

None: the unit is done. The integrator merges wip/replay-fix (it touches remitwatch/selfcheck.py, docs/TROUBLESHOOTING.md,
tests/test_replay_selfcheck.py and this file; no other unit changed them as of 09:20 UTC). To redo the real-data
reproduction: `RW_TREE=<tree> python3 -I -B $S/tk/units/replay-fix/repro_posted_fix.py` and
`RW_TREE=<tree> python3 -I -B $S/tk/units/replay-fix/repro_heldfacts_fix.py` (read only, in memory).

## Measured (real data, read only, in memory; scripts in the private folder)

posted money (`repro_posted_fix.py`: the base ledger cut at Jul 31 and Aug 31, the aliases learn_payer_aliases learns
from the weekly rows each cutoff keeps: 0 in July, 1 name in August, Optum (UHC) -> VA Community Care):
- before (tip): August fails, 93 cells (46 insurer paid, 47 adjustments), $477,532.67, all under Optum (UHC); again on a
  second check. The control (July aliases kept) passes.
- after: August passes, 1 payer name changed group, 214 cells moved with it; a second check passes; 259 names recorded
  with the marks. The control passes.
- a real loss planted in memory (the moved name's postings of its largest closed cell, service 2025-09, posted 2025-10,
  328 postings, $59,752.73 insurer paid + $6,659.44 adjustments = $66,412.17) read with the learned alias: tip reports
  $477,532.67 across 93 cells (the loss buried in the false fall); the fix reports exactly $66,412.17 across 2 cells
  under Optum (UHC), the regroup waiting until it is read again or accepted.

held-run facts (`repro_heldfacts_fix.py`: the code's own after_detection on the 141 items the replay's September run
kept, with the verifier's fingerprints at the July, August and September cutoffs; August held by posted money):
- before (tip): the held August run rewrote 71 of the 141 fingerprints; September kept all 141 ($8,209.94), the 67 of
  the class ($3,871.34) among them.
- after: the held run left July's facts as they were (0 changed); September let 71 leave by new facts on their own
  claim ($4,408.21: all 67, $3,871.34, and the 4 the verifier named), and kept 70 ($3,801.73) whose claims show nothing
  new since July. Left by signal: SECONDARY_NOT_BILLED 52, NO_ANSWER 9, CREDIT_BALANCE 4, CLAIM_DENIED_UNPAID 3,
  UNDERPAID_VS_OWN_RATE 2, DENIED_DUPLICATE_UNPAID 1.

## Open questions

- Items the old code already kept for this reason on a live store stay kept until a person accepts them (the fix acts
  from the next held run on). The live server's 11-27 release learned its alias before its first posted check, so the
  posted bug most likely did not hold the live server.
