# needs_G.md: wiring remitwatch/selfcheck.py (unit G, phase 3a, after the fixer) -> the phase-3b hook-in agent, units C, D/INT, R, E, H, GUARD

Status: FINAL for phase 3a, revised by G's fixer after the independent review (section 14 lists every review finding and
what happened to it). Unit G owns only remitwatch/selfcheck.py and tests/test_selfcheck.py (156 test instances, all
invented data, file store + compact store + the compact store's serialized image). Nothing below has been applied; every
item names the file, the function, the exact code, where it goes (by the line it follows: other units are editing these
files, so line numbers would be stale), and the test that proves it once wired. No identifiers anywhere below.

Plain rules only, no AI. Every G function takes its context explicitly (conn, run_id, mode, today) and returns what it did;
the clock is read only through selfcheck.today_of()/now_of() (REMITWATCH_TODAY drives simulated days) and the environment
only through selfcheck.setting(). G logs through remitwatch.runlog (runlog.event / runlog.caught), never its own logger.

What changed in the fixer's round, for whoever wires it (each is in the sections below):
- begin_detection's context now carries `events_max` (the newest finding_events id): pass the same ctx to after_detection.
- A held run puts back every status a person saved while the run ran, and still lists the run's new items to work
  (up to NEW_WORK_FLOOD; a flood is held with the run).
- The vanished check now also counts items detect.save keeps only as stale rows (a person worked them, no rule produces
  them), keeps vanished items on the first run too, accepts a replacement only from an item THIS run made, and excuses an
  item whose own claim got new facts since the run before (selfcheck_facts).
- accept_findings accepts exactly the offenders of the run the person looked at (selfcheck_offenders), and takes
  `run_id=` (refuses when the checks ran again since). The Data tab lists every offender with selfcheck.offenders().
- plan_next counts runs the platform killed (runlog's open markers, read from the run log in progress, or `killed=`);
  the serverless memory budget is 2,188 MB (its 80% mark is release gate 3's 1.75 GB), and an optional step is never left
  for a calm run more than MAX_DEFER_DAYS (7): record_did(conn, {..., "ran": [...]}) ends a deferral.
- New critical check posted_money (check_posted): money posted in a closed month never leaves the ledger. check_ledger
  calls it; on Vercel it must also run right after each Production file is applied (section 3d).
- line_balance compares EMA's A/R with the postings both ways (each A/R line by the money it holds open: a denied line,
  which EMA lists with $0 in Current A/R, by its Denial Amount), and lines an earlier A/R listed that the newest no longer
  does (ema_ar_items.absent_since) while their postings leave money open. check_ledger's result has `gaps` (every dollar
  the checks could not tie, by kind, with a total).
- Found on the real world in the fixer's own run (both fixed, with failing tests first): an item kept from a store that
  keeps no states yet (the live store before this release: the first wired run) had no state, so it was on no list and the
  next run dropped it with no trace; it now gets the state every list already shows for it (Work now; Sent: Waiting), and
  an open item with no state counts as listed. And line_balance read a denied line's $0 Current A/R as nothing owed (545 lines,
  $192,106.30 shown as gaps that were not); it now reads the Denial Amount.
- self_audit takes no ledger any more (it never read it): drop `ledger=` from the calls below.
- No dollar floor (the owner's decision 9): AUDIT_GAPS on any new gap, GIVEN_UP_RISING on any rise. The settings
  REMITWATCH_AUDIT_GAP_CENTS and the $500 constants are gone.

## 0. Columns (remitwatch/detect.py, EXTRA_FINDING_COLS)

Add four columns (the first three are the fixed interface of brief_3a; the fourth lets the self-check and H's decision log
name the state rule instead of reading the basis text):

```python
    # the worklist's ranking (remitwatch.selfcheck: the item's dollars x its pattern's recovery rate, with the basis)
    "expected_value_cents": "INTEGER", "recovery_rate": "REAL", "recovery_basis": "TEXT",
    # the rule states.py decided the state by (states_of()[fid]['rule']: open, credit_refund, follow_up_due, ...)
    "state_rule": "TEXT",
```

And in `Detector._set_states` (detect.py), after the line `r["appeal_block"] = st.get("appeal_block")`, add:

```python
            r["state_rule"] = st.get("rule")
```

`api.FINDING_COLS` (unit C1) gets the same four. The worklist sorts by `COALESCE(expected_value_cents, shortfall_cents) DESC,
appeal_deadline` (brief_3a). Proving test once wired: tests/test_selfcheck.py::test_apply_expected_values_writes_the_ranking_columns
(it adds the columns itself today; after wiring it must still pass) and a new detect test that a saved item carries state_rule.

## 1. Detection (remitwatch/detect.py, Detector.run): the self-check around the save, both hosts

`Detector.run` serves both hosts (pipeline.run_detection on the server, cloud.run_cloud_cycle on Vercel), so one pair of
hooks covers both. Replace the first lines of `run()` and its tail:

At the very start of `Detector.run` (before `try: if self.conn.execute("SELECT 1 FROM ema_rows LIMIT 1")`):

```python
        from . import selfcheck as _sc
        # (G) the worklist as people see it now, for safe mode, the vanished check and the track record
        self._selfcheck_ctx = _sc.begin_detection(self.conn, run_id=self.run_id, today=self.today)
```

(Placing it right before `out = self.save()` instead is also correct and narrows the window in which a person's change
made on the dashboard is not in the snapshot; either way after_detection puts back every person's change logged after
the snapshot, from ctx['events_max'], so the placement is not load-bearing.)

At the end of `Detector.run`, replace `self.hook_hide()` / `return out` with:

```python
        self.hook_hide()
        from . import runtime, selfcheck as _sc
        try:        # (G) each item's dollars x its pattern's recovery rate, from the 835 archive (one bulk update)
            self.value_summary = _sc.apply_expected_values(self.conn, _sc.recovery_rates(self.conn))
        except Exception as exc:       # noqa: BLE001  (the ranking waits for the next run; the items stand)
            from . import runlog
            runlog.caught(exc, "selfcheck:values")
        # (G) the self-check on what was saved: invariants, items that vanished kept, safe mode, track record, history
        self.selfcheck = _sc.after_detection(self.conn, self._selfcheck_ctx, mode="serverless" if runtime.serverless() else "server",
                                             today=self.today, run_id=self.run_id)
        return out
```

Order with unit H's decision log: H's `before = runlog.snapshot_items(conn)` goes beside `begin_detection` and
`runlog.record_decisions(conn, run_id, before)` goes AFTER `after_detection` (so a held run logs no change people never saw,
and the values update is logged as rule selfcheck:recovery_rate, which runlog already names).

What after_detection does (selfcheck.after_detection): runs the item checks (state_valid, followup_date, open_and_closed,
closure_proof, facility_twin, precision_guard, past_window, deadline_basis, counts_lists, feed_835, feed_ema), the vanished
check against the snapshot, and counts the stored money checks (section 3: ledger_store, money_conservation, line_balance,
posted_money) and the dashboard copy check (section 4). A critical check with NEW offenders beyond its tolerance (0, the
precision guard 5; for a money check a known cell whose gap grew by more than $1 is new too) holds the run: findings go back
to the snapshot (the last good worklist), every status a person saved while the run ran is put back on top (from the
'manual', 'hidden' and 'unhidden' history entries written after the snapshot), the run's new items to work that no failing
check names are listed as well (at most NEW_WORK_FLOOD = 20 items or 10% of the list; more is held with the run), this
run's finding_events move into selfcheck_held (compressed, with this run's items), progress_snapshots of the run are rebuilt
from what people see, meta selfcheck:safe_mode is on. The next run that passes releases it. The first run of the self-check
only sets the baseline for the hold. Items that vanished with no reason (no closure, no person's decision, no item this run
made for their line, claim or service, not folded, not superseded, no new facts on their own claim since the run before) are
put back, or listed again when detection kept them only as stale rows, with a note (KEPT_HINT) and a finding_events row kind
'kept_by_selfcheck', on every run, the first included, until a person accepts the change on the Data tab (section 6).

