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
from datetime import date, datetime, timedelta, timezone

OPEN = ("open", "in_progress", "appealed")
TODO = ("open", "in_progress")          # still to do: an item sent to the payer waits on the payer, not on a person
SMALL_CENTS = 2000                      # items known to be worth under $20: about a third of the list and a few percent of the money

# The kind of work each finding takes. A signal not named here falls to its action (fix and resubmit, appeal) and then
# to "check first".
WORK_TYPES: list[dict] = [
    {"key": "fix", "label": "Fix and resubmit", "needs": "EMA and the payer's claim number",
     "how": "The claim went out with something missing or wrong (a modifier, a code, information the payer asked for). Correct it in EMA and send the correction the way the payer takes it before the date shown (a corrected claim for most payers; for Medicare a reopening, a new claim or a redetermination, as the item says), then press Mark sent."},
    {"key": "auth", "label": "Get the authorization", "needs": "The authorization record, or the payer's portal",
     "how": "Denied for a missing prior authorization. Find the authorization in EMA and appeal with it; if there is none, ask the payer for a retro-authorization. Tell the front desk so it does not happen again."},
    {"key": "note", "label": "Appeal with the visit note", "needs": "The visit or procedure note from EMA",
     "how": "The payer says the visit or level was not supported, or bundled a separately documented visit. Build the appeal letter, attach the note for that date, send it, then press Mark sent."},
    {"key": "rule", "label": "Appeal on the coding rules", "needs": "Nothing extra: the letter cites the rule",
     "how": "The payer bundled a procedure or add-on that the coding rules let you bill. Appeal with the letter; if the rule allows a modifier and the sites were separate, add 59 or XS and resubmit instead."},
    {"key": "rate", "label": "Ask for the usual rate", "needs": "The payer's phone line or portal",
     "how": "Paid less than this payer usually pays for the code. Ask it to reprocess at its usual rate; one request can list every claim for the same code."},
    {"key": "check", "label": "Check first", "needs": "A look at the claim",
     "how": "Something a person has to decide: bill another payer or the patient, fix an enrollment, find an earlier payment, or let it go."},
]
_WORK_OF = {
    "fix": {"DENIED_MISSING_MODIFIER", "DENIED_CODING", "DENIED_MISSING_INFO", "EM_BUNDLED_NO_MODIFIER", "EM_BUNDLED_MOD57_MINOR"},
    "auth": {"DENIED_AUTH"},
    "note": {"EM_BUNDLED_MOD25", "EM_BUNDLED_MOD57_MAJOR", "EM_BUNDLED_MOD24", "DENIED_MEDICAL_NECESSITY", "DENIED_FREQUENCY", "DENIED_LEVEL_OF_SERVICE",
             "DOWNCODE_SVC06", "DOWNCODE_VS_BILLED", "DOWNCODE_CHARGE", "DOWNCODE_LEVEL_REDUCED", "DOWNCODE_RATE", "DOWNCODE_LINE_SWAP", "DOWNCODE_LEVEL_DENIED"},
    "rule": {"PROCEDURE_BUNDLED", "ADDON_BUNDLED"},
    "rate": {"UNDERPAID_VS_OWN_RATE", "SECOND_RATE", "MPPR_EXCESS"},
    "check": {"DENIED_NONCOVERED", "DENIED_DUPLICATE_UNPAID", "DENIED_PROVIDER", "DENIED_TIMELY_FILING", "DENIED_OTHER", "DENIED_MISSING_PRIMARY",
              "CODE_CHANGED"},
}
SIGNAL_WORK = {sig: key for key, sigs in _WORK_OF.items() for sig in sigs}
# A whole claim denied takes the work its reason needs: fixed and sent again when the detector says so, else by the
# reason (the same groups detect.py uses for lines), else a person checks it first.
CLAIM_DENIAL = "CLAIM_DENIED_UNPAID"
CLAIM_KIND_CARCS = {
    "auth": {"197", "198", "15", "39", "210", "243"},
    "note": {"50", "B12", "B22", "167", "56", "A1", "B5", "272", "58", "216", "150", "186", "151", "119", "222", "273", "B14", "35"},
    "rule": {"97", "B10", "236", "234", "231", "M15"},
}
WORK_SIGNALS = set(SIGNAL_WORK) | {CLAIM_DENIAL}

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
    "CLAIM_DENIED_UNPAID": ("Whole claim denied and never paid", "Read the reason: fix and resubmit if it names an error, appeal if not, let it go if the payer is right."),
    "UNDERPAID_VS_OWN_RATE": ("Paid less than this payer usually pays", "Ask the payer to reprocess at its usual rate; one request can list every claim for the code."),
    "SECOND_RATE": ("Payer pays a second, lower amount for this code", "Ask the payer once about all the claims at this amount, giving the likely reason shown."),
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
}
STATUS_LABELS = {"open": "To do", "in_progress": "Started", "appealed": "Sent", "resolved": "Paid", "lost": "Lost", "dismissed": "Let go"}


