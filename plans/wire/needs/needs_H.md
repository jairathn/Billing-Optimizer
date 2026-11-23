# needs_H: wiring remitwatch/runlog.py, repair.py and diagnose.py (unit H -> the phase 3b hook-in agent, units F, G, E)

Status: FINAL for phase 3a, revised by unit H's fixer (2026-11-23, after the independent review of unit H). Unit H owns
remitwatch/runlog.py, repair.py, diagnose.py, tests/test_runlog.py, tests/test_repair.py, tests/test_diagnose.py,
CLAUDE.md and docs/TROUBLESHOOTING.md. It edited nothing else. What the fix changed for the hook-in: a new P0 (a cycle
that dies before its lock, and the scheduler's child exit code), P4 and C5 (`repair.not_retried` around the ingest),
P6 and C7 (`note_run_finished` takes the manifest's steps; C7 calls it too), P9 (the silent blocks' lines as of 02:20 UTC), L2 (the fax reads are now really tried again), W10 and W11 (new tests), section 9c (re-measured on real data; 9d: the scrubber's name rules, no hook needed), section 11 (two new questions). Every item
below names the file, the function, the anchor line (the exact text to search for; line numbers are as of 2026-11-23
00:40 UTC and will drift; other units are still editing detect.py, pipeline.py and cloud.py, so where a
line number disagrees the anchor text wins: every anchor below was unique at that time unless the item says otherwise), the code, and the test that proves it once wired. No identifiers anywhere below.

## 0. What the hooks call (fixed interfaces)

runlog (one module-level "run in progress", a contextvar; everything is scrubbed on the way in):
- `rl = runlog.start_run(kind, *, trigger, conn=None, db_path=None, state=None, logs_dir=None, host=None, run_id=None)`:
  the sink is `state` (serverless: the state folder's logs/), else `logs_dir`, else `<dirname(db_path)>/logs` (server:
  /data/logs). With `conn`: learns the store's patient names for the scrubber, records the feed positions, brings the
  store's run_log up to date from the index. Finds runs killed earlier (their open marker) and logs them. Never raises.
- `rl.run_id` (`run-YYYYMMDDTHHMMSSZ-xxxx`), `rl.step(name, inputs=None, contain=False, checkpoint=True)` (context
  manager: ms, memory, /tmp on serverless; an exception is logged with its stack and the inputs, then raised again
  unless contain=True), `rl.event(step, event, level=, rule=, rule_version=, item=, counts=, ms=, detail=, error=)`,
  `rl.log(*args)` (a drop-in for `log=print`: each progress line becomes an event; nothing is printed on Vercel;
  `rl.echo_to(fn)` also hands each scrubbed line to fn on a server), `rl.set(section, value)` (invariants,
  self_regulation, positions, outputs, or any named section: decisions, archive, safe_mode...), `rl.add_inputs([...])`,
  `rl.fail(exc, step)` (the exception that ended the run), `rl.save_to_store(conn)` (serverless, before the store is
  saved), `rl.finish(conn=None, out=None, state=None, tables=False) -> str` (writes the manifest, the index, removes
  the open marker, prunes old logs once a day, writes the run_log row when conn is given; returns the ONE line for
  Vercel's function log: `RemitWatch run <run_id>: <outcome> (<s> s)`).
- `runlog.caught(exc, step, inputs=None, level="error")`: one line in an except block that contains an exception; logs
  it with its stack to the run in progress; a no-op when none is in progress; the same exception logged twice is
  recorded once.
- `runlog.event(step, name, **fields)`: G and E log through this (selfcheck.py already does).
- Decision log: `before = runlog.snapshot_items(conn)`; `runlog.record_decisions(conn, run_id, before, after=None, *,
  step="detect", rule_version=None, rules=None) -> summary`; `runlog.record_changes(conn, run_id, [{"finding_id",
  "kind", "rule", "change", "evidence"}], step=..., rule_version=...)` for a module that changes items itself;
  `runlog.decisions_for(conn, finding_id, limit=50) -> {"items": [{"at", "run_id", "step", "kind", "rule",
  "rule_version", "change": {field: [before, after]}, "evidence": [...]}]}`. Stored in the table item_decisions (a
  table of its own, not finding_events: finding_events is read by save(), the activity feed and the drawer's history,
  and per-run automatic rows would swamp them). A person's actions keep their finding_events rows.
- `runlog.ensure_schema(conn)` (run_log, item_decisions), `runlog.prune_store(conn)`, `runlog.panel(conn, logs_dir)`,
  `runlog.logs_dir_for(db_path)`, `runlog.positions(conn, state)`, `runlog.silent_excepts(paths)`.
- Since the review: `runlog.record_early_failure(exc, step, *, trigger=, db_path=, state=)` (a run that failed before its
  run log started: one failed run with the stack in the logs folder, which needs no store; never raises),
  `runlog.note_child_exit(db_path, returncode, *, trigger=, started=)` (the scheduler's child ended badly: noted once;
  a child that left its marker is recorded as killed at once). `runlog.scrub(value, key=None, *, cap=None)` keeps
  whole lists (the bundle). The open marker is written at each step's start (on Vercel also every 20 s within a long
  step) with rss, peak and the last 20 events; the run that finds a killed run writes that run's record (manifest and
  trail), so the bundle and the panel have it. A contained step (`contain=True`) drops its exception's traceback, and
  the run log keeps no exception (a mark on the exception tells a repeat). A progress line is an error only when the
  scrubbed line holds the word ERROR (WARN/WARNING for a warning). `panel()` adds `stale` (no run for twice the usual
  gap) and `logged_errors` per run; its index lines carry `steps_ok`. Word keys (status, source, how, rule...) are no
  longer kept as they are: only keys with a strict shape are (runlog.KEY_SHAPES); two-letter names the store holds are
  learned and masked when written with a capital.

repair:
- `reg = repair.registry_for(rl, conn=conn)`; `reg.save()` (logs folder's repair_state.json and meta repair:state);
  `reg.dirty`.
- `repair.retry_call(fn, *args, target=, registry=, until=<run deadline epoch s>, **kwargs)`: transient failures
  (`repair.is_transient`: connection reset, timeout, 5xx, 429, a stalled SFTP line) tried again after 2, 8, 30 s; out
  of tries raises `repair.Held` (a RuntimeError) from the last error, which the caller's existing except block contains.
  A refused token or password, a conflict, a code bug, a fax whose sending may have happened: raised at once.
  (`until`, not `deadline`, so a callee's own `deadline` argument passes through.)
- `repair.resilient(client, target, registry=, until=, only=None)`: the same for each call of a client; an iterator is
  retried only before its first item; calls in `repair.NEVER_RETRIED` (send, upload, put, move, delete...) are never
  wrapped; `only` limits the wrapped calls (the fax line: status, inbox, fetch, check, find_sent). The client's own
  signatures stay visible to `inspect.signature`.
- Since the review: `repair.not_retried(fn)` (a callback a retried call makes: what it raises is never retried, P4, C5);
  `is_transient(exc, read=False)` reads words only of the network's and the clients' errors (OSError, RuntimeError,
  TriZettoError, FaxError, SSHException), follows a client error's cause, matches a status only as a status ("failed
  503", "(503", "HTTP 429"), never a code bug's; `resilient(..., only=...)` treats the named calls as reads (a fax's
  maybe_sent does not stop them). A run counts once in a target's `failed_runs` however many calls were held; after a
  hold, the run's later calls to that target get one plain try each, PROBES_AFTER_HOLD (3) of them, then Held at once.
  `registry_for` clears killed-run counts from the run index (a later finished run that passed the step); kills are
  saved at once by note_killed_runs. `sweep_reports` holds only reports the reader held (not refused ones) and ends a
  hold once its catalog row is discarded, applied or gone, or a later export of the same report and scope is applied.
- `repair.read_file(key, fn, ..., registry=, kind=)`, `repair.rebuild_if_failing(name, check, rebuild, registry=)`,
  `repair.note_killed_runs(reg, killed, repaired=None)`, `repair.note_run_finished(reg, steps)`,
  `repair.note_stale_lock(reg, keys, how)`, `repair.log_stale_locks(keys, how, db_path=, state=)` (outside a run),
  `repair.check_schema(conn, ensure, expected, registry=)`, `repair.note_pressure(reg, actions, reading=)`,
  `repair.sweep_reports(conn, reg, since)`, `repair.alert_candidates(conn)`, `repair.open_problems(conn=, sink=)`,
  `repair.recent(conn)`, `repair.prune(conn)`, `repair.ensure_schema(conn)` (repair_log).

diagnose: `diagnose.build_bundle(conn=None, db_path=None, state=None, logs=None, days=7, run_id=None, out_path=None)
-> {"bytes" or "path", "parts", "runs", "scan", "size"}`; `diagnose.main(argv)` (`python -m remitwatch.diagnose`);
`diagnose.register_part(name, fn)` (G and E may add a part; it is scrubbed like the rest).

No new setting. Constants: runlog.SERVER_KEEP_DAYS 180, SERVERLESS_KEEP_DAYS 90, budgets 512 MB / 100 MB,
DECISIONS_KEEP_DAYS 400, STORE_RUNS_KEEP_DAYS 200; repair.REPAIR_KEEP_DAYS 200.

## 1. remitwatch/detect.py (both hosts: the decision log is written where items change)

D1. `ensure_schema`, after its last line (anchor `from . import ema_reports; ema_reports.ensure_schema(conn)`, line 321):
```python
    from . import repair, runlog; runlog.ensure_schema(conn); repair.ensure_schema(conn)      # noqa: E702  (logs: run_log, item_decisions, repair_log)
```
(cloud.empty_serving calls ensure_schema, so the dashboard's copy gets the three tables too.)

D2. `Detector.run`, first lines of its body (anchor `    def run(self) -> dict:`, line 4034):
```python
    def run(self) -> dict:
        from . import runlog
        self.state_rules: dict[str, str] = {}
        self._decisions_before = runlog.snapshot_items(self.conn)     # what the items were before this run (the decision log)
        try:
            ...(unchanged)
```

D3. `Detector._set_states`, after `r["appeal_block"] = st.get("appeal_block")` (line 3525):
```python
            getattr(self, "state_rules", {})[r["finding_id"]] = st.get("rule")        # the state rule, for the decision log
```

D4. `Detector.run`, the end (anchor `        self.hook_hide()` then `        return out`, lines 4083-4084). After every
post-save change (hide rules, hook_hide; and before any safe-mode hold by selfcheck if it runs inside run(); if G holds
closures after run() returns, G records its holds with `runlog.record_changes(..., kind="held", rule="selfcheck:safe_mode")`):
```python
        self.hook_hide()
        try:
            from . import runlog
            self.decisions = runlog.record_decisions(self.conn, self.run_id, self._decisions_before,
                                                     rules=getattr(self, "state_rules", None), rule_version=DETECT_VERSION)
            rl = runlog.current()
            if rl is not None:
                rl.set("decisions", self.decisions)
        except Exception as exc:        # noqa: BLE001  (the items are saved; only this run's decision rows are missing, and the log says so)
            from . import runlog
            runlog.caught(exc, "decisions")
        return out
```

D5. `Detector._ema_step`'s except block (anchor `self.ema_errors.append({"step": name, "error": ...})`, line 2768), first
line of that block's work:
```python
            from . import runlog
            runlog.caught(exc, "ema:" + name)
```
and in `Detector.run` the two broad excepts (anchor `from .link import EMA_COVERAGE_KEY, save_ema_coverage`, line 4042:
`except Exception as exc:` then `runlog.caught(exc, "links")`; anchor `self.conn.rollback()        # the analysis is
saved`, line 4082: `except Exception as exc:` then `runlog.caught(exc, "hide_rules")`). The silent one at line 4052
(`self.conn.commit()` then `pass` inside the coverage fallback) becomes `except Exception as exc2: runlog.caught(exc2,
"links_fallback", level="warning")`.

Proves it: W3 and W4 below. RULE_FILES stays as it is (runlog does not change what detection finds).

## 2. remitwatch/pipeline.py (the server's cycle)

P0 (new, review finding: a cycle that dies before its lock leaves no trace). a) `run_cycle`, the three lines that open the
store and take the lock (anchor, unique as a block: `    store = Store(db_path)` followed by `    out: dict = {"started":
_now(), "trigger": ...}` and `    token = acquire_lock(store.conn)`, lines 437-439) become:
```python
    from . import runlog
    try:
        store = Store(db_path)
        out: dict = {"started": _now(), "trigger": os.environ.get("REMITWATCH_TRIGGER", "manual")}
        token = acquire_lock(store.conn)
    except Exception as exc:        # a store that cannot be opened or brought up to date: no run log yet, so say so
        runlog.record_early_failure(exc, "open_store", trigger=os.environ.get("REMITWATCH_TRIGGER", "manual"), db_path=db_path)
        raise
```
b) `Scheduler.run_child` (anchor `    def run_child(self, trigger: str) -> int:`, line 736): the Popen failure and the exit
code are noted (stdout and stderr stay DEVNULL: the child's own run log has the detail, scrubbed; raw stderr could hold
a cell). The body after `db_path, started = self.db_path, time.time()` becomes:
```python
        from . import runlog
        try:
            proc = subprocess.Popen(self.argv(db_path), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            runlog.record_early_failure(exc, "start_child", trigger=trigger, db_path=db_path)
            return -1
        with self._guard:
            self._children[proc.pid] = (proc, started, db_path)
        rc = -9
        try:
            rc = proc.wait(timeout=CHILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        finally:
            with self._guard:
                self._children.pop(proc.pid, None)
            # a child that ended without releasing its locks (killed: the time limit above, the system's memory limit, a
            # stop) left them behind: the cycle's, and the fax run's or the backup's when it was in one
            _release_child(db_path, proc.pid, started)
        if rc:
            runlog.note_child_exit(db_path, rc, trigger=trigger, started=started)     # never raises; noted once
        return rc
```
Proves it: W10 and W11.

P1. `_ema_step` (line 258), its except block:
```python
    except Exception as exc:       # noqa: BLE001
        from . import runlog
        runlog.caught(exc, key)
        out[key] = {"error": type(exc).__name__}
        return None
```

P2. `run_cycle`, right after the lock is taken (anchor `token = acquire_lock(store.conn)` and its `if not token:` block,
line 387; a skipped cycle writes no run log, the running one does):
```python
    from . import repair, runlog
    rl = runlog.start_run("cycle", trigger=out["trigger"], db_path=db_path, conn=store.conn, host="server")
    reg = repair.registry_for(rl, conn=store.conn)
    if rl.manifest["sections"].get("killed_runs"):
        repair.note_killed_runs(reg, rl.manifest["sections"]["killed_runs"])
    log = rl.echo_to(log).log           # each progress line is an event (scrubbed); the console still sees it
```
(beat_log, defined just below, keeps calling `log`.)

P3. The Dropbox sync (anchor `st = dropbox_sync(store, full=full, log=beat_log)`, line 425):
```python
                try:
                    from .dropbox_sync import DropboxClient
                    with rl.step("dropbox_sync"):
                        st = dropbox_sync(store, client=repair.resilient(DropboxClient(), "dropbox", registry=reg,
                                                                         until=time.time() + 1800),
                                          full=full, log=beat_log)
                    new_files = st.pop("new_files", [])
                    out["sync"] = st
                except Exception as exc:
                    runlog.caught(exc, "dropbox_sync")
                    ...(the existing lines: new_files from exc, out["sync"] error, rollback)
```
Check first: nothing in dropbox_sync.sync tests `isinstance(client, DropboxClient)` (today nothing does).

P4. TriZetto (anchor `tz = trizetto.pull(store.conn, lambda items: ...`, line 453):
```python
            dl = time.time() + 3000
            try:
                with rl.step("trizetto"):
                    tz = repair.retry_call(trizetto.pull, store.conn,
                                           repair.not_retried(lambda items: _ingest_batch(store, items, beat_log)),
                                           deadline=dl, log=beat_log, target="trizetto", registry=reg, until=dl - 900)
            except Exception as exc:
                runlog.caught(exc, "trizetto")
                tz = {"error": str(exc)[:300]}
```
(A whole pull is safe to repeat: files already read are skipped. A refused or unclear sign-in is never retried, and
neither is anything the ingest raises: `repair.not_retried` marks it, so a code bug inside _ingest_batch, whatever its
message says, is raised at once and never repeats the SFTP pull. The anchor today is line 519,
`tz = trizetto.pull(store.conn, lambda items: _ingest_batch(store, items, beat_log), deadline=time.time() + 3000, log=beat_log)`.)

P5. Steps around the rest: `with rl.step("uploads"):` around `uploads.ingest_queued(...)` (and `runlog.caught(exc,
"uploads")` in its except), `with rl.step("detect"):` around `summary = run_detection(db_path)` changed to
`summary = run_detection(db_path, run_id=rl.run_id)` (line 499: the detection run and the run log share one id),
`with rl.step("alerts"):` around `out["alerts"] = run_alerts(store.conn)` changed to `run_alerts(store.conn,
run_id=rl.run_id)` (line 522), `runlog.caught(exc, "notes_matched")` in that except, `runlog.caught(exc, "report")`
around the report.

P6. Before the try's last two lines, `out["finished"] = _now()` then `return out` (line 536; the same assignment also
stands in the except block below it, line 540: not that one):
```python
        repair.sweep_reports(store.conn, reg, rl.manifest["started_at"])
        repair.note_run_finished(reg, rl.manifest["steps"])      # the manifest's steps as they are: only those that went well
        runlog.prune_store(store.conn)
        repair.prune(store.conn)
        if reg.dirty:
            reg.save()
```

P7. The cycle's own except (anchor `out["error"] = f"{type(exc).__name__}: {exc}"`, line 539): first line
`rl.fail(exc, "cycle")`. The finally block, after `_meta_set(store.conn, LAST_KEY, out)` / `store.conn.commit()`
(line 560), before `store.close()`:
```python
            try:
                rl.finish(conn=store.conn, out=out)
            except Exception as exc:        # noqa: BLE001  (the cycle's own summary is already in LAST_KEY)
                print(f"RemitWatch: the run's log could not be finished ({type(exc).__name__}).", file=sys.stderr, flush=True)
```
`rl` must exist there: define `rl = None` before the `try:` and guard with `if rl is not None`.

P8. Locks removed outside a run. `start_scheduler_from_env` (anchor `gone = clear_locks_left_behind(db_path)`, line 717),
inside `if gone:`:
```python
            from . import repair
            repair.log_stale_locks(gone, "pipeline.clear_locks_left_behind", db_path=db_path)
```
`_release_child` (anchor `clear_lock_of_pid(db_path, pid, since=started)`, line 698):
```python
    try:
        if clear_lock_of_pid(db_path, pid, since=started):
            from . import repair
            repair.log_stale_locks([f"locks of process {pid}"], "pipeline.clear_lock_of_pid", db_path=db_path)
    except Exception as exc:
        from . import runlog
        runlog.caught(exc, "release_child", level="warning")
```

P9. The other silent broad excepts in pipeline.py: runlog.silent_excepts lists 9 at 02:20 UTC (three more appeared
in `people_changed_since_detection` for a while and were made to log by their owner, so the ceiling test passes again):
`acquire_lock` (line 90, a lock that cannot be read: `runlog.caught(exc, "lock", level="warning")` before the takeover);
`drop_kept_copies` (402); `beat_log` (448, heartbeat); the rollback after a sync failure (489); the ledger close (622);
the finally's rollback and LAST_KEY (631, 638); `_end_child` (768); `_release_child` (775, P8). Each becomes `except
Exception as exc:` + `runlog.caught(exc, "<that step>", level="warning")` (the behaviour stays: the same return value
or pass after the line). Then lower `SILENT_CEILING["pipeline.py"]` in tests/test_runlog.py to the new count (0 is the
goal).

## 3. remitwatch/cloud.py (the serverless cycle)

C1. `acquire` (line 143), the takeover branch (anchor `state.put(LOCK, body, mode="update", rev=m["rev"])`, line 159):
record that a stale lock was taken over, for the run to log:
```python
                state.put(LOCK, body, mode="update", rev=m["rev"])
                global LAST_TAKEOVER
                LAST_TAKEOVER = {"since": m.get("modified")}
                return token
```
with `LAST_TAKEOVER: dict | None = None` beside the constants, and `LAST_TAKEOVER = None` at the top of `acquire`.

C2. `run_cloud_cycle`, right after `token = acquire(state)` and its `if not token:` (line 1481):
```python
    from . import repair, runlog
    rl = runlog.start_run("cycle", trigger=trigger, state=state, host="serverless")
    log = rl.log                    # Vercel's own log gets the one line printed at the end, never detail
    reg = None
```

C3. After the store is loaded and its schema made (anchor `conn.executescript(ALERTS_DDL + CLOUD_DDL)`, after line 1493):
```python
        runlog.SCRUB.learn(conn)
        rl.manifest["positions"] = {"before": runlog.positions(conn, state)}
        runlog.sync_store_from_index(conn, rl.sink)
        reg = repair.registry_for(rl, conn=conn)
        if LAST_TAKEOVER:
            repair.note_stale_lock(reg, ["lock.json"], "cloud.acquire")
```
and after the A1 repair (anchor `rep = repair_ledger(store, state, log=log)` / `out["ema_repair"] = rep`):
```python
        if rl.manifest["sections"].get("killed_runs"):
            repair.note_killed_runs(reg, rl.manifest["sections"]["killed_runs"], repaired=out.get("ema_repair"))
```
Note the name clash: cloud.py's `repair_ledger` is unrelated; import the module as `from . import repair as self_repair`
if `repair` reads ambiguously there.

C4. `runlog.caught(exc, ...)` as the first line of each except block that contains a step: `ema_migrate` (line 1508),
`ema_repair` (1515), `ema_howto` (1522), `uploads_sweep` (1542), `intake` (1549), `trizetto` (1559), `ema_waiting` (1575),
`intake_retain` (1695), `report_error` (1734), and the activity step's errors. Wrap with `rl.step(...)`: `load` (load_raw), `intake`, `trizetto`,
`dropbox` (sync_and_ingest), `detect`, `activity`, `alerts`, `save_store` (save_raw), `save_serving`, `mail`. Each
step's start writes the open marker to the state folder (so a run Vercel stops is blamed on the step it was in): about
10 Dropbox puts of a few KB per run, about 3 s at 0.3 s each, plus one every 20 s inside a long step.

C5. The client (anchor `ingest = sync_and_ingest(store, client, state, deadline=t0 + budget * 0.5, ...)`, line 1569):
pass `repair.resilient(client, "dropbox", registry=reg, until=t0 + budget * 0.5)` in place of `client`. The state folder
(DropboxState) is never wrapped: its writes are puts and moves (never retried by a wrapper) and its own client retries 5xx.
TriZetto (anchor `tz = trizetto.pull(conn, lambda items: _ingest_batch(store, items, log), deadline=t0 + budget * 0.4,
log=log)`, line 1619): `repair.retry_call(trizetto.pull, conn, repair.not_retried(lambda items: _ingest_batch(store,
items, log)), deadline=t0 + budget * 0.4, log=log, target="trizetto", registry=reg, until=t0 + budget * 0.3)`.

C6. Detection (anchor `det = Detector(store)`, line 1621): `det = Detector(store, rl.run_id)`.

C7. Before the store is saved (anchor `meta = save_raw(state, store)`, line 1684):
```python
            repair.sweep_reports(conn, reg, rl.manifest["started_at"])
            repair.note_run_finished(reg, rl.manifest["steps"])  # the steps so far that went well clear their kill counts
            runlog.prune_store(conn)
            repair.prune(conn)
            if reg is not None and reg.dirty:
                reg.save()              # meta repair:state (saved with the store) and logs/repair_state.json
            rl.save_to_store(conn)
```
(and make `shown_changed`'s neighbour condition save the store when `reg.dirty` was true, so the repair_log rows
persist: add `or bool(reg is not None and reg.dirty)` to the condition that calls save_raw.) note_run_finished here is
belt and braces: registry_for already clears a step's kill count from the run index once a later run passed it, so a
missing hook can no longer make the alert permanent (the review's Vercel finding).

C8. The outer except (anchor `out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"`, line 1754): first line
`rl.fail(exc, "cycle")`. In the finally, after `write_status(state, out)` (line 1773) and before `release(state, token)`:
```python
        try:
            if reg is not None and reg.dirty:
                reg.save()              # a failed run still counts its failure in the state folder
            print(rl.finish(out=out, state=state), flush=True)      # the one line in Vercel's function log
        except Exception as exc:        # noqa: BLE001
            print(f"RemitWatch run {rl.run_id}: the log could not be written ({type(exc).__name__})", flush=True)
```
The silent broad excepts in cloud.py (11: lines 95 peak_mb, 171 release, 438 report_kind, 634 drop_copies, 674
repair_ledger, 866 report_kind_path, 1468 write_status, 1755/1764 the finally, 1790 trigger_cycle, 1805 _same_site):
`runlog.caught(exc, "<step>", level="warning")` in each; then lower `SILENT_CEILING["cloud.py"]`.

C9. The dashboard's copy (anchor `    ("ema_sources", None, None),`, line 1110): add after it
```python
    # what the runs logged (remitwatch.runlog, repair): the Data tab's panel and the drawer's "Why did this change?"
    ("run_log", None, None), ("repair_log", None, None),
    ("item_decisions", None, "finding_id IN (SELECT finding_id FROM main.findings) AND at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-180 days')"),
```
and bump `SERVING_BUILD` once more (line 58: SERVING_TABLES changed). Another unit already moved it from "7" to "8" for
the A/R's note columns, uncommitted at 00:40 UTC; whatever it holds then, the hook makes it the next number ("9" after "8"). Sizes measured on the world: section 9.

## 4. remitwatch/alerts.py

A1. `AlertEngine.evaluate`, after `self.ema_rule()` (line 371), add `self.repair_rule()`, and the rule:
```python
    def repair_rule(self) -> None:
        """SELF_REPAIR_FAILED: a self-repair rule (remitwatch.repair) ran out of attempts. The title says what failed,
        where, what was tried and the next step; it clears by itself once the target works again."""
        try:
            from . import repair
            cands = repair.alert_candidates(self.conn)
        except Exception as exc:        # noqa: BLE001
            from . import runlog
            runlog.caught(exc, "alerts:repair", level="warning")
            return
        for a in cands:
            self._raise(a["kind"], a["severity"], a["title"], a["detail"], None, "repair", None, None, None, a.get("n"), None,
                        key=tuple(a["key"]))
```
and in the module docstring's Kinds list:
`SELF_REPAIR_FAILED a self-repair rule ran out of attempts (remitwatch.repair): what failed, where, what was tried, the next step`.

## 5. remitwatch/cli.py

L1. A `diagnose` command (after `cmd_compare_cloud`, line 901; parser beside `compare-cloud`, line 1010):
```python
def cmd_diagnose(args: argparse.Namespace) -> int:
    """The diagnostic bundle (remitwatch.diagnose): run logs, errors, self-checks and repairs, no patient data."""
    from . import diagnose
    return diagnose.main(["--db", args.db, "--days", str(args.days)] + (["--run", args.run] if args.run else [])
                         + (["--out", args.out] if args.out else []))
```
```python
    dg = sub.add_parser("diagnose", help="a diagnostic bundle: run logs, errors, self-checks and repairs (no patient data)")
    dg.add_argument("--days", type=int, default=7, help="the runs of the last N days")
    dg.add_argument("--run", default=None, help="only this run id")
    dg.add_argument("--out", default=None, help="where to write the zip (default: a diagnostics folder beside the store)")
    dg.set_defaults(fn=cmd_diagnose)
```
README's `cli.py` line then lists `diagnose` (test_docs.test_the_readme_lists_every_command): unit F.

L2. `_fax_run` (anchor `prov = fax.provider()`, line 459), after the `if prov is None` return:
```python
    from . import repair
    prov = repair.resilient(prov, "srfax", only=("status", "inbox", "fetch", "check", "find_sent"))   # never send
```
(`prov.name` and every other attribute pass through. `only` is the caller's word that these calls only read: fax.SRFax
marks every dropped connection maybe_sent, a status check's too, and the wrapper tries those reads again all the same;
a send is never wrapped. Optional, for the fax module's owner: `fax.SRFax._call` could set maybe_sent only for
`Queue_Fax`; nothing here depends on it.)

## 6. remitwatch/api_ops.py (new in 3b) and its include line in api.py

O1. Routes (signed-in users; the bundle holds no patient data and the alerts tell a biller to download it). Same auth
pattern as remitwatch/api_results.py (`_api()` imported lazily, `_user` dependency):
```python
"""The Data tab's operations panel "Logs and diagnostics" (remitwatch.runlog, repair, diagnose), and the decision log an
item's drawer shows ("Why did this change?"). Every route needs the sign-in."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import diagnose, runlog, runtime

router = APIRouter()
_security = HTTPBasic(auto_error=False)


def _api():
    from . import api
    return api


def _user(request: Request, credentials: Optional[HTTPBasicCredentials] = Depends(_security)) -> str:
    """The same sign-in as every other route."""
    return _api().require_auth(request, credentials)


def _logs():
    """Where this host's logs are: the server's logs folder, or (Vercel) the state folder."""
    if runtime.serverless():
        from .state import state_from_env
        return state_from_env(fast=True)
    return runlog.logs_dir_for(_api().DB_PATH)


@router.get("/api/ops/logs")
def ops_logs(_: str = Depends(_user)):
    conn = _api().db()
    try:
        return runlog.panel(conn, _logs())
    finally:
        if not runtime.serverless():
            conn.close()


@router.get("/api/ops/diagnostic-bundle")
def ops_bundle(days: int = Query(7, ge=1, le=60),
               run: Optional[str] = Query(None, pattern=r"^run-\d{8}T\d{6}Z(-[0-9a-f]{4,8})?$"), _: str = Depends(_user)):
    if runtime.serverless():
        res = diagnose.build_bundle(conn=_api().db(), state=_logs(), days=days, run_id=run)
    else:
        res = diagnose.build_bundle(db_path=_api().DB_PATH, days=days, run_id=run)
    name = time.strftime("remitwatch-diagnostic-%Y%m%d-%H%M.zip", time.gmtime())
    return Response(res["bytes"], media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/api/findings/{finding_id}/decisions")
def finding_decisions(finding_id: str = Path(..., pattern=r"^[0-9a-f]{8,64}$"), _: str = Depends(_user)):
    conn = _api().db()
    try:
        return runlog.decisions_for(conn, finding_id)
    finally:
        if not runtime.serverless():
            conn.close()
```
(C1 already calls GET /api/findings/{id}/decisions from the drawer (static/features.js, `xpDecisions`, reading `items[].at,
change, rule, rule_version`) and /api/meta already probes the route (`"decisions": _route_here(...)`), so the drawer's
"Why did this change?" appears as soon as O1 and O2 land, with no page change.)
(On Vercel `api.db()` is the dashboard's copy, shared by the instance: never closed here. build_bundle raises on a
problem; let that be a 500 with no detail, never a zip holding an exception's text.)

O2. api.py, after `from . import api_results` / `app.include_router(api_results.router)` (lines 5007-5008):
`from . import api_ops  # noqa: E402  (unit H: logs and diagnostics, the decision log)` and
`app.include_router(api_ops.router)`.

O3. On Vercel the panel reads index.json and repair_state.json from the state folder (two small downloads); the bundle
reads the index and each chosen run's file (one download per run, 7 days is about 10 to 30). Both well inside a page
request's time.

## 7. static/ops.js, "Logs and diagnostics" panel (inside C2's `<section id="ops-panels">`)

From GET /api/ops/logs: the `sentence` as the panel's first line; a table of `runs` (started, outcome in words,
seconds, peak memory, errors, repairs; a killed run shows its last step); `open_problems` (what failed, since when, the
`next_step` verbatim); a button "Download the diagnostic bundle" (GET /api/ops/diagnostic-bundle) with the `bundle.what`
sentence under it. Plain English, no em dashes. Item drawer (C1): GET /api/findings/{id}/decisions as "Why did this
change?": each row's `at` (date), the rule in words, before and after of each changed field.

## 8. For unit G (selfcheck) and unit E (archive)

- Log through `runlog.event(...)` and `runlog.caught(...)` (selfcheck.py does already). Attach results to the run with
  `runlog.current().set("invariants", <after_detection's dict>)` and `set("self_regulation", ...)`; E:
  `set("archive", <archive.run's dict>)`. The bundle's examples/ read the run's `invariants.checks[*]` (`id`, `status`
  "fail" or "error", `examples`: finding ids).
- An item a module changes after detection (E retiring an item, G holding a closure in safe mode) is recorded with
  `runlog.record_changes(conn, run_id, [...], step="archive" | "selfcheck", rule_version=...)`, kind and rule named
  ("state", "archive:retired"; "held", "selfcheck:safe_mode"), so "Why did this change?" covers it.
- Self-regulation actions also go to the registry: `repair.note_pressure(reg, actions, reading={...})`.
- A derived-table check G runs can be repaired by H's rule: `repair.rebuild_if_failing("ledger_summaries", check,
  lambda: ema_ledger.refresh(store, ledger, parts="AB"), registry=reg)`; `safe_mode` True in its answer is G's cue.
- G and E may add their own bundle part: `diagnose.register_part("selfcheck", fn)` (fn(conn) -> dict). The generic
  reader already includes the tables named selfcheck_* except selfcheck_held.

## 9. Tests that prove the wiring (add with the hooks; all invented data, both hosts)

W1 (pipeline): after `pipeline.run_cycle(db, sync=False, local=<folder with tests.test_runlog.denial_files>, email=False)`
the logs folder beside the store holds `runs-<day>.jsonl` whose last manifest has `outcome == "ok"`, the step "detect",
and `sections.decisions.by_kind`; `events-<day>.jsonl` holds `run_start`; the store's run_log has that run.
W2 (cloud): after `cloud.run_cloud_cycle(state, client, email=False)` (tests/test_cloud.py's `box` fixture) the state
folder holds `logs/runs/<day>/<run_id>.jsonl.gz` and `logs/index.json`; `capsys` shows exactly one line matching
`^RemitWatch run run-\d{8}T\d{6}Z-[0-9a-f]{4}: ok \(` and nothing else (the cycle's `log` is the run log).
W3 (decisions): tests/test_runlog.py::test_a_closure_by_a_later_payment_is_recorded_with_its_rule_and_evidence without
its own snapshot/record calls: two `Detector(...).run()` and `runlog.decisions_for(conn, fid)` has the closed row
(both store kinds).
W4 (privacy): tests/test_runlog.py's two `test_no_planted_identifier_reaches_any_log_byte_*` tests: drop their own
`runlog.start_run`/`finish` and the `_wired_ema_step` monkeypatch (the cycle does both now); keep the poisoned step and the
byte scan. They must stay green.
W5 (alerts): a registry with an exhausted network entry saved to meta, then `alerts.run_alerts(conn)` raises one
SELF_REPAIR_FAILED alert; after `reg.succeeded(...)` + save, the next run_alerts clears it.
W6 (routes): with the API test client on both hosts: GET /api/findings/<fid>/decisions returns the closed row; GET
/api/ops/diagnostic-bundle returns a zip (`diagnose.unpack`) with runs/, events/, errors.json, store.json, no planted
word; GET /api/ops/logs returns the panel. A request without the login gets 401.
Already run once, as a scratch check outside the repository: wire/H3a/ops_check/test_ops_hook.py takes O1 (the code
block in section 6, verbatim), D1 and C9 from this file, wires them in memory and runs W6 on both hosts with invented
data (two detection runs, a closure by a later payment, a Dropbox call held after its retries). It passes on Python 3.13
and 3.12 (2026-11-23 00:30 UTC); its serverless half needs Connection.serialize, absent in the SQLite 3.40 build. Once
api_ops.py exists, copy its two tests into tests/test_runlog.py without the in-memory wiring.
W7 (cli): `python -m remitwatch.cli --db <db> diagnose --days 2 --out <zip>` exits 0 and writes the zip.
W8 (retry wiring): a FakeDropbox whose list_folder raises requests.ConnectionError twice: run_cycle (server, Dropbox
configured with the fake) still lists and reads the files; repair_log has two "retrying" rows and one "repaired".
W9: lower tests/test_runlog.py's SILENT_CEILING for pipeline.py and cloud.py to what remains (target 0).
W10 (P0a, the review's test made exact, for tests/test_runlog.py): a store that cannot be opened makes the cycle fail
before its lock, and the panel shows it. Already run once outside the repository: wire/H3a/fix/p0_check/apply_and_test.py
applies P0a and P0b's code blocks verbatim from this file to a scratch copy of the package and runs W10 and W11 (pass).
```python
def test_a_scheduled_cycle_that_dies_before_its_lock_is_visible_on_the_panel(tmp_path, monkeypatch):
    from remitwatch import pipeline
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    store = Store(db)
    yesterday = runlog.utcnow() - timedelta(days=1)
    rl = runlog.start_run("cycle", trigger="schedule", db_path=db, conn=store.conn, host="server", now=yesterday)
    rl.finish(conn=store.conn, now=yesterday + timedelta(minutes=2))      # yesterday's good run
    store.close()

    def broken_store(path, *a, **kw):
        raise sqlite3.OperationalError("duplicate column name: work_state")     # a migration bug
    monkeypatch.setattr(pipeline, "Store", broken_store)
    with pytest.raises(sqlite3.OperationalError):
        pipeline.run_cycle(db, sync=False, email=False, report=False)
    c = sqlite3.connect(db)
    p = runlog.panel(c, runlog.logs_dir_for(db))
    c.close()
    assert p["last_run"]["outcome"] == "failed" and p["last_run"]["first_error"]["type"] == "OperationalError"
```
W11 (P0b): `pipeline.Scheduler` with `argv` monkeypatched to `[sys.executable, "-c", "import sys; sys.exit(3)"]` and a
db_path in tmp_path: `run_child("schedule") == 3`, and `runlog.read_index(runlog.DirSink(runlog.logs_dir_for(db)))[-1]`
has outcome "failed" and exit_code 3; with `"import sys; sys.exit(0)"` nothing is added.

## 9b. Measured on the world copy (eg2 store with the four EMA reports, server mode; aggregates only)

- First cycle under a run log (force_detect): 112 s, peak 908 MB; 16,165 items before, 17,755 after; the decision log
  wrote 18,136 rows (15,583 first states, 1,747 raised, 452 signal changes, 194 closed with their rule, 157 no longer
  raised, 3 reopened). On the live store the first wired run will look like this: almost every item gets its first
  work_state. Rows average 407 bytes after the basis rule (state rows), so about 7 MB for 17,769 items, written in
  3.7 s; the serving copy filter (current items, 180 days) carries them: plan about 1 MB more in serving.sqlite.gz.
- Second cycle with no new file: 77 decisions (17 SECONDARY_NOT_BILLED raised, 3 gone, 42 EMA outcomes, 15 expired);
  it ran after UTC midnight and after the first cycle's post-detection ledger refresh (X10), so dates moved and EMA
  summaries changed. A third detection the same day changed nothing but the injected fault below: the decision log
  is what makes such drift visible (for INT/G: "a second cycle changes nothing" holds within a day and a refresh).
- Snapshot of 17,769 items: 0.59 s, 8 MB. The scrubber learns 10,808 name words in 0.59 s. 2,000 log lines scrub in 0.08 s.
- Faults: a killed run's marker found and recorded as killed (and the crash of the measuring script itself was found
  the same way); a lock of a dead process removed by clear_locks_left_behind and logged; a flaky Dropbox listing over
  the real mirror (1,423 files) repaired after 2 retries, an always-failing one held after 4 tries; a Production file
  cut inside a row held by the reader (0 rows applied), swept into the registry, SELF_REPAIR_FAILED alert raised; the
  ledger's summaries tampered by $1,234.56 detected by selfcheck.check_ledger, rebuilt once (refresh AB, 31 s), check
  passed, no safe mode; a code bug dropping EMA_DENIED: 722 "gone" decisions, all of that signal, and the next run
  with the fix raised the same 722.
- Bundle from the world: 7 runs, 26 KB, 0.4 s, nothing masked on the second pass. Scan of the bundle and every log byte
  against the store's 10,839 name words and 411,668 identifiers: no name that is not also a code or payer word; two
  identifier-shaped tokens matched, both coincidences (a word of RemitWatch's own code that one claim's control number
  equals; a transaction-set control number inside the store's own keys that one member id equals, in the store's
  decision table only).

## 9c. Re-measured by the fixer on a fresh world copy (2026-11-23, 01:35-01:50 UTC; aggregates only; the machine was busy)

- The scrubber learns 10,849 name words (42 of two letters) in 0.3 s; before the fix 10,807 (none of two letters).
- A first cycle under a run log: 194 s (the builder's 112 s on a quieter machine), peak 916 MB, outcome ok; the decision
  log wrote 18,331 rows in 9.4 s. The same 17,161 decisions computed with the old and the new scrubber (write off):
  7.9 and 9.8 s before the fix, 11.0 and 12.6 s after. Word keys now scrubbed as free text cost about 3 s, once, on the
  first wired run (later runs write about 77 rows).
- The store's own values under word keys, old rule against new: signal (48 values), status (2), work_state (5),
  ema_outcome (18), category (8), payer_group (100), source (2) all unchanged; 2 of 11 `how` texts changed, both
  "rebilled to <payer>" with the payer's words masked (the payer stays in the same evidence under `payer`).
- An outage over the real mirror (1,423 downloads, one run each, as mirror() calls them): each run made 7 calls (4, then
  3 plain tries) and waited 40 s once, and counted one failed run; the alert came at the third run; a run in which the
  service came back held 2 files, downloaded the next 58 and cleared the alert.
- A killed run (a server marker whose process is gone, stopped in detect): found, recorded as killed in detect, its record
  holds steps load and detect and 4 trail events, and the bundle has it. A Production file cut short: held by the
  reader, swept into the registry (one alert), discarded on the catalog, swept again: alert cleared, one 'released' row.
- 150 MB of a real report's buffers in a contained error: 0 MB still held after `runlog.caught` and after a contained
  step (before the fix the run log kept the exception, its frames and the buffers until the run ended). A store that
  cannot be opened: `record_early_failure` wrote a failed run and the panel said "failed".
- The bundle after those faults: 14 runs (11 ok, 2 killed, 1 partial), 37 parts, 44 KB, 2.3 s; masked_on_second_pass 0,
  left_out 0; 70 event lines, every one a JSON object. Scan of every byte against the store's 10,849 name words and
  399,653 identifiers: no name; one identifier-shaped token, a word of the code's own (as before). In the store's
  item_decisions, two coincidences of the store's own keys (no free text): 3-letter runs of hex equal to 7 learned short
  names, and one transaction-set control number segment equal to one member id (as before).

## 9d. The scrubber's name rules, tightened by the fixer last (2026-11-23, 02:35-03:20 UTC; no hook needed)

A check on the eg2 store's real names (34,420 distinct patient and subscriber names, each written into an invented log
line three ways; counts only) showed what the scrubber left visible if a name ever reached a log line: in the 835's own
form "LAST, FIRST" and its segment form "LAST*FIRST", 1,177 names kept a word visible (7 wholly), almost all a first or
last name that is also a word of RemitWatch's own code (the tests use invented ones: Mark, Will, Rose, Price; the
scrubber keeps the code's words readable) written in capitals beside a masked name; in "First Last", 14 (7 wholly). runlog.Scrubber now:
- masks a capitalized two-letter word beside a masked name (An, My, He, No: given names and surnames the keep list
  leaves out of the names learned);
- masks a word of the code's own beside a name the store holds even in capitals; only the log's level words stay
  (ERROR, WARNING, INFO, DEBUG, CRITICAL);
- learns a name made only of the code's words as its pairs of words side by side (15 pairs on the real store) and masks
  them where they stand together with capitals;
- reads `*` (the 835's separator), a middle initial and a name's suffix (J, J., JR, SR, II, IV) as part of one name,
  never a two-letter word ("Blue Shield Of Texas" stays readable).
After: 0 names with any word visible in all three forms (34,420 checked). The words learned are the same 10,848 as
before (learn now reads each pair of name columns once: 0.68 s before, 0.93 s after on the busy machine). Payer names:
the same 215 of 227 stay whole under `payer_group`; as free text 12 of 227 read differently, all 12 already partly masked
before (a payer word that is also a patient's name). Typical log lines with no name cost the same as before. Tests:
test_a_two_letter_word_beside_a_masked_name_goes_with_it, test_a_word_of_the_codes_own_in_a_name_the_store_holds_is_masked_in_every_form,
test_a_name_with_one_part_missing_is_still_learned (tests/test_runlog.py), each broken and restored.

## 10. For unit F (docs)

README.md layout: remitwatch/runlog.py (run manifests, event log, decision log, scrubber, log retention), repair.py
(self-repair registry), diagnose.py (diagnostic bundle), api_ops.py; the cli.py line gains `diagnose`. OPERATIONS.md:
where the logs live (server /data/logs, 180 days; Vercel logs/ in the state folder, 90 days; one line per run in
Vercel's function log), the Data tab's Logs and diagnostics panel, the bundle (Data tab button, `python -m
remitwatch.cli diagnose`), what the self-repair does by itself and the SELF_REPAIR_FAILED alert. ARCHITECTURE.md:
the decision log table item_decisions and why it is apart from finding_events. Link CLAUDE.md and
docs/TROUBLESHOOTING.md from README. CLAUDE.md and TROUBLESHOOTING.md describe the wired state (this file's hooks);
re-read both after 3b and fix anything the hook-in did differently. No new setting to document.

## 11. Open questions for the owner or the orchestrator

- The bundle route is open to any signed-in user (no patient data inside; the alert tells the biller to download it).
  Owner-gated instead is one dependency change (require_owner) if preferred.
- Retention numbers (180/90 days, 512/100 MB, decisions 400 days) are constants, not settings; say if they should be
  settings.
- (Fixer) A refused report (the wrong file, columns missing) is no longer a self-repair hold, so it raises no
  SELF_REPAIR_FAILED: the Data tab and the Upload page say why, and EMA_STALE covers a report kind that stops arriving.
  Say if a refused report should raise an alert of its own (that would be alerts.py's, unit B's).
- (Fixer) Two-letter surnames the store holds (Le, Ng, Wu, Do...) are masked in logs wherever they are written with a
  capital, so a log line "Do not ..." loses its first word when a patient is named Do. Privacy first; English words
  (no, to, in, an, ...) and RemitWatch's own codes (CO, PR, OA, PI, CR, AR, OK, ID) are never learned as names.
