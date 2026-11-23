# Chaos round 0 backlog: what push 2 must fix that hotfix 2 does not (2026-11-23 02:00)

Source: ${S}/chaos/r0_results/chaos_r0_<a|b|c|d|e|f>.json (exact steps, evidence, suspected code) and the verifiers' verdicts
(verify_r0_<x>.json in the same folder when they land). Every item below gets a failing invented test first, then the fix, on both
hosts where the code runs on both. V re-checks every one on the release candidate (brief_V.md: "Chaos round 0 ... re-checked first").

## A. Hotfix 2's guarantees (H1 to H11, ${S}/hotfix2/brief.md) must hold in push 2's rewritten code
Hotfix 2 lands on main with tests (tests/test_hotfix2*.py and their page checks). Push 2's pages and api were rewritten by C1, C2
and R, so a git merge cannot carry the fixes. Unit PORT (below) copies those test files into the dev branch unchanged and makes
them pass on push 2's code. At release the orchestrator records the merge of main with the dev tree kept (git merge -s ours) only
after those tests pass on the dev tree.

## B. Items for push 2 (owner in brackets)
- 0a-3 [PORT] Closing the 'Appealing by fax' window does not stop the batch; the next batch revives it. Each batch its own stop flag.
- 0b-4 [PORT] One 'Fax them anyway' overrides the stale hold for a whole batch. Reject stale_ok with bulk; per item only.
- 0a-11 [PORT] A network drop mid-batch races through every remaining item. Stop the batch on a network error.
- 0a-6 [PORT] Serverless: two instances apply each other's journal in arrival order. Last change by time wins on every copy;
  force a journal read before a serverless fax reservation.
- 0b-7 [PORT] 'idk' accepted as a let-go reason; bulk let go with only a confirm. A reason from a fixed list for in-window items,
  with free text; an owner-approved policy or the owner's decision for the rest (PLAN decision 14 and owner rule 2).
- 0b-13 [PORT] 'Show me the first item' opens an item whose instructions contradict each other (work-type text vs the fax caption).
- 0e-4 [INT] A payment taken back in full (an 835 of reversals only) creates no item, and older items keep quoting it. An item for
  every claim whose standing payment falls to zero through a reversal with no re-adjudication within N days.
- H8's feed date [INT] (detect.py open-claim pass): port the hotfix's function and tests into push 2's detect.py.
- 0e-1 [INTAKE] A cut-short remittance supersedes the complete copy. Hold a transaction that ends without SE or whose SE count or
  BPR balance does not match; never let an unbalanced copy supersede a balanced one; prefer the copy with more claims.
- 0e-5 [INTAKE] Another clinic's ERA becomes this practice's work. Learn the practice's payee TINs and NPIs from the archive (more
  than one is normal: group and individual NPIs), hold a remittance for any other payee, named on the Data tab and Upload page,
  with a way to take a file back.
- 0e-6 [INTAKE] A CSV export, a .zip of ERAs or an empty file in the Dropbox folder is skipped without a word; on Vercel there is no
  other way in. List every unread file with its reason in the run status and on the Data tab; read CSV exports from Dropbox.
- 0e-7 [INTAKE] Excel CSV with a BOM and cp1252 accents loses its first column. Strip the BOM from the bytes before decoding.
- 0e-8 [INTAKE] An export missing one column is refused with a wrong reason, in the run log only. Name the report and the column.
- 0e-9 [INTAKE] An old export re-saved today is taken as EMA's newest word. Rank exports by their content dates.
- 0e-10 [INTAKE] An added or removed column re-imports every row; Find a claim lists one charge four times. Fingerprint canonical
  fields; list distinct rows.
- 0b-10 [INTAKE] The ERA Payment Line export is read as charge lines (8 of 10 weekly ERA exports in the live store).
- 0b-11 [INTAKE] Upload messages mislead (locked .xlsx called old .xls; header-only CSV accepted; A/R refusal reason).
- 0b-12 [INTAKE] Upload page at 390 px cuts off 'Attach note'; unknown amounts show $0.00.
- chaos c and f items: added here when they land.

## Verdicts (independent verifiers, on the live code 949c4db)
- d (power user): all 11 reproduced and live. 0d-1 high (late fax answer drives another item's buttons); 0d-2, 0d-3, 0d-4, 0d-5,
  0d-7 medium; 0d-6, 0d-8, 0d-9, 0d-10, 0d-11 low. All inside hotfix 2 (H1 to H11). Full verdicts: ${S}/chaos/r0_results/verify_r0_d.json.
- a (impatient clicker, multitasker): all 12 reproduced. Live: 0a-1 and 0a-2 high; 0a-4, 0a-5, 0a-6, 0a-7 medium; 0a-8, 0a-9,
  0a-10, 0a-12 low. Not live (bulk paused): 0a-3 high, 0a-11 low. Hotfix 2 covers 0a-1, 0a-2, 0a-4, 0a-5, 0a-7, 0a-8, 0a-9, 0a-10,
  0a-12. Push 2 (PORT): 0a-3, 0a-11 (before bulk is switched on), 0a-6 (two serverless instances: re-read the journal before a
  status change or a fax reservation; last change by time wins on every copy). Full verdicts: ${S}/chaos/r0_results/verify_r0_a.json.
- f (clock and calendar), tester's own report (verifier running): 0f-1 high (from 7 PM Eastern an item due today reads closed and the
  fax is refused: every 'today' is the UTC date); 0f-2 high (while the feed is stale, follow-ups stop: cut from the newest payment;
  second requests held with no override); 0f-3 medium (Humana 18 months as 540 days); 0f-4 medium (Medicare redetermination 120
  days from the notice, not from presumed receipt at +5); 0f-5 low (365/730-day years); 0f-6 low (no weekday or holiday shown;
  Medicare next-workday rule); 0f-7 low (scoreboard year turns over at 7 PM Eastern). Hotfix 2c (wf_d3ce4878-681) takes 0f-1 to
  0f-5. Push 2: 0f-1 to 0f-5 ported (INT for codes.py/detect.py; PORT for api/insights/pages), 0f-6 (PORT + INT), 0f-7 (R's fixer
  or PORT: practice-local day in results and the scoreboard).
