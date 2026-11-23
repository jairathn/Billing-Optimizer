# needs_A1.md: what unit A1 (intake-core) needs from other units

Final version. Every item names the file, the function and the exact change. A1 does not edit any of these files (one
exception, stated in section 6). No identifiers.

## 1. Unit B (detect.py, actions.py): the ledger refresh, `ema:map_dirty` and `ema:keep_dirty`

The contract the wiring implements (A1's side is done in pipeline.py and cloud.py):

- Server (`pipeline.run_cycle`): one ledger per cycle (`ema_ledger.Ledger.open(ema_ledger.ledger_path(db))`, opened lazily,
  only when a Production file comes or a ledger file exists). Every Production file of the cycle is applied (intake
  uploads, the Dropbox mirror, the reports waiting to be read again). Before detection, when a ledger file exists, the
  pipeline runs `ema_ledger.refresh(store, ledger)`: Part A when the ledger changed (seq), Part B after Part A or when meta
  `ema:map_dirty` is set. Part B clears `ema:map_dirty`. After detection, when meta `ema:keep_dirty` is set, the pipeline
  runs `refresh` once more (X10).
- Serverless (`cloud.run_cloud_cycle`): only a run that applies a Production file opens the ledger (`cloud.ProductionSlot`);
  it runs `ema_ledger.refresh(store, lg, release=handle.save)` and leaves detection to the next invocation (`more=True`,
  `cloud:detect_pending`). Normal runs never open the ledger.
- Who sets `ema:map_dirty`: `ema_reports.apply_ar` (an A/R became current) and the router `dropbox_sync.ingest_new_files`
  after new 835 transactions (`dropbox_sync.mark_map_dirty`, only when the store holds an applied EMA report). TriZetto goes
  through the same router (`cloud._ingest_batch`), so it is covered.
- What detection must do (B, `Detector.run()` step 2 of amendment 4.5, after `build_links`, before `load`):
  ```python
  if self.conn.execute("SELECT 1 FROM meta WHERE key='ema:map_dirty'").fetchone():
      from . import ema_ledger
      ema_ledger.refresh(self.store, None, parts="B", log=lambda *_a: None)
  ```
  This covers Vercel's normal runs (after 835s or an A/R), a server whose refresh failed (the cycle notes it in
  `out["ema_refresh"]` and goes on), and stores with no ledger yet. Detection never runs Part A (no ledger there).
- `ema:keep_dirty` (X10): set by detection's `save()` when the bills of live findings changed (needs_ema_ledger 3.3).
- Keep `detect.ensure_schema`'s last line (A1's): `from . import ema_reports; ema_reports.ensure_schema(conn)`. It also
  makes the dashboard copy's empty EMA tables (`cloud.empty_serving`).
- Held decisions on Vercel (B owns actions.py): the journal action `ema_report_decision` must call
  `ema_reports.decide(conn, file_id, decision, user)` in `actions.apply`. The next run's `cloud.reread_waiting` then reads
  the held file again (by its Dropbox id, or from `intake/done/<pair>` for an upload) and applies it. For a file that
  `ema_ledger.hold_slow_file` held (three runs that did not finish), Apply must also clear its attempts:
  `ema_ledger.LedgerHandle(state).clear_attempt(file_id)` (the api route has the state folder; actions.apply does not).
- Note, not a request: `DETECT_VERSION` hashes ema_reports.py and ema_ledger.py among the rule files, so any edit to them
  re-runs detection once on both hosts. During this push every unit's edits did that between runs; the acceptance runs
  of A1 used a frozen copy of the tree for that reason.

## 2. Unit A2 (uploads.py, api_uploads.py, tests/fakes.py): the upload hand-off as A1 implemented it

- `remitwatch/intake.py`: `kick(data_dir)` (pokes the scheduler, or starts one background cycle when no scheduler runs and
  the store file exists; returns bool), `process_ready(store, data_dir=None, *, ledger=None, deadline=None, log=print)`
  (server, at the start of each cycle), `process_state(store, state, *, production=None, ledger=None, deadline=None,
  max_bytes=None, defer_close=False, log=print)` and `close_state_pairs(state, to_close)` (serverless).
- A pair is read only when its sidecar exists; names starting with "." are ignored; `incoming/sessions/`, `*.part` and
  `upload_sessions/` are never touched. The SHA-256 and size of the original bytes are checked again while reading (a .gz
  is unpacked as it is read); a mismatch is refused with "the file changed on the way".
