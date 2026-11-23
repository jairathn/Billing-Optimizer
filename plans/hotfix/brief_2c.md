# HOTFIX 2c brief: deadlines that close on the right day, and follow-ups that keep coming while the feed is stale (live code)

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad

## Why
The clock-and-calendar chaos tester found, on the code the billers use today (main 949c4db), that items are declared closed while
the payer would still take them, and that follow-ups have stopped. Each is missed money, and the owner's rules are that work is
ranked by money then deadline and that "lost to deadlines" on the scoreboard is true. Read the four findings in full, with their
steps and evidence: ${S}/chaos/r0_results/chaos_r0_f.json, ids 0f-1 to 0f-5 (0f-6 and 0f-7 are for push 2, not you).

## Where
Your own worktree: ${S}/hotfix2c/wt (branch hotfix/deadlines, from main 949c4db). Edit only there; never commit or push. Hotfix 2
(${S}/hotfix2/brief.md) and a swing alarm (${S}/hotfix2/brief_2b.md) are being built at the same time in other worktrees and will be
merged with yours by the orchestrator: keep each change small and local (no refactors, no renames) so the merge is easy, and list
every function you change. The rules of ${S}/hotfix2/brief.md apply to you (failing test first with invented data, both hosts,
break-and-restore, plain English, no em dashes, no PHI in anything you write or return). Disk is short: keep at most one copy of
the real store (${S}/live_state_1122/store.sqlite) at a time in ${S}/hotfix2c/work/, check `df -h /` before copying (stop and say so
if under 3 GB free), and delete every copy when done.

## The fixes
F1 (0f-1). One practice-local day. Every place the code takes "today" to compare with a deadline, a follow-up date, an expiry, a
   list filter (SQL date('now'), api._day(), insights.utc_today(), the expiry check in appeals_send, appeals_appealable, the brief)
   uses the practice's date: a setting REMITWATCH_TZ (default America/New_York; named in .env.example and the runbooks; an unknown
   zone falls back to the default with a logged warning). Pass it to SQL as a parameter. The brief sends it so the page header and
   'closes today' agree. An item due today stays workable, listed and faxable until midnight practice time. Test: at 19:15 Eastern
   an item due that day stays in the open lists and the send gets past the expiry check; at 00:05 Eastern the next day it is closed.
F2 (0f-2). Follow-ups keep coming while the remittance feed is stale. Count follow-ups from appealed_at (or the fax's sent date) to
   the practice's today, not to the newest payment. When the feed is behind (the same measure the fax hold uses), keep them listed
   with a plain note that the remittances are behind and EMA or the payer's portal should be checked first; let a person send a
   faxed second request or payment request after confirming "still unpaid in EMA" (JSON true only), exactly as the first appeal
   already allows; the daily run itself still does not send automatically while the feed is behind. 'To decide' is counted from
   the practice's today as well, and its wording says what it counts. Test: an item appealed 61 days ago with the newest payment
   106 days old appears in the follow-ups and the 'followup' list, and a person's confirmed send goes.
F3 (0f-3, 0f-5). Windows stated in months or years are added as calendar months or years (Humana 18 months; Anthem two years;
   UnitedHealthcare and Optum 12 months; Medicare one calendar year from service for first filing; any other rule in codes.py that
   a payer states in months or years). Keep day counts only where the rule is stated in days. Month-end and February 29 handled
   (the last valid day of the target month). Test: Humana 18 months from 2025-05-24 is 2026-11-24; across 2028-02-29.
F4 (0f-4). Medicare Part B redeterminations: 120 days counted from receipt, which 42 CFR 405.942(a)(1) presumes is 5 days after the
   notice date, so the last day is the remittance date plus 125 days. The fax is never refused before that day. Apply the same
   presumption anywhere else the code counts a Medicare window from the notice (check CORRECTED_RULES and the letters' wording).
F5. Bump the rules version the code uses to decide that detection must run again, so the live stores re-detect once after deploy.
   Measure on one copy of the live store, old code vs new (server detection on the store file): items whose deadline moved, by
   payer group and rule, how many reopened (were shown closed and are now open), their dollars, follow-ups that now come due; the
   distribution of deadline shifts in days. Aggregates and finding ids only.

## Done means
Each fix with its failing tests (server and serverless where it runs on both), the break-and-restore record, the measurement, the
suites of every file you touched in python3, then the full suite once in ${S}/fix/venv2_compat312/bin/python and once in
${S}/fix/venv_sqlite340/bin/python (one at a time; the machine is shared), plus both node checks if pages changed. Return: per fix
{id, status fixed | partly | not_needed, tests, break_restore, notes}, the measurement, the suites, every file and function changed,
and what push 2 must keep (the guarantees your tests lock in).
