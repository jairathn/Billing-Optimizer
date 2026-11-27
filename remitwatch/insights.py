"""
What the home screen says: the work still inside a filing window grouped by the kind of work it takes, the bigger
levers that are not claim by claim (payer rates against Medicare, fees set below what payers allow, payment fees,
denial causes that keep recurring), and what in the data is hiding money (a stale feed, missing remittance weeks, EMA
exports that miss visits, deadlines resting on unconfirmed rules).

Everything here reads tables the dashboard's serverless copy keeps (findings, payer_rates, exposure, meta), so it
works on both hosts. Amounts are cents.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, timedelta

OPEN = ("open", "in_progress", "appealed")
TODO = ("open", "in_progress")          # still to do: an item sent to the payer waits on the payer, not on a person
SMALL_CENTS = 2000                      # items known to be worth under $20: about a third of the list and a few percent of the money

# The kind of work each finding takes. A signal not named here falls to its action (fix and resubmit, appeal) and then
# to "check first".
WORK_TYPES: list[dict] = [
    {"key": "fix", "label": "Fix and resubmit", "needs": "EMA and the payer's claim number (for a claim rejected before the payer: its rejection report; for one still in EMA's print batch: the batch)", "short": "Correct it in EMA and resend it the way the item says.",
     "how": "The claim went out with something missing or wrong (a modifier, a code, information the payer asked for). Correct it in EMA and send the correction the way the payer takes it before the date shown (a corrected claim for most payers; for Medicare a reopening, a new claim or a redetermination, as the item says), then press Mark sent."},
    {"key": "auth", "label": "Get the authorization", "needs": "The authorization record, or the payer's portal",
     "short": "Find the authorization and appeal, or ask for a retro-auth.",
     "how": "Denied for a missing prior authorization. Find the authorization in EMA and appeal with it; if there is none, ask the payer for a retro-authorization. Tell the front desk so it does not happen again."},
    {"key": "note", "label": "Appeal with the visit note", "needs": "The visit or procedure note from EMA",
     "short": "Send the appeal letter with the visit note from EMA.",
     "how": "The payer says the visit or level was not supported, or bundled a separately documented visit. Build the appeal letter, attach the note for that date, send it, then press Mark sent."},
    {"key": "rule", "label": "Appeal on the coding rules", "needs": "Nothing extra: the letter cites the rule",
     "short": "Send the appeal letter; it cites the coding rule.",
     "how": "The payer bundled a procedure or add-on that the coding rules let you bill. Appeal with the letter; if the rule allows a modifier and the sites were separate, add 59 or XS and resubmit instead."},
    {"key": "rate", "label": "Ask for the usual rate", "needs": "The payer's phone line or portal",
     "short": "Ask the payer to reprocess at its usual rate.",
     "how": "Paid less than this payer usually pays for the code. Ask it to reprocess at its usual rate; one request can list every claim for the same code."},
    {"key": "chase", "label": "Chase the payer", "needs": "The payer's portal or phone line",
     "short": "Find out where the claim is: the payer's portal, or call.",
     # the date an unanswered claim sent on time shows runs the payer's appeal window past its first-filing limit
     # (detect.run_ema_open_claims), so a claim the payer has no record of is sent again by that limit, not by that date
     "how": "EMA shows the claim sent and unpaid, and no answer from the payer has come back. Look it up on the payer's portal "
            "(or call). Not on file: send it again within the first-filing limit (see the filing window on the item). Rejected: "
            "correct it in EMA and send it again. Denied or paid: get the remittance or the EOB and work it here."},
    {"key": "bill", "label": "Bill the second payer", "needs": "The first payer's remittance and the second payer's details in EMA",
     "short": "Send the claim to the second payer.",
     "how": "The first payer paid and left a share a second payer on file should pay, and nothing has come from it. Send the "
            "claim to the second payer with the first payer's remittance before the date shown, then press Mark billed."},
    {"key": "post", "label": "Post the payment", "needs": "The 835 (or the payer's portal) and EMA's posting screen",
     "short": "Post the payment or the denial in EMA.",
     "how": "The payer's remittance is on file but EMA never posted it (or posted less). Find the deposit, post the payment or "
            "the denial in EMA under the trace shown, then press Mark posted."},
    {"key": "check", "label": "Check first", "needs": "A look at the claim",
     "short": "Decide: other payer, the patient, appeal, or let go.",
     "how": "Something a person has to decide: bill another payer or the patient, fix an enrollment, find an earlier payment, or let it go."},
]
_WORK_OF = {
    "fix": {"DENIED_MISSING_MODIFIER", "DENIED_CODING", "DENIED_MISSING_INFO", "EM_BUNDLED_NO_MODIFIER", "EM_BUNDLED_MOD57_MINOR",
            "REJECTED_PRE_PAYER", "NOT_SENT"},
    "chase": {"NO_ANSWER", "FORWARDED_TO_CARRIER"},
    "bill": {"SECONDARY_NOT_BILLED"},
    "post": {"REMIT_NOT_POSTED"},
    "auth": {"DENIED_AUTH", "DENIED_REFERRAL"},
    "note": {"EM_BUNDLED_MOD25", "EM_BUNDLED_MOD57_MAJOR", "EM_BUNDLED_MOD24", "DENIED_MEDICAL_NECESSITY", "DENIED_FREQUENCY", "DENIED_LEVEL_OF_SERVICE",
             "DOWNCODE_SVC06", "DOWNCODE_VS_BILLED", "DOWNCODE_CHARGE", "DOWNCODE_LEVEL_REDUCED", "DOWNCODE_RATE", "DOWNCODE_LINE_SWAP", "DOWNCODE_LEVEL_DENIED"},
    "rule": {"PROCEDURE_BUNDLED", "ADDON_BUNDLED"},
    "rate": {"UNDERPAID_VS_OWN_RATE", "SECOND_RATE", "MPPR_EXCESS", "DENIED_CONTRACT_RATE", "PROVIDER_RATE_GAP", "OLD_FEE_SCHEDULE"},
    "check": {"DENIED_NONCOVERED", "DENIED_DUPLICATE_UNPAID", "DENIED_PROVIDER", "DENIED_TIMELY_FILING", "DENIED_OTHER", "DENIED_MISSING_PRIMARY",
              "CODE_CHANGED", "EMA_DENIAL_AS_CONTRACTUAL", "CREDIT_BALANCE", "DENIED_COVERAGE_DATES", "DENIED_PLAN_EXCLUSION"},
}
SIGNAL_WORK = {sig: key for key, sigs in _WORK_OF.items() for sig in sigs}
# A whole claim denied takes the work its reason needs: fixed and sent again when the detector says so, else by the
# reason (the same groups detect.py uses for lines), else a person checks it first.
CLAIM_DENIAL = "CLAIM_DENIED_UNPAID"
# a denial only EMA saw (no remittance on file) is worked like a whole claim denied: by its action, else by its reason
CLAIM_DENIALS = (CLAIM_DENIAL, "EMA_DENIED")
CLAIM_KIND_CARCS = {
    "auth": {"197", "198", "15", "39", "210", "243"},
    "note": {"50", "B12", "B22", "167", "56", "A1", "B5", "272", "58", "216", "150", "186", "151", "119", "222", "273", "B14", "35"},
    "rule": {"97", "B10", "236", "234", "231", "M15"},
}
WORK_SIGNALS = set(SIGNAL_WORK) | set(CLAIM_DENIALS)

# What each finding is, in plain words, and the next step. Reason codes stay on the finding as detail.
SIGNAL_LABELS: dict[str, tuple[str, str]] = {
    "DOWNCODE_SVC06": ("Visit paid at a lower level than billed", "Appeal with the visit note for the level billed."),
    "DOWNCODE_VS_BILLED": ("Visit paid at a lower level than EMA shows was billed", "Check in EMA which code went out. If the higher level was sent, appeal with the visit note."),
    "DOWNCODE_CHARGE": ("Charged for a higher visit level than the code paid", "Check in EMA which code was sent. If it was the higher level, appeal with the visit note."),
    "DOWNCODE_LEVEL_REDUCED": ("Visit payment cut for its level, code left unchanged", "Appeal with the visit note showing the level billed."),
    "DOWNCODE_RATE": ("Visit paid at a lower level's rate", "Ask the payer why. If it is a level cut, appeal with the visit note."),
    "DOWNCODE_LINE_SWAP": ("Visit denied and a lower level paid instead", "Appeal with the visit note for the level billed."),
    "DOWNCODE_LEVEL_DENIED": ("Visit denied because of its level", "Appeal with the visit note; if it supports a lower level, resubmit at that level."),
    "CODE_CHANGED": ("Payer changed a procedure code", "Compare the note with both codes. If yours was right, appeal; if the payer's was, let it go and change how it is billed."),
    "EM_BUNDLED_NO_MODIFIER": ("Visit not paid: billed with a procedure and no modifier 25", "If the note shows a separate problem, add 25 and resubmit as a corrected claim."),
    "EM_BUNDLED_MOD57_MINOR": ("Visit not paid: modifier 57 used with a minor procedure", "Change 57 to 25 and resubmit as a corrected claim."),
    "EM_BUNDLED_MOD57_MAJOR": ("Visit on the day of a major surgery decision not paid", "Appeal with the visit note showing the decision for surgery was made at this visit."),
    "EM_BUNDLED_MOD25": ("Visit with modifier 25 not paid: bundled into a same-day procedure", "Appeal with the visit note showing a significant, separate problem."),
    "EM_BUNDLED_MOD24": ("Visit in a post-op period denied (modifier 24)", "Appeal with the note showing the problem was unrelated to the surgery."),
    "ADDON_BUNDLED": ("Add-on code paid $0", "Appeal: add-on codes such as 17312, 11103 and 17003 are paid with their main code."),
    "PROCEDURE_BUNDLED": ("Procedure bundled into another one done the same day", "Appeal with the operative note; if the rule allows a modifier and the sites were separate, add 59 or XS and resubmit."),
    "DENIED_MISSING_MODIFIER": ("Denied: a modifier is missing or wrong", "Add or correct the modifier the remark names and resubmit as a corrected claim."),
    "DENIED_CODING": ("Denied: the code and modifier do not fit together", "Fix the code or modifier and resubmit as a corrected claim."),
    "DENIED_MISSING_PRIMARY": ("Add-on denied because its main procedure was not paid", "Work the main procedure first; once it is paid, ask the payer to reprocess the add-on."),
    "DENIED_MISSING_INFO": ("Denied: information missing", "Send what the remark asks for (a diagnosis, an NDC number, a record) and resubmit."),
    "DENIED_AUTH": ("Denied: no authorization", "Find the authorization and appeal with it, or ask for a retro-authorization. Tell the front desk."),
    "DENIED_LEVEL_OF_SERVICE": ("Procedure denied: the payer says the records do not support it", "Appeal with the procedure note and the pathology report."),
    "DENIED_MEDICAL_NECESSITY": ("Denied as not medically necessary", "Appeal with the visit note and the coverage criteria it meets."),
    "DENIED_FREQUENCY": ("Denied: too many units or too often", "If the units were right, appeal with the note; if not, resubmit with the right units."),
    "DENIED_NONCOVERED": ("Not covered by this plan", "Check benefits: send it to the right payer, bill the patient where allowed, or let it go."),
    "DENIED_TIMELY_FILING": ("Denied: sent too late", "If the clearinghouse report shows it went out on time, appeal with that proof; otherwise let it go."),
    "DENIED_PROVIDER": ("Denied: the clinician is not enrolled with this payer", "Fix the enrollment, then ask the payer to reprocess."),
    "DENIED_DUPLICATE_UNPAID": ("Denied as a duplicate, but never paid", "Look for the first claim's payment; if none was ever paid, ask the payer to reprocess."),
    "DENIED_OTHER": ("Paid $0 for another reason", "Read the reason on the item, then fix and resubmit, appeal, or let it go."),
    "NO_ANSWER": ("Sent, and the payer never answered", "Look the claim up on the payer's portal or call. If the payer has no record of it, send it "
                                                        "again within the first-filing limit (see the filing window on the item); if it was "
                                                        "denied or paid, get the remittance and work it here."),
    "REJECTED_PRE_PAYER": ("Never reached the payer: rejected, or still in EMA's print batch", "Correct it in EMA, or print it, and send it before the date shown."),
    "CLAIM_DENIED_UNPAID": ("Whole claim denied and never paid", "Read the reason: fix and resubmit if it names an error, appeal if not, let it go if the payer is right."),
    "UNDERPAID_VS_OWN_RATE": ("Paid less than this payer usually pays", "Ask the payer to reprocess at its usual rate; one request can list every claim for the code."),
    "SECOND_RATE": ("Payer pays a second, lower amount for this code", "Ask the payer once about all the claims at this amount, giving the likely reason shown."),
    "PROVIDER_RATE_GAP": ("Payer pays one clinician below its usual rate", "Check the clinician's credentialing and the contract, then ask the payer once about every claim listed."),
    "OLD_FEE_SCHEDULE": ("Payer paid new-year services on its old fee schedule", "Send the payer one repricing request listing every claim at its new rates."),
    "MPPR_EXCESS": ("Multiple-procedure reduction cut too deep", "Ask the payer to reprocess: add-ons are not reduced, and later procedures are cut by half, not more."),
    "FEE_SKIM": ("Payment fee taken out of a payment", "Ask the payer or its payment vendor for standard direct deposit with no fee."),
    "TAKEBACK": ("Money taken back from a later payment", "Match it to the payer's overpayment letter; if it was not owed, dispute it."),
    "CONTRACT_RATE_GAP": ("Commercial payer pays far below Medicare for a common code", "Bring it to the payer's contract review."),
    "MA_BELOW_MEDICARE": ("Medicare Advantage plan pays below Medicare", "Check the contract; if it promises a share of Medicare, ask for the difference."),
    "MPPR_POLICY": ("Payer pays third and later procedures below half", "Raise the multiple-procedure terms at contract review."),
    "CHARGE_BELOW_RATE": ("Your fee is lower than what the payer allows", "Raise your fee for this code above the highest amount any payer allows. Past claims cannot be appealed."),
    "G2211_DENIED": ("G2211 denied on most visits by this payer", "Decide whether to keep billing G2211 to this payer. From 2027 it becomes modifier B1."),
    "MIPS_ADJUSTMENT": ("Medicare quality program (MIPS) adjustment", "For information."),
    "SEQUESTRATION": ("Federal 2 percent Medicare reduction", "For information; not recoverable."),
    "NOT_SENT": ("Claim never left EMA", "Fix what holds it in EMA (or print it) and send it before the date shown."),
    "EMA_DENIED": ("Denied in EMA, no remittance on file", "Get the EOB from the payer's portal, then work the denial like any other."),
    "REMIT_NOT_POSTED": ("Paid by 835, not posted in EMA", "Find the deposit and post the payment in EMA under the trace shown."),
    "EMA_DENIAL_AS_CONTRACTUAL": ("EMA writes off this payer's denials as contractual",
                                  "Have EMA post this reason as a denial, not a contractual adjustment, so it reaches the denial queue."),
    "FORWARDED_TO_CARRIER": ("Sent on by the payer to another carrier", "Expect the other carrier's payment; ask it for the claim if none comes by the follow-up date."),
    "DENIED_REFERRAL": ("Denied: no referral on file", "Get the referral from the patient's primary care doctor, then send the claim again."),
    "DENIED_COVERAGE_DATES": ("Denied: not covered on the date of service", "Check the patient's coverage on that date and bill the plan that covered it, else the patient."),
    "DENIED_PLAN_EXCLUSION": ("Denied: the plan excludes this service", "Check the plan's exclusion; bill the patient only if the plan allows it (never a Medicaid member)."),
    "DENIED_CONTRACT_RATE": ("Denied: no contracted rate for this clinician", "Ask the payer to load the clinician on the agreement, then have the claim paid at the contracted rate."),
    "SECONDARY_NOT_BILLED": ("Second payer never billed", "Send the claim to the second payer with the first payer's remittance."),
    "CREDIT_BALANCE": ("Insurance paid more than was owed", "Decide with the owner: refund the payer, or let it recoup. Medicare credits are due back within 60 days."),
}
STATUS_LABELS = {"open": "To do", "in_progress": "Started", "appealed": "Sent", "resolved": "Paid", "lost": "Lost", "dismissed": "Let go"}
# The same findings in six words or fewer, for list rows and the recurring causes; the full label above is the tooltip and
# the item's title.
SHORT_LABELS: dict[str, str] = {
    "DOWNCODE_SVC06": "Paid at a lower level", "DOWNCODE_VS_BILLED": "Paid lower than EMA shows",
    "DOWNCODE_CHARGE": "Charged a higher level", "DOWNCODE_LEVEL_REDUCED": "Cut for its level", "DOWNCODE_RATE": "Paid a lower level's rate",
    "DOWNCODE_LINE_SWAP": "Lower level paid instead", "DOWNCODE_LEVEL_DENIED": "Denied for its level", "CODE_CHANGED": "Payer changed the code",
    "EM_BUNDLED_NO_MODIFIER": "Visit bundled, no modifier 25", "EM_BUNDLED_MOD57_MINOR": "Modifier 57 on a minor procedure",
    "EM_BUNDLED_MOD57_MAJOR": "Visit with 57 bundled into surgery", "EM_BUNDLED_MOD25": "Visit with modifier 25 not paid",
    "EM_BUNDLED_MOD24": "Post-op visit denied", "ADDON_BUNDLED": "Add-on code paid $0", "PROCEDURE_BUNDLED": "Procedure bundled into another",
    "DENIED_MISSING_MODIFIER": "Modifier missing or wrong", "DENIED_CODING": "Code or modifier combination",
    "DENIED_MISSING_PRIMARY": "Add-on without its main code", "DENIED_MISSING_INFO": "Information missing on the claim",
    "DENIED_AUTH": "No authorization", "DENIED_LEVEL_OF_SERVICE": "Records do not support it", "DENIED_MEDICAL_NECESSITY": "Medical necessity",
    "DENIED_FREQUENCY": "Frequency or units", "DENIED_NONCOVERED": "Not covered by the plan", "DENIED_TIMELY_FILING": "Filed too late",
    "DENIED_PROVIDER": "Clinician not enrolled", "DENIED_DUPLICATE_UNPAID": "Called a duplicate, never paid", "DENIED_OTHER": "Paid $0, other reason",
    "CLAIM_DENIED_UNPAID": "Whole claim denied", "NO_ANSWER": "No answer from the payer", "REJECTED_PRE_PAYER": "Never reached the payer", "UNDERPAID_VS_OWN_RATE": "Below the payer's usual rate", "SECOND_RATE": "Payer's second, lower rate",
    "MPPR_EXCESS": "Multiple-procedure cut too deep", "FEE_SKIM": "Payment fee", "TAKEBACK": "Money taken back",
    "CONTRACT_RATE_GAP": "Far below Medicare", "MA_BELOW_MEDICARE": "Below Medicare", "MPPR_POLICY": "Later procedures cut below half",
    "PROVIDER_RATE_GAP": "One clinician paid below rate", "OLD_FEE_SCHEDULE": "Paid on last year's fee schedule",
    "CHARGE_BELOW_RATE": "Your fee below the payer's rate", "G2211_DENIED": "G2211 usually denied", "MIPS_ADJUSTMENT": "MIPS adjustment",
    "SEQUESTRATION": "Federal 2 percent reduction", "NOT_SENT": "Not sent from EMA", "EMA_DENIED": "EMA denial, no 835",
    "REMIT_NOT_POSTED": "Payment not posted", "EMA_DENIAL_AS_CONTRACTUAL": "Denials posted as contractual",
    "FORWARDED_TO_CARRIER": "Sent on to another carrier", "SECONDARY_NOT_BILLED": "Second payer never billed",
    "DENIED_REFERRAL": "No referral", "DENIED_COVERAGE_DATES": "Not covered that day", "DENIED_PLAN_EXCLUSION": "Plan excludes it",
    "DENIED_CONTRACT_RATE": "No contracted rate",
    "CREDIT_BALANCE": "Credit to refund or recoup",
}


def work_type(signal: str | None, actionable: str | None, carc: str | None = None) -> str:
    if signal in CLAIM_DENIALS:
        if actionable == "correct_and_resubmit":
            return "fix"
        return next((k for k, carcs in CLAIM_KIND_CARCS.items() if (carc or "") in carcs), "check")
    if signal in SIGNAL_WORK:
        return SIGNAL_WORK[signal]
    return {"correct_and_resubmit": "fix", "appeal": "rule"}.get(actionable or "", "check")


def kind_sql(kind: str) -> tuple[str, list]:
    """work_type as a condition on the findings table, so a list filtered by kind holds what the counts counted."""
    parts, params = [], []
    named = sorted(sg for sg, k in SIGNAL_WORK.items() if k == kind)
    if named:
        parts.append(f"signal IN ({','.join('?' * len(named))})")
        params += named
    whole = "signal IN (" + ",".join(f"'{x}'" for x in CLAIM_DENIALS) + ")"
    if kind == "fix":
        parts.append(f"({whole} AND actionable='correct_and_resubmit')")
    elif kind in CLAIM_KIND_CARCS:
        cs = sorted(CLAIM_KIND_CARCS[kind])
        parts.append(f"({whole} AND COALESCE(actionable,'')!='correct_and_resubmit' AND carc IN ({','.join('?' * len(cs))}))")
        params += cs
    elif kind == "check":
        cs = sorted(set().union(*CLAIM_KIND_CARCS.values()))
        parts.append(f"({whole} AND COALESCE(actionable,'')!='correct_and_resubmit' AND COALESCE(carc,'') NOT IN ({','.join('?' * len(cs))}))")
        params += cs
    fallback = {"fix": "actionable='correct_and_resubmit'", "rule": "actionable='appeal'",
                "check": "COALESCE(actionable,'') NOT IN ('correct_and_resubmit','appeal')"}.get(kind)
    if fallback:            # a signal named nowhere goes by its action
        known = sorted(WORK_SIGNALS)
        parts.append(f"(COALESCE(signal,'') NOT IN ({','.join('?' * len(known))}) AND {fallback})")
        params += known
    return ("(" + " OR ".join(parts) + ")") if parts else "0", params


def utc_today() -> str:
    """Today as the rest of the dashboard sees it: the practice's day (runtime.practice_today, REMITWATCH_TZ), so a window
    whose last day this is stays open until midnight where the practice is. The name is older than that rule."""
    from . import runtime
    return runtime.practice_today()


def claim_windows(conn: sqlite3.Connection, statuses: tuple) -> dict[str, list[tuple]]:
    """Each routine-rate finding's claims as (deadline, cents): such a finding stands for many claims with their own
    filing dates, and every count here takes its claims by their own dates (detect.pattern_windows)."""
    from .detect import pattern_windows
    out: dict[str, list[tuple]] = defaultdict(list)
    try:
        for fid, _g, _c, d, n, cents in pattern_windows(conn, statuses):
            out[fid].append((d, cents or 0, n or 0))
    except sqlite3.Error:
        pass
    return out


def _in(windows: list[tuple], lo: str, hi: str | None = None) -> tuple[int, int]:
    """How many of a pattern's claims, and their cents, have a deadline from lo up to (not including) hi."""
    got = [(c, n) for d, c, n in windows if d and d >= lo and (hi is None or d < hi)]
    return sum(n for _c, n in got), sum(c for c, _n in got)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _tables(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _meta(conn: sqlite3.Connection, key: str):
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None
    except (sqlite3.Error, ValueError, TypeError):
        return None


def newest_payment(conn: sqlite3.Connection) -> str | None:
    """The newest payment date in the store that is a day on the calendar: one no calendar has (BPR16 20270229, kept as
    the file wrote it) is skipped for the real one before it."""
    if "transactions" not in _tables(conn):
        return None
    from .detect import real_day
    for (d,) in conn.execute("""SELECT DISTINCT payment_date FROM transactions WHERE COALESCE(superseded,0)=0
                                AND payment_date IS NOT NULL ORDER BY 1 DESC"""):
        if real_day(d):
            return d
    return None


def feed_day(conn: sqlite3.Connection, today: str) -> str | None:
    """The day the remittances reach for most payers (detect.feed_date), never after `today`: one stray newer file does not
    move it. Worked out from the store on a server. A serverless host's copy holds only the lines behind items, so there it
    is the date the last analysis worked out on the full store, else the newest payment that is a day on the calendar.
    What the fax hold (api._feed_day), the Data tab's feed check and the warning line on the pages all go by."""
    from . import runtime
    from .detect import FEED_META, feed_date, real_day
    if "transactions" not in _tables(conn):
        return None
    if not runtime.serverless():
        return feed_date(conn, today)
    saved = _meta(conn, FEED_META)
    d = saved.get("date") if isinstance(saved, dict) else None
    if not real_day(d):         # no analysis saved it (or saved a day no calendar has, before that was checked)
        d = newest_payment(conn)
    return min(str(d)[:10], today) if d else None


def feed_hole(conn: sqlite3.Connection, today: str) -> dict | None:
    """The weeks with no remittance from any payer that the day the remittances reach stops at (detect.feed_gap: "from",
    "to", "newest"), else None. Worked out from the store on a server; on a serverless host's copy, the hole the last
    analysis found on the full store (saved with FEED_META beside the date feed_day reads). What the fax hold
    (api._feed_gap) and the Data tab's feed check name (misuse check CH-6)."""
    from . import runtime
    from .detect import FEED_META, feed_gap, real_day
    if "transactions" not in _tables(conn):
        return None
    if not runtime.serverless():
        return feed_gap(conn, today)
    saved = _meta(conn, FEED_META)
    gap = saved.get("gap") if isinstance(saved, dict) else None
    if not isinstance(gap, dict) or not all(real_day(gap.get(k)) for k in ("from", "to", "newest")):
        return None             # none saved (no hole, or an analysis before this release)
    return {k: str(gap[k])[:10] for k in ("from", "to", "newest")}


def work(conn: sqlite3.Connection, today: str) -> dict:
    """Work still to do (not yet sent to the payer) inside its filing window, by the kind of work it takes, with what
    falls due in 7 and 30 days, and what is sent and waiting on the payer. A routine-rate pattern counts its claims by
    their own dates: it is in window while one claim is, with the cents of those claims."""
    soon7 = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    soon30 = (date.fromisoformat(today) + timedelta(days=30)).isoformat()
    wins = claim_windows(conn, TODO)
    out = {t["key"]: dict(t, n=0, cents=0, due7_n=0, due7_cents=0, due30_n=0, due30_cents=0, working_n=0) for t in WORK_TYPES}
    expired_n = expired_cents = small_n = small_cents = 0
    for fid, sig, act, carc, status, dl, cents in conn.execute(
            f"""SELECT finding_id, signal, actionable, carc, status, appeal_deadline, COALESCE(shortfall_cents,0) FROM findings
                WHERE status IN ({','.join('?' * len(TODO))}) AND COALESCE(stale,0)=0 AND appeal_deadline IS NOT NULL
                  AND category IN ('denial','bundling','downcoding','underpayment')""", TODO):
        if fid in wins:
            n_in, c_in = _in(wins[fid], today)
            n7, c7 = _in(wins[fid], today, soon7)
            n30, c30 = _in(wins[fid], today, soon30)
            gone = [c for d, c, _n in wins[fid] if d and d < today]
            if gone:
                expired_n += 1
                expired_cents += sum(gone)
            if not n_in:
                continue
            cents, d7, d30 = c_in, (1, c7) if n7 else (0, 0), (1, c30) if n30 else (0, 0)
        elif dl < today:
            expired_n += 1
            expired_cents += cents
            continue
        else:
            d7 = (1, cents) if dl < soon7 else (0, 0)
            d30 = (1, cents) if dl < soon30 else (0, 0)
        w = out[work_type(sig, act, carc)]
        w["n"] += 1
        w["cents"] += cents
        w["working_n"] += status != "open"
        if fid not in wins and 0 < cents < SMALL_CENTS:
            small_n += 1
            small_cents += cents
        w["due7_n"] += d7[0]
        w["due7_cents"] += d7[1]
        w["due30_n"] += d30[0]
        w["due30_cents"] += d30[1]
    types = [out[t["key"]] for t in WORK_TYPES if out[t["key"]]["n"]]
    total = {k: sum(t[k] for t in types) for k in ("n", "cents", "due7_n", "due7_cents", "due30_n", "due30_cents", "working_n")}
    sent = conn.execute("""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE status='appealed' AND COALESCE(stale,0)=0
                           AND category IN ('denial','bundling','downcoding','underpayment')""").fetchone()
    undated = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE status IN ({','.join('?' * len(TODO))})
                               AND COALESCE(stale,0)=0 AND appeal_deadline IS NULL
                               AND category IN ('denial','bundling','downcoding','underpayment')""", TODO).fetchone()
    return {"types": types, "total": total, "expired_n": expired_n, "expired_cents": expired_cents, "sent_n": sent[0], "sent_cents": sent[1],
            "undated_n": undated[0], "undated_cents": undated[1], "small_n": small_n, "small_cents": small_cents, "small_under": SMALL_CENTS}


def recovered(conn: sqlite3.Connection, today: str) -> dict:
    """What came back: findings marked recovered, by a person or by a later payment, in the last 30 days and this year."""
    d30 = (date.fromisoformat(today) - timedelta(days=30)).isoformat()
    year = today[:4]
    r = conn.execute("""SELECT COALESCE(SUM(CASE WHEN COALESCE(recovered_date, status_changed_at, last_seen) >= ? THEN recovered_cents END),0),
                               COALESCE(SUM(CASE WHEN COALESCE(recovered_date, status_changed_at, last_seen) >= ? THEN 1 END),0),
                               COALESCE(SUM(CASE WHEN substr(COALESCE(recovered_date, status_changed_at, last_seen),1,4) = ? THEN recovered_cents END),0)
                        FROM findings WHERE status='resolved' AND COALESCE(recovered_cents,0) > 0 AND COALESCE(stale,0)=0
                          AND category IN ('denial','bundling','downcoding','underpayment')""", (d30, d30, year)).fetchone()
    return {"last30_cents": r[0], "last30_n": r[1], "year_cents": r[2], "year": year}


def contract_levers(conn: sqlite3.Connection, newest: str | None) -> dict:
    """Each commercial and Medicare Advantage payer's usual office rates against what Medicare pays the practice for the
    same codes, over the last full year of volume: the payer's mode times each code's lines, against Medicare's. Only
    codes with at least 20 clean lines carry a rate. A payer below Medicare is a contract question, not an appeal."""
    if not newest or not {"payer_rates", "exposure"} <= _tables(conn):
        return {"year": None, "payers": []}
    year = str(int(newest[:4]) - 1)        # payments for December's visits arrive into the next year: the newest year is never full
    rates = {}
    for r in conn.execute("""SELECT payer_group, code, tier, mode_cents, medicare_cents FROM payer_rates
                             WHERE year=? AND pos_class='office' AND n >= 20 AND medicare_cents > 0 AND mode_cents > 0""", (year,)):
        rates[(r[0], r[1], r[2])] = (r[3], r[4])
    agg: dict[str, dict] = {}
    for grp, kind, code, tier, lines in conn.execute("""SELECT payer_group, payer_kind, code, tier, SUM(lines) FROM exposure
                                                      WHERE substr(month,1,4)=? AND pos_class='office' AND payer_kind IN ('commercial','medicare_advantage')
                                                      GROUP BY 1,2,3,4""", (year,)):
        rt = rates.get((grp, code, tier))
        if not rt or not lines:
            continue
        a = agg.setdefault(grp, {"payer_group": grp, "payer_kind": kind, "lines": 0, "payer_cents": 0, "medicare_cents": 0, "em_payer": 0, "em_medicare": 0})
        a["lines"] += lines
        a["payer_cents"] += rt[0] * lines
        a["medicare_cents"] += rt[1] * lines
        if code.startswith("992"):
            a["em_payer"] += rt[0] * lines
            a["em_medicare"] += rt[1] * lines
    payers = []
    for a in agg.values():
        if a["lines"] < 200 or not a["medicare_cents"]:
            continue
        payers.append({"payer_group": a["payer_group"], "payer_kind": a["payer_kind"], "lines": a["lines"],
                       "pct_of_medicare": round(a["payer_cents"] / a["medicare_cents"], 3),
                       "visit_pct_of_medicare": round(a["em_payer"] / a["em_medicare"], 3) if a["em_medicare"] else None,
                       "gap_cents": a["medicare_cents"] - a["payer_cents"]})
    payers.sort(key=lambda p: -p["gap_cents"])
    return {"year": year, "payers": payers}


def fee_months(newest: str | None) -> tuple[str | None, str | None]:
    """The 12 full months before the newest payment's month (a month that is still being paid is not a full one)."""
    if not newest:
        return None, None
    y, m = int(newest[:4]), int(newest[5:7])
    end = (y, m - 1) if m > 1 else (y - 1, 12)
    start = (end[0] - 1, end[1] + 1) if end[1] < 12 else (end[0], 1)
    return f"{start[0]:04d}-{start[1]:02d}", f"{end[0]:04d}-{end[1]:02d}"


def fee_levers(conn: sqlite3.Connection, newest: str | None = None) -> dict:
    """Fees set below what a payer allows (CHARGE_BELOW_RATE), one row per code: what it left unpaid in all, the fee in
    the newest year and the highest amount a capped payer allowed that year (raise the fee above it). And payment-vendor
    fees (FEE_SKIM) still open, by payer, with the months they cover and the last 12 full months."""
    by: dict[str, dict] = {}
    for r in _rows(conn, """SELECT procedure_code code, payer_group, year, COALESCE(shortfall_cents,0) cents, evidence FROM findings
                            WHERE signal='CHARGE_BELOW_RATE' AND COALESCE(stale,0)=0 AND procedure_code IS NOT NULL
                              AND status IN ('open','in_progress')"""):
        try:
            ev = json.loads(r["evidence"] or "{}")
        except (TypeError, ValueError):
            ev = {}
        c = by.setdefault(r["code"], {"code": r["code"], "cents": 0, "lines": 0, "payers": set(), "first_year": r["year"], "year": r["year"],
                                      "charge_cents": None, "top_rate_cents": None})
        c["cents"] += r["cents"]
        c["lines"] += int(ev.get("lines") or 0)
        c["payers"].add(r["payer_group"])
        c["first_year"] = min(c["first_year"] or r["year"], r["year"] or c["first_year"])
        if (r["year"] or "") > (c["year"] or ""):
            c["year"], c["charge_cents"], c["top_rate_cents"] = r["year"], None, None
        if r["year"] == c["year"]:
            if ev.get("charge_cents"):
                c["charge_cents"] = max(c["charge_cents"] or 0, int(ev["charge_cents"]))
            if ev.get("payer_rate_cents"):
                c["top_rate_cents"] = max(c["top_rate_cents"] or 0, int(ev["payer_rate_cents"]))
    caps = sorted((dict(c, payers=sorted(p for p in c["payers"] if p)) for c in by.values()), key=lambda c: -c["cents"])
    lo, hi = fee_months(newest)
    fees = _rows(conn, """SELECT payer_group, SUM(shortfall_cents) cents, COUNT(DISTINCT month) months, MIN(month) first_month, MAX(month) last_month,
                                 COALESCE(SUM(CASE WHEN month >= ? AND month <= ? THEN shortfall_cents END),0) cents12
                          FROM findings WHERE signal='FEE_SKIM' AND COALESCE(stale,0)=0 AND status != 'dismissed'
                          GROUP BY payer_group ORDER BY cents DESC""", (lo or "", hi or ""))
    return {"charge_caps": caps, "charge_cap_cents": sum(c["cents"] for c in caps), "payment_fees": fees,
            "payment_fee_cents": sum(f["cents"] or 0 for f in fees),
            "payment_fee_since": min((f["first_month"] for f in fees if f.get("first_month")), default=None),
            "payment_fee12_cents": sum(f["cents12"] or 0 for f in fees), "payment_fee12_payers": sum(1 for f in fees if f["cents12"]),
            "payment_fee12_from": lo, "payment_fee12_to": hi}


# The causes that keep coming back, in the same short words the list rows use
CAUSES = {sig: SHORT_LABELS[sig] for sig in (
    "DENIED_AUTH", "DENIED_MISSING_INFO", "DENIED_MISSING_MODIFIER", "DENIED_CODING", "DENIED_NONCOVERED", "DENIED_MEDICAL_NECESSITY",
    "DENIED_FREQUENCY", "DENIED_DUPLICATE_UNPAID", "DENIED_TIMELY_FILING", "DENIED_PROVIDER", "DENIED_MISSING_PRIMARY", "CLAIM_DENIED_UNPAID",
    "EM_BUNDLED_MOD25", "EM_BUNDLED_NO_MODIFIER", "EM_BUNDLED_MOD57_MINOR", "PROCEDURE_BUNDLED", "EM_BUNDLED_MOD57_MAJOR", "ADDON_BUNDLED",
    "EM_BUNDLED_MOD24")}


def denial_causes(conn: sqlite3.Connection, newest: str | None) -> list[dict]:
    """The causes that keep coming back, by payment date: the last 90 days against the 90 before. Prevention, not
    appeals: each is a habit or a front-desk step to change."""
    if not newest:
        return []
    end = date.fromisoformat(newest[:10])
    a, b = (end - timedelta(days=90)).isoformat(), (end - timedelta(days=180)).isoformat()
    out = []
    for sig, label in CAUSES.items():
        r = conn.execute("""SELECT SUM(CASE WHEN payment_date > ? THEN 1 ELSE 0 END), SUM(CASE WHEN payment_date > ? THEN shortfall_cents ELSE 0 END),
                                   SUM(CASE WHEN payment_date > ? AND payment_date <= ? THEN 1 ELSE 0 END),
                                   SUM(CASE WHEN payment_date > ? AND payment_date <= ? THEN shortfall_cents ELSE 0 END)
                            FROM findings WHERE signal=? AND COALESCE(stale,0)=0""", (a, a, b, a, b, a, sig)).fetchone()
        n90, c90, nprev, cprev = (r[0] or 0), (r[1] or 0), (r[2] or 0), (r[3] or 0)
        if n90 or nprev:
            out.append({"signal": sig, "label": label, "work": work_type(sig, None), "n90": n90, "cents90": c90, "n_prev": nprev, "cents_prev": cprev})
    out.sort(key=lambda x: -x["cents90"])
    return out


_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                 "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "eighteen": 18, "twenty": 20, "thirty": 30, "forty": 40,
                 "forty five": 45, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def _unit_days(unit: str) -> int:
    return {"d": 1, "w": 7, "m": 30}.get(unit[:1], 365)


def password_days(value: str | None) -> int | None:
    """How long a password lasts, in days, however TriZetto's answer puts it: '90', '90 days', '8 weeks', '6 months',
    '1 year', '1 yr', 'six months', 'annually', 'quarterly', 'twice a year'. A number goes with the unit right after it,
    and a day count wins over another unit ('365 days (1 year)' is 365). 0 (no reminder) for nothing, 0 or 'never'; None
    when it cannot be read (the data checks then say so)."""
    import re
    v = " ".join(re.sub(r"[-_/,;()]", " ", (value or "").lower()).split())
    if not v:
        return 0
    if re.search(r"\bnever\b|\bnone\b|\bno expir|\bnot expire|n't expire", v):
        return 0
    if re.search(r"\bsemi ?annual|\btwice (a|per) year|\bhalf (a )?year|\bevery other quarter", v):
        return 182
    if re.search(r"\bbi ?(annual|ennial|yearly)", v):
        return None                     # twice a year, or every two years: ask
    if re.search(r"\bquarter", v):
        return 90
    for word, n in sorted(_NUMBER_WORDS.items(), key=lambda kv: -len(kv[0])):
        v = re.sub(rf"\b{word}\b", str(n), v)
    units = r"(days?|d|weeks?|wks?|w|months?|mos?|mths?|years?|yrs?|y)"
    pairs = re.findall(rf"(\d+) ?{units}\b", v)
    if pairs:
        days = [int(n) for n, unit in pairs if unit.startswith("d")]
        return days[0] if days else int(pairs[0][0]) * _unit_days(pairs[0][1])
    if re.fullmatch(r"\d+", v):
        return int(v)
    number = re.search(r"\d+", v)
    for unit in ("day", "week", "month", "year"):       # a number and a unit apart ("90 calendar days")
        if re.search(rf"\b{unit}s?\b", v):
            return (int(number.group(0)) if number else 1) * _unit_days(unit)
    if re.search(r"annual|yearly", v):
        return 365
    if re.search(r"monthly", v):
        return 30
    if re.search(r"weekly", v):
        return 7
    return None


def _who_enters() -> tuple[str, str]:
    """Who puts a new TriZetto password into this site, and how: the server's configure.sh, or Vercel's settings when a
    serverless host reads TriZetto itself (trizetto.here)."""
    from . import runtime
    if runtime.serverless():
        return ("whoever looks after the site in Vercel, who replaces TRIZETTO_SFTP_PASSWORD there (and TRIZETTO_SFTP_PASSWORD_SET, "
                "the day) and redeploys", "in Vercel, then redeploy")
    return "whoever runs the server, who enters it with configure.sh", "with configure.sh"


def trizetto_health(conn: sqlite3.Connection, today: str, env: dict | None = None) -> list[dict]:
    """The TriZetto feed's sign-in, which nothing else watches: a sign-in TriZetto refused (the feed holds off for a day so
    the login is never locked: remitwatch.trizetto), and a password about to expire (TriZetto makes it expire, every 60
    days to a year by vendors' accounts). Changed late, the feed stops. Said only by the site that reads TriZetto
    (trizetto.here: the server, unless a serverless host is switched on for it), and in line with docs/TRIZETTO_REQUEST.md:
    requests to TriZetto in writing, passwords never by email, the Site ID's own password never touched for RemitWatch."""
    import os

    from . import trizetto
    e = os.environ if env is None else env
    if not trizetto.configured(e):
        return []
    enters, how = _who_enters()
    out = []
    held = _meta(conn, trizetto.REFUSED_META)
    test_once = ("Whoever runs the site: press Test the TriZetto connection on /setup once. It says whether the feed is waiting, "
                 "and until when.")
    reset = ("the TriZetto administrator asks TriZetto in writing (the portal's Contact Us) to reset the RemitWatch login's "
             "password. Change it yourself only on a page that shows the RemitWatch login's username, never the Site ID's. "
             f"The new password goes by phone or in person, never by email, to {enters}; it is tried at once. If the feed uses "
             "the Site ID's own login (shared with ModMed), reset nothing: get the current password from whoever changed it, "
             "with ModMed.")
    # the day of the refusal where the practice is (the hold keeps a UTC time): one at 9 PM Eastern is that day's
    from .runtime import practice_date
    day = practice_date(str((held or {}).get("at") or ""))
    if held and held.get("at") and held.get("unclear"):
        out.append({"key": "trizetto_signin", "ok": False, "at": day,
                    "title": f"TriZetto's sign-in did not finish twice in a row on {day}",
                    "detail": "The feed waits 6 hours before it tries again, so the login is never locked. TriZetto may have "
                              "been down. If it keeps happening, the password may be refused without a clear answer.",
                    "who": f"{test_once} If it says TriZetto refused the sign-in, {reset}"})
    elif held and held.get("at") and held.get("kind") == "method":
        out.append({"key": "trizetto_signin", "ok": False, "at": day,
                    "title": f"TriZetto would not take a password for the feed's login on {day}",
                    "detail": "TriZetto's server asked for another way to sign in, so no remittances come straight from TriZetto. "
                              "The feed tries again once a day.",
                    "who": "TriZetto administrator: ask TriZetto in writing (the portal's Contact Us) for the RemitWatch login to "
                           "sign in with a password. Change nothing in the portal."})
    elif held and held.get("at"):
        # "the password" only when TriZetto refused a password sent by the password method (trizetto.SignInRefused.kind)
        what = "password" if held.get("kind") == "password" else "sign-in"
        out.append({"key": "trizetto_signin", "ok": False, "at": day,
                    "title": f"TriZetto refused the feed's {what} on {day}",
                    "detail": "No remittances come straight from TriZetto until it is fixed. The feed tries the same password only "
                              "once a day, so the login is never locked.",
                    "who": f"{test_once} If TriZetto still refuses it, {reset}"})
    raw = (e.get("TRIZETTO_SFTP_PASSWORD_DAYS") or "").strip()
    days = password_days(raw)
    if days is None:
        out.append({"key": "trizetto_password", "ok": False,
                    "title": "How long TriZetto's SFTP password lasts could not be read",
                    "detail": f"TRIZETTO_SFTP_PASSWORD_DAYS is \"{raw[:40]}\", which is not a number of days, so no reminder "
                              "comes before the password expires.",
                    "who": f"Whoever runs the site: enter it again as a number of days (0 if it never expires) {how}."})
        return out
    try:
        set_on = date.fromisoformat((e.get("TRIZETTO_SFTP_PASSWORD_SET") or "")[:10])
    except ValueError:
        set_on = None
    if set_on and days > 0:
        due = set_on + timedelta(days=days)
        left = (due - date.fromisoformat(today)).days
        if left <= 20:
            out.append({"key": "trizetto_password", "ok": False, "due": due.isoformat(), "days_left": left,
                        "title": (f"TriZetto's SFTP password expires on {due.isoformat()}" if left >= 0 else
                                  f"TriZetto's SFTP password expired on {due.isoformat()}"),
                        "detail": "Change it before then: the remittance feed stops on the old one.",
                        "who": "TriZetto administrator: ask TriZetto in writing (the portal's Contact Us) for a new password for "
                               "the RemitWatch login, or set it yourself only on a page that shows the RemitWatch login's username, "
                               f"never the Site ID's. Give it by phone or in person, never by email, to {enters}, the same day. "
                               "If the feed uses the Site ID's own login (shared with ModMed), change it only together with "
                               "ModMed, the same day."})
    return out


# A send week stands out (a batch that may never have reached the payers, the rest of trace finding T-N13) when at least
# LOST_BATCH_MIN of its claims are never answered, LOST_BATCH_SHARE of the week's claims at least, and LOST_BATCH_TIMES the
# usual share of the other weeks.
LOST_BATCH_MIN = 5
LOST_BATCH_SHARE = 0.25
LOST_BATCH_TIMES = 3


def unanswered_weeks(conn: sqlite3.Connection) -> dict:
    """The claims listed as never answered (NO_ANSWER items still to work) whose send the 837s show, by the week of that
    send (the item's evidence 'sent_837'), against the claims the 837s show sent that week (detect.WAITING_837_META's
    sent_by_week): {"weeks": [{"week", "unanswered", "cents", "sent", "share", "payers": [[group, n]]}], "usual_share",
    "stand_out": [the weeks that stand out]}. Both sides count the same claims, the 837s' sends: a claim no 837 carried
    (EMA's exports alone date it) is in neither, so a week never shows more claims unanswered than sent (fx-837-r2-7).
    Reads only the findings and the meta, so the dashboard's copy shows it too."""
    from .detect import WAITING_837_META, week_of
    from . import runlog
    meta = _meta(conn, WAITING_837_META) or {}
    sent = meta.get("sent_by_week") if isinstance(meta, dict) else None
    sent = sent if isinstance(sent, dict) else {}
    weeks: dict[str, dict] = {}
    try:
        rows = conn.execute("""SELECT evidence, shortfall_cents, payer_group FROM findings WHERE signal = 'NO_ANSWER'
                               AND COALESCE(stale, 0) = 0 AND status NOT IN ('resolved', 'lost', 'dismissed')""").fetchall()
    except sqlite3.Error as exc:
        runlog.caught(exc, "insights.unanswered_weeks")
        rows = []
    for ev, cents, grp in rows:
        try:
            s837 = (json.loads(ev or "{}") or {}).get("sent_837")
            day = s837.get("day") if isinstance(s837, dict) else None
        except (ValueError, TypeError) as exc:
            runlog.caught(exc, "insights.unanswered_weeks")
            day = None
        wk = week_of(day)
        if not wk:
            continue
        w = weeks.setdefault(wk, {"week": wk, "unanswered": 0, "cents": 0, "payers": defaultdict(int)})
        w["unanswered"] += 1
        w["cents"] += int(cents or 0)
        w["payers"][grp or "Unknown"] += 1
    out = []
    for wk in sorted(weeks):
        w = weeks[wk]
        n_sent = int(sent.get(wk) or 0)
        out.append({"week": wk, "unanswered": w["unanswered"], "cents": w["cents"], "sent": n_sent or None,
                    "share": round(w["unanswered"] / n_sent, 3) if n_sent else None,
                    "payers": sorted(([g, n] for g, n in w["payers"].items()), key=lambda x: (-x[1], x[0]))[:3]})
    shares = sorted(w["share"] for w in out if w["share"] is not None)
    usual = shares[len(shares) // 2] if shares else None
    stand = [w for w in out if w["share"] is not None and w["unanswered"] >= LOST_BATCH_MIN and w["share"] >= LOST_BATCH_SHARE
             and (usual is None or w["share"] >= LOST_BATCH_TIMES * usual)]
    return {"weeks": out, "usual_share": usual, "stand_out": stand}


def _waiting_837(conn: sqlite3.Connection) -> list[dict]:
    """The claims sent that no payer has answered yet and that are not due yet (detect.WAITING_837_META): on no list until
    their due day, so the Data tab says how many, their dollars and when they come due (blindness is loud)."""
    from .detect import WAITING_837_META
    w = _meta(conn, WAITING_837_META) or {}
    if not isinstance(w, dict) or not w.get("n"):
        return []
    n, cents = int(w.get("n") or 0), int(w.get("billed_cents") or 0)
    groups = sorted(((g, v) for g, v in (w.get("by_group") or {}).items() if isinstance(v, dict)),
                    key=lambda gv: (-int(gv[1].get("billed_cents") or 0), gv[0]))
    by = "; ".join(f"{g}: {int(v.get('n') or 0)} (${int(v.get('billed_cents') or 0) / 100:,.2f}, the first due {v.get('first_due')})"
                   for g, v in groups[:5]) + ("; and more" if len(groups) > 5 else "")
    return [{"key": "waiting_837", "ok": True, "n": n, "cents": cents, "first_due": w.get("first_due"), "last_due": w.get("last_due"),
             "by_group": dict(groups), "by_due_week": w.get("by_due_week") or {},
             "title": f"{n} {'claim' if n == 1 else 'claims'} sent {'is' if n == 1 else 'are'} waiting for the payer's answer "
                      f"(${cents / 100:,.2f} billed)",
             "detail": (f"{'It is' if n == 1 else 'They are'} not on any list until {'it comes' if n == 1 else 'they come'} due, "
                        f"from {w.get('first_due')} to {w.get('last_due')}" + (f". By payer: {by}" if by else "") + "."),
             "who": "Nobody yet: a claim still unanswered on its due day is listed under Chase the payer that day."}]


def _unanswered_weeks_item(conn: sqlite3.Connection) -> list[dict]:
    """The send weeks whose claims went unanswered far more often than usual (unanswered_weeks): a batch that may never
    have reached the payers."""
    got = unanswered_weeks(conn)
    stand = got["stand_out"]
    if not stand:
        return []
    n = len(stand)
    words = "; ".join(f"the week of {w['week']}: {w['unanswered']} of {w['sent']} claims sent got no answer (${w['cents'] / 100:,.2f}, "
                      + ", ".join(f"{g} {k}" for g, k in w["payers"]) + ")" for w in stand[:5]) + ("; and more" if n > 5 else "")
    usual = got.get("usual_share")
    return [{"key": "unanswered_weeks", "ok": False, "n": n, "weeks": stand, "usual_share": usual,
             "title": f"{n} {'week' if n == 1 else 'weeks'} of claims sent went mostly unanswered",
             "detail": (words[:1].upper() + words[1:] + "."
                        + (f" In a usual week {usual:.0%} of the claims sent get no answer." if usual is not None else "")
                        + " A batch that never reached the payers looks like this."),
             "who": "Billing lead: open the clearinghouse's acceptance report for those weeks, and send again the claims the "
                    "payers never received."}]


def _feeds_837(conn: sqlite3.Connection) -> list[dict]:
    """What the 837 claim files show that the other feeds do not, as the last analysis saved it (detect.NO_ERA_META and
    detect.SEND_WEEKS_META, which the dashboard's copy carries): a payer whose claims never get an ERA, so that only EMA's
    postings can show its answers; weeks one feed shows claims sent and the other none (trace finding T-N13); the send
    weeks whose claims went mostly unanswered (a lost batch); and the claims sent that are not due yet (waiting)."""
    from .detect import NO_ERA_META, SEND_WEEKS_META
    out: list[dict] = _unanswered_weeks_item(conn) + _waiting_837(conn)
    for p in [p for p in _meta(conn, NO_ERA_META) or [] if isinstance(p, dict) and p.get("payer") and p.get("claims")][:10]:
        n, cents = int(p.get("claims") or 0), int(p.get("billed_cents") or 0)
        listed, ema_ans, blind = int(p.get("listed") or 0), int(p.get("answered_in_ema") or 0), int(p.get("unchecked") or 0)
        parts = []
        if ema_ans:
            parts.append(f"{ema_ans} of those due EMA shows answered")
        if listed:
            parts.append(f"{listed} EMA shows no answer to {'is' if listed == 1 else 'are'} listed as never answered")
        if blind:
            parts.append(f"{blind} no source can show an answer to {'is' if blind == 1 else 'are'} not listed")
        out.append({"key": "no_era_837", "ok": False, "payer": p["payer"], "payer_id": p.get("payer_id"), "n": n, "cents": cents,
                    "listed": listed, "answered_in_ema": ema_ans, "unchecked": blind,
                    "title": f"No ERA comes for claims sent to {p['payer']}",
                    "detail": (f"{n} {'claim' if n == 1 else 'claims'} sent since {p.get('since')} (${cents / 100:,.2f} billed), and no 835 "
                               f"for any of them: only EMA's postings can show whether {p['payer']} answered."
                               + (" " + "; ".join(parts) + "." if parts else "")),
                    "who": f"Billing lead: ask {p['payer']} (or TriZetto) about ERA enrollment, and keep posting its payments in EMA. "
                           "Check the claims not listed on its portal."})
    weeks = _meta(conn, SEND_WEEKS_META) or {}
    a = [w for w in weeks.get("ema_no_837") or [] if isinstance(w, dict) and w.get("week")] if isinstance(weeks, dict) else []
    b = [w for w in weeks.get("no_ema_837") or [] if isinstance(w, dict) and w.get("week")] if isinstance(weeks, dict) else []
    if a or b:
        def wk(ws: list[dict], what: str) -> str:
            return "; ".join(f"the week of {w['week']} ({int(w.get('claims') or 0)} {what})" for w in ws[:6]) + ("; and more" if len(ws) > 6 else "")
        said = []
        if a:
            said.append(f"EMA shows claims sent and no 837 file is on file in {wk(a, 'claims in EMA')}")
        if b:
            said.append(f"the 837 files show claims sent and EMA's claim exports show none in {wk(b, 'claims in the 837s')}")
        n = len(a) + len(b)
        out.append({"key": "send_weeks", "ok": False, "n": n, "ema_no_837": a, "no_ema_837": b,
                    "title": f"{n} {'week' if n == 1 else 'weeks'} of claims sent are in one feed and not the other",
                    "detail": ("; ".join(said)[:1].upper() + "; ".join(said)[1:] + ". A claim sent in a week with no 837 on file cannot be "
                               "dated by it, and a week EMA does not show cannot be checked against EMA."),
                    "who": "Billing lead: put the 837 claim files for those weeks in the Dropbox folder. Practice administrator: pull "
                           "Claim & Claim Status again for the weeks EMA's exports miss."})
    return out


def data_health(conn: sqlite3.Connection, newest: str | None, today: str, feed: str | None = None) -> list[dict]:
    """What in the data is hiding money, most urgent first. Each item says what to do and who does it. The feed check goes
    by the day the remittances reach for most payers (`feed`, else feed_day's), not the newest payment: one stray newer
    file, or the newest week put in after weeks that are missing, does not make the payments current."""
    items = []
    reach = (feed or feed_day(conn, today) or newest or "")[:10]
    if reach:
        age = (date.fromisoformat(today) - date.fromisoformat(reach)).days
        got = None
        try:
            got = conn.execute("SELECT MAX(ingested_at) FROM files WHERE kind IN ('835','remit','rmt')").fetchone()[0]
        except sqlite3.Error:
            pass
        # the day the last file came where the practice is (a file read at 8:30 PM Eastern came that day), as today is
        from .runtime import practice_date
        got = practice_date(str(got)) if got else None
        recent = bool(got) and (date.fromisoformat(today) - date.fromisoformat(got)).days <= 10
        later = (newest or "")[:10] > reach         # newer payments are in, from a few payers or after weeks that are missing
        # weeks with no remittance from any payer, then newer ones (misuse check CH-6): named, with what to do. Only the hole
        # this day stops at (it starts the day after it)
        gap = feed_hole(conn, today) if age > 10 else None
        if gap and gap["from"] != (date.fromisoformat(reach) + timedelta(days=1)).isoformat():
            gap = None
        from .detect import gap_words
        items.append({"key": "feed", "ok": age <= 10, "days": age, "newest": (newest or reach)[:10], "feed": reach,
                      "last_file": (got or "")[:10] or None, "gap": gap,
                      "title": "Payments are current" if age <= 10 else gap_words(gap) if gap else (
                          f"The remittances reach {reach} for most payers ({age} days ago)" if later else f"No payment newer than {reach} ({age} days ago)"),
                      "detail": "" if age <= 10 else (
                          "Nobody here can see what the payers paid in those weeks, so items paid then may still show as open." if gap else
                          "Newer payments are in for only a few payers, or after weeks of files that are missing, so items worked and paid "
                          "since then may still show as open." if later else
                          "Payments after that date are not in the site, so items worked and paid since then still show as open."
                          + (f" Files did arrive on {got[:10]}, but none holds a later payment." if recent else "")),
                      "who": ("Billing lead: put the TriZetto downloads of those weeks in the Dropbox folder (if TriZetto no longer shows "
                              "them, ask TriZetto for them)." if gap else
                              "Billing lead, with TriZetto: ask whether newer remittances exist and why they are not in the downloads." if recent
                              else "Billing lead: put the latest TriZetto downloads in the Dropbox folder.")})
    # One payer's remittances stopped while the feed goes on (misuse check CH-8): the claims sent to it and due since its last
    # remittance are not listed as never answered, since nothing on file says whether it answered them. The analysis saves
    # them (detect.PAYERS_BEHIND_META), most dollars first; a payer is named once its last remittance is more than
    # PAYER_QUIET_DAYS behind the day the feed reaches (before that it is between two files).
    from .detect import PAYER_QUIET_DAYS, PAYERS_BEHIND_META

    def quiet(p: dict) -> bool:
        try:
            return (date.fromisoformat(reach) - date.fromisoformat(str(p["since"])[:10])).days > PAYER_QUIET_DAYS
        except ValueError:
            return True             # a date no calendar has: named, so that a person looks
    behind = [p for p in _meta(conn, PAYERS_BEHIND_META) or [] if isinstance(p, dict) and p.get("payer") and p.get("since") and quiet(p)]
    if behind:
        def held(p: dict) -> str:
            k, c = int(p.get("claims") or 0), int(p.get("billed_cents") or 0)
            return (f"{k} {'claim' if k == 1 else 'claims'} sent to {p['payer']} and due since then (${c / 100:,.2f} billed) "
                    f"{'is' if k == 1 else 'are'} not listed as never answered")
        n, cents = sum(int(p.get("claims") or 0) for p in behind), sum(int(p.get("billed_cents") or 0) for p in behind)
        one = behind[0]
        items.append({"key": "payer_stopped", "ok": False, "n": n, "cents": cents, "payers": behind[:20],
                      "title": (f"No remittance from {one['payer']} since {one['since']}" if len(behind) == 1 else
                                f"No recent remittance from {len(behind)} payers: "
                                + ", ".join(f"{p['payer']} (since {p['since']})" for p in behind[:3]) + (", and more" if len(behind) > 3 else "")),
                      "detail": (f"{held(one)}: nothing on file says whether {one['payer']} answered {'it' if n == 1 else 'them'}."
                                 if len(behind) == 1 else
                                 "; ".join(f"{p['payer']}, no remittance since {p['since']}: {held(p)}" for p in behind[:5])
                                 + ("; and more" if len(behind) > 5 else "") + ". Nothing on file says whether those payers answered them."),
                      "who": "Billing lead, with TriZetto: ask why the payer's remittances stopped (its ERA enrollment, or files missing from the "
                             "downloads) and put any missing files in the Dropbox folder. Until they are in, check those claims on the payer's portal."})
    items += _feeds_837(conn)
    items += trizetto_health(conn, today)
    # versions of one remittance that differ (remitwatch.store.mark_superseded): no rule can say which stands, so both are
    # counted until a person checks
    from .store import DIFFER_META
    found = [v for v in _meta(conn, DIFFER_META) or [] if isinstance(v, dict)]

    def listed(vs: list[dict]) -> str:
        return "; ".join(f"{v.get('payer') or 'a payer'}, trace {v.get('trace')}, paid {v.get('payment_date')}: versions of "
                         + " and ".join(f"${c / 100:,.2f}" for c in v.get("payments_cents") or []) for v in vs[:3]) + ("; and more." if len(vs) > 3 else ".")
    versions = [v for v in found if v.get("kind") != "lost"]
    if versions:
        n = len(versions)
        claims = sum(int(v.get("claims_on_both") or 0) for v in versions)
        twice = sum(int(v.get("cents_counted_twice") or 0) for v in versions)
        items.append({"key": "remit_versions", "ok": False, "n": n, "claims": claims, "cents": twice,
                      "title": "Two versions of one remittance differ" + (f" ({n} remittances)" if n > 1 else ""),
                      "detail": (f"Both versions are counted, so {claims} {'claim that is' if claims == 1 else 'claims that are'} on both "
                                 f"{'counts' if claims == 1 else 'count'} twice (${twice / 100:,.2f}). {listed(versions)}"),
                      "who": "Billing lead: look the payment up on the payer's portal, or ask TriZetto, which version stands, and tell "
                             "whoever looks after RemitWatch which file is wrong."})
    lost = [v for v in found if v.get("kind") == "lost"]
    if lost:
        n = len(lost)
        claims = sum(int(v.get("claims_not_counted") or 0) for v in lost)
        cents = sum(int(v.get("cents_not_counted") or 0) for v in lost)
        items.append({"key": "remit_copy_lost", "ok": False, "n": n, "claims": claims, "cents": cents,
                      "title": "A broken copy of a remittance holds claims the complete one does not" + (f" ({n} remittances)" if n > 1 else ""),
                      "detail": (f"RemitWatch counts the complete version and leaves out the copy that is cut short or does not add up, "
                                 f"so {claims} {'claim' if claims == 1 else 'claims'} only on that copy {'is' if claims == 1 else 'are'} not "
                                 f"counted (${cents / 100:,.2f} paid). {listed(lost)}"),
                      "who": "Billing lead: ask TriZetto for the whole remittance again, or look it up on the payer's portal, and check "
                             "those claims before anyone chases them."})
    # A payer took back a payment no remittance on file shows: a reversal (trace finding T-I5), or a recoupment withheld from
    # a later check naming the claim (money-1). For a service after the payer's first remittance on file, the remittance that
    # paid it is missing. The analysis saves the counts (detect.REVERSALS_NOT_ON_FILE_META)
    from .detect import REVERSALS_NOT_ON_FILE_META
    rev = _meta(conn, REVERSALS_NOT_ON_FILE_META) or {}
    if isinstance(rev, dict) and int(rev.get("claims") or 0) > 0:
        n, cents = int(rev.get("claims") or 0), int(rev.get("cents") or 0)
        old_n, old_cents = int(rev.get("older_claims") or 0), int(rev.get("older_cents") or 0)
        rc_n, rc_cents = int(rev.get("recouped_claims") or 0), int(rev.get("recouped_cents") or 0)
        items.append({"key": "reversal_not_on_file", "ok": False, "claims": n, "cents": cents, "lines": int(rev.get("lines") or 0),
                      "entries": int(rev.get("entries") or 0), "recouped_claims": rc_n, "recouped_cents": rc_cents,
                      "older_claims": old_n, "older_cents": old_cents,
                      "title": (f"A payer took back a payment that no remittance on file shows (${cents / 100:,.2f})" if n == 1 else
                                f"Payers took back payments on {n} claims that no remittance on file shows (${cents / 100:,.2f})"),
                      "detail": ("The remittance that paid " + ("that claim" if n == 1 else "those claims") + " is missing: nothing on file "
                                 "shows the payment, so it is not counted, and the takeback takes nothing from the payments that are."
                                 + ((f" {'The claim was' if n == 1 else f'{rc_n} of the claims were'} taken back by a recoupment the payer "
                                     f"withheld from a later check (${rc_cents / 100:,.2f}); one of $500 or more is also on the work list, "
                                     "to match to the payer's letter.") if rc_n else "")
                                 + (f" {old_n} more {'claim takes back a payment' if old_n == 1 else 'claims take back payments'} made before "
                                    f"the remittances on file begin (${old_cents / 100:,.2f}): that is expected." if old_n else "")),
                      "who": "Billing lead, with TriZetto: ask for the remittances that paid those claims (the payer's portal shows each "
                             "payment) and put them in the Dropbox folder."})
    info = _meta(conn, "activity:info") or {}
    gaps = info.get("gaps") or []
    if gaps:
        weeks = sum(int(g.get("weeks") or 0) for g in gaps)
        short: dict[str, int] = {}          # a payer's gaps share the months they leave short, so take each payer's largest
        for g in gaps:
            key = g.get("payer_id") or g.get("payer") or ""
            short[key] = max(short.get(key, 0), int(g.get("missing") or 0))
        missing = sum(short.values())
        items.append({"key": "missing_weeks", "ok": False, "n": len(gaps), "weeks": weeks, "payers": len(short), "claims": missing,
                      "title": f"{weeks} {'week' if weeks == 1 else 'weeks'} of remittances look missing from {len(short)} {'payer' if len(short) == 1 else 'payers'}",
                      "detail": f"About {missing:,} claims those weeks would have paid are not in the site.",
                      "who": "Billing lead, with TriZetto: send the files for those weeks again."})
    ema = _meta(conn, "ema:coverage") or {}
    thin = [w for w in ema.get("weeks") or [] if w.get("claims", 0) >= ema.get("min_claims", 30) and w.get("in_ema", 0) < ema.get("share", 0.8) * w["claims"]]
    if thin:
        items.append({"key": "ema_gaps", "ok": False, "n": len(thin), "weeks": [w["week"] for w in thin],
                      "title": f"EMA charge exports miss {len(thin)} {'week' if len(thin) == 1 else 'weeks'} of visits",
                      "detail": "What was billed cannot be checked against what was paid for those visits.",
                      "who": "Practice administrator: pull Encounter & Charge Lines again for those dates of service."})
    r = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0), SUM(actionable='correct_and_resubmit') FROM findings
                         WHERE status IN ({','.join('?' * len(TODO))}) AND COALESCE(stale,0)=0 AND appeal_deadline >= ? AND deadline_basis LIKE '%not confirmed%'
                           AND category IN ('denial','bundling','downcoding','underpayment')""", TODO + (today,)).fetchone()
    if r[0]:
        items.append({"key": "deadlines", "ok": False, "n": r[0], "cents": r[1], "fixes": r[2] or 0,
                      "title": f"{r[0]:,} open items rest on a filing window not yet confirmed",
                      "detail": "The date shown is a default for that payer type, or the appeal window where a corrected claim's limit is "
                                "set by the payer agreement (often 90 days from the date of service, so it can be earlier).",
                      "who": "Practice administrator: check the windows in the payer agreements, Anthem and UnitedHealthcare first."})
    # a claim no payer answered has no remittance line, so no ERA row in EMA can ever describe it: left out of both counts.
    # EMA's side is known from its ERA export (ema_handling) or from Production's postings on the item's own line
    # (ema_outcome, when the line was found)
    fcols = {r[1] for r in conn.execute("PRAGMA table_info(findings)")}
    known = ("(ema_handling IS NOT NULL OR (ema_outcome IS NOT NULL AND ema_outcome NOT IN ('not_found','not_covered','shared_line')))"
             if "ema_outcome" in fcols else "ema_handling IS NOT NULL")
    h = conn.execute(f"""SELECT COUNT(*), SUM({known}) FROM findings WHERE status IN ({','.join('?' * len(TODO))})
                         AND COALESCE(stale,0)=0 AND appeal_deadline >= ? AND category IN ('denial','bundling','downcoding','underpayment')
                         AND signal NOT IN ('NO_ANSWER','REJECTED_PRE_PAYER','NOT_SENT','EMA_DENIED')""",
                     TODO + (today,)).fetchone()
    if h[0]:
        share = (h[1] or 0) / h[0]
        if share < 0.5:
            items.append({"key": "ema_handling", "ok": False, "share": round(share, 3),
                          "title": f"EMA's side is known for {share:.0%} of the open work",
                          "detail": "For the rest the site cannot tell whether a biller already wrote it off, moved it to the patient or resent it.",
                          "who": "Practice administrator: export ERA & Payment Line Adjustments every week (and once from November 2024)."})
    try:        # the self-check first (what the app cannot see, safe mode, red checks), then the store's size and memory
        from . import archive, selfcheck
        items = selfcheck.data_health(conn, today) + items + archive.data_health(conn)
    except (sqlite3.Error, ImportError, KeyError, TypeError, ValueError) as exc:
        from . import runlog
        runlog.caught(exc, "data_health:selfcheck", level="warning")
    items += ema_health(conn, today)
    return items


def ema_health(conn: sqlite3.Connection, today: str) -> list[dict]:
    """EMA's report items for the list (remitwatch.ema_checks.data_health: the reports on file and how old, held reports,
    missing columns, the map, payments with no remittance, credits, unknown status words), once a report has reached the
    site (its catalog, which the dashboard's copy holds too, so both hosts list the same items). On the copy they come
    from what the run stored; nothing is created or written here."""
    if "ema_report_files" not in _tables(conn):
        return []
    try:
        if not conn.execute("SELECT 1 FROM ema_report_files LIMIT 1").fetchone():
            return []
        from . import ema_checks as ec
        return ec.data_health(conn, today=today)
    except (sqlite3.Error, ImportError, KeyError, TypeError, ValueError):
        return []


MONEY_CATEGORIES = ("denial", "bundling", "downcoding", "underpayment")


def batches(conn: sqlite3.Connection, today: str, limit: int = 200) -> list[dict]:
    """Work still to do inside its window, as batches: one kind of work for one payer, so one person can clear a pile
    of the same thing in one sitting (one portal, one kind of letter). Each batch carries its first deadline, what
    closes within 7 and 14 days, how many of its items someone has started, how many are already sent (those have left
    the pile), and its commonest code. A routine-rate pattern counts its claims in window, by their own dates."""
    d7 = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    d14 = (date.fromisoformat(today) + timedelta(days=14)).isoformat()
    wins = claim_windows(conn, OPEN)
    out: dict[tuple, dict] = {}
    for fid, sig, act, carc, status, dl, cents, payer, claim, code in conn.execute(
            f"""SELECT finding_id, signal, actionable, carc, status, appeal_deadline, COALESCE(shortfall_cents,0), COALESCE(payer_group,'Unknown'),
                       COALESCE(claim_key, patient_control_number), procedure_code
                FROM findings WHERE status IN ({','.join('?' * len(OPEN))}) AND COALESCE(stale,0)=0 AND appeal_deadline >= ?
                  AND category IN ({','.join('?' * len(MONEY_CATEGORIES))})""", OPEN + (today,) + MONEY_CATEGORIES):
        kind = work_type(sig, act, carc)
        b = out.setdefault((kind, payer), {"kind": kind, "payer_group": payer, "n": 0, "cents": 0, "claims": set(), "pattern_claims": 0,
                                           "first_deadline": None, "due7_n": 0, "due14_n": 0, "started_n": 0, "sent_n": 0, "codes": {}})
        if status == "appealed":
            b["sent_n"] += 1
            continue
        if fid in wins:
            mine = [(d, c, n) for d, c, n in wins[fid] if d and d >= today]
            if not mine:
                continue
            cents, first = sum(c for _d, c, _n in mine), min(d for d, _c, _n in mine)
            b["pattern_claims"] += sum(n for _d, _c, n in mine)
        else:
            first = dl
            if claim:
                b["claims"].add(claim)
        b["n"] += 1
        b["cents"] += cents
        b["first_deadline"] = min(b["first_deadline"] or first, first)
        b["due7_n"] += first < d7
        b["due14_n"] += first < d14
        b["started_n"] += status == "in_progress"
        if code:
            b["codes"][code] = b["codes"].get(code, 0) + cents
    rows = []
    for b in out.values():
        if not b["n"]:
            continue
        top = max(b["codes"].items(), key=lambda kv: kv[1])[0] if b["codes"] else None
        rows.append(dict({k: v for k, v in b.items() if k not in ("claims", "codes", "pattern_claims")},
                         claims=len(b["claims"]) + b["pattern_claims"], top_code=top))
    rows.sort(key=lambda r: -r["cents"])
    return rows[:limit]


def to_decide(conn: sqlite3.Connection, newest: str | None, days: int = 120, today: str | None = None) -> dict:
    """Items with no filing date because a person has to decide what they are (a code the payer changed, a service
    called not covered, a duplicate, a payer's takeback): those paid in the last `days` days to today, where the practice
    is (not to the newest payment: a feed that stopped must not freeze the count), by kind and payer."""
    if not newest:
        return {"n": 0, "cents": 0, "groups": []}
    since = (date.fromisoformat(today or utc_today()) - timedelta(days=days)).isoformat()
    groups = _rows(conn, f"""SELECT signal, COALESCE(payer_group,'Unknown') payer_group, COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents,
                                    MAX(payment_date) last_paid
                             FROM findings WHERE status IN ({','.join('?' * len(TODO))}) AND COALESCE(stale,0)=0 AND appeal_deadline IS NULL
                               AND actionable='review' AND category IN ({','.join('?' * len(MONEY_CATEGORIES))}) AND payment_date >= ?
                             GROUP BY 1,2 ORDER BY cents DESC""", TODO + MONEY_CATEGORIES + (since,))
    return {"n": sum(g["n"] for g in groups), "cents": sum(g["cents"] for g in groups), "since": since, "days": days, "groups": groups[:30]}


def missed(conn: sqlite3.Connection, today: str, days: int = 30) -> dict:
    """Open items whose filing window closed in the last `days` days without anyone working them. A routine-rate pattern
    counts when one of its claims closed then, with those claims' cents."""
    since = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    wins = claim_windows(conn, ("open",))
    n = cents = 0
    for fid, dl, c in conn.execute(f"""SELECT finding_id, appeal_deadline, COALESCE(shortfall_cents,0) FROM findings WHERE status='open'
                                       AND COALESCE(stale,0)=0 AND appeal_deadline IS NOT NULL AND category IN ({','.join('?' * len(MONEY_CATEGORIES))})""",
                                   MONEY_CATEGORIES):
        if fid in wins:
            got = [x for d, x, _l in wins[fid] if d and since <= d < today]
            if got:
                n, cents = n + 1, cents + sum(got)
        elif since <= dl < today:
            n, cents = n + 1, cents + c
    return {"n": n, "cents": cents, "days": days}


def done(conn: sqlite3.Connection, today: str) -> dict:
    """What people moved today and this week (Monday on): items, and the dollars of those sent or recorded paid."""
    from .runtime import practice_date
    week = (date.fromisoformat(today) - timedelta(days=date.fromisoformat(today).weekday())).isoformat()
    if "finding_events" not in _tables(conn):
        return {"today_n": 0, "week_n": 0, "week_cents": 0}
    last: dict[str, tuple] = {}          # each item's last change by a person this week: an undo leaves nothing done
    # each change counts on the practice's day it was made (the history keeps UTC times, a day ahead from 7 PM Eastern)
    since = (date.fromisoformat(week) - timedelta(days=1)).isoformat()
    for fid, at, to, cents, sig, payer in conn.execute("""SELECT e.finding_id, e.at, e.to_status, COALESCE(e.amount_cents,0), f.signal, f.payer_name
                                                         FROM finding_events e LEFT JOIN findings f ON f.finding_id = e.finding_id
                                                         WHERE e.kind='manual' AND substr(e.at,1,10) >= ? ORDER BY e.at, e.id""", (since,)):
        day = practice_date(at)
        if day < week:
            continue
        # asking a payer to stop its payment fees moves all its fee months at once: that is one request, and fees are not
        # money sent or paid
        key = ("fee", payer) if sig == "FEE_SKIM" else fid
        last[key] = (day, to, 0 if sig == "FEE_SKIM" else cents)
    moved = [v for v in last.values() if v[1] != "open"]
    return {"today_n": sum(1 for v in moved if v[0] == today), "week_n": len(moved),
            "week_cents": sum(v[2] for v in moved if v[1] in ("appealed", "resolved")), "week_start": week}


def follow_ups(conn: sqlite3.Connection, newest: str | None, days: int = 30, today: str | None = None) -> dict:
    """Items marked sent to the payer more than `days` days ago and still not paid: sent before the practice's day
    `days` days before today. Counted to today, not to the newest payment, so they keep coming while the remittances
    are behind (api.brief then says so: EMA or the payer's portal is checked first). `newest` is no longer used."""
    from . import runtime
    cut = runtime.practice_day_start((date.fromisoformat(today or utc_today()) - timedelta(days=days)).isoformat())
    r = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE status='appealed' AND COALESCE(stale,0)=0
                         AND COALESCE(appealed_at, status_changed_at) < ? AND category IN ({','.join('?' * len(MONEY_CATEGORIES))})""",
                     (cut,) + MONEY_CATEGORIES).fetchone()
    return {"n": r[0], "cents": r[1], "days": days}


def brief(conn: sqlite3.Connection, today: str | None = None) -> dict:
    """Everything the home screen shows, in one call."""
    today = today or utc_today()
    tables = _tables(conn)
    if "findings" not in tables:
        return {"ready": False}
    newest = newest_payment(conn)
    feed = feed_day(conn, today)        # how far the remittances reach for most payers: the feed check and the warning line
    from . import runtime
    # tz: the practice's zone, which the pages date a stored moment in (today.js practiceDate), as the server does
    return {"ready": True, "today": today, "tz": runtime.practice_zone_name(), "newest_payment": newest, "feed_date": feed,
            "work": work(conn, today), "recovered": recovered(conn, today),
            "batches": batches(conn, today), "decide": to_decide(conn, newest, today=today), "missed": missed(conn, today), "done": done(conn, today),
            "follow_ups": follow_ups(conn, newest, today=today), "contracts": contract_levers(conn, feed or newest), "fees": fee_levers(conn, feed or newest),
            "causes": denial_causes(conn, feed or newest), "health": data_health(conn, newest, today, feed), "work_types": WORK_TYPES,
            "labels": {k: {"label": v[0], "next": v[1], "work": SIGNAL_WORK.get(k), "short": SHORT_LABELS.get(k, v[0])} for k, v in SIGNAL_LABELS.items()},
            "statuses": STATUS_LABELS,
            "claim_kinds": {k: sorted(v) for k, v in CLAIM_KIND_CARCS.items()}}