- Catalog: `file_id` = the sidecar's sha256; `via="upload"`; origin = `{"upload_id", "pair", "user", "name", "size",
  "content_hash"}` plus `"path"` (server, the done copy) or `"state_path"` (serverless, `intake/done/<pair>`). The
  Dropbox content hash in the origin is what closes the same raw CSV kept waiting from Dropbox (and the reverse).
- Done sidecar: the original keys plus `"result": {"status": applied|already|held|refused|waiting|not_read|failed,
  "outcome": <the reader's own word: current, superseded, already, held, refused, waiting, read, not_read, too_large>,
  "lines": [plain sentences], "at", "file_id", "report", "rows_applied"}` (report keys only for a report).
- Order: server: the data file moves to `done/` before it is read (it is the kept copy the catalog points at), then the
  sidecar. Serverless: the pair moves to `intake/done/` only after the run saved its store image (a run that dies before
  leaves it in `intake/`; the next run reads it again and finds it 'already').
- No log line names an upload: the readers' lines about a pair say `upload <upload_id>` instead of the file's name.
- Held uploads: no requeue is needed. `pipeline.reread_waiting` and `cloud.reread_waiting` read a held report whose decision
  is 'apply' again from its kept copy (origin `path` or `state_path`). If the page also moves the pair back, the second
  read is 'already' and changes nothing.
- Kept copies: a Production file's kept copy goes once REMITWATCH_EMA_REPORT_KEEP newer files cover it whole, on both hosts
  now (server `pipeline.drop_kept_copies`, Vercel `cloud.ProductionSlot.drop_copies`, after the saves). The done sidecar
  and the catalog row stay. Other done pairs are kept (see open questions in report_A1.md).
- Done from A.4: `cloud.build_serving` serves `ema_reports.serving_tables(conn)`; `cloud.copy_howto` copies the state
  folder's `ema_howto.json` into meta `ema:howto` each run (the store is saved only when it changed) and `cli import-cloud`
  does the same (`_import_howto`); `.env.example` names `REMITWATCH_UPLOAD_MAX_GB=2`.
- Still asked of A2:
  - `uploads.ingest_queued(store, log=print, ledger=None)`: take the parameter and pass it on to
    `dropbox_sync.ingest_new_files(..., ledger=ledger, via="upload", origins={path: {"path": path, "upload_id": upload_id}})`.
    `pipeline.run_cycle` already passes it when the function takes it (checked with `inspect.signature`).
  - `tests/fakes.py` `FakeDropbox`: `download_chunks(self, path_or_id, chunk=4 << 20, timeout=None)`,
    `download_bytes(self, path_or_id, timeout=None)`, and `move(self, from_path, to_path)` that renames inside the fake
    root (DropboxConflict when the target exists, DropboxNotFound when the source is gone). A1's code works without them
    (it checks the signature; `DropboxState.move` falls back to get/put/delete).

## 3. Unit F (docs: README.md, docs/*, tests/test_docs.py)

`tests/test_docs.py` fails on these five until they are written (all other units' additions are listed in the same tests):
- README.md command list: `ema-reports` (`python -m remitwatch.cli ema-reports --status | --summarize | --reread <file
  id or all> | --decide <file id> apply|discard | --retain-force`; the mutating ones take the pipeline's lock).
- README.md layout: `remitwatch/intake.py` (the upload hand-off: pairs from the Upload page read at the start of each cycle
  on the server, before the Dropbox delta on Vercel).
- docs/SERVER_RUNBOOK.md and docs/VERCEL_SETUP.md: name `REMITWATCH_EMA_REPORTS` (all / ar / off; all on a server, ar on
  Vercel by default), `REMITWATCH_EMA_CLAIM_URL`, `REMITWATCH_EMA_PAYER_ALIASES` (host settings only, never the repository),
  `REMITWATCH_EMA_LEDGER` (server: where the posting ledger lives; beside the store when empty), `REMITWATCH_EMA_REPORT_KEEP`
  (2), `REMITWATCH_EMA_RETAIN_DAYS` (1100 on a server, 730 on Vercel; retention over 60% at once waits for
  `ema-reports --retain-force`). Wording to reuse is in `.env.example`.
- docs/OPERATIONS.md: on Vercel a raw Production CSV over 60 MB waits with "Too big to read on Vercel: zip or gzip it, or
  upload it in the app (the app compresses it)."; one Production file per run (a run that reads one leaves detection to
  the next); a file three runs could not finish is held with "Too slow to read on this site: zip the CSV ... or let the
  server read it."; the server's backup now also writes `ema_ledger-<stamp>.sqlite.gz`; `import-cloud` moves the posting
  ledger and the how-to boxes; `compare-cloud` compares the EMA tables by row counts and money sums, and the ledger.

## 4. Unit C (api.py, pages)

- The Data tab can list: `files` rows of kind `ema:unknown` / `ema:too_large` with status `not_read` (a spreadsheet that is
  none of the reports or exports, or a weekly export over 60 MB); catalog rows `waiting` with their reason (a raw
  Production CSV over 60 MB on Vercel; a second Production file in one run: "This site reads one Production file per run;
  this one is read in a following run, which starts by itself."); `held` rows with Apply / Discard (section 1 for the
  route).
- `GET /api/meta` can read `cache:meta`'s `ema_reports` key (`ema_reports.coverage`, computed by the run) instead of
  recomputing it on the dashboard copy.
- The Upload page's per-upload result: for reports, read the catalog row (A2's `report_outcome` does); the done sidecar's
  `result.status` uses A2's words (section 2).

## 5. Whoever owns the scheduler / runbook (E or F)

- Retention of `incoming/done/` (server) and `intake/done/` (Vercel) pairs other than covered Production copies is not
  automatic. A weekly A/R and the weekly pulls add a few MB a week. Decide a rule (for example: keep 180 days, never the
  newest copy of each report, never a held or waiting file's copy) and who runs it.

## 6. tests/test_feeds.py (no unit owns it): changed by A1

Two tests pinned the old "reports are not read" behaviour and failed by design once the router reads EMA's reports
(needs_ema_reports 4.4; A2 asked "whoever owns the router" to decide). A1 rewrote exactly those two, nothing else in the
file: `test_an_ar_and_a_production_report_in_the_folder_are_read_as_reports_and_never_as_exports` and
`test_an_ar_report_queued_by_an_older_upload_page_is_read_as_a_report`. The rest of test_feeds.py passes unchanged.