- c (flip-flopper, owner changes mind), tester's report (verifier running): 0c-1 high (an appealed item whose signal changes loses
  its fax: chase stops, history blank, can be faxed twice; detect.py save heir inheritance + fax.py followups + api _faxes_out by
  finding id); 0c-2 high (a reopened paid item marked Sent again gets weekly second requests for a claim the app shows paid);
  0c-3 high (a hide rule removed after the signal changed leaves items Let go for ever); 0c-4 high (REMITWATCH_FAX_SENDER set on
  one host only: both hosts fax the same item); 0c-5 medium (bulk: number changed mid-batch); 0c-6 medium (a typed recovery never
  checked against the payment on file); 0c-7 medium (changing the dashboard password with old tabs open locks the office out by
  address); lows 0c-8 to 0c-14 (old owner password retries lock owner features; payment on a hidden item stays let go; notes row
  keeps an old reason; CSV notes column can carry a typed name and DOB with names off; weekly email 'New this run'; bulk summary
  text; re-saving the original fax number leaves it unconfirmed).
  ROUTING (pending verdicts): 0c-1, 0c-2 -> hotfix 2d candidates if reproduced (else INT detect.py heir + PORT fax followups);
  0c-3 INT+PORT; 0c-4 HOOK+PORT (one sender across hosts: a sender claim with a heartbeat in the shared Dropbox, refuse to send
  when the other host holds it; setup page warns) + owner advice now; 0c-5 PORT (bulk); 0c-6 R or PORT; 0c-7, 0c-8 PORT (the page
  stops refreshing on 401; the server counts one wrong value from one browser once); lows PORT/R.
- f verdicts: all 7 reproduced and live (0f-1 to 0f-4 medium; 0f-5 to 0f-7 low). 0f-4 counts 1,450 open Medicare Part B items at
  payment date + 120. Hotfix 2c covers 0f-1 to 0f-5. Full verdicts: ${S}/chaos/r0_results/verify_r0_f.json.
- b verdicts: all 13 reproduced. Live: 0b-1 and 0b-2 high; 0b-3, 0b-5, 0b-6, 0b-8 medium; 0b-7, 0b-9 to 0b-13 low. Not live (bulk):
  0b-4 high. Hotfix 2 covers 0b-1, 0b-2, 0b-3, 0b-5, 0b-6, 0b-8, 0b-9. Full verdicts: ${S}/chaos/r0_results/verify_r0_b.json.
- c verdicts: 0c-1, 0c-2, 0c-3 high and live (reproduced on both hosts); 0c-4 medium live (two senders when the setting is set on
  one host only); 0c-5 medium not live (bulk); 0c-6 medium live; 0c-7 NOT reproduced (low); 0c-8 to 0c-14 low, live. Hotfix 2d
  covers 0c-1 (D3) and 0c-2 (D4). 0c-3 -> INT (detect.py save: an heir of a hidden item must return when the rule is removed),
  0c-4 -> HOOK/PORT, the rest as routed above. Full verdicts: ${S}/chaos/r0_results/verify_r0_c.json.
- GUARD L4-3 [INT, HIGH, latent on live]: a status a person saves while detection computes is rolled back by the save (the item is
  To do again while its history says Let go or Sent). The save must re-read people's changes made since the run started and keep
  them (a person's decision is never overwritten). Live today only when detection runs in working hours (rare while the 835 feed
  is down; regular once TriZetto is back): push 2 must fix it; deploys run their re-detection before the billers start.
- GUARD L4-10 [PORT]: Accept on a write-off pattern must need the owner (decision 14). L4-23 [PORT]: a held-down 's' key marks a
  string of items Sent (key repeat): ignore repeats and require one press per item.
- e verdicts: all 10 reproduced and live. 0e-1 and 0e-2 high; 0e-3, 0e-4, 0e-5, 0e-6, 0e-8 medium; 0e-7, 0e-9, 0e-10 low (0e-9: the
  mechanism is real but the trigger is not a re-save, see the verdict). Hotfix 2 covers 0e-2 and 0e-3 (H8); hotfix 2d covers the
  cut-short supersession part of 0e-1 (D1); the rest INT/INTAKE as routed. Full verdicts: ${S}/chaos/r0_results/verify_r0_e.json.

## ROUND 0 TOTAL: 67 findings from 6 irrational-human testers on the live code; 66 reproduced by independent verifiers (0c-7 not).
- GUARD L5-16 [PORT, compliance]: when a note turns out to be another patient's, the faxes and emailed packets that already carried
  it must be flagged for a person (possible disclosure: the practice's breach assessment, telling the payer, sending the right
  note). After hotfix 2's identity check (H6) ships, run it once over every packet already sent and list any that fail, for the
  practice manager to review (aggregate count to the owner; the list only in the app).
- GUARD L5 overall: 23 instances, 11 high (unexplained automatic changes until the decision log is wired; no take-back of a
  wrong file; statuses outliving bad inputs; letters for problems no longer seen). Owners per GUARDRAILS.md when it lands.
