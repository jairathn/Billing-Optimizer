# GUARD brief: find whole classes of silent failure, not single bugs

S = /tmp/claude-0/-home-user-Billing-Optimizer/96e81489-2ad2-5c70-991d-e00db2e22749/scratchpad

## Why
Chaos testers found that one stray remittance file made every payer look current and flooded the worklist with about 1,600
claims the payers had answered; a cut-short copy of a remittance replaced the complete one; another clinic's file became this
practice's work; a double click marked an unseen item Sent; a screen left open erased a recovery. The owner (2026-11-23): "it is
concerning that while building this codebase, these guardrails were not thought of in advance ... I am trusting that your chaos
testers will uncover everything, or that there are ways to double check all of these so one stray 835 doesn't destroy the whole
codebase." Testers find instances. This audit finds the classes, lists every instance of each class in the code, and specifies the
structural guard that makes the class impossible or loud, so the answer does not depend on a tester having thought of the case.

## The code
A frozen snapshot of the release being built: ${S}/guard/snap (read-only; never edit it or /home/user/Billing-Optimizer; never
commit). The live sites run main (949c4db), checked out at ${S}/chaos/rc0; hotfix 2 for the live code is described in
${S}/hotfix2/brief.md. Chaos findings so far: ${S}/chaos/r0_results/*.json; their backlog: ${S}/wire/chaos_r0_backlog.md. The plan,
owners and release gates: ${S}/wire/PLAN.md. Real data, copies only: ${S}/live_state_1122/store.sqlite (aggregates and finding ids
in everything you return; no patient details). Invented data in any test you write; work in ${S}/guard/<your lens>/.

## The classes (one lens each)
L1 One input moves a shared value. Every place the code derives a value for the whole store, a payer or a period from one row,
   file or record (MAX, MIN, newest, latest, first, the last export, the newest file, a single flag), where that value then gates
   detection, states, deadlines, holds, alerts, the dashboard or the daily email. For each: what one bad or odd input moves it,
   what it gates, how many items and dollars it could move on the real store, and the robust form (a clamp, a quorum, a
   volume-weighted median, a per-payer value with a floor, a hold).
L2 Trust at the door. Every input path: 835s (Dropbox, TriZetto, upload), EMA weekly exports, A/R, Production, uploaded notes and
   documents, incoming faxes, the Vercel journal, settings and environment, payer fax numbers, the owner's policy decisions. For each:
   what is checked before it can affect anything (whose it is: payee, practice; complete: trailers, counts, balances; plausible:
   dates, amounts; new: duplicates under a new id or name; what it really is: bytes vs name; encoding; size), what happens to an
   input that fails (held and named where a person sees it, or silently skipped, or read anyway), and whether a file once read can
   be taken back.
L3 Blast radius of one run. The largest change one run can make to what people see and act on before any person sees it: items
   appearing (a flood), items vanishing or closing, dollars moving, per signal, per payer, both hosts. Which existing checks (the
   self-checks and safe mode in remitwatch/selfcheck.py, the invariants, the alerts) catch a flood and a vanishing, and which do
   not. Specify the swing guard: thresholds, what is held (the new worklist, automatic closures), what people see, who releases it.
L4 Every write checks what the person saw. Every route that changes state (statuses single and bulk, hide rules, policy, notes,
   fax sends and resends, number save and confirm, upload decisions, held report apply or discard, spot checks, owner settings), on
   both hosts: a double submit, a stale screen, two people at once, the Vercel journal replayed out of order or twice.
L5 Undo and explanation. Every automatic change and every ingest: recorded with its rule and evidence (the decision log), shown in
   the item's "Why did this change?", and undoable. List every effect that sticks after the bad input is removed (a copy marked
   superseded, ledger rows, archived data, a fax already sent, a hide rule, a person's action taken on a wrong list).

## What to return
Every instance you find of your class (file, function, line), what it can do (a concrete invented scenario; a failing invented test
when you can write one in your folder), whether it is already guarded in the snapshot, in hotfix 2, or open, and the guard that
closes the class. Then the class-level guard as a spec a builder can implement and a property a generated test can check.
