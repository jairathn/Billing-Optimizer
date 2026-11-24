# Handoff: where the work stands and what comes next

Keep this file current. Whoever works on RemitWatch next (any Claude session, on either account) starts here, then reads
`wip/plans:plans/README.md` (the original plan, briefs and research) and the owner's rules in `CLAUDE.md`, which lives on the
push-2 branch (`git show origin/claude/ecstatic-fermi-orfcgo:CLAUDE.md`; it is not on main yet, and its code map describes
push 2's modules, but its rules apply to every change). This file supersedes the "Next, in
order" section of the README on `wip/plans`.

Last updated: 2026-11-24, 04:30 UTC.

## 1. Where things are

- **Released 2026-11-24 04:22 UTC**: `main` is `f601b6c` (the live hotfix bundle and both review rounds). The server's
  `/api/health` showed `f601b6c` at 04:25 UTC. Vercel builds main itself; check its first run (10:17 UTC daily) in the state
  folder: `status.json` (no `error`, `memory_mb.peak` under 2,278) and `serving.json` `build` "7" (only the new code writes
  7). The first runs re-analyse once (rules 2.9) and read the six-week catch-up and the 837s; the swing alarm is expected
  to fire high on the catch-up run (about +900 open items, +$217k): acknowledge it once the files are checked.
- Before the release: both sites ran `main` at `949c4db`. The server's `/api/health` shows the commit it
  runs. Vercel's last run is in the state folder's `status.json` (Dropbox, `/RemitWatch-state`).
- **This branch** (`claude/epic-davinci-imb6tj`) is the integration branch. Its code is the live hotfix bundle:
  main + `hotfix/chaos-r0` (hotfix 2, H1-H11) + `hotfix/swing-alarm` (2b) + `hotfix/deadlines` (2c) +
  `hotfix/supersede-and-heirs` (2d) + I1 (no faxed first-level appeal to CGS, Medicare KY J15: myCGS or mail) + integration
  fixes: the practice's day in three places, a 2d fix (a site whose first runs saved no store is still a first load; a
  refused run keeps the proof), the swing alarm's item-by-item counts (`swing_moves`, guard finding L3-9), and a feed-date
  rule for missing weeks (more than 7 days with no payment from any payer, then newer files: the feed reaches the day before
  the gap). One rules version: `DETECT_RULES = "2.9"`. The merge history keeps the other session's four WIP commits.
