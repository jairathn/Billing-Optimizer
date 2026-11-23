# needs_ema_reports.md: what existing files must change to wire in `remitwatch/ema_reports.py`

Builder 1 (recognition, readers, keys, A/R, catalog, ingest, preview, decisions, migration). No existing file was edited.
Everything below is for the wiring phase. Each item names the file, the function, the behaviour, and the call into
`ema_reports` (imported as `from . import ema_reports` or `er`). The module has no import of `store`, `detect`, `cloud`,
`uploads` or `dropbox_sync`, so none of these imports can make a cycle. It imports `codes`, `runtime` and
`ema_exports` (for `NA_TEXT`, `TOTAL_LINE`, `export_created`).

## 0. What the module gives the wiring (quick reference)

| Name | Use |
|---|---|
| `ensure_schema(conn)` | every EMA store table and index (11 tables, `EMA_TABLES`); safe on every start; adds missing columns |
| `CANDIDATE_EXTS` | `.xlsx .csv .tsv .txt .zip`: endings the router asks `recognize` about first |
| `recognize(path_or_bytes, name)` | `Recognized` or None; raises `ReportRefused` (code `duplicate_column`) for a report with a column named twice |
| `is_plain_zip(path)` / `recognize_zip(path, name)` | a zip that is not a workbook / its report members; `ReportRefused('zip_in_zip')` |
| `detect_report(columns)` | `'production'`, `'ar_report'` or None, from header cells (for `ema_exports.detect_family`) |
| `ingest_file(store, path, rec=None, *, via, origin=None, force=False, ledger=None, deadline=None, log=print)` | the only way a report enters; result dict below |
| `record_waiting(conn, *, file_id, report, source_name, via, origin, reason, size=None)` | keep a report to read later |
| `waiting(conn, report=None)` | waiting entries, oldest first, `origin` parsed |
| `supersede_waiting(conn, file_id, *, reason, replaced_by=None)` | close a waiting entry that will never be read as it is |
| `provisional_id(content_hash)`, `BIG_CSV_REASON` | the catalog id and wording for a raw CSV too large for Vercel (X2) |
| `preview(conn, path, rec=None)` | what a file holds, for the Upload page; never opens the ledger; changes nothing but the key salt |
| `decide(conn, file_id, 'apply'|'discard', user)` | held files only; `{'requeue': origin}` on apply |
| `catalog(conn)` / `coverage(conn)` | `GET /api/ema/reports`; never contains an origin or the salt |
| `migrate_misread(store, *, serverless, log)` / `confirm_misread(store, sha256, rec)` | E4 clean-up |
| `serving_tables(conn)`, `SERVING_META_FILTER`, `KEY_COLUMNS` | the dashboard copy without keys, origins or salt |
| `claim_url_template(conn)` | "Open in EMA" template with `{claim}`, or None |
| `switch()`, `reads(report)` | `REMITWATCH_EMA_REPORTS` |
| `register_functions(conn, salt=None)` | `rw_name_key`, `rw_status_class` (1 or 2 args), `rw_bill`, `rw_payer_group`, `rw_payer_kind`, `rw_check_loose`, `rw_shape` |
| `ChunkStream(chunks)` | wraps `download_chunks` as a stream when a reader must read from Dropbox directly |

`ingest_file` result: `{'status': 'current'|'superseded'|'held'|'refused'|'waiting'|'already'|'not_report', 'report',
'file_id', 'reason', 'rows_read', 'rows_kept', 'rows_applied', 'live_rows', 'scope', 'box', 'as_of', 'replaced':
[(file_id, status)], 'account'}` (plus `'code'` on a refusal). A zip gives `{'status': 'zip', 'members': [result, ...],
'ignored': [member names]}`, or `{'status': 'refused', 'code': 'zip_in_zip', ...}`. It sets `store.ema_changed = True`
whenever the catalog changed. The `reason` and every log line hold no cell value (counts, column and payer names only).

`via` is a free label: use `'dropbox'`, `'mirror'`, `'upload'`, `'cli'`. `origin` (never served): server `{'path': ...}`,
upload `{'path': ..., 'upload_id': ...}`, Vercel `{'dropbox_id', 'path', 'content_hash', 'size'}`; a zip member adds
`'member'`; a misread re-queue adds `'misread': True`.