Proving tests once wired: tests/test_selfcheck.py::test_the_real_detector_with_a_dropped_rule_keeps_its_items and
test_a_dropped_rule_keeps_the_items_people_worked_on_the_list and test_safe_mode_keeps_a_change_a_person_made_while_detection_ran
(today they call begin/after around the real Detector themselves; once wired, remove those two calls from the tests:
Detector.run alone must keep the items and the person's change), test_a_critical_failure_holds_the_run_and_the_next_good_run_releases_it,
test_the_first_run_sets_the_baseline_and_holds_nothing, test_the_first_run_keeps_vanished_items_too,
test_a_held_run_still_lists_the_new_items_it_found. Add to tests/test_cloud.py (both stores): a cycle whose detection is
monkeypatched to close an item without proof the second time leaves meta selfcheck:safe_mode on and the serving copy shows
the item open.

THE FIRST WIRED RUN (owners: B/INT, before the release): on the real world the first run with the self-check finds the
items push 2's rules no longer produce and KEEPS them (section 8 has the counts). They stay listed, marked "Kept by
RemitWatch's self-check", until the owner accepts the change on the Data tab. B/INT should find out before the release which
rules stopped producing them (section 15 lists them by signal with example finding ids): either a rule is wrong (fix it) or
the change is intended (the owner accepts it once, on the Data tab, with the list in front of them). The live store keeps
no states before this release: a kept item gets the state every list already shows for it (Work now; Sent: Waiting), so
it is on Work now and the next run keeps it again (section 15 A1).

## 2. The server cycle (remitwatch/pipeline.py, run_cycle)

a) Right after `token = acquire_lock(store.conn)` / its `if not token:` block, and AFTER unit H's `runlog.start_run(...)`
(plan_next reads the runs the run log found killed: their open markers):

```python
    from . import selfcheck as sc
    t_cycle = time.time()
    plan = sc.plan_next(store.conn, _meta_json_or_none(store.conn, LAST_KEY), mode="server")   # LAST_KEY's JSON value
    out["plan"] = {k: plan[k] for k in ("level", "name", "skip", "overdue")}
    if plan["actions"]:
        out["self_regulation"] = plan["actions"]
```
(`_meta_json_or_none`: `json.loads(_meta_get(conn, LAST_KEY) or "null")` inside a try; the cycle already writes LAST_KEY.)
Optional second evidence of a killed cycle: read `_lock_owner(store.conn)` BEFORE `acquire_lock`; when a lock was there and
acquire_lock took it over (its heartbeat was stale), pass `killed={"started_at": held["started"], "last_step": None}` to
plan_next (runlog's marker is the main evidence; a run killed before its first marker leaves only the lock).

b) After `out["ingest"] = ingested`: `sc.mark(out, "ingest", t_cycle)`.

c) After the ledger refresh block (after `r = _ema_step(out, "ema_refresh", ema_ledger.refresh, store, ledger(), log=beat_log)`
and its `if r ...:` lines, still inside `if lg_box or os.path.exists(...)`):

```python
            money = _ema_step(out, "selfcheck_money", sc.check_ledger, store.conn, ledger())
            if money:
                out["money_checks"] = {c["id"]: c["status"] for c in money.get("checks") or []}
                out["money_gaps"] = money.get("gaps")
```
(check_ledger runs check_posted too: posted money is measured every cycle, so a newer export that drops a posting is seen
the cycle it is applied.)

