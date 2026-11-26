# Progress: unit hook (E, G and H wired into both cycles, then the owner's decision 1)

Brief: plans/briefs/2026-11-26_hook.md (with plans/brief_HOOK_2026-11-24.md and plans/briefs/2026-11-26_common.md).
Branch: wip/hook. Last updated: 2026-11-26 18:40 UTC.

## Done
- 2026-11-26 18:35 UTC: merged the integration tip (fc73dec) into wip/hook, no conflicts. After the merge, in python3:
  tests/test_hooks.py, test_selfcheck.py, test_runlog.py, test_diagnose.py, test_repair.py, test_archive.py: 423 passed;
  test_cloud.py, test_answer_needs_proof.py, test_ema_checks.py, test_docs.py, test_aws.py: 200 passed, 2 skipped.
- Earlier (2026-11-24/25, commits 5c75342..71b6971): the hooks of needs_E, needs_G and needs_H on both hosts; the 1 GiB
  fix in Store.from_bytes; the ceiling budgets (5657a45); api_ops.py, ops.js, the CLI commands (5954bf4); regulation carried
  over idle Vercel runs; self-check facts count only new input; a left_record check.

## Half done
- (none yet)

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
