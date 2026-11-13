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
from datetime import date, timedelta

OPEN = ("open", "in_progress", "appealed")

# The kind of work each finding takes. A signal not named here falls to its action (fix and resubmit, appeal) and then
# to "check first".
WORK_TYPES: list[dict] = [
    {"key": "fix", "label": "Fix and resubmit", "needs": "EMA and the payer's claim number",
     "how": "The claim went out with something missing or wrong (a modifier, a code, information the payer asked for). Correct it in EMA and send it as a corrected claim before the date shown, then press Mark sent."},
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
              "CODE_CHANGED", "CLAIM_DENIED_UNPAID"},
}
SIGNAL_WORK = {sig: key for key, sigs in _WORK_OF.items() for sig in sigs}
WORK_SIGNALS = set(SIGNAL_WORK)

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


def work_type(signal: str | None, actionable: str | None) -> str:
    if signal in SIGNAL_WORK:
        return SIGNAL_WORK[signal]
    return {"correct_and_resubmit": "fix", "appeal": "rule"}.get(actionable or "", "check")


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
    """Open work still inside its filing window, by the kind of work it takes, with what falls due in 7 and 30 days.
    A routine-rate pattern counts once, on its own latest date (its claims' dates are on the finding)."""
    soon7 = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    soon30 = (date.fromisoformat(today) + timedelta(days=30)).isoformat()
    out = {t["key"]: dict(t, n=0, cents=0, due7_n=0, due7_cents=0, due30_n=0, due30_cents=0, working_n=0) for t in WORK_TYPES}
    for r in conn.execute(f"""SELECT signal, actionable, status, appeal_deadline, COALESCE(shortfall_cents,0) FROM findings
                              WHERE status IN ({','.join('?' * len(OPEN))}) AND COALESCE(stale,0)=0 AND appeal_deadline >= ?
                                AND category IN ('denial','bundling','downcoding','underpayment')""", OPEN + (today,)):
        sig, act, status, dl, cents = r
        w = out[work_type(sig, act)]
        w["n"] += 1
        w["cents"] += cents
        if status != "open":
            w["working_n"] += 1
        if dl < soon7:
            w["due7_n"] += 1
            w["due7_cents"] += cents
        if dl < soon30:
            w["due30_n"] += 1
            w["due30_cents"] += cents
    types = [out[t["key"]] for t in WORK_TYPES if out[t["key"]]["n"]]
    total = {k: sum(t[k] for t in types) for k in ("n", "cents", "due7_n", "due7_cents", "due30_n", "due30_cents", "working_n")}
    expired = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE status IN ({','.join('?' * len(OPEN))})
                               AND COALESCE(stale,0)=0 AND appeal_deadline < ? AND category IN ('denial','bundling','downcoding','underpayment')""",
                           OPEN + (today,)).fetchone()
    return {"types": types, "total": total, "expired_n": expired[0], "expired_cents": expired[1]}


def recovered(conn: sqlite3.Connection, today: str) -> dict:
    """What came back: findings marked recovered, by a person or by a later payment, in the last 30 days and this year."""
    d30 = (date.fromisoformat(today) - timedelta(days=30)).isoformat()
    year = today[:4]
    r = conn.execute("""SELECT COALESCE(SUM(CASE WHEN COALESCE(recovered_date, status_changed_at, last_seen) >= ? THEN recovered_cents END),0),
                               COALESCE(SUM(CASE WHEN COALESCE(recovered_date, status_changed_at, last_seen) >= ? THEN 1 END),0),
                               COALESCE(SUM(CASE WHEN substr(COALESCE(recovered_date, status_changed_at, last_seen),1,4) = ? THEN recovered_cents END),0)
                        FROM findings WHERE status='resolved' AND COALESCE(recovered_cents,0) > 0""", (d30, d30, year)).fetchone()
    return {"last30_cents": r[0], "last30_n": r[1], "year_cents": r[2], "year": year}


def contract_levers(conn: sqlite3.Connection, newest: str | None) -> dict:
    """Each commercial and Medicare Advantage payer's usual office rates against what Medicare pays the practice for the
    same codes, over the last full year of volume: the payer's mode times each code's lines, against Medicare's. Only
    codes with at least 20 clean lines carry a rate. A payer below Medicare is a contract question, not an appeal."""
    if not newest or not {"payer_rates", "exposure"} <= _tables(conn):
        return {"year": None, "payers": []}
    year = str(int(newest[:4]) - 1) if newest[5:7] < "12" else newest[:4]
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
    """Fees set below what a payer allows (CHARGE_BELOW_RATE) and payment-vendor fees (FEE_SKIM), last two years."""
    caps = _rows(conn, """SELECT procedure_code code, payer_group, year, shortfall_cents cents, title FROM findings
                          WHERE signal='CHARGE_BELOW_RATE' AND COALESCE(stale,0)=0 ORDER BY year DESC, shortfall_cents DESC""")
    fees = _rows(conn, """SELECT payer_group, SUM(shortfall_cents) cents, COUNT(*) months, MAX(month) last_month FROM findings
                          WHERE signal='FEE_SKIM' AND COALESCE(stale,0)=0 AND status IN ('open','in_progress') GROUP BY payer_group ORDER BY cents DESC""")
    return {"charge_caps": caps, "charge_cap_cents": sum(c["cents"] or 0 for c in caps), "payment_fees": fees,
            "payment_fee_cents": sum(f["cents"] or 0 for f in fees)}


CAUSES = {
    "DENIED_AUTH": "No authorization", "DENIED_MISSING_INFO": "Information missing on the claim", "DENIED_MISSING_MODIFIER": "Modifier missing or wrong",
    "DENIED_CODING": "Code or modifier combination", "DENIED_NONCOVERED": "Not covered by the plan", "DENIED_MEDICAL_NECESSITY": "Medical necessity",
    "DENIED_FREQUENCY": "Frequency or units", "DENIED_DUPLICATE_UNPAID": "Called a duplicate", "DENIED_TIMELY_FILING": "Filed too late",
    "DENIED_PROVIDER": "Provider enrollment", "DENIED_MISSING_PRIMARY": "Add-on without its main code", "CLAIM_DENIED_UNPAID": "Whole claim denied",
    "EM_BUNDLED_MOD25": "Visit with modifier 25 not paid", "EM_BUNDLED_NO_MODIFIER": "Visit billed with a procedure, no modifier",
    "EM_BUNDLED_MOD57_MINOR": "Modifier 57 on a minor procedure", "PROCEDURE_BUNDLED": "Procedure bundled into another",
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
        items.append({"key": "feed", "ok": age <= 10, "days": age, "newest": newest[:10],
                      "title": "Remittance files are current" if age <= 10 else f"No new remittance files for {age} days",
                      "detail": "Payments after the newest file are not in the site, so worked and paid items still show as open." if age > 10 else "",
                      "who": "Billing lead: put the latest TriZetto downloads in the Dropbox folder."})
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
    r = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE status IN ({','.join('?' * len(OPEN))}) AND COALESCE(stale,0)=0
                         AND appeal_deadline >= ? AND deadline_basis LIKE '%not confirmed%'""", OPEN + (today,)).fetchone()
    if r[0]:
        items.append({"key": "deadlines", "ok": False, "n": r[0], "cents": r[1],
                      "title": f"{r[0]:,} open items rest on a filing window not yet confirmed",
                      "detail": "The date shown is a conservative default for that payer type; the contract or the payer's manual decides.",
                      "who": "Practice administrator: check the window in the payer's agreement."})
    h = conn.execute(f"""SELECT COUNT(*), SUM(ema_handling IS NOT NULL) FROM findings WHERE status IN ({','.join('?' * len(OPEN))})
                         AND COALESCE(stale,0)=0 AND appeal_deadline >= ? AND category IN ('denial','bundling','downcoding','underpayment')""",
                     OPEN + (today,)).fetchone()
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
    """Open work still inside its window, as batches: one kind of work for one payer, so one person can clear a pile
    of the same thing in one sitting (one portal, one kind of letter). Each batch carries its first deadline, what
    closes within 7 and 14 days, how many items someone has started or sent, and its commonest code."""
    d7 = (date.fromisoformat(today) + timedelta(days=7)).isoformat()
    d14 = (date.fromisoformat(today) + timedelta(days=14)).isoformat()
    out: dict[tuple, dict] = {}
    for sig, act, status, dl, cents, payer, claim, code in conn.execute(
            f"""SELECT signal, actionable, status, appeal_deadline, COALESCE(shortfall_cents,0), COALESCE(payer_group,'Unknown'), claim_key, procedure_code
                FROM findings WHERE status IN ({','.join('?' * len(OPEN))}) AND COALESCE(stale,0)=0 AND appeal_deadline >= ?
                  AND category IN ({','.join('?' * len(MONEY_CATEGORIES))})""", OPEN + (today,) + MONEY_CATEGORIES):
        kind = work_type(sig, act)
        b = out.setdefault((kind, payer), {"kind": kind, "payer_group": payer, "n": 0, "cents": 0, "claims": set(), "first_deadline": dl,
                                           "due7_n": 0, "due14_n": 0, "started_n": 0, "sent_n": 0, "codes": {}})
        b["n"] += 1
        b["cents"] += cents
        if claim:
            b["claims"].add(claim)
        b["first_deadline"] = min(b["first_deadline"], dl)
        b["due7_n"] += dl < d7
        b["due14_n"] += dl < d14
        b["started_n"] += status == "in_progress"
        b["sent_n"] += status == "appealed"
        if code:
            b["codes"][code] = b["codes"].get(code, 0) + cents
    rows = []
    for b in out.values():
        top = max(b["codes"].items(), key=lambda kv: kv[1])[0] if b["codes"] else None
        rows.append(dict({k: v for k, v in b.items() if k not in ("claims", "codes")}, claims=len(b["claims"]), top_code=top))
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
                             FROM findings WHERE status IN ({','.join('?' * len(OPEN))}) AND COALESCE(stale,0)=0 AND appeal_deadline IS NULL
                               AND actionable='review' AND category IN ({','.join('?' * len(MONEY_CATEGORIES))}) AND payment_date >= ?
                             GROUP BY 1,2 ORDER BY cents DESC""", OPEN + MONEY_CATEGORIES + (since,))
    return {"n": sum(g["n"] for g in groups), "cents": sum(g["cents"] for g in groups), "since": since, "groups": groups[:30]}


def missed(conn: sqlite3.Connection, today: str, days: int = 30) -> dict:
    """Open items whose filing window closed in the last `days` days without anyone working them."""
    since = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    r = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE status='open' AND COALESCE(stale,0)=0
                         AND appeal_deadline >= ? AND appeal_deadline < ? AND category IN ({','.join('?' * len(MONEY_CATEGORIES))})""",
                     (since, today) + MONEY_CATEGORIES).fetchone()
    return {"n": r[0], "cents": r[1], "days": days}


