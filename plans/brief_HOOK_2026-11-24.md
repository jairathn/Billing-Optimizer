# Brief: the hook-in unit (push 2's phase 3b), as of 2026-11-24

Whoever runs this unit (an agent, or a session picking the work up) works from this file and from push 2's own plan on
`wip/plans` (`git show origin/wip/plans:plans/wire/<file>`):
- `brief_HOOK.md`: the unit's brief.
- `needs/needs_E.md`, `needs/needs_G.md`, `needs/needs_H.md`: the exact hook bodies, each with the test that proves it.
- `PLAN.md`: the owner's words, orchestrator decisions 9 (no dollar floor) and 10 (dollars per biller-minute), and the
  release gates.

Paths in those files that start with `${S}` point to another session's scratch folder, which is gone. Use the inputs below
instead.

## What changed since push 2's plan was written

- The branch is `claude/epic-davinci-imb6tj`: push 2 merged with the release (main f601b6c), plus the detection fix
  batches. Start from its tip. Do not bump `DETECT_RULES`: the integrator bumps it once.
- The modules are built and on the branch: `archive.py` (E), `selfcheck.py` (G), `runlog.py`, `repair.py` and
  `diagnose.py` (H). Nothing the app runs imports `selfcheck` or `diagnose`, and the cycle never calls
  `runlog.start_run`. `remitwatch/api_ops.py` and `remitwatch/static/ops.js` do not exist yet. CLAUDE.md already describes
  the wired state, so the code must catch up to it.
- **The 1 GiB ceiling (new, measured 2026-11-24).** `Store.from_bytes` deserializes the image into the connection.
  SQLite caps a deserialized database at 1,024 MB (`SQLITE_MEMDB_DEFAULT_MAXSIZE`). Python cannot raise the cap, so any
  write past it fails with "database or disk is full". A plain `:memory:` database has no such cap; checked with invented
  data.
  - A run whose store grows past 1,024 MB on Vercel fails at detection's save. Measured: a 1,086 MB image failed exactly
    there.
  - Fix it in `Store.from_bytes`: deserialize into a source connection, drop the bytes, copy into a plain in-memory
    connection with the backup API, then close the source. The peak stays about twice the image, as today.
  - Make G's and E's budgets count the image against the ceiling as well as against the memory budget. Warn loudly well
    before it, saying in dollars and items what would stop being checked.
  - Failing test first. Give the test a small injectable copy step; do not build a 1 GiB database in the suite. Measure on
    the real image.
- **Vercel's memory.** These numbers come from the release code on the live site, 2026-11-24 10:28 UTC, with the full 837
  history:
  - image: 712 MB in memory, 144 MB gzipped;
  - memory: load 1,749 MB, detection 1,832 MB, peak 2,008 MB, against a limit of 2,278 MB.

  The merged code holds EMA's report tables too. Measured on private copies of that image:
  - with A/R and Production: 779 MB, 136 MB gzipped;
  - with A/R only: 720 MB.

  Peaks are in `plans/HANDOFF.md`. Push 2's own budget is 2,188 MB, and release gate 3 asks 1.75 GB. E's archive is the
  lever. Its `MOVED` tables include `claims_837` and `lines_837`. Measure E's horizons on the real image again: the 837
  history now starts in 2024-01.
- **Owner decisions in force:**
  - nothing defaults to accept (decisions 14 and 16);
  - no dollar floor (decision 9);
  - Vercel reads the A/R by default and Production only when switched on (decision 6), unless the owner decides otherwise
    on the memory question.

## Inputs for the real-data acceptance (private; never in the repository)

The scratch folder is the session's private folder: `$SP/world2`, `$SP/audit`.
- Server cycle:
  - `world2/world2.sqlite`: the post-catch-up store, EMA's four reports read by push 2's readers, and the full 837
    history from Vercel's image;
  - `world2/world2_ema_ledger.sqlite`: the posting ledger.
  - Work on a copy and delete it after.
- Serverless path:
  - `world2/img/store.sqlite.gz`: Vercel's image as saved, sha256 13f1b396...;
  - `world2/img_ema.sqlite` and `world2/img_ar.sqlite`: that image plus EMA's tables, uncompressed. Build them with
    `world2/build_img_ema.py <tree>`.
- `audit/scripts/p2_serverless.py <tree> <image> <today>`: loads an image as `cloud.load_raw` does and runs detection,
  printing memory only. `audit/scripts/m1_run.py`: the server cycle on a world copy (set `P2_DIR`, `P2_DB`).
- Real data is patient data. Print aggregates only. Every log, manifest and bundle byte from a real-data run must pass the
  PHI scan (names, birth dates, member ids, claim ids, check numbers): zero hits.

## Acceptance (from brief_HOOK.md, adjusted)

1. Every hook in needs_E, needs_G and needs_H applied on both hosts, each with its named test (invented data, both store
   kinds). Break each and see its test fail, then restore it exactly.
2. `api_ops.py` routes and `ops.js` panels, in plain words, phone width, no em dashes. `cli.py` commands: `diagnose`,
   `self-audit`, `archive --dry-run|--run`.
3. The server cycle on a copy of world2 runs twice, and the second run changes nothing. The serverless path on the real
   image (and img_ema) runs until `more` is false, then once idle. For each run, record:
   - the manifest;
   - the peak memory against the budget and the 1 GiB ceiling;
   - the time;
   - /tmp under 512 MB.
   Every invariant passes, or names a real bug with its owner.
4. H's five injected faults, each on a copy, are detected, contained or repaired, and logged. For the code-bug fault,
   hand only the bundle, CLAUDE.md and the repository path to a fresh agent, and record whether it finds the root cause.
5. A forced high-memory reading makes the next run shed work and stay under budget, with the alert and the panel saying
   so.
6. The suite runs once at the end in Python 3.12 and 3.11.1, plus both node checks. Commit on your own branch; do not
   push. Report what you built, what you measured and what is left.
