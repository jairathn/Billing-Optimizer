# needs_R.md: what unit R (Results and ROI) needs from other units

Status: FINAL after the fix round (2026-11-23). Unit R owns remitwatch/results.py, remitwatch/api_results.py,
remitwatch/static/results.js and tests/test_results.py. It edits no other file. Everything below names the file, the
function and the exact change. No identifiers. Sections marked CHANGED differ from the builder's version of this file.

## 1. HOOK (phase 3b), or whoever owns pipeline.py and cloud.py: the run's step on both hosts (REQUIRED; CHANGED: order)

Until this lands, a server's Results page works the figures out itself (once in the request when none exist, then in a
background thread after each newer analysis, showing the stored figures with "updating" meanwhile), and a serverless
page shows only the live counts with "The first run after this update fills them" (it never works money out on the
dashboard copy). The registry of flagged items (results_items) and the saved money of each item (results_money) only
stay current when this runs every cycle.

Server, `remitwatch/pipeline.py`, `run_cycle`, right after `out["alerts"] = run_alerts(store.conn)` (before `notify`):

```python
        try:        # the Results tab's figures, the registry of flagged items, the weekly snapshot (remitwatch.results)
            from . import results
            out["results"] = results.refresh(store.conn, host="server", log=beat_log)
        except Exception as exc:        # the cycle goes on; the page says how old its figures are
            from . import runlog
            runlog.caught(exc, "results.refresh")
            out["results"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
```

Serverless, `remitwatch/cloud.py`, `run_cloud_cycle`, inside `if settled:` right after `out["alerts"] = run_alerts(conn)` (and
the `notified_at` update), before `alerts_changed = ...`:

```python
            try:    # figures, registry and snapshot; the page's spot checks and the owner's figures come in from the state folder
                from . import results
                out["results"] = results.refresh(conn, host="serverless", state=state, log=log)
            except Exception as exc:
                from . import runlog
                runlog.caught(exc, "results.refresh")
                out["results"] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
```

and in the same function add `or bool((out.get("results") or {}).get("changed"))` to the `shown_changed = bool(...)`
expression, so the store and the dashboard copy are saved when the figures changed.

