# HOTFIX 2b brief: a loud alarm when one run moves the worklist far more than usual (live code, interim guard)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad

## Why
The owner: "there are ways to double check all of these so one stray 835 doesn't destroy the whole codebase". Hotfix 2 closes the
stray-835 and future-date cases it knows about. Push 2 brings a full swing guard that holds a bad worklist. Until then the live
sites need a guard that does not care WHY the list jumped: if one run adds or removes far more items or dollars than runs normally
do, every page and the alert email say so, with the numbers, before anyone works the list. Interim and alert-only: it holds nothing.

## Where
Your own worktree: ${S}/hotfix2b/wt (branch hotfix/swing-alarm, from main 949c4db). Edit only there; never commit or push. Hotfix 2
(${S}/hotfix2/brief.md) is being built at the same time in another worktree and will be merged with yours by the orchestrator:
keep your change inside alerts.py and the smallest page and email hooks it needs, so the merge is easy. Its rules apply to you
(tests first with invented data, both hosts, break-and-restore, plain English, no em dashes, no PHI anywhere). Real data only on
copies: ${S}/live_state_1122/store.sqlite. Disk is short: keep at most one copy of the real store at a time in ${S}/hotfix2b/work/,
check `df -h /` before copying (stop and say so if under 3 GB free), and delete every copy when done.

## Build
1. remitwatch/alerts.py: a SWING rule. After each detection run (both hosts already call the alert engine after detection), compare
   the newest detection run with the one before it, from what the store already keeps (progress_snapshots per run: category,
   status, n, cents; detection_runs: version, summary by signal). Raise a high alert when open items or open dollars, overall or in
   one category, rise or fall by more than the thresholds; say exactly what moved (before, after, by category), how many files of
   each kind were read since the run before (counts only; never a file name), and what to do ("Before working new items, open the
   Data tab and check the files read since <time>. If one is wrong, tell <the setup page's contact> and do not work the new items
   yet."). When the rules version changed between the two runs, say so and use severity info (a release re-detects once).
   The alert stays on, re-raised by every run, for seven days after the run that moved, unless a person acknowledges it; it never
   clears itself at the next quiet run.
2. Thresholds from data, not guesses: measure the run-to-run changes in the live store copy's progress_snapshots history (overall
   and per category, items and dollars), and set each threshold above the largest normal change you see there (report the
   distribution and your choice). Name them as constants in alerts.py with a comment saying how they were set.
3. Every page shows an active SWING alert at the top (check where STALE_FEED shows today and make SWING show at least as loudly, on
   both hosts, including the Vercel dashboard copy), and the alert email includes it.
4. Tests (invented data, both store kinds): a flood (one run adds 1,600 items) raises it; a vanishing (one run closes or drops
   800) raises it; a normal day does not; a rules-version change gives info, not high; the alert survives the next quiet run and
   clears only on acknowledgement or after seven days; no file name or patient detail in the alert text. Break-and-restore.
5. Replay on a copy of the live store: run detection twice (the second with one invented stray 835 added through the real reader
   path on the OLD feed-date code to show the alarm fires on the flood; then on hotfix 2's code to show it stays quiet). Aggregates
   only.
6. Run the tests you added and the suites of the files you touched in python3; the orchestrator runs the four environments.
Return: what you built, the thresholds and the measured distribution, the tests, the replay result, files changed.

## ADDED (guard audit L3-9): count items that appear and vanish, not only net sums
Per-category sums net out when a run replaces many items with as many others (a relabel: the same money under new finding ids),
and they cannot see items moving between work states. The alarm must count, per category, the open items that APPEARED and the
open items that VANISHED since the previous run, by finding id: keep the previous run's open-item ids (a small table or a meta row,
both hosts; on Vercel in the store image), and alarm on appeared or vanished past the thresholds even when the net change is small.
A test: one run replaces 800 open items with 800 different ones in one category (net 0); the alarm fires.
