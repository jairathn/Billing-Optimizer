# Unit replay-fix: progress

Branch `wip/replay-fix` (worktree `$S/tk/wt/replay-fix`), from the integration tip 7b2aeb0. Private folder
`$S/tk/units/replay-fix` (mode 700). The two code bugs the month-by-month replay confirmed (their verdicts are in
`$S/tk/replay/replay_finish_result.json`, result.verdicts; the first verdict there, one claim keyed two ways, is gapfix
G8's, not this unit's).

## Review round 1 (2026-11-28): the fix round

The review of 0cd46c8 (wf_47797c0b-c26; its tests are `$S/tk/units/replay-fix-r1/r1/test_r1_posted.py` and
`test_r1_held.py`, copied to `$S/tk/units/replay-fix/r1/`) found three things. All three verified: on 0cd46c8 its tests
gave 60 passed, 12 failed (each of the 12 one of the three findings, on file, compact and image stores). Merged the
integration tip ed245a1 (handoff only) first.

- rf-r1-1 (medium), verified. Marks made before the groups were recorded went through the month totals at every check
  while any fall was open (K_POSTED_GROUPS was written only once nothing was missing), and the month totals absorbed a
  loss that another group's gain offset, with a text naming a cause nothing checked. Fixed in `check_posted` and
  `_posted_by_month`: (a) the groups are recorded at that first check even with a fall open, so the month totals stand
  in once; (b) a month whose total did not fall starts again only when whole payer names explain its falls: a name
  that changed group took all its money, so in every month where it has money (months whose total fell left out) its
  group of now changed by at least that money, the same way, a takeback counting as negative money. A loss offset by
  another group's gain is caught unless the gain is all the money a name has in the window (that one check cannot tell
  it from a move: documented, with its dollars in the text); (c) `info.regrouped` = {slices, cents} and the text
  (`_moved_text`) gives the dollars and what to compare if no name changed group. The reviewer's patch did (a) and (c);
  (b) is mine, and it makes the reviewer's first-run test pass too (the reviewer had left it as a one-check leftover).
- rf-r1-2 (low), verified. When the marks move to the groups of now, a changed cell restarted from what it held, so a
  sub-$1 leak under an alias that comes and goes was never caught. Fixed: a changed cell keeps the fall it already had
  (`short = high - then`, at most $1), as the reviewer proposed. Caught at the third export in the test.
