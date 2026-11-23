# needs_ema_ledger.md: what existing files must change to wire in `remitwatch/ema_ledger.py`

Builder 2 (the posting ledger, apply and holds, retention, refresh Part A and Part B, the claim-link map, the serverless
handle). No existing file was edited. Everything below is for the wiring phase: file, function, behaviour, and the call
into `ema_ledger` (import it as `from . import ema_ledger` inside functions; it imports `ema_reports`, `runtime` and,
lazily, `detect`, so a module-level import from `detect` would make a cycle).

## 0. What the module gives the wiring (quick reference)

| Name | Use |
|---|---|
| `ledger_path(db_path)` | server ledger file: `REMITWATCH_EMA_LEDGER`, else `ema_ledger.sqlite` beside the store |
| `Ledger.open(path, readonly=False, scratch=False)` | open or create (0600, WAL on a server; `scratch` = journal and sync off). Raises `RuntimeError` for another schema, `FileNotFoundError` for a read-only open of a missing file |
| `Ledger.seq() / files() / file(id) / boxes() / stats()` | change counter; applied files; their boxes (current and partly replaced by default); counts and size for the CLI |
| `Ledger.retain(days=1100)` | drop postings whose service date is 1,100 days before the newest posting (refresh calls it) |
| `Ledger.forget(file_id)` | take one file out (CLI `--reread`); `sync_catalog` then marks it `reread` |
| `Ledger.copies_to_drop(keep=None)` | files with no live rows that `REMITWATCH_EMA_REPORT_KEEP` (default 2) newer live files cover whole: their kept upload copy may go |
| `Ledger.backup(dest)` | SQLite online backup, written to `dest.part` and moved into place, mode 0600 |
| `apply_production_file(store, path_or_stream, rec, *, file_id, via, origin, force, ledger, deadline, serverless, log, source_name=None)` | called by `ema_reports.ingest_file`; a stream of CSV bytes also works (file id = SHA-256 of what was read when `file_id` is None) |
| `sync_catalog(store, ledger, file_ids=None)` | catalog statuses from the ledger; restores rows a crashed run lost; marks `reread` what the ledger lost |
| `refresh(store, ledger=None, *, parts='auto', log)` | Part A (needs the ledger) and Part B (store only); one store transaction at the end |
| `map_links(conn)`, `mark_facility(conn)`, `keep_rule_sql(...)` | Part B's map, G2 and the keep rule on their own (tests, CLI) |
| `ledger_postings(ledger, links, limit=500)` | the claim view's EMA postings on the server, newest first, no key columns |
| `LedgerHandle(state, tmp_dir=None)` | serverless: `open()`, `save(conn=None)`, `discard()`, `behind(conn)`, `state_rev()`, `note_attempt(id)`, `attempts(id)`, `clear_attempt(id)` |
| `hold_slow_file(store, file_id)`, `MAX_ATTEMPTS`, `ATTEMPT_HOLD_REASON` | serverless: hold a file whose read was started 3 times without finishing |
| `reason_class(grp, code, kind)`, `scope_of(svc_lo, posted_lo)`, `check_key(check)` | shared rules (classes listed in section 9) |

`apply_production_file` result (what `ingest_file` writes to the catalog): `{'status': 'current'|'superseded'|'held'|
'waiting'|'already', 'reason', 'rows_read', 'rows_kept', 'rows_applied', 'live_rows', 'scope', 'box' (a Box), 'as_of',
'replaced': [(file_id, 'replaced'|'partly_replaced')], 'account' (counts and dates, plus links, payers, claim_lines,
charge_line_share), 'links', 'holds': [{'code', 'message'}], 'received_at', 'seq'}`. Refusals raise
`ema_reports.ReportRefused` (`ingest_file` records them). Past `deadline` it returns `waiting` and the ledger is unchanged.

`refresh` result: `{'part_a': {...counts, seconds} | None, 'part_b': {'traces', 'checks', 'map', 'keep', 'coverage',
'seconds'} | None, 'retained', 'catalog', 'published': {'lines', 'payments', 'reasons', 'checks'}, 'seconds'}`.
`parts='auto'`: Part A when a ledger is given and its seq (or, serverless, its state revision) differs from store meta
`ema:ledger_seq` / `ema:ledger_rev`, or retention deleted rows, or meta `ema:keep_dirty` is set; Part B after Part A or
when meta `ema:map_dirty` is set. With a ledger it first runs retention and `sync_catalog`.

