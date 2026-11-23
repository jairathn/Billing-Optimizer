# needs_E: wiring remitwatch/archive.py (unit E -> the phase-3b hook-in agent; notes for C, G, H, R, F, INT)

Status: FINAL for phase 3a. Unit E owns only `remitwatch/archive.py` and `tests/test_archive.py`; it edited no other file.
Everything below is a request to the owner of that file (in phase 3b: the hook-in agent). Each hook is a call or two; the
logic is in archive.py. The anchors are code text as it stood on 2026-11-23 (line numbers move; the text is what to find).

## 0. What the module does, in one paragraph, and its names

Old, settled bills leave the store the daily run loads, into gzipped JSON-lines files the run never reads (server: a folder
beside the store, `REMITWATCH_ARCHIVE_DIR` else `<store dir>/archive`, 0700/0600; serverless: the state folder's
`archive/`). A bill leaves only when (a) its newest service AND its newest remittance or EMA posting are older than the
horizon (`REMITWATCH_RETENTION_MONTHS`, default 36, floor 24), (b) none of its items is open (work now, waiting, can't be
checked; with no state, a person's open/started/sent status; a stale row only while started or sent), (c) no open or worked
routine-rate pattern lists one of its lines, (d) no open remittance-level item (a payment EMA never posted) is on its
remittance, (e) no other item that stays names it in its proof, note or evidence (a duplicate "already paid under claim
..."), (f) it was not reopened in the last 90 days and no reopening is waiting, (g) every service year of its 835 lines has a
kept baseline (below). Its 835 claims, lines, adjustments, remarks, links, EMA export rows, 837 rows, items and their history
leave; the remittance envelope (transactions, provider-level adjustments) never does. A batch is written, read back and
checked record by record before one transaction moves its index in and its rows out (counts must match, items a person
changed meanwhile keep their bill). A/R snapshots pulled more than 13 months ago go the same way, and the posting ledger's
postings go to the archive before the ledger's own retention deletes them. Find a claim and the claim view open archived
bills from the index; a reopening (or new data for an archived bill) brings the bill back whole.

The year baselines: once the archive takes lines of a service year (the year is "locked"), detection keeps that year's payer
rates, counts, cross-payer and Medicare figures as measured on the whole year (`apply_frozen_rates`), and keeps the year's
aggregate items as they were (`carry_locked_aggregates`). Without these two detect hooks the archive refuses to take any
bill with 835 lines (it waits for a kept baseline: `plan()['waiting_years']`, Data health "archive_waiting").

Names (all take the run's context; none reads the clock or the environment except `_today` and `settings`):
`settings`, `horizon_months`, `budget_mb`, `archive_dir`, `sink_for(db_path=|state=)`, `DirSink`, `StateSink`,
`run(conn, *, horizon=None, budget_state=None, sink=None, run_id=None, today=None, mode=None, deadline=None, log=None)`,
`plan`, `summary`, `archive_ar`, `before_ledger_retain`, `attach_ledger`, `ledger_postings`, `apply_frozen_rates`,
`carry_locked_aggregates`, `locked_years`, `search`, `resolve_bill`, `lookup_claim`, `lookup_finding`, `claim_db`, `restore`,
`request_restore`, `journal_entry`, `take_journal`, `process_requests`, `restore_touched`, `measure`, `record_run`,
`history`, `budget_state`, `data_health`, `panel`, `compact`, `SERVING_TABLES`, `ArchiveError`, `peak_mb`.

## 1. remitwatch/detect.py (two hooks; both proven on real data, section 7)

1a. `Detector.build_baselines`, its last statements today are

```python
            if abs(snap - med) <= 0.08:
                self.mppr[key] = {"pct": snap, "n": len(vals), "median": round(med, 3)}
        self.conn.commit()
```

Insert before `self.conn.commit()`:

```python
        from . import archive           # unit E: a year the archive took keeps the baselines measured on its whole year
        archive.apply_frozen_rates(self)
```

(Placing it right after the `self.market[key] = ...` loop, before `self.medicare = {}`, also works: the function sets the
Medicare figures and rewrites the locked years' payer_rates rows itself only when it runs after them. The real-data runs
used the end placement.) It also writes each year's snapshot (years before today less 24 months) once per change: a few
thousand rows.

1b. `Detector.run`, today

```python
        self.hook_fold()
        out = self.save()               # 12: heirs, states, keep_dirty
```

becomes

```python
        self.hook_fold()
        from . import archive           # unit E: a locked year's aggregate items stay as measured on its whole year
        archive.carry_locked_aggregates(self)
        out = self.save()               # 12: heirs, states, keep_dirty
```

What it does: the stored aggregate items (no line, claim or claim id; not FEE_SKIM/TAKEBACK/EMA-sourced) of a locked year
replace what was computed on the year's remainder; a new one is dropped; an item on a line such a new pattern took comes
back as it was. A person's status and notes stay theirs (save carries them by id as for any item).

Tests that prove them once wired: `tests/test_archive.py::test_a_year_the_archive_took_keeps_the_rates_measured_on_the_whole_year`
and `::test_a_locked_years_totals_stay_as_measured_on_the_whole_year` patch exactly these two positions today (the
`frozen_hook` fixture and a `Detector.save` wrapper); once wired, both still pass (the wrapper repeats the call, which is a
no-op the second time). Add to `RULE_FILES` in detect.py: `"archive.py"` (the rules hash then re-analyses once when it
changes).

## 2. remitwatch/ema_ledger.py (the ledger's retention archives first): ALREADY WIRED, nothing to do

Found in the checkpointed tree on 2026-11-23 (applied by another unit; E read the code and did not change it):
- `Ledger.retain`, between the `would_wait` hold and `c.execute("BEGIN IMMEDIATE")`: `archive.before_ledger_retain(self,
  cutoff)`; an `OSError` or `RuntimeError` (ArchiveError is one) keeps every posting, sets `self.archive_error`, and the
  summary reports `retain_archive_error`.
- `LedgerHandle.open` (serverless): `archive.attach_ledger(self.ledger, archive.StateSink(self.state))`.
- `open_for_store(db_path)` (server; pipeline.py's `ledger()` and cli.py use it): `archive.attach_ledger(lg,
  archive.sink_for(db_path=db_path))`, so the postings go to the store's archive folder wherever the ledger file is.

Tests that prove it: `tests/test_archive.py::test_ledger_postings_are_archived_before_retention_deletes_them` and
`::test_a_ledger_with_nowhere_lasting_to_write_is_not_archived_and_a_bad_folder_stops_the_delete`; on real data, section 7.3.
Still worth adding in tests/test_ema_ledger.py (its owner): a ledger opened with `open_for_store` and postings across the
cutoff, `retain(days, force=True)`, then `archive.ledger_postings(archive.sink_for(db_path=...), [link])` returns every
deleted posting (rows deleted == rows archived), and a folder that refuses the write leaves every posting and sets
`archive_error`.

## 3. remitwatch/pipeline.py (server cycle)

3a. Before the detection decision. Today, after the ledger refresh block:

```python
            heartbeat(store.conn, token)
        has_run = bool(store.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='detection_runs'").fetchone()) and \
```

insert before `has_run = ...`:

```python
        # unit E: reopenings asked in the app, and archived bills new data arrived for, come back before detection
        from . import archive
        arc_sink = archive.sink_for(db_path=db_path)
        back = [_ema_step(out, "archive_requests", archive.process_requests, store.conn, sink=arc_sink, log=beat_log) or {},
                _ema_step(out, "archive_touched", archive.restore_touched, store.conn, sink=arc_sink, log=beat_log) or {}]
        if any(b.get("changed") for b in back):
            changed = True
            out["archive_back"] = {"asked": back[0].get("restored", 0), "new_data": back[1].get("restored", 0)}
```

3b. After detection, once the store is reopened and refreshed. Today:

```python
        if "detect" not in out:         # detection rebuilds the provider tables itself; this covers an upgrade with no new files
```

insert before it:

```python
        # unit E: retention after detection (the items' states are fresh), then this run's size and memory
        from . import archive, runtime
        arc = _ema_step(out, "archive", archive.run, store.conn, sink=archive.sink_for(db_path=db_path),
                        budget_state=archive.budget_state(store.conn), run_id=out.get("run_id"), mode="server", log=beat_log)
        if arc and (arc.get("bills") or (arc.get("ar") or {}).get("files") or arc.get("errors")):
            out["archive"] = {k: arc.get(k) for k in ("bills", "lines", "findings", "cutoff", "errors")}
        archive.record_run(store.conn, run_id=out.get("run_id"), result=arc,
                           measured=archive.measure(store.conn, peak=archive.peak_mb(), mode="server", limit_mb=runtime.memory_mb()))
        heartbeat(store.conn, token)
```

`archive.measure` reads `dbstat` (about 1 s on the 800 MB store; SQLite reuses the pages the archive frees, so the file
does not shrink on a server and needs no VACUUM). `out.get("run_id")`: unit H's manifest sets it; until then None is fine.

Tests once wired: a fixture cycle (tests/test_parity.py style) with an 835 archive three years old and one new remittance:
after `run_cycle`, `archive_bills` has the old settled bills, `meta archive:history` has one entry, and a second cycle archives
nothing; a reopening asked with `archive.request_restore` is back (and its status set) after the next cycle.

## 4. remitwatch/cloud.py (serverless cycle)

4a. The journal. Today

```python
        journal = read_actions(state)
        done, left, wrote = apply_actions(conn, journal) if journal else ([], [], False)
```

becomes

```python
        journal = read_actions(state)
        from . import archive
        arc_sink = archive.StateSink(state)
        taken, journal = archive.take_journal(conn, journal, sink=arc_sink) if journal else ([], journal)
        done, left, wrote = apply_actions(conn, journal) if journal else ([], [], False)
        done, wrote = done + taken, wrote or bool(taken)
```

4b. Before the detection decision, today `pending = conn.execute("SELECT 1 FROM meta WHERE key='cloud:detect_pending'")...`:
insert before it

```python
        back = [archive.process_requests(conn, sink=arc_sink, log=log), archive.restore_touched(conn, sink=arc_sink, log=log)]
        if any(b.get("changed") for b in back):
            ingested = True             # the bills that came back are analysed in this run (or the deferred one)
            out["archive_back"] = {"asked": back[0].get("restored", 0), "new_data": back[1].get("restored", 0)}
```

4c. Retention in a run that did not analyse (its memory is free), with time left. After the detection block's
`conn.commit()` (today the line before `# What each clinician did: rebuilt after detection, ...`), insert:

```python
        arc: dict = {}
        if has_lines and not ran_detect and not remaining and "deferred" not in out and budget - (time.time() - t0) > 120:
            arc = archive.run(conn, sink=arc_sink, budget_state=archive.budget_state(conn), run_id=out.get("run_id"),
                              mode="serverless", deadline=t0 + budget - 90, log=log)
            if arc.get("bills") or (arc.get("ar") or {}).get("files"):
                out["archive"] = {k: arc.get(k) for k in ("bills", "lines", "findings", "cutoff", "more")}
                out["compact"] = archive.compact(conn)      # the image is this host's memory and upload: give the pages back
                ema_dirty = True                            # the store and the dashboard's copy are saved (items left)
```

and, right before the save block (today the comment `# Save only what changed, the store first: ...`):

```python
        try:                            # this run's size and memory, for the Data tab and the next run's horizon
            archive.record_run(conn, run_id=out.get("run_id"), result=arc or None,
                               measured=archive.measure(conn, peak=peak_mb(), mode="serverless", limit_mb=runtime.memory_mb()))
        except Exception as exc:        # noqa: BLE001  (a measure that fails never stops the run; it is logged)
            out["archive_measure"] = {"error": type(exc).__name__}
```

The entry lasts on the runs that save the store (every run that analysed, read a file, folded the journal or archived);
an idle run's entry is dropped with its unsaved store, which is fine: `budget_state` and the panel read the last saved one.
The peak recorded there is the run's peak so far (load, ingest, analysis); the save adds one copy of the image (its
serialized bytes), which `status.json`'s `memory_mb.peak` (written after the save) shows. Vercel's SQLite 3.40 has no
`dbstat`: `measure` then estimates table sizes from row counts and a 200-row sample (`exact: False`), about a second.

4d. The dashboard's copy. Two calls, both needed:
- `serving_tables(conn)`: append `archive.SERVING_TABLES` (archive_bills, archive_ids, archive_findings, archive_files less
  its checksum), so Find a claim and deep links open archived claims from the dashboard's copy. Today its last line is
  `return out + list(ema_reports.serving_tables(conn))`; it becomes
  `return out + list(ema_reports.serving_tables(conn)) + list(archive.SERVING_TABLES)` (with `from . import archive`).
- `empty_serving(path)`: after `actions_mod.ensure_hide_rules(c)`, add `from . import archive; archive.ensure_schema(c)`.
  Without it the copy has no archive tables and `build_serving` skips them without a word (it copies only columns the copy
  already has).
Cost, measured on the world after the floor run (41,516 archived bills): the copy grows from 128.7 MB to 146.7 MB (+18.0 MB;
gzipped 24.8 MB to 29.1 MB, +4.3 MB): archive_bills 10.6 MB, archive_ids 6.6 MB, archive_findings 0.7 MB. It grows by about
430 bytes per archived bill. If that is too much for the copy, the alternative is to keep the index in the archive folder as
one small file the dashboard reads only when a search finds nothing in the copy (not built).
Test once wired: tests/test_cloud.py style, a fixture store with one archived bill, `save_serving`, then the dashboard's
`/api/claims/search` finds the bill's claim id and `/api/claims/view` opens it (archived: true).

4e. Memory and VACUUM: measured on Vercel's real image in section 7. VACUUM runs only in a run that did not analyse.

Tests once wired: `tests/test_cloud.py` style: LocalState, a three-year-old fixture archive, `run_cloud_cycle` twice (the
first idle run archives and compacts; the second changes nothing); `state.list("archive")` has the bills file; a journal entry
`archive.journal_entry(claim, ...)` is taken by the next cycle and the bill is back.

## 5. remitwatch/api.py and the new remitwatch/api_ops.py (C2/C1 and the 3b agent)

Sink on each host: server `archive.sink_for(db_path=DB_PATH)`; serverless `archive.StateSink(SERVING_COPY.state())`.

- `/api/claims/search`: after the hot results, `items += [dict(x, archived=True) for x in archive.search(conn, text)]`
  (by claim id start, bill id, or payer claim number; names are not searched in the archive: say so under the box when the
  names switch is on and nothing was found).
- `/api/claims/view`: when `not rem and not ema_ids` (today the 404), `db = archive.claim_db(conn, bill, sink=sink)`; if not
  None, run the same body against `db` (refactor the body into `_claim_view(rows_fn, bill)` with `rows_fn` bound to `db`),
  and add `archived: True, archived_at` from `archive.lookup_claim(...)`. `db` holds the bill's rows under the store's table
  names and schema, the provider-level adjustments that name its claim ids, payer_rates, detection_runs, the reports catalog
  and meta (less the key salt): story.item_story / item_verdict / next_step run on it unchanged (proven on 103 items).
- `/api/findings/{id}`: when the item is not in the store, `archive.lookup_finding(conn, fid, sink=sink)` (the item, its
  history with an "archived" event that says why and names the rule `archive.retire` version 1).
- New `POST /api/archive/reopen {claim_id | finding_id, reason}` (signed-in): server `archive.request_restore(conn, claim,
  actor=user, reason=..., finding_id=fid, status="open")`; serverless `cloud.record_action(state, archive.journal_entry(...))`.
  Reply: "It comes back with the next run (within a day, or press Run now)".
- `GET /api/ops/archive` -> `archive.panel(conn)` (reads meta only; works on the dashboard's copy; writes nothing), for the
  Data tab panel "Store size and memory" (static/ops.js); `archive.data_health(conn)` items join the Data health list.
- A CLI command (cli.py): `archive [--dry-run] [--horizon N]` -> `archive.run(...)` / `archive.plan(...)`, and
  `archive-restore <claim id>` -> `archive.restore(...)` under the pipeline lock.

## 6. Other units

- G (selfcheck): `archive.budget_state(conn)` is the last run's peak against its budget; `archive.run(..., budget_state=...)`
  tightens the horizon by 6 months per run over 80% (never below 24), and relaxes 6 at a time under 60%. Data already archived
  stays archived. G may call it with its own state instead; do not call both in one run.
- H (runlog): log `archive.run`'s result (counts, files, errors) as the step's event; the decision log sees archived items
  vanish from findings: treat ids in `archive_findings` with `archived_at` of this run as rule `archive.retire` version
  `archive.RULE_VERSION`, change {"location": ["store", "archive"]}, evidence = the file name.
- R (results): items moved to the archive are counted from `archive_findings` (status, work_state, shortfall_cents,
  recovered_cents, archived_at) so week-by-week totals keep them.
- F (docs): retention and archives (the rules above, the floor, the locked years, where files live, never deleted, how to
  reopen); `REMITWATCH_RETENTION_MONTHS`, `REMITWATCH_ARCHIVE_DIR`, `REMITWATCH_MEMORY_BUDGET_MB` in .env.example and the
  runbooks; README's module list names archive.py.
- activity.py (owner): the Providers tab is rebuilt from the store's lines; once lines are archived its oldest months thin out.
  If those months must stay, keep provider rows of months before `archive.locked_years` in `activity.refresh` (not done).
- INT/B: the pooled MPPR_POLICY item (no year) is measured on the store's lines only; its dollars fall when old lines leave
  (section 7). Acceptable for a renegotiation insight, but if its figure must cover history, give it a year or carry it.

## 7. Measured on real data (a world copy and Vercel's real image), aggregates only

All runs: a frozen copy of the package (archive.py identical to the repository's, sha1 b928304), today 2026-11-22, both
detect hooks of section 1 patched in at their positions. Bill and item ids stayed in sqlite files under the work dir; the
figures below are counts and sums.

### 7.1 The world copy (a server store, 801 MB, built with b0_build.py)

Before: 193,542 claims, 420,349 lines, 850,463 adjustments, 259,311 remarks, 71,508 EMA export rows, 32,696 remittances;
17,935 items (6,188 done, 6,249 expired, 5,490 to work now, 8 accepted). Detection 78.7 s, peak 889 MB.

What each horizon would take (plan only, nothing moved):

| horizon (months) | cutoff | bills | claims | lines | held: open item / pattern / named by another item | waiting for a baseline |
|---|---|---|---|---|---|---|
| 36 (default) | 2023-11-01 | 0 | 0 | 0 | 0 / 0 / 0 | 0 |
| 30 | 2024-05-01 | 15,662 | 20,239 | 39,384 | 15 / 12 / 1 | 0 |
| 24 (floor) | 2024-11-01 | 41,516 | 55,600 | 110,879 | 58 / 35 / 4 | 0 |
| 18 (below the floor) | 2025-05-01 | 53,531 | 74,398 | 154,502 | 262 / 111 / 0 | 11,516 |
| 12 (below the floor) | 2025-11-01 | 53,859 | 75,905 | 158,407 | 821 / 282 / 0 | 36,767 |

The default takes nothing today: the world's remittances are paid from January 2024 on, and a bill leaves only when its
newest payment is older than the horizon too. At 36 months the first bills leave in February 2027. Below the floor the
years after 2024 have no kept baseline yet (the "waiting" column), so even a tighter horizon would wait for them.

Runs (the default, then 30 months as the first tightening, then the floor):
- 36 months: 0 bills, 2.3 s; a second run: 0.
- Kill test at 30 months: the run was stopped after its first file was written and checked, before its index rows and its
  deletes. Store counts unchanged, 1 file on disk, 0 bills indexed. The next run found the same file name with the same
  content, did not rewrite it, and finished: 15,662 bills, 20,239 claims, 39,384 lines, 892 items, 11 files, 17.8 s.
- 24 months: another 25,854 bills, 35,361 claims, 71,495 lines, 151,342 adjustments, 44,824 remarks, 1,714 items, 15 item
  history rows; 18 files (16.8 MB gzipped, 184.9 MB of JSON), 31.5 s, peak 215 MB; 0 changed meanwhile, 0 errors.
- A second run at 24 months: 0 bills, every table's count the same.
- In all: 41,516 bills (newest service 2021: 6, 2022: 34, 2023: 3,709, 2024: 37,767), 55,600 claims, 110,879 lines (26%),
  2,606 items, 29 files, 26.4 MB gzipped (292 MB of JSON). Index rows: 41,516 bills, 96,076 ids (claim ids and payer claim
  numbers). Locked years: 2021 to 2024.

Rows in the store before and after: claims 193,542 to 137,942; lines 420,349 to 309,470; adjustments 850,463 to 617,341;
remarks 259,311 to 191,203; findings 17,935 to 15,329; item history 877 to 860. Unchanged by design: remittances 32,696,
provider-level adjustments 17,705 (the envelope and the de-duplication fingerprints), and on this world the EMA export rows,
links and handling rows (their bills are paid or serviced inside the horizon). Lines left in the store by service year: 2019:
12, 2021: 39, 2022: 74, 2023: 1,239 (held bills), 2024: 47,584 (November and December, and held bills), 2025: 151,951,
2026: 97,520.

Table sizes (dbstat): adjustments 203.6 to 160.9 MB, lines 182.2 to 145.1, claims 98.3 to 74.9, remarks 46.3 to 37.0,
findings 28.4 to 25.6. The file stays 801 MB with 97.3 MB of free pages that SQLite fills with new data before it grows the
file again (a server does not VACUUM; `compact` is for the serverless image).

Detection on the archived store: 59.5 s (was 78.7 s), peak 715 MB (was 889 MB). The baselines hook overrode 989 payer-rate
rows of the locked years with their kept figures; the aggregates hook kept 103 stored year aggregates, dropped 1 new pattern,
put back 4 items.

Every item before the archive, one by one (17,935): 15,328 the same (state, dollars, status, stale flag), 2,606 archived
(all settled: 940 done, $164,075.61; 1,666 expired, $148,095.51; none open), 0 gone without being archived, 0 new, 1
changed: the pooled MPPR_POLICY item (no year), $462.64 to $353.73 (section 9).

Worklist (work now, waiting, can't be checked; not stale), by signal: 44 signals; 43 have the same count and dollars before
and after; MPPR_POLICY has 1 item before and after, $462.64 to $353.73. In all 5,490 items and $1,525,545.58 before, 5,490
items and $1,525,436.67 after. No item on the worklist was archived; every removed item is in the 2,606 above.

Samples, taken before the archive and checked after: 100 archived bills (stratified by service year and by whether they
had items): each found by `lookup_claim` with all its claims, and by `search`: 0 problems. 103 archived items (100
stratified by state and category, and the items on the sampled bills): each opened by id (`lookup_finding`, ending in its
"archived" event), and its story, verdict and next step computed on `claim_db` identical to before the archive (rows,
reconciliation, gap, state, sentence, rule, action, deadline): 103 of 103, 0 errors.

The Data tab panel on the world after (`archive.panel`, exactly): "The last run used 215 MB of its 12,800 MB budget (2%).",
"The store the daily run loads is 802 MB.", "41,516 older settled claims (110,879 service lines) are in the archive, in 29
files; Find a claim still opens them.", "Claims with service in the last 24 months stay in the store, and every claim with
open work, whatever its age."

Restores on the final code (`restore`, then the next run archives them again): 100 archived bills by stratified hash, and 60
archived bills that carry items. Every table's rows came back exactly as archived (160 of 160 bills); the 94 items on them
came back with the same status, state, dollars, notes, stale flag and recovered amount, and told the same story on the store
as from the archive (94 of 94); the next run archived all 160 again with no error, and every table's count, the index
included, was then the same as before the restores.

### 7.2 Vercel's real image (a copy of live_state_1122, the serverless path)

Loaded as `cloud.load_raw` loads it (gunzip, deserialize), detection with both hooks, the archive at 36, 30 and 24 months
into a state folder (`StateSink` over `LocalState`), `compact` (VACUUM), then a fresh process loaded the archived image and
ran detection again. Today 2026-11-22.

| | before | after the archive (24 months) |
|---|---|---|
| image in memory | 516.4 MB (519.9 MB after detection) | 434.4 MB |
| gzipped (each run downloads and uploads it) | 103.4 MB | 85.1 MB |
| load | 2.8 s, peak 1,175 MB | 2.0 s, peak 994 MB |
| detection | 57.4 s | 43.4 s |
| the run's peak (load and detection) | 1,405 MB, 88% of the 1,600 MB budget ("warn") | 1,130 MB, 71% |
| items | 16,163 | 13,557 (the 2,606 archived, all settled) |
| worklist | 4,165 items, $1,363,229.35 | 4,165 items, $1,363,120.44 (the MPPR_POLICY drift) |

Largest tables before: lines 132.3 MB, adjustments 98.0, claims 78.4, EMA export rows 44.7, remarks 26.0, Dropbox file list
25.8, remittances 25.5, exposure 24.0. After: lines 95.1, adjustments 71.1, claims 54.9, EMA export rows 44.1, remittances
25.4, Dropbox file list 24.8, exposure 22.1, remarks 19.2. VACUUM: 520.7 to 434.4 MB in 1.2 s (in memory, in a run that did
not analyse). The archive itself: 36 months 0 bills; 30 months 15,662 bills (11 files, 16.0 s); 24 months 25,854 bills (18
files, 16.8 MB gzipped, 28.5 s); the archive files in the state folder 26.4 MB in all. It took the same bills and items as on
the world: the two hold the same 835 history, and the live image's items carry no person's work on those bills (its whole
history has 2 manual events).

What it means on Vercel: today's image runs at 88% of its budget, so with the hooks wired the run tightens the horizon from
36 to 30 and then 24 months (6 months per run while the last recorded run is over 80%), and the floor brings the run to 71%,
where the horizon holds. The save adds one copy of the image on top (its serialized bytes): about 434 MB instead of 520 MB.

### 7.3 The posting ledger and the A/R snapshots (never triggered by today's data at the default settings)

Ledger: a copy of the world's ledger (928,925 postings), retention set to 598 days so that it deletes something (at 1,100 days
nothing is old enough yet). `before_ledger_retain` wrote 113,559 postings to one file (3.1 MB gzipped, 2.5 s), read back and
checked, and recorded it in the ledger's `archive_parts`; retention then deleted 113,559 postings: equal. 50 of 50 sampled
claim links found in the archive by `ledger_postings`; a second call wrote nothing. The same on a server folder and on a
state folder.

A/R: the world's 2 snapshots (9,653 rows). Today nothing leaves. On 2027-12-22 (13 months on) both left: 2 files, 9,653 rows
in the files, 0 rows left in the store, both catalog rows "replaced" with 0 live rows and `replaced_by` naming the file (a
file seen again in Dropbox is not read again: "replaced" counts as done in ema_reports and the sync). Note: when no newer
A/R workbook arrives, the last one leaves after 13 months too (the brief's rule); the A/R list is then empty until the next
pull. C2's A/R panel can say so from `archive_files` (kind "ar").

## 8. What the real-data passes found in the module, and the fixes (each has a failing test first, then break and restore)

First pass (before the fixes), the 24-month floor on the world copy, detection before and after compared item by item:
1. Three open REMIT_NOT_POSTED items ($355.49, payments EMA never posted) vanished: they are remittance-level (a transaction,
   no claim), so no keep rule saw them, and their remittance's bills left. Fix: an open remittance-level item keeps every
   bill on its remittance. Test `test_an_old_service_paid_inside_the_horizon_stays_and_an_open_remittance_item_keeps_its_remittance_whole`.
2. A new NO_ANSWER appeared for a 2026 claim: the rule judges a payer's recent remittance activity, and part of it was old
   services paid recently. Fix: a bill leaves only when its newest service AND its newest remittance or EMA posting are older
   than the horizon (same test). This also keeps the answer-time measure (states.RESPONSE_YEARS) and the alert engine's
   500 days of payments whole.
3. Year aggregates of the years the archive took (G2211_DENIED x3 vanished, MIPS and SEQUESTRATION totals shrank) and one new
   SECOND_RATE pattern formed from four expired one-off underpayments (its support statistics were measured on the year's
   remainder). Fix: `carry_locked_aggregates` (hook 1b). Tests `test_a_locked_years_totals_stay_as_measured_on_the_whole_year`,
   `test_a_new_pattern_measured_on_what_the_archive_left_is_dropped_and_its_lines_items_come_back`.
4. 17 of 47 sampled archived items told another story from the archive: the payers' provider-level adjustments that name
   the bill's claim ids were missing from `claim_db`. Fix: `claim_db` copies them (all 47, then 103 of 103, identical).
5. `summary()` undercounted archived items by state (two keys collapsed into one). Fixed (GROUP BY the label).
Second pass:
6. A paid duplicate ("already paid under claim ..." of another bill) lost its proof and reopened (expired here; with an open
   window it would have been wasted work). Fix: a bill named in the proof, note or evidence of an item that stays, stays.
   Test `test_a_bill_another_items_proof_names_stays_until_the_item_goes`.
Third pass:
7. The second run at the floor archived 5 more bills (bills held only by items that had just left). Fix: the proof holds are
   computed to a fixed point from the items that stay, so one run reaches the final set (same test, second half).
From the invented tests:
8. A person who reopened an item after the plan, before its batch was built, would have seen the bill archived (the commit
   only rechecked changes made between building and committing a batch). Fix: inside the delete transaction every bill must
   still have no open item, no fresh pin and no reopening asked. A person's started or sent status counts as open whatever
   the stored state (the person's status wins in states.py; the stored state can be older than it). Tests
   `test_a_file_whose_every_bill_a_person_changed_meanwhile_is_written_but_not_listed`,
   `test_a_person_changing_an_item_while_the_run_works_keeps_the_bill`.
9. Pins were compared with the wall clock instead of the run's day (a test driving days broke). Fixed.
10. The query-plan test explained statements after the run, when the temporary key tables were gone, so it checked nothing.
    It now explains each statement as it runs (`_PlanCheck`); no table is scanned per row on the compact store.

## 9. Known limits (said plainly)

- The Providers tab (activity.py) is rebuilt from the store's lines: months whose lines left thin out (section 6).
- The pooled MPPR_POLICY item (no year) is measured on the store's lines; its dollars fall when old lines leave.
- Find a claim does not search names in the archive (claim id, bill id and payer claim number only).
- A cross-bill proof is protected when the staying item names the other claim (its id in a note, its claim or line key in the
  evidence). A rule that leans on another bill without naming it is not seen; none was found on the real data after fix 6.
- Real data never triggers the A/R (13 months) or ledger (1,100 days) parts at today's date: both were exercised on real
  data with dates moved (section 7) and on invented data in the tests.
- The archive is written by one cycle at a time (the pipeline's lock, the serverless lock); `restore` must run inside the
  cycle (process_requests, restore_touched), never from a web request beside a running detection.
- The dashboard's copy carries the archive's index: about 430 bytes per archived bill (18 MB for 41,516; section 4d).
- When no newer A/R workbook arrives, the last one leaves after 13 months too, and the A/R list is empty until the next pull.
- Until hooks 1a and 1b are wired, the archive takes no bill that has 835 lines (it waits for kept baselines; `plan()`
  says how many, and Data health shows "archive_waiting"). EMA-only and 837-only bills can still leave.
- At the default 36 months nothing leaves on today's data until February 2027. On Vercel the self-regulation will take the
  horizon to 24 months within a few runs, because today's image runs at 88% of its memory budget (section 7.2).

## 10. Tests (2026-11-23)

- tests/test_archive.py: 40 tests (both store kinds where the store matters): 40 pass on the system Python (3.13, SQLite
  3.53), venv2_compat and venv2_compat312; venv_sqlite340 38 pass and 2 fail only because Python 3.11.1 there has no
  `Connection.serialize` (the compact image is built by serialize).
- Break and restore: 41 breaks of archive.py (one per rule, check and step), each made at least one test fail, and each
  restore was byte-identical (sha1 of the file).
- Full suite once at the end: venv2_compat312 32 failed, 1,920 passed; venv_sqlite340 88 failed, 15 errors, 1,867 passed.
  None of the failures is in tests/test_archive.py except the 2 serialize-only ones, and no failure's traceback passes
  through remitwatch/archive.py. The others are in files other units are editing now (test_states, test_story,
  test_selfcheck grace, test_desk, test_fax, test_claims, test_ema_checks, test_uploads, review5, review6, review7) or are
  serialize-only on 3.40 (test_cloud, test_parity, test_ema_intake and others). test_docs fails on README's layout, which
  leaves out 12 new modules, archive.py among them: F's item in section 6.