ORDER (NEW): when unit E's archive step is hooked into the same cycles, run it AFTER results.refresh. refresh saves each
item's money and work in results_money; an item whose bill the archive then takes keeps exactly those figures (the
archive deletes the claims, lines, items and history its money and work were worked out from). An archive step that
runs before refresh in a cycle where new money came on a bill it takes would lose that one cycle's change.
ALSO (NEW): run the archive step only when `out["results"]` has neither "error" nor a non-empty "errors". A refresh that
failed, or could not work out part of the money (a states.facts_of error, listed on the figures), saves nothing to
results_money that time (the last full run's figures stay), so archiving then could take a bill whose newest money is not
saved yet. The next clean run saves it and the archive can go ahead.

`refresh` does nothing when nothing it rests on changed (`results.data_rev`), so an idle run costs a few queries.
Measured after this fix round on a world built from the four real reports (aggregates only; the machine was shared and
loaded, about 3 to 6 times slower than the builder's measurements): server file store, first refresh 9.4 s and 114 MB
(16,916 items registered, 90 with later records); with every flag moved back to its remittance (9,640 items with later
records) 60.5 s and 262 MB; the same on the serverless in-memory image (826 MB) 54.6 s, the step's own peak 1,100 MB
(under the 1,750 MB gate), an idle second call 0.03 s. About 86% of the step is states.facts_of (section 8). Test once
wired: run `tests/test_results.py`, plus a cycle test that asserts `out["results"]["changed"]` on the first cycle and
`out["results"]["skipped"] == "nothing changed"` on an idle second cycle, on both hosts (test_cloud's `box` fixture for
serverless, a LocalState folder with `results/spot_checks_<year>.json` merged into `spot_checks`).

## 2. HOOK / H (diagnose.py): spot-check disagreements in the diagnostic bundle (unchanged)

`results.spot_check_cases(conn, days=90)` returns each disagreement of the last 90 days as {finding_id, day, stratum, state,
signal, payer_group, reason, basis (the state basis RemitWatch held when it was picked), note_chars, answered_at}. The typed
note itself is left out (free text a person wrote; it may name a patient). Please add it to the bundle (one JSON file,
`spot_check_disagreements.json`) next to each finding id's decision log, so "the rule behind it gets fixed".

## 3. Fix-B / owner of remitwatch/report.py: the weekly email lines (unchanged body; CHANGED: the lines)

In `report.build_report`, after the "## Alerts" block (`lines.append("")` that follows it):

```python
    try:
        from .results import email_lines
        res = email_lines(store.conn)
    except Exception as exc:        # the weekly report goes out without these lines and says why
        from . import runlog
        runlog.caught(exc, "results.email_lines")
        res = [f"The Results figures could not be read ({type(exc).__name__})."]
    if res:
        lines.append("## Money returned")
        lines += [f"- {x}" for x in res]
        lines.append("")
```

`email_lines` gives the top line with its split, "may have come back anyway" with its measured rate only when it rests
on 20 or more similar items (below that: "not enough similar items yet to say how much (n of the 20 needed)"), lost to
deadlines and at risk in 14 days, and the spot-check agreement. Counts and dollars only, no patient data.

## 4. C1 (api.py, index.html, features.js): the work R counts (CHANGED: the status notes R reads)

- The shell is done by C1 and checked (tab, router, script tag, include line).
- R counts as a person's work in RemitWatch: history lines of kind `sent`, `resubmitted`, `corrected`, `appeal`,
  `mark_sent`, `call`, `portal`, `outcome`, `log_outcome`, `posted`, `mark_posted`, `billed`, `mark_billed` and `fix` with a
  person as actor (not `system`, `hide rule`, `packets`, `fax`), every fax with status `sent`, and a `manual` line to
  status `appealed` (a person's Sent). NEW: a `manual` Sent line is read as the work its note names, as the app writes
  it today: "Faxed to ..." (the fax send and `_mark_sent_appeal`) is the fax, folded into the fax's own record when one
  went out within 3 days (one fax counts once, as a fax); "Billed: ..." (features.js Mark billed) is folded into the
  `billed` line Mark billed writes beside it; "Call or portal check: ..." (Log what happened, the payer is working on it)
  is a call or portal check. PLEASE KEEP THESE THREE NOTE PREFIXES (or tell R the new ones: results.STATUS_NOTE_KINDS,
  one line each). A Sent line with any other note is "Corrected claim or appeal marked sent".
- A fix recorded on an item (for the denial chart's markers): kind `fix` with a short note, or a person's note that says
  "credentialing", "enrollment", "authorization", "coding" or "modifier" fixed. The note text is never copied.
- Optional, for the item panel (C1, index.html): when the panel closes after being opened from a Results list, results.js
  opens the list again at the same page with the item's row in focus (it watches #drawer itself; nothing to wire).

## 5. C2 (today.js): the scoreboard and the day's spot checks (CHANGED: new keys, one line asked for)

`GET /api/results/scoreboard?day=<the page's own date YYYY-MM-DD>` -> `{recovered_cents, lost_cents, at_risk_cents,
given_up_cents, as_of, today, weeks: [{week, recovered_cents, lost_cents, at_risk_cents, given_up_cents, frozen?,
frozen_at?, now_cents?}], counts: {recovered, lost, at_risk, given_up}, basis: {key: one line}, given_up_source:
'results'|'selfcheck', given_up_check?: {cents, n, at, source}, given_up_weeks: one line, spot: {picked, answered, due}}`.
- Recovered and lost are null before the first run that worked them out (show a dash, not a link).
- A frozen week's recovered figure is what was frozen; `now_cents` (when present) is that week's money as read today.
- `given_up_check` is the self-check's own figure when it does not store the items behind it (section 6): show it as a
  cross-check line with no link, never as the clickable figure.
- ASKED: one line in Today's plan when `spot.due > 0`: "N spot checks for today, about N minutes" that sets
  `state.resultsOpen = 'spot'` and goes to the Results tab; results.js then scrolls to the spot checks and puts the focus
  on the first unanswered Agree. A click on any other figure sets `state.resultsOpen` to its key (recovered, lost,
  at_risk, given_up) as before.
- Pass the browser's own date as `day=`: the server keys the day's five picks by it (within a day of the server's UTC
  date), so the picks do not change at 7 pm Eastern.

## 6. G (selfcheck.py): the given-up figure (CHANGED: one source per figure)

- `selfcheck:given_up` = `{"cents", "n", "recoverable_cents", "at", "weeks"}` as today, plus, ASKED, `"ids": [[finding_id,
  written_off_cents], ...]` for exactly the items the figure counts. With `ids`, Results, the scoreboard and Today show
  the self-check's figure (labelled "From the self-check"), its list opens exactly those items, and the week by week
  column reads its `weeks`. Without `ids`, they show Results' own figure (labelled "Worked out here": written off on
  service from 2025 on, status open, no person's history line, not accepted by policy), which opens its own list, and
  the self-check's figure appears beside it as a cross-check that opens nothing. The figure and the list it opens never
  come from two definitions again.
- `selfcheck:audit` = `{"coverage", "precision", "matrix", "at"}` at the first run after the release: R copies it into the
  frozen "before" (meta `results:baseline`).

## 7. E (archive.py): keep R's tables, and run after refresh (CHANGED: corrects the builder's note)

The builder's note "A retired finding is fine: R reads the registry" held for first-flag dates only. The money and the
work of an item were worked out from its bill's claims, lines and history, which the archive deletes. Now:
- Never archive or delete rows of `results_items`, `results_snapshots`, `spot_checks` or `results_money` (NEW). They are
  small: results_money holds one row per item with money or work (its split, its kept events with their rules, its
  work), written only when it changed.
- Results treats an item as archived when its bill is in `archive_bills`, and then uses its results_money row instead of
  working it out again (test_money_returned_to_date_survives_the_archive_taking_the_settled_bill runs the real
  `archive.run` on both store kinds). If `restore` brings a bill back (its row leaves archive_bills), Results works it
  out again from the restored rows.
- Hook order: section 1 (archive after results.refresh in the same cycle, and only after a refresh with no errors).
- A refresh that could not work part of the money out writes nothing to results_money (NEW;
  test_a_run_that_could_not_work_part_of_the_money_out_keeps_what_was_saved).

## 8. INT / D (states.py): the interface R relies on, a pairing case, and the step's cost (CHANGED: cost)

- `states.facts_of(conn, items, outcomes={}, today=..., context=...)` with `r835.rows` keys date, status, tk, claim_key,
  line_key, paid, claim_paid, code, ocode, svc, payer, cfi, received, pccn, pcn, charge, adj, reversed_by, reverses and
  `ema.pays` keys link, svc, cpt, check, posted, payer, group, cents, in_835; `states.response_days(conn, group, work=...,
  payer_kind=...)`; `states.bill_of`; `states.row_group` and `states._same_payer` (read with a fallback). Please keep these
  names (tests/test_results.py fails loudly if they change). R reads `ema_checks` (check_no, payer, match, cause,
  damaged, trace_key, pair_trace) itself for the check match's rule.
- Pairing case (unchanged): `_pair_reversals` only pairs a reversal with a line of the same payer GROUP, and the group
  follows the claim filing indicator; when a payer moves a claim to another of its products the reversal pairs with the new
  answer on its own remittance. Results pairs again within each group with "the same payer by name counts"
  (`results._pair_group`); the same fallback in `_pair_reversals` would keep the drawer's story and Results in step.
  tests/test_results.py::test_a_reversal_cancels_the_earlier_answer_not_the_new_one_on_its_own_remittance_even_under_another_product
  is the case in invented data (it now expects 10 dollars and nothing left taken back).
- COST (NEW, for Fix D): on the backdated world 64.7 s of the 75.6 s Results step was `states.facts_of` (13 calls of 800
  items). 25.2 s of it is one line in `_r835`: `hit = next((x for x in pcns if x and x in str(ref or "")), None)` scans
  every claim id of the chunk for every PLB row with reason WO, FB, 72, J1, L6 or CS (all PLB rows, not only the chunk's):
  O(PLB rows x claim ids) per call. Suggested change: build `ids = set(pcns)` once and find the claim id in each PLB
  reference by extracting candidate tokens (split the reference on non-alphanumerics and look each token up in `ids`, plus
  the bill prefix), or select only PLB rows whose reference contains one of the chunk's bills. `_export_rows` took another
  17.9 s. Both are in Fix D's file; the detection run and the item panel pay the same cost.

## 9. F (docs): the Results tab as it works now (CHANGED)

tests/test_docs.py fails until the README layout names `remitwatch/results.py`, `remitwatch/api_results.py` and, on the
`static/` line, `results.js` (unchanged). OPERATIONS, the Results tab (what changed in this round in CAPITALS):
- The top line counts every dollar paid on an item's line after its first flag, plus money RemitWatch found; never money
  paid before the flag; one payment once. MONEY THE PAYER PAID BEFORE THE FLAG AND TOOK BACK IS PAID BACK FIRST BY ANY
  LATER PAYMENT: ONLY WHAT COMES BEYOND IT COUNTS ("taken back" is what is still unpaid).
- The three parts: after RemitWatch work (WORK ON AN ITEM OF THE SAME SERVICE, never another service of the claim),
  found by RemitWatch (MONEY FROM A PAYER OTHER THAN THE ONE THAT ANSWERED where no second payer was billed, BOTH PAYERS
  NAMED: A REMITTANCE OR AN ITEM WITH NO PAYER NAME NEVER COUNTS AS ANOTHER PAYER'S MONEY; an unposted
  835 payment posted, a claim that never left EMA), no recorded work ("may have come back anyway" with its measured rate,
  stated ONLY FROM 20 similar items up).
- AN EMA POSTING THE CHECK MATCH TIES TO AN 835 IS THAT 835'S PAYMENT. A PERSON'S PAID MARK COUNTS ONLY ON A DENIAL,
  BUNDLING, DOWNCODING OR UNDERPAYMENT ITEM WHOSE GROUP SHOWS NO PAYMENT RECORD AFTER THE FLAG; a mark on a payment the
  payer took back is listed apart ("marked Paid, but the payer took the payment back").
- The lift ("Did the work make the difference?") counts an item as worked ONLY BY WORK BEFORE ITS MONEY CAME (a payment
  marked posted never makes it worked); THE RETURN COUNTING ONLY WHAT THE WORK ADDED NEEDS 20 WORKED AND 20 UNWORKED ITEMS.
- ROI from the owner's costs; THE OWNER TYPES THE OWNER'S PASSWORD ON THE COSTS OR MINUTES PANEL ITSELF.
- Hours saved is an estimate; EACH THING COUNTS AT THE MINUTES IN FORCE THE WEEK IT HAPPENED (an edit prices what comes
  next); counts say how many items they are on ("2 on 1 item").
- Weeks freeze two weeks after they end; A FROZEN WEEK OPENS THE ITEMS IT COUNTED, WITH TODAY'S READING BESIDE IT where
  money moved since; frozen weeks show on both hosts.
- THE FIVE DAILY SPOT CHECKS FOLLOW THE PRACTICE'S OWN DAY; a line at the top of the tab says how many wait; an answer
  is recorded in place; Disagree asks what is wrong before it saves. A disagreement goes to the diagnostic bundle.
- THE ITEMS OPENED FROM A FIGURE'S LIST WALK THE LIST (Next, j and k), and closing the item brings the list back.

## 10. The cloud.py owner: nothing to add (CHANGED)

The frozen weeks now travel inside the run's own figures (meta `results:dashboard` and `results:lists`, which the
dashboard copy carries), so `SERVING_TABLES` needs no `results_snapshots`; do not add `results_money` to it either.

## 11. Owner decisions still open (for the release note)

- The fixed costs (hosting, other) and the price of a fax page and of an AI call are entered by the owner on the page; ROI
  shows a dash until they are.
- The minutes per task behind "hours saved" are the brief's defaults (12, 20, 10, 5) until the owner edits them; an edit
  applies from the week it is saved (the history keeps the earlier minutes). If the owner wants an edit to re-price past
  weeks too, that is a one-line change (results.hours_saved prices every week at today's minutes).
- The self-check's given-up figure becomes the headline once G stores the items behind it (section 6).
