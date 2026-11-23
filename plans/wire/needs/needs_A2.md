# Needs from unit A2 (uploads) for SWEEP (intake files, after A1), C (api.py and pages), the deploy files' owner and F (docs)

Updated 2026-11-23 by the A2 fixer (the security and end-to-end reviews). Section F, first, is the status of every
cross-unit item the two reviews raised and the requests still open; sections A to E are the hand-off contract written by
A2's build (still current; the details the fix changed are marked "fix"). No identifiers below.

## F. Fix round (2026-11-23): cross-unit items from the two reviews, and what is still open

### F.1 Status (checked in the tree on 2026-11-23, 01:15 UTC)

| item | review | owner | status |
|---|---|---|---|
| gzip bomb / giant line: a 1 MB upload stops every cycle and run | security #0 (high) | A2 + SWEEP | A2 part done (see report_A2.md): the finish refuses an original larger than ema_reports.unpack_limit(bytes sent) (200 to 1 above a 64 MiB floor) and a line longer than ema_reports.LINE_MAX in an export sent as text; a compressed original is capped at ema_reports.UNPACK_MAX (2 GiB) at the start. Intake side done by SWEEP: intake.hash_file reads through ema_reports.Capped, _state_pair unpacks 8 MiB at a time up to unpack_limit, every pair's attempt is noted before it is read (note_server_attempt / note_state_attempt); ema_reports._rows reads a line at most LINE_MAX + 2 long. |
| abandoned uploads swept only when someone opens the Upload page | security #5 | SWEEP | done by SWEEP: pipeline.sweep_uploads(db_path) each server cycle, cloud.run_cloud_cycle sweeps state_uploads(state) each run (out["uploads_swept"]). A2 also offers uploads.sweep_server(db_path) / uploads.sweep_state(state), which never raise ({"error": kind}); either is fine. |
| a Production report kept waiting is never read once the host reads Production | e2e #0 (high) | SWEEP + A2 | cloud.reread_waiting now tests the STORED size (_stored_size) against MAX_FILE_BYTES: done by SWEEP. A2 part done: "already have this" and the outcome line give the reason as it stands now (uploads.waiting_words: "read by the next run: this site reads the Production (collections) report now", or the current switch). While the site does not read it, the finish, the same file again and its checklist row say it is kept and not read (uploads.kept_unread), never that the next run reads it. |
| a run that is going when an upload finishes | e2e #3 | C1/SWEEP + A2 | api.cron_cycle already starts a "continue" run when intake.state_pending(st) lists pairs after a run: done. A2 part done: the finish says "A run is going now. When it ends, the next run starts and reads this file first." when lock.json is held. |
| weekly export read through intake says only "Read as an EMA export." | e2e #10 | SWEEP | done by SWEEP (_router_lines words each export with uploads.describe from the router's ema_files). |
| uploads_note_match reads its JSON body before the sign-in | security #1 | C (api.py) | OPEN: F.2. |
| Caddy lets 260 MiB through for every path under /api/uploads/ | security #1 | deploy files' owner | OPEN: F.3. |
| docs for the new limits | - | F | OPEN: F.4. |
| the checklist rows' meaning changed (only files read make a row fresh) | e2e #1, #2 | C2 | inform: F.5. |

### F.2 For C (remitwatch/api.py): read the note-match body after the sign-in

FastAPI reads and parses a Body(...) parameter before it runs the route's dependencies (the sign-in among them): today
an unauthenticated 64 MiB body to POST /api/uploads/notes/{id}/match is parsed before the 401 (measured +322 MB by the
security review). A2's routes now read their JSON after the sign-in, at most 64 KB (api_uploads._json_body). Exact change:

```python
@app.post("/api/uploads/notes/{note_id}/match", dependencies=[Depends(require_xhr)])
async def uploads_note_match(note_id: str, request: Request, _: str = Depends(require_auth)):
    from . import api_uploads
    body = await api_uploads._json_body(request)          # after the sign-in; at most 64 KB; 400 on bad JSON, 413 beyond
    return await asyncio.to_thread(_uploads_note_match, note_id, body)


def _uploads_note_match(note_id: str, body: dict):
    ...   # the current body of uploads_note_match, unchanged
```