Measured on the real files (server, this machine): both Production files apply in 8.3 s and 6.5 s; ledger 139.5 MB
(36.9 MB gzipped at level 1); refresh 20.3 s (Part A 10.2 s, Part B 9.0 s) at 142 MB RSS. Serverless-like (compact
in-memory store, zipped CSVs, one file per run): 22.8 s and 30.8 s per run; /tmp peak 346 MB (ledger 140 MB + refresh work
database 206 MB; the 38-48 MB staging file and the 63 MB unpack are earlier in the run); RSS rose from 582 MB after the load
to a 1,325 MB peak, most of it SQLite doubling the in-memory store's buffer when the store grows (any store growth on that
host does this).

## 1. `remitwatch/pipeline.py` (`run_cycle`, the server)

1. **One ledger per cycle, opened only when needed.** After the lock:
   ```python
   from . import ema_ledger
   lg_box = []
   def ledger():                      # a zero-argument opener: ingest_file opens it only for a Production file
       if not lg_box:
           lg_box.append(ema_ledger.Ledger.open(ema_ledger.ledger_path(db_path)))
       return lg_box[0]
   ```
   Close it in the `finally` (`for lg in lg_box: lg.close()`).
2. **Pass `ledger=ledger`** to `ingest_new_files(store, new_files, log=log, ledger=ledger)` (needs_ema_reports item 4.4
   adds the parameter), to `uploads.ingest_queued(store, log=beat_log, ledger=ledger)`, to the TriZetto `_ingest_batch`
   route if a report can come that way, and to the re-read of waiting entries (needs_ema_reports item 7.3:
   `ema_reports.ingest_file(store, w["origin"]["path"], via=..., origin=w["origin"], ledger=ledger, log=beat_log)`).
   All Production files of a cycle are applied, in any order (the box invariant makes the order irrelevant).
3. **Map dirty after 835s.** When the cycle stored new remittances (`ingested["remits"] > 0`, TriZetto included), set
   `INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:map_dirty', '1')` and commit, so detection re-maps (Part B).
4. **Refresh before detection** (the store is still open, before `store.close()`):
   ```python
   if lg_box or os.path.exists(ema_ledger.ledger_path(db_path)):
       r = ema_ledger.refresh(store, ledger(), log=beat_log)
       if r.get("part_a") or r.get("part_b"):
           changed = True             # new EMA summaries: detection runs
   ```
   (`'auto'` makes this cheap when nothing changed: retention, a catalog check and a seq compare.) Wrap it like the other
   steps: an exception is noted in `out["ema_refresh"]` and the cycle goes on.
5. **X10, lines of new findings.** After `run_detection`, when meta `ema:keep_dirty` is set (detect item 3.3), reopen the
   store and run `ema_ledger.refresh(store, ledger(), log=beat_log)` once more (Part A and B), so the lines of bills that
   just got a finding are kept. Optional; without it they are kept at the next Production apply.
6. **Kept copies.** After the ingest, for `fid in ledger().copies_to_drop()` delete only the Upload page's kept copy
   (`uploads/exports/<sha16>/`) of that file. Never the Dropbox mirror. The catalog row stays.

## 2. `remitwatch/cloud.py` (`run_cloud_cycle`, Vercel)

Production on Vercel is read only from files of 60 MB or less (X2) and only when `ema_reports.reads("production")`.

1. **Crash repair, early in each run** (after `ensure_schema`, before ingest), only when the state folder holds a ledger:
   ```python
   h = ema_ledger.LedgerHandle(state)
   if h.behind(conn):                 # the ledger was saved and the store was not (a run died between the two saves)
       lg = h.open()
       seq0 = lg.seq()
       ema_ledger.refresh(store, lg, log=log)      # Part A runs (revision differs), sync_catalog restores the catalog
       if lg.seq() != seq0:
           h.save(conn)               # retention or a salt reset changed it
       else:
           h.discard()                # refresh already wrote meta ema:ledger_rev = h.rev
       conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('cloud:detect_pending', '1')")
   ```
