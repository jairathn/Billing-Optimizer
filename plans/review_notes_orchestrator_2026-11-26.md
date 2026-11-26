# The orchestrator's own read of every code change since a84c781 (2026-11-26)

Notes, not verified findings: each "check" or "risk" here is for the review workflow, a fixer or the final trace to confirm or refute. No patient data: code observations and aggregate counts only.

## detect.py (diff lines 1-620)
- COMPONENTS is defined twice in detect.py (constants block near SENT_837 and again near FULL_SHARE): same value, cosmetic; a later edit to one would silently shadow the other.
- feed_date: hole rule needs >= 20 payment days in the 120-day window; returns min(reach, a). Fine. Live today: no hole; one future-dated file clamps to today.
- PR handling: COST_SHARE = PR-1/2/3 only; PR-22/242/27/227/31 are denials to work (calibration fix 7). Rule 8 (archive_rules) separately raises PR-288/27/26/200/49/204 items: decision 2 applies there. PR-27 sits in both readings: check decisions unit keeps them consistent (bill the patient + ask for current insurance).
- AMT*B6 = paid with PR share: allowed rebuilt as charge - CO/OA/PI. OK.
- bill_units from lines_837 / EMA charge lines when SVC05 empty (calibration fix 2). OK.
- void_claims (CLP09 8): no items from answers to voided claims (T-N5). Ledger still counts the void's reversal. OK.
## detect.py (diff lines 620-1100): yardsticks and ledger
- _build_yardsticks: rate read on clean full-rate lines (status 1/19, paid>0, no orig code, rank 1, no pricing modifiers but 26/TC, no CARC 59/B10/97/236/234/150/186, not same-day policy, per-unit at paid units). Clinician rate only for NPIs with a person's first name. OK.
- State of a payer group for the fee schedule taken from " IN" in the group name, else KY: fragile if a group is named otherwise (check codes.py group names).
- usual_rate facility fallback scales by KY's facility/office ratio even for Indiana payers: approximation, sizes only (never calls underpaid). Low.
- _yardstick: physician paid at 85% of the payer's mode -> PROVIDER_RATE_GAP discount (owner rule 4.1). OK.
- _build_ledger: reversal cancels only a payment of its own amount (T-I5), payer joined claim by claim (union-find over ids and names seen together on a claim). Lost reversals counted for the Data tab. OK.
## detect.py part 1 (ledger end, units, recovery, settled-by-answer)
- PLB netting by claim named (claim id inside text, payer claim number naming one claim), joined payer per claim; J1 withholds the new payment's increase line by line; deferred recoveries skipped when the reversal already carried the balance. Complex: money reviewer should test J1 beside a reversal and a WO naming a claim by the payer's number.
- units: billed_units_of = SVC07 -> SVC05 -> 837/EMA units (only when adjustments + payment account for the whole charge) -> 1. units_billed skips $0 lines with no adjustments (T-N7).
- line_expected: rate per unit x units, capped at charge; no rate: fee-schedule level or own charge ratio; unlisted -> charge. Drugs/skin subs priced by archive rule 10.
- _recover: recovery = payments for the item's own units after its answer, per payer in date order, capped by what the service gained (less takebacks since, less what other findings took): used_units by paying line. This is where decision 3's "what arrived" lands for the claim-rule closures.
- _settled_by_answer: 'as_secondary' only after a COB-type denial (cob) and a CLP02 2/3 answer by the same payer (same id, group, and name in many-payer groups) with OA-23 or a standing payment; 'to_patient' = same payer allowed, paid 0, cost share only. One claim settles only when all its matching lines agree.
## detect.py part 2 (settled closures, provider gaps, part-aware resolve_elsewhere)
- _close_by_answer as_secondary: recovered = the secondary's own standing payment (via _mark_paid, capped at shortfall); a $0 secondary answer closes with recovered 0. When no other payer's primary answer is on file, the note says "the primary's remittance is not on file: look it up" BUT THE ITEM CLOSES. Money risk: the primary's share may never be chased once the item leaves Work now. Decision 3 ("remainder stays listed") must cover this: when the primary's payment is not on file in the 835s or EMA, the primary's share stays listed. Check the decisions unit does this.
- emit_provider_gaps: one PROVIDER_RATE_GAP per payer and clinician (owner rule 4.1), sized by live claims only unless worked. OK.
- emit_second_rates: unsupported routine amounts on >= 5 lines become one review item (T-N4). OK.
- resolve_elsewhere part-aware (T-C1): fits(part) and pays(units by part): whole needs whole or both parts. OK.
- _mark_paid has three recovery paths (recovery tuple; 835 paying line by units; EMA used key). Complex: money reviewer.
## detect.py part 3 (line rules, CO-45, underpaid grouping)
- _emit: central cap: a line is never worth more than its charge (T-C7). OK.
- Zero-paid line rule still skips 99024, G2211 and charges <= $1 entirely: G2211 denials are never line items, only the G2211_DENIED info pattern. Owner decision 4 ("keep pursuing codes a payer almost never pays") meets this rule: research must say per payer whether G2211 is ever payable there (CMS: not with -25 except AWV/vaccine/preventive; commercial payers that do not recognise it: likely never). Raise with the owner: G2211 at commercial payers is thousands of ~$16 lines; pursuing them only makes sense where a policy allows payment.
- CO-151 + M86 on a service already paid (standing payment on or before the answer, units covered): no item (T-N8). OK.
- Underpayment one-offs need FULL_SHARE (0.6) and _backed (20 clean lines); lines with no SVC05 units and several billed are skipped; capped at charge. Same-day duplicate deliveries collapsed (T-I3, _answer_copies keeps the worked delivery).
- _co45_not_denials: whole-charge CO-45 not a denial when claim-level paid, rebilled under another code and paid, or payer has no rate (never allowed or paid the code). The 'no rate' exclusion could hide a real denial for a code the payer never paid because it always denies it CO-45: check with the money reviewer (it is counted in ema_counts, not listed).
- _emit_underpaid: clinician discount -> PROVIDER_RATE_GAP; routine amount >= 5 lines -> SECOND_RATE group; PA/NP at 85% where the payer's PA/NP policy is unknown -> counted, not listed.
## detect.py part 4 (claim rules settled by answer, the 837 loaders)
- Claim rule: a code the denying payer settled itself (as secondary / to patient) counts as settled; a claim listed before whose unpaid codes are all trivial and settled by answer closes via _close_by_answer with paid_rows recovered.
- _cob_only: bills whose only remittances are one company's COB denial then its secondary processing, never paid as primary: the primary's money stays chased like T-C2 (the 837 pass). This answers my part-2 worry for that shape; the decisions unit must keep it so.
- _load_837 keeps sends only for EMA-listed claim ids and bills no payer answered as primary (memory). A bill answered as primary on any claim is never chased from the 837s even if a code on it was dropped (T-C9 territory): check fix7e-b.
- _answer_days_837: p95 per payer group over two years, >= NO_ANSWER_DAYS, written to meta mid-run (committed in the middle of detection: a killed run leaves it; harmless).
- _patient_keys/_answered_by_name/_ema_by_patient: answers on other bills by keyed names and member ids, payer_company must match (or differ, for a claim only a secondary answered), part_fits for 26/TC. Heavy SQL with temp tables: SQLite 3.40 compatible as written (no new functions seen).
## detect.py part 5 (one answered rule, run_ema_open_claims)
- _answered_rule: answered on its own EMA claim after the send, else per service by EMA postings for the patient (stands, payer_company matches, part_fits) or an answer as primary on another bill by keyed name. Bare $0 rows answer nothing (zero_answer_sql). Claims only a secondary answered need another payer's posting (the primary's).
- run_ema_open_claims: answered = a primary processing (CLP02 1/19/4/22) unless the bill is cob-only; a claim only another payer answered counts answered only when that payer is the one EMA billed. The 837 newest send dates the claim (T-I4); a void newest send ends it; a newer submission on the 837s drops the exports' older one; print-batch claims dated against the export day (T-N11); CH-8 holds claims whose payer's answers stop before the due day (counted in payers_behind, not listed). One visit chased under two bills: only the newest, per part (26/TC).
- paid_visits keyed by Python hash() of tuples: per-process randomised but used only within the run: fine.
## detect.py part 6 (pricing, open-claim items, the 837 pass)
- price_unanswered_lines: one pricer for both never-answered passes: rate per unit for the part (26/TC) x units, MPPR for 2nd+ surgical, capped at charge; no rate -> no_rate_size. OK.
- NO_ANSWER items when only another payer answered: size = expected - what that payer paid; hint says the primary's remittance is missing; cob case names the coordination denial. OK (this is the chase that keeps decision 3's remainder alive for cob-only bills).
- run_837_open_claims skips a claim when "the payer the 837 named answered it" (any CLP02, e.g. as secondary). If the practice billed payer X as secondary and no primary answered any claim of the bill (bill not in prim_bills), the primary's money is not chased by this pass. QUESTION for the answers reviewer / fix7e-b: is a claim the 837 sent to its secondary (SBR01 = S) with no primary answer on file chased anywhere (EMA exports pass, Production check)? The 837's SBR01 is not read (next_fixes 3).
- 837 pass: lines under $1 are nothing owed; due = send + NO_ANSWER_DAYS (or the payer's usual time from the visit); CH-8 holds; payers with no ERA judged by EMA only (counted when EMA is off).
## detect.py parts 7-8 (twins, reports' claims, takebacks, save, heirs)
- Send-day twins used one to one only, bounded by how many claims went out that day with those lines to that group (_sent_that_day). Conservative: OK.
- _837_ema_answers: a claim whose EMA claim cannot be found and is due only by EMA's postings waits, counted in meta ema:unanswered_unseen with dollars and overdue-30 (blindness loud). OK.
- run_ema_report_claims: the answered rule applied to G8 and C3f; on a failed step every claim stays listed (fail safe). OK.
- _taken_back_items: TAKEBACK listed at any amount (no floor) unless another payment pays the units, answered again, rebilled, or partial recoupment; a listed denial of the same service carries the takeback instead. OK.
- save(): a new RATE_SIGNALS item whose window already closed is not made (T-N4). Faxed or fax-prep items count as worked (their work follows the heir). carry_to_heirs moves faxes, packets, events and payer-letter ties to the heir in the same save; finding_heirs keeps chains.
- CLAUDE.md "never catch an exception without logging it": carry_to_heirs (json ValueError: continue), run() meta saves and record_moves (except sqlite3.Error: rollback) catch without runlog.caught. Small rule breaks; the rules reviewer should list them; fix in fix7e or the hook unit (which wires runlog).
- The full detect.py diff is read (4,698 lines).
## archive_rules.py diff (part a)
- Rule 2 for whole claims (_claim_after_primary): a payer's claim denial after another payer paid as primary is about the patient's share the primary left; sized at that share; Medicaid members never billed (written off). OK.
- Rule 5 h: claim the practice voided afterwards (void answered, nothing answered since): done. OK.
- Rule 7 now one item per billed line across reasons and payers (copy denials fold into the payer's own answer; CARC 109 hands the line to the payer that answered since); worked listings stay as 'replaced' with the new answer named. OK.
- apply_actions: recovered 0 when counts_as_recovery is false: this is where decision 3 (recovery = what arrived) must change for the primary-denial-then-secondary-payment case.
- Unlisted drug codes (J3490) never priced from a table: only the line's own price. OK.
## ema_checks (money2_b/c)
- zero_answer_sql: a $0 row under a check is an answer only with an allowed amount or a CARC/RARC posted the same day; a bare $0 row is no answer; item note says so and names patient-side money (claim may be done if payer put it on patient). Conservative toward listing; the hint points the biller to check. Watch in final trace: bare-$0 items that were actually PR-only answers (would be "already worked").
- unanswered_claims / _paid_on_another_claim / not_sent: answers count only for their own part (26/TC/whole: part_fits). Unknown part answers only a whole-service claim. OK.
- ema_denials payer_policy default now 'pursue' (decision 16 = owner's decision 4). OK.
## states.py
- _resends_837: a resend after the remittance to the same payer (payer id answered or same company), or to any payer after an other-payer CARC, is confirmed work ('resubmitted'), waits the payer's p95 answer time. Same-day sends skipped. OK.
- paid_first_elsewhere (fix6): Medicaid denial after another payer paid >= half the expected (or half the charge) -> done 'paid_first_other_payer' (no money: Medicaid pays to its own rate, member not billable). Duplicate denial after same payer paid -> done. Judgment: correct for Medicare primaries (80% > KY Medicaid rate); possible small leak where Medicaid rate exceeds primary payment (commercial primary paying little but >= half). Flag to final trace: sample these and compare Medicaid fee to primary paid.
- TAKEBACK repaid after takeback (EMA posted, not in 835, net >= 95% of takeback) -> done. OK.
- Archive proven settled (paid_before/other_payer_paid/voided) -> done before credit handling unless a person worked it here. OK.
## small files
- runtime.practice_today (America/New_York default, REMITWATCH_TZ); used in alerts, report, letters, status days_behind (feed_date). Fallback _UsEastern correct (second Sunday March 07:00 UTC, first Sunday Nov 06:00 UTC).
- codes: window_end counts months for month/year windows; Medicare +5 receipt days. add_months clamps. OK.
- pipeline: store_problem stops a run on a missing/emptied store (marker beside the store), daily alert mail; status() uses feed_date; people_changed_since_detection by event id.
- cli: send-appeals prints id/status/code only (privacy). accept-store command.
## store.py / cloud.py
- Supersession rule 2: versions compete only when they share a claim id+charge or fingerprint (or a broken copy with the same payment); complete balanced versions never hidden by broken ones; both-differ counted and shown (DIFFER_META), lost claims of hidden broken copies shown. recheck on rule change. OK.
- Store image check: sha256+size recorded in status.json; a missing/other image stops the run (accept-store --state-folder). Pending record for lost commit answers. Cursor written only beside an image. OK.
## actions.py
- heir_of everywhere (faxes, events, status); journal entry ids; NOT_APPLIED when a stale page's press would undo the run's own Paid or hide (history line; person decides). Acceptable; visible.
## ema_ledger.py
- KEEP_RULE 2: every line kept (no pruning by items). Store image grows: serverless image ~894 MB uncompressed (real data, two service years) vs SQLite deserialize cap 1,024 MB -> the hook unit's cap fix is critical; verify on merge (and headroom as data grows: ~0.85 KB/line).
## uploads.py
- misnamed PDFs rejected; several_patients rejects reports posing as notes; note_identity needs name + ID/MRN + date of service; match_finding keeps only ids the text states. Good for fax safety.
## alerts / insights
- SWING alert (alert only, 7 days, levels from data), stale feed by feed_date + gap words; data_health adds payer_stopped, no-ERA 837 payers, send weeks, remittance versions differ / broken copy lost, reversal not on file. follow_ups/to_decide counted to the practice's today. OK.
## api.py
- _person_status: seen_status check, in-flight fax check, Paid needs amount > 0 (except blocks post/credit), bounds (10x charge, $1M). Server in one IMMEDIATE transaction; serverless journals with seen.
- DECISION 2 CONFLICT RISK: "can be marked done too if user desires" for patient-group denials: if the decisions unit makes Done a $0 Paid, PAID_ZERO refuses it (block must be in ZERO_PAID_BLOCKS). Check the unit's implementation on merge.
- _sent_once_faxed keeps a run's change made while SRFax had the fax; _prep_take in-flight markers; _appeal_on_file blocks a second appeal on the same line from another item; no_fax route (CGS level 1); note identity check before fax (held note_check, person ticks). bulk/stale_ok/confirmed only as JSON true.
- hide: owner password, typed reason, confirm count equals preview (409 changed), in-flight items skipped and journaled.
- claims_search partial note on serverless.
## fax.py
- digits ASCII only; practice day on letters/cover; paid_on_file stops chases of money the store shows paid (person decides); NO_FAX CGS level 1 (myCGS/mail), Medicare (other) by hand.
## static
- spot check: hide dialog escapes payer/code/carc (faxModal escapes title). Rest left to the review workflow's people agent.