def work_type(signal: str | None, actionable: str | None, carc: str | None = None) -> str:
    if signal == CLAIM_DENIAL:
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
    whole = f"signal='{CLAIM_DENIAL}'"
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
    """Today as the rest of the dashboard and SQL date('now') see it."""
    return datetime.now(timezone.utc).date().isoformat()


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
    if "transactions" not in _tables(conn):
        return None
    r = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()
    return r[0] if r and r[0] else None


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


def fee_levers(conn: sqlite3.Connection) -> dict:
    """Fees set below what a payer allows (CHARGE_BELOW_RATE), one row per code: what it left unpaid in all, the fee in
    the newest year and the highest amount a capped payer allowed that year (raise the fee above it). And payment-vendor
    fees (FEE_SKIM) still open, by payer, with the months they cover."""
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
    fees = _rows(conn, """SELECT payer_group, SUM(shortfall_cents) cents, COUNT(DISTINCT month) months, MIN(month) first_month, MAX(month) last_month
                          FROM findings WHERE signal='FEE_SKIM' AND COALESCE(stale,0)=0 AND status IN ('open','in_progress')
                          GROUP BY payer_group ORDER BY cents DESC""")
    return {"charge_caps": caps, "charge_cap_cents": sum(c["cents"] for c in caps), "payment_fees": fees,
            "payment_fee_cents": sum(f["cents"] or 0 for f in fees),
            "payment_fee_since": min((f["first_month"] for f in fees if f.get("first_month")), default=None)}


CAUSES = {
    "DENIED_AUTH": "No authorization", "DENIED_MISSING_INFO": "Information missing on the claim", "DENIED_MISSING_MODIFIER": "Modifier missing or wrong",
    "DENIED_CODING": "Code or modifier combination", "DENIED_NONCOVERED": "Not covered by the plan", "DENIED_MEDICAL_NECESSITY": "Medical necessity",
    "DENIED_FREQUENCY": "Frequency or units", "DENIED_DUPLICATE_UNPAID": "Called a duplicate", "DENIED_TIMELY_FILING": "Filed too late",
    "DENIED_PROVIDER": "Provider enrollment", "DENIED_MISSING_PRIMARY": "Add-on without its main code", "CLAIM_DENIED_UNPAID": "Whole claim denied",
    "EM_BUNDLED_MOD25": "Visit with modifier 25 not paid", "EM_BUNDLED_NO_MODIFIER": "Visit billed with a procedure, no modifier",
    "EM_BUNDLED_MOD57_MINOR": "Modifier 57 on a minor procedure", "PROCEDURE_BUNDLED": "Procedure bundled into another",
    "EM_BUNDLED_MOD57_MAJOR": "Visit with modifier 57 bundled into a major surgery", "ADDON_BUNDLED": "Add-on code paid $0",
    "EM_BUNDLED_MOD24": "Visit in a post-op period denied",
}


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


