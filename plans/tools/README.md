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