def done(conn: sqlite3.Connection, today: str) -> dict:
    """What people moved today and this week (Monday on): items, and the dollars of those sent or recorded paid."""
    week = (date.fromisoformat(today) - timedelta(days=date.fromisoformat(today).weekday())).isoformat()
    if "finding_events" not in _tables(conn):
        return {"today_n": 0, "week_n": 0, "week_cents": 0}
    r = conn.execute("""SELECT SUM(substr(at,1,10) = ?), SUM(substr(at,1,10) >= ?),
                               SUM(CASE WHEN substr(at,1,10) >= ? AND to_status IN ('appealed','resolved') THEN COALESCE(amount_cents,0) END)
                        FROM finding_events WHERE kind='manual' AND substr(at,1,10) >= ?""", (today, week, week, week)).fetchone()
    return {"today_n": r[0] or 0, "week_n": r[1] or 0, "week_cents": r[2] or 0, "week_start": week}


def follow_ups(conn: sqlite3.Connection, newest: str | None, days: int = 30) -> dict:
    """Items marked sent to the payer more than `days` days before the newest payment and still not paid."""
    if not newest:
        return {"n": 0, "cents": 0}
    cut = (date.fromisoformat(newest[:10]) - timedelta(days=days)).isoformat()
    r = conn.execute("""SELECT COUNT(*), COALESCE(SUM(shortfall_cents),0) FROM findings WHERE status='appealed' AND COALESCE(stale,0)=0
                        AND COALESCE(appealed_at, status_changed_at) < ?""", (cut,)).fetchone()
    return {"n": r[0], "cents": r[1], "days": days}


def brief(conn: sqlite3.Connection, today: str | None = None) -> dict:
    """Everything the home screen shows, in one call."""
    today = today or date.today().isoformat()
    tables = _tables(conn)
    if "findings" not in tables:
        return {"ready": False}
    newest = newest_payment(conn)
    return {"ready": True, "today": today, "newest_payment": newest, "work": work(conn, today), "recovered": recovered(conn, today),
            "batches": batches(conn, today), "decide": to_decide(conn, newest), "missed": missed(conn, today), "done": done(conn, today),
            "follow_ups": follow_ups(conn, newest), "contracts": contract_levers(conn, newest), "fees": fee_levers(conn),
            "causes": denial_causes(conn, newest), "health": data_health(conn, newest, today), "work_types": WORK_TYPES,
            "labels": {k: {"label": v[0], "next": v[1], "work": SIGNAL_WORK.get(k)} for k, v in SIGNAL_LABELS.items()}, "statuses": STATUS_LABELS}
