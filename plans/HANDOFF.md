# Handoff: where the work stands and what comes next

Keep this file current. Whoever works on RemitWatch next (any Claude session, on either account) starts here, then reads
`wip/plans:plans/README.md` (the original plan, briefs and research) and the owner's rules in `CLAUDE.md`, which lives on the
push-2 branch (`git show origin/claude/ecstatic-fermi-orfcgo:CLAUDE.md`; it is not on main yet, and its code map describes
push 2's modules, but its rules apply to every change). This file supersedes the "Next, in
order" section of the README on `wip/plans`.

Last updated: 2026-11-25, 03:20 UTC.

## 1. Where things are

- **Released 2026-11-24 04:22 UTC**: `main` is `f601b6c` (the live hotfix bundle and both review rounds). The server's
  `/api/health` showed `f601b6c` at 04:25 UTC. Vercel builds main itself; check its first run (10:17 UTC daily) in the state
  folder: `status.json` (no `error`, `memory_mb.peak` under 2,278) and `serving.json` `build` "7" (only the new code writes
  7). The first runs re-analyse once (rules 2.9) and read the six-week catch-up and the 837s; the swing alarm is expected
  to fire high on the catch-up run (about +900 open items, +$217k): acknowledge it once the files are checked.
  **Checked 10:29 UTC**: the run chain read every file (7,736 wanted, 0 remaining, 0 errors), memory peak 2,008 of 2,278 MB
  on the release code, `serving.json` build "7" (data_rev 8), 17,281 findings, image 144 MB gz (the full 837 history,
  158,573 claims 2024-01 to 2026-11). The 835 side of that image equals the private world copy's row for row; a second
  world copy with the full 837 history is in the session's private folder (`world2/world2.sqlite`, built by
  `world2/build_world2.py`) for the final measurements.
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
5. Audit pass 1 done (`plans/audit_pass1_2026-11-24.md`, auditor `plans/tools/claim_audit.py`), then checked by hand
   (`plans/audit_calibration_2026-11-24.md`: 196 verdicts traced, 131 right, 57 wrong, 8 undecidable; the auditor is
   trustworthy on already-done, duplicate and closed-correct verdicts, not yet on money left on the table, closed-wrongly,
   wrong-amount and already-worked: its biggest blind spot is EMA's Production ledger). Push 2 scored by the same auditor
   (`plans/push2_eval_2026-11-24.md`). **Decision (2026-11-24): finish push 2, not the released detection.**
6. **Now, in order** (the owner's bar: not one item already worked or paid, no owed money missing, nothing closed wrongly):
   1. Merge the release (main f601b6c) into push 2 on this branch: **done (2026-11-24 08:00 UTC)**, commits 9e8f006 (the
      merge: 13 files, 48 conflict spots, each resolved to keep both sides), 38b2dcb (follow-ups), 2712707 (payer patterns
      pursued by default) and 38d83d8 (follow-ups 2: the units rule never closes a downcode; detection records the newest
      person's event it saw, by id; the walk keeps its panel mark; the release's browser test drives push 2's fax preview). Resolutions to know: five-part hide rules (push 2) keep the release's preview, owner password,
      typed reason, count check and in-flight faxes left out (`/api/hide/preview` takes code, modifier and reason too);
      `appeal_deadline` uses calendar months and Medicare +5 (release) beside `legal_deadline` (push 2: shown only when
      later); the never-answered pass holds a claim whose own payer's answers stop before its due day (CH-8) unless EMA's
      postings for that payer reach past the feed (push 2's `answers_through`); the secondary-only answers (T-C2) feed push
      2's EMA checks; person status changes keep the release's checks and push 2's let-go reasons; the stale-feed fax hold
      names the gap and takes push 2's EMA vouch; the swing moves are recorded after push 2's hide hook; serving build 9;
      rules 2.12; the Detector's day is the practice's. Follow-ups found by the merged suite: the archive-rule hooks are
      wired (needs_B2 section 1); a save in the middle of a run (push 2's Production slot) records its image in the run's
      own status (else a run stopped after it made every later run refuse a good store); the one-time EMA clean-up saves
      the store only when it cleaned something (CH-2); push 2's panel actions send the status they showed (H3) and its fax
      preview sends once, asks for the note check and never acts on another item when its answer comes late (H1, H6); an
      item done by posting in EMA takes Paid with $0; a person's change in a run's start second counts as after it. A code
      a payer almost never pays is pursued until the owner accepts it (decisions 14 and 16; push 2 deleted 580 such items
      under a default 'accept'). Still failing: push 2's own unfinished docs and fax-command tests (7: README layout,
      runbook settings, the fax commands), its docs unit's work. The third full run (python 3.13) on 38d83d8 was in
      progress at the push; the other three environments are not run yet.
      Measured on a private copy of the real store (world.sqlite: the post-catch-up store with EMA's A/R and Production
      reports): the merged detection (at 2712707) ran in 129 s, peak 962 MB, server mode; its scoring by the A15 auditor
      was running (scripts in the session's private audit folder: `m1_run.py`, `p2_analyze.py m1/world_m1.sqlite m1`). Push 2's other unit branches (`wip/archive`, `wip/results`,
      `wip/today-data`, `wip/unit-h`, `wip/intake-sweep`) are not merged: product features around detection, integrated
      later per `wip/plans:plans/wire/PLAN.md` (units E, R, C2, H, SWEEP, INT, HOOK).
      From now on this branch carries push 2 + the release; `main` stays at the release for hotfixes (a hotfix goes on a
      local branch off `main`, reviewed, then pushed to `main`).
   2. Auditor pass 2 (rules A16): the 13 fixes in `plans/audit_calibration_2026-11-24.md` section 3, EMA's Production
      postings and A/R notes first; re-check by hand; then score the merged code. **Running (08:00 UTC)** as a background
      agent in the session's private audit folder (`claim_audit_a16.py`, outputs `a16/`, notes `a16_notes.md`). The merged
      code scored by A15 is in `plans/push2_eval_2026-11-24.md` section 4.
   3. Push 2's remaining needs (`plans/push2_eval_2026-11-24.md` section 2): done: no default "accept" (pursue until the
      owner accepts, decisions 14 and 16) and the archive-rule hooks wired. Left: the shared failure kinds, each with a
      failing test from invented data, and serverless memory. **Running (08:15 UTC)** as two background agents, each in
      its own git worktree from 389c267, committing on local branches for the integrator to merge: (a) settled elsewhere
      (T-C3/T-N3 a coordination denial the same payer settled as secondary, T-N6 processed to the patient on the same
      bill, T-N8 CO-151 M86), (b) money accounting (T-I1 PLB references that embed the claim id, T-I5 a reversal of a
      version not on file, T-C5/T-C6 recovered dollars and the closure's remittance). Each measures on its own private
      copy of the world and hand-traces samples. (a) is merged (6c34e08): already done on Work now in scope 173 ($15,690)
      -> 131 ($11,863); 23 of 23 hand-traced closures right; left: rule 8's patient-group items 52 ($2,540, the owner's
      ruling), 23 moved-to-patient claim denials whose own reason puts the amount on the patient or that another payer
      answered, 40 paid by another family, 10 voided by EMA, 6 small. Open points from it: two conventions for a recovery
      when a payer denies as primary and pays later as secondary (archive rule 3 records $0, the new closure the payment:
      choose one: the amount is what arrived, the Results tab attributes it); the primary's remittance behind 32 such
      closures ($5,066) is on no list (handed to the 837 pass); another payer's answer never closes an item (a supplement's
      PR-96 after Medicare's PR-1 stays listed). (b) is merged (acbf60c): closures whose recovery differs, in scope 172 ->
      76; closures on a payment taken back 6 -> 0; 170 payment lines on 126 claims taken back ($24,114); 13 wrongly closed
      items reopened; a Data-tab check counts reversals of payments not on file (64 claims, $26,627; 90 lines are second
      reversals of an amount already reversed, possibly duplicate deliveries). Open points from it, for the next batch:
      a service whose payment a take-back left unpaid has no item when the recoupment is under $500 (TAKEBACK lists PLB WO
      of $500 or more; 12 services in scope, $823): list such a service whatever the amount (owner decision 9, no dollar
      floor), not every small recoupment (about 320 a year are legitimate recoveries of double payments); a claim-level
      take-back is split across lines largest first; an empty SVC05 is read as 1 unit (also the auditor's fix 2);
      paid_cover counts a secondary only at half the expected amount, so a 20% coinsurance is not in a recovery; Medicare
      J1 entries are not read. (c) the 837 pass (plans/design_837_never_answered.md), (d) the duplicate, already-paid
      and voided leftovers and (e) item amounts (sizes by units and component, the calibration's rate rules) are running.
      Full suite on acbf60c (python 3.13): 2,640 passed, 0 failed; the other three environments not run yet.
      (d) is merged (8ed3b76): Work now in scope: voided 10 -> 0; paid 40 -> 21 (12 the auditor's artifacts: line
      position, a second payer's answer, the second payer's open share); duplicates 40 -> 24 (20 the auditor's artifact:
      Anthem's 77600 answered as 77436, each the only item on its service; 4 real left); already worked 36 -> 33; 21 of
      21 hand traces right. Open points from it: the ledger pairs a reversal with a payment by payer id, so a reversal
      under another of the payer's ids leaves a taken-back payment standing (guarded only in the secondary-settlement
      check); TAKEBACK items look at no service of the claim; a same-day payment on another bill of a once-a-day code
      gives only a hint; the whole-charge CO-45 rule misses claim-level payments, codes rebilled under another code and
      payers with no rate; a payer's re-processing that lists a line twice is read as two units; two same-day deliveries
      of one underpayment (T-I3); a claim-level second-payer denial should name the primary's payment (as rule 2 does
      for lines); an archive Done shows only its rule's label as its state basis.
      **A16 view of the merged tip** (0e6ef4a, scored 10:25 UTC by `plans/tools/claim_audit_a16.py` through
      `plans/tools/a16/`; Work now, services since 2026-01-01; push 2 alone in brackets): correct 1,449 [1,419]; already
      done 324, $77,541 [374]; already worked 29 [41]; duplicate 8 [112]; wrong amount 346 [330]; undecided 71 [75];
      closed wrongly 1 [9]; money left on the table 656 services, $66,823 [650, $63,277]. Already done is mostly paid
      off-feed by EMA's books (181 items, $61,896), in three kinds: (1) 77 denials whose line EMA shows paid, almost all a
      Kentucky Medicaid fee-for-service denial (CO-96, CO-24, CO-109) of a service UHC Community Plan had paid before
      (routes settled_in_ema, note, check_first; C6 never closes on a payment before the denial): **fixer fix6 running
      (10:40 UTC)**; (2) 54 never-answered and (3) 49 rejected-before-the-payer claims (the item carries no EMA link; the
      auditor ties them by the patient or the only matching claim) with insurance payments posted since: **handed to the
      837 fixer** (one "answered in EMA" rule for every never-answered item). The rest of already done: moved to the
      patient 66 ($4,157, mostly rule 8's patient-group items: the owner's ruling), not payable 21 ($7,237, owner rule
      4.2), paid on an 835 13, the 26/TC global 12, small kinds. Money left on the table: denials 233 services ($26,619,
      102 of them on items waiting for a follow-up date, which this view counts as off the list), never answered 213
      ($30,174: the 837 fixer's), short 198 ($9,319: the amounts fixer's).
      Fixers running at 10:45 UTC, each in its own worktree, committing on a local branch for the integrator: fix837 (the
      837 pass, `plans/design_837_never_answered.md`, base 2b72f47 + 6c34e08), fix4 (item amounts, base 383d99e), fix5
      (ledger and leftovers, base 0e6ef4a), fix6 (EMA paid before the denial, base 0e6ef4a). Integrate each (merge, the
      suite, push), bump DETECT_RULES once, then measure the whole on world2 with A16 and hand-trace.
      **15:15 UTC.** All four fixers stopped at the account's 5-hour usage limit at 10:45; they were resumed at 14:55
      after it reset (a fixer stopped that way resumes from its saved transcript with SendMessage). fix6 is merged
      (d27ce98): a Medicaid plan's denial of a service another payer had paid first (EMA's first payer on the claim is a
      Medicare Advantage plan; UHC's checks are posted in EMA under a 'Medicaid Community Plan' name, which the name rules
      put in the Medicaid catch-all and C6 read as the denier's own) and a duplicate denial of a service the same payer
      paid first are done, with the payments as proof rows. A16: already done on Work now 324 -> 249 (paid 204 -> 133),
      closed wrongly unchanged, money left on the table +$18.42. Left from it: the Medicaid catch-all is still read as one
      family in C6's same-payer reading and the older paid-before branch; C6 ignores the patient's copay when it compares a
      payment with half of the expected amount (an Anthem MA 99212 fully paid with the copay stays listed); about 70 of the
      denials carry KY Medicaid provider-enrollment reasons (B7, N570, N288): a question for the owner, no money on these
      items. The hook-in unit (E, G, H wired into both cycles, the Data tab panels, the 1 GiB fix) started at 15:10 as a
      background agent on branch `hook` from d27ce98.
      fix837 is merged (c52a54c): the 837s are a source of sends (a send date where EMA has none; a resend to the denying
      payer waits for its answer); claims only the 837s know get one item each (837_OPEN_CLAIM, priced at the payer's own
      rate per line and component, capped at the charge; voids end a claim; CH-8 holds a payer whose remittances stop; the
      Data tab names payers with no ERAs and holes in either feed); one "answered in EMA" rule serves every never-answered
      and rejected claim (exports, reports, 837s). A16 against the merged tip: already done on Work now 324 -> 219, paid
      204 -> 113, undecided 71 -> 41; of the 103 EMA-paid never-answered and rejected items 7 stay (claims dated only by the
      visit day with look-alikes). Detection 163 s (was 129 s). Open points from it: a claim sent to a payer as its
      secondary is priced at that payer's full rate (the 837's SBR01 is not read); export claims dated only by the visit day
      get no send-day twins; a print-batch duplicate made after the visit was paid stays listed; a dependent's 837 carries
      the subscriber's id; 2024 sends cannot be checked against EMA (its ledger starts later); story.py shows no 837 rows
      (full visibility); the claim filing indicator is not read; unanswered claims are not grouped by send week (T-N13).
      **Both merged, measured (c52a54c, 15:52 UTC; A16 on a fresh copy of world.sqlite, Work now, in scope; the merged tip
      in brackets):** correct 1,433 [1,449]; already done 155, $19,591 [324, $77,541] (paid 42, $7,030 [204, $64,163]);
      already worked 21 [29]; duplicate 5 [8]; wrong amount 343 [346]; undecided 41 [71]; closed wrongly 1 [1]; money
      left on the table 666 services, $66,686 [656, $66,823]. EMA-paid items still listed: 16 ($4,269) [181, $61,896].
      Full suite on c52a54c: 2,694 passed, 0 failed (python 3.13). Item by item against the merged tip
      (`plans/tools/a16/moves_w.py`): resent denials now wait for the resend's answer (correct: they were already worked);
      25 never-answered claims came onto the list ($8,695, owed); 76 never-answered or rejected claims left it
      ($16,546: 43 the auditor calls owed, 33 undecided). These 76 are being hand-traced by an independent workflow
      (16:00 UTC) before the merge is trusted: any wrongly dropped claim becomes a failing test.
      fix5 is merged (373da95, 16:08 UTC; no textual conflicts, the three fixers' tests pass together: 420): money taken
      back is listed at any amount, reversals pair under the claim payer's joined ids, Medicare's J1 entries are read, a
      secondary's share counts in recoveries, a whole-charge CO-45 that is no denial is not listed, one answer delivered
      twice is one item, a second payer's claim-level denial names the primary's payment. A16 on its own run: closures
      whose recovery differs 507 -> 123, money left on the table -$563, closed wrongly unchanged. Its open points, and
      every other fixer's, are in `plans/next_fixes_2026-11-24.md` (the next batch, in order; determinism first).
      fix4 is merged (32e4de6, 16:45 UTC): sizes by units and component, the payer's own rate by product class and
      recognised tier (PROVIDER_RATE_GAP for an unexplained clinician discount), backed underpayments. Merge resolutions:
      one pricing for every never-answered pass (fix4's price_unanswered_lines; the 837 pass's own pricer removed), and one
      component_of (two definitions existed; the later silently won and returned None where fix4's rate tables key on '').
      All four fixers' and the touched tests pass together (608). **Determinism measured (16:45 UTC)**: two fresh copies
      give identical items, but consecutive cycles on one store do not: 17,829 -> 17,509 -> 17,828 items, 321
      SECONDARY_NOT_BILLED items flipping every cycle, and a one-time settle of 34 PR-204 and coverage-date items after the
      first cycle. Cause: the EMA line summary keeps a bill's lines by the run's own items. Fixer fix7a started 16:50
      (release gate 4: a second cycle changes nothing).
      **f773a10** fixes the fix4 merge commit (it staged detect.py before a shadowed variable was renamed, so every
      never-answered claim raised TypeError at 32e4de6). Suite on f773a10: 2,798 passed, 0 failed. A16 on f773a10 (Work
      now, in scope; c52a54c in brackets): correct 1,301 [1,433]; already done 157 [155]; already worked 21 [21];
      duplicate 4 [5]; wrong amount 144, $10,193 [343, $22,344]; undecided 26 [41]; closed wrongly 1 [1]; closures whose
      recovery differs 123 [507]; money left on the table $72,808 [$66,686], up because fix4's firmer rate rule (a 60%
      share plus backing) dropped 151 underpayments ($9,400) that A16 (a 50% mode) still calls owed, while 201 it calls
      not short ($9,081) rightly left. That threshold is being settled by hand: workflow wf_74399af8-fcf (03:20 UTC), a
      stratified sample of 31 against each payer's own paid history.
      **The 76 dropped never-answered claims, hand-traced (workflow wf_d630475e-230):** 75 rightly dropped (58 paid, 8 a
      $0 answer or to the patient, 3 denied with the denial listed elsewhere, 5 owed but covered by another item or not
      yet due, 1 settled otherwise); 1 wrongly dropped: a bare $0 row on a batch check (no CARC, no allowed amount) taken
      as an answer, the claim still pending in EMA ($167.22). Its blind second trace was cut by the usage limit and is
      rerunning. Both rule bugs found (that one and the 26/TC one) are in `plans/next_fixes_2026-11-24.md`.
      Usage limits stopped every agent at 10:45 and again at about 17:00 UTC on 11-24; fix7a and the hook-in unit were
      resumed at 03:15 UTC on 11-25.
      **Determinism**: fix837 saw the SECONDARY_NOT_BILLED "already done" count swing between 690 and 424 across runs of the
      same code on fresh copies (it blames the ledger refresh after detection): to check (two runs, compare item by item).
      Next after them: the 837 as a source of sends and the never-answered
      pass (T-I2, T-I4, T-N11, T-N13; needs the full 837 history from Vercel's image), one item per unit leftovers
      (duplicates 40 in scope), sizes (T-C7, T-N9), paid-before leftovers (T-C4), short payments.
   4. Iterate (fix, audit, trace every disagreement by hand) until the auditor and the hand traces find no failure; then
      the release gates (four environments, node checks, catch-up on a fresh live copy, privacy scan) and release.
      Docs: push 2's modules, page scripts, the `ema-reports` command and nine settings are now in the README and both
      runbooks (merge fadf79b; tests/test_docs.py passes). Still stale, to fix before release: docs/OPERATIONS.md (around
      its lines 48, 357, 364) says A/R and Production reports are not read or are refused and that uploads are a
      server's only; the README's "What you get" table has nothing of push 2 and its "Upload from EMA" row says three
      exports, server only; the runbook's Backups section lacks the posting ledger's daily copy and its restore;
      .env.example lacks REMITWATCH_EMA_STRICT and REMITWATCH_EMA_STALE_DAYS; six settings read through helpers are named
      in neither runbook (REMITWATCH_RETENTION_MONTHS, _MEMORY_BUDGET_MB, _ARCHIVE_DIR, _TODAY, _CYCLE_BUDGET_SECONDS,
      _AUDIT_WASTED_ITEMS). CLAUDE.md overstates the wiring: nothing the app runs imports selfcheck.py or diagnose.py,
      the cycle never calls runlog.start_run, and repair is used only by runlog's panel code and diagnose (push 2's units
      G and H are built, not wired: its HOOK unit). A held ledger retention cannot be released on Vercel (only the
      server's `ema-reports --retain-force`).
   5. Full visibility (837 to 835 to EMA posting to open balance, per charge, reconciled to the cent), built on the merged
      code with the 837 as a first-class source.
   Started 2026-11-24 04:35 UTC: the audit's private foundations (a private EMA-ledger world from the four A/R and
   Production reports with push 2's readers; the auditor's first pass on the post-catch-up store copy). Its outputs live
   in the session's private scratch folder; the auditor's code (no patient data) is in plans/tools, its aggregate results
   in plans/. If that work is lost, start it again from claim_audit_design.md.
   The owner's decision (2026-11-23): ship the hotfix, then work every claim before the next release
   (`plans/claim_audit_design.md`). The acceptance cases from the catch-up trace (`plans/catchup_trace_2026-11-23.md`)
   still apply: one item per unit of service across resubmissions and reversals; void claims; services already paid;
   secondary processing settles a COB denial; take-backs whose reference embeds the claim id; the 26/TC split; an
   837-based never-answered pass. Push 2's plan for the rest is in `wip/plans:plans/wire/` (units E, R, C2, SWEEP fix
   stages, INT, HOOK, docs, verification).

## 3b. Decisions waiting on the owner (with the recommendation)

1. **Which host runs the daily analysis.** Vercel keeps the store in memory, and the merged code is near the limit.
   - The release's own run on the live image (2026-11-24 10:28 UTC: full 837 history, 712 MB in memory) peaked at 2,008 of
     2,278 MB.
   - The merged code adds EMA's report tables (779 MB with A/R and Production, 720 MB with A/R only) and heavier
     detection.
   - SQLite also caps a store loaded this way at 1,024 MB (measured): past that, the save fails. The hook-in unit removes
     that cap (`plans/brief_HOOK_2026-11-24.md`).
   - Measured 2026-11-24 15:00 UTC on Vercel's real image, loaded the way Vercel loads it (load and detection only, no save):

     | code | image | load peak | detection peak | detection time |
     |---|---|---|---|---|
     | release (live today) | 720 MB | 1,403 MB | 1,662 MB | 37 s |
     | merged, A/R only | 720 MB | 1,397 MB | 1,762 MB | 105 s |
     | merged, A/R and Production | 779 MB | 1,510 MB | 1,813 MB | 129 s |

     The release's 17,281 items equal the live site's. The live site runs about 1.75 times slower and adds about 170 MB
     before detection and 176 MB at the save. So the merged code there would peak at about 2,110 MB with A/R only and
     2,230 MB with Production, against 2,278 MB. Detection alone would take about 185 to 225 s of the run's 240 s budget,
     so it would need its own invocation every day.
   - Options: (a) the server is the one host that analyses; Vercel keeps the dashboard copy and people's actions
     (recommended: no memory ceiling); (b) Vercel analyses too, reading the A/R only, with the archive trimming old
     settled claims (unit E: 24 months at most); (c) a larger Vercel function, if the plan allows it.
2. **Rule 8, patient-group denials** that are not deductible, coinsurance or copay (PR-204 plan exclusion, PR-288
   referral, PR-27 coverage ended). The choices are to work them (bill the patient, or find the right plan) or to count
   them done. About 66 items on Work now ($4,157) wait on this.
3. **The recovered amount when a payer denies as primary and pays later as secondary.** Today two rules record it two
   ways. Recommended: the amount that arrived.
4. **Owner rules 4.1 and 4.2 in the product** (the auditor already applies the recommended versions):
   - 4.1: a provider's own rate counts only as a recognised tier (100% or 85% of the payer's full rate). An unexplained
     discount is listed once per payer and provider.
   - 4.2: codes a payer never pays, for example G2211 beside an E/M with -25 at Medicare, and 99024. Twenty code-change
     items ($7,150) on Work now are for codes the payer almost never pays. Today the product pursues them until the owner
     accepts them (decisions 14 and 16).

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
