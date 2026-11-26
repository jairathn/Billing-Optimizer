# Progress: unit hook (E, G and H wired into both cycles, then the owner's decision 1)

Brief: plans/briefs/2026-11-26_hook.md (with plans/brief_HOOK_2026-11-24.md and plans/briefs/2026-11-26_common.md).
Branch: wip/hook. Last updated: 2026-11-26 19:45 UTC.

## Done
- 2026-11-26 18:35 UTC: merged the integration tip (fc73dec) into wip/hook, no conflicts. After the merge, in python3:
  tests/test_hooks.py, test_selfcheck.py, test_runlog.py, test_diagnose.py, test_repair.py, test_archive.py: 423 passed;
  test_cloud.py, test_answer_needs_proof.py, test_ema_checks.py, test_docs.py, test_aws.py: 200 passed, 2 skipped.
- Earlier (2026-11-24/25, commits 5c75342..71b6971): the hooks of needs_E, needs_G and needs_H on both hosts; the 1 GiB
  fix in Store.from_bytes; the ceiling budgets (5657a45); api_ops.py, ops.js, the CLI commands (5954bf4); regulation carried
  over idle Vercel runs; self-check facts count only new input; a left_record check.

- 0ae525a: items 2-6 checked against needs_E/G/H. Gap found and fixed: an archived bill found by Find a claim opened as a
  404 (api.claim_view now reads the archive's record through an in-memory copy held for the request, api._ARCHIVED_DB);
  archived search rows carry the page's fields; an archived item's panel (ops.js opArchivedItem) and the claim view say
  so and offer "Bring it back to the lists". Test: test_hooks.py test_an_archived_claim_found_by_find_a_claim_opens_whole...
  and the browser check (tests/ops_page.js) opens both at 1400 and 390 px.
- 8f38502: /api/cron/faxes wraps the fax line's reads in repair.resilient as cli._fax_run does (needs_H L2); test
  test_the_daily_fax_runs_reads_are_tried_again_and_a_send_never_is on both entries.

## Half done: the owner's decision 1 (Vercel analyses only on a press), the design being built
- vercel.json: the /api/cron/cycle cron goes; /api/cron/faxes stays (no analysis; it sends nothing unless
  REMITWATCH_FAX_SENDER=serverless, and the server sends by default). No cron "only keeps the site healthy".
- Vercel never starts a run by itself: /api/sync/freshen (page open), the Dropbox webhook, an upload's finish
  (api_uploads._wake) and a held report's "apply" start nothing there; their answers say "press Run the analysis now".
- The press: POST /api/ops/run (signed in, XHR). Vercel: preview 409, no storage 503, no CRON_SECRET 400; a run holding
  lock.json, or a press in the last 90 s (state file press.json), answers {started: false, running: true, text: "A run is
  already going ..."}; else press.json is written and cloud.trigger_cycle(base, "press", force=1) starts the same chain.
  Server: 404 (the server keeps its schedule). /api/sync/run on Vercel goes through the same press function.
- cloud.run_cloud_cycle: a forced run (a press) hands detection to a fresh invocation when the time left is short (as
  after a large read) and counts that as progress, so the chain continues; status.json carries "analysed" {at, run_id,
  findings} from the last run that ran detection (carried by runs that did not), and "unchecked" {n, cents, text} when a
  run shed work or left detection pending: what it could not check, in items and dollars.
- Pages: header chip on Vercel ("analysed <when> · only when someone presses Run the analysis now, Data tab"); Data tab
  ops panel section "Analysis on this site" with the button, the running state and the unchecked box; Feed panel on Vercel
  points to it; Upload and Setup pages say the same line.
- Docs: OPERATIONS.md, VERCEL_SETUP.md (crons list, test_docs checks each cron path and time), README (vercel.json line:
  "one daily schedule"), the release note line. CLAUDE.md line 87 still says the daily cron: NOT edited by this unit
  (agents may not change CLAUDE.md); flagged for the owner/integrator.

## Next
1. Check items 2-6 of the brief against needs_E/G/H (saved privately in $S/tk/units/hook/needs_*.md from
   `git show origin/wip/plans:plans/wire/needs/needs_<X>.md`) and fill any gap with a failing test first.
2. The owner's decision 1 (Vercel analyses only on a press).
3. Real-data runs on a private world copy ($S/tk/units/hook/).
4. Suites in 3.13, 3.12, 3.11.1 and the node checks.

## Numbers measured so far
- (from the earlier runs, see plans/HANDOFF.md section 0 item 2): three server cycles converge; five faults detected,
  contained or repaired; forced high memory sheds to level 4; NEAR_CEILING at 76% of 1,024 MB; PHI scan of 129 files: 0.

## Open questions
- (none yet)