d) After `out["detect"] = {"findings": sum(summary.values())}`: `sc.mark(out, "detect", t_det)` (with `t_det = time.time()`
taken just before `run_detection`), and when `plan["skip"]` holds "activity", pass it on: run_detection's provider-table
refresh is left for a calm cycle (`run_detection(db_path, skip_activity="activity" in plan["skip"])`, one new keyword in
detect.run_detection: `if not skip_activity: refresh(store, ...)`).

e) Right BEFORE `out["alerts"] = run_alerts(store.conn)`:

```python
        today = sc.today_of()
        for step, fn in (("selfcheck_track", lambda: sc.track_record(store.conn, today=today)),
                         ("selfcheck_given_up", lambda: sc.given_up(store.conn, today=today)),
                         ("selfcheck_blind", lambda: sc.blindness(store.conn, today=today))):
            _ema_step(out, step, fn)
        ran = [] if "activity" in plan["skip"] else ["activity"]
        if sc.audit_due(store.conn, today) and "audit" not in plan["skip"]:
            audit = _ema_step(out, "selfcheck_audit", sc.self_audit, store.conn, today=today)
            if audit and not audit.get("skipped"):
                out["self_audit"] = {k: audit.get(k) for k in ("gaps_n", "gaps_cents", "wasted_n", "alert")}
                ran.append("audit")
        out["housekeeping"] = sc.housekeeping(mode="server", db_path=db_path, conn=store.conn,
                                              archive_result=out.get("archive_compact"))
        sc.record_did(store.conn, {"shed": plan["skip"], "level": plan["level"], "ran": ran})
        store.conn.commit()
```
(`out["archive_compact"]`: wherever unit E's hook keeps what `archive.compact(conn)` returned this cycle; use E's key if
needs_E names another. housekeeping never compacts by itself. Add "report" / "backfill" to `ran` where the cycle sends the
weekly report or runs the backfill: an optional step's deferral ends when it has run.)

f) Unit E's archive hook (needs_E) passes `budget_state=sc.archive_budget_state(plan)` to `archive.run(...)`.

Proving tests: tests/test_selfcheck.py::test_a_run_past_80_percent_makes_the_next_run_spread_then_archive_then_shed_then_alert,
test_vercels_normal_runs_are_calm, test_the_weekly_steps_are_never_left_for_more_than_a_week,
test_a_run_the_platform_killed_counts_as_pressure (a subprocess run dies after its runlog marker; the next run's plan is level
1), plus a new tests/test_sync.py case: run_cycle twice with `REMITWATCH_MEMORY_BUDGET_MB=1` (any run passes 80%): the second
cycle's out["plan"]["level"] is 1 and meta selfcheck:regulate holds the cycle; with level 3 out has no "activity".

## 3. The serverless cycle (remitwatch/cloud.py)

a) run_cloud_cycle, right after `mem = {"load": peak_mb()}` / `out["memory_mb"] = mem`, and after unit H's
`runlog.start_run(...)` (as on the server):

```python
        from . import selfcheck as sc
        try:
            prev_status = json.loads(state.get(STATUS) or b"{}")
        except Exception:       # noqa: BLE001  (no previous status: a first run plans calm)
            prev_status = {}
        plan = sc.plan_next(conn, prev_status, mode="serverless", budget_s=budget)
        out["plan"] = {k: plan[k] for k in ("level", "name", "skip", "overdue")}
        if plan["actions"]:
            out["self_regulation"] = plan["actions"]
        sc.mark(out, "load")
```
status.json is written only in run_cloud_cycle's finally block: a run killed for memory leaves the previous status, so the
killed run is known only from runlog's open marker (start_run finds it) or from the lock cloud.acquire took over (pass
`killed={"started_at": <the lock body's started>}` when acquire takes over a stale lock: one more return value from acquire).

b) Spread (level >= 1): in the detection block, change the hand-over condition
`if ingested and (left_s < estimate * 1.5 + 45 or n_ingested >= DEFER_DETECT_AFTER_FILES):` to
`if ingested and (plan["spread"] or left_s < estimate * 1.5 + 45 or n_ingested >= DEFER_DETECT_AFTER_FILES):`
(ProductionSlot already reads one Production file per run; with spread, any run that ingested hands detection to a fresh
invocation, more=True). After `mem["ingest"] = peak_mb()` add `sc.mark(out, "ingest")`; after `mem["detect"] = peak_mb()`
add `sc.mark(out, "detect")`.

c) Shed (level >= 3, and never for more than 7 days for one step: plan["skip"] already leaves out an overdue step): the
provider tables condition becomes
`if has_lines and not remaining and "deferred" not in out and activity.stale(conn) and "activity" not in plan["skip"] and budget - (time.time() - t0) > 45:`;
the report block's `if due and conn.execute(...)` gains `and "report" not in plan["skip"]`; the backfill gains
`and "backfill" not in plan["skip"]`. Record what was shed and what ran: `out["shed"] = list(plan["skip"])` and, at the end,
`sc.record_did(conn, {"shed": plan["skip"], "level": plan["level"], "ran": ran})` with `ran` the optional steps this
invocation did run ("activity" when the provider tables were rebuilt, "report" when the report was sent, "backfill",
"audit" when the self-audit ran).

d) The money checks on Vercel need the ledger AND the store summaries of the same revision. The ledger is released inside
ema_ledger.refresh, so they run at the start of an apply, before the new file: in `ProductionSlot.apply`, right after
`self.seq0 = lg.seq()`:

