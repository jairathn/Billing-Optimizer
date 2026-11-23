# HOTFIX 2 brief: what chaos round 0 found in the code the billers use today

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad

## Why this exists
The live sites (server and Vercel) run main at 949c4db. Chaos testers who behave like tired, rushed, confused people found ways
the live code can fax the wrong patient's note, mark the wrong item Sent, erase a recovery, hide hundreds of items with one key,
or flood the worklist with claims the payers did answer. The billers start around 13:00 UTC. This hotfix goes to main before
then. A much larger release (push 2) is being built in /home/user/Billing-Optimizer on another branch: NEVER read from, edit or
run anything there. Your only code is the worktree below.

## Where you work
- The worktree ${S}/hotfix2/wt (branch hotfix/chaos-r0, from origin/main 949c4db). Edit only there. Never commit, never push:
  the orchestrator commits and pushes after independent checks.
- Your scratch folder: ${S}/hotfix2/work/ (worlds, servers, screenshots). Delete real-data copies at the end.
- The findings, with exact steps, evidence and suspected code: ${S}/chaos/r0_results/chaos_r0_a.json, chaos_r0_b.json,
  chaos_r0_d.json, chaos_r0_e.json (the ids below are theirs). Read each one you fix in full.
- Real data, copies only: ${S}/live_state_1122/store.sqlite (Vercel's real store image with the billers' statuses). Open copies.
- Rules from CLAUDE.md apply in full (invented data in tests, the scrubber for logs, no PHI in anything you return, no real
  faxes, emails, Dropbox, TriZetto or AI calls: unset SRFAX_*, DROPBOX_*, TRIZETTO_*, ANTHROPIC_* and mail settings; the fakes in
  tests/fake_fax.py, tests/fakes.py, tests/test_fax.py's site fixture, tests/test_cloud.py's serverless fixture).
- Every fix: a failing test first (invented data), then the fix, then break the fix on purpose and see the test fail, then
  restore it exactly. Both hosts wherever the code runs on both (the server's store file and the serverless site with its
  serving copy and journal). Page behaviour: Playwright through node, in the style of tests/review_assets/page_check.js and
  tests/review6/test_r6_cov_page.py (skipped only when playwright cannot be required), on an invented store.
- Keep each change small and local. This is a hotfix of live code: no refactors, no renames, no new features beyond what each
  item needs. Plain English in every message a person sees; no em dashes.

## The fixes (all required unless the code proves a finding wrong; then say so with the evidence)

H1. The item panel acts on the item it shows, once (pages: static/index.html, faxes.js, features.js, desk.js).
Findings 0a-2 (double click on 'Mark sent and open the next' marks the next, unseen item Sent; triple marks two), 0a-7 and 0d-4
(a slow or out-of-order answer leaves the panel on another item than the row clicked), 0d-1 (a late 'Appeal by fax' answer
rebinds the buttons of another item's panel: pressing 'fax it anyway' in item B faxes item A), 0d-5 (when a fax goes out its
item reopens over the item the person moved to; typed text is lost), 0a-8 and 0a-10 (Let go during a fax replaced by Sent; a
double 'Sent' line in the history).
Required: every action button is bound to the finding id the panel was rendered for; a button is disabled from the first click
until its answer comes back (a second click does nothing); openDetail keeps a request counter and drops any answer that is not the
latest; code that runs after an await touches the panel only if the panel still shows the same item (otherwise a short notice
names what finished, without moving the panel); 'open the next' opens the next item only once per click.

