# HOTFIX 2d brief: paid claims hidden as duplicates, a missing store image taken for a first load, and appeals that lose their fax

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad

## Why
The guardrail audit and the chaos testers found these on the code the billers use today (main 949c4db):
- L2-3: store.mark_superseded groups 835 transactions by (payer, trace number, payment date) and hides all but one, without asking
  whether they hold the same claims. On a copy of the live store this hides 12 paid claims ($696.84) right now: two different
  remittances that share a trace and a date (and cut-short copies, L2-2). Those claims then look unanswered and get chased.
  Evidence and a failing test: ${S}/guard/L2/find.json (L2-2, L2-3) and ${S}/guard/L2/test_l2_door.py.
- L2-19: on Vercel, a store image moved or renamed in the state folder makes the next run start from an empty store and save it
  as the practice's: every item, status and recovery disappears from the site. On a server, a store file that is missing (a
  volume not mounted) is created empty the same way. Evidence: the same files (L2-19).
- 0c-1: an appealed item whose signal changes (new claim files or EMA data prove the billed level) gets a new finding id; its fax
  stays on the old id, so its follow-ups stop without a word, its history shows no fax, and the same appeal can be faxed again.
- 0c-2: when a person reopens an item the run had closed as paid and marks it Sent again, the follow-up engine faxes the payer a
  weekly 'second request' for a claim the store shows paid.
  Steps and evidence: ${S}/chaos/r0_results/chaos_r0_c.json (0c-1, 0c-2); the independent verifier's verdicts will be in
  verify_r0_c.json in the same folder (read them if present).

## Where
Your own worktree: ${S}/hotfix2d/wt (branch hotfix/supersede-and-heirs, from main 949c4db). Edit only there; never commit or push.
Hotfix 2 (${S}/hotfix2/brief.md), 2b (${S}/hotfix2/brief_2b.md) and 2c (${S}/hotfix2c/brief_2c.md) are being built at the same time
in other worktrees and will be merged with yours: keep every change small and local (no refactors, no renames), and list every
function you change. The rules of ${S}/hotfix2/brief.md apply (a failing test first with invented data, both hosts where the code
runs on both, break-and-restore, plain English, no em dashes, no PHI in anything you write or return, never a value read from a file
in an exception or log line). Disk is short: at most one copy of the real store (${S}/live_state_1122/store.sqlite) at a time in
${S}/hotfix2d/work/, `df -h /` before copying (stop and say so under 3 GB free), delete every copy when done.

## The fixes
D1 (L2-3, L2-2 where it is the same rule). Supersede only versions of one remittance: copies whose claim sets overlap (the same
   claim id with the same charge) or whose content fingerprints match. Two balanced transactions with no claim in common under one
   key are both kept. Two overlapping but different versions: keep the complete, balanced one (a copy that ends without its trailer
   or whose totals do not add up never supersedes a balanced one); if both are balanced and differ, keep both visible and record a
   problem a person sees ("two versions of one remittance differ"). Existing stores are re-evaluated once (the next run recomputes
   supersession). Measure on one copy of the live store: groups, transactions and paid claims and dollars hidden before and after.
D2 (L2-19). A run never starts from an empty store where a store existed. Serverless: when the store image is missing but
   status.json or cursor.json show earlier runs, the run stops before changing anything, with a loud status and alert ("The store
   image is missing from the state folder. Nothing was changed. Put it back, or restore it from Dropbox's version history.");
   a first load needs both absent. Record the image's size and sha256 in status.json at each save, and refuse to run on an image
   that does not match the last save unless a person runs the documented recovery command. Server: a marker file beside the store
   (written at first creation) makes a missing store a stop, not a new empty store. Name the recovery steps in docs/OPERATIONS.md.
D3 (0c-1). When detection gives an item's money a new finding id (its signal changed), the faxes, packets and history of the old
   id follow it: the follow-ups keep running on the heir, the heir's panel shows the fax, and appeals_send refuses a second appeal
   for the same claim and level as it does for the same id. Choose the smallest safe mechanism (look faxes up by the claim and
   line the item covers as well as by id, or re-point the rows at save) and say why.
D4 (0c-2). The follow-up engine never sends a second request, a next-level appeal or a payment request for an item whose money
   the store shows paid (the claim or line paid in full on file after the appeal), whatever the item's status says; it records a
   line in the item's history and lists the item for a person ("You marked this Sent, but a remittance shows it paid on <date>.
   Check it before RemitWatch chases it.").
D5. If D1 or D3 change what detection produces, bump the rules version so the live stores recompute once after deploy.

## Done means
Each fix with its failing tests, break-and-restore record, the measurement for D1, the suites of the files you touched in python3,
then the full suite once in ${S}/fix/venv2_compat312/bin/python and once in ${S}/fix/venv_sqlite340/bin/python. Return per fix
{id, status fixed | partly | not_reproduced, tests, break_restore, notes}, the measurement, the suites, every file and function
changed, and what push 2 must keep.