```python
        from . import selfcheck as sc
        try:
            sc.check_ledger(conn, lg)           # the store's summaries and the ledger, both as the last apply left them
        except Exception as exc:               # noqa: BLE001  (measured next apply; detection is not held for it)
            from . import runlog
            runlog.caught(exc, "selfcheck:money")
```
and, so a newer export that drops postings is seen in the run that applies it (not a week later at the next apply), right
after the new file is committed to the ledger and BEFORE ema_ledger.refresh releases it (in ProductionSlot.apply, after the
`ema_reports.ingest_file(...)` / ledger commit call):

```python
        try:
            sc.check_posted(conn, lg)          # money posted in a closed month never leaves the ledger (critical)
        except Exception as exc:               # noqa: BLE001
            from . import runlog
            runlog.caught(exc, "selfcheck:posted")
```
Cost on the real ledger: check_ledger 4.6-6 s, +50 MB; check_posted alone about 1 s (one grouped pass).

e) Before `out["alerts"] = run_alerts(conn)` (inside `if settled:`), the same block as the server's section 2e, with
`sc.housekeeping(mode="serverless", state=state, conn=conn, archive_result=out.get("archive_compact"))`, and the self-audit
only when this invocation did NOT run detection (`not ran_detect`, so the audit's temporary tables never meet detection's
peak) and "audit" not in plan["skip"]. The audit reads the store's own lean EMA lines on both hosts (the keep rule keeps every
line its rules look at); it takes no ledger.

f) The dashboard copy (cloud.save_serving): after `size = build_serving(conn, path)` and before the file is read:

```python
    from . import selfcheck as sc
    try:
        copy = sqlite3.connect(path)
        try:
            sc.check_serving(conn, copy)       # kept in meta selfcheck:serving; the next run's history shows it
        finally:
            copy.close()
    except Exception as exc:                   # noqa: BLE001
        from . import runlog
        runlog.caught(exc, "selfcheck:serving")
```
Unit H's repair registry (repair.rebuild_if_failing("serving_copy", ...)) rebuilds the copy once when it fails.

g) cloud.SERVING_TABLES: add `("selfcheck_audit_items", None, None)` (the self-audit's gap and wasted lists, last 3 audits
only; keys are bills, dates and codes, like the claims the copy already holds) and `("selfcheck_offenders", None, None)`
(the latest run's offenders of the failing critical checks: finding ids, month cells, EMA line keys; what the Data tab
lists behind a red check and what a person accepts) so the Data tab can list them on Vercel. Every other panel figure is
in meta, which the copy carries.

g2) Journal requests from the Data tab on Vercel: in `cloud.apply_actions` (or actions.apply, unit B/C1), an entry
`{"kind": "selfcheck_request", "what": "audit" | "accept", "checks": [...], "run_id": <the run the page showed>, "user": ...}`
calls `selfcheck.apply_request(conn, action)`. The run_id matters: the entry is applied at the start of the next invocation,
and an acceptance meant for one run must not accept a later run's offenders nobody has seen (it then accepts nothing and
says so).

Proving tests: tests/test_cloud.py (both stores): with a fake previous status.json over 80% of memory (peak 1,800 MB), the
next run's out["plan"]["level"] == 1 and its detection is deferred after an ingest; a status at level 3 skips activity;
test_selfcheck.py::test_the_dashboard_copy_is_compared_with_the_store; a Production apply on a ledger whose newer export
drops a closed month's payment leaves meta selfcheck:posted failing (test_selfcheck.py::
test_a_payment_a_newer_export_of_the_same_box_drops_is_a_visible_gap shows the module side).

## 4. Alerts (remitwatch/alerts.py, AlertEngine)

Add one method and call it LAST in `evaluate()` (after `self.ema_rule()`), so `self.active` already holds RAMP, RISING,
NEW_BEHAVIOR, RESUMED and NEW_REASON_CODE (the pattern watch does not duplicate a payer they cover):

```python
    def selfcheck_rule(self) -> None:
        """The self-check's alerts (remitwatch.selfcheck.alert_candidates): safe mode, failing checks and checks that could
        not run, near the limits, the weekly self-audit, the track record, money given up without a decision, and the
        pattern watch."""
        try:
            from . import selfcheck
            cands = selfcheck.alert_candidates(self.conn, today=self.today, active=self.active)
        except Exception as exc:       # noqa: BLE001  (logged; the other alerts stand)
            from . import runlog
            runlog.caught(exc, "alerts:selfcheck")
            return
        for a in cands:
            self._raise(a["kind"], a.get("severity") or "medium", a["title"], a.get("detail") or {}, a.get("payer_group"),
                        "selfcheck", None, None, None, a.get("n"), a.get("cents"), key=tuple(a["key"]))
```
Extend, not duplicate, RAMP and RISING: where `ramp_rules` raises RAMP for the 'denial' or 'bundling' family, add
`detail["breakdown"] = selfcheck.pattern_breakdown(self.conn, p, asof=self.asof)` (which code x reason drives it).
Kinds added: SAFE_MODE, CHECK_FAILED (a critical check failing, or one that could not run: the guard it gives is off),
NEAR_LIMIT, AUDIT_GAPS (any new coverage gap: no dollar floor), WASTED_WORK, TRACK_RECORD, GIVEN_UP_RISING (any rise),
DENIAL_PATTERN, CREDENTIALING, CLINICIAN_BELOW_GROUP, BATCH_REPROCESSED, PATTERN_ERROR (add them to the alerts.py docstring
list). REMITS_MISSING stays ema_checks' (not duplicated). Proving tests: a new test in tests/test_selfcheck.py or
test_remitwatch.py: run_alerts on a store in safe mode has a SAFE_MODE row; a second run_alerts after the release clears it;
test_selfcheck.py::test_a_critical_check_that_crashes_raises_an_alert_and_a_health_item and
test_any_new_coverage_gap_and_any_rise_in_money_given_up_raise_their_alerts show the candidates.

## 5. Data health and the banner (remitwatch/insights.py, api.py)