## 1. `remitwatch/ema_exports.py`

1. **`detect_family(columns)`**: first line of the body:
   `rep = ema_reports.detect_report(columns)`; `if rep: return rep` (returns `'production'` or `'ar_report'`). Import
   inside the function (`from .ema_reports import detect_report`) to avoid a load-order cycle (`ema_reports` imports
   `ema_exports` at module level).
2. **`read_export_detail(path)`** (and so `read_export` and `iter_rows`): before reading the file whole, refuse a report:
   `from .ema_reports import recognize, ReportRefused`; `try: rec = recognize(path, os.path.basename(path)) except
   ReportRefused: rec = True`; if `rec` is truthy raise `ValueError("This is EMA's A/R or Production report. RemitWatch
   reads it as a report (remitwatch.ema_reports), not as an export.")`. This must happen before `open(...).read()`:
   reading a 206 MB CSV whole takes about 1.3 GB. Also refuse after the header when `family in ema_reports.REPORTS`
   (belt and braces for a header the peek missed).
3. **`iter_export_paths(root)`**: keep `.xlsx` as today; add `.csv`, `.tsv` and `.zip` only when
   `ema_reports.recognize(p, n)` is a report (a `.zip` when `ema_reports.is_plain_zip(p) and ema_reports.recognize_zip(p, n)`).
   Today's only caller is `cli.cmd_ingest_ema` (item 9).

## 2. `remitwatch/store.py`

- **`Store.__init__`**: after `self.conn = connect(...)`, call `from .ema_reports import ensure_schema as _ema_schema;
  _ema_schema(self.conn)`.
- **`Store.from_bytes`**: after `store.conn.executescript(ddl(True))`, call the same (tables added since the image was
  written). The EMA tables keep their indexes in a compact store (they are small; Part B and the checks need them).

## 3. `remitwatch/detect.py`