2. **An apply run takes at most one Production file.** For a Production file in the batch (a zip member or a CSV of 60 MB
   or less), or a waiting Production entry whose origin size is 60 MB or less:
   - `fid` = its SHA-256 (a waiting entry: `w["file_id"]`); if `h.attempts(fid) >= ema_ledger.MAX_ATTEMPTS`:
     `ema_ledger.hold_slow_file(store, fid)` and skip it.
   - `h.note_attempt(fid)` **before** any read starts (it writes `ema_attempts.json` to the state folder, so a run the
     platform kills still counts).
   - `lg = h.open()`; `res = ema_reports.ingest_file(store, path, via="dropbox", origin=origin, ledger=lg,
     deadline=t0 + budget - 75, log=log)`.
   - `res["status"]` (or a zip member's) `waiting` (the time limit): `h.discard()`; keep the attempt; leave the file for
     the next run. Any exception: `h.discard()` and re-raise or record.
   - otherwise: `ema_ledger.refresh(store, lg, log=log)`, then **save in this order**: `h.save(conn)` (gzip level 1 to
     `ema_ledger.sqlite.gz`, writes meta `ema:ledger_rev`, deletes the scratch copy), `h.clear_attempt(fid)`, set
     `cloud:detect_pending`, `save_raw(state, store)`, the dashboard copy, then the Dropbox cursor; return `more=True`
     so detection runs in the next invocation.
   - The scratch ledger and staging file are gone before `build_serving` runs (save and discard delete them).
3. **Normal runs never open the ledger.** Detection's Part B runs from the store (detect item 3.1).
4. **Held decisions** (needs_ema_reports api item): when a person presses Apply on a file held by `hold_slow_file`, also
   call `LedgerHandle(state).clear_attempt(file_id)` so it gets three new tries.
5. `/tmp`: SQLite's own sort files also go to `/tmp` on Vercel (no setting needed); the measured peak above includes them.

## 3. `remitwatch/detect.py`

1. **`Detector.run()` step 2 (amendment 4.5):** after `build_links`, before `load`:
   ```python
   if self.conn.execute("SELECT 1 FROM meta WHERE key='ema:map_dirty'").fetchone():
       from . import ema_ledger
       ema_ledger.refresh(self.store, None, parts="B", log=lambda *_a: None)
   ```
   (store only; re-maps links with no bill, recomputes the in-835 marks and payer groups, prunes, clears the flag).
2. **`_rules_hash()`**: add `"ema_ledger.py"` (with `ema_reports.py` and `ema_checks.py`).
3. **`ema:keep_dirty` (X10).** In `save()`, compare the set of bills of open, started, sent or worked findings before and
   after (bill = `ema_reports.bill_of_claim(patient_control_number)`); when it changed, set meta `ema:keep_dirty = '1'`.
   Part A clears it. The rule for "open, started, sent or worked" is `ema_ledger._LIVE_FINDING`
   (`stale` 0 and: status open, in_progress, appealed, lost or dismissed, or notes, or appealed_at, or resolved by a
   person, `auto_note` NULL).

## 4. `remitwatch/cli.py`

1. **`ema-reports`** (new subcommand):
   - `--status`: `ema_reports.catalog(conn)` (report, source_name, status, as_of, rows), `coverage(conn)`, and
     `ema_ledger.Ledger.open(ema_ledger.ledger_path(db), readonly=True).stats()` when the file exists, plus meta
     `ema:map_coverage` (JSON: bills, mapped, share, by_method, links, conflicts, facility, patient_day).
   - `--summarize`: `ema_ledger.refresh(Store(db), Ledger.open(ledger_path(db)), parts="AB")`.
   - `--reread <file_id|all>`: one file: `lg.forget(fid)` then `ema_ledger.sync_catalog(store, lg)` (it becomes
     `reread`; the next cycle reads it again from its origin). `all`: move the ledger file aside (or delete it), then
     `sync_catalog(store, Ledger.open(path))` marks every current Production file `reread`.
   - `--decide <file_id> apply|discard`: `ema_reports.decide(conn, file_id, decision, "cli")`.
2. **`backup_store`** (`cmd_backup`): beside the store's copy, `Ledger.open(ledger_path(db)).backup(dest_dir/ema_ledger-<stamp>.sqlite)`
   (gzip it like the store if wanted); `keep` applies to both. Skip when the ledger file does not exist.
3. **`cmd_import_cloud`**: also bring the ledger: `h = ema_ledger.LedgerHandle(state, tmp_dir=<a folder on the server's
   disk>)`; `lg = h.open()`; `lg.backup(ema_ledger.ledger_path(db))`; `h.discard()`. The imported store carries its
   `ema:key_salt`, so the keys agree. When the state folder has no ledger, nothing to do.
4. **`cmd_compare_cloud`**: compare `ema_lines`, `ema_line_payments`, `ema_line_reasons`, `ema_checks`, `ema_months`,
   `ema_link_map`, `ema_link_events` by count and money sums; leave out `person`, `name_key`, `mrn_key`, `note_hash`,
   `last_note_hash` (`ema_reports.KEY_COLUMNS`) and `mapped_at`.

## 5. `remitwatch/api.py`

1. **Claim view (`/api/claims/view`, `GET /api/findings/{id}` `ema` block), server only:** links of the bill
   `SELECT link FROM ema_link_map WHERE bill = ? AND conflict = 0` (plus `ema_claim_links.link` for its claim ids), then
   `ema_ledger.ledger_postings(Ledger.open(ledger_path(DB_PATH), readonly=True), links, limit=500)`; close the ledger
   after. On Vercel (`runtime.serverless()`) show "EMA's postings are kept on the server" instead. No key column is
   returned.
2. **Data health** (builder 3 owns the items): `ema_map` reads meta `ema:map_coverage.share` (not ok under 0.90);
   `ema_link_conflicts` = `conflicts / links` (not ok over 0.02).

## 6. Remittance ingest (`dropbox_sync.ingest_new_files`, `cloud._ingest_batch`, TriZetto, uploads)

When new 835 transactions are stored, set meta `ema:map_dirty = '1'` (in the same commit). `apply_ar` already sets it.

## 7. `remitwatch/ema_reports.py` (builder 1's module)

1. `_ingest_production`: pass `received_at=res.get("received_at")` into the catalog fields when the row is new, so the
   catalog's arrival time equals the ledger's (the ledger orders files by `(as_of, created_at, received_at, file_id)`).
2. `_ingest_one` treats `reread` like `waiting` (already so): a file `sync_catalog` marked `reread` is read again.
3. Nothing else: `ingest_file` already calls `apply_production_file` lazily and `preview` uses `ema_ledger.scope_of`.

## 8. `.env.example` and docs

- `REMITWATCH_EMA_LEDGER=` (server: where the ledger lives; empty = beside the store).
- `REMITWATCH_EMA_REPORT_KEEP=2` (kept upload copies per box).
- SERVER_RUNBOOK: the ledger's location and size (about 140 MB for two years, WAL), backups, `ema-reports` commands.
- VERCEL_SETUP / ARCHITECTURE: the ledger in the state folder (`ema_ledger.sqlite.gz`, about 37 MB at two years),
  `ema_attempts.json`, one Production file per run, the /tmp figures above.

## 9. Notes for builder 3 (`ema_checks`) on what Part A and Part B write

- `ema_lines`: one row per (link, svc, cpt) that the keep rule kept; `keep` lists the rules met (`nonroutine`, `no835`,
  `unmapped`, `on_ar`, `finding`, `open_status`). `status` is the newest non-null status of the line's postings;
  `status_class` uses `rw_status_class(status, ins_answer_rows > 0)`. `balance` = charges - ins_paid - contractual -
  noncontractual - pat_paid_joined - pat_adj_joined. `pat_join` is `one`, `several` or NULL (patient-side money of that
  patient, day and code joins only when exactly one line has those keys; the line's own patient money is always in).
  `pat_after_denial`: patient payments posted after `denial_first_posted` and after the visit day. `nonroutine` and
  `denial_first_posted` count only a payer's non-routine CARC (group not PR, code not in `detect.IGNORE_CARCS`) with a
  non-zero adjustment. `allowed` is the largest net allowed of one payer. `bill`/`claim_id`/`map_method` come from the
  map (`map_method` is `conflict` for a conflict, and `bill` is NULL then); `facility` from the map. A line absent from
  `ema_lines` means "no EMA evidence here", never paid.
- `ema_line_payments`: every insurance payment row and $0 check row of the kept lines (`ins_paid` 0 for an answer);
  `role` is `primary`, `secondary` or `other` (the posting's payer against the row's primary and secondary payer);
  `in_835` = the check equals an 835 trace exactly or as integers; `trace_key` = that transaction's key. '' for a missing
  check or payer.
- `ema_line_reasons`: the payers' non-routine reasons and EMA's own kinds (group '', the kind as code, class `ema_kind`)
  on every kept line, and every reason (routine too) on lines kept by `finding`. `reason_class` values: `routine`,
  `patient` (group PR), `none`, `ema_kind`, `timely`, `bundling`, `level`, `coding`, `cob`, `information`, `auth`,
  `medical_necessity`, `frequency`, `noncovered`, `provider`, `duplicate`, `other` (from detect.py's CARC sets).
- `ema_checks`: all (check, EMA payer) pairs of insurance rows with a check (never product or patient-side rows), from
  the whole ledger (not pruned). `match` = `exact`, `loose` (letters and digits only with leading zeros dropped, or the
  last 10 characters within the payer group when both have 10 or more) or `none`; `trace_paid_cents` is the 835's claim
  paid total (CLP04 sum, E13). `cause` and `pair_trace` are yours: Part A keeps them for existing pairs.
- `ema_months`: measures `ins_paid`, `pat_paid`, `charges` (not products), `product`, `contractual_routine`,
  `contractual_denial` (detail = reason class), `noncontractual` (detail = class), `timely`, `denial_code` (detail =
  `<class>|<code>`, G16). By posted month and EMA payer. `no835` is yours; `patient_kind` is deferred (X5).
- `ema_link_map`: methods `ar`, `payment`, `patient_day`, and `facility` (a facility link with no bill; `facility` = 1).
  An `ar` link whose payment votes name another bill has `conflict` = 1 and keeps the A/R's bill. Use `conflict = 0 AND
  bill IS NOT NULL` for "mapped". `votes`/`total`: payment votes (or, for patient_day, shared keys and the bill's keys).
- `ema_link_events`: (link, 'status'|'action', value, first_seen = the Production as_of that first showed it).
- Meta `ema:map_coverage`: `{bills, mapped, share, by_method, links, conflicts, facility, patient_day: {bills, mapped,
  ties, charge_mismatch}, at}`.

## 10. Tests and other notes

- `tests/test_ema_ledger.py` (41 tests) covers the LedgerHandle with `LocalState` too (round trip, a run killed after the
  ledger save, attempts written before a read, the staging file removed). The cycle-level tests (`run_cloud_cycle`
  against `pipeline.run_cycle`, FakeDropbox) belong to the wiring phase's `tests/test_ema_vercel.py`.
- `test_efficiency`: `tests/test_ema_ledger.py::test_the_work_tables_lookups_use_their_indexes` already checks the
  refresh's per-line lookups are index searches (a work table without declared key types made the real refresh never
  finish). New detection statements over `ema_lines`/`ema_line_payments` should be checked the same way.
- `tests/test_ema_reports.py::test_a_payer_missing_from_a_reexport_holds_it` fails now and then on its own (builder 1's
  test): it writes "AR 2.xlsx" twice and expects the same bytes, but openpyxl stamps the save time into the workbook
  (docProps/core.xml, even when `created` is given), so when the two writes straddle a second the file id changes and the
  second read is held, not `already`. Write the workbook once and ingest the same file (or the same bytes) again.

## 11. Fix review (EMA fixer): what changed in `ema_ledger.py` and what the wiring must do

1. **Serverless save order (`cloud.py`).** Call `el.refresh(store, lg, release=h.save)`. After Part A, refresh detaches the
   ledger and calls `release()`, so the ledger's scratch copy is saved and can go before Part B's work file grows. The value
   `release()` returns is stored as the store's ledger revision (META_REV). The cycle must not call `h.save()` again for
   that run. Keep clearing the attempt marker only after the store image is saved. A run that dies between the two is
   repaired by the next one as before (the ledger revision is newer than the store's). Measured on a real 512 MB tmpfs:
   two years peak 303.8 MB of /tmp (was 391-395 MB); a third year 287.0 MB (was 527-536 MB, over the limit). Memory peak
   1,376 MB; refresh 21-24 s; a whole apply step 32-34 s. Out has `released` and `retain_held`.
2. **H8 (serverless only).** `holds()` can return H8, "Too large for this site's temporary space", when `tmp_need()` says
   the apply would not fit in /tmp less a 32 MB margin. Treat it as a hold: the file waits with that message on the Data
   page. It is not a slow or failed attempt and must not count toward the attempt limit. The server reads such a file.
3. **Retention.** `REMITWATCH_EMA_RETAIN_DAYS` (at least 366). Default 1100 days on the server and 730 on serverless,
   where /tmp is 512 MB. Add it to `.env.example` with that reason. `apply_production_file` now trims before it commits
   (counting from the file's newest posting, never from a date past today), so a commit never holds more than the window.
   `Ledger.retain()` refuses to delete more than 60% of the postings at once: it writes `ema:retain_held` to ledger_meta,
   refresh copies it to store meta `ema:retain_held` and to its result. Data health should show it, and the CLI needs a
   way to confirm it: `ledger.retain(force=True)` (for example `remitwatch ema-retain --force`).
4. **Work files.** Work files named `ema_refresh-*` older than an hour are removed when a ledger opens and when a refresh
   starts (a crashed run's). On serverless the work database sorts in memory (`temp_store=MEMORY`); on the server on disk.
5. **The 8.3 gate** should add /tmp to memory and time, with the figures in item 1. New ledger statements (the
   `ix_pd_key_b` index on `pd_key(bill)`, the routine-reasons pass of Part A) are covered by the plans in
   `tests/test_ema_ledger.py`; if `tests/test_efficiency.py` lists ledger plans, add these.
6. **Not done:** pruning `ema_link_map` and `ema_link_events` on serverless. Map rows feed coverage and a partial Part B,
   and events are history that only grows by insert. They are small next to the ledger.
