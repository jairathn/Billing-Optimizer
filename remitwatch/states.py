"""
The one place an item's state is decided (amendment 13.1). Every item is in exactly one of:

  work_now    a person needs to work it today or this week
  waiting     a person or EMA shows the work was done (an appeal or corrected claim faxed or sent, a resubmission the payer
              received, an EMA claim action, an A/R note about an appeal, a call or a resubmission); it follows up on the
              date the work was seen plus the payer's response time (the 90th percentile from the archive), and goes back to
              work_now on that date when the payer has not answered by then
  done        paid, or settled by the patient or a second payer, with the proof
  accepted    a loss someone accepted: the owner's write-off policy, a hide rule, or a person who let it go or marked it lost
  expired     past every window, plus 30 days, with no work pending
  cant_check  the money never reaches any source RemitWatch reads (a remittance that goes to another payee, a payer whose
              answers reach neither the 835s nor EMA's postings): a data gap, never counted as fine

A person's status always wins over the automatic state where the two conflict, and the state says so ("Marked Sent by
... EMA shows it paid since: mark it Paid").

Public names:
  state_of(conn, item, *, outcome=None, today=None)      one item -> {'work_state', 'follow_up_on', 'state_basis', 'evidence', ...}
  states_of(conn, items, *, outcomes=None, today=None)   many items at once, gathered in chunks (the detection run's save)
  work_evidence(conn, item)                              the dated work on an item, oldest first
  response_days(conn, payer_group, work=None)            the payer's answer time, 90th percentile, from the archive
  save_response_days(conn)                               measure it for every payer group into meta (the detection run)
  classify_note(text, as_of=None)                        what an A/R billing note says was done (for the A/R reader)
  facts_of(conn, items, ...)                             the facts the states rest on (remitwatch.story shows them)

Facts come from the store only: the 835 tables, RemitWatch's own records (statuses, events, faxes), and the EMA tables
(Production's line summaries, the A/R, the claim links and events). Every lookup goes through temporary key tables, 500
keys at a time, on the indexes a serverless compact store keeps too. Nothing here writes to the store except
save_response_days (meta) and the temporary tables. Dates are ISO strings, money is integer cents.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

STATES = ("work_now", "waiting", "done", "accepted", "expired", "cant_check")
LABELS = {"work_now": "Work now", "waiting": "Waiting", "done": "Done", "accepted": "Accepted", "expired": "Expired",
          "cant_check": "Can't be checked"}

EXPIRE_GRACE_DAYS = 30          # past every window by this many days: expired
DEFAULT_RESPONSE_DAYS = 45      # a payer group with fewer than MIN_ANSWERS answers measured
MIN_ANSWERS = 20
RESPONSE_YEARS = 2              # the answers measured: remittances of the two years before the newest one
CLAIM_TRANSIT_DAYS = 7          # a corrected or new claim reaches the payer through the clearinghouse in about a week
CALL_WAIT_DAYS = 30             # what a payer's phone line or portal usually promises ("allow 30 days")
FORWARD_WAIT_DAYS = 45          # a claim forwarded to another carrier (CO-B11, claim status 19 with a carrier named)
FOLLOW_UP_MARGIN = 14           # a follow-up of work nobody can confirm falls due at least this long before the deadline
# an appeal's answer: the payer's own decision time (fax.WAIT_DAYS), never shorter than its usual claim answer time
APPEAL_WAIT = {"medicare": 60, "medicare_advantage": 60, "medicaid": 30, "commercial": 30, "supplement": 30, "tricare": 45,
               "va": 45, "other": 45}
RESPONSE_KEY = "states:response_days"
CHUNK = 500

MONEY = ("denial", "bundling", "downcoding", "underpayment")
CLAIM_SIGNALS = ("NO_ANSWER", "REJECTED_PRE_PAYER", "NOT_SENT")
EMA_ONLY = ("EMA_DENIED", "NO_ANSWER", "REJECTED_PRE_PAYER", "NOT_SENT", "REMIT_NOT_POSTED")
INFO_ONLY = ("MIPS_ADJUSTMENT", "SEQUESTRATION")
CONTRACT = ("CONTRACT_RATE_GAP", "MA_BELOW_MEDICARE", "MPPR_POLICY", "CHARGE_BELOW_RATE", "SECOND_RATE",
            "EMA_DENIAL_AS_CONTRACTUAL", "G2211_DENIED")
FEES = ("FEE_SKIM", "TAKEBACK")
DUP_CARCS = frozenset({"18", "B13"})
SHORT = ("downcoding", "underpayment")
PATIENT_SHARE = frozenset({"1", "2", "3"})               # PR deductible, coinsurance, copay: the patient's share
PAID_STATUS = ("1", "2", "3", "19", "20")                 # claim statuses that carry a payer's processing
SECOND_STATUS = ("2", "3", "20", "21")                    # processed as the second or third payer

# EMA's claim actions and statuses, as the work they show (EMA's own words: 'Initial Appeal Electronic', 'Submitted
# Corrected Claim Portal', 'Sent Medical Records Fax', 'Claim Reprocessing', 'Claim Billed To Secondary Tertiary
# Insurance', 'Pending Remit Information', 'Claim In Process', the status 'Insurance Reprocessing' ...)
ACTION_KINDS = (("appeal", re.compile(r"APPEAL", re.I)), ("corrected", re.compile(r"CORRECTED|RESUBMIT", re.I)),
                ("records", re.compile(r"RECORDS", re.I)), ("reprocess", re.compile(r"REPROCESS|ADJUSTMENT REQUEST", re.I)),
                ("secondary", re.compile(r"SECONDARY|TERTIARY", re.I)),
                ("call", re.compile(r"CLAIM IN PROCESS|PENDING REMIT", re.I)))
WORK_WORDS = {"appeal": "an appeal", "corrected": "a corrected claim", "records": "records sent", "reprocess": "a reprocessing request",
              "secondary": "the claim billed to the next payer", "call": "a call or portal check", "sent": "sent to the payer",
              "fax": "a fax to the payer", "resubmitted": "a resubmission", "started": "started", "follow_up": "a follow-up date"}


# ------------------------------------------------------------------------------------------------------------ helpers
def _today(today: Any) -> str:
    return str(today or date.today().isoformat())[:10]


def _d(iso: Any) -> date | None:
    try:
        return date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


def _add(iso: Any, days: int) -> str | None:
    d = _d(iso)
    return (d + timedelta(days=int(days))).isoformat() if d else None


def _days(a: Any, b: Any) -> int | None:
    da, db = _d(a), _d(b)
    return (db - da).days if da and db else None


def usd(cents: Any) -> str:
    c = int(cents or 0)
    return f"-${abs(c) / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"


def day(iso: Any) -> str:
    """A date as the pages say it: Mar 3, 2026."""
    d = _d(iso)
    return f"{d:%b} {d.day}, {d.year}" if d else str(iso or "")


def tail(check: Any) -> str:
    """The last four letters or digits of a check or trace number: enough to find it, never the whole number."""
    return re.sub(r"[^A-Za-z0-9]", "", str(check or ""))[-4:]


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return default
    if not r or r[0] is None:
        return default
    try:
        return json.loads(r[0])
    except ValueError:
        return r[0]


def _chunks(seq: Sequence, n: int = CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _temp(conn: sqlite3.Connection, name: str, cols: Sequence[str], rows: Iterable[Sequence]) -> None:
    conn.execute(f"DROP TABLE IF EXISTS temp.{name}")
    conn.execute(f"CREATE TEMP TABLE {name} ({', '.join(cols)})")
    rows = list(rows)
    if rows:
        conn.executemany(f"INSERT OR IGNORE INTO temp.{name} VALUES ({','.join('?' * len(cols))})", rows)


def _drop(conn: sqlite3.Connection, *names: str) -> None:
    for n in names:
        conn.execute(f"DROP TABLE IF EXISTS temp.{n}")


def _ev(item: dict) -> dict:
    ev = item.get("evidence")
    if isinstance(ev, dict):
        return ev
    try:
        out = json.loads(ev) if ev else {}
    except (TypeError, ValueError):
        out = {}
    return out if isinstance(out, dict) else {}


_BILL = re.compile(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}")
_SUB = re.compile(r"C(\d{1,3})$")


def bill_of(pcn: Any) -> str | None:
    """An EMA claim id's bill (the id without its submission suffix, an F kept for the surgery center's claims)."""
    if not pcn:
        return None
    s = str(pcn)
    m = _BILL.fullmatch(s[1:] if s.startswith("F") and len(s) == 14 else s)
    if not m:
        return s
    return ("F" if s.startswith("F") and len(s) == 14 else "") + m.group(1)


def submission_no(pcn: Any) -> int:
    m = _SUB.search(str(pcn or ""))
    return int(m.group(1)) if m else 0


def _grp_kind(name: Any) -> tuple[str, str]:
    from .codes import normalize_payer
    return normalize_payer(name) if name else ("Unknown", "other")


_GK_CACHE: dict = {}


def row_group(x: dict) -> tuple[str, str]:
    """An 835 row's payer group and kind, its claim filing indicator considered (codes.payer_group_kind: a company's
    Medicare Advantage or Medicaid product is its own group, as the items name them)."""
    k = (x.get("payer"), x.get("cfi"))
    if k not in _GK_CACHE:
        if len(_GK_CACHE) > 5000:
            _GK_CACHE.clear()
        from .codes import payer_group_kind
        _GK_CACHE[k] = payer_group_kind(x.get("payer"), x.get("cfi")) if x.get("payer") else ("Unknown", "other")
    return _GK_CACHE[k]


def _family(group: Any) -> str:
    """Payer groups as the 835 and EMA vocabularies both spell them (ema_checks._family)."""
    from . import ema_checks as ec
    return ec._family(group)


def _same_payer(a: Any, b: Any) -> bool:
    return bool(a) and bool(b) and (a == b or _family(a) == _family(b))


def _code_meaning(code: Any) -> str:
    from .codes import carc_text
    return carc_text(str(code or "")) or ""


# ------------------------------------------------------------------------------------------- what a billing note says
_NOTE_KINDS = (
    ("appeal", re.compile(r"appeal|reconsider|redetermin|dispute|reopen", re.I)),
    ("call", re.compile(r"\bcall(ed|ing)?\b|\bspoke\b|\bspk\b|\brep\b|ref(erence)?\s*(#|no|num)|escalat|portal|availity|checked (the )?status", re.I)),
    ("resubmitted", re.compile(r"resubmit|re-submit|corrected claim|\brefil|\bre-fil|rebill|re-bill|sent (a )?(new )?claim|frequency 7|freq 7|replacement claim", re.I)),
    ("records", re.compile(r"medical record|\brecords\b|notes? sent|faxed|attach", re.I)),
    ("reprocess", re.compile(r"reprocess|overturn|adjustment request|reconsideration approved", re.I)),
    ("follow_up", re.compile(r"\bf/u\b|follow[ -]?up|allow \d", re.I)),
    ("write_off", re.compile(r"write ?off|written off|\bw/o\b|adjust(ed)? off", re.I)),
)
_NO_APPEAL = re.compile(r"\b(no|not|don'?t|do not|won'?t|will not|decided not to|did not)\s+(\w+\s+){0,2}(appeal|dispute|resubmit)", re.I)
_NOTE_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
_ALLOW = re.compile(r"allow\s*(\d{1,3})\s*(?:-|to)?\s*(\d{1,3})?\s*(?:business\s+|bus\.?\s+|working\s+)?(day|week)s?", re.I)
_NOTE_WORK = ("appeal", "call", "resubmitted", "records", "reprocess")


def classify_note(text: str | None, as_of: str | None = None) -> dict:
    """What an A/R billing note says was done, without keeping its words: {'kinds': [...], 'date': the newest date the
    note states (on or before as_of; a date written without a year takes as_of's year, or the year before when it would
    fall after as_of), 'wait_days': the longest "allow N days" it states}. Kinds: appeal, call, resubmitted, records,
    reprocess, follow_up, write_off, and no_appeal when the note records a decision not to appeal or resubmit (then appeal
    and resubmitted are dropped). For the A/R reader to store next to the note's hash (needs_D.md)."""
    t = str(text or "")
    if not t.strip():
        return {"kinds": [], "date": None, "wait_days": None}
    kinds = [k for k, rx in _NOTE_KINDS if rx.search(t)]
    if _NO_APPEAL.search(t):
        kinds = [k for k in kinds if k not in ("appeal", "resubmitted")] + ["no_appeal"]
    ref = _d(as_of) or date.today()
    dates = []
    for m in _NOTE_DATE.finditer(t):
        mo, dy, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= mo <= 12 and 1 <= dy <= 31):
            continue
        y = (int(yr) + 2000 if len(yr) == 2 else int(yr)) if yr else ref.year
        try:
            d = date(y, mo, dy)
        except ValueError:
            continue
        if not yr and d > ref:
            try:
                d = date(y - 1, mo, dy)
            except ValueError:
                continue
        if d <= ref and (ref - d).days <= 800:
            dates.append(d)
    wait = None
    for m in _ALLOW.finditer(t):
        n = max(int(m.group(1)), int(m.group(2) or 0))
        n *= 7 if m.group(3).lower().startswith("week") else 1
        wait = max(wait or 0, n)
    return {"kinds": kinds, "date": max(dates).isoformat() if dates else None, "wait_days": wait}


def action_kind(value: Any) -> str | None:
    """The work an EMA claim action or status shows (appeal, corrected, records, reprocess, secondary, call), or None."""
    v = str(value or "")
    if not v.strip() or v.strip().lower() in ("none", "other", "-"):
        return None
    for k, rx in ACTION_KINDS:
        if rx.search(v):
            return k
    return None


# ------------------------------------------------------------------------------------------- the payer's answer time
_RESPONSE_CACHE: dict = {}