- **`ensure_schema(conn)`**: call `ema_reports.ensure_schema(conn)` (this also covers `cloud.empty_serving`, which builds
  the dashboard copy's empty tables through `detect.ensure_schema`).
- **`_rules_hash()`**: add `"ema_reports.py"` (and the two other EMA modules, per spec 10) to the hashed files.
- New finding columns are builder 3's / the wiring's (`detect.EXTRA_FINDING_COLS`): spec 5.5 plus `written_off_when`,
  `ema_outcome`, `ema_outcome_on`. `serving_tables(conn)` already uses `findings.ema_link` when that column exists.

## 4. `remitwatch/dropbox_sync.py`

1. **`WANTED_EXT`**: add `".csv", ".tsv", ".zip"` (`.txt` is already there).
2. **`wanted(entry)`**: a `.zip` is wanted, except the backfill zip: `name == os.environ.get("REMITWATCH_BACKFILL_ZIP",
   "Trizetto.zip").lower()` stays unwanted (cloud reads it through `ingest_zip`).
3. **`DropboxClient.download_chunks(path_or_id, chunk=4 << 20, timeout=(10, 30))`**: pass `timeout=timeout` to the
   request instead of `300` (E12: every Dropbox read for reports has a 30 s read timeout).
4. **`ingest_new_files(store, paths, log=print, origins=None, ledger=None)`** (the router). For each path `p` (name `n`):
   - if `n.lower().endswith(ema_reports.CANDIDATE_EXTS)`:
     - `.zip`: if `ema_reports.is_plain_zip(p)`: `res = ema_reports.ingest_file(store, p, via=via, origin=(origins or
       {}).get(p), ledger=ledger, log=log)`; otherwise (a workbook named .zip) go on below. A zip with no report member
       gives `{'status': 'zip', 'members': []}`: count it as skipped.
     - else `try: rec = ema_reports.recognize(p, n)` `except ema_reports.ReportRefused: rec = "refused"`. When `rec`:
       `res = ema_reports.ingest_file(store, p, None if rec == "refused" else rec, via=via, origin=(origins or {}).get(p),
       ledger=ledger, log=log)`. Count `out["ema"] += 1` when `res["status"] in ("current", "superseded")` (or a zip with
       such a member) so detection runs; collect `out.setdefault("ema_reports", []).append({k: res[k] for k in
       ("status", "report", "rows_applied")})`.
     - On any exception from a report file, log only `type(exc).__name__` (E18: a converter's message can hold a cell),
       e.g. `log(f"  EMA report ERROR {n}: {type(exc).__name__}")`, and add `n` to `out["failed"]`.
   - the three weekly exports as today, except:
     - a file over 60 MB is refused before it is read: `out["failed"].append(f"{n} (too large for this report)")`,
       `store.record_file(sha, p, kind="ema:too_large", status="not_read")` (it is not read whole);
     - an `unknown` family is recorded with no rows: `store.record_file(sha, p, kind="ema:unknown", status="not_read",
       lines=0)` and `store.ingest_ema_rows` is not called (today unknown layouts insert rows of family `unknown`).
   - `via`: `'mirror'` on the server's Dropbox mirror, `'dropbox'` from `cloud._ingest_batch`, `'upload'` from
     `uploads.ingest_queued` (add a `via` parameter, default `'mirror'`).
5. **`sync(...)`**: no change needed; `.csv` and `.zip` are now mirrored and `download` already streams to disk.

## 5. `remitwatch/cloud.py`

1. **`_ingest_batch(store, items, log)`**: an item may have a 4th element `origin` (dict). Build `origins = {local_path:
   origin}` while writing the batch files and call `ingest_new_files(store, paths, log=log, origins=origins, via="dropbox")`.
   In `sync_and_ingest`, pass `{"dropbox_id": e.get("id"), "path": e.get("path_display"), "content_hash":
   e.get("content_hash"), "size": e.get("size")}` as that 4th element.
2. **The X2 path in `sync_and_ingest`** (replaces "(too large)" for reports): for an entry over `MAX_FILE_BYTES`
   whose name ends `.csv`, `.tsv` or `.txt`: read one chunk, `head = next(iter(client.download_chunks(eid, chunk=65536,
   timeout=(10, 30))), b"")`, then close (stop iterating). `rec = ema_reports.recognize(head, name)`. When
   `rec and rec.report == "production"`: `ema_reports.record_waiting(conn, file_id=ema_reports.provisional_id(h),
   report="production", source_name=name, via="dropbox", origin={...as above...}, reason=ema_reports.BIG_CSV_REASON,
   size=e.get("size"))`; mark it in `dropbox_seen`; do not count it as an error. A large file that is not a report keeps
   today's "(too large)". The provisional entry is closed by `ingest_file` when a zipped member with the same name and
   uncompressed size is read (already implemented, `_supersede_raw_twins`).
3. **Re-reading waiting entries by Dropbox id** (each run, after `sync_and_ingest`, inside the ingest deadline): for
   `w in ema_reports.waiting(conn)` with `ema_reports.reads(w["report"])`, an origin with `dropbox_id`, and
   `(w["origin"].get("size") or 0) <= MAX_FILE_BYTES` (Production on Vercel: one file per run and only with the ledger
   handle, builder 2's needs): `data, meta = client.download_bytes(w["origin"]["dropbox_id"])`; `sha =
   hashlib.sha256(data).hexdigest()`:
   - if `meta["content_hash"]` differs from the origin's and `w["file_id"]` is not provisional and `sha != w["file_id"]`:
     `ema_reports.supersede_waiting(conn, w["file_id"], reason="The file changed in Dropbox before it was read again; the
     newer copy is read instead.")`, then ingest the new bytes as below;
   - pass `(w["source_name"], data, None, w["origin"])` to `_ingest_batch`. `ingest_file` removes rows the old code read
     from the same bytes (`confirm_misread`) before reading;
   - if the fetched file is not a report (`ema_reports.recognize` gives None), call `ema_reports.confirm_misread(store,
     sha, None)`: it drops a waiting entry `migrate_misread` queued for a genuine export, and changes nothing else;
   - `DropboxNotFound`: `ema_reports.supersede_waiting(conn, w["file_id"], reason="The file is no longer in Dropbox.")`.
4. **`migrate_misread`**: once per host, early in `run_cloud_cycle` after `ensure_schema(conn)`:
   `ema_reports.migrate_misread(store, serverless=True, log=log)` (the meta flag `ema:migrated_v1` makes later calls
   no-ops). It queues candidates from `dropbox_seen` and deletes nothing until item 3 confirms the fetched file.
5. **Dashboard copy**: in `build_serving`, iterate `SERVING_TABLES + ema_reports.serving_tables(conn)` and use
   `ema_reports.SERVING_META_FILTER` as the `meta` filter (it adds `key NOT LIKE 'ema:key_salt%'` to today's filter).
   `serving_tables` leaves out `origin` and every key column (`person`, `name_key`, `mrn_key`, `note_hash`,
   `last_note_hash`) from every EMA table, and copies only lines, payments, reasons and events of links on the A/R or
   behind findings (E18, spec 5.6). Bump `SERVING_BUILD` to `"7"`.
6. **`meta_summary`** (optional): add `"ema_reports": ema_reports.coverage(conn)` so the header shows "EMA as of".

## 6. `remitwatch/uploads.py`

1. **`sniff(path, name)`**: unchanged for `.xlsx`/`.csv`; a `.zip` for which `ema_reports.is_plain_zip(path) and
   ema_reports.recognize_zip(path, name)` returns members is kind `"ema_report_zip"` (not `"zip"` of remittances).
   `_export_text(head)`: also `True` when `ema_reports.recognize(head, "x.csv")` is a report.
2. **`accept(...)`**: right after the kind is `"ema"` (or `"ema_report_zip"`), and before the 60 MB export limit:
   `try: rec = ema_reports.recognize(work, name) except ema_reports.ReportRefused as exc: raise Rejected(exc.message)`.
   When `rec` (or the zip has report members): the size limit is `MAX_BYTES` (240 MB), not `EXPORT_MAX_BYTES`;
   `summary = ema_reports.preview(conn, work, rec)`; a `summary["refused"]` raises `Rejected(summary["refused"])`; keep the
   file under `uploads/exports/<sha16>/` as today and queue it. The current rejection in `preview_export` ("This report's
   rows name no claim or bill ... an AR or production report looks like this") is no longer reached for reports.
3. **`describe(s, status, ...)`** for `s["kind"] == "ema_report"`: lines from the preview: report name, rows, service
   dates, scope (years for the A/R, `service`/`posted` for Production), as_of, what it replaces (`replaces`/`overlaps`),
   holds (`holds[*].message`), the A/R `aging_window` ("lists only lines with activity in the last 30 days"), and always
   "Names, dates of birth, record numbers, billing notes and staff names are never kept."
4. **`_already(conn, sha)`**: also read `ema_report_files` by `file_id = sha`: `waiting` -> "kept; read once this host
   reads this report" (its `status_reason`), `held` -> "held: <status_reason>; Apply or Discard it on the Data tab",
   `refused` -> its `status_reason`, `current`/`superseded`/`replaced` -> "read on <applied_at>".
5. **`_finish(conn, up, errors)`** for a report upload: read the catalog row (`file_id = up["stored_sha"] or
   up["upload_id"]`) instead of `files`: `added = rows_applied`, `file_status = status`, `reason = status_reason`. A
   waiting, held or refused report has no `files` row and is not a failure.
6. **`ingest_queued(store, ...)`**: pass `via="upload"` and `origins={path: {"path": path, "upload_id": upload_id}}` to the
   router, and the pipeline's ledger (builder 2).
7. Held decisions (with api item 9): on `apply` with `requeue.upload_id`, set that uploads row back to `queued` and wake
   the cycle.

## 7. `remitwatch/pipeline.py` (`run_cycle`)

1. After `store = Store(db_path)` and the lock: `ema_reports.migrate_misread(store, serverless=False, log=beat_log)`
   (once per host by meta `ema:migrated_v1`; afterwards a no-op). It purges a confirmed misread report's rows and
   re-queues it (an upload goes back to `queued` in `uploads`, a mirror file becomes a waiting entry with `origin.path`).
2. Pass `ledger=` (builder 2's opener) to `ingest_new_files` and `uploads.ingest_queued`.
3. After the uploads: re-read waiting entries: for `w in ema_reports.waiting(store.conn)` with
   `ema_reports.reads(w["report"])`, `w["origin"].get("path")` on disk and no `upload_id` in the origin (uploads are
   re-queued in the `uploads` table instead): `ema_reports.ingest_file(store, w["origin"]["path"], via=w["via"] or
   "mirror", origin=w["origin"], ledger=<ledger>, log=beat_log)`. A path that is gone:
   `ema_reports.supersede_waiting(store.conn, w["file_id"], reason="The file is no longer in the folder.")`.
4. Count applied reports into `ingested["ema"]` (or `store.ema_changed`) so `changed` triggers detection.

## 8. `remitwatch/codes.py` (Step 2, E9; re-analyses the archive once)

Move the VA rule to the top of `PAYER_RULES` (above `OPTUM`) and add `VACAA`:
`(r"VETERAN|^VA\b|TRIWEST|OPTUM VA|CCN|VACAA", "VA Community Care", "va")`. `ema_reports.payer_group_of` already applies
this pattern before `normalize_payer`, so EMA results are right before this ships.

## 9. `remitwatch/api.py`

- `GET /api/ema/reports` (both modes, login): `ema_reports.catalog(conn)` (no origin, no salt).
- `POST /api/ema/reports/{file_id}/decision` (login + `require_xhr`), body `{"decision": "apply"|"discard"}`:
  `ema_reports.decide(conn, file_id, decision, user)`. Server: on apply re-queue (`requeue.upload_id` -> uploads row
  `queued`; `requeue.path` -> nothing more, the cycle re-reads waiting/held entries) and wake the cycle. Vercel: write
  the journal action `ema_report_decision` (actions.apply) and let the next run call `decide` and fetch by
  `requeue.dropbox_id`. A held file is re-read by `ingest_file`, which applies it (force) once `decision` is `apply`.
- `GET /api/meta` adds `ema`: `cov = ema_reports.coverage(conn)`; `{"ar_as_of": max(v["as_of"] for v in
  cov["ar"].values()) or None, "production_through": cov["production_as_of"], "held": len(catalog["held"]),
  "claim_url": ema_reports.claim_url_template(conn)}`.

## 10. `remitwatch/cli.py`

- `cmd_ingest_ema`: route a path to `ema_reports.ingest_file(store, p, via="cli")` when `ema_reports.recognize(p, n)`
  (or `is_plain_zip`) says it is a report, before `iter_rows` (which will refuse reports, item 1.2).
- `ema-reports --status`: print `catalog(conn)["files"]` (report, source_name, status, as_of, rows) and `coverage`.

## 11. `.env.example` and docs

- `REMITWATCH_EMA_REPORTS=` (`all` on a server, `ar` on Vercel by default; `off` stops reading both reports).
- `REMITWATCH_EMA_CLAIM_URL=` (a template with `{claim}`, or `off`; empty: learned from the reports).
- `REMITWATCH_EMA_PAYER_ALIASES=` (JSON, empty in the repo; set only in the host's environment).

## 12. Meta keys this module writes (for data health and the wiring)

`ema:key_salt` (never served), `ema:claim_url_prefix`, `ema:payer_aliases_learned`, `ema:unknown_status_words`,
`ema:migrated_v1`, `ema:misread_suspects` (new: the Data tab lists them as "may be a misread report"; entries hold
sha256, family, rows, why; no names), and `ema:map_dirty = '1'` after an A/R snapshot becomes current (the 4.5 step 2 Part
B trigger "after an A/R arrived").

## 13. Notes for the other two builders

- **Ledger (builder 2)**: `iter_production(src, rec, *, salt, account, deadline)` yields `Posting` tuples in the ledger's
  column order (no `unit_allowable`); `deadline` is a `time.time()` value checked every 10,000 rows (`ReadTimeout`).
  Refusals raised while reading: `missing_columns`, `two_hosts`, `two_date_orders`, `too_many_rows`, `unreadable`
  (never carrying a cell). `account` gets counts and dates only (keys in the amendment plus `bad_links`, `format`); pass
  an `ema_reports.Account()` to also receive `.prefixes` and `.status_words` as attributes (never put them in the
  stored account). `ingest_file` writes the catalog, `files` and `ema_sources` rows from `apply_production_file`'s result
  and the `replaced` statuses; it learns the claim prefix itself. It calls `apply_production_file` only when `ledger` is
  given (a `Ledger` or a zero-argument callable). `preview` uses `ema_ledger.scope_of` when it can be imported.
  New `ema_lines` columns (`facility`, `first_charge_posted`, `first_ins_posting`, `ins_answer_rows`) and
  `ema_link_map.facility` are appended at the end of their tables; use column lists in every INSERT.
- **Checks (builder 3)**: `ema_ar.exposure_cents` is Current A/R, else Denial Amount (never the sum); `ema_ar.payer_group`
  is the group of the payer the line is owed by (the secondary payer for secondary coverage, else the primary).
  `ema_ar_items.worked_how` is a comma list of `note`, `follow_up`, `action`, `resubmitted`; `absent_since` means only
  "not in the newest snapshot". Item keys: `I|claim id|cpt|svc|coverage|n` for insurance lines with a claim id;
  `U|<keyed hash>|n` (insurance, no claim: unbilled) and `P|<keyed hash>|n` (patient lines) use a 16-hex keyed digest
  instead of the raw `mrn_key`/`name_key`. `status_class(status, has_answer)`: Resolved/Other is `gave_up` only when
  `has_answer` is False. `ema_ar.status_class` is filled without answer information.

## 14. Fix review (EMA fixer): what changed in `ema_reports.py` and what the wiring must do

1. **Dashboard copy (`cloud.py`).** Build the EMA part of the copy from `er.serving_tables(conn)`: it now takes the store
   connection, because the rows it keeps depend on the store. It keeps the A/R rows, every claim number of a bill that has
   an item (any submission of that bill, not only the map's claim id), the claim numbers items name, and their lines,
   payments, reasons, map rows and events. Checks are kept when unmatched or paid on a kept line. The filters are plain SQL
   with no RemitWatch function, so the copy builds on any connection. The meta entry must use `er.SERVING_META_FILTER`
   (it leaves out `ema:key_salt`). `cloud.empty_serving` (and any other place that makes an empty copy) must run
   `er.ensure_schema` on it so the pages work before the first report. Bump `SERVING_BUILD`: the copy's shape changed
   (`ema_ar.bucket` column, fewer EMA rows). Measured on the real store: copy 151.7 MB (29.3 MB gzipped), EMA tables 31.9 MB
   of it (128.8 MB in the store), built in 2 s. Every page reads the same on the copy as on the store.
2. **Salt.** `register_functions(conn, salt=None, *, create=True)`. Pages and anything that reads the dashboard copy pass
   `create=False` (ema_checks does this itself): a copy never makes a salt, and `rw_name_key` gives NULL without one.
   `stored_salt(conn)` reads the store's salt or None.
3. **`ema_sources`.** A report's row is now named `report:<file name>` (`er.source_key`), so a report never replaces the
   row of an export with the same file name, or the other way round. Code that reads `ema_sources` for exports should
   keep to the export families (or skip names starting with `report:`).
4. **Dates.** A date before 2000-01-01, a posting date more than 1 day ahead of today, or a service date more than 31 days
   ahead is read as missing and counted in `out_of_range_dates`. The A/R as-of day is never later than today, and A/R
   retention counts from the earlier of the as-of day and today. A 5-digit text cell is no longer an Excel serial date
   (only a typed number is); spec 4.3 said text serials were accepted. Docs should say so.
5. **New holds.** A/R: `er.format_holds(account)` holds a re-export whose rows lost their dates, amounts or shape compared
   with the file on file. Production: `er.production_format_holds(account)` adds H1 for rows that do not line up with the
   header and for a file that ends mid-row (an interrupted copy), and **H7** when damaged check numbers (scientific
   notation) pass max(10, 0.5% of the rows with a check). The wiring shows the hold message as given; data health lists it.
6. **Rows left out.** Rows whose cell count differs from the header are left out and counted (`left['misaligned']`).
7. **Crash repair.** When a file is `already` in the ledger but its store rows are missing (a run that died between the
   ledger commit and the store write), `ingest_file` writes the store rows again. Nothing to wire.
8. **Index dropped.** `ensure_schema` drops `ix_elp_check` (no query used it; about 8 MB on the real store).
9. **Tests elsewhere.** The misread-migration tests in `tests/test_ema_reports.py` make the old rows by switching off
   `ema_exports._refuse_report` and `ema_exports.detect_family` for one call (the export reader now refuses reports). If
   those two names change in `ema_exports.py`, change the helper `_old_rows` in that test file the same way.
10. **Known limit.** On the dashboard copy, the claim view cannot show an export's as-of day, because `ema_sources` is not
    served. Serve it (it holds no patient data) or leave the day out on the copy.