def data_health(conn: sqlite3.Connection, newest: str | None, today: str) -> list[dict]:
    """What in the data is hiding money, most urgent first. Each item says what to do and who does it."""
    items = []
    if newest:
        age = (date.fromisoformat(today) - date.fromisoformat(newest[:10])).days
        got = None
        try:
            got = conn.execute("SELECT MAX(ingested_at) FROM files WHERE kind IN ('835','remit','rmt')").fetchone()[0]
        except sqlite3.Error:
            pass
        recent = bool(got) and (date.fromisoformat(today) - date.fromisoformat(got[:10])).days <= 10
        items.append({"key": "feed", "ok": age <= 10, "days": age, "newest": newest[:10], "last_file": (got or "")[:10] or None,
                      "title": "Payments are current" if age <= 10 else f"No payment newer than {newest[:10]} ({age} days ago)",
                      "detail": "" if age <= 10 else ("Payments after that date are not in the site, so items worked and paid since then still show as open."
                                                       + (f" Files did arrive on {got[:10]}, but none holds a later payment." if recent else "")),
                      "who": ("Billing lead, with TriZetto: ask whether newer remittances exist and why they are not in the downloads." if recent
                              else "Billing lead: put the latest TriZetto downloads in the Dropbox folder.")})
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
    h = conn.execute(f"""SELECT COUNT(*), SUM(ema_handling IS NOT NULL) FROM findings WHERE status IN ({','.join('?' * len(TODO))})
                         AND COALESCE(stale,0)=0 AND appeal_deadline >= ? AND category IN ('denial','bundling','downcoding','underpayment')""",
                     TODO + (today,)).fetchone()
    if h[0]:
        share = (h[1] or 0) / h[0]
        if share < 0.5:
            items.append({"key": "ema_handling", "ok": False, "share": round(share, 3),
                          "title": f"EMA's side is known for {share:.0%} of the open work",
                          "detail": "For the rest the site cannot tell whether a biller already wrote it off, moved it to the patient or resent it.",
                          "who": "Practice administrator: export ERA & Payment Line Adjustments every week (and once from November 2024)."})
    return items


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
                       claim_key, procedure_code
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


def to_decide(conn: sqlite3.Connection, newest: str | None, days: int = 120) -> dict:
    """Items with no filing date because a person has to decide what they are (a code the payer changed, a service
    called not covered, a duplicate, a payer's takeback): those paid in the last `days` days, by kind and payer."""
    if not newest:
        return {"n": 0, "cents": 0, "groups": []}
    since = (date.fromisoformat(newest[:10]) - timedelta(days=days)).isoformat()
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
    week = (date.fromisoformat(today) - timedelta(days=date.fromisoformat(today).weekday())).isoformat()
    if "finding_events" not in _tables(conn):
        return {"today_n": 0, "week_n": 0, "week_cents": 0}
    last: dict[str, tuple] = {}          # each item's last change by a person this week: an undo leaves nothing done
    for fid, at, to, cents in conn.execute("""SELECT finding_id, at, to_status, COALESCE(amount_cents,0) FROM finding_events
                                              WHERE kind='manual' AND substr(at,1,10) >= ? ORDER BY at, id""", (week,)):
        last[fid] = (at[:10], to, cents)
    moved = [v for v in last.values() if v[1] != "open"]
    return {"today_n": sum(1 for v in moved if v[0] == today), "week_n": len(moved),
            "week_cents": sum(v[2] for v in moved if v[1] in ("appealed", "resolved")), "week_start": week}


def follow_ups(conn: sqlite3.Connection, newest: str | None, days: int = 30) -> dict:
    """Items marked sent to the payer more than `days` days before the newest payment and still not paid."""
    if not newest:
        return {"n": 0, "cents": 0}
    cut = (date.fromisoformat(newest[:10]) - timedelta(days=days)).isoformat()
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
    return {"ready": True, "today": today, "newest_payment": newest, "work": work(conn, today), "recovered": recovered(conn, today),
            "batches": batches(conn, today), "decide": to_decide(conn, newest), "missed": missed(conn, today), "done": done(conn, today),
            "follow_ups": follow_ups(conn, newest), "contracts": contract_levers(conn, newest), "fees": fee_levers(conn),
            "causes": denial_causes(conn, newest), "health": data_health(conn, newest, today), "work_types": WORK_TYPES,
            "labels": {k: {"label": v[0], "next": v[1], "work": SIGNAL_WORK.get(k)} for k, v in SIGNAL_LABELS.items()}, "statuses": STATUS_LABELS,
            "claim_kinds": {k: sorted(v) for k, v in CLAIM_KIND_CARCS.items()}}