insights.data_health, before `items += ema_health(conn, today)`:

```python
    try:
        from . import selfcheck
        items = selfcheck.data_health(conn, today) + items       # blindness and safe mode first
    except (sqlite3.Error, ImportError, KeyError, TypeError, ValueError):
        pass
```
Every page's banner (unit C1/C2): `/api/meta` (or the brief) returns `selfcheck.banner(conn)` ({level, text} or None);
the pages show it above everything and never show "all clear" while it is not None. Both read meta only, so they work on
the dashboard's copy. Proving test: test_selfcheck.py::test_the_panel_and_data_health_read_only_meta_so_the_copy_shows_the_same
and test_the_banner_and_the_data_tab_requests.

Unit C2's blind spot (api_ema.blind_spot, GET /api/ema/blind, Today, the Data tab) already prefers meta `selfcheck:blind` when
it is stored: G stores the fields needs_C2 section 4 asks for, beside its own: `{at, today, blind, feeds: [{feed: "835" |
"production" | "ar", label, since, days, stale}], cents (charges billed to insurance since the newest remittance with no
payer's answer on file, line by line from the store's EMA lines, leaving out each line whose answer EMA posted; plus the
items to work once when an EMA report is old), basis, lines, since, sources, banner}`. No change in api_ema.py or today.js;
the run only has to call `selfcheck.blindness` (section 2e/3e). Proving tests: test_blindness_says_what_the_app_cannot_see_in_dollars,
test_blind_dollars_leave_out_claims_ema_shows_answered, test_stale_ema_reports_make_the_app_say_what_it_cannot_see.

## 6. The Data tab panel and routes (new remitwatch/api_ops.py, static/ops.js; phase 3b)

- `GET /api/ops/selfcheck` -> `selfcheck.panel(conn)` ("Running on its own": lines, regulation, safe_mode, checks with
  history (last 14 runs), audit (latest + weekly matrix), track, patterns, money (with `posted` and `gaps`: every dollar the
  money checks could not tie, by kind), given_up, blind, housekeeping, rates, acceptances (who accepted what, when)).
- `GET /api/ops/selfcheck/offenders/{check}` -> `selfcheck.offenders(conn, check)` (every offender of the latest run for one
  failing critical check, largest first, each with `accepted`; for vanished these are finding ids the page links to). The
  page shows this list before a person accepts, and the latest run's id (`panel(conn)['last_check']['run_id']`).