def response_table(conn: sqlite3.Connection) -> dict:
    """Each payer group's answer time from the archive: days from the payer's receipt of a claim (the 835's claim received
    date) to the remittance that answered it, over the remittances of the two years before the newest one. {'groups':
    {group: {'p50', 'p90', 'n'}}, 'all': {...}, 'default': 45, 'newest': date}. Groups as the items name them
    (codes.payer_group_kind: a company's Medicare Advantage or Medicaid product is its own group)."""
    stored = _meta(conn, RESPONSE_KEY, None)
    newest = None
    if _table(conn, "transactions"):
        newest = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()[0]
    if isinstance(stored, dict) and stored.get("groups") is not None and stored.get("newest") == newest:
        return stored
    key = (id(conn), newest)
    if key in _RESPONSE_CACHE:
        return _RESPONSE_CACHE[key]
    out = {"groups": {}, "all": None, "default": DEFAULT_RESPONSE_DAYS, "newest": newest}
    if newest and _table(conn, "claims") and "received_date" in _cols(conn, "claims"):
        from .codes import payer_group_kind
        cache: dict = {}

        def grp(name: Any, cfi: Any) -> str:
            k = (name, cfi)
            if k not in cache:
                cache[k] = payer_group_kind(name, cfi)[0]
            return cache[k]
        conn.create_function("st_grp", 2, grp)
        since = _add(newest, -365 * RESPONSE_YEARS)
        rows = conn.execute("""SELECT st_grp(c.payer_name, c.claim_filing_indicator) AS g,
                                      CAST(julianday(t.payment_date) - julianday(c.received_date) AS INTEGER) AS d
                               FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
                               WHERE COALESCE(t.superseded, 0) = 0 AND c.received_date IS NOT NULL AND t.payment_date >= ?
                                 AND c.claim_status_code IN ('1','2','3','4','19','20')""", (since,)).fetchall()
        by: dict[str, list] = {}
        everything: list = []
        for g, d in rows:
            if d is None or d < 0 or d > 730:
                continue
            by.setdefault(g or "Unknown", []).append(d)
            everything.append(d)

        def pct(v: list, p: int) -> int:
            v = sorted(v)
            return v[min(len(v) - 1, max(0, (len(v) * p + 99) // 100 - 1))]
        for g, v in by.items():
            out["groups"][g] = {"p50": pct(v, 50), "p90": pct(v, 90), "n": len(v)}
        if len(everything) >= MIN_ANSWERS:
            out["all"] = {"p50": pct(everything, 50), "p90": pct(everything, 90), "n": len(everything)}
    if len(_RESPONSE_CACHE) > 8:
        _RESPONSE_CACHE.clear()
    _RESPONSE_CACHE[key] = out
    return out


def save_response_days(conn: sqlite3.Connection) -> dict:
    """Measure every payer group's answer time and keep it in meta states:response_days, so a serverless dashboard copy
    (which holds only the claims of items) answers the same. The detection run calls this once, before the states."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("DELETE FROM meta WHERE key=?", (RESPONSE_KEY,))
    _RESPONSE_CACHE.clear()
    t = response_table(conn)
    t = dict(t, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (RESPONSE_KEY, json.dumps(t, sort_keys=True)))
    conn.commit()
    return t


def response_days(conn: sqlite3.Connection, payer_group: str | None, work: str | None = None,
                  payer_kind: str | None = None) -> int:
    """The payer's answer time, the 90th percentile from the archive (response_table), 45 days when fewer than 20 of its
    answers are on file (the all-payer figure first, when there is one). By the kind of work waiting for the answer:
      None, 'claim'                     the measured time alone
      'corrected', 'resubmitted', 'sent_claim', 'secondary'   plus the week a claim takes to reach the payer
      'appeal', 'records', 'reprocess', 'sent', 'fax'         at least the payer's own decision time for an appeal
                                                             (60 days Medicare and Medicare Advantage, 30 commercial)
      'call', 'follow_up'               at least 30 days ("allow 30 days")
      'forwarded'                       45 days (a claim forwarded to another carrier)"""
    t = response_table(conn)
    groups = t.get("groups") or {}
    g = groups.get(payer_group or "")
    if g is None and payer_group:
        fam = _family(payer_group)
        g = next((v for k, v in groups.items() if _family(k) == fam and (v.get("n") or 0) >= MIN_ANSWERS), None)
    if g and (g.get("n") or 0) >= MIN_ANSWERS:
        base = int(g["p90"])
    elif t.get("all"):
        base = int(t["all"]["p90"])
    else:
        base = DEFAULT_RESPONSE_DAYS
    base = max(1, base)
    if work in (None, "claim"):
        return base
    if work in ("corrected", "resubmitted", "sent_claim", "secondary"):
        return base + CLAIM_TRANSIT_DAYS
    if work in ("appeal", "records", "reprocess", "sent", "fax"):
        kind = payer_kind or _grp_kind(payer_group)[1]
        return max(base, APPEAL_WAIT.get(kind or "other", APPEAL_WAIT["other"]))
    if work in ("call", "follow_up"):
        return max(base, CALL_WAIT_DAYS)
    if work == "forwarded":
        return FORWARD_WAIT_DAYS
    return base


# ------------------------------------------------------------------------------- the payer's record for a pattern
RECOVERY_MIN = 5                # fewer earlier items of a pattern than this: no rate
_RECOVERY_CACHE: dict = {}


def _pattern_key(item: dict) -> tuple:
    """An item's pattern: payer group x code x reason (the CARC; the signal when the item has none, as for a short payment)."""
    return (item.get("payer_group") or "", item.get("procedure_code") or "", str(item.get("carc") or item.get("signal") or ""))


def recovery_table(conn: sqlite3.Connection) -> dict:
    """RemitWatch's own record of every pattern (payer group x code x reason), from all the items it ever listed, open or
    closed: {pattern: {'n', 'paid', 'lost'}}. 'paid': closed as paid (by the analysis on a later payment, or by a person
    who recorded a recovery); 'lost': marked Lost or let go. Worked out once per store state."""
    if not _table(conn, "findings"):
        return {}
    cols = _cols(conn, "findings")
    if not {"payer_group", "procedure_code", "carc", "signal", "status"} <= cols:
        return {}
    stamp = conn.execute("SELECT COUNT(*), MAX(rowid) FROM findings").fetchone()
    key = (id(conn), tuple(stamp))
    if key in _RECOVERY_CACHE:
        return _RECOVERY_CACHE[key]
    auto = "auto_note" if "auto_note" in cols else "NULL"
    rec = "recovered_cents" if "recovered_cents" in cols else "0"
    out: dict = {}
    for grp, code, carc, sig, cat, status, note, cents in conn.execute(
            f"""SELECT payer_group, procedure_code, carc, signal, category, status, {auto}, {rec} FROM findings
                WHERE category IN ('denial', 'bundling', 'downcoding', 'underpayment')"""):
        k = (grp or "", code or "", str(carc or sig or ""))
        d = out.setdefault(k, {"n": 0, "paid": 0, "lost": 0})
        d["n"] += 1
        if status == "resolved" and (note or (cents or 0) > 0):
            d["paid"] += 1
        elif status in ("lost", "dismissed"):
            d["lost"] += 1
    if len(_RECOVERY_CACHE) > 4:
        _RECOVERY_CACHE.clear()
    _RECOVERY_CACHE[key] = out
    return out


def recovery_of(conn: sqlite3.Connection, item: dict, table: dict | None = None) -> dict:
    """The payer's record for the item's pattern, the item itself left out: {'n', 'paid', 'lost', 'rate' (None under
    RECOVERY_MIN earlier items), 'basis'}. What the worklist multiplies an item's dollars by (non-negotiable 4)."""
    t = table if table is not None else recovery_table(conn)
    k = _pattern_key(item)
    d = dict(t.get(k) or {"n": 0, "paid": 0, "lost": 0})
    if d["n"] and item.get("status") not in ("resolved", "lost", "dismissed"):
        d["n"] -= 1             # this item, still open
    grp, code, reason = k
    what = f"{grp or 'this payer'}, {code or 'this code'}, {reason}"
    if d["n"] < RECOVERY_MIN:
        return dict(d, rate=None, basis=f"Too few earlier items like this ({d['n']}: {what}) to give a rate")
    rate = round(d["paid"] / d["n"], 3)
    return dict(d, rate=rate, basis=f"{d['paid']} of the {d['n']} earlier items like this ({what}) were later paid")


# ------------------------------------------------------------------------------------------------ gathering the facts
def _list(v: Any) -> list:
    """An evidence value as a list: some items keep a count where others keep the rows."""
    return list(v) if isinstance(v, (list, tuple)) else []


def _item_codes(item: dict) -> list[str]:
    out = []
    for c in (item.get("billed_code"), item.get("original_procedure_code"), item.get("procedure_code")):
        if c and c not in out:
            out.append(c)
    return out


def _service_keys(item: dict) -> list[tuple]:
    """The (date, code) services an item is about: its line, or the codes a claim-level or EMA-only item lists."""
    ev = _ev(item)
    svc = item.get("service_date")
    if item.get("line_key"):
        return [(svc, c) for c in _item_codes(item)] if svc else []
    out: list[tuple] = []
    for p in _list(ev.get("unpaid")):
        if isinstance(p, (list, tuple)) and len(p) == 2 and p[0]:
            out.append((p[1] or svc, p[0]))
    for ln in _list(ev.get("lines")):
        if isinstance(ln, (list, tuple)) and len(ln) >= 2:
            # EMA items list [code, date, ...]; NO_ANSWER and REJECTED from the exports list [date, code, ...] or dicts
            a, b = ln[0], ln[1]
            if isinstance(a, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", a or ""):
                out.append((a, b))
            elif b and isinstance(b, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", b[:10] or ""):
                out.append((b[:10], a))
        elif isinstance(ln, dict) and (ln.get("cpt") or ln.get("code")):
            out.append((ln.get("service_date") or ln.get("svc") or svc, ln.get("cpt") or ln.get("code")))
    if not out:
        for c in _list(ev.get("codes")):
            if c and svc:
                out.append((svc, c))
    if not out and svc and item.get("procedure_code"):
        out.append((svc, item["procedure_code"]))
    seen, uniq = set(), []
    for k in out:
        if k[0] and k[1] and k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def _links_of(item: dict, outcome: dict | None) -> list[int]:
    ev = _ev(item)
    lks: list[int] = []
    for v in [(outcome or {}).get("link"), item.get("ema_link"), ev.get("ema_link")] + _list(ev.get("ema_links")):
        try:
            if v is not None and int(v) not in lks:
                lks.append(int(v))
        except (TypeError, ValueError):
            continue
    for ln in (outcome or {}).get("lines") or []:
        try:
            if ln and ln[0] is not None and int(ln[0]) not in lks:
                lks.append(int(ln[0]))
        except (TypeError, ValueError):
            continue
    return lks


def _r835(conn: sqlite3.Connection, items: list[dict]) -> dict[str, dict]:
    """Every 835 adjudication of each item's services (its bill: every submission, every payer), reversals paired, with
    each line's reasons and remarks, and the takebacks (PLB) whose reference names the claim."""
    out: dict[str, dict] = {f["finding_id"]: {"rows": [], "plb": [], "bill": bill_of(f.get("patient_control_number"))}
                            for f in items}
    if not _table(conn, "claims") or not _table(conn, "lines"):
        return out
    want: dict[str, list] = {}
    for f in items:
        b = bill_of(f.get("patient_control_number"))
        if b:
            want.setdefault(b, []).append(f)
    if not want:
        return out
    tcols = _cols(conn, "transactions")
    ccols = _cols(conn, "claims")
    lcols = _cols(conn, "lines")
    has_adj = _table(conn, "adjustments")
    has_rmk = _table(conn, "remarks")
    method = "t.payment_method" if "payment_method" in tcols else "NULL"
    trace = "t.trace_number" if "trace_number" in tcols else "c.trace_number"
    recv = "c.received_date" if "received_date" in ccols else "NULL"
    freq = "c.claim_frequency_code" if "claim_frequency_code" in ccols else "NULL"
    pccn = "c.payer_claim_control_number" if "payer_claim_control_number" in ccols else "NULL"
    xover = "c.crossover_carrier_name" if "crossover_carrier_name" in ccols else "NULL"
    cpat = "c.patient_responsibility_cents" if "patient_responsibility_cents" in ccols else "NULL"
    lcn = "l.line_control_number" if "line_control_number" in lcols else "NULL"
    units = "l.paid_units" if "paid_units" in lcols else "NULL"
    ounits = "l.original_units" if "original_units" in lcols else "NULL"
    mods = "l.modifiers" if "modifiers" in lcols else "NULL"
    allowed = "l.allowed_cents" if "allowed_cents" in lcols else "NULL"
    sup = "COALESCE(t.superseded, 0)" if "superseded" in tcols else "0"
    bills = sorted(want)
    rows_by_bill: dict[str, list] = {}
    for part in _chunks(bills):
        _temp(conn, "st_b", ["bill TEXT PRIMARY KEY", "hi TEXT"], [(b, b + "~") for b in part])
        cur = conn.execute(f"""SELECT b.bill, c.claim_key, c.patient_control_number, c.claim_status_code, c.payer_name,
                    t.payment_date, {trace}, {method}, {recv}, {freq}, {pccn}, {xover}, c.charge_cents, c.paid_cents, {cpat},
                    l.line_key, COALESCE(l.service_date, c.statement_from), l.procedure_code, l.original_procedure_code, {mods},
                    l.charge_cents, l.paid_cents, {allowed}, {units}, {ounits}, {lcn}, c.transaction_key, c.claim_filing_indicator
                FROM temp.st_b b JOIN claims c ON c.patient_control_number >= b.bill AND c.patient_control_number < b.hi
                JOIN transactions t ON t.transaction_key = c.transaction_key
                LEFT JOIN lines l ON l.claim_key = c.claim_key
                WHERE {sup} = 0""")
        for r in cur.fetchall():
            if bill_of(r[2]) != r[0]:
                continue
            rows_by_bill.setdefault(r[0], []).append(r)
    _drop(conn, "st_b")
    # reasons and remarks of those lines and claims
    lkeys = sorted({r[15] for rs in rows_by_bill.values() for r in rs if r[15]})
    ckeys = sorted({r[1] for rs in rows_by_bill.values() for r in rs if r[1]})
    adj_l: dict[str, list] = {}
    adj_c: dict[str, list] = {}
    rmk_l: dict[str, list] = {}
    rmk_c: dict[str, list] = {}
    if has_adj and (lkeys or ckeys):
        _temp(conn, "st_lk", ["k TEXT PRIMARY KEY"], [(k,) for k in lkeys])
        _temp(conn, "st_ck", ["k TEXT PRIMARY KEY"], [(k,) for k in ckeys])
        for lk, g, rc, amt in conn.execute("""SELECT a.line_key, a.group_code, a.reason_code, a.amount_cents FROM adjustments a
                                               WHERE a.line_key IN (SELECT k FROM temp.st_lk)"""):
            adj_l.setdefault(lk, []).append((g or "", rc or "", amt or 0))
        for ck, g, rc, amt in conn.execute("""SELECT a.claim_key, a.group_code, a.reason_code, a.amount_cents FROM adjustments a
                                               WHERE a.claim_key IN (SELECT k FROM temp.st_ck) AND a.line_key IS NULL"""):
            adj_c.setdefault(ck, []).append((g or "", rc or "", amt or 0))
        if has_rmk:
            for lk, code in conn.execute("SELECT r.line_key, r.code FROM remarks r WHERE r.line_key IN (SELECT k FROM temp.st_lk)"):
                rmk_l.setdefault(lk, []).append(code)
            for ck, code in conn.execute("""SELECT r.claim_key, r.code FROM remarks r WHERE r.claim_key IN (SELECT k FROM temp.st_ck)
                                            AND r.line_key IS NULL"""):
                rmk_c.setdefault(ck, []).append(code)
        _drop(conn, "st_lk", "st_ck")
    plb_by_bill: dict[str, list] = {}
    if _table(conn, "plb") and "reference" in _cols(conn, "plb"):
        pcns = sorted({r[2] for rs in rows_by_bill.values() for r in rs if r[2]})
        if pcns:
            _temp(conn, "st_pc", ["pcn TEXT PRIMARY KEY"], [(p,) for p in pcns])
            for ref, rc, amt, paid_on, payer, tr in conn.execute(
                    """SELECT p.reference, p.reason_code, p.amount_cents, p.payment_date, p.payer_name, p.trace_number FROM plb p
                       WHERE p.reference IS NOT NULL AND p.reason_code IN ('WO','FB','72','J1','L6','CS')"""):
                hit = next((x for x in pcns if x and x in str(ref or "")), None) if ref else None
                if hit:
                    plb_by_bill.setdefault(bill_of(hit), []).append({"reason": rc, "cents": amt or 0, "date": paid_on, "payer": payer,
                                                                      "check_tail": tail(tr), "claim_id": hit})
            _drop(conn, "st_pc")
    for b, fs in want.items():
        recs = []
        for r in rows_by_bill.get(b, []):
            recs.append({"claim_key": r[1], "pcn": r[2], "status": str(r[3] or ""), "payer": r[4], "date": r[5], "check_tail": tail(r[6]),
                         "method": r[7], "received": r[8], "freq": r[9], "pccn": r[10], "crossover": r[11], "claim_charge": r[12],
                         "claim_paid": r[13], "claim_pr": r[14], "line_key": r[15], "svc": r[16], "code": r[17], "ocode": r[18],
                         "modifiers": r[19], "charge": r[20], "paid": r[21], "allowed": r[22], "units": r[23], "ounits": r[24],
                         "lcn": r[25], "tk": r[26], "cfi": r[27],
                         "adj": adj_l.get(r[15], []) if r[15] else [], "rmk": rmk_l.get(r[15], []) if r[15] else [],
                         "cadj": adj_c.get(r[1], []), "crmk": rmk_c.get(r[1], [])})
        _pair_reversals(recs)
        for f in fs:
            keys = set(_service_keys(f))
            codes = {c for _s, c in keys}
            dates = {s for s, _c in keys}
            mine = []
            for x in recs:
                if x["line_key"] is None:
                    # a claim with no service lines (a whole-claim answer): it answers every service of the claim
                    if x["pcn"] and (not dates or x["svc"] in dates or x["svc"] is None):
                        mine.append(x)
                    continue
                if (x["svc"], x["code"]) in keys or (x["svc"], x["ocode"]) in keys or (not dates and (x["code"] in codes or x["ocode"] in codes)):
                    mine.append(x)
            mine.sort(key=lambda x: (x["date"] or "", x["status"] == "22", x["claim_key"] or "", x["line_key"] or ""))
            out[f["finding_id"]]["rows"] = mine
            out[f["finding_id"]]["plb"] = plb_by_bill.get(b, [])
    return out


def _pair_reversals(recs: list[dict]) -> None:
    """Mark each status-22 line with the line it reverses, and that line as reversed. A reversal repeats the received
    date of the claim it reverses (or a later one, the date the payer received the request to reprocess), so a line the
    payer received after the reversal's received date is never its pair (the new answer that often comes on the same
    remittance); among the rest (same payer, code and charge, answered on or before the reversal): the same received
    date first, then the line whose reasons the reversal negates exactly, then the opposite payment, then an earlier
    remittance, then the newest. A reversal of a claim the archive does not hold stays unpaired."""
    for x in recs:
        x.setdefault("reversed_by", None)
        x.setdefault("reverses", None)
    for x in sorted((r for r in recs if r["status"] == "22"), key=lambda r: r["date"] or ""):
        rx = x.get("received")
        cands = [o for o in recs if o is not x and o["status"] != "22" and not o.get("reversed_by")
                 and (o["date"] or "") <= (x["date"] or "") and _same_payer(row_group(o)[0], row_group(x)[0])
                 and o["code"] == x["code"] and abs(o["charge"] or 0) == abs(x["charge"] or 0)
                 and (o["pccn"] == x["pccn"] or not o["pccn"] or not x["pccn"] or o["pcn"] == x["pcn"])
                 and not (rx and o.get("received") and o["received"] > rx)]
        if not cands:
            continue
        mirror = sorted((g, c, -a) for g, c, a in x["adj"])

        def score(o: dict) -> tuple:
            return (bool(rx) and o.get("received") == rx, bool(mirror) and sorted(o["adj"]) == mirror,
                    (o["paid"] or 0) == -(x["paid"] or 0), (o["date"] or "") < (x["date"] or ""), o["date"] or "",
                    o["line_key"] or "")
        pick = max(cands, key=score)
        pick["reversed_by"] = x["line_key"] or x["claim_key"]
        x["reverses"] = pick["line_key"] or pick["claim_key"]


def _ema(conn: sqlite3.Connection, items: list[dict], outcomes: dict) -> dict[str, dict]:
    """Each item's EMA lines (its outcome's lines, else its bill's mapped lines of its services), their payments,
    reasons, status and action history, and the surgery center's twin line when EMA has one."""
    out: dict[str, dict] = {f["finding_id"]: {"lines": [], "pays": [], "reasons": [], "events": [], "twins": [], "links": []}
                            for f in items}
    if not _table(conn, "ema_lines"):
        return out
    ecols = _cols(conn, "ema_lines")
    want: dict[str, list] = {}         # fid -> [(link, svc, cpt)]
    need_map: dict[str, list] = {}     # fid -> bill, services (no outcome lines)
    for f in items:
        fid = f["finding_id"]
        o = outcomes.get(fid) or {}
        lines = [(int(ln[0]), ln[1], ln[2]) for ln in o.get("lines") or [] if ln and ln[0] is not None]
        if lines:
            want[fid] = lines
            continue
        lks = _links_of(f, o)
        keys = _service_keys(f)
        if lks:
            want[fid] = [(lk, s, c) for lk in lks for s, c in keys] or [(lk, None, None) for lk in lks]
        b = bill_of(f.get("patient_control_number"))
        if b:
            need_map[fid] = [b, keys]
    if need_map and _table(conn, "ema_link_map"):
        bills = sorted({v[0] for v in need_map.values()})
        by_bill: dict[str, list] = {}
        mcols = _cols(conn, "ema_link_map")
        fac = "COALESCE(m.facility, 0)" if "facility" in mcols else "0"
        for part in _chunks(bills):
            _temp(conn, "st_mb", ["bill TEXT PRIMARY KEY"], [(b,) for b in part])
            for b, lk in conn.execute(f"""SELECT m.bill, m.link FROM ema_link_map m WHERE m.bill IN (SELECT bill FROM temp.st_mb)
                                          AND COALESCE(m.conflict, 0) = 0 AND {fac} = 0"""):
                by_bill.setdefault(b, []).append(lk)
        _drop(conn, "st_mb")
        for fid, (b, keys) in need_map.items():
            have = want.get(fid) or []
            for lk in by_bill.get(b, []):
                for s, c in keys:
                    if (lk, s, c) not in have:
                        have.append((lk, s, c))
            if have:
                want[fid] = have
    all_keys = sorted({k for v in want.values() for k in v}, key=lambda k: (k[0], k[1] or "", k[2] or ""))
    if not all_keys:
        return out
    line_cols = [c for c in ("link", "svc", "cpt", "bill", "claim_id", "charges", "ins_paid", "ins_paid_no835", "contractual",
                             "noncontractual", "pat_paid_joined", "pat_adj_joined", "pat_join", "pat_after_denial", "balance",
                             "status", "status_class", "action", "action_on", "as_of", "facility", "twin_link", "units", "allowed",
                             "first_charge_posted", "first_posted", "last_posted", "first_ins_posting", "payer_group", "payer_kind",
                             "primary_payer", "secondary_payer", "denial_first_posted", "flags") if c in ecols]
    lines: dict[tuple, dict] = {}
    exact = [k for k in all_keys if k[1] is not None and k[2] is not None]
    loose = sorted({k[0] for k in all_keys if k[1] is None or k[2] is None})
    for part in _chunks(exact):
        _temp(conn, "st_el", ["link INTEGER", "svc TEXT", "cpt TEXT"], part)
        for r in conn.execute(f"""SELECT {', '.join('l.' + c for c in line_cols)} FROM temp.st_el k JOIN ema_lines l
                                  ON l.link = k.link AND l.svc = k.svc AND l.cpt = k.cpt"""):
            d = dict(zip(line_cols, r))
            lines[(d["link"], d["svc"], d["cpt"])] = d
    for part in _chunks(loose):
        _temp(conn, "st_ll", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
        for r in conn.execute(f"SELECT {', '.join('l.' + c for c in line_cols)} FROM temp.st_ll k JOIN ema_lines l ON l.link = k.link"):
            d = dict(zip(line_cols, r))
            lines[(d["link"], d["svc"], d["cpt"])] = d
    _drop(conn, "st_el", "st_ll")
    keys_found = sorted(lines)
    pays: dict[tuple, list] = {}
    reasons: dict[tuple, list] = {}
    pcols = _cols(conn, "ema_line_payments")
    for part in _chunks(keys_found):
        _temp(conn, "st_ek", ["link INTEGER", "svc TEXT", "cpt TEXT"], part)
        if pcols:
            in835 = "y.in_835" if "in_835" in pcols else "0"
            tkey = "y.trace_key" if "trace_key" in pcols else "NULL"
            for lk, svc, cpt, chk, posted, payer, grp, cents, i8, role, tk in conn.execute(
                    f"""SELECT y.link, y.svc, y.cpt, y.check_no, y.posted, y.payer, y.payer_group, y.ins_paid, {in835},
                               {'y.role' if 'role' in pcols else 'NULL'}, {tkey}
                        FROM temp.st_ek k JOIN ema_line_payments y ON y.link = k.link AND y.svc = k.svc AND y.cpt = k.cpt"""):
                pays.setdefault((lk, svc, cpt), []).append({"check": chk or "", "check_tail": tail(chk), "posted": posted, "payer": payer or "",
                                                           "group": grp, "cents": cents or 0, "in_835": bool(i8), "role": role,
                                                           "trace_key": tk})
        if _table(conn, "ema_line_reasons"):
            for lk, svc, cpt, grp, code, cls, payer, contr, nonc, first, last in conn.execute(
                    """SELECT z.link, z.svc, z.cpt, z.grp, z.code, z.reason_class, z.payer, z.contractual, z.noncontractual,
                              z.first_posted, z.last_posted
                       FROM temp.st_ek k JOIN ema_line_reasons z ON z.link = k.link AND z.svc = k.svc AND z.cpt = k.cpt"""):
                reasons.setdefault((lk, svc, cpt), []).append({"grp": grp or "", "code": code or "", "cls": cls, "payer": payer or "",
                                                              "contr": contr or 0, "nonc": nonc or 0, "first": first, "last": last})
    _drop(conn, "st_ek")
    events: dict[int, list] = {}
    if _table(conn, "ema_link_events"):
        lks = sorted({k[0] for k in keys_found} | {lk for v in want.values() for lk, _s, _c in v})
        for part in _chunks(lks):
            _temp(conn, "st_ev", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
            for lk, kind, value, seen in conn.execute("""SELECT e.link, e.kind, e.value, e.first_seen FROM temp.st_ev k
                                                         JOIN ema_link_events e ON e.link = k.link"""):
                events.setdefault(lk, []).append({"kind": kind, "value": value, "first_seen": seen})
        _drop(conn, "st_ev")
    twins: dict[int, dict] = {}
    twin_links = sorted({ln.get("twin_link") for ln in lines.values() if ln.get("twin_link")})
    if twin_links:
        for part in _chunks(twin_links):
            _temp(conn, "st_tw", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
            for r in conn.execute(f"SELECT {', '.join('l.' + c for c in line_cols)} FROM temp.st_tw k JOIN ema_lines l ON l.link = k.link"):
                d = dict(zip(line_cols, r))
                twins.setdefault(d["link"], {})[(d["svc"], d["cpt"])] = d
        _drop(conn, "st_tw")
    for f in items:
        fid = f["finding_id"]
        mine = [lines[k] for k in sorted(set(want.get(fid) or [])) if k in lines]
        if not mine:
            # loose keys (link only) give every line of the link; keep the item's services when it names any
            lk_only = {k[0] for k in want.get(fid) or [] if k[1] is None or k[2] is None}
            keys = set(_service_keys(f))
            mine = [ln for k, ln in sorted(lines.items()) if k[0] in lk_only and (not keys or (k[1], k[2]) in keys)]
        # one EMA line per service: the item's own claim number first, then a line with a charge, then the newest
        explicit = set(_links_of(f, outcomes.get(fid)))
        best: dict[tuple, tuple] = {}
        for ln in mine:
            rank = (ln["link"] in explicit, (ln.get("charges") or 0) > 0, ln.get("last_posted") or "", -ln["link"])
            cur = best.get((ln["svc"], ln["cpt"]))
            if cur is None or rank > cur[0]:
                best[(ln["svc"], ln["cpt"])] = (rank, ln)
        mine = [v[1] for _k, v in sorted(best.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or ""))]
        o = out[fid]
        o["lines"] = mine
        o["links"] = sorted({ln["link"] for ln in mine} | set(_links_of(f, outcomes.get(fid))))
        for ln in mine:
            k = (ln["link"], ln["svc"], ln["cpt"])
            o["pays"] += [dict(p, svc=ln["svc"], cpt=ln["cpt"], link=ln["link"]) for p in pays.get(k, [])]
            o["reasons"] += [dict(z, svc=ln["svc"], cpt=ln["cpt"], link=ln["link"]) for z in reasons.get(k, [])]
            tw = twins.get(ln.get("twin_link") or -1, {}).get((ln["svc"], ln["cpt"]))
            if tw:
                o["twins"].append(tw)
        for lk in o["links"]:
            o["events"] += [dict(e, link=lk) for e in events.get(lk, [])]
        o["pays"].sort(key=lambda p: (p["posted"] or "", p["check"]))
        o["reasons"].sort(key=lambda z: (z["first"] or "", z["grp"], z["code"]))
    return out


def _ar(conn: sqlite3.Connection, items: list[dict], links: dict[str, list]) -> dict[str, list]:
    """Each item's A/R rows (its claim id, its bill's other submissions, its EMA claim numbers), with the item history's
    first-seen and worked marks, and what the billing note says when the reader classified it (note_kind)."""
    out: dict[str, list] = {f["finding_id"]: [] for f in items}
    if not _table(conn, "ema_ar"):
        return out
    acols = _cols(conn, "ema_ar")
    icols = _cols(conn, "ema_ar_items") if _table(conn, "ema_ar_items") else set()
    extra = [c for c in ("note_kind", "note_date", "note_wait") if c in acols]
    sel = ["a.claim_id", "a.link", "a.svc", "a.cpt", "a.as_of", "a.exposure_cents" if "exposure_cents" in acols else "a.balance_cents",
           "a.denial_cents", "a.claim_status", "a.status_class", "a.claim_action", "a.follow_up", "a.has_note", "a.denial_category",
           "a.party", "a.coverage", "a.item_key"] + [f"a.{c}" for c in extra]
    names = ["claim_id", "link", "svc", "cpt", "as_of", "exposure", "denial", "status", "status_class", "action", "follow_up",
             "has_note", "denial_category", "party", "coverage", "item_key"] + extra
    hist: dict[str, dict] = {}
    rows_by_cid: dict[str, list] = {}
    rows_by_link: dict[int, list] = {}
    pcns = sorted({f.get("patient_control_number") for f in items if f.get("patient_control_number")})
    bills = sorted({bill_of(p) for p in pcns if bill_of(p)})
    for part in _chunks(bills):
        _temp(conn, "st_ab", ["bill TEXT PRIMARY KEY", "hi TEXT"], [(b, b + "~") for b in part])
        for r in conn.execute(f"""SELECT b.bill, {', '.join(sel)} FROM temp.st_ab b JOIN ema_ar a
                                  ON a.claim_id >= b.bill AND a.claim_id < b.hi"""):
            if bill_of(r[1]) == r[0]:
                rows_by_cid.setdefault(r[0], []).append(dict(zip(names, r[1:])))
    _drop(conn, "st_ab")
    lks = sorted({lk for v in links.values() for lk in v})
    for part in _chunks(lks):
        _temp(conn, "st_al", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
        for r in conn.execute(f"SELECT {', '.join(sel)} FROM temp.st_al k JOIN ema_ar a ON a.link = k.link"):
            d = dict(zip(names, r))
            rows_by_link.setdefault(d["link"], []).append(d)
    _drop(conn, "st_al")
    keys = sorted({d["item_key"] for v in list(rows_by_cid.values()) + list(rows_by_link.values()) for d in v if d.get("item_key")})
    if keys and icols:
        isel = [c for c in ("item_key", "first_seen", "last_seen", "note_changed", "worked_on", "worked_how", "last_follow_up") if c in icols]
        for part in _chunks(keys):
            _temp(conn, "st_ai", ["k TEXT PRIMARY KEY"], [(x,) for x in part])
            for r in conn.execute(f"SELECT {', '.join('i.' + c for c in isel)} FROM temp.st_ai k JOIN ema_ar_items i ON i.item_key = k.k"):
                d = dict(zip(isel, r))
                hist[d["item_key"]] = d
        _drop(conn, "st_ai")
    for f in items:
        fid = f["finding_id"]
        seen, rows = set(), []
        for d in rows_by_cid.get(bill_of(f.get("patient_control_number")) or "", []) + [d for lk in links.get(fid, [])
                                                                                     for d in rows_by_link.get(lk, [])]:
            k = (d.get("item_key"), d.get("as_of"), d.get("cpt"), d.get("svc"))
            if k in seen:
                continue
            seen.add(k)
            rows.append(dict(d, **{f"hist_{a}": b for a, b in (hist.get(d.get("item_key")) or {}).items() if a != "item_key"}))
        rows.sort(key=lambda d: (d.get("as_of") or "", d.get("svc") or "", d.get("cpt") or ""))
        out[fid] = rows
    return out


def _rw(conn: sqlite3.Connection, items: list[dict]) -> dict[str, dict]:
    """RemitWatch's own records of each item: its history (statuses, events) and its faxes."""
    out: dict[str, dict] = {f["finding_id"]: {"events": [], "faxes": []} for f in items}
    fids = sorted(out)
    if _table(conn, "finding_events"):
        for part in _chunks(fids):
            _temp(conn, "st_fe", ["fid TEXT PRIMARY KEY"], [(x,) for x in part])
            for fid, at, kind, fr, to, cents, actor, note in conn.execute(
                    """SELECT e.finding_id, e.at, e.kind, e.from_status, e.to_status, e.amount_cents, e.actor, e.note
                       FROM finding_events e WHERE e.finding_id IN (SELECT fid FROM temp.st_fe) ORDER BY e.at, e.id"""):
                out[fid]["events"].append({"at": at, "kind": kind, "from": fr, "to": to, "cents": cents, "actor": actor or "",
                                           "note": note})
        _drop(conn, "st_fe")
    if _table(conn, "faxes"):
        fcols = _cols(conn, "faxes")
        sel = [c for c in ("finding_id", "direction", "purpose", "level", "status", "created_at", "sent_at", "number", "payer_group")
               if c in fcols]
        for part in _chunks(fids):
            _temp(conn, "st_fx", ["fid TEXT PRIMARY KEY"], [(x,) for x in part])
            for r in conn.execute(f"""SELECT {', '.join('x.' + c for c in sel)} FROM faxes x
                                      WHERE x.finding_id IN (SELECT fid FROM temp.st_fx) ORDER BY x.created_at"""):
                d = dict(zip(sel, r))
                d["number_tail"] = tail(d.pop("number", None))
                out[d["finding_id"]]["faxes"].append(d)
        _drop(conn, "st_fx")
    return out


def _export_rows(conn: sqlite3.Connection, items: list[dict]) -> dict[str, list]:
    """The weekly exports' claim-status rows of each item's bill (every submission): submitted and denied dates."""
    out: dict[str, list] = {f["finding_id"]: [] for f in items}
    if not _table(conn, "ema_rows"):
        return out
    want: dict[str, list] = {}
    for f in items:
        b = bill_of(f.get("patient_control_number"))
        if b:
            want.setdefault(b, []).append(f["finding_id"])
    for part in _chunks(sorted(want)):
        _temp(conn, "st_eb", ["bill TEXT PRIMARY KEY", "hi TEXT"], [(b, b + "~") for b in part])
        for b, cid, st, sub, den, svc, cpt in conn.execute(
                """SELECT b.bill, e.claim_id, e.claim_status, e.claim_submission_date, e.denial_date, e.service_date, e.cpt
                   FROM temp.st_eb b JOIN ema_rows e ON e.claim_id >= b.bill AND e.claim_id < b.hi
                   WHERE e.family = 'claim_status'"""):
            if bill_of(cid) != b:
                continue
            for fid in want[b]:
                out[fid].append({"claim_id": cid, "status": st, "submitted": (sub or "")[:10] or None, "denied": (den or "")[:10] or None,
                                 "svc": (svc or "")[:10] or None, "cpt": cpt})
    _drop(conn, "st_eb")
    return out


def _context(conn: sqlite3.Connection, today: str) -> dict:
    """What every item's state shares: the reports' dates, freshness, the first report of each kind, the write-off policy."""
    ctx: dict = {"today": today, "prod_as_of": None, "prod_fresh": False, "ar_as_of": None, "first_prod": None, "first_ar": None,
                 "newest_835": None, "boxes": [], "group_last_835": {}}
    if _table(conn, "ema_report_files"):
        for report, lo, hi in conn.execute("""SELECT report, MIN(as_of), MAX(as_of) FROM ema_report_files
                                              WHERE status IN ('current','partly_replaced','replaced','superseded') GROUP BY report"""):
            if report == "production":
                ctx["first_prod"], ctx["prod_as_of"] = lo, hi
            elif report == "ar_report":
                ctx["first_ar"], ctx["ar_as_of"] = lo, hi
        try:
            from . import ema_checks as ec
            fr = ec.freshness(conn, today)
            ctx["prod_as_of"] = fr.get("production_as_of") or ctx["prod_as_of"]
            ctx["prod_fresh"] = bool(fr.get("production_fresh"))
            ctx["ar_as_of"] = fr.get("ar_as_of") or ctx["ar_as_of"]
            ctx["boxes"] = fr.get("boxes") or []
        except Exception:      # noqa: BLE001  (a store without the reports' catalog: no EMA freshness)
            pass
    if _table(conn, "ema_link_events"):
        r = conn.execute("SELECT MIN(first_seen) FROM ema_link_events").fetchone()
        if r and r[0] and (not ctx["first_prod"] or r[0] < ctx["first_prod"]):
            ctx["first_prod"] = r[0]
    if _table(conn, "ema_ar_items") and "first_seen" in _cols(conn, "ema_ar_items"):
        r = conn.execute("SELECT MIN(first_seen) FROM ema_ar_items").fetchone()
        if r and r[0] and (not ctx["first_ar"] or r[0] < ctx["first_ar"]):
            ctx["first_ar"] = r[0]
    if _table(conn, "transactions"):
        ctx["newest_835"] = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()[0]
        names: dict = {}
        for payer, newest in conn.execute("""SELECT payer_name, MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0
                                             GROUP BY payer_name"""):
            g = _grp_kind(payer)[0]
            if newest and (g not in names or newest > names[g]):
                names[g] = newest
        ctx["group_last_835"] = names
    ctx["policy"] = _meta(conn, "ema:writeoff_policy", {}) or {}
    return ctx


def _outcomes(conn: sqlite3.Connection, items: list[dict], today: str) -> dict:
    """line_outcomes for the items that have one (money items and EMA-only denials), with the store's own rules."""
    try:
        from . import ema_checks as ec
        if not _table(conn, "ema_lines") or not _table(conn, "ema_link_map"):
            return {}
        rules = ec.store_rules(conn)
        return ec.line_outcomes(conn, items, code_once=rules.code_once, today=today)
    except sqlite3.Error:
        return {}


def facts_of(conn: sqlite3.Connection, items: list[dict], *, outcomes: dict | None = None, today: str | None = None,
             context: dict | None = None) -> dict[str, dict]:
    """Every fact the states (and the story) rest on, for each item: {'r835', 'ema', 'ar', 'rw', 'exports', 'outcome'},
    gathered in chunks. outcomes: ema_checks.line_outcomes' answer for these items when the caller has it (the detection
    run); otherwise worked out here with the store's own rules."""
    t = _today(today)
    items = [dict(f) for f in items if f.get("finding_id")]
    if outcomes is None:
        outcomes = _outcomes(conn, items, t)
    ctx = context or _context(conn, t)
    r835 = _r835(conn, items)
    ema = _ema(conn, items, outcomes)
    links = {fid: v["links"] for fid, v in ema.items()}
    ar = _ar(conn, items, links)
    rw = _rw(conn, items)
    ex = _export_rows(conn, items)
    return {f["finding_id"]: {"item": f, "outcome": outcomes.get(f["finding_id"]), "r835": r835[f["finding_id"]],
                              "ema": ema[f["finding_id"]], "ar": ar[f["finding_id"]], "rw": rw[f["finding_id"]],
                              "exports": ex[f["finding_id"]], "ctx": ctx} for f in items}


# ----------------------------------------------------------------------------------------------- the work on an item
def _evidence(fx: dict) -> list[dict]:
    """The dated work on one item, oldest first: {'date', 'kind', 'source', 'text', 'dated_by', 'wait_days', 'confirmed'}.
    dated_by: 'event' (RemitWatch's own record), 'first_seen' (the first EMA report that showed it, after the first one),
    'first_report' (it was already in the first report: its real date is unknown), 'note_date' (a date the note states),
    'payer' (the payer's received date)."""
    f = fx["item"]
    ctx = fx["ctx"]
    after = f.get("payment_date") or ""
    out: list[dict] = []
    # RemitWatch: statuses a person set, faxes sent
    for e in fx["rw"]["events"]:
        if e["kind"] == "manual" and e["to"] in ("appealed", "in_progress"):
            kind = "sent" if e["to"] == "appealed" else "started"
            who = e["actor"] or "a person"
            text = f"Marked {'Sent' if kind == 'sent' else 'Started'} by {who} on {day(e['at'])}" + (f": {e['note']}" if e.get("note") else "")
            out.append({"date": str(e["at"])[:10], "kind": kind, "source": "remitwatch", "text": text, "dated_by": "event",
                        "confirmed": kind == "sent"})
        elif e["kind"] == "packet":
            out.append({"date": str(e["at"])[:10], "kind": "packet", "source": "remitwatch", "dated_by": "event", "confirmed": False,
                        "text": f"Appeal packet built on {day(e['at'])}" + (f" by {e['actor']}" if e.get("actor") else "")})
    if f.get("status") == "appealed" and f.get("appealed_at") and not any(x["kind"] == "sent" for x in out):
        out.append({"date": str(f["appealed_at"])[:10], "kind": "sent", "source": "remitwatch", "dated_by": "event", "confirmed": True,
                    "text": f"Marked Sent on {day(f['appealed_at'])}"})
    for x in fx["rw"]["faxes"]:
        if (x.get("direction") or "out") != "out" or x.get("status") == "failed":
            continue
        when = str(x.get("sent_at") or x.get("created_at") or "")[:10]
        if not when:
            continue
        purpose = (x.get("purpose") or "appeal").replace("_", " ")
        out.append({"date": when, "kind": "fax", "source": "remitwatch", "dated_by": "event", "confirmed": x.get("status") == "sent",
                    "text": f"Faxed to {x.get('payer_group') or 'the payer'} ({purpose}, level {x.get('level') or 1}) on {day(when)}"
                            + (f", fax line ending {x['number_tail']}" if x.get("number_tail") else "") + f": {x.get('status') or 'sent'}"})
    # EMA: claim actions and statuses that show work, dated by the first Production report that showed them
    first_prod = ctx.get("first_prod")
    for e in fx["ema"]["events"]:
        k = action_kind(e["value"]) if e["kind"] in ("action", "status") else None
        if e["kind"] == "status" and k not in ("reprocess", "records"):
            k = None
        if not k:
            continue
        seen = e.get("first_seen")
        first = bool(first_prod) and bool(seen) and seen <= first_prod
        out.append({"date": seen, "kind": k, "source": "production", "dated_by": "first_report" if first else "first_seen",
                    "confirmed": False, "text": f"EMA claim {'action' if e['kind'] == 'action' else 'status'} '{e['value']}'"
                    + (f", already set on the first Production report ({day(seen)}): its own date is unknown" if first
                       else f", first seen on the Production report of {day(seen)}")})
    # the A/R: claim actions and billing notes about an appeal, a call or a resubmission
    first_ar = ctx.get("first_ar")
    seen_notes = set()
    for a in fx["ar"]:
        hist_first = a.get("hist_first_seen") or a.get("as_of")
        k = action_kind(a.get("action"))
        if k:
            first = bool(first_ar) and bool(hist_first) and hist_first <= first_ar
            out.append({"date": hist_first, "kind": k, "source": "ar", "dated_by": "first_report" if first else "first_seen",
                        "confirmed": False, "text": f"A/R claim action '{a['action']}'" + (
                            f", already on the first A/R ({day(hist_first)})" if first else f", first on the A/R of {day(hist_first)}")})
        if a.get("has_note"):
            kinds = [x for x in str(a.get("note_kind") or "").split(",") if x]
            work = [x for x in kinds if x in _NOTE_WORK]
            key = (a.get("item_key"), tuple(kinds))
            if key in seen_notes:
                continue
            seen_notes.add(key)
            changed = a.get("hist_note_changed")
            when = a.get("note_date") or changed or hist_first
            by = "note_date" if a.get("note_date") else ("first_seen" if changed else
                                                        ("first_report" if first_ar and hist_first and hist_first <= first_ar else "first_seen"))
            if work:
                words = " and ".join(WORK_WORDS.get(x, x) for x in work)
                out.append({"date": when, "kind": work[0], "kinds": work, "source": "ar", "dated_by": by, "confirmed": False,
                            "wait_days": a.get("note_wait"),
                            "text": f"A/R billing note about {words}" + (
                                f", dated {day(when)} in the note" if by == "note_date" else
                                f", already on the first A/R ({day(when)}): its own date is unknown" if by == "first_report" else
                                f", first on the A/R of {day(when)}")
                            + (f"; it says to allow {a['note_wait']} days" if a.get("note_wait") else "")})
            elif "no_appeal" in kinds:
                out.append({"date": when, "kind": "no_appeal", "source": "ar", "dated_by": by, "confirmed": False,
                            "text": "A/R billing note records a decision not to appeal" + (f" (on the A/R of {day(when)})" if when else "")})
        if a.get("follow_up"):
            out.append({"date": a.get("as_of"), "kind": "follow_up", "source": "ar", "dated_by": "first_seen", "confirmed": False,
                        "follow_up": a["follow_up"], "text": f"EMA follow-up date set for {day(a['follow_up'])} (A/R of {day(a.get('as_of'))})"})
    # the payer received a corrected or new claim for the same services after this item's remittance
    for x in fx["r835"]["rows"]:
        if not x.get("received") or not after or x["status"] == "22":
            continue
        # the payer received the claim again after this item's remittance (a payer reprocessing on its own keeps the first
        # received date): a corrected or new claim somebody sent
        if (x["date"] or "") > after and x["received"] > after:
            who = f.get("payer_group") if _same_payer(row_group(x)[0], f.get("payer_group")) else row_group(x)[0]
            out.append({"date": x["received"], "kind": "resubmitted", "source": "835", "dated_by": "payer", "confirmed": True,
                        "text": f"{who or 'The payer'} received a {'corrected' if x.get('freq') == '7' else 'new'} claim on "
                                f"{day(x['received'])} (its answer of {day(x['date'])})"})
    for x in fx["exports"]:
        if x.get("submitted") and after and x["submitted"] > after and submission_no(x["claim_id"]) > submission_no(f.get("patient_control_number")):
            out.append({"date": x["submitted"], "kind": "resubmitted", "source": "claim_status", "dated_by": "event", "confirmed": True,
                        "text": f"EMA's claim-status export shows the claim sent again on {day(x['submitted'])}"})
    uniq, seen = [], set()
    for e in sorted(out, key=lambda e: (e.get("date") or "", e["source"], e["kind"])):
        k = (e.get("date"), e["source"], e["kind"], e["text"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def work_evidence(conn: sqlite3.Connection, item: dict, *, outcome: dict | None = None, today: str | None = None) -> list[dict]:
    """The dated work on one item, oldest first: RemitWatch's statuses, packets and faxes; EMA's claim actions and the
    statuses that show work ('Insurance Reprocessing'), dated by the first Production report that showed them; the
    A/R's claim actions and billing notes about an appeal, a call or a resubmission, dated by the note's own date, else
    by the first A/R pull that showed them (the very first pull dates every note already there, and says so); a corrected
    or new claim the payer received (the 835's received date), and a later submission in EMA's claim-status export."""
    fx = facts_of(conn, [item], outcomes={item.get("finding_id"): outcome} if outcome else None, today=today)
    return _evidence(fx[item["finding_id"]]) if item.get("finding_id") in fx else []


# ------------------------------------------------------------------------------------------------- reading the facts
def standing_rows(rows: list[dict]) -> list[dict]:
    """The 835 adjudications still standing (neither a reversal nor reversed)."""
    return [x for x in rows if x["status"] != "22" and not x.get("reversed_by")]


def own_rows(fx: dict) -> list[dict]:
    """The item's own 835 lines: its line, else its claim's lines of its services."""
    f = fx["item"]
    rows = fx["r835"]["rows"]
    if f.get("line_key"):
        mine = [x for x in rows if x.get("line_key") == f["line_key"]]
        if mine:
            return mine
    if f.get("claim_key"):
        return [x for x in rows if x["claim_key"] == f["claim_key"]]
    return []


def code_once(fx: dict) -> bool:
    """The item's own claim carries its code once on its date, so a figure for that date and code (another submission's
    payment, EMA's line) is this line's own; a second unit or lesion billed on its own line makes it not."""
    own = own_rows(fx)
    if not own:
        return True
    ck, svc = own[0]["claim_key"], own[0]["svc"]
    return len([x for x in fx["r835"]["rows"] if x["claim_key"] == ck and x.get("line_key") and x["svc"] == svc]) <= 1


def _payer_answers_after(fx: dict, since: str | None) -> list[dict]:
    """The payer's answers on the item's services after a date: 835 adjudications (a reversal included) and EMA's
    insurance postings ($0 answers included) and the payer's reasons."""
    if not since:
        return []
    out = []
    for x in fx["r835"]["rows"]:
        if (x["date"] or "") > since:
            out.append({"date": x["date"], "source": "835", "paid": x["paid"] or 0, "status": x["status"], "payer": x["payer"],
                        "group": row_group(x)[0], "kind": row_group(x)[1],
                        "codes": [f"{g}-{c}" for g, c, _a in x["adj"] if g != "PR" or c not in PATIENT_SHARE]})
    remit_days = set()
    days_835 = sorted({x["date"] for x in fx["r835"]["rows"] if x["date"]})
    tks = {x.get("tk") for x in fx["r835"]["rows"] if x.get("tk")}
    for p in fx["ema"]["pays"]:
        if p["posted"] and (p["cents"] or p["check"]):
            remit_days.add(p["posted"])
        if (p["posted"] or "") > since and (p["cents"] or p["check"]):
            # EMA's posting of a remittance whose 835 is on file repeats that 835's answer (posted days or weeks later)
            if p.get("in_835") and (p.get("trace_key") in tks or any(0 <= (_days(d, p["posted"]) or 99) <= 14 for d in days_835)):
                continue
            out.append({"date": p["posted"], "source": "production", "paid": p["cents"], "status": None, "payer": p["payer"],
                        "group": p.get("group") or _grp_kind(p["payer"])[0], "codes": []})
    for z in fx["ema"]["reasons"]:
        if z["grp"] in ("", "PR") or z.get("cls") in ("routine", "none", "patient", "ema_kind"):
            continue
        # a reason EMA posted on a day a remittance was posted is the payer's; one posted on another day is a person's
        # adjustment in EMA (a write-off), not an answer
        days = sorted(d for d in {z["first"], z["last"]} if d and d in remit_days and d > since)
        if days:
            out.append({"date": days[-1], "source": "production", "paid": 0, "status": None, "payer": z["payer"],
                        "group": _grp_kind(z["payer"])[0] if z["payer"] else None, "codes": [f"{z['grp']}-{z['code']}"]})
    return sorted(out, key=lambda a: (a["date"] or "", a.get("status") != "22", a["paid"]))


def _repeat_answer(fx: dict, before: str | None) -> dict | None:
    """The newest $0 answer of the item's services that repeats an earlier answer of the same claim (the same payer claim
    number, or the same claim id and received date) with the same reasons, dated on or before `before`: the payer
    re-adjudicated the claim (an appeal or a reprocessing request answered and upheld). As _payer_answers_after's rows,
    marked 'inferred'."""
    rows = [x for x in standing_rows(fx["r835"]["rows"]) if x.get("line_key") and (x["paid"] or 0) <= 0]
    best = None
    for r in rows:
        if before and (r["date"] or "") > before:
            continue
        rc = {(g, c) for g, c, _a in r["adj"] if g != "PR"}
        if not rc:
            continue
        for e in rows:
            if e is r or (e["date"] or "") >= (r["date"] or ""):
                continue
            same_claim = (bool(e.get("pccn")) and e.get("pccn") == r.get("pccn")) or (
                e["pcn"] == r["pcn"] and bool(e.get("received")) and e.get("received") == r.get("received"))
            if same_claim and {(g, c) for g, c, _a in e["adj"] if g != "PR"} == rc:
                if best is None or (r["date"] or "") > (best["date"] or ""):
                    best = r
                break
    if best is None:
        return None
    return {"date": best["date"], "source": "835", "paid": 0, "status": best["status"], "payer": best["payer"],
            "group": row_group(best)[0], "kind": row_group(best)[1], "inferred": True,
            "codes": [f"{g}-{c}" for g, c, _a in best["adj"] if g != "PR" or c not in PATIENT_SHARE]}


def _last_payer_activity(fx: dict, before: str | None = None) -> str | None:
    """The newest day the payer did anything on the item's services: an 835 answer, an insurance posting in EMA, or a
    reason EMA posted on a day a remittance was posted (a reason posted on another day is a person's write-off)."""
    f = fx["item"]
    remit_days = {p["posted"] for p in fx["ema"]["pays"] if p["posted"] and (p["cents"] or p["check"])}
    dates = [f.get("payment_date")]
    dates += [x["date"] for x in fx["r835"]["rows"]]
    dates += list(remit_days)
    dates += [d for z in fx["ema"]["reasons"] if z["grp"] not in ("", "PR") for d in (z["first"], z["last"]) if d in remit_days]
    dates = [d for d in dates if d and (before is None or d <= before)]
    return max(dates) if dates else None


def _deadline(f: dict) -> tuple[str | None, str | None]:
    """The item's last date to act and its basis: the deadline it carries, else the payer's appeal window from its
    remittance (an item with no deadline of its own, such as a non-covered denial), else the first-filing limit."""
    if f.get("appeal_deadline"):
        return f["appeal_deadline"], f.get("deadline_basis") or "the item's deadline"
    from .codes import appeal_rule, filing_rule
    grp, kind = f.get("payer_group"), f.get("payer_kind")
    if f.get("payment_date") and f.get("signal") not in CLAIM_SIGNALS:
        r = appeal_rule(grp, kind)
        return _add(f["payment_date"], r["days"]), r["label"] + ("" if r["verified"] else " (not confirmed against the payer's manual)")
    if f.get("service_date"):
        r = filing_rule(grp, kind)
        return _add(f["service_date"], r["days"]), r["label"]
    return None, None


def _ema_line_totals(fx: dict) -> dict:
    t = {"charges": 0, "ins_paid": 0, "contractual": 0, "noncontractual": 0, "pat_paid": 0, "pat_adj": 0, "balance": 0, "n": 0,
         "several": False, "facility": False}
    for ln in fx["ema"]["lines"]:
        t["n"] += 1
        t["charges"] += ln.get("charges") or 0
        t["ins_paid"] += ln.get("ins_paid") or 0
        t["contractual"] += ln.get("contractual") or 0
        t["noncontractual"] += ln.get("noncontractual") or 0
        t["pat_paid"] += ln.get("pat_paid_joined") or 0
        t["pat_adj"] += ln.get("pat_adj_joined") or 0
        t["balance"] += ln.get("balance") or 0
        t["several"] = t["several"] or ln.get("pat_join") == "several"
        t["facility"] = t["facility"] or bool(ln.get("facility"))
    return t


def _threshold(f: dict) -> int:
    from . import ema_checks as ec
    return ec._threshold(f)


def paid_proof(fx: dict) -> dict | None:
    """Proof the item's money arrived or was settled, or None: {'rule', 'cents', 'date', 'payer', 'text', 'source',
    'watch'}. In order: a later EMA payment no 835 carries (same payer, or another payer when EMA shows the line
    settled: rebilled and paid); a later 835 payment on the same service still standing; the payer reprocessed it to the
    patient's share; the line was paid before this denial (another plan's answer, a duplicate, or the denial of a
    replacement claim while the original payment stands); the allowed amounts on the date and code reach the usual rate
    (a line the payer split across claims); a claim no payer seemed to answer that EMA shows paid."""
    f = fx["item"]
    o = fx.get("outcome") or {}
    sig, cat = f.get("signal"), f.get("category")
    mine = f.get("payment_date") or ""
    thr = _threshold(f) if cat in MONEY or sig == "EMA_DENIED" else 1
    tot = _ema_line_totals(fx)
    oc = o.get("outcome")
    fp = o.get("first_paid_after") or {}
    if oc == "paid_after":
        return {"rule": "paid_later_ema", "cents": o.get("paid_after_same") or 0, "date": fp.get("date"), "payer": fp.get("payer"),
                "check_tail": fp.get("check_tail"), "source": "production",
                "text": f"Paid later: {usd(o.get('paid_after_same'))} by {fp.get('payer') or 'the payer'} on a check with no 835 "
                        f"({day(fp.get('date'))}, check ending {fp.get('check_tail') or '?'})"}
    if oc == "paid_after_other" and tot["n"] and tot["balance"] <= 0:
        return {"rule": "rebilled_paid", "cents": o.get("paid_after_other") or 0, "date": fp.get("date"), "payer": fp.get("payer"),
                "check_tail": fp.get("check_tail"), "source": "production",
                "text": f"Rebilled to {fp.get('payer') or 'another payer'}, paid: {usd(o.get('paid_after_other'))} on {day(fp.get('date'))} "
                        f"(check ending {fp.get('check_tail') or '?'}, no 835); EMA shows the line settled"}
    rows = fx["r835"]["rows"]
    stand = standing_rows(rows)
    own = own_rows(fx)
    once = code_once(fx)
    lcns = {x.get("lcn") for x in own if x.get("lcn")}
    if not lcns and own:
        # a re-adjudication often drops the line control number: the same claim's earlier answer of the code carries it
        o0 = own[0]
        lcns = {x["lcn"] for x in rows if x.get("lcn") and x["code"] == o0["code"] and x["status"] != "22" and (
            (bool(o0.get("pccn")) and x.get("pccn") == o0.get("pccn")) or
            (x["pcn"] == o0["pcn"] and bool(o0.get("received")) and x.get("received") == o0.get("received")))}
    units = max([abs(x.get("ounits") or x.get("units") or 1) for x in own] or [1])

    def same_line(x: dict) -> bool:
        """This 835 line is the item's own line answered again: its line control number, or the code billed once on the
        item's claim and date and answered on another submission of the bill covering the units."""
        covers = abs(x.get("units") or 1) >= units
        if lcns and x.get("lcn") in lcns and x["pcn"] == (own[0]["pcn"] if own else x["pcn"]):
            return covers
        return once and x["claim_key"] != f.get("claim_key") and covers
    # the detection run found fewer units paid than billed (its hint 'partly paid'): the 835 has decided it is short
    short_units = "partly_paid" in _ev(f)
    later_paid = [] if short_units else [x for x in stand if (x["date"] or "") > mine and (x["paid"] or 0) > 0 and same_line(x)]
    denier = f.get("payer_group") or _grp_kind(f.get("payer_name"))[0]
    if later_paid and cat in MONEY:
        paid = sum(x["paid"] or 0 for x in later_paid)
        # the payer that denied the claim sent to it as primary later paid the same line as the second payer, after the
        # primary's answer (OA-23): it only owed its second-payer part, whatever the amount (C64)
        second = [x for x in later_paid if x["status"] in SECOND_STATUS and any(g == "OA" and c == "23" for g, c, _a in x["adj"])
                  and _same_payer(row_group(x)[0], denier)]
        if cat not in SHORT and second and own and not any(x["status"] in SECOND_STATUS for x in own):
            x = second[0]
            return {"rule": "paid_as_secondary", "cents": sum(y["paid"] or 0 for y in second), "date": x["date"], "payer": x["payer"],
                    "check_tail": x["check_tail"], "source": "835",
                    "text": f"Paid as the second payer: {usd(sum(y['paid'] or 0 for y in second))} by {x['payer']} on {day(x['date'])}, after the "
                            f"primary payer's answer (OA-23); this denial was of the claim sent to {denier} as if it were primary"}
        if cat in SHORT:
            # an underpayment or downcode is settled by a later payment of the shortfall (95%)
            need = int(0.95 * (f.get("shortfall_cents") or 0))
            if paid >= max(1, need):
                x = later_paid[0]
                return {"rule": "paid_later_835", "cents": paid, "date": x["date"], "payer": x["payer"], "check_tail": x["check_tail"],
                        "source": "835", "text": f"Paid again on a later remittance: {usd(paid)} by {x['payer']} on {day(x['date'])} "
                                                 f"(check ending {x['check_tail']})"}
        elif paid >= thr:
            x = later_paid[0]
            return {"rule": "paid_later_835", "cents": paid, "date": x["date"], "payer": x["payer"], "check_tail": x["check_tail"],
                    "source": "835", "text": f"Paid on a later remittance: {usd(paid)} by {x['payer']} on {day(x['date'])} "
                                             f"(check ending {x['check_tail']})"}
    # reprocessed to the patient: a reversal of the denial and a new answer with the patient's share and no payment
    if cat in ("denial", "bundling"):
        reproc = [x for x in stand if (x["date"] or "") > mine and (x["paid"] or 0) == 0 and x["status"] in ("1", "19")
                  and any(g == "PR" and c in PATIENT_SHARE and a > 0 for g, c, a in x["adj"])
                  and any(r["status"] == "22" and (r["date"] or "") > mine for r in rows)]
        if reproc:
            x = reproc[-1]
            pr = sum(a for g, c, a in x["adj"] if g == "PR" and c in PATIENT_SHARE)
            what = _pr_words(x["adj"])
            if not tot["n"] or tot["balance"] <= 0 or tot["several"]:
                return {"rule": "moved_to_patient", "cents": 0, "date": x["date"], "payer": x["payer"], "check_tail": x["check_tail"],
                        "source": "835", "patient_cents": pr,
                        "text": f"{row_group(x)[0]} reversed the denial and reprocessed it on {day(x['date'])}: {usd(pr)} to the "
                                f"patient's {what}, nothing owed by the payer" + ("; EMA shows the patient paid" if tot["n"] and tot["balance"] <= 0
                                                                                  and tot["pat_paid"] else "")}
            return {"rule": "moved_to_patient_open", "cents": 0, "date": x["date"], "payer": x["payer"], "check_tail": x["check_tail"],
                    "source": "835", "patient_cents": pr, "patient_open": tot["balance"],
                    "text": f"{row_group(x)[0]} reprocessed it on {day(x['date'])}: {usd(pr)} to the patient's {what}; "
                            f"EMA shows {usd(tot['balance'])} still open"}
    if oc == "patient" and tot["n"] and tot["balance"] <= 0:
        return {"rule": "moved_to_patient", "cents": 0, "date": o.get("outcome_on"), "payer": None, "source": "production",
                "text": f"Settled by the patient: EMA shows {usd(o.get('patient_after'))} paid by the patient after the denial and the line closed"}
    # forwarded to another carrier (CO-B11), and EMA shows the line paid and settled: the carrier paid (C08); its payment
    # can come before the remittance that says the claim was forwarded
    fwd = _forwarded(fx)
    if fwd and cat in MONEY and tot["n"] and tot["balance"] <= 0 and tot["ins_paid"] > 0:
        pays = [p for p in fx["ema"]["pays"] if (p["cents"] or 0) > 0]
        p = pays[-1] if pays else {}
        return {"rule": "forwarded_paid", "cents": tot["ins_paid"], "date": p.get("posted"), "payer": p.get("payer"),
                "check_tail": p.get("check_tail"), "source": "production",
                "text": f"Forwarded to {fwd['carrier']} and paid: EMA shows {usd(tot['ins_paid'])} from insurance on this line"
                        + (f" (posted {day(p.get('posted'))}, check ending {p.get('check_tail') or '?'}"
                           + ("" if p.get("in_835") else ", no 835") + ")" if p else "") + ", and the line settled"}
    # paid before this denial, by another plan or the same one (a duplicate, or the denial of a replacement claim)
    if cat in ("denial", "bundling") and not (f.get("paid_cents") or 0):
        before = [x for x in stand if (x["date"] or "") < mine and (x["paid"] or 0) > 0 and (once or (lcns and x.get("lcn") in lcns))]
        carc = str(f.get("carc") or "")
        # a duplicate denial restating a payment EMA posted before it: the payer split the paid line into its payment and
        # its adjustments, each marked previously paid (B13 with PR-3, CO-45 and CO-253 lines adding up to the charge), C07
        if carc in DUP_CARCS and tot["n"] and tot["balance"] <= 0 and own:
            prior = [p for p in fx["ema"]["pays"] if (p["posted"] or "") <= mine and (p["cents"] or 0) > 0
                     and (not p.get("group") or _same_payer(p.get("group"), denier))]
            own_charge = sum(abs(x["charge"] or 0) for x in own if x.get("line_key"))
            pieces = [x for x in rows if x["claim_key"] == own[0]["claim_key"] and x.get("line_key") and x["svc"] == own[0]["svc"]
                      and x["status"] != "22"]
            hit = next((p for p in prior if p["cents"] == own_charge), None)
            if prior and (hit or (len(pieces) > 1 and sum(abs(x["charge"] or 0) for x in pieces) == tot["charges"])):
                p = hit or prior[-1]
                return {"rule": "paid_before_duplicate", "cents": p["cents"], "date": p["posted"], "payer": p["payer"],
                        "check_tail": p["check_tail"], "source": "production", "watch": False,
                        "text": f"Already paid: EMA shows {usd(p['cents'])} by {p['payer'] or 'the payer'} posted {day(p['posted'])} (check ending "
                                f"{p['check_tail'] or '?'}" + ("" if p["in_835"] else ", no 835") + "), before this duplicate denial, and the line "
                                "settled; the payer's later answer restates that payment"}
        replacement = any(x.get("freq") == "7" for x in own)
        as_primary = any(x["status"] in ("1", "4", "19") for x in own) or not own
        if before:
            x = max(before, key=lambda r: r["date"] or "")
            same = _same_payer(row_group(x)[0], denier)
            settled = not tot["n"] or tot["balance"] <= 0
            # another plan's answer owes nothing only when it processed the claim as primary after another plan paid as
            # primary (two plans each taking itself for primary: the right one paid); a second payer's denial of the
            # coinsurance after the primary paid can still be owed
            if not same and settled and as_primary and x["status"] in ("1", "19"):
                return {"rule": "paid_before_other_payer", "cents": x["paid"], "date": x["date"], "payer": x["payer"],
                        "check_tail": x["check_tail"], "source": "835",
                        "text": f"Already paid: {usd(x['paid'])} by {x['payer']} on {day(x['date'])}, before this denial; the "
                                f"{denier} denial is another plan's answer and owes nothing"}
            if same and (carc in DUP_CARCS or replacement) and settled:
                return {"rule": "replacement_denied_paid" if replacement else "paid_before_duplicate", "cents": x["paid"],
                        "date": x["date"], "payer": x["payer"], "check_tail": x["check_tail"], "source": "835", "watch": True,
                        "text": (f"Paid {usd(x['paid'])} on {day(x['date'])} on the original claim; the payer denied the replacement "
                                 "claim and has not taken the payment back: watch for a takeback" if replacement else
                                 f"Already paid {usd(x['paid'])} on {day(x['date'])} (check ending {x['check_tail']}); this denial is "
                                 "of a duplicate")}
            # the same payer paid this service before, the payment still stands (no reversal, no takeback naming the
            # claim) and brought at least what this line should: the later denial answers another copy of the claim (a
            # superseded original after the corrected claim was paid, C62), whatever its reason
            takeback = any(p["reason"] in ("WO", "72") and (p["date"] or "") >= (x["date"] or "") for p in fx["r835"].get("plb") or [])
            # the same line: its line control number (EMA keeps it across submissions), or neither line has one and the
            # code is on the claim once; a second unit or lesion billed on its own line is never closed by the first one's
            # payment
            twin = [y for y in before if _same_payer(row_group(y)[0], denier)
                    and ((lcns and y.get("lcn") in lcns) or (not lcns and not y.get("lcn") and once))]
            x = max(twin, key=lambda r: r["date"] or "") if twin else x
            if twin and settled and (x["paid"] or 0) >= thr and not takeback:
                return {"rule": "paid_before_same_payer", "cents": x["paid"], "date": x["date"], "payer": x["payer"],
                        "check_tail": x["check_tail"], "source": "835", "watch": True,
                        "text": f"Already paid: {usd(x['paid'])} by {x['payer']} on {day(x['date'])} (check ending {x['check_tail']}), and the "
                                "payment still stands; this later denial answers another copy of the claim, so nothing more is owed: "
                                "watch for a takeback"}
        pb = o.get("paid_before_first") or {}
        if (o.get("paid_before") or 0) > 0 and tot["n"] and tot["balance"] <= 0 and pb:
            same = _same_payer(_grp_kind(pb.get("payer"))[0], denier) if pb.get("payer") else True
            if not same or carc in DUP_CARCS or replacement:
                return {"rule": "paid_before_other_payer" if not same else "paid_before_duplicate", "cents": o["paid_before"],
                        "date": pb.get("date"), "payer": pb.get("payer"), "check_tail": pb.get("check_tail"), "source": "production",
                        "watch": same,
                        "text": f"Already paid: EMA shows {usd(o['paid_before'])} by {pb.get('payer') or 'insurance'} on {day(pb.get('date'))}, "
                                "before this denial, and the line settled" + ("" if not same else "; this denial is of a duplicate")}
    # an underpayment whose allowed amounts on the date and code add up to the usual rate (a line split across claims)
    if cat in SHORT and (f.get("expected_cents") or 0) > 0:
        # the payer split the line across claims of one remittance (MA15): the pieces share the line control number
        pieces = [x for x in stand if x.get("line_key") and lcns and x.get("lcn") in lcns and (x["date"] or "") == mine]
        allowed = sum(x["allowed"] or 0 for x in pieces)
        if len({x["claim_key"] for x in pieces}) > 1 and allowed >= int(0.99 * f["expected_cents"]):
            return {"rule": "paid_in_full_split", "cents": allowed, "date": mine, "payer": f.get("payer_name"),
                    "source": "835", "text": f"Paid in full: the payer split {f.get('procedure_code')} across {len({x['claim_key'] for x in pieces})} "
                                             f"claims on the remittance of {day(mine)}, and their allowed amounts add up to {usd(allowed)}, "
                                             "the usual rate"}
        if once and tot["n"] and not tot["several"] and tot["balance"] <= 0 and tot["ins_paid"] + tot["pat_paid"] >= int(0.99 * f["expected_cents"]):
            return {"rule": "paid_in_full_ema", "cents": tot["ins_paid"], "date": None, "payer": None, "source": "production",
                    "text": f"Paid in full: EMA shows {usd(tot['ins_paid'])} from insurance and {usd(tot['pat_paid'])} from the patient on this "
                            f"line, at least the usual allowed amount of {usd(f['expected_cents'])}"}
    # a claim RemitWatch saw no answer to, that EMA shows paid
    if sig in CLAIM_SIGNALS and tot["n"]:
        sent = _ev(f).get("sent") or f.get("service_date") or ""
        paid = [p for p in fx["ema"]["pays"] if (p["cents"] or 0) > 0 and (p["posted"] or "") >= sent]
        cents = sum(p["cents"] for p in paid)
        if cents > 0 and tot["balance"] <= 0:
            p0 = paid[0]
            return {"rule": "claim_paid_in_ema", "cents": cents, "date": p0["posted"], "payer": p0["payer"], "check_tail": p0["check_tail"],
                    "source": "production", "text": f"Paid: EMA shows {usd(cents)} paid by {p0['payer'] or 'the payer'} from {day(p0['posted'])} "
                                                    f"(check ending {p0['check_tail']}{', no 835' if not p0['in_835'] else ''}), and the claim settled"}
    return None


def _pr_words(adj: list) -> str:
    kinds = {c for g, c, a in adj if g == "PR" and a > 0}
    words = [w for c, w in (("1", "deductible"), ("2", "coinsurance"), ("3", "copay")) if c in kinds]
    return " and ".join(words) or "share"


def credit_of(fx: dict) -> int:
    """A credit EMA shows on the item's line that insurance money made (over $1, and not patient money that cannot be
    split between two claims): the amount, as a positive number of cents, else 0."""
    tot = _ema_line_totals(fx)
    if tot["n"] and not tot["several"] and tot["balance"] < -100 and tot["ins_paid"] > 0 and -tot["balance"] <= tot["ins_paid"]:
        return -tot["balance"]
    return 0


def _forwarded(fx: dict) -> dict | None:
    """The item's own answer forwarded the claim to another carrier (CO-B11, or claim status 19 naming a crossover
    carrier on a $0 answer): {'carrier', 'date'}."""
    f = fx["item"]
    mine = f.get("payment_date") or ""
    if str(f.get("carc") or "") == "B11":
        own = [x for x in fx["r835"]["rows"] if (x["date"] or "") == mine]
        carrier = next((x.get("crossover") for x in own if x.get("crossover")), None)
        return {"carrier": carrier or "the proper payer", "date": mine}
    return None


def _decide(fx: dict, today: str) -> dict:
    f = fx["item"]
    ctx = fx["ctx"]
    sig, cat, st = f.get("signal") or "", f.get("category") or "", f.get("status") or "open"
    grp = f.get("payer_group")
    ev = _evidence(fx)
    res: dict = {"work_state": None, "follow_up_on": None, "state_basis": None, "evidence": [], "rule": None, "person": False,
                 "proof": None, "deadline": None, "deadline_basis": None, "work": None, "answered": None, "appeal_block": None,
                 "credit_cents": 0, "closed_on": None, "closed_basis": None}
    deadline, dbasis = _deadline(f)
    res["deadline"], res["deadline_basis"] = deadline, dbasis

    def put(state: str, rule: str, basis: str, rows: list | None = None, follow: str | None = None) -> dict:
        res.update(work_state=state, rule=rule, state_basis=basis[:300], follow_up_on=follow)
        res["evidence"] = list(rows or [])
        return res
    proof = paid_proof(fx) if (cat in MONEY or sig in EMA_ONLY) else None
    credit = credit_of(fx) if cat in MONEY else 0
    res["proof"], res["credit_cents"] = proof, credit
    reproc = any(x["status"] == "22" and (x["date"] or "") > (f.get("payment_date") or "") for x in fx["r835"]["rows"])
    # ------------------------------------------------------------------ a person's status wins
    events = fx["rw"]["events"]
    manual = [e for e in events if e["kind"] == "manual"]
    last_manual = manual[-1] if manual else None
    who = (last_manual or {}).get("actor") or "a person"
    when = (last_manual or {}).get("at") or f.get("status_changed_at")
    conflict = f" {proof['text']}: mark it Paid." if proof and proof.get("rule") not in ("moved_to_patient_open",) else ""
    if f.get("stale") and st not in ("resolved", "dismissed", "lost"):
        return put("done", "stale", "No longer listed: the rules that raised it no longer find it on the newest data; its notes are kept")
    if st == "resolved":
        res["person"] = not f.get("auto_note")
        if f.get("auto_note"):
            return put("done", "auto_closed", str(f["auto_note"]), [{"source": "remitwatch", "kind": "auto_note", "text": f["auto_note"]}])
        rec = f" (recovered {usd(f['recovered_cents'])})" if f.get("recovered_cents") else ""
        return put("done", "person_paid", f"Marked Paid by {who} on {day(when)}{rec}",
                   [{"source": "remitwatch", "kind": "status", "text": f"Marked Paid by {who} on {day(when)}{rec}"}])
    if st in ("dismissed", "lost"):
        res["person"] = True
        hidden = next((e for e in reversed(events) if e["kind"] in ("hidden", "unhidden", "manual")), None)
        if hidden and hidden["kind"] == "hidden":
            return put("accepted", "hidden_rule", f"Hidden by the rule for {grp or 'this payer'}: {hidden.get('note') or 'not expected to pay these'}",
                       [{"source": "remitwatch", "kind": "hidden", "text": hidden.get("note") or "hidden by a rule"}])
        word = "Marked Lost" if st == "lost" else "Let go"
        note = f": {last_manual['note']}" if last_manual and last_manual.get("note") else (f": {f['notes']}" if f.get("notes") else "")
        return put("accepted", "person_lost" if st == "lost" else "person_let_go", f"{word} by {who} on {day(when)}{note}",
                   [{"source": "remitwatch", "kind": "status", "text": f"{word} by {who} on {day(when)}{note}"}])
    if st == "in_progress":
        res["person"] = True
        base = f"Started by {who} on {day(when)}"
        return put("work_now", "person_started", base + (f".{conflict}" if conflict else ""),
                   [e for e in ev if e["kind"] == "started"][-1:])
    if st == "appealed":
        res["person"] = True
        sent = [e for e in ev if e["kind"] in ("sent", "fax") and e["source"] == "remitwatch"]
        anchor = max((e["date"] for e in sent if e.get("date")), default=None) or str(f.get("appealed_at") or f.get("status_changed_at") or "")[:10]
        work = "sent_claim" if (f.get("actionable") == "correct_and_resubmit") else "sent"
        return _follow(res, fx, ev, anchor, work, f"Marked Sent by {who} on {day(anchor)}", today, deadline, confirmed=True,
                       conflict=conflict, person=True, put=put)
    # ------------------------------------------------------------------ automatic states
    if sig in INFO_ONLY:
        return put("accepted", "info_only", "For information: nothing to recover")
    if sig in CONTRACT:
        return put("work_now", "owner_decision", "A contract or billing-policy question for the owner, not a claim to appeal")
    if sig in FEES:
        if deadline and today > (_add(deadline, EXPIRE_GRACE_DAYS) or "9999"):
            res["closed_on"], res["closed_basis"] = deadline, dbasis
            return put("expired", "expired", f"The payer's window to dispute it closed on {day(deadline)}, more than {EXPIRE_GRACE_DAYS} days ago")
        return put("work_now", "fees", "A takeback to match to the payer's overpayment letter, and to dispute if it was not owed"
                   if sig == "TAKEBACK" else "A payment fee the payer's vendor took: ask for standard EFT with no fee")
    if sig == "REMIT_NOT_POSTED":
        return put("work_now", "post_payment", "The 835 paid this and EMA has not posted it: post it in EMA")
    if proof and not credit and proof["rule"] != "moved_to_patient_open":
        res["appeal_block"] = "paid"
        rows = [{"source": proof["source"], "kind": "proof", "date": proof.get("date"), "text": proof["text"]}]
        return put("done", proof["rule"], proof["text"], rows)
    if credit:
        res["appeal_block"] = "credit"
        return put("work_now", "credit_refund", f"EMA shows a credit of {usd(credit)} on this line: insurance paid more than the line "
                                                "was owed; decide refund or recoupment (owner)")
    o = fx.get("outcome") or {}
    if o.get("outcome") == "written_off_by_policy":
        pol = (ctx.get("policy") or {}).get(o.get("pattern") or "") or {}
        return put("accepted", "policy", f"The owner's write-off policy accepts {o.get('pattern') or 'this pattern'}"
                   + (f" (set by {pol.get('by')} on {day(pol.get('at'))})" if pol.get("by") else ""))
    if reproc:
        res["appeal_block"] = "reprocessed"
    if proof and proof["rule"] == "moved_to_patient_open":
        res["appeal_block"] = "reprocessed"
        return put("work_now", "bill_patient", proof["text"] + ": bill the patient", [{"source": "835", "kind": "proof", "text": proof["text"]}])
    fwd = _forwarded(fx)
    if fwd and cat in MONEY:
        follow = _add(fwd["date"], FORWARD_WAIT_DAYS)
        basis = f"Forwarded to {fwd['carrier']} on {day(fwd['date'])}: expect its payment"
        if follow and follow >= today:
            res["appeal_block"] = "waiting"
            return put("waiting", "forwarded", basis + f"; check on {day(follow)} if none comes", follow=follow)
        return put("work_now", "forwarded_late", basis + f"; nothing has come by {day(follow)}: ask {fwd['carrier']} for it", follow=follow)
    # the work seen on the item (RemitWatch's own marks only when no person set the item back to To do since)
    reopened = max((str(e["at"])[:10] for e in manual if e["to"] == "open"), default="")
    work_ev = [e for e in ev if e["kind"] in ("sent", "fax", "appeal", "corrected", "records", "reprocess", "secondary", "call",
                                               "resubmitted") and (e.get("date") or "") >= (f.get("payment_date") or "")
               or (e["kind"] in ("appeal", "corrected", "records", "reprocess", "secondary", "call", "resubmitted")
                   and e.get("dated_by") == "first_report")]
    work_ev = [e for e in work_ev if not (e["source"] == "remitwatch" and reopened and (e.get("date") or "") <= reopened)]
    if work_ev and (cat in MONEY or sig in EMA_ONLY):
        # work with a known date first: an undated note or action (dated only by the first report that showed it) most
        # often records the same work a payer's received date or a person's mark dates exactly
        dated = [e for e in work_ev if e.get("dated_by") != "first_report"]
        last = max(dated or work_ev, key=lambda e: (e.get("date") or "", e["source"]))
        work = {"appeal": "appeal", "records": "records", "reprocess": "reprocess", "corrected": "corrected", "resubmitted": "resubmitted",
                "secondary": "secondary", "call": "call", "sent": "sent", "fax": "fax"}.get(last["kind"], "appeal")
        # work whose own date nobody knows (already in the first report) cannot hold an item past every window: nothing
        # shows it was done in time, or that anything is still pending (C59)
        if all(e.get("dated_by") == "first_report" for e in work_ev) and deadline \
                and today > (_add(deadline, EXPIRE_GRACE_DAYS) or "9999"):
            res["closed_on"], res["closed_basis"] = deadline, dbasis
            return put("expired", "expired", f"Every window closed (the last on {day(deadline)}) more than {EXPIRE_GRACE_DAYS} days ago. "
                                             f"{last['text']}; nothing shows that work was done in time or is still pending", [last])
        return _follow(res, fx, ev, last.get("date"), work, last["text"], today, deadline, confirmed=bool(last.get("confirmed")),
                       conflict="", person=False, put=put, ev_row=last)
    # past every window
    if deadline and (cat in MONEY or sig in EMA_ONLY) and today > (_add(deadline, EXPIRE_GRACE_DAYS) or "9999"):
        res["closed_on"], res["closed_basis"] = deadline, dbasis
        return put("expired", "expired", f"Every window closed (the last on {day(deadline)}) more than {EXPIRE_GRACE_DAYS} days ago, "
                                         "with no work pending")
    # no source can show the answer
    cc = _cant_check(fx, today)
    if cc:
        return put("cant_check", cc[0], cc[1])
    return put("work_now", "open", _open_basis(fx))


def _follow(res: dict, fx: dict, ev: list, anchor: str | None, work: str, text: str, today: str, deadline: str | None, *,
            confirmed: bool, conflict: str, person: bool, put, ev_row: dict | None = None) -> dict:
    """Waiting on work seen on `anchor`: until the payer's answer time has passed (the note's own "allow N days" when
    it says), then work now; at once when the payer answered since (paid: done; anything else: the answer to work); and
    when the work's own date is unknown (it was already in the first report) and nothing came from the payer for longer
    than its answer time before that report, the follow-up is due now. Work nobody can confirm falls due at least two
    weeks before the deadline."""
    f = fx["item"]
    grp, kind = f.get("payer_group"), f.get("payer_kind")
    conn_free_wait = _wait_of(fx, work, ev_row)
    rows = [ev_row] if ev_row else [e for e in ev if e.get("source") == "remitwatch" and e["kind"] in ("sent", "fax")][-1:]
    res["work"] = ev_row or {"date": anchor, "kind": work, "text": text}
    res["appeal_block"] = "waiting"
    answers = _payer_answers_after(fx, anchor) if anchor else []
    first = bool(ev_row) and ev_row.get("dated_by") == "first_report"
    if not answers and first and not person and work in ("appeal", "reprocess", "records", "corrected", "resubmitted", "call"):
        # work whose date is unknown, and the payer answered the same claim again with the same reason before the report
        # that first showed the work: most likely its answer to that work (an appeal upheld), C26
        rep = _repeat_answer(fx, anchor)
        if rep:
            answers = [rep]
    if answers:
        a = answers[-1]
        res["answered"] = a
        res["appeal_block"] = None
        what = (f"paid {usd(a['paid'])}" if a["paid"] > 0 else
                ("reversed its answer" if a.get("status") == "22" else
                 ("answered " + (", ".join(a["codes"]) if a["codes"] else "with $0.00"))))
        lead = (f"{text}. The payer answered the same claim again on {day(a['date'])} with the same reason ({', '.join(a['codes'])}): most "
                "likely its answer to that work, upheld" if a.get("inferred") else f"{text}; the payer answered on {day(a['date'])} ({what})")
        # the newest answer to the work is a denial whose own window closed more than 30 days ago: expired, when the
        # item's own window has closed too or the answer came from another payer (the claim was rebilled to the right
        # one: the first denial no longer matters), C60. A person's Sent status keeps it for that person to read.
        if not person and a["paid"] <= 0 and a.get("status") != "22":
            ag = a.get("group") or (_grp_kind(a["payer"])[0] if a.get("payer") else None) or grp
            ak = a.get("kind") or (_grp_kind(a["payer"])[1] if a.get("payer") else None) or kind
            from .codes import appeal_rule
            ar_ = appeal_rule(ag, ak)
            closes = _add(a["date"], ar_["days"])
            other = bool(ag) and bool(grp) and not _same_payer(ag, grp)
            if closes and today > (_add(closes, EXPIRE_GRACE_DAYS) or "9999") and (
                    other or (deadline and today > (_add(deadline, EXPIRE_GRACE_DAYS) or "9999"))):
                res["appeal_block"] = None
                res["closed_on"], res["closed_basis"] = closes, ar_["label"]
                return put("expired", "expired", f"{lead}. {ag or 'The payer'}'s window to dispute that answer closed on {day(closes)}, more "
                                                 f"than {EXPIRE_GRACE_DAYS} days ago", rows + [{"source": a["source"], "kind": "answer",
                                                                                               "date": a["date"], "text": f"answer of {day(a['date'])}: {what}"}])
        return put("work_now", "work_answered", f"{lead}: work its answer" + (f".{conflict}" if conflict else ""),
                   rows + [{"source": a["source"], "kind": "answer", "date": a["date"], "text": f"answer of {day(a['date'])}: {what}"}])
    follow = _add(anchor, conn_free_wait) if anchor else None
    if not confirmed and deadline:
        cap = _add(deadline, -FOLLOW_UP_MARGIN)
        if follow and cap and cap < follow:
            follow = max(cap, anchor or cap)
    if first and not person:
        last_act = _last_payer_activity(fx, anchor)
        quiet = _days(last_act, anchor) if last_act and anchor else None
        if quiet is not None and quiet > conn_free_wait:
            return put("work_now", "work_stale", f"{text}. Nothing has come from the payer since {day(last_act)}, {quiet} days before that "
                                                 "report: check where it stands before doing more", rows, follow=anchor)
    if follow and follow >= today:
        return put("waiting", "work_waiting", f"{text}; follow up on {day(follow)} if the payer has not answered" + (f".{conflict}" if conflict else ""),
                   rows, follow=follow)
    return put("work_now", "follow_up_due", f"{text}; no answer from the payer by {day(follow)}: follow up" + (f".{conflict}" if conflict else ""),
               rows, follow=follow)


def _wait_of(fx: dict, work: str, ev_row: dict | None) -> int:
    f = fx["item"]
    stated = (ev_row or {}).get("wait_days")
    if stated:
        return int(stated)
    conn = fx.get("_conn")
    if conn is not None:
        return response_days(conn, f.get("payer_group"), work, f.get("payer_kind"))
    t = fx["ctx"].get("response") or {}
    g = (t.get("groups") or {}).get(f.get("payer_group") or "")
    base = int(g["p90"]) if g and (g.get("n") or 0) >= MIN_ANSWERS else (int(t["all"]["p90"]) if t.get("all") else DEFAULT_RESPONSE_DAYS)
    if work in ("corrected", "resubmitted", "sent_claim", "secondary"):
        return base + CLAIM_TRANSIT_DAYS
    if work in ("appeal", "records", "reprocess", "sent", "fax"):
        return max(base, APPEAL_WAIT.get(f.get("payer_kind") or "other", APPEAL_WAIT["other"]))
    if work in ("call", "follow_up"):
        return max(base, CALL_WAIT_DAYS)
    return base


def _cant_check(fx: dict, today: str) -> tuple[str, str] | None:
    """Why no source can show this item's answer, or None. A claim no payer answered when neither the payer's 835s nor
    EMA's postings reach the day its answer was due; a surgery-center (facility) claim with no remittance and no EMA
    posting at all (its money goes to another payee)."""
    f = fx["item"]
    ctx = fx["ctx"]
    sig = f.get("signal")
    ev = _ev(f)
    if sig == "NO_ANSWER":
        due = ev.get("answer_due") or _add(ev.get("sent") or f.get("service_date"), 30)
        lasts = ctx.get("group_last_835") or {}
        grp = f.get("payer_group") or ""
        last835 = lasts.get(grp) or max((d for g, d in lasts.items() if grp and _family(g) == _family(grp)), default=None) \
            or ev.get("payer_last_remittance")
        prod_ok = ctx.get("prod_fresh") and _covered(ctx, f.get("service_date"))
        if due and (not last835 or last835 < due) and not prod_ok:
            why = []
            why.append(f"no 835 from {f.get('payer_group') or 'this payer'} after {day(last835)}" if last835 else
                       f"no 835 from {f.get('payer_group') or 'this payer'} on file")
            why.append("EMA's Production report does not cover this date of service" if ctx.get("prod_as_of") and not _covered(ctx, f.get("service_date"))
                       else (f"EMA's Production report is from {day(ctx.get('prod_as_of'))}" if ctx.get("prod_as_of") else "no EMA Production report on file"))
            return ("cant_check_no_source", "No source can show the payer's answer: " + "; ".join(why) + ". Check the payer's portal.")
    tot = _ema_line_totals(fx)
    if tot["facility"] and not fx["r835"]["rows"] and not fx["ema"]["pays"] and not any(z["grp"] not in ("", "PR") for z in fx["ema"]["reasons"]) \
            and sig in ("NO_ANSWER", "EMA_DENIED"):
        return ("cant_check_facility", "The surgery center's facility claim: its remittances go to another payee, and EMA shows nothing "
                                       "posted. Check the facility's EOB.")
    return None


def _covered(ctx: dict, svc: Any) -> bool:
    for b in ctx.get("boxes") or []:
        if b.get("scope") == "posted":
            continue
        if svc and (b.get("svc_lo") or "") <= str(svc) <= (b.get("svc_hi") or ""):
            return True
    return any(b.get("scope") == "posted" for b in ctx.get("boxes") or [])


def _open_basis(fx: dict) -> str:
    f = fx["item"]
    o = fx.get("outcome") or {}
    deadline, _b = _deadline(f)
    oc = o.get("outcome")
    words = {"auto_written_off": "EMA's posting rule wrote it off the day the remittance posted; nobody worked it",
             "written_off_while_pending": "EMA wrote it off the day it posted, while the payer was still waiting for information",
             "lost_to_timely_filing": "written off for late filing after an earlier denial",
             "written_off_at_posting": "written off in EMA the day the remittance posted",
             "contractual_at_posting": "written off as contractual in EMA the day the remittance posted",
             "written_off_later": "a person wrote it off in EMA later, on a day with no remittance",
             "contractual_later": "written off as contractual in EMA later",
             "paid_after_partial": "EMA shows part of it paid since",
             "paid_after_other": "another payer paid part of it since",
             "paid_before": "EMA shows the line paid before this denial: check whether the denial still matters",
             "open": "EMA shows it open", "action": "EMA shows a claim action on it"}
    head = {"NO_ANSWER": "No answer from the payer on file", "REJECTED_PRE_PAYER": "Never reached the payer",
            "NOT_SENT": "Never left EMA", "EMA_DENIED": "Denied in EMA, no remittance on file"}.get(f.get("signal") or "",
                                                                                                   "Not paid and nobody has worked it")
    tail_ = f"; {words[oc]}" if oc in words else ""
    return head + tail_ + (f"; act by {day(deadline)}" if deadline else "")


# --------------------------------------------------------------------------------------------------------- public API
def states_of(conn: sqlite3.Connection, items: Iterable[dict], *, outcomes: dict | None = None, today: str | None = None,
              facts: dict | None = None) -> dict[str, dict]:
    """The state of many items at once: {finding id: state_of's answer}. items: finding rows (dicts); outcomes:
    ema_checks.line_outcomes' answer when the caller has it (the detection run passes its own). Gathered 500 at a time."""
    t = _today(today)
    items = [dict(f) for f in items if f.get("finding_id")]
    ctx = _context(conn, t)
    ctx["response"] = response_table(conn)
    out: dict[str, dict] = {}
    for part in _chunks(items, 2000):
        fx_all = facts or facts_of(conn, part, outcomes=outcomes if outcomes is not None else None, today=t, context=ctx)
        for f in part:
            fx = fx_all.get(f["finding_id"])
            if fx is None:
                continue
            fx["_conn"] = conn
            try:
                out[f["finding_id"]] = _public(_decide(fx, t))
            finally:
                fx.pop("_conn", None)
    return out


def state_of(conn: sqlite3.Connection, item: dict, *, outcome: dict | None = None, today: str | None = None) -> dict:
    """The state of one item: {'work_state', 'follow_up_on', 'state_basis', 'evidence': [...]} and, for the story,
    'rule' (what decided it), 'label', 'person' (a person's status decided it), 'proof' (for done), 'deadline' and its
    basis, 'work' (the newest work seen), 'answered' (the payer's answer since), 'appeal_block' (why a bulk appeal must
    leave it out: paid, credit, reprocessed or waiting; None when it may go), 'credit_cents'. item: a finding row (dict).
    outcome: its ema_checks.line_outcomes value when the caller has it."""
    fid = item.get("finding_id") or "item"
    item = dict(item, finding_id=fid)
    return states_of(conn, [item], outcomes={fid: outcome} if outcome else None, today=today)[fid]


def _public(res: dict) -> dict:
    res["label"] = LABELS.get(res.get("work_state") or "", "")
    return res


def stored_state(item: dict) -> dict | None:
    """The state the detection run stored on the item (work_state, follow_up_on, state_basis), or None."""
    if item.get("work_state") in STATES:
        return {"work_state": item["work_state"], "follow_up_on": item.get("follow_up_on"), "state_basis": item.get("state_basis"),
                "label": LABELS[item["work_state"]]}
    return None