H2. One fax at a time per item, and Sent only when a fax went out (server: api.py appeals_send, set_status, _faxes_out,
_mark_sent_appeal; fax.py if needed).
Findings 0a-1 (Mark sent during the fax minute: nothing is faxed, the item is Sent, the reason never shown), 0d-2 (a second
'Appeal by fax' while the first is still sending marks the item Sent even when that send then fails: the reservation still
'sending' with no provider id is counted as a fax that went out), 0a-9 (concurrent presses run duplicate reviews), 0a-12 ('already
faxed' after the server stopped between the reservation and the send).
Required: while a fax for the item is in flight (reserved, no provider id, younger than the send can take), appeals_send answers
409 with code in_flight ("A fax for this item is going out now. Wait a minute and look at the item again.") and changes nothing;
a person's status change on that item is refused the same way (409 in_flight) on both hosts; a reservation too old to be in
flight and with no provider id is reported as unknown (code unknown_send: "A fax for this item was started at <time> and RemitWatch
does not know whether it went out. Look on the Faxes page before sending it again.") and never counted as sent and never marks
the item Sent. A fax that SRFax took (provider id) still marks Sent as today.

H3. A screen left open cannot undo what changed since (server + pages: api.py set_status, bulk_status, _record_status;
actions.py apply_status; cloud.py journal replay).
Findings 0a-4 (Sent or Let go pressed on a panel left open while the hourly run closed the item as Paid permanently overrides the
Paid and erases the recovery), 0a-5 (a bulk Let go from a list opened earlier lets go an appeal another tab just faxed; its
follow-ups stop), 0d-8 (on Vercel, Sent, To do, Sent within one second ends as To do: the replay de-duplicates by finding, second,
status and actor).
Required: the page sends seen_status (the status the panel or list showed for that item when it was drawn) with every status
change, single and bulk (bulk: seen = {finding_id: status}). The server compares it with the item's current status on the copy
it serves (the store on a server, the serving copy on Vercel): different -> single: 409 code changed with the current status and
a plain message ("This item changed since your page loaded: it is now <label>. It was not changed."); bulk: those items are left
as they are and returned in changed: [ids], the others are applied, and the page says how many were left and why. A request
without seen_status (a page loaded before this release) is refused for an item the daily run closed by itself (status set by the
run, e.g. paid with an automatic note) and for an item with a fax in flight; otherwise it behaves as today. The serverless journal
replay must keep every entry a person made (give each journal entry a unique id when it is written and de-duplicate on that id;
keep reading old entries without one exactly as today).

H4. Amounts mean what was typed (pages + server: features.js setFindingStatus; api.py set_status; actions.py apply_status).
Findings 0d-3 ('1,250.00' read as $1.00, '$95' as the full shortfall, -50 accepted, 1e9 accepted) and 0b-6 (Paid defaults to
the full disputed amount and counts it as recovered, even when only part was paid or nothing was).
Required: the Paid prompt starts empty and shows the disputed amount only as a hint; it accepts 1250, 1,250, 1,250.00, $1,250.00,
1250.5 and rejects anything else (letters, negatives, more than two decimals, exponent forms, more than ten times the line's charge
or more than $1,000,000) by asking again with a plain reason; an empty answer asks again ("Type the amount the payer paid, from
the remittance or EMA. If you do not know it yet, leave the item as it is."). The server refuses a person's single-item Paid
without a whole-cent amount, a negative amount, or one above the same bound (400 code amount, plain message). Bulk Paid (if the
page offers it) keeps today's behaviour but its confirmation must say each item counts as paid in full and the total.

H5. The API reads only what it was sent (server: api.py appeals_send, set_status, bulk_status, hide routes).
Findings 0d-6 (stale_ok, confirmed and bulk sent as the text "false", "no" or "0" count as yes; use_ai likewise) and 0d-9
(malformed bodies give 500 on the server and 200-then-dropped on Vercel; repeated ids counted twice; notes of any size).
Required: a safety override (stale_ok, confirmed, bulk, and any other yes/no that skips a hold) counts only when it is JSON true;
use_ai is off for false, 0, "0", "false", "no", "off". A body that is not an object, a status that is not text, ids that are not a
list of text, a recovered amount that is not a whole number, notes over 5,000 characters: 400 with a plain message on BOTH hosts,
before anything is written or journaled. Repeated ids count once.

H6. A note is faxed only for the patient and visit it shows (server: uploads.py sniff, match_text, match_finding, _take_note;
api.py appeals_build, appeals_send, the note match route; pages: features.js note picker if needed).
Findings 0b-2 (a visit note naming another patient and another date, attached by a person, is faxed to the payer without any
identity check: match_finding gives the claim's own ids as ids_stated), 0b-3 (a PDF renamed .csv is kept as a visit note,
matched by any name and date in it, and after one 'Use this note' click it was faxed with other patients' rows).
Required: (a) a document is a visit note for an item only if its text shows this patient by two identifiers (as the ModMed note
path already requires: name plus date of birth, or name plus record number) and the item's date of service; a person attaching
it does not change what the text shows. (b) A document whose text names more than one patient (two or more different names or
birth dates, or a table of rows) is not a visit note: refused as a note with a plain reason ("This looks like a report listing
several patients, not one visit note."). (c) A file whose name says .csv, .xlsx or .txt but whose bytes are a PDF (or the other
way round) is refused in the report uploader with a plain reason, never kept as a note. (d) A note with no readable text (a scan)
or that fails (a) is held before any fax with code note_check and a plain message, and goes only after a person ticks "I opened
this note: it is this patient's visit on <date>" (sent as note_checked: true, JSON true only), which is recorded in the item's
history with who and when. Never put a name, date or id from a note into an exception, a log line or a test.

H7. Hiding a reason for a payer shows its size first and needs the owner (server: api.py hide_add and a new preview route;
pages: desk.js hideReason and the panel's hide link).
Findings 0b-5 (one click on 'Hide ... for <payer>' inside one item lets go 262 items and all future ones, count only shown after)
and 0d-7 (one link plus Enter on a prefilled prompt; no owner password).
Required: before anything is hidden the page shows how many open items it lets go now, their dollars, how many of them are still
inside their filing window and their dollars, and that every future item of that reason for that payer will be let go too (a GET
preview route, signed-in, both hosts, from the copy served). Hiding then needs the owner's password when one is set (the same
x-owner-password header and require_owner checks the Providers tab uses, including the wrong-try counting) and a typed
confirmation (the person types the number of items shown). The server refuses a hide without the owner's password (when set) and
without a confirm count equal to the count it computes now (409 changed if different). No prefilled reason text: the reason is
typed.

H8. One stray remittance cannot make the whole feed look current (server: detect.py run_ema_open_claims and anything else that
takes MAX(payment_date) as 'the remittances reach this day'; alerts.py stale rule; api.py _data_behind_days and its callers).
Findings 0b-1 and 0e-2 (one 835 from one payer, even a $71.59 adjustment-only file, makes every payer look current: about 1,600
claims the payers did answer become 'never answered', the stale-feed alert clears and the 14-day fax hold lifts) and 0e-3 (one
835 dated in the future wipes out every 'never answered' and 'never reached the payer' item).
Required: one function (in detect.py or a small helper module both detect.py and api.py import) gives the date the remittance
feed reaches: payment dates after today count as today for this purpose (and a file whose payment date is more than 7 days after
the day it was read is held as suspect where the readers allow it without a refactor; otherwise just clamped); per payer group,
the latest payment date; the feed date is the latest date D such that payer groups holding at least half of the remittance lines
of the 120 days before the clamped newest date are current through D (a volume-weighted median of the groups' latest dates). With
fewer than three payer groups it is the clamped newest date (so small stores and existing tests behave as today). The open-claim
pass ('never answered' and 'never reached the payer'), the stale-feed alert and the fax hold all use it. Bump the rules version
the code uses to decide that detection must run again, so the live stores re-detect once after deploy. Measure on a copy of the
live store (old code vs new, server detection on a store file): items by signal with counts and dollars, before and after, and the
feed date before and after. Aggregates only.

H9. Fax numbers are ASCII digits (fax.py digits). Finding 0d-11 (full-width or Arabic-Indic digits accepted).
Required: only 0-9 count as digits; a number with other digit characters is refused with a plain message.

H10. Find a claim does not say 'nothing matches' when it cannot know (api.py claims_search, static/claim.js).
Finding 0b-8 (on Vercel the dashboard copy holds claims only for bills with items, so a paid claim with no item reads as 'Nothing
in the remittance files ... matches'). Required: on the serverless host the no-result text says the copy here holds only claims
that have items, that a paid claim with no item will not show here, and where to look (the server site, if the deployment has one
configured, or EMA). On the server the text stays as is. Also 0b-9: claim ids with dashes or '#' are searched with those removed.

H11. Address-bar names are only RemitWatch's (index.html tabOpen, render). Finding 0d-10 (#constructor, #__proto__, #toString
rewrite the address to code text). Required: alias and tab lookups use own properties only.

Out of scope (push 2 owns them; do not touch): bulk appeal behaviour (switched off on live), 0a-3, 0b-4, 0b-7, 0b-10 to 0b-13,
0a-6, 0a-11, 0e-1, 0e-4 to 0e-10, anything in archive, Results or the A/R and Production readers.

## Done means
1. Each H item: its failing tests (server and serverless where it runs on both; Playwright for page behaviour), the fix, the
   break-and-restore record (test name, what you broke, that it failed, that the restore is exact by git diff).
2. The chaos steps of every finding you fixed, replayed on the worktree's code in a real browser and over the API on both hosts:
   each now behaves as expected (aggregate notes only).
3. H8's before and after measurement on a copy of the live store.
4. The full suite once in ${S}/fix/venv2_compat312/bin/python (3.12 with SQLite 3.45: the AWS image and CI) and once in
   ${S}/fix/venv_sqlite340/bin/python (3.11.1 with SQLite 3.40.0: Vercel; only tests that serialize a store image may fail there),
   plus `node tests/calc_model_check.js` and `node tests/lab_review_check.js`. Run suites one at a time (the machine is shared;
   memory is tight). The orchestrator runs the other two environments.
5. `git diff --stat` and a list of every file changed. Nothing committed.
6. Return the structured result: per H item {id, findings, status: fixed | not_reproduced | partly, tests, break_restore, notes},
   the measurement, the suite results, the files changed, and anything you saw that is live-severe and not in this list (with
   steps), and what push 2 must keep (the guarantees your tests lock in).