- rf-r1-3 (low, predates the branch), verified. Accepting posted money moves the cell's mark, but the accepted baseline
  kept the old cents, so a later loss in that cell up to that amount was not new and did not hold the run. Fixed in
  `_baseline`: an accepted posted_money key compares with 0; the accepted cents stay in selfcheck_baseline on record.
  And `offenders()` (the Data tab's list behind the check) no longer calls such a new fall accepted: an accepted
  posted_money key counts as accepted only when a person accepted what the run shown found.
- docs/TROUBLESHOOTING.md (posted_money paragraph) says all three.

Tests (tests/test_replay_selfcheck.py, invented data, file, compact and image stores): 18 failing tests first (commit
382b77d, red), then the fix (995abe7); then the reviewer's other posted and held cases ported as regression tests
(124 tests in the file). Break and restore (`$S/tk/units/replay-fix/break_restore.py`, output `break_restore.txt`):
groups recorded once -> 8 fail; the whole-name rule -> 6; signed money -> 2; every month of a name -> 3; the kept
shortfall -> 3; the accepted baseline -> 3; the accepted label -> 3; the moved dollars -> 6; restored exactly (`git diff` equal to the saved
diff). The reviewer's own files against this tree: 72 passed.

Real data, read only, in memory (`$S/tk/units/replay-fix/repro_posted_r1.py`, outputs `repro_posted_r1_before.txt` on
0cd46c8 and `repro_posted_r1_after.txt`): (A)-(C) as before (the recorded path unchanged). New: (D) marks with no
record and the alias learned before the first check: before ok with {slices 93}; after ok, 93 month cells and
$477,532.67 shown as moved, groups recorded (259 names), the next check ok. (E) the same with the planted $66,412.17
loss: both catch exactly $66,412.17, but before the groups stayed unrecorded (0 names) while it was open; after, 259
recorded. (F) the same with a planted offset in one recent month (a Medicaid KY name loses its postings of that
month, $611.81, and a Humana Medicare Advantage name that already held far more there gains the same): before ok,
nothing caught; after fail and held, $32,141.11 in 4 cells: the $611.81 and $31,529.30 of the moved name's money in
that same month (a month that lost money keeps its marks; the documented leftover below). With the groups recorded the
same offset is exactly $611.81.

## The two bugs and the fixes (remitwatch/selfcheck.py)

1. posted_money fell falsely when a payer name changed group (`check_posted`). The marks are per (service month, closed
   posting month, payer GROUP, measure); the groups followed the name rules of now (learned aliases,
   REMITWATCH_EMA_PAYER_ALIASES, a release's rules), so a moved name's money read as a fall of its old group and the
   critical 'hold' check put the app in safe mode (the replay's August: 93 cells, $477,532.67 of marks, 597 new items
   held). Fix: the marks record the group each EMA payer name had when they were made (meta `selfcheck:posted_groups`,
   `K_POSTED_GROUPS`, names with money in the window only). Each name's money is compared under that group (exact, also
   when a real loss is read in the same run as the regroup); once nothing is missing the marks move to the groups of now
   (cells the move changed start again from what they hold, keeping the fall they already had: round 1, rf-r1-2; others
   keep their mark). While a loss is outstanding the old groups stay, and names met for the first time take their group
   of now. Marks an earlier version made (no record): `_posted_by_month`, at one check only (round 1, rf-r1-1): a month
   whose total did not fall by more than $1 and whose falls whole payer names explain starts again under the groups of
   now, any other keeps its marks; the groups are recorded at that check. Text and CHECKS what-to-check name the cause;
   `info.regrouped` holds counts and cents only.
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

Round 1 (the review's three findings), on 386f026:
- [x] red tests 382b77d, fix 995abe7, the reviewer's cases kept as tests 368c053, the offenders label 386f026
- [x] break and restore of each part (above), restored exactly
- [x] the 13 self-check files (826 tests): newest libraries 826 passed; 3.12/SQLite 3.45 826 passed (python3 and
      3.11.1 in the full suites below). Earlier, on 995abe7 (773 tests): python3, newest libraries and 3.12 773 passed;
      3.11.1 659 passed, 34 skipped, 80 failed or errors, all the serialize limit
- [x] full suite, python3 (shard.sh) on 386f026: 4,437 passed, 29 skipped, 1 xfailed, 0 failed (a first try stopped on
      "No space left on device" while other units filled the disk; rerun)
- [x] full suite, 3.11.1/SQLite 3.40 (shard.sh) on 386f026: 4,196 passed, 53 skipped, 1 xfailed, 217 failed or errors:
      the same 217 tests as the 3.11.1 run at 0cd46c8, every one `AttributeError: ... no attribute 'serialize'`; none in
      test_replay_selfcheck.py (85 passed, 39 image cases skipped there)
- [x] docs test with the private word list: 40 passed; static/ unchanged (no node checks needed)
- [x] trial merges (`git merge-tree`) with wip/replay-fix-2a, wip/replay-fix-2b, wip/gapfix, wip/routes, wip/ui-a: clean

## Next command

None: the unit is done (round 1 included). The integrator merges wip/replay-fix (it touches remitwatch/selfcheck.py,
docs/TROUBLESHOOTING.md, tests/test_replay_selfcheck.py and this file). wip/replay-fix-2a and wip/replay-fix-2b merged
0cd46c8, so they need round 1's commits too (merge wip/replay-fix again; the round-1 hunks are in check_posted,
_posted_by_month, _moved_text and _baseline, apart from the vanished and facts code 2b changes). To redo the real-data
reproduction: `RW_TREE=<tree> python3 -I -B $S/tk/units/replay-fix/repro_posted_r1.py` (it includes the cases of
repro_posted_fix.py) and `RW_TREE=<tree> python3 -I -B $S/tk/units/replay-fix/repro_heldfacts_fix.py` (read only, in
memory). The break and restore: `python3 $S/tk/units/replay-fix/break_restore.py <tree> $S/tk/units/replay-fix` (it
needs selfcheck.fixed.py and r1fix.diff saved from the tree first).

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
- The one check of an earlier version's marks (once per store, the first after this release, only if a cell fell):
  a month that both lost money and had a payer name move keeps its marks, so its gap includes the moved name's money
  there (real data (F): $32,141.11 shown for a planted $611.81; caught and held either way; a person accepts once the
  export is checked). A loss offset by a name whose whole money in the window is the gain is let go there, with its
  dollars in the text. Two names that swapped groups in one month are not followed (a fall shown, a person accepts).
  Inferring each moved name's old group from the months it moved in would make these exact; not done (it could
  misplace a loss if a guess were wrong).
