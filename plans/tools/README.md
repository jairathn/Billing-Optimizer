# Tools used to verify the live hotfix (see ../HANDOFF.md)

- `shard.sh`, `four_envs.sh`: the suite in the four environments CLAUDE.md requires, in parallel shards.
- `measure.py <tree> <store copy> <out.json> <label>`: one analysis and the alert engine on a private copy of a store with the
  code of <tree>; writes aggregates only (open items by signal, deadlines moved, people's statuses kept, alerts).
- `catchup.py <tree> <base store> <workdir> <file list>`: analyse a copy (state B), read new files through the same router
  the cycle uses (`dropbox_sync.ingest_new_files`), analyse again (state A); keeps B and A in <workdir> for tracing and
  prints aggregates: what was read, the feed date, open items before and after, and how each item moved.
- `dlx.py`: lets an agent download Dropbox files through the connector's single-use links without re-typing listings or
  links (it reads the agent's own transcript; set RW_TRANSCRIPTS). Steps are in its docstring.
Run anything that opens real data with `python3 -I`, in a private folder, and print aggregates only.
- `claim_audit.py` (rules A15) and `claim_audit_a16.py` (rules A16, the current one): the independent every-claim auditor.
  It imports nothing from remitwatch. It builds each unit of service's truth from the raw 835, 837 and EMA tables of a
  private store copy, then judges every item against it (correct, already done, already worked, duplicate, wrong amount,
  undecided, closed wrongly, money left on the table). `--selftest` runs its 182 checks on invented data.
  Results and method: `../audit_calibration_2026-11-24.md`, `../push2_eval_2026-11-24.md` and `../HANDOFF.md`.
- `a16/`: the scripts that score a detection run with A16. Set `AUDIT_DIR` to the private audit folder: it holds the world
  copies, `world_ema_ledger.sqlite` (or `p2/ema_ledger.sqlite`) and `a16/pass1/audit_result.sqlite`.
  - `p2_analyze16w.py <world copy after detection> <tag>`: scores views S (every open item) and W (the Work now list).
    It writes `$AUDIT_DIR/a16/p2/out_<tag>/` and prints aggregates only.
  - `headline.py <out dir> verdicts_w lot_w`: the headline tables (in scope = services billed since 2026-01-01).
  - `done_breakdown.py <out dir> verdicts_w`: already-done items on the list, by signal and by what settled them.
  - `offfeed_probe.py <out dir> <world copy>`: items EMA shows paid off-feed, crossed with the code's own fields.