- `GET /api/ops/selfcheck/audit/{list}` (list = gap | wasted) -> `selfcheck.audit_lists(conn, list)` (a gap carries the
  finding id of the item RemitWatch closed, expired or set aside by itself when there is one, else none: its key is
  bill|date|code, a check's transaction key for an unposted check; show bill and date only with the names switch on).
- `POST /api/ops/selfcheck/audit` (signed in) -> server: `selfcheck.apply_request(conn, {"what": "audit"})`; Vercel: journal
  `{"kind": "selfcheck_request", "what": "audit"}` (section 3g2).
- `POST /api/ops/selfcheck/accept` {checks: [...], run_id} (owner-gated: a business decision) -> server:
  `selfcheck.accept_findings(conn, user, checks, run_id=run_id)`; Vercel: journal
  `{"kind": "selfcheck_request", "what": "accept", "checks": [...], "run_id": ..., "user": ...}`. The answer has `accepted`
  ({check: n}) or `stale: True` with a sentence to show ("The checks ran again after this page was opened ...").
Page wording is already plain English with no em dashes (tested).

## 7. Housekeeping (section 2e/3e above)

`selfcheck.housekeeping(mode=..., db_path=... | state=..., conn=..., archive_result=...)` calls `uploads.ServerUploads(db_path).sweep()` /
`uploads.state_uploads(state).sweep()` (pieces left a day), `ema_ledger.clean_work_files(folder)`, and removes kept upload
copies only when the catalog shows the report replaced, superseded or discarded, or the upload was 'already', after 30 days
(the sidecar stays; never the newest current file of a report, never a held or waiting one). It does NOT call
`archive.compact` (VACUUM needs the store's size again; unit E places it before detection): it records E's result in meta
selfcheck:housekeeping when the cycle passes it as `archive_result=`. Proving test:
tests/test_selfcheck.py::test_housekeeping_runs_each_cleanup_and_keeps_what_a_person_may_need (it fails if housekeeping calls
archive.compact).

## 8. Unit D / INT / B (states.py, story.py, detect.py): real bugs the checks found on the real world

1. `story.next_step(...)['expected_cents']` and its basis: read the item's stored `expected_value_cents` and
   `recovery_basis` (fall back to states.recovery_of when the columns are NULL), so the drawer and the worklist agree.
2. Optional: `states.response_days` may take the larger of the archive's p90 and RemitWatch's own measured waits from meta
   `selfcheck:answer_days` ({payer group: {p90, p50, n}}, only groups with n >= 20).
3. Real bugs found by the checks on the integrated world (the fixer's re-run, 2026-11-23, current code of every unit): see
   the section "REAL-DATA FINDINGS (fixer)" at the end of this file (counts, dollars, example finding ids, owners).

## 9. Unit R (results.py)

Use `selfcheck.given_up(conn)` (meta selfcheck:given_up: cents, recoverable_cents, lost_cents, by_payer, by_reason,
by_month, weeks[], and now window_from_payer_cents: write-offs whose item has no deadline on file, judged by the payer's own
appeal window from its answer, never 'lost' by default; items_written_off_cents and with_no_item_cents: the ledger's
write-offs at posting with no RemitWatch item at all, counting every item on record whatever its status) for "given up
without a decision", and `selfcheck.scoreboard(conn)` for at risk in 14 days, lost to deadlines by week (track record), the
self-audit's gaps and wasted work by week, and the closure error rate. Both read meta and the findings only (they work on
the dashboard copy).

The two meta keys needs_R section 6 reads are stored under R's names, so results.py needs no change: `selfcheck:given_up`
has `cents`, `n`, `recoverable_cents`, `at` (plus lost_cents, by_payer, by_reason, by_month, weeks); `selfcheck:audit` has
`at`, `coverage` ({lines, listed, held_by_reason, missed, missed_cents, missed_by, share}), `precision` ({work_now,
possible_waste, possible_waste_cents, share}) and `matrix` at the top (plus `latest` and `weeks`). The self-audit now
counts a line RemitWatch closed, expired or set aside BY ITSELF as missed (coverage.missed_by names how: no_item,
closed_by_remitwatch, done, expired, cant_check), and only a person's or the owner's decision as held_by_reason, so coverage
falls on the real world (section at the end). Proving tests: test_given_up_counts_write_offs_at_posting_with_no_decision_or_policy,
test_the_self_audit_lists_gaps_covered_lines_and_possible_wasted_work, test_the_audit_counts_lines_remitwatch_closed_or_expired_on_its_own_as_missed.

## 10. Unit E (archive.py)

`archive.run(conn, ..., budget_state=selfcheck.archive_budget_state(plan))` (section 2f): at level >= 2 the share is raised to
the 80% mark so `effective_horizon` tightens by its own step, never below its floor; otherwise the measured share (so a calm
run relaxes it). The serverless budget is now 2,188 MB (its 80% mark is release gate 3's 1.75 GB), so Vercel's normal runs
(1.3-1.5 GB) no longer tighten the horizon. E's `archive.compact` result is recorded by housekeeping when the cycle passes
it as `archive_result=` (section 2e); housekeeping never compacts by itself.

## 11. Unit H (runlog, repair, diagnose)

- The run manifest: `rl.set("invariants", (selfcheck_result or {}).get("checks"))` and `rl.set("self_regulation", plan)`
  (runlog.set already names both sections).
- runlog.start_run must run BEFORE selfcheck.plan_next in both cycles: plan_next reads
  `runlog.current().manifest['sections']['killed_runs']` (the open markers of runs the platform killed) and counts each once
  as a pressured run (it remembers them in meta selfcheck:regulate killed_seen). Without a run log in progress it counts only
  what `killed=` hands it.
- The decision log (runlog.record_decisions, after after_detection): an item the self-check kept this run (a
  'kept_by_selfcheck' finding_events row with this run_id) is unchanged or listed again by the self-check, not by a rule:
  name its change `selfcheck:kept_vanished`. On the first wired run a kept item also gets its state from the self-check
  (Work now, or Waiting when Sent): today record_decisions would file that as `states:work_now`.
- repair: `repair.note_pressure(registry, [{"action": a} for a in plan["actions"]], reading=plan["pressure"])` each run
  that plans level >= 1.
- diagnose already reads selfcheck_* tables and meta selfcheck:safe_mode / selfcheck:last; it can add
  `selfcheck.held_copy(conn)` (the held run's items and events) as a part (diagnose.register_part) to show what a held run
  would have done. BUG for H: diagnose._selfcheck_tables reads each table with `ORDER BY rowid`, which fails on WITHOUT ROWID
  tables (selfcheck_baseline, selfcheck_audit_items, selfcheck_track, selfcheck_money, selfcheck_offenders, selfcheck_facts,
  selfcheck_posted all are): the bundle gets {"error": "OperationalError"} for them. Use `ORDER BY 1 DESC` (or no ORDER BY with
  LIMIT) for WITHOUT ROWID tables. selfcheck_offenders and selfcheck_facts hold finding ids and EMA line keys
  (claim number|date|code): the scrubber's digit rule turns the claim numbers and dates into <n>.

## 12. Docs (unit F)

Name in README layout `remitwatch/selfcheck.py`; in docs: the settings REMITWATCH_MEMORY_BUDGET_MB (serverless 2,188 default:
its 80% mark is release gate 3's 1.75 GB; server: none unless set), REMITWATCH_CYCLE_BUDGET_SECONDS (server, 3600),
REMITWATCH_AUDIT_WASTED_ITEMS (10), REMITWATCH_TODAY (simulated days only); REMITWATCH_AUDIT_GAP_CENTS no longer exists (the
owner's decision 9: any new coverage gap raises the alert). Safe mode in plain words (what it keeps, what it holds, that the
statuses people save during a run stand and new work still shows, how it is released, who can accept a change and that an
acceptance covers exactly the list shown); kept items ("Kept by RemitWatch's self-check": an item the newest analysis no
longer lists with no reason, kept until the owner accepts); the weekly self-audit; the banner; posted money (a newer
EMA export that lacks postings an older one had is a red check until the complete export is sent again or the owner
accepts it).

## 13. GUARD (the swing guard, PLAN.md release gate 9)

The self-check's safe mode is one of the swing guard's parts. What it does today, for the GUARD audit to build on (not to
duplicate): a vanishing (items people were shown leave with no reason) is kept on the worklist every run until a person
accepts; a held run lists the run's new items only up to NEW_WORK_FLOOD = (20 items, 10% of the list), more is held with the
run and said in the safe-mode text; a critical check that cannot run raises CHECK_FAILED and never holds. A rules-version
re-detection (a release) is NOT let through by the self-check: its vanished items are kept until the owner accepts them;
if GUARD's spec says such a run goes through with a notice, the notice is the vanished check's list (offenders()) and the
owner's one acceptance; the thresholds above are GUARD's to set.

## 14. The independent review's findings: what the fixer did

(The findings text handed to the fixer was cut off after its seventh finding; the fixer worked from the reviewer's 19
failing tests in G3a/review/test_review_g.py (35 instances over the store kinds), one per finding, and ported each into
tests/test_selfcheck.py. With the fixes, 29 of the 35 pass; the 6 that still fail do so by design: rv1 x3, rv4b, rv7 x2,
each explained below.)
- rv10 (high) a payment dropped by a newer export is a gap nowhere: FIXED. New critical check posted_money (high-water marks
  per service month x closed posting month x payer group x measure, selfcheck_posted; retention's service months left out;
  skipped while a Production file is being read again); line_balance two-sided with patient money joined first, lines an
  earlier A/R listed that the newest no longer does (the A/R's own history, ema_ar_items.absent_since; a line no A/R ever
  listed is not compared, as the A/R lists open balances only), A/R lines with no posting; check_ledger's `gaps` total. The reviewer's single-check test passes (line_balance);
  a single measurement after both exports cannot see a drop no earlier check recorded, so the wiring measures every cycle and
  every Production apply (sections 2c, 3d).
- rv1 / rv1b (high) worked items made stale, and old rows on the line excusing a vanished item: FIXED (stale rows count;
  only items this run made carry a line on; a person's decision is a route; new facts on the item's own claim is a route).
  The reviewer's rv1 test asserts the items stay stale (the buggy state) before asking for them to be listed; the ported test
  asserts they are listed again with the person's status and notes.
- rv16 (high) the first wired run keeps nothing: FIXED (kept on every run; only the hold needs a baseline).
- rv2 (high) safe mode undoes a person's change made during the run: FIXED (re-applied from the history entries after the
  snapshot; also the hide rules a person made).
- rv3 / rv3b (high) acceptance accepts the next run's offenders: FIXED (exact keys of the run shown; run_id refusal).
- rv4 (high) Vercel's normal runs count as pressure and shed the audit for good: FIXED (2,188 MB budget, 80% = 1.75 GB;
  MAX_DEFER_DAYS = 7 for every optional step). The 3-calm-runs step-down stays (it keeps the level from flapping); the
  maximum deferral is what guarantees the weekly steps run.
- rv4b (high) a killed run is never counted: FIXED through runlog's open markers (or `killed=`); the reviewer's test reads
  only a lock in the state folder, which plan_next cannot see; the ported test kills a real subprocess run.
- rv5 (high) automatic states filed under 'held by a reason': FIXED (missed, with missed_by and the item's id in the gap list).
- rv6 a held run withholds new items: FIXED (listed unless a failing check names them, or a flood).
- rv7 the audit never reads the ledger it is given: FIXED by removing the parameter (the store's lean lines hold every line
  its rules need; a test proves an auto write-off with no item is found without a ledger). The reviewer's test expects the
  ledger to be read and so still fails by design.
- rv8 proof rows of no money: FIXED (proof_money: dated payments above $0 net of takebacks).
- rv9 no deadline counted as lost: FIXED (payer's window from its answer; unknown dates count as recoverable).
- rv9b let-go items reported as money with no item: FIXED (every item on record, each service once).
- rv11 blind dollars include lines EMA shows answered: FIXED (line by line from the store's EMA lines).
- rv12 a resubmitted service paid later is missed by the precision guard: FIXED (per claim, the largest claim's total).
- rv14 /tmp reading misses SQLite's unlinked temporary files: FIXED (open deleted files under the folder, /proc/self/fd).
- rv15 a critical check that crashes raises nothing: FIXED (CHECK_FAILED 'could not run' and a Data health item; no hold).
- PLAN decision 9 (no dollar floor) in selfcheck.py: DONE (AUDIT_GAPS on any new gap, GIVEN_UP_RISING on any rise).
- Found by the fixer's own real-data run, not by the review (section 15 A): kept items with no state lost a run later, and
  denied lines on the A/R read as settled by line_balance. Both FIXED with failing tests first. A small one too: the
  safe-mode sentence and the CHECK_FAILED alerts lower-cased a check's title ("money posted in ema"), and said "The 1
  change people saved during the run stand"; both FIXED.

## 15. REAL-DATA FINDINGS (fixer, 2026-11-23)

The world: a copy of the real store (the 835 archive) with the EMA exports read again by the current code (A/R as of
2026-11-20, two files, 9,653 rows; Production 2025 and 2026, 928,925 rows), every unit's code as of this morning, server
mode, simulated day 2026-11-22. Deleted after the run. The scripts and their logs (aggregates and finding ids only) are in
wire/G3a/fix (g_fix1 to g_fix5, g_ar_probe).

A. Two bugs in selfcheck.py this run found, both fixed (a failing test first, broken on purpose and restored):
1. Items kept from a store with no states were lost a run later. The world's items before the first wired run had no
   state (like the live store before this release). The first run kept 917 items ($143,274.05) with no state: the lists
   showed them as Work now by their guess, but the self-check did not count them as listed, and the second run dropped
   all 917 with no trace. Now a kept item gets the state every list already shows for it (Work now; Sent: Waiting), and an
   open item with no state counts as listed. Re-run with 900 copies of real open items standing in for items the release
   before made (the states cleared, the self-check's tables emptied): the first run kept all 900 on Work now, the second
   kept the same 900 again, state_valid passed both times (fx3 196.4 s and fx4 197.9 s detection, 2.5 and 2.9 s
   self-check, peak 1,122 MB). Tests: test_an_item_kept_from_a_store_with_no_states_yet_is_listed_and_kept_again,
   test_an_open_item_with_no_state_is_listed_as_the_lists_show_it.
2. line_balance read a denied line's $0 Current A/R as nothing owed: 545 lines ($192,106.30) showed as "the postings leave
   more". EMA lists a denied line with $0 in Current A/R and the money in Denial Amount (ema_reports keeps it as
   exposure_cents). Now each A/R line is compared by the money it holds open. Test:
   test_a_denied_line_the_ar_lists_by_its_denial_amount_agrees_with_its_postings.

B. What the checks say about the real world (final code):
- Money: ledger_store, money_conservation (1,018 cells tie to the cent) and posted_money (7,080 marks, the first
  measurement; posting months closed before 2026-11, services from 2024-01) pass; check_ledger takes 15.6 to 21.5 s and
  under 50 MB. line_balance: 8,013 of the 8,050 A/R lines agree with their postings to the dollar; 37 do not ($17,538.74):
  the A/R holds more on 14 ($8,512.98), the postings leave more on 23 ($9,025.76); median difference $46.32, largest
  $4,098.36. For the biller to compare in EMA (a posting missing from Production, or an A/R pulled before a posting).
  Every dollar the checks could not tie: $335,868.01, of which $318,329.27 is patient money with no claim number that
  matches no single line (shown by month and payer group) and $17,538.74 the 37 lines.
- Lines that left the A/R cannot be measured on this world: it holds one A/R day (no ema_ar_items row has absent_since).
  They will be once a second A/R export is read.
- The first wired run (fx1; the self-check never ran on this store): detection 412.9 s, the self-check 5.1 s, peak 889 MB.
  It kept 917 items ($143,274.05) the current rules no longer make: DENIED_MEDICAL_NECESSITY 307, DENIED_AUTH 270,
  UNDERPAID_VS_OWN_RATE 146, NO_ANSWER 116, CLAIM_DENIED_UNPAID 69, REJECTED_PRE_PAYER 7, DENIED_TIMELY_FILING 1,
  DENIED_DUPLICATE_UNPAID 1; 209 more left by a route (an item this run made for the same line 205, the claim answered 3,
  the same service 1). Examples: 347bb48e20e4224173a6b8b6, 161180bedea98c1b8a6145d1, 5e9281edcbed5f9b6ccf8f55,
  caa20a2a6732931484b1bb93, ec7449a5e74ec2371d7bcb3e, 03bf633b77dccafc29b78d16. Owner B/INT: the world's earlier items
  came from earlier code of the same day and EMA exports read by an earlier reader; find which rule or data change
  dropped them before the release. On a first run no facts are stored yet, so a departure new data caused is counted as
  unexplained too (the facts route compares with the run before's fingerprints).
- The second run on the same data (fx2, detection 232.0 s): nothing new vanished, and precision_guard's 17 known
  offenders were not new.
- precision_guard (critical): 17 items on Work now ($1,406.95) whose service was paid after their remittance:
  DENIED_FREQUENCY 12, CODE_CHANGED 3, EM_BUNDLED_MOD25 1, UNDERPAID_VS_OWN_RATE 1. Three groups checked by hand against
  the raw rows: c0401a932e49a8ba5802bb9e (EMA shows $70.80 paid later on the E/M, on no 835);
  fe7921c901cf6eba7d11aa8c, 0b11178e55a19097e11219de and e19f445753d3a4a8c46641fe (835 payments of $388.33 by another
  payer group, under the code the claim was sent with); b8430a6009c5f6e50c54c98b and 26db285576a6f6e2e93b9991 (later
  835 lines of the same code paid $52.35, $21.37 of it by the same payer). Owner B/D: the paid-later closure misses a
  payment EMA shows with no 835, a payment by another payer group, and a frequency denial paid on a later line. They are
  the first run's baseline, so they do not hold runs; a new one would.
- closure_proof (proof needs money now), past_window, state_valid, followup_date, open_and_closed, facility_twin,
  deadline_basis and counts_lists pass. feed_835 warns: the newest 835 is 44 days old.
- The self-audit (13.8 s): of 3,594 lines its rules say need work, 1,713 are listed (47.7%); 1,881 ($213,894.38) are
  missed: no item at all 1,724 ($187,203.93), closed by RemitWatch on its own 130 ($24,533.26), done 21 ($1,897.00),
  expired 6 ($260.19); none held by a person's decision (the world has none). Possible wasted work: 480 of the 4,023
  Work now items ($56,561.21). Owner B/D: the 130 automatic closures and the 1,724 lines with no item.
- Money given up without a decision: $152,894.19 on 1,398 items (recoverable $100,235.45, lost $52,658.74); every item
  had a deadline on file. EMA's write-offs at posting: $658,041.63, of which $151,445.47 is on RemitWatch items and
  $506,596.16 on lines with no item at all (owner B: rules that miss them, or write-offs that are contractual in fact;
  R shows the figure).
- Blindness: $985,872.36 billed since the newest 835 (44 days) with no 835 and nothing posted in EMA.
- Safe mode on the real world (fx5, final code): a run that closed one item with no proof was held (closure_proof):
  detection 207.4 s, the self-check with the hold 5.8 s, peak 977 MB. The worklist stayed as it was (17,161 items), the
  closure was held (the item is open on Work now again), the status a person saved while the run ran stands (Started),
  and 4 of the run's 5 new items were listed; the fifth, an item about a whole claim (CLAIM_DENIED_UNPAID) whose bill, day
  and code an item on the held worklist already covers, is held with the run as designed. The banner said: "RemitWatch
  kept the worklist as it was before this run and held the run's automatic closures, because this check failed: every
  automatic closure carries its proof. It still lists the 4 new items to work the run found. The 1 change a person saved
  during the run stands." Two small fixes found here (a failing test first for each): a title naming EMA was lower-cased
  into "money posted in ema" in this sentence and in the CHECK_FAILED alerts (titles now keep their capitals; test:
  test_the_banner_and_the_alerts_name_a_check_as_it_is_written), and one change read "The 1 change people saved during
  the run stand" (now singular; test_safe_mode_keeps_a_change_a_person_made_while_detection_ran).