Test (tests/test_api_review.py or test_uploads.py through C): with a password set and no login, a malformed JSON body
(`content=b"{not json"`, Content-Type application/json) gets 401, not 422.

### F.3 For the owner of deploy/Caddyfile: 260 MiB only where a one-piece upload goes

Today `@uploads path /api/uploads /api/uploads/*` lets 260 MiB through to every route under /api/uploads/ (start, finish,
status, the note routes). Replace the two `request_body` blocks with exactly this (tabs as in the file):

```
	# uploads by hand: the one-piece upload (a zip of remittance files, a note, an export) may be large, a piece of a large
	# file sent in pieces is at most 4 MiB, and nothing else needs more than 20 MB (the JSON routes read at most 64 KB).
	# MiB, not MB: Caddy's MB is 1,000,000 bytes, and 260MiB must stay above the app's own limit of 240 MiB per upload.
	@uploads {
		method POST
		path /api/uploads
	}
	request_body @uploads {
		max_size 260MiB
	}
	@pieces path /api/uploads/chunk
	request_body @pieces {
		max_size 5MiB
	}
	@other {
		not {
			method POST
			path /api/uploads
		}
		not path /api/uploads/chunk
	}
	request_body @other {
		max_size 20MB
	}
```

Run `caddy validate --config deploy/Caddyfile --adapter caddyfile` (no Caddy binary was available to A2). The test
tests/test_uploads.py::test_caddy_takes_large_uploads_only_on_the_upload_route (A2's file) already accepts both the
current form and exactly this one, and checks the piece limit (over 4 MiB + 64 KB, at most 20 MB).

### F.4 For F (docs: README.md, docs/OPERATIONS.md, docs/SERVER_RUNBOOK.md, docs/VERCEL_SETUP.md)

Besides section C below, say (plain words):
- A file sent compressed may be at most 2 GB before compression (what the readers unpack, ema_reports.UNPACK_MAX); one
  sent as it is, REMITWATCH_UPLOAD_MAX_GB (2 by default) as sent.
- The finish refuses a file that unpacks to far more than any export (about 200 times what was sent) or that has a line
  longer than 1 MB; such a file is never handed to a cycle.
- An upload may send no more than the size it gave at its start (plus a little for compression); a server keeps 512 MB of
  disk free for every piece (507 otherwise, and the upload carries on once there is room).
- On Vercel, a file larger than 60 MB as sent (cloud.MAX_FILE_BYTES) is refused before it is sent, with "upload it on the
  practice's server, or export a shorter date range".
- An upload cut off in one browser and carried on in another is sent afresh automatically (its pieces were compressed
  differently); the cut-off one is let go after two idle minutes, else removed after a day.
- The checklist: a row is up to date only by what RemitWatch has read; a report held or kept unread says so on its row;
  an upload no cycle has read in two days is "upload not read" and the row is overdue when its data is.
- On Vercel, where Production is not read by default (REMITWATCH_EMA_REPORTS 'ar'), an uploaded Production report is
  kept, and the page says so at once ("kept and not read yet: this site does not read the Production (collections)
  report yet ..."); it is read once the setting allows. The practice's server reads it now.

### F.5 For C2 (static/today.js and the Data tab): what GET /api/ema/exports rows now mean

- `age_days` is the age of the newest data RemitWatch READ of that export (as_of, else the newest read file's arrival);
  None when none was read, even if a file arrived and was held or kept waiting. Today's "never arrived" for
  `age_days == null` is then wrong for a held or unread file: show `state_line`, or test the new fields.
- New fields: `flag` (fresh, waiting, overdue, stuck, held, unread: what the Upload page's chip says), `stuck` (an upload
  no cycle has read in WAITING_FRESH_DAYS = 2 days), `held`, `unread` (the newest file's catalog status when it was not
  read), `read_at`, `reads_here` and `host_line` (this site does not read the report yet).
- `waiting` is now true only for an upload received within two days; an older one sets `stuck` and leaves `overdue` to
  the data's age.
- An upload of a report this site does not read (ema_reports.reads; Production on a serverless host by default) is never
  `waiting` or `stuck`: it is kept, its `last_line` says "kept and not read yet: this site does not read the ... yet",
  `flag` is `unread` (or `overdue` by the data read), and `host_line` is then None (the last line already says it; it is
  set only while no file of that report is kept unread).

### F.6 For the owner of remitwatch/api.py's packet checks (fax and notes): whole-second "since"

tests/test_uploads.py::test_notes_are_matched_by_patient_and_date_or_by_a_person failed now and then: its check before the
note is taken back called api._packet_problem(..., first=True), and the first-fax rule ("Another note for this visit was
uploaded since.") compares uploaded_notes.matched_at with the packet's created_at, both whole-second ISO times. The visit
note the test attaches right after the packet is built counts as "since" only when the two fall in different seconds
(proved: with 1.1 s between uploads the old check fails every time). A2 changed only its test (the check before the take-back
now uses first=False, the take-back rule it is about; after it, both first=False and first=True must say "taken back").
For the owner, no change asked; if "matched in the same second as the build" should count as "since", compare with >= or
keep fractions of a second in both times.

## A. For A1 (remitwatch/intake.py, pipeline.py, cloud.py, cli.py, dropbox_sync.py, .env.example)

### A.1 The hand-off, exactly as A2 writes it (PLAN.md "THE UPLOAD HAND-OFF", with the details A2 fixed)

- Server: `<data_dir>/incoming/ready/<upload_id>__<safe name>[.gz]` plus `<same name>.json`, both mode 0600, folder 0700.
  `data_dir` is `uploads.data_dir(db_path)` = the folder of REMITWATCH_DB (/data on AWS). Folder names are the constants
  `uploads.INCOMING` ("incoming"), `uploads.READY` ("ready"), `uploads.DONE` ("done").
- Serverless: state-folder names `intake/<upload_id>__<safe name>[.gz]` and `intake/<same name>.json`
  (`uploads.STATE_INTAKE` = "intake"; in Dropbox that is `<REMITWATCH_STATE_PATH>/intake/`; a LocalState folder has the
  same names under REMITWATCH_STATE_DIR).
- `upload_id` is 32 lowercase hex characters (`uploads.UPLOAD_ID`). `<safe name>` is `uploads.intake_safe_name(name)`.
- Sidecar: exactly the keys `uploads.SIDECAR_KEYS` = name, origin ("upload"), user, sha256, size, gz, received_at,
  report_hint (built by `uploads.make_sidecar`). `sha256` and `size` are those of the ORIGINAL bytes (before the page's
  gzip). `gz` true means the data file is one gzip member (the browser's CompressionStream output, unpack with
  `zlib.decompressobj(31)`). `report_hint` is the page's guess from the header row ("ar_report", "production",
  "charge_lines", "era_lines", "claim_status" or null): never trust it over `ema_reports.recognize`.
- A pair is complete when its sidecar exists. A2 writes the sidecar LAST (atomically: a hidden temporary file, then a
  rename). A data file without its sidecar is an upload still finishing or one that was cut off: never read, move or
  delete it; A2's sweep removes it after 24 hours.
- Ignore every name that starts with "." in ready/ and intake/ (A2's temporary files, `.<name>.<random>.tmp`).
- Never read or touch A2's working folders: server `<data_dir>/incoming/sessions/` and `<data_dir>/incoming/<id>.part`;
  serverless `upload_sessions/` in the state folder.
- Before A2 writes a pair it has already checked the whole file against `sha256` and `size` (server always; serverless
  unless the check passed its 200 s budget, then the page is told "the run checks it as it reads it"). Check again while
  reading anyway: it is cheap and it is the contract.

### A.2 What can arrive

- The A/R and Production reports, any size: the page always sends them in pieces. The one-piece route
  (`POST /api/uploads`, `uploads.accept`) also hands a recognised A/R or Production report to ready/ (gz false) instead of
  queueing it as an export (`uploads._report_hand_off`; its `uploads` row has kind "ema_report", status "ready", and
  `upload_id` = the file's SHA-256).
- Any other file over 20 MB on the server: the three weekly exports, 835/837 files, a .zip of those.
- On a serverless host every file the page takes except visit notes (reports, weekly exports, 835/837 files, zips).

### A.3 What intake must do with a pair (A2's page and status route rely on these)

1. The report catalog's `file_id` of a report read from a pair must be the sidecar `sha256` (the original bytes), so
   `uploads.report_outcome(conn, sha256)` finds the result and an upload and the same file in Dropbox apply once.
   (A2's "already have this" check at start and finish reads `ema_report_files.file_id`, `files.sha256` and the
   `uploads` table by that same SHA-256.)
2. Ingest with `via="upload"` and `origin={"upload_id": <the pair name's prefix>, "pair": <pair name>, "user": sidecar user}`.
   No file name in a log line.
3. When a pair is processed (applied, already, held, refused, not a file RemitWatch reads, failed): move the DATA file
   to done/ first (`<data_dir>/incoming/done/<pair>` or `intake/done/<pair>`), then the sidecar, and add to the sidecar
   in done/ a `"result"`: `{"status": "applied"|"already"|"held"|"refused"|"waiting"|"not_read"|"failed", "lines": [plain
   sentences], "at": ISO time, "file_id": sha256}`. `uploads.record_view` shows `result.lines` when the catalog has no row
   for the file (non-report files, refusals before the catalog). For reports the page reads the catalog row itself.
4. Non-report pairs on the server: the simplest correct route is to unpack a .gz into `uploads.incoming(db_path)` and call
   `uploads.accept(db_path, tmp, sidecar["name"], sidecar["user"], sha=sidecar["sha256"])`: it unpacks zips one level,
   keeps the file under uploads/, queues it for `uploads.ingest_queued` in the same cycle, and refuses what RemitWatch
   does not read in plain words (use its `lines` as the result lines). Run it before `ingest_queued` in the cycle.
5. Serverless: read the pair as a Dropbox file is read (the router, `cloud._ingest_batch` with the origin element),
   streaming the .gz; Production through the LedgerHandle path. A .gz of a Production CSV is about 7% of the CSV (the
   206 MB file is 14.3 MB), so it fits the 60 MB rule.
6. `intake.kick(data_dir)`: A2 calls it (if `remitwatch.intake` has a callable `kick`) right after a server finish and
   after a one-piece report hand-off (`uploads.kick_intake`). It must return at once and start the processing in the
   background under the pipeline's lock (for example `pipeline.run_once_in_background(db_path, "upload")`).
7. Held uploads (with C): `ema_reports.decide(...)` returns `requeue` = the origin above. On Apply, move the pair back from
   done/ to ready/ (server) or from intake/done/ to intake/ (serverless), drop its `result`, and kick or trigger a run;
   intake then reads it with the decision applied.
8. Retention of done/ pairs is yours. When the done sidecar is gone, the page falls back to the catalog and `files` by
   SHA-256 ("Read on <date>").

### A.4 Small things in A1's files

- `dropbox_sync.DropboxClient._content_call`: A2's relay calls it for `files/upload_session/start`, `append_v2` and
  `finish`. Keep its signature, its `RuntimeError("Dropbox {endpoint} failed {status}: {body}")` text (A2 reads
  `incorrect_offset` and `"correct_offset": N` from it to tell a piece that already reached Dropbox) and the
  `DropboxConflict` on a path conflict. If you add public session methods, tell A2 and keep `_content_call`.
- `cloud.build_serving`: add `ema_reports.serving_tables(conn)` (needs_ema_reports item 5). A2's checklist reads
  `ema_report_files` (last arrival, as_of) and the per-upload result from the dashboard copy on Vercel; until then those
  show as "Has not arrived yet" there.
- `cloud.run_cloud_cycle` (optional, for parity): copy the state folder's `ema_howto.json` (`uploads.HOWTO_STATE`) into
  the store's meta key `ema:howto` (`uploads.HOWTO_META`) when it differs, and `cli import-cloud` the same, so a move to
  the server keeps the "How we pull this in our EMA" boxes. A2 reads the state file first on Vercel, else that meta key.
- `.env.example`: `REMITWATCH_UPLOAD_MAX_GB=2` (the most a file may weigh as it is sent, compressed when the page
  compresses it; default 2).

## B. For C (remitwatch/api.py and the pages)

- api.py ends with two lines from A2: `from . import api_uploads` and `app.include_router(api_uploads.router)`. Keep
  them after `app`, `require_auth`, `require_xhr`, `db`, `base_url` and `SHOW_PHI` are defined (api_uploads reads them
  from api at call time). Do not add routes at `/api/uploads/start`, `/api/uploads/chunk`, `/api/uploads/recent`,
  `/api/uploads/{id}/status`, `/api/uploads/{id}/finish`, `/api/ema/exports...`. With FastAPI 0.162 an included router
  is one `_IncludedRouter` entry in `app.routes` (it has no `.path`): any code or test that walks `app.routes` must skip
  entries without `path`.
- `/api/meta`: add `"report_uploads": True` on both hosts, and in index.html show the Upload link when
  `m.uploads || m.report_uploads` (today `$('#uploadLink').hidden = !m.uploads;`). The Upload page now takes EMA's
  reports and exports on Vercel too. Keep `uploads` meaning "visit notes by hand" (desk.js, features.js and faxes.js use it).
- `/api/uploads` (GET) and the one-piece `POST /api/uploads` stay as they are; `uploads.SERVER_ONLY` was reworded (it
  still names the Dropbox folder).
- Data tab: the held-report Apply/Discard route of needs_ema_reports item 9: for an origin with `upload_id`/`pair`, see
  A.3 item 7.
- Today and the Data tab may link "Upload" next to the EMA freshness alerts: `/upload` lists the five exports with
  "Overdue" chips from `GET /api/ema/exports` (`exports[].overdue`, `age_days`, `state_line`).

## C. For F (docs: README.md, docs/*): tests/test_docs.py fails on these until they are written

- README.md layout: name `remitwatch/api_uploads.py` (the Upload page's large files in pieces and the EMA export
  checklist) and, on the `static/` line, `upload.js` (the Upload page's script).
- docs/SERVER_RUNBOOK.md and docs/VERCEL_SETUP.md: name `REMITWATCH_UPLOAD_MAX_GB` (default 2: the most a file may weigh
  as it is sent, compressed when the page compresses it). Both guides fail `test_every_setting_the_app_reads_is_named_
  for_each_host` without it (the same test also lists eight REMITWATCH_EMA_* settings of other units).
- docs/OPERATIONS.md, the weekly routine (amendments 10 and 12): the Upload page's checklist of the five exports (what
  each is for, filters, columns checked on drop, format, cadence, overdue after 8 / 14 / 8 days), the practice's own
  "How we pull this in our EMA" box (any signed-in person edits it; who, when and earlier versions are kept), files of
  any size sent in 4 MB pieces (a text report compressed on the way), Pause / Resume, carrying on after a closed tab by
  choosing the same file again, "Already have this; nothing changed", and where a finished upload goes on each host
  (`<data_dir>/incoming/ready/`, the state folder's `intake/`). Server runbook: abandoned uploads are removed after a day.

## D. More for A1 (learned in the acceptance and the full run)

- openpyxl refuses a PATH whose name does not end in .xlsx (`ema_reports.recognize(path, name)` then returns None for a
  workbook). The upload's temporary file has no ending, so `uploads._report_hand_off` passes an open file instead. Any
  intake code that unpacks a `.gz` pair to a temporary file must keep the original ending (or pass a file object).
- `dropbox_sync.ingest_new_files` now records an unreadable layout as a `files` row with status `not_read` (and still
  logs the line). `uploads._finish` now treats `status == 'not_read'` as a failed upload and shows the loader's own
  line. tests/test_feeds.py (not A2's) still expects the old behaviour in two tests
  (`test_a_report_that_is_none_of_the_three_exports_is_neither_loaded_nor_recorded`,
  `test_an_ar_report_queued_before_the_upload_page_refused_it_is_not_read_and_says_why`): with the current router the
  second one's invented "A/R" layout is even read as an export (two rows added). Whoever owns the router decides which
  is right and updates those tests.

## E. More for C

- `GET /api/ema/exports` rows now carry `waiting` (bool): a report uploaded and not read yet. Such a row is not
  `overdue`, its `last_line` names the upload and its `state_line` says the next cycle (run) reads it. Today and the
  Data tab may show the same instead of "overdue" for that export.