- **On main since 2026-11-24** (see above).
- **New data in Dropbox** (`/Trizetto Data`, the folder RemitWatch reads): six weekly folders of remittances (2026-10-12 to
  11-20, with that week's 837s and EMA exports) and six folders of 837s (2024 to September 2026, about 5,400 files). The live
  sites had not read them as of this update. Four `.exe` files and one `.zip` of EMA exports sit in the 10-19 to 10-23
  folder: RemitWatch ignores both (the zip is chaos finding 0e-6, push 2).

## 2. What was verified

- Full suite in the four environments (3.13/SQLite 3.53; newest libraries; 3.12/3.45; 3.11.1/3.40.0) and both node checks,
  on the merged tree before the review fixes: all green except the failures 3.11.1 is allowed (no `Connection.serialize`).
- On a private copy of the live Vercel store (2026-11-23 10:19 UTC image), old code against new: no person's decision
  overwritten; 4,677 open items get later deadlines (calendar months, Medicare +5), none earlier; 13 items ($1,010) that read
  expired are open again; six remittances wrongly hidden by the old supersede rule come back; the release's own re-run
  raises no swing.
- Catch-up on a private copy, new code (the six weeks and about 1,276 of the 837 files): 1,472 new remittances read, 466
  files skipped as exact repeats, feed date 2026-11-20 (one remittance is dated 2026-11-27, in the future: the old code would
  take that as the feed date), 186 items closed as paid ($21,046), 71 left the findings without a payment ($12,981; being
  accounted for, see 4), 1,140 new open items ($206,976). The swing alarm fires (high) on the catch-up, as it should.

- Catch-up hand-trace done (`plans/catchup_trace_2026-11-23.md`): ingest exact, old and new code agree on every shared item,
  184 of 186 closures right, every departure explained except 12 whose primary remittance is missing. It also found that
  the detection logic live today (older than the hotfix) lists the same service more than once, keeps items on payer
  decisions the payer reversed, lists voided claims and services already paid, and never reads the 837s, so 1,986 claims
  sent and never answered ($927k billed, 713 of them VA Community Care) have no item. Those are the next phase's work
  (step 5), with that file as its acceptance cases; the hotfix does not change them.

## 3. Next steps, in order

1. Round-1 review fixes: all merged here (mr-1, mr-2, mr-3, mr-4 in-flight case, mr-5, mr-7, mr-8, mr-9, mr-10, mr-11,
   mr-12), each with failing-first tests (tests/test_feed_and_day.py, tests/test_notes_bodies_cli.py and others).
2. Round 2: all merged here: CH-1, CH-2, CH-3, CH-4, CH-6, CH-10, CH-14, and the money fixes T-C1 (a payment for one part
   of a service, 26 or TC, closes only that part), T-C2 (a claim answered only by a secondary stays listed until its
   primary's remittance is in), T-N5 (no item from an answer to a voided claim), CH-8 (a payer whose remittances stopped
   has its claims counted on the Data tab, not listed as never answered).
   Final checks on e0021cf: the suite in the four environments (1,234 passed in each of 3.13, newest libraries and
   3.12; 3.11.1 only the allowed serialize failures) and both node checks; the catch-up again on a fresh copy of the live
   store: every item that moved against the first run is explained (20 items on voided claims gone, $1,157.50; the 2
   split-service closures reopened plus 2 TC denials a 26 payment had closed and 1 claim denial; 30 never-answered items
   whose bill only a secondary answered, the primary's remittance missing and EMA showing payment pending, hand-checked).
3. Run the four environments and the node checks (`plans/tools/four_envs.sh`), and the catch-up harness on a fresh copy of
   the live store (`plans/tools/catchup.py`). Every item that closes or leaves must be explained by a payment, a newer copy
   of its remittance, or an heir.
4. Push this branch's tip to `main` (fast-forward; pushing to main is authorized after review). Check the server's
   `/api/health` shows the commit; watch Vercel's next run (`status.json`: errors, `memory_mb.peak` with the 837s; the limit
   is 2,278 MB, the last peak 1,529 MB).
5. Started 2026-11-24 04:35 UTC: the audit's foundations (a private EMA-ledger world from the four A/R and Production
   reports with push 2's readers; the independent auditor program, first pass on the post-catch-up store copy). Its
   outputs live in the session's private scratch folder; the auditor's code (no patient data) goes to plans/tools once it
   is validated, and its aggregate results to plans/. If that work is lost, start it again from claim_audit_design.md.
   The owner's decision (2026-11-23): ship the hotfix, then work every claim before the next release
   (`plans/claim_audit_design.md`: an independent auditor over every unit of service; not one item may be already worked or
   paid, no owed money missing, nothing closed wrongly). Detection accuracy, then full visibility. First the findings in `plans/catchup_trace_2026-11-23.md` (one item per unit
   of service across resubmissions and reversals; void claims; services already paid; secondary processing settles a COB
   denial; take-backs whose reference embeds the claim id; the 26/TC split; an 837-based never-answered pass), each with a
   failing test built from the traced pattern (invented data). Then full visibility (the owner's goal, 2026-11-23): with 837s, 835s, EMA's A/R and Production reports and the weekly exports
   now covering the same dates, every charge can be followed from claim sent (837) to payer's answer (835) to EMA posting to
   open balance, reconciled to the cent, every gap a named, ranked item. Plan: profile and join the sources over 2024 to now
   (the full 837 set is easiest to take from Vercel's store image once Vercel has read it: one file); hand-trace every kind
   of mismatch; then finish push 2 (`claude/ecstatic-fermi-orfcgo`: units E, R, C2, SWEEP fix stages, INT, HOOK, docs,
   verification, per `wip/plans:plans/wire/`) with the 837 as a first-class source.

## 4. Open findings

Round 1 (merge review, being fixed): mr-1 a payment date that is not a real day crashes the gap rule (high); mr-2 the Data
tab's feed check and the lists' warning still read the newest payment (high); mr-3 the hourly retry re-faxes a request for a
claim shown paid; mr-4 a fax in flight when its item is re-keyed stays on the old id; mr-5 a lost Dropbox answer after a good
store save stops the serverless site; mr-7 a single patient's note with five procedure lines is refused as several patients;
mr-8 days behind counted on the UTC day; mr-9 a status journaled for an old id never reaches its heir; mr-10 an old gap holds
the feed back for 120 days; mr-11 422 instead of 400 for bad bodies; mr-12 the send-appeals command prints visit dates.

Round 2 (misuse check): CH-1 (high) on Vercel a status pressed on a page drawn before a run erases the run's Paid at the next
fold; CH-2 a new serverless site with an empty data folder stops for ever (the cursor is written without a store save);
CH-3 after deploy a high swing banner about the old code's own earlier runs; CH-4 a send that finishes after the run marked
the item Paid, or after a hide, overwrites it with Sent; CH-6 missing weeks are not named anywhere; CH-8 one payer stopping
while others continue turns its paid claims into never-answered items; CH-10 Paid accepts $0; CH-14 an old id answers
404 instead of pointing to its heir.

Deferred to push 2 (documented, not fixed in the hotfix): CH-7 a remittance cut short with no complete copy is counted
silently (needs a Data-tab check with dollars); CH-12 a store-missing stop shows only on the Data tab; CH-13 a wrong
"two versions differ" message; mr-6 on the server a new or unmounted data volume still starts an empty store (the marker
shares the volume). Also the chaos round 0 backlog in `wip/plans:plans/wire/chaos_r0_backlog.md`.

## 5. How to work here

- **Test environments**: `python3` (newest Python, SQLite 3.5x); a fresh venv with `pip install --upgrade -r
  requirements-dev.txt` (newest libraries); Python 3.12 with SQLite 3.45 (Ubuntu's `python3.12`); Python 3.11.1 with SQLite
  3.40.0 (`uv python install 3.11.1`, then a venv). Unset `ANTHROPIC_BASE_URL` and every `SRFAX_`, `DROPBOX_`, `TRIZETTO_`,
  `SMTP_`, `MODMED_` variable before running tests. `plans/tools/shard.sh` runs the suite in three parallel shards (about
  5 minutes per environment on 4 CPUs).
- **Page tests** load Chart.js from `cdnjs.cloudflare.com`: the cloud environment's network allowlist must include it
  (it does in the `Billing-Optimizer` environment).
- **Real data** is patient data. Read it only in a private scratch folder, print aggregates only (counts, dollars, codes,
  payer groups, finding ids, the store's own keys), never names, birth dates, member or claim numbers, or one claim's dates,
  never into the repository, a test, a log, an issue or a pull request.
- **Getting the data**: the Dropbox connector (`list_folder`, `download_link`). Single-use links cost about 13 KB of tool
  output per 25 files, and an agent re-reads its whole history on every call, so keep each download agent to about 250
  files; `plans/tools/dlx.py` builds the work list from the agent's own listings and fetches each batch with curl, so the
  agent never re-types a listing or a link (set RW_TRANSCRIPTS and RW_DROPBOX_NS, see its header). The live store image is one
  file: `/RemitWatch-state/store.sqlite.gz`.
- **Usage**: big parallel fan-outs hit the 5-hour limit (about 25 agents in 25 minutes did on 2026-11-23). Keep two to four
  agents at a time, push after every milestone, and update this file before starting anything long.
