"""
The one place an item's state is decided (amendment 13.1). Every item is in exactly one of:

  work_now    a person needs to work it today or this week
  waiting     a person or EMA shows the work was done (an appeal or corrected claim faxed or sent, a resubmission the payer
              received, an EMA claim action, an A/R note about an appeal, a call or a resubmission); it follows up on the
              date the work was seen plus the payer's response time (the 90th percentile from the archive), and goes back to
              work_now on that date when the payer has not answered by then
  done        paid, or settled by the patient or a second payer, with the proof
  accepted    a loss someone accepted: the owner's write-off policy, a hide rule, or a person who let it go or marked it lost
  expired     past every window (the latest of the item's own deadline, the payer's own limit, its dispute window, the
              corrected-claim clock where a corrected claim can fix it, a Medicare reopening where a clerical fix can, the
              first-filing limit of a claim), with no work pending
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

from .runtime import practice_date

STATES = ("work_now", "waiting", "done", "accepted", "expired", "cant_check")
LABELS = {"work_now": "Work now", "waiting": "Waiting", "done": "Done", "accepted": "Accepted", "expired": "Expired",
          "cant_check": "Can't be checked"}

EXPIRE_GRACE_DAYS = 0           # past every window: expired that day (orchestrator decision 1: no grace in the work queue)
ARCHIVE_GRACE_DAYS = 30         # what unit E's archive waits after the last window before retiring an expired item
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
CONTRACT = ("CONTRACT_RATE_GAP", "MA_BELOW_MEDICARE", "MPPR_POLICY", "CHARGE_BELOW_RATE", "SECOND_RATE", "PROVIDER_RATE_GAP",
            "EMA_DENIAL_AS_CONTRACTUAL", "G2211_DENIED")
# one request to the payer for many claims it priced alike, money it owes (not a contract question): its own work, a
# repricing request, kept out of the bulk appeal like the contract items
BATCH = ("OLD_FEE_SCHEDULE",)
FEES = ("FEE_SKIM", "TAKEBACK")
OWN_RULES = ("SECONDARY_NOT_BILLED", "CREDIT_BALANCE")    # items whose money no denial rule judges: they have their own
DUP_CARCS = frozenset({"18", "B13"})
# reasons that say the claim is another payer's to pay (coordination of benefits, not its member, coverage dates, sent on to
# another carrier; detect.OTHER_PAYER_CARCS): the bill sent to another payer after one is the denial's work
OTHER_PAYER_CARCS = frozenset({"22", "109", "B11", "31", "26", "27", "200", "177", "B9"})
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
# the work, as a noun that reads in "follow up on <it>", "ask whether it received <it>" and "a billing note about <it>"
WORK_WORDS = {"appeal": "the appeal", "corrected": "the corrected claim", "records": "the records sent", "reprocess": "the reprocessing request",
              "secondary": "the claim billed to the next payer", "call": "the call or portal check", "sent": "the appeal or claim marked Sent",
              "fax": "the faxed appeal", "resubmitted": "the resubmitted claim", "started": "the work started", "billed": "the claim billed to the next payer",
              "follow_up": "the follow-up date set in EMA", "note": "the billing note in EMA", "packet": "the appeal packet",
              "no_appeal": "a decision not to appeal", "write_off": "a write-off"}
# the verb that dates a piece of work ("the call or portal check, made Apr 2"; "the appeal, sent Apr 1")
WORK_WHEN = {"call": "made", "follow_up": "set", "note": "written", "started": "started", "no_appeal": "written", "write_off": "written"}
# work that holds an item (it waits for the payer's answer to it, or a person must read it first)
WORK_KINDS = ("sent", "fax", "appeal", "corrected", "records", "reprocess", "secondary", "call", "resubmitted", "billed", "note", "follow_up")
# denials whose route can be a corrected claim (an authorization number, a modifier, a code): its clock counts too
CORRECTABLE_SIGNALS = ("DENIED_AUTH", "DENIED_MISSING_MODIFIER", "DENIED_CODING", "EM_BUNDLED_NO_MODIFIER", "EM_BUNDLED_MOD57_MINOR",
                       "DENIED_MISSING_INFO", "DENIED_REFERRAL")
AUTH_CARCS = frozenset({"197", "198", "243", "288", "15", "62", "39"})
# remarks naming a code, a modifier or a diagnosis that is missing or wrong: a Medicare reopening can fix those (a clerical
# error); a wrong diagnosis behind a medical-necessity denial is one too (C24), and so is a drug's code (C59, M51)
CLERICAL_RARCS = frozenset({"M51", "M76", "M81", "MA63", "M20", "M67", "M77"})
REOPENING_DAYS = 365
# what EMA's current claim action says is pending at the payer (an item past every window still waits on it)
PENDING_ACTIONS = ("appeal", "corrected", "records", "reprocess")
# The owner's decision 2 (2026-11-26): archive rule 8's patient-group denials that are not the patient's deductible,
# coinsurance or copay (archive_rules.PR_GROUP_CODES, group PR) are billed to the patient. A person records "Billed the
# patient" (a status Sent whose note starts with BILLED_PATIENT: the item waits for the patient's payment in EMA) or "Done"
# (a status Paid with $0 whose note starts with DONE_NOTE: done by that person). PATIENT_WAIT_DAYS: two monthly statements
# before an unpaid patient balance comes back to Work now (every waiting item has a follow-up day, selfcheck followup_date)
PATIENT_DENIAL_SIGNALS = ("DENIED_REFERRAL", "DENIED_COVERAGE_DATES", "DENIED_PLAN_EXCLUSION")
PATIENT_DENIAL_CODES = frozenset({"288", "27", "26", "200", "49", "204"})
BILLED_PATIENT = "Billed the patient"
DONE_NOTE = "Done"
PATIENT_WAIT_DAYS = 60
# the rules of a patient-group denial whose work is on the patient's side (billing the patient, waiting on the patient's
# payment, checking the patient's account, settled by the patient, or a person's Done): sized at the patient's share. Any
# other work on it (a corrected claim or a resubmission sent to the payer, its follow-up) is with the payer and is worth
# what the payer pays for the service (review r1-4)
PATIENT_SIDE_RULES = frozenset({"bill_patient_denial", "patient_unpaid", "billed_patient", "cant_check_patient", "patient_paid",
                                "moved_to_patient", "person_done"})
# the rules a person's own decision-2 action leads to (Billed the patient, then the patient's payment or its absence; Done):
# when that action is newer than the run that stored the item's state, the state it gives is the item's (story._state_of)
PATIENT_PERSON_RULES = frozenset({"billed_patient", "patient_unpaid", "patient_paid", "person_done"})


# ------------------------------------------------------------------------------------------------------------ helpers
def _today(today: Any) -> str:
    """The day an item's state is decided on: the caller's (the run's day), else the practice's (runtime.practice_today),
    never the machine's (UTC on both hosts, a day ahead of a US practice each evening)."""
    if today:
        return str(today)[:10]
    from .runtime import practice_today
    return practice_today()


def _pday(v: Any) -> str:
    """YYYY-MM-DD of a stored day as written, or of a stored moment (an ISO time with its zone, as every record is
    written) on the practice's calendar (runtime.practice_date): a person's mark or a fax at 9:30 PM Eastern is that
    day's, never UTC's next day. '' when there is none. (A plain day, by far the most read, skips the parsing.)"""
    if not v:
        return ""
    s = str(v)
    return s if len(s) == 10 else practice_date(s)


def _moment(v: Any) -> datetime | None:
    """A stored moment (an ISO time with its zone, as every record is written) as a time that compares across zones;
    None for a plain day, a time with no zone or anything that is not a time."""
    s = str(v or "").strip()
    if len(s) <= 10:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def fax_day(sent_at: Any, created_at: Any) -> str:
    """The practice's day a fax went: its sent time as the fax line gave it, never before the day it was queued (a fax
    cannot go before it was queued: a service's placeholder time or a time in another zone's words gives way to the
    queueing day). '' when neither is a day."""
    sent, made = _pday(sent_at), _pday(created_at)
    sent = sent if _d(sent) else ""
    made = made if _d(made) else ""
    return max(sent, made) if sent and made else (sent or made)


def _newest_work(rows: list[dict]) -> dict | None:
    """The newest of a person's work rows: by the practice's day, then by the moment it was recorded, so of two marks on
    one day the later is the newest (review r2-3: Billed the patient after a Sent that morning is what the item waits on,
    and a corrected claim sent after a Billed the patient that day waits on the payer). A row with no moment ranks
    before one with a moment on its day."""
    def key(e: dict) -> tuple:
        m = _moment(e.get("at"))
        return (e.get("date") or "", m.timestamp() if m else float("-inf"))
    return max(rows, key=key, default=None)


def _came_after(read: Any, day: Any, moment: datetime | None, reopen_day: str) -> bool:
    """Whether a payment reached RemitWatch after a person's change at `moment`: by when its remittance was read, when
    the store knows it (an 835 often comes days before the payment date it carries, so a payment dated after the change
    may have been on file when the person decided, and rejected by them); else by its date against the practice's day of
    the change."""
    got = _moment(read)
    if got is not None and moment is not None:
        return got > moment
    return str(day or "") > reopen_day


def _d(iso: Any) -> date | None:
    """A stored day, or the practice's date of a stored moment (_pday): every day the states count with, compare or say
    is the practice's, as today is."""
    try:
        return date.fromisoformat(_pday(iso))
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
    ref = _d(as_of) or _d(_today(None))         # the practice's day, where the note was written
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
    # when RemitWatch read the remittance (its file's ingested_at): an 835 often comes days before the payment date it
    # carries (the EFT's), so only this says whether a payment was on file when a person decided (_decide, a reopen). The
    # dashboard's copy keeps no file of a payment: there it is unknown
    has_read = "file_sha256" in tcols and _table(conn, "files") and "ingested_at" in _cols(conn, "files")
    read = "fl.ingested_at" if has_read else "NULL"
    read_join = "LEFT JOIN files fl ON fl.sha256 = t.file_sha256" if has_read else ""
    bills = sorted(want)
    rows_by_bill: dict[str, list] = {}
    for part in _chunks(bills):
        _temp(conn, "st_b", ["bill TEXT PRIMARY KEY", "hi TEXT"], [(b, b + "~") for b in part])
        cur = conn.execute(f"""SELECT b.bill, c.claim_key, c.patient_control_number, c.claim_status_code, c.payer_name,
                    t.payment_date, {trace}, {method}, {recv}, {freq}, {pccn}, {xover}, c.charge_cents, c.paid_cents, {cpat},
                    l.line_key, COALESCE(l.service_date, c.statement_from), l.procedure_code, l.original_procedure_code, {mods},
                    l.charge_cents, l.paid_cents, {allowed}, {units}, {ounits}, {lcn}, c.transaction_key, c.claim_filing_indicator,
                    {read}
                FROM temp.st_b b JOIN claims c ON c.patient_control_number >= b.bill AND c.patient_control_number < b.hi
                JOIN transactions t ON t.transaction_key = c.transaction_key
                {read_join}
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
    # what the detection run's ledger found the payer's recoupments (PLB WO, FB, J1 naming a claim, on any later check)
    # took from each payment, and whether that takeback is on an item of its own (detect._save_taken): paid_proof never
    # counts what an unlisted takeback took as paid
    taken_l: dict[str, tuple] = {}
    if lkeys and _table(conn, "ledger_taken"):
        listed = "listed" if "listed" in _cols(conn, "ledger_taken") else "0"
        for part in _chunks(lkeys):
            taken_l.update((r[0], (r[1] or 0, int(r[2] or 0))) for r in conn.execute(
                f"SELECT line_key, taken_cents, {listed} FROM ledger_taken WHERE line_key IN ({','.join('?' * len(part))})", part))
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
        # a payer's own claim number that names one claim of a bill: a recoupment written 'WO:<that number>' names that claim
        # (the ledger reads it so: detect._plb_names; fx-ledger-r2-9)
        by_number: dict[str, set] = {}
        for rs in rows_by_bill.values():
            for r in rs:
                if r[10] and r[2]:
                    by_number.setdefault(str(r[10]).strip().upper(), set()).add(r[2])
        if pcns:
            _temp(conn, "st_pc", ["pcn TEXT PRIMARY KEY"], [(p,) for p in pcns])
            upper = sorted(((x.upper(), x) for x in pcns if x), key=lambda t: -len(t[0]))
            for ref, rc, amt, paid_on, payer, tr in conn.execute(
                    """SELECT p.reference, p.reason_code, p.amount_cents, p.payment_date, p.payer_name, p.trace_number FROM plb p
                       WHERE p.reference IS NOT NULL AND p.reason_code IN ('WO','FB','72','J1','L6','CS')"""):
                text = str(ref or "").upper()
                # the claim id in any letter case (the longest that fits: C1 is no match for C17), else the payer's number
                hit = next((x for u, x in upper if u in text), None) if text else None
                if not hit and text:
                    words = {w for w in re.split(r"[^0-9A-Z]+", text) if len(w) >= 6}
                    found = {x for w in words for x in by_number.get(w, ()) if len(by_number[w]) == 1}
                    hit = next(iter(found)) if len(found) == 1 else None
                if hit:
                    plb_by_bill.setdefault(bill_of(hit), []).append({"reason": rc, "cents": amt or 0, "date": paid_on, "payer": payer,
                                                                      "check_tail": tail(tr), "claim_id": hit})
            _drop(conn, "st_pc")
    for b, fs in want.items():
        recs = []
        for r in rows_by_bill.get(b, []):
            # 'trace': the check's whole number, kept only to match EMA's postings of the same check (never shown)
            recs.append({"claim_key": r[1], "pcn": r[2], "status": str(r[3] or ""), "payer": r[4], "date": r[5], "check_tail": tail(r[6]),
                         "trace": r[6],
                         "method": r[7], "received": r[8], "freq": r[9], "pccn": r[10], "crossover": r[11], "claim_charge": r[12],
                         "claim_paid": r[13], "claim_pr": r[14], "line_key": r[15], "svc": r[16], "code": r[17], "ocode": r[18],
                         "modifiers": r[19], "charge": r[20], "paid": r[21], "allowed": r[22], "units": r[23], "ounits": r[24],
                         "lcn": r[25], "tk": r[26], "cfi": r[27], "read": r[28],
                         "taken": (taken_l.get(r[15]) or (0, False))[0] if r[15] else 0,
                         "taken_listed": (taken_l.get(r[15]) or (0, 0))[1] if r[15] else 0,
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


def _same_payer_name(a: dict, b: dict) -> bool:
    """The same payer by its name alone (a company that reprocessed a claim under another of its products, a Medicare
    Advantage plan say, reverses the first answer under the new product's filing indicator)."""
    from .codes import normalize_payer
    if not a.get("payer") or not b.get("payer"):
        return False
    na, nb = normalize_payer(a["payer"])[0], normalize_payer(b["payer"])[0]
    return na == nb and na != "Unknown"


def _pair_reversals(recs: list[dict]) -> None:
    """Mark each status-22 line with the line it reverses, and that line as reversed. A reversal repeats the received
    date of the claim it reverses (or a later one, the date the payer received the request to reprocess), so a line the
    payer received after the reversal's received date is never its pair (the new answer that often comes on the same
    remittance); among the rest (same payer, code and charge, answered on or before the reversal): the same received
    date first, then the line whose reasons the reversal negates exactly, then the opposite payment, then an earlier
    remittance, then the newest. The same payer by name counts too when the filing indicator moved the claim to another of
    its products (a reversal then carries the new product's indicator), the same group preferred after the reasons and the
    amount, as Results pairs them (results._pair_group). A reversal of a claim the archive does not hold stays unpaired."""
    for x in recs:
        x.setdefault("reversed_by", None)
        x.setdefault("reverses", None)
    for x in sorted((r for r in recs if r["status"] == "22"), key=lambda r: r["date"] or ""):
        rx = x.get("received")
        gx = row_group(x)[0]
        cands = [o for o in recs if o is not x and o["status"] != "22" and not o.get("reversed_by")
                 and (o["date"] or "") <= (x["date"] or "") and (_same_payer(row_group(o)[0], gx) or _same_payer_name(o, x))
                 and o["code"] == x["code"] and abs(o["charge"] or 0) == abs(x["charge"] or 0)
                 and (o["pccn"] == x["pccn"] or not o["pccn"] or not x["pccn"] or o["pcn"] == x["pcn"])
                 and not (rx and o.get("received") and o["received"] > rx)]
        if not cands:
            continue
        mirror = sorted((g, c, -a) for g, c, a in x["adj"])

        def score(o: dict) -> tuple:
            return (bool(rx) and o.get("received") == rx, bool(mirror) and sorted(o["adj"]) == mirror,
                    (o["paid"] or 0) == -(x["paid"] or 0), _same_payer(row_group(o)[0], gx), (o["date"] or "") < (x["date"] or ""),
                    o["date"] or "", o["line_key"] or "")
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


def _s837(conn: sqlite3.Connection, items: list[dict]) -> dict[str, list]:
    """The 837 sends of each remittance item's bill (every submission, every payer: claims_837, one row per claim in a
    file, BHT04 the day it went out): {finding id: [{'claim_id', 'date', 'freq', 'payer_id', 'payer', 'services': {(date,
    code)}}]}. Only items from a remittance (a denial the bill can be sent again after) are looked up."""
    out: dict[str, list] = {f["finding_id"]: [] for f in items}
    if not _table(conn, "claims_837"):
        return out
    want: dict[str, list] = {}
    for f in items:
        if f.get("payment_date") and f.get("signal") not in CLAIM_SIGNALS:
            b = bill_of(f.get("patient_control_number"))
            if b:
                want.setdefault(b, []).append(f["finding_id"])
    if not want:
        return out
    has_lines = _table(conn, "lines_837")
    for part in _chunks(sorted(want)):
        _temp(conn, "st_8b", ["bill TEXT PRIMARY KEY", "hi TEXT"], [(b, b + "~") for b in part])
        sends: dict[str, dict] = {}
        for b, key, cid, bht, freq, pid, pname in conn.execute(
                """SELECT b.bill, c.claim_837_key, c.patient_control_number, c.bht_created, TRIM(COALESCE(c.frequency_code, '')),
                          c.payer_id, c.payer_name
                   FROM temp.st_8b b JOIN claims_837 c ON c.patient_control_number >= b.bill AND c.patient_control_number < b.hi"""):
            if bill_of(cid) != b or not bht:
                continue
            sends[key] = {"bill": b, "claim_id": cid, "date": str(bht)[:10], "freq": freq, "payer_id": pid, "payer": pname,
                          "services": set()}
        if has_lines and sends:
            for key, dos, code in conn.execute(
                    """SELECT l.claim_837_key, l.service_date, l.procedure_code FROM temp.st_8b b
                       JOIN lines_837 l ON l.patient_control_number >= b.bill AND l.patient_control_number < b.hi"""):
                s = sends.get(key)
                if s is not None and code:
                    s["services"].add((str(dos or "")[:10], code))
        for s in sends.values():
            for fid in want.get(s["bill"], []):
                out[fid].append(s)
    _drop(conn, "st_8b")
    return out


def _837_payer(s: dict) -> tuple[str, str]:
    """An 837 send's payer group and the company it stands for: the VA's network by its payer id (named after its
    administrator), else the payer name's group."""
    from .detect import VA_GROUP, VA_PAYER_IDS, payer_company
    if str(s.get("payer_id") or "").strip().upper() in VA_PAYER_IDS:
        return VA_GROUP[0], "va"
    g = _grp_kind(s.get("payer"))[0] if s.get("payer") else ""
    return g, payer_company(g)


def _resends_837(fx: dict) -> list[dict]:
    """The bill sent again electronically after this item's remittance (the 837s; the catch-up trace's T-I4: 45 items on
    Work now were resends EMA's exports do not show), as work on the item: to the denying payer (the payer id of the
    send it answered, or the company the payer's name stands for), or to any payer after a reason that says the claim is
    another payer's (the calibration's fix 10). A send that did not carry the item's service, or a void, is not."""
    f = fx["item"]
    after = f.get("payment_date") or ""
    sends = fx.get("s837") or []
    if not after or not sends:
        return []
    from .detect import payer_company
    keys = set(_service_keys(f))
    mine = payer_company(f.get("payer_group"))
    answered = {s["payer_id"] for s in sends if s["claim_id"] == f.get("patient_control_number") and s["date"] <= after and s.get("payer_id")}
    carcs = {c for c in re.split(r"[^A-Z0-9]+", str(f.get("carc") or "").upper()) if c}
    elsewhere = bool(carcs & OTHER_PAYER_CARCS)
    out = []
    for s in sends:
        if s["date"] <= after or s["freq"] == "8":
            continue
        if keys and s["services"] and not (keys & s["services"]):
            continue
        grp, company = _837_payer(s)
        same = (s.get("payer_id") in answered) or (bool(company) and company == mine)
        if not same and not elsewhere:
            continue
        who = f.get("payer_group") if same else (grp or "another payer")
        row = {"date": s["date"], "kind": "resubmitted", "source": "837", "dated_by": "event", "confirmed": True,
               "payer_group": f.get("payer_group") if same else grp,
               "text": f"Sent again electronically to {who or 'the payer'} on {day(s['date'])} (837 claim file"
                       + (", a replacement claim" if s["freq"] == "7" else "") + ")"}
        # it waits the payer's usual answer time after a send, as the 837s measure it (95th percentile), where known
        wait = _answer_wait(fx.get("ctx") or {}, row["payer_group"])
        if wait:
            row["wait_days"] = wait
        out.append(row)
    return out


def _answer_wait(ctx: dict, group: Any) -> int | None:
    """The payer group's answer time after a send, from the 837s (detect.ANSWER_DAYS_META): its own 95th percentile where
    20 answers are measured, else a group of the same family's, else all payers'; None when nothing is measured."""
    t = ctx.get("answer_837") or {}
    groups = t.get("groups") or {}
    g = groups.get(group or "")
    if g is None and group:
        g = next((v for k, v in groups.items() if _same_payer(k, group)), None)
    g = g or t.get("all")
    try:
        return int(g["p95"]) if g and g.get("p95") else None
    except (TypeError, ValueError):
        return None


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
    ctx["ema_names"] = _ema_names(conn)
    # each payer group's answer time after a send, from the 837s (detect.ANSWER_DAYS_META): how long a bill sent again waits
    ctx["answer_837"] = _meta(conn, "detect:answer_days_837", {}) or {}
    return ctx


def _outcomes(conn: sqlite3.Connection, items: list[dict], today: str) -> dict:
    """line_outcomes for the items that have one (money items and EMA-only denials), with the store's own rules."""
    try:
        from . import ema_checks as ec
        if not _table(conn, "ema_lines") or not _table(conn, "ema_link_map"):
            return {}
        return ec.line_outcomes(conn, items, code_once=_code_once_rule(conn), today=today)
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
    s8 = _s837(conn, items)
    return {f["finding_id"]: {"item": f, "outcome": outcomes.get(f["finding_id"]), "r835": r835[f["finding_id"]],
                              "ema": ema[f["finding_id"]], "ar": ar[f["finding_id"]], "rw": rw[f["finding_id"]],
                              "exports": ex[f["finding_id"]], "s837": s8[f["finding_id"]], "ctx": ctx} for f in items}


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
            note = str(e.get("note") or "")
            if kind == "sent" and note.startswith(BILLED_PATIENT):
                # "Billed the patient" (the owner's decision 2): the patient was billed for a patient-group denial
                rest = note[len(BILLED_PATIENT):].lstrip(" :").strip()
                out.append({"date": _pday(e["at"]), "at": e["at"], "kind": "billed", "patient": True, "source": "remitwatch",
                            "dated_by": "event", "confirmed": True,
                            "text": f"Patient billed by {who} on {day(e['at'])}" + (f": {rest}" if rest else "")})
                continue
            if kind == "sent" and note.startswith("Billed:"):
                # the page's Mark billed: the claim went to the next payer (or the patient), not an appeal to this one
                out.append({"date": _pday(e["at"]), "at": e["at"], "kind": "billed", "source": "remitwatch", "dated_by": "event",
                            "confirmed": True,
                            "text": f"Marked billed by {who} on {day(e['at'])}" + (f": {note[len('Billed:'):].strip()}" if note[7:].strip() else "")})
                continue
            text = f"Marked {'Sent' if kind == 'sent' else 'Started'} by {who} on {day(e['at'])}" + (f": {e['note']}" if e.get("note") else "")
            out.append({"date": _pday(e["at"]), "at": e["at"], "kind": kind, "source": "remitwatch", "text": text, "dated_by": "event",
                        "confirmed": kind == "sent"})
        elif e["kind"] == "packet":
            out.append({"date": _pday(e["at"]), "kind": "packet", "source": "remitwatch", "dated_by": "event", "confirmed": False,
                        "text": f"Appeal packet built on {day(e['at'])}" + (f" by {e['actor']}" if e.get("actor") else "")})
    if f.get("status") == "appealed" and f.get("appealed_at") and not any(x["kind"] == "sent" for x in out):
        out.append({"date": _pday(f["appealed_at"]), "kind": "sent", "source": "remitwatch", "dated_by": "event", "confirmed": True,
                    "text": f"Marked Sent on {day(f['appealed_at'])}"})
    for x in fx["rw"]["faxes"]:
        if (x.get("direction") or "out") != "out" or x.get("status") == "failed":
            continue
        when = fax_day(x.get("sent_at"), x.get("created_at"))
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
    seen_notes, seen_follow = set(), set()
    for a in fx["ar"]:
        hist_first = a.get("hist_first_seen") or a.get("as_of")
        k = action_kind(a.get("action"))
        if k:
            first = bool(first_ar) and bool(hist_first) and hist_first <= first_ar
            out.append({"date": hist_first, "kind": k, "source": "ar", "dated_by": "first_report" if first else "first_seen",
                        "confirmed": False, "text": f"A/R claim action '{a['action']}'" + (
                            f", already on the first A/R ({day(hist_first)})" if first else f", first on the A/R of {day(hist_first)}")})
        if a.get("has_note"):
            # note_kind: what the A/R reader read in the note (classify_note), NULL where it read nothing (a reader that
            # does not classify notes yet): such a note is work of a kind nobody knows, and a person reads it first
            raw = a.get("note_kind")
            known = raw is not None
            kinds = [x for x in str(raw or "").split(",") if x]
            work = [x for x in kinds if x in _NOTE_WORK]
            key = (a.get("item_key"), tuple(kinds), known)
            if key not in seen_notes:
                seen_notes.add(key)
                changed = a.get("hist_note_changed")
                when = a.get("note_date") or changed or hist_first
                by = "note_date" if a.get("note_date") else ("first_seen" if changed else
                                                            ("first_report" if first_ar and hist_first and hist_first <= first_ar else "first_seen"))
                dated = (f", dated {day(when)} in the note" if by == "note_date" else
                         f", already on the first A/R ({day(when)}): its own date is unknown" if by == "first_report" else
                         f", first on the A/R of {day(when)}")
                if work:
                    words = " and ".join(WORK_WORDS.get(x, x) for x in work)
                    out.append({"date": when, "kind": work[0], "kinds": work, "source": "ar", "dated_by": by, "confirmed": False,
                                "wait_days": a.get("note_wait"),
                                "text": f"A/R billing note about {words}" + dated
                                + (f"; it says to allow {a['note_wait']} days" if a.get("note_wait") else "")})
                if "no_appeal" in kinds or "write_off" in kinds:
                    what = "no_appeal" if "no_appeal" in kinds else "write_off"
                    out.append({"date": when, "kind": "no_appeal", "decided": what, "source": "ar", "dated_by": by, "confirmed": False,
                                "text": f"A/R billing note records {WORK_WORDS[what]}" + dated})
                if not known:
                    out.append({"date": when, "kind": "note", "source": "ar", "dated_by": by, "confirmed": False,
                                "text": "EMA has a billing note on this line" + dated})
        if a.get("follow_up"):
            # a follow-up date a person set in EMA: someone is working the claim and comes back to it that day
            fkey = (a.get("item_key"), a.get("follow_up"))
            if fkey not in seen_follow:
                seen_follow.add(fkey)
                out.append({"date": a.get("as_of"), "kind": "follow_up", "source": "ar", "dated_by": "first_seen", "confirmed": False,
                            "follow_up": str(a["follow_up"])[:10],
                            "text": f"EMA follow-up date set for {day(a['follow_up'])} (first on the A/R of {day(a.get('as_of'))})"})
    # the payer received a corrected or new claim for the same services after this item's remittance
    for x in fx["r835"]["rows"]:
        if not x.get("received") or not after or x["status"] == "22":
            continue
        # the payer received the claim again after this item's remittance (a payer reprocessing on its own keeps the first
        # received date): a corrected or new claim somebody sent. Another payer counts only when it processed the claim as
        # primary (a rebill to the payer that pays first); a second payer's processing (crossover, OA-23) is not that work
        if (x["date"] or "") > after and x["received"] > after:
            same = _same_payer(row_group(x)[0], f.get("payer_group"))
            if not same and x["status"] not in ("1", "4", "19"):
                continue
            who = f.get("payer_group") if same else row_group(x)[0]
            out.append({"date": x["received"], "kind": "resubmitted", "source": "835", "dated_by": "payer", "confirmed": True,
                        "payer_group": row_group(x)[0],
                        "text": (f"{who or 'The payer'} received a {'corrected' if x.get('freq') == '7' else 'new'} claim on "
                                 f"{day(x['received'])} (its answer of {day(x['date'])})" if same else
                                 f"Rebilled to {who}: it received the claim on {day(x['received'])} (its answer of {day(x['date'])})")})
    for x in fx["exports"]:
        if x.get("submitted") and after and x["submitted"] > after and submission_no(x["claim_id"]) > submission_no(f.get("patient_control_number")):
            out.append({"date": x["submitted"], "kind": "resubmitted", "source": "claim_status", "dated_by": "event", "confirmed": True,
                        "text": f"EMA's claim-status export shows the claim sent again on {day(x['submitted'])}"})
    # the 837 claim files: the bill sent again electronically after this item's remittance (T-I4)
    out += _resends_837(fx)
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
    payment, EMA's line) is this line's own; a second unit or lesion billed on its own line makes it not. A line of the
    code billed at $0 (a placeholder or a piece the payer split off) is not a second service (C02, C27)."""
    own = own_rows(fx)
    if not own:
        return True
    ck, svc = own[0]["claim_key"], own[0]["svc"]
    return len([x for x in fx["r835"]["rows"] if x["claim_key"] == ck and x.get("line_key") and x["svc"] == svc
                and (x.get("charge") or 0) != 0]) <= 1


def _code_once_rule(conn: sqlite3.Connection, fallback: Any = None):
    """ema_checks.line_outcomes' code_once for the outcomes this module works out itself: the claim carries the item's
    code once on its date, lines billed at $0 left out (as code_once above). Unit B's detection run passes its own."""
    cache: dict[str, Any] = {}

    def once(f: dict) -> bool:
        ck = f.get("claim_key")
        if not ck or not _table(conn, "lines"):
            return True
        if ck not in cache:
            if len(cache) > 20_000:
                cache.clear()
            from collections import Counter
            cache[ck] = Counter((r[0], r[1]) for r in conn.execute(
                "SELECT service_date, procedure_code FROM lines WHERE claim_key=? AND COALESCE(charge_cents, 0) <> 0", (ck,)))
        return cache[ck][(f.get("service_date"), f.get("procedure_code"))] <= 1
    return once


def _payer_answers_after(fx: dict, since: str | None, payers: Sequence[str] = (), only: bool = False) -> list[dict]:
    """The payer's answers on the item's services after a date: 835 adjudications (a reversal included) and EMA's
    insurance postings ($0 answers included) and the payer's reasons. Only the item's payer's, and the payers named in
    `payers` (a payer the claim was rebilled to): a second payer's processing is not an answer to the work. only: the
    payers named alone (a claim billed to the second payer waits on that payer, never on the first one)."""
    if not since:
        return []
    f = fx["item"]
    groups = [g for g in ([] if only else [f.get("payer_group")]) + list(payers) if g]
    if only and not groups:
        return []

    def ours(group: Any) -> bool:
        return not groups or not group or any(_same_payer(group, g) for g in groups)
    out = []
    for x in fx["r835"]["rows"]:
        if (x["date"] or "") > since and ours(row_group(x)[0]):
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
            if p.get("role") not in (None, "", "primary") and not ours(p.get("group")):
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
    from .codes import appeal_rule, filing_rule, rule_at, rule_facts, window_end
    grp, kind = f.get("payer_group"), f.get("payer_kind")
    if f.get("payment_date") and f.get("signal") not in CLAIM_SIGNALS:
        r = rule_at(f["payment_date"], appeal_rule(grp, kind, **rule_facts(f)))      # the window before an effective date, if earlier
        return _add(f["payment_date"], r["days"]), r["label"] + ("" if r["verified"] else " (not confirmed against the payer's manual)")
    if f.get("service_date"):
        r = filing_rule(grp, kind, **rule_facts(f))
        return window_end(f["service_date"], r) or _add(f["service_date"], r["days"]), r["label"]
    return None, None


def _later(*dates: Any) -> str | None:
    got = [str(d)[:10] for d in dates if d]
    return max(got) if got else None


def windows(f: dict, deadline: str | None = None, dbasis: str | None = None, fx: dict | None = None) -> list[dict]:
    """Every window the item can still be worked in, by date: {'what', 'date', 'basis', 'process', 'own'}. process: what
    the window is for ('appeal': a dispute or appeal to the payer, 'corrected': a corrected claim, 'reopening': a Medicare
    reopening of a clerical error, 'first_filing': a claim no payer has). own: the item's own deadline, or the payer's own
    limit for the same request (legal_deadline). The others:
      the payer's dispute window from the remittance, with its own limit (codes.appeal_rule, codes.legal_deadline), for
          every denial and short payment, a correct-and-resubmit item too (Humana's 18 months behind its 1-year corrected
          claim, C21 and C25); never for a claim forwarded to another carrier (CO-B11: the first payer denied nothing).
          A payer that counts from the original determination ("original", "initial" in its rule) counts from the first
          denial of the same service with the same reason (fx: the item's facts), never from a later repeat of it
      the corrected-claim clock (codes.corrected_rule) where the route can be a corrected claim: an authorization number,
          a modifier, a code, missing information
      a Medicare reopening, one year from the remittance, where the fix can be clerical: a code or modifier, a diagnosis
          (a medical-necessity denial, C24), a remark naming a missing or invalid code (C59)
      the first-filing limit of a claim no payer answered
    The item expires the day after the latest of them (no grace in the work queue)."""
    from . import codes
    if deadline is None and dbasis is None:
        deadline, dbasis = _deadline(f)
    sig, cat = f.get("signal") or "", f.get("category") or ""
    grp, kind = f.get("payer_group"), f.get("payer_kind")
    carc = str(f.get("carc") or "")
    rarcs = {x.strip() for x in str(f.get("rarc") or "").split(",") if x.strip()}
    process = "first_filing" if sig in CLAIM_SIGNALS else ("corrected" if f.get("actionable") == "correct_and_resubmit" else "appeal")
    out: list[dict] = []
    if deadline:
        out.append({"what": "the item's deadline", "date": str(deadline)[:10], "basis": dbasis, "process": process, "own": True})
    legal = str(f.get("legal_deadline") or "")[:10]
    if legal and (not deadline or legal > str(deadline)[:10]):
        out.append({"what": "the payer's own limit", "date": legal, "basis": "the payer's own limit for the same request",
                    "process": process, "own": True})
    pay = f.get("payment_date")
    money = cat in MONEY or sig == "EMA_DENIED"
    forwarded = carc == "B11" or sig == "FORWARDED_TO_CARRIER"
    facts = codes.rule_facts(f)             # which plan of the group (the plan's or payer's name, the reason, paid or not)
    if pay and money and sig not in CLAIM_SIGNALS and sig not in OWN_RULES and not forwarded:
        r = codes.appeal_rule(grp, kind, **facts)
        # a payer that takes no appeal of an ordinary denial (Kentucky Medicaid, 907 KAR 1:671) has no dispute window for it:
        # its corrected or new claim's limit is the window
        no_appeal = bool(r.get("no_appeal")) and sig != "TAKEBACK"
        start = first_denial(fx, r) if fx is not None else None
        start = start if start and start < str(pay)[:10] else pay
        d = None if no_appeal else _later(codes.window_end(start, r), codes.legal_deadline(start, r))
        if d:
            # the label of the rule the window was counted by (before an effective date, the one before it)
            out.append({"what": "the payer's dispute window", "date": d, "basis": codes.rule_at(start, r)["label"], "process": "appeal",
                        "own": False})
        cr = None
        if kind != "medicare" and (no_appeal or f.get("actionable") == "correct_and_resubmit" or sig in CORRECTABLE_SIGNALS or carc in AUTH_CARCS):
            cr = codes.corrected_rule(grp, kind, f.get("rarc"), sig, carc, **facts)
        elif kind != "medicare" and (cat == "denial" or sig == "EMA_DENIED" or int(f.get("paid_cents") or 0) <= 0):
            # a payer that wants a denial its claim information caused corrected and sent again as a new claim before any review
            # (Indiana Medicaid, IHCP 'For Claim Denials'): that window is open whatever the item's route (deadlines review r2-6).
            # A whole-claim denial is a denial whatever its claim's paid amount reads
            cr = codes.corrected_rule(grp, kind, f.get("rarc"), sig, carc, **dict(facts, paid=False))
            cr = cr if cr and cr.get("resubmit_denials") else None
        if cr:
            d = _later(*codes.corrected_window(cr, f.get("service_date"), pay))
            if d:
                out.append({"what": "the corrected-claim limit", "date": d, "basis": cr["label"], "process": "corrected", "own": False})
        if kind == "medicare" and (sig in codes.MEDICARE_CLERICAL_SIGNALS or sig == "DENIED_MEDICAL_NECESSITY"
                                   or carc in codes.MEDICARE_CLERICAL_CARCS or rarcs & CLERICAL_RARCS):
            out.append({"what": "a Medicare reopening", "date": _add(pay, REOPENING_DAYS), "process": "reopening", "own": False,
                        "basis": "Medicare reopening of a clerical error, within one year of the remittance (42 CFR 405.980)"})
    if sig in CLAIM_SIGNALS and f.get("service_date"):
        r = codes.filing_rule(grp, kind, **facts)
        d = _later(codes.window_end(f["service_date"], r), codes.legal_deadline(f["service_date"], r)) or _add(f["service_date"], r["days"])
        out.append({"what": "the first-filing limit", "date": d, "basis": r["label"], "process": "first_filing", "own": False})
    seen, uniq = set(), []
    for w in sorted((w for w in out if w.get("date")), key=lambda w: (w["date"], not w["own"])):
        if (w["what"], w["date"]) not in seen:
            seen.add((w["what"], w["date"]))
            uniq.append(w)
    return uniq


def first_denial(fx: dict | None, rule: dict) -> str | None:
    """The first $0 answer of the item's service by the same payer with the same reason, when the payer's window runs from
    the original determination (its rule says "original" or "initial"); else None."""
    if not fx or not re.search(r"\b(original|initial)\b", str(rule.get("label") or ""), re.I):
        return None
    f = fx["item"]
    carc = str(f.get("carc") or "")
    pay = str(f.get("payment_date") or "")[:10]
    days = sorted(x["date"] for x in standing_rows(fx["r835"]["rows"]) if x.get("line_key") and (x["paid"] or 0) <= 0 and x["date"]
                  and x["date"] < pay and _same_payer(row_group(x)[0], f.get("payer_group"))
                  and (not carc or any(c == carc for _g, c, _a in x["adj"])))
    return days[0] if days else None


def last_window(f: dict, deadline: str | None = None, dbasis: str | None = None, wins: list | None = None) -> dict | None:
    """The window that closes last (windows()), or None when the item has no date at all. The item's own deadline wins a
    tie, so the basis named is the one the item was dated by."""
    wins = windows(f, deadline, dbasis) if wins is None else wins
    if not wins:
        return None
    top = max(w["date"] for w in wins)
    return next((w for w in wins if w["date"] == top and w["own"]), None) or next(w for w in wins if w["date"] == top)


def _last_window(f: dict, deadline: str | None) -> str | None:
    """The last day any window is open (last_window's date)."""
    w = last_window(f, deadline, f.get("deadline_basis"))
    return w["date"] if w else deadline


def _archive_hint(f: dict) -> dict | None:
    """What the 835 archive's rules (remitwatch.archive_rules, before save) said of the item: evidence['archive_rule']
    with a state_hint ('done', 'work_now', 'waiting') and a strength ('proven', 'likely'), or None."""
    a = _ev(f).get("archive_rule")
    if not isinstance(a, dict) or a.get("state_hint") not in ("done", "work_now", "waiting"):
        return None
    if not a.get("hint") and f.get("hint"):
        # the rule's sentence (archive_rules.apply_actions keeps it on the item's hint; an item saved before it was kept
        # with the label too): a state it decides shows the sentence, not the label alone
        a = dict(a, hint=f["hint"])
    return a


# what the archive rules prove settles a denial (archive_rules' reclass): the same payer paid the line before this answer
# of a copy (rule 1), the primary paid it and nothing is left of the share (rule 2), the practice voided the claim (rule 5)
SETTLED_BY_ARCHIVE = ("paid_before", "other_payer_paid", "voided")


def _worked_here(fx: dict) -> bool:
    """A person worked the item in RemitWatch beyond its status: a note, an appeal date, a fax sent for it, a packet."""
    f = fx["item"]
    if f.get("notes") or f.get("appealed_at"):
        return True
    if any((x.get("direction") or "out") == "out" for x in fx["rw"]["faxes"]):
        return True
    return any(e["kind"] == "packet" for e in fx["rw"]["events"])


def _archive_done(res: dict, hint: dict, put, credit: int = 0) -> dict:
    """Done on the archive rules' proven reading, its decisive proof rows as the evidence; a credit EMA shows on the line is
    named as the claim's to refund or recoup."""
    rows = [{"source": r.get("source") or "835", "kind": r.get("kind") or "proof", "date": r.get("date"), "text": r.get("text")}
            for r in hint.get("proof") or [] if r.get("decisive")]
    res["appeal_block"] = "paid"
    basis = _hint_text(hint, 170 if credit else 240) or "The 835 archive shows it settled"
    if credit:
        basis = (f"{basis}. EMA shows a credit of {usd(credit)} on this line: a refund or recoupment of the claim (its credit "
                 "item), not work on this denial")
    return put("done", "archive_" + str(hint.get("reclass") or hint.get("rule") or "rule"), basis, rows)


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


# The PLB reasons the ledger reads as a takeback naming a claim (detect._build_ledger): an overpayment recovered (WO), a
# balance carried forward or recovered (FB) and Medicare's non-reimbursable withholding (J1); an adjustment (CS) or an
# authorized return (72) naming the claim nets with them, and never starts a takeback alone.
TAKEBACK_REASONS = ("WO", "FB", "J1")
TAKEBACK_NETS = ("WO", "FB", "J1", "CS", "72")


def taken_back_since(fx: dict, since: Any) -> bool:
    """A recoupment the 835s name for one of the bill's claims still takes money after `since`: the claim's WO, FB, J1,
    CS and 72 entries net to more than nothing and one of them, a WO, FB or J1 taking money, is dated on or after
    `since` (the ledger nets a claim's entries the same way: a balance carried forward and recovered later is no
    takeback). A payment made before it may not stand (money-8: a WO, an FB or a J1 alike)."""
    since = str(since or "")[:10]
    per: dict[str, list] = {}
    for q in fx["r835"].get("plb") or []:
        if q.get("reason") in TAKEBACK_NETS:
            per.setdefault(q.get("claim_id") or "", []).append(q)
    for qs in per.values():
        if sum(q.get("cents") or 0 for q in qs) > 0 and any(
                q["reason"] in TAKEBACK_REASONS and (q.get("cents") or 0) > 0 and str(q.get("date") or "")[:10] >= since for q in qs):
            return True
    return False


def _taken_unlisted(x: dict) -> bool:
    """A recoupment took money from this 835 payment (ledger_taken) and no item of its own carries that takeback."""
    return bool(x.get("taken")) and not x.get("taken_listed")


# CARC 22 (this care may be covered by another payer) and 23 (the impact of the prior payer's adjudication) on an answer:
# the payer processed the claim after another payer's answer, whatever claim status it reports
AFTER_OTHER_CARCS = frozenset({"22", "23"})


def _after_other_payer(x: dict) -> bool:
    """This 835 answer was processed after another payer's (CARC 22 or 23 on its line or its claim, by the payer): a
    supplement answering with claim status 1 is paying its share as second payer, never another plan's payment as primary
    (fx-ledger-r1-3: 342 of the 820 paid supplement answers on the real copy that report claim status 1 carry OA-22 or
    OA-23)."""
    return any(c in AFTER_OTHER_CARCS and g != "PR" for g, c, _a in list(x.get("adj") or []) + list(x.get("cadj") or []))


def _zero_billed(item: dict) -> set[tuple]:
    """The (date, code) services a claim-level item bills at $0.00: its evidence lines in the [code, date, charge, ...] shape
    the never-answered claims carry, with a known charge of nothing (a post-operative visit beside the service). A line
    whose charge is unknown is not one."""
    out: set[tuple] = set()
    for ln in _list(_ev(item).get("lines")):
        if isinstance(ln, (list, tuple)) and len(ln) >= 3 and isinstance(ln[1], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", ln[1][:10] or "") \
                and isinstance(ln[2], (int, float)) and not isinstance(ln[2], bool) and ln[2] == 0 and ln[0]:
            out.add((ln[1][:10], ln[0]))
        elif isinstance(ln, dict) and (ln.get("cpt") or ln.get("code")):
            ch = ln.get("charge_cents", ln.get("charges"))
            if isinstance(ch, (int, float)) and not isinstance(ch, bool) and ch == 0:
                out.add((ln.get("service_date") or ln.get("svc") or item.get("service_date"), ln.get("cpt") or ln.get("code")))
    return out


def _ema_pay_taken(fx: dict, p: dict) -> dict:
    """What the payer took back since of one insurance payment EMA posted, by the 835s: {'gone': cents no item of its own
    carries, 'listed': cents worked on an item of its own, 'rows': the 835 payment lines it posts, 'date': their remittance's
    date}. The 835 payment it posts (its check's remittance, the same code and date of service) is the ledger's to judge:
    what the payer's recoupments took of it (ledger_taken), listed or not; all of it when the payer reversed it. A payment no
    835 on file carries: a recoupment the 835s name for the claim after it was posted takes it all (taken_back_since), on
    no item of its own. EMA posts the payment and seldom a recoupment on another patient's check, so its line looks
    settled on money the payer took back (fx-ledger-r1-4)."""
    cents = int(p.get("cents") or 0)
    out = {"gone": 0, "listed": 0, "rows": [], "date": None}
    if cents <= 0:
        return out
    rows = [x for x in fx["r835"]["rows"] if x.get("line_key") and x["status"] != "22" and (x["paid"] or 0) > 0
            and p.get("cpt") in (x["code"], x["ocode"]) and (not p.get("svc") or not x["svc"] or x["svc"] == p["svc"])
            and ((p.get("trace_key") and x.get("tk") == p["trace_key"])
                 or (p.get("check") and x.get("trace") and _same_check(p["check"], x["trace"])))]
    if rows:
        out["rows"], out["date"] = rows, min(x["date"] or "" for x in rows) or None
        if any(x.get("reversed_by") for x in rows):
            out["gone"] = cents
            return out
        out["gone"] = min(cents, sum(min(x["taken"], x["paid"] or 0) for x in rows if _taken_unlisted(x)))
        out["listed"] = min(cents - out["gone"], sum(min(x["taken"], x["paid"] or 0) for x in rows if x.get("taken") and x.get("taken_listed")))
        return out
    if not p.get("in_835") and taken_back_since(fx, p.get("posted")):
        out["gone"] = cents
    return out


def _taken_note(rows: list[dict]) -> str:
    """The words a proof adds when a payment it counts was taken back since and that takeback is worked on an item: its own
    (a TAKEBACK item, or the recoupment's own listing: ledger_taken.listed 1), or the payer's own denial of the service,
    which the payment had answered (listed 2). The person checking the proof sees the takeback and where it is worked."""
    x = next((y for y in rows if y.get("taken") and y.get("taken_listed")), None)
    if not x:
        return ""
    where = "on the payer's own denial of the service" if x["taken_listed"] == 2 else "on its own takeback item"
    return f"; {x['payer'] or 'the payer'} took {usd(min(x['taken'], abs(x['paid'] or 0)))} of it back later, worked {where}"


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
    # the later EMA payments this branch and the next count, less what the payer took back of each since on the 835s (EMA
    # posts the payment and seldom the recoupment: a payment recouped since pays nothing, m1-fx-ledger-5)
    later_gone, later_rows = 0, []
    if oc in ("paid_after", "paid_after_other"):
        for p in fx["ema"]["pays"]:
            if (p["posted"] or "") > mine and (p["cents"] or 0) > 0:
                t = _ema_pay_taken(fx, p)
                later_gone += t["gone"]
                later_rows += t["rows"]
    if oc == "paid_after" and (o.get("paid_after_same") or 0) - later_gone >= max(1, thr):
        net = (o.get("paid_after_same") or 0) - later_gone
        return {"rule": "paid_later_ema", "cents": net, "date": fp.get("date"), "payer": fp.get("payer"),
                "check_tail": fp.get("check_tail"), "source": "production",
                "text": f"Paid later: {usd(net)} by {fp.get('payer') or 'the payer'} on a check with no 835 "
                        f"({day(fp.get('date'))}" + (f", check ending {fp['check_tail']})" if fp.get("check_tail") else ")")
                        + _taken_note(later_rows)}
    # another payer's later payment settles the line only when it paid as a payer that pays first (rebilled to the right
    # one); the second payer on file paying its share (the coinsurance) leaves the primary's denial standing
    later_roles = {p.get("role") for p in fx["ema"]["pays"] if (p["cents"] or 0) > 0 and (p["posted"] or "") > mine}
    if (oc == "paid_after_other" and tot["n"] and tot["balance"] <= 0 and later_roles != {"secondary"}
            and (o.get("paid_after_other") or 0) - later_gone >= max(1, thr)):
        return {"rule": "rebilled_paid", "cents": o.get("paid_after_other") or 0, "date": fp.get("date"), "payer": fp.get("payer"),
                "check_tail": fp.get("check_tail"), "source": "production",
                "text": f"Rebilled to {fp.get('payer') or 'another payer'}, paid: {usd(o.get('paid_after_other'))} on {day(fp.get('date'))} "
                        + (f"(check ending {fp['check_tail']}, no 835)" if fp.get("check_tail") else "(no 835)") + "; EMA shows the line settled"}
    rows = fx["r835"]["rows"]
    stand = standing_rows(rows)
    # the payments a recoupment (PLB) took back since, which the detection run's ledger names on the item: they pay nothing
    gone = set(_ev(f).get("taken_back_lines") or [])
    if gone:
        stand = [x for x in stand if x.get("line_key") not in gone]
    # and what the ledger's recoupments took from any payment of the bill (a WO, FB or J1 on a later check), when no item of
    # its own carries that takeback (the service's own answer is the only place it is worked): a payment taken back in full
    # pays nothing, one taken back in part pays what is left of it. A takeback on an item of its own (a TAKEBACK item of the
    # payment, or the recoupment's own listing) is worked there: the payment still answers this item and its proof names
    # the takeback, so the same money is never worked twice
    stand = [dict(x, paid=(x["paid"] or 0) - x["taken"]) if _taken_unlisted(x) else x for x in stand
             if not (_taken_unlisted(x) and x["taken"] >= (x["paid"] or 0) > 0)]
    own = own_rows(fx)
    once = code_once(fx)
    lcns = {x.get("lcn") for x in own if x.get("lcn")}
    if not lcns and own:
        # a re-adjudication often drops the line control number: the same claim's earlier answer of the code carries it
        o0 = own[0]
        lcns = {x["lcn"] for x in rows if x.get("lcn") and x["code"] == o0["code"] and x["status"] != "22" and (
            (bool(o0.get("pccn")) and x.get("pccn") == o0.get("pccn")) or
            (x["pcn"] == o0["pcn"] and bool(o0.get("received")) and x.get("received") == o0.get("received")))}
    # the units the item is about: every unit billed on its line, or, when the payer paid part of the line, the units it
    # denied (a frequency denial of a second fraction is settled by that fraction paid on a resubmission)
    billed_u = max([abs(x.get("ounits") or x.get("units") or 1) for x in own] or [1])
    paid_u = max([abs(x.get("units") or 0) for x in own if (x.get("paid") or 0) > 0 and x.get("ounits")] or [0])
    units = billed_u - paid_u if 0 < paid_u < billed_u else billed_u

    def same_line(x: dict) -> bool:
        """This 835 line is the item's own line answered again: its line control number, or the code billed once on the
        item's claim and date and answered on another submission of the bill covering the units."""
        covers = abs(x.get("units") or 1) >= units
        if lcns and x.get("lcn") in lcns and x["pcn"] == (own[0]["pcn"] if own else x["pcn"]):
            return covers
        return once and x["claim_key"] != f.get("claim_key") and covers
    # the detection run found fewer units paid than billed (its hint 'partly paid'): the 835 has decided it is short
    short_units = "partly_paid" in _ev(f)
    # a claim nobody answered (a never-answered claim's item): only a payment made after the claim went out answers it, and
    # every service it bills must be paid (a resend that added a service is not paid by the first send's payment: answers-5)
    whole_claim = sig in CLAIM_SIGNALS and not f.get("line_key")
    after = max(mine, str(_ev(f).get("sent") or "")[:10]) if whole_claim else mine
    later_paid = [] if short_units else [x for x in stand if (x["date"] or "") > after and (x["paid"] or 0) > 0 and same_line(x)]
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
                            f"primary payer's answer (OA-23); this denial was of the claim sent to {denier} as if it were primary"
                            + _taken_note(second)}
        if cat in SHORT:
            # an underpayment or downcode is settled when the payer that paid short paid the shortfall later (95%), net of
            # its own payment a reversal took back (a reversal and the same amount paid again is no money), or when its
            # newest answer allowed the usual amount. A second payer's payment (its share of the coinsurance) never counts.
            mine_ = [x for x in later_paid if _same_payer(row_group(x)[0], denier) and x["status"] not in SECOND_STATUS]
            taken = sum(x["paid"] or 0 for x in own if x.get("reversed_by") and (x["paid"] or 0) > 0)
            net = sum(x["paid"] or 0 for x in mine_) - taken
            need = int(0.95 * (f.get("shortfall_cents") or 0))
            newest = max(mine_, key=lambda r: (r["date"] or "", r.get("line_key") or "")) if mine_ else None
            exp = f.get("expected_cents") or 0
            at_rate = bool(newest) and exp > 0 and (newest.get("allowed") or 0) >= int(0.99 * exp)
            if mine_ and (net >= max(1, need) or at_rate):
                x = newest
                got = (f"Paid again on a later remittance: {usd(net)} more by {x['payer']} on {day(x['date'])} (check ending "
                       f"{x['check_tail']})" if net >= max(1, need) else
                       f"Paid at the usual rate on a later remittance: {x['payer']} allowed {usd(x.get('allowed'))} on {day(x['date'])} "
                       f"(check ending {x['check_tail']}), at least the usual {usd(exp)}")
                return {"rule": "paid_later_835", "cents": max(0, net), "date": x["date"], "payer": x["payer"], "check_tail": x["check_tail"],
                        "source": "835", "text": got + _taken_note(mine_)}
        else:
            # a denial is settled by a later payment of the line as a payer that pays first; a second payer's payment of its
            # share (claim status 2, 3, 20, 21 from another payer) never settles the primary's denial. With no expected
            # amount, a payment must reach half of what the payer allowed (else half the charge less its reductions)
            prim = [x for x in later_paid if not (x["status"] in SECOND_STATUS and not _same_payer(row_group(x)[0], denier))]
            paid = sum(x["paid"] or 0 for x in prim)
            floor = thr if thr > 1 else _close_floor(own, prim)
            if whole_claim:
                # a service the claim bills at $0.00 (a post-operative visit no payer pays) needs no payment of its own
                # (T-N7; fx-ledger-r2-6)
                keys = set(_service_keys(f)) - _zero_billed(f)
                got = {(x["svc"], c) for x in prim for c in (x["code"], x["ocode"]) if c}
                if not keys <= got:
                    prim = []           # a service of the claim has no payment of its own since the send: not paid
            if prim and paid >= floor:
                x = prim[0]
                return {"rule": "paid_later_835", "cents": paid, "date": x["date"], "payer": x["payer"], "check_tail": x["check_tail"],
                        "source": "835", "text": f"Paid on a later remittance: {usd(paid)} by {x['payer']} on {day(x['date'])} "
                                                 f"(check ending {x['check_tail']})" + _taken_note(prim)}
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
            # each payment EMA posted, less what the payer took back of it since on the 835s and no item of its own carries
            # (EMA posts the payment and seldom the recoupment: fx-ledger-r1-4); a takeback worked on its own item is named
            prior = []
            for p in fx["ema"]["pays"]:
                if (p["posted"] or "") <= mine and (p["cents"] or 0) > 0 and (not p.get("group") or _same_payer(p.get("group"), denier)):
                    t = _ema_pay_taken(fx, p)
                    if p["cents"] - t["gone"] > 0:
                        prior.append(dict(p, cents=p["cents"] - t["gone"], taken_rows=t["rows"]))
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
                                "settled; the payer's later answer restates that payment" + _taken_note(p["taken_rows"])}
        replacement = any(x.get("freq") == "7" for x in own)
        as_primary = any(x["status"] in ("1", "4", "19") for x in own) or not own
        if before:
            x = max(before, key=lambda r: r["date"] or "")
            same = _same_payer(row_group(x)[0], denier)
            settled = not tot["n"] or tot["balance"] <= 0
            # another plan's answer owes nothing only when it processed the claim as primary after another plan paid as
            # primary (two plans each taking itself for primary: the right one paid); a second payer's denial of the
            # coinsurance after the primary paid can still be owed. A payment processed after another payer's (CARC 22 or
            # 23, whatever its claim status) is a second payer's share, never a primary payment (fx-ledger-r1-3)
            if not same and settled and as_primary and x["status"] in ("1", "19") and not _after_other_payer(x):
                return {"rule": "paid_before_other_payer", "cents": x["paid"], "date": x["date"], "payer": x["payer"],
                        "check_tail": x["check_tail"], "source": "835",
                        "text": f"Already paid: {usd(x['paid'])} by {x['payer']} on {day(x['date'])}, before this denial; the "
                                f"{denier} denial is another plan's answer and owes nothing" + _taken_note([x])}
            # a payment whose takeback is worked on an item of its own: EMA's open balance may be that takeback, posted (the
            # restatement of the payment is not the takeback's work: fx-ledger-r1-1)
            listed_back = bool(x.get("taken_listed")) and tot["balance"] <= min(x.get("taken") or 0, x["paid"] or 0)
            if same and (carc in DUP_CARCS or replacement) and (settled or listed_back):
                note = _taken_note([x])
                return {"rule": "replacement_denied_paid" if replacement else "paid_before_duplicate", "cents": x["paid"],
                        "date": x["date"], "payer": x["payer"], "check_tail": x["check_tail"], "source": "835", "watch": True,
                        "text": (f"Paid {usd(x['paid'])} on {day(x['date'])} on the original claim; the payer denied the replacement claim"
                                 + (note or " and has not taken the payment back: watch for a takeback") if replacement else
                                 f"Already paid {usd(x['paid'])} on {day(x['date'])} (check ending {x['check_tail']}); this denial is "
                                 "of a duplicate" + note)}
            # the same payer paid this service before, the payment still stands (no reversal, no takeback naming the
            # claim) and brought at least what this line should: the later denial answers another copy of the claim (a
            # superseded original after the corrected claim was paid, C62), whatever its reason
            takeback = taken_back_since(fx, x["date"])
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
            # what EMA counts as paid before, less what the payer took back of those payments since on the 835s and no item
            # of its own carries, where EMA never posted the takeback (its own posted takebacks are netted in its figures
            # already): EMA's line looks settled on money that is gone (fx-ledger-r1-4)
            gone, named = 0, []
            anchor = str(o.get("anchor") or mine)
            for p in fx["ema"]["pays"]:
                if (p["cents"] or 0) <= 0:
                    continue
                t = _ema_pay_taken(fx, p)
                if (str(t["date"] or "") <= mine) if t["rows"] else ((p["posted"] or "") < anchor):
                    gone += t["gone"]
                    named += t["rows"]
            posted_back = sum(-(p["cents"] or 0) for p in fx["ema"]["pays"] if (p["cents"] or 0) < 0)
            left = o["paid_before"] - max(0, gone - posted_back)
            same = _same_payer(_grp_kind(pb.get("payer"))[0], denier) if pb.get("payer") else True
            if left > 0 and (not same or carc in DUP_CARCS or replacement):
                return {"rule": "paid_before_other_payer" if not same else "paid_before_duplicate", "cents": left,
                        "date": pb.get("date"), "payer": pb.get("payer"), "check_tail": pb.get("check_tail"), "source": "production",
                        "watch": same,
                        "text": f"Already paid: EMA shows {usd(left)} by {pb.get('payer') or 'insurance'} on {day(pb.get('date'))}, "
                                "before this denial, and the line settled" + ("" if not same else "; this denial is of a duplicate")
                                + _taken_note(named)}
        # a Medicaid plan's denial of a service another payer had paid first, or a duplicate denial of one the same payer
        # had paid first: EMA's payments on the item's own lines, payer by payer (fix6)
        first = paid_first_elsewhere(fx)
        if first:
            return first
    # an underpayment whose allowed amounts on the date and code add up to the usual rate (a line split across claims)
    if cat in SHORT and (f.get("expected_cents") or 0) > 0:
        # the payer split the line across claims of one remittance (MA15): the pieces share the line control number
        pieces = [x for x in stand if x.get("line_key") and lcns and x.get("lcn") in lcns and (x["date"] or "") == mine]
        allowed = sum(x["allowed"] or 0 for x in pieces)
        nclaims = len({x["claim_key"] for x in pieces})
        # the pieces: across claims of one remittance (MA15), or lines of one claim that share the line control number
        # (a payer paying one service as two lines, C27)
        if len(pieces) > 1 and allowed >= int(0.99 * f["expected_cents"]):
            where = f"across {nclaims} claims" if nclaims > 1 else f"into {len(pieces)} lines of the claim"
            return {"rule": "paid_in_full_split", "cents": allowed, "date": mine, "payer": f.get("payer_name"),
                    "source": "835", "text": f"Paid in full: the payer split {f.get('procedure_code')} {where} on the remittance of {day(mine)}, "
                                             f"and their allowed amounts add up to {usd(allowed)}, the usual rate"}
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


def _close_floor(own: list[dict], pays: list[dict]) -> int:
    """What a later payment must reach to settle a denial whose expected amount is unknown: half of what the paying answer
    allowed, else half of the line's charge less its routine reductions. Never one cent: a second payer's coinsurance is
    not the payer paying (M4)."""
    allowed = max((x.get("allowed") or 0 for x in pays), default=0)
    if allowed > 0:
        return max(1, allowed // 2)
    x = own[0] if own else None
    if not x:
        return 1
    co = sum(a for g, c, a in x["adj"] if g == "CO" and c in CONTRACTUAL_CARCS)
    return max(1, ((x["charge"] or 0) - co) // 2)


# ------------------------------------------------------------------------------- paid first, by another payer or the same
# The payer-name rules' catch-all for a name with MEDICAID in it (codes.PAYER_RULES). EMA names a company's Medicaid plan
# "<company> Medicaid Community Plan" and the state's own program "Medicaid of <state>": both land in it, so the company a
# name begins with is what tells them apart. On real data a Medicare Advantage company's checks are posted under such a
# name, and read as the state's Medicaid they looked like the denying payer's own (fix6).
MEDICAID_CATCH_ALL = "Medicaid (other)"


def _ema_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Which payer each EMA payer name stands for, by the remittances EMA posted under it: {EMA payer name: the 835 payer
    name of most of the checks EMA posted under that name that are exactly an 835's trace (ema_checks)}."""
    if not _table(conn, "ema_checks") or not _table(conn, "transactions") or "trace_key" not in _cols(conn, "ema_checks"):
        return {}
    per: dict[str, dict] = {}
    for name, payer, n in conn.execute("""SELECT c.payer, t.payer_name, COUNT(*) FROM ema_checks c
                                          JOIN transactions t ON t.transaction_key = c.trace_key
                                          WHERE c.match = 'exact' AND c.payer IS NOT NULL AND t.payer_name IS NOT NULL
                                          GROUP BY c.payer, t.payer_name"""):
        per.setdefault(name, {})[payer] = n
    return {name: max(c.items(), key=lambda kv: (kv[1], kv[0]))[0] for name, c in per.items()}


def _ema_payer_is_denier(fx: dict, p: dict) -> bool | None:
    """Whether a payment (or $0 answer) EMA posted is the denying payer's own: its check is one of that payer's remittances
    on the bill; else EMA posts that payer's remittances under its name (_ema_names); else its name says so (the name
    rules, a company's plan in the Medicaid catch-all keeping the company its name begins with). None when nothing names
    the payer. p: {'payer', 'check'}."""
    f = fx["item"]
    denier = f.get("payer_group") or _grp_kind(f.get("payer_name"))[0]
    names = {x["payer"] for x in fx["r835"]["rows"] if x.get("payer") and _same_payer(row_group(x)[0], denier)}
    if f.get("payer_name"):
        names.add(f["payer_name"])
    chk = p.get("check")
    if chk:
        for x in fx["r835"]["rows"]:
            if x.get("trace") and _same_check(chk, x["trace"]):
                return x.get("payer") in names
    name = str(p.get("payer") or "")
    m = ((fx.get("ctx") or {}).get("ema_names") or {}).get(name)
    if m:
        return m in names or _same_payer(_grp_kind(m)[0], denier)
    g = _grp_kind(name)[0] if name else (p.get("group") or "")
    if not g:
        return None
    if g == MEDICAID_CATCH_ALL and _family(name) != _family(g):
        return _family(name) == _family(denier)
    return _same_payer(g, denier)


def paid_first_elsewhere(fx: dict) -> dict | None:
    """A denial the payments EMA posted before it already settle, as paid_proof's proof, or None (fix6):
    - a Medicaid plan denied a service whose first payer on EMA's claim is another plan, after a payer other than the
      Medicaid plan paid it: Medicaid pays after other insurance and only up to its own rate, and the member cannot be
      billed, so its denial leaves no money to recover (the rule EMA-only denials follow, ema_checks C5) ('paid_first_other_payer');
    - a duplicate denial (CO-18, B13) of a service the same payer paid first, whatever share of a second payer is still
      open on the line ('paid_before_duplicate').
    Counted: the payments on the item's own EMA lines (never the surgery center's; for a claim's item, a line whose charge
    is the claim's lines' to the cent; for a line's item, a code the claim carries once), payer by payer, posted before the
    item's remittance and net of that payer's later takebacks, and never a payment whose remittance was reversed. For a
    duplicate denial they reach half of what the item should bring. For a Medicaid denial they reach the whole of it:
    Medicaid owes up to its own rate less what the first payer paid, so a first payment under that rate leaves the rest
    owed, and the item stays work, its basis naming what is still Medicaid's to pay (fx['paid_first_partial'], money-9).
    A takeback the 835s name for the claim after the first payment keeps the item as work (the ledger's takeback reasons:
    WO, FB, J1, with CS and 72 netting, money-8). EMA's postings decide, never its claim status; a person's status decides
    before any of this."""
    f = fx["item"]
    if f.get("category") not in ("denial", "bundling") or (f.get("paid_cents") or 0) > 0 or f.get("signal") in EMA_ONLY:
        return None
    own = own_rows(fx)
    if any((x["paid"] or 0) > 0 for x in own if x["status"] != "22"):
        return None
    mine = str(f.get("payment_date") or "")[:10]
    denier = f.get("payer_group") or _grp_kind(f.get("payer_name"))[0]
    medicaid = f.get("payer_kind") == "medicaid" or "medicaid" in str(denier or "").lower()
    dup = str(f.get("carc") or "") in DUP_CARCS
    if not mine or not (medicaid or dup):
        return None
    keys = set(_service_keys(f))
    if f.get("line_key") and not code_once(fx):
        return None                 # EMA's line holds the code for another line of the claim as well
    lines = []
    for ln in fx["ema"]["lines"]:
        if (ln["svc"], ln["cpt"]) not in keys or ln.get("facility"):
            continue
        if not f.get("line_key"):
            charge = sum(x["charge"] or 0 for x in own if x.get("line_key") and x["status"] != "22" and x["svc"] == ln["svc"]
                         and ln["cpt"] in (x["code"], x["ocode"]))
            if charge and charge != (ln.get("charges") or 0):
                continue            # EMA's line holds this code for other claims' lines too
        lines.append(ln)
    if not lines:
        return None
    if medicaid and not dup and not all(_ema_payer_is_denier(fx, {"payer": ln.get("primary_payer")}) is False for ln in lines):
        return None                 # the Medicaid plan is the one EMA bills first (or EMA names no first payer)
    reversed_traces = [x["trace"] for x in fx["r835"]["rows"] if x.get("trace") and x.get("reversed_by")]
    counted: list[dict] = []
    for ln in lines:
        ps = [p for p in fx["ema"]["pays"] if p["link"] == ln["link"] and p["svc"] == ln["svc"] and p["cpt"] == ln["cpt"]
              and (p["cents"] or 0) != 0]
        for name in sorted({p["payer"] for p in ps}):
            mine_ps = [p for p in ps if p["payer"] == name]
            before = [p for p in mine_ps if (p["posted"] or "") < mine]
            pos = [p for p in before if p["cents"] > 0]
            if not pos or any(_same_check(p["check"], t) for p in pos for t in reversed_traces):
                continue
            who = _ema_payer_is_denier(fx, pos[0])
            if who is None or who != dup:
                continue            # a Medicaid denial counts another payer's money; a duplicate denial its own payer's
            kept = before + [p for p in mine_ps if (p["posted"] or "") >= mine and p["cents"] < 0]
            if sum(p["cents"] for p in kept) > 0:
                counted += kept
    total = sum(p["cents"] for p in counted)
    # a duplicate denial restates its payer's own payment: half of what the item should bring settles it. Medicaid as second
    # payer pays up to its own rate: the first payer's payments must reach the whole of what the item should bring
    worth = int(f.get("expected_cents") or f.get("shortfall_cents") or 0)
    need = _threshold(f) if dup else worth
    if need <= 1:
        # nothing says what the item should bring: half of what its own EMA lines charge
        need = max(1, sum(ln.get("charges") or 0 for ln in lines) // 2)
    # half of what the item should bring: a first payment under it never settles the denial. With nothing to say what the
    # item should bring, that is the floor of half its own EMA charges above (fx-ledger-r2-3: never _threshold's one cent)
    half = _threshold(f) if worth > 1 else need
    if total <= 0:
        return None
    pos = sorted((p for p in counted if p["cents"] > 0), key=lambda p: (p["posted"] or "", p["check"]))
    first = pos[0]
    if taken_back_since(fx, first["posted"]):
        return None                 # a takeback named for the claim since: the payment may not stand (money-8)
    if total < need:
        if dup or total < half:
            return None
        # the first payer paid part of what Medicaid allows: the rest of it is still Medicaid's to pay, up to what EMA shows
        # open on the line. Nothing of it open in EMA leaves nothing to recover, as when the first payment reached the rate
        open_ = sum(ln.get("balance") or 0 for ln in lines)
        owed = max(0, min(worth - total, open_))
        if owed > 0:
            fx["paid_first_partial"] = {"paid": total, "owed": owed, "worth": worth, "open": open_, "payer": first["payer"],
                                        "date": first["posted"], "in_835": bool(first.get("in_835"))}
            return None
    rows = [{"source": "production", "kind": "payment", "date": p["posted"], "payer": p["payer"], "cents": p["cents"],
             "check_tail": p["check_tail"], "in_835": bool(p.get("in_835")), "line": f"{p['cpt']} {p['svc']}",
             "text": (f"EMA posted {usd(p['cents'])} from {p['payer'] or 'insurance'} on {day(p['posted'])} for {p['cpt']} "
                      f"(check ending {p['check_tail'] or '?'}" + ("" if p.get("in_835") else ", no 835") + ")"
                      if p["cents"] > 0 else
                      f"EMA posted a takeback of {usd(-p['cents'])} by {p['payer'] or 'insurance'} on {day(p['posted'])} for {p['cpt']}")}
            for p in sorted(counted, key=lambda p: (p["posted"] or "", p["cpt"], p["check"]))]
    # the words stay under the 300 characters a stored basis keeps: the checks are in the rows
    open_ = sum(ln.get("balance") or 0 for ln in lines)
    paid = f"EMA posted {usd(total)} on {day(first['posted'])}" + ("" if first.get("in_835") else " (no 835)")
    if dup:
        text = (f"Already paid: EMA posted {usd(total)} from {first['payer'] or denier} on {day(first['posted'])}"
                + ("" if first.get("in_835") else " (no 835)") + ", before this duplicate denial: it restates that payment"
                + (f"; the second payer's share of {usd(open_)} is still open in EMA" if open_ > 0 else ""))
        rule = "paid_before_duplicate"
    else:
        text = (f"Already paid by {first['payer']}: {paid}, before this {denier} denial. As second payer, Medicaid pays only up to "
                "its own rate and the member cannot be billed: no money to recover"
                + (f"; {usd(open_)} is still open in EMA" if open_ > 0 else ""))
        rule = "paid_first_other_payer"
    return {"rule": rule, "cents": total, "date": first["posted"], "payer": first["payer"], "check_tail": first["check_tail"],
            "source": "production", "watch": True, "text": text, "rows": rows}


CONTRACTUAL_CARCS = frozenset({"45", "253", "144", "59", "131", "94", "237", "2", "23"})
COB_CARCS = frozenset({"22"})
CHECK_HINTS = ("Possibly paid", "Processed on claim", "Partly paid")
# work-now rules whose next step is a check before anything else: never a packet, a fax or a bulk appeal
CHECK_RULES = ("check_first", "settled_in_ema")
# appeal_block for a work-now item whose next step is not an appeal (unit B's bulk SQL reads appeal_block IS NULL): its
# route's reason, in unit C's BLOCK_WORDS
ROUTE_BLOCKS = {"decide": "check_first", "corrected": "corrected", "call": "call", "cob": "cob", "eob_first": "eob_first",
                "bill_secondary": "bill", "bill_patient": "bill", "post": "post", "refund": "credit", "contract": "owner",
                "policy": "owner", "fees": "fees", "repricing": "batch"}


def _same_check(a: Any, b: Any) -> bool:
    """Two check or trace numbers are one: equal letters and digits (leading zeros dropped), or the longer ends with the
    shorter of at least 8 (EMA keeps some check numbers cut short)."""
    x = re.sub(r"[^A-Za-z0-9]", "", str(a or "")).upper().lstrip("0")
    y = re.sub(r"[^A-Za-z0-9]", "", str(b or "")).upper().lstrip("0")
    if not x or not y:
        return False
    if x == y:
        return True
    s, longer = sorted((x, y), key=len)
    return len(s) >= 8 and longer.endswith(s)


def check_money(fx: dict) -> list[dict]:
    """The item's line check by check, the 835 against EMA: {'tk', 'trace_tail', 'date', 'payer', 'group', 'method',
    'net_835' (every answer of the line on that 835, reversals included), 'ema' (EMA's money posted under the same check),
    'ema_rows', 'reversal' (the 835 reverses an answer of the line)}. EMA's posting belongs to a check by the 835 the
    checks table matched it to (trace_key), else by the check's whole number, never by its last four characters."""
    by: dict = {}
    for x in fx["r835"]["rows"]:
        if not x.get("line_key"):
            continue
        d = by.setdefault(x.get("tk"), {"tk": x.get("tk"), "trace": x.get("trace"), "trace_tail": x.get("check_tail"),
                                       "date": x.get("date"), "payer": x.get("payer"), "group": row_group(x)[0],
                                       "method": x.get("method"), "net_835": 0, "ema": 0, "ema_rows": 0, "reversal": False})
        d["net_835"] += x["paid"] or 0
        d["reversal"] = d["reversal"] or x["status"] == "22"
    for p in fx["ema"]["pays"]:
        hit = by.get(p.get("trace_key")) if p.get("trace_key") else None
        if hit is None and p.get("check"):
            hit = next((d for d in by.values() if d.get("trace") and _same_check(p["check"], d["trace"])), None)
        if hit is not None:
            hit["ema"] += p["cents"] or 0
            hit["ema_rows"] += 1
    return sorted(by.values(), key=lambda d: (d["date"] or "", d["trace_tail"] or ""))


def plb_unbooked(fx: dict) -> list[dict]:
    """Takebacks (PLB WO or 72) whose reference names the item's claim that EMA shows no takeback for on the line."""
    plb = [p for p in fx["r835"].get("plb") or [] if p["reason"] in ("WO", "72")]
    if not plb:
        return []
    neg = [p for p in fx["ema"]["pays"] if (p["cents"] or 0) < 0]
    return [p for p in plb if not any(abs(abs(n["cents"]) - abs(p["cents"])) <= 1 for n in neg)]


def unposted_takebacks(fx: dict) -> dict:
    """Takebacks on the payer's 835 that EMA never posted on the item's line (EMA then shows money the payer took back):
    per check that carries a reversal of the line, EMA's money under that check less the 835's net under it; and, when no
    reversal of the line is on file, each provider-level takeback (PLB WO or 72) naming the claim with no takeback in EMA
    (a reversal and its PLB recoupment are one takeback, never counted twice). {'cents', 'rows': [{'cents', 'date',
    'payer', 'check_tail', 'kind'}]}; nothing when EMA does not hold the line."""
    if not fx["ema"]["lines"]:
        return {"cents": 0, "rows": []}
    rows = []
    checks = check_money(fx)
    for d in checks:
        if d["reversal"] and d["ema"] - d["net_835"] > 0:
            rows.append({"cents": d["ema"] - d["net_835"], "date": d["date"], "payer": d["payer"], "check_tail": d["trace_tail"],
                         "kind": "reversal"})
    if not any(d["reversal"] for d in checks):
        for p in plb_unbooked(fx):
            rows.append({"cents": abs(p["cents"] or 0), "date": p["date"], "payer": p["payer"], "check_tail": p["check_tail"], "kind": "plb"})
    return {"cents": sum(r["cents"] for r in rows), "rows": rows}


def _credit_left(fx: dict, credit: int) -> tuple[int, dict]:
    """The part of a credit EMA shows that is money owed back, and the takebacks EMA never posted: a credit the unposted
    takebacks cover (within $1) is no refund at all, only a posting to fix."""
    tb = unposted_takebacks(fx)
    if credit <= 0 or tb["cents"] <= 0:
        return credit, tb
    left = credit - tb["cents"]
    return (0 if left <= 100 else left), tb


def _takeback_words(tb: dict) -> str:
    r = max(tb["rows"], key=lambda x: x["date"] or "") if tb["rows"] else {}
    who = _grp_kind(r.get("payer"))[0] if r.get("payer") else "the payer"
    return (f"{who}'s takeback of {usd(tb['cents'])}" + (f" (835 of {day(r.get('date'))}" + (f", check ending {r['check_tail']}" if r.get("check_tail") else "")
                                                         + ")" if r.get("date") else ""))


def _carc_group(fx: dict) -> str:
    """The group of the item's own reason on its remittance (CO, PR, OA, PI), '' when it cannot be told."""
    f = fx["item"]
    carc = str(f.get("carc") or "")
    if f.get("carc_group"):
        return str(f["carc_group"])
    for x in own_rows(fx):
        for g, c, _a in list(x["adj"]) + list(x["cadj"]):
            if c == carc:
                return g
    return ""


def patient_denial(f: dict) -> bool:
    """A patient-group denial the owner's decision 2 bills to the patient: archive rule 8's PR-288, PR-27, PR-26, PR-200,
    PR-49 or PR-204 (never CO-147, a contractual reason, and never the patient's cost share)."""
    if (f.get("signal") or "") not in PATIENT_DENIAL_SIGNALS or str(f.get("carc") or "") not in PATIENT_DENIAL_CODES:
        return False
    return str(f.get("carc_group") or _ev(f).get("carc_group") or "").upper() == "PR"


def second_share_back(f: dict) -> bool:
    """A second payer's share taken back in full (F1's money-2, routed to F5): a TAKEBACK item of a second payer's line
    (evidence second_payer), never a Medicaid plan's (its payment is payment in full: the share is not its member's). The
    patient is billed for it as for a patient-group denial: Billed the patient, then the patient's payment in EMA."""
    return (f.get("signal") or "") == "TAKEBACK" and bool(_ev(f).get("second_payer")) and f.get("payer_kind") != "medicaid"


def first_payer_answer(fx: dict) -> dict | None:
    """The first payer's standing answer of a second payer's line (its code and date): the newest standing answer by
    another payer on or before the second payer's own payment, with what it put on the patient. {'row', 'group', 'pr',
    'pr_codes', 'co_codes', 'paid'}, or None when no such answer is on file (the first payer's 835 never reached the
    store). What a second payer took back is the patient's to pay only as far as the first payer left it on the patient."""
    f = fx["item"]
    rows = fx["r835"]["rows"]
    me = next((x for x in rows if x.get("line_key") and x["line_key"] == f.get("line_key")), None)
    if me is None:
        return None
    mine, svc, code = str(me.get("date") or "")[:10], str(me.get("svc") or "")[:10], me.get("code")
    who = str(me.get("payer") or "").strip().upper()
    prior = [x for x in standing_rows(rows) if x.get("line_key") and x["line_key"] != me["line_key"]
             and str(x.get("payer") or "").strip().upper() != who and x.get("code") == code
             and str(x.get("svc") or "")[:10] == svc and str(x.get("date") or "")[:10] <= mine]
    if not prior:
        return None
    x = max(prior, key=lambda r: (str(r.get("date") or ""), r.get("line_key") or ""))
    pr = [(c, a) for g, c, a in x.get("adj") or [] if g == "PR" and a]
    co = [(c, a) for g, c, a in x.get("adj") or [] if g == "CO" and a > 0]
    return {"row": x, "group": _grp_kind(x.get("payer"))[0], "pr": sum(a for _c, a in pr), "pr_codes": sorted({f"PR-{c}" for c, _a in pr}),
            "co_codes": sorted({f"CO-{c}" for c, _a in co}), "paid": int(x.get("paid") or 0)}


def patient_share(fx: dict) -> dict:
    """What a patient-group denial asks of the patient: every PR amount the payer set on the item's own line, never more
    than the charge less what any payer paid for the service (the standing payments of its other answers). {'cents', 'pr':
    [(group, code, cents)], 'charge', 'paid', 'patient_paid' (what EMA's line shows the patient paid, when the patient's
    money is not split across several lines), 'left' (cents less that: what is still to bill)}. With the item's own line
    not in the facts (a copy without it), the size the detection run gave it."""
    f = fx["item"]
    own = [x for x in fx["r835"]["rows"] if x.get("line_key") and x["line_key"] == f.get("line_key") and x["status"] != "22"]
    paid = sum(x["paid"] or 0 for x in standing_rows(fx["r835"]["rows"]) if x.get("line_key") and x["line_key"] != f.get("line_key")
               and (x["paid"] or 0) > 0)
    tot = _ema_line_totals(fx) if fx.get("ema") else None
    by_patient = max(tot["pat_paid"], 0) if tot and tot["n"] and not tot["several"] else 0
    if second_share_back(f):
        # the share the second payer paid and took back (its recoupment's cents): no PR adjustment of its own line says it
        tb = _ev(f).get("taken_back")
        cents = max(int((tb.get("cents") if isinstance(tb, dict) else None) or f.get("shortfall_cents") or 0), 0)
        return {"cents": cents, "pr": [], "charge": int(f.get("charge_cents") or 0), "paid": paid, "patient_paid": by_patient,
                "left": max(cents - by_patient, 0)}
    if not own:
        cents = max(int((_ev(f).get("patient_share") or {}).get("cents") or f.get("shortfall_cents") or 0), 0)
        return {"cents": cents, "pr": [], "charge": int(f.get("charge_cents") or 0), "paid": paid, "patient_paid": by_patient,
                "left": max(cents - by_patient, 0)}
    pr = [(g, c, a) for x in own for g, c, a in x["adj"] if g == "PR" and a]
    charge = sum(x["charge"] or 0 for x in own)
    cents = max(min(sum(a for _g, _c, a in pr), charge - paid), 0)
    # a payer that answered the service after this one (the next payer: a supplement, a Medicaid plan) set the patient's
    # part itself: never more than the patient share it left; what it took off as contractual is not the patient's to pay
    # (review r1-5). A next payer that put nothing on the patient leaves the share as it is (its contractual answer is said
    # before billing: story._bill_patient_denial_step)
    nxt = next_payer_answer(fx)
    if nxt and nxt["pr"] > 0:
        cents = min(cents, nxt["pr"])
    return {"cents": cents, "pr": pr, "charge": charge, "paid": paid, "patient_paid": by_patient, "left": max(cents - by_patient, 0)}


def next_payer_answer(fx: dict) -> dict | None:
    """The newest standing answer of the item's service (its code and date) by another line after the item's own answer:
    the next payer (a supplement, a Medicaid plan), with what it put on the patient and on the practice. {'row', 'pr',
    'co', 'paid', 'pr_codes', 'co_codes'}, or None."""
    f = fx["item"]
    mine, svc, code = str(f.get("payment_date") or "")[:10], str(f.get("service_date") or "")[:10], f.get("procedure_code")
    later = [x for x in standing_rows(fx["r835"]["rows"]) if x.get("line_key") and x["line_key"] != f.get("line_key")
             and x.get("status") != "22" and x.get("code") == code and str(x.get("svc") or "")[:10] == svc
             and str(x.get("date") or "")[:10] > mine]
    if not later:
        return None
    x = max(later, key=lambda r: (str(r.get("date") or ""), r.get("line_key") or ""))
    pr = [(c, a) for g, c, a in x["adj"] if g == "PR" and a > 0]
    co = [(c, a) for g, c, a in x["adj"] if g == "CO" and a > 0]
    return {"row": x, "pr": sum(a for _c, a in pr), "co": sum(a for _c, a in co), "paid": int(x.get("paid") or 0),
            "pr_codes": sorted({f"PR-{c}" for c, _a in pr}), "co_codes": sorted({f"CO-{c}" for c, _a in co})}


def patient_side_settled(tot: dict, owed: int, *, adjusted: bool) -> bool:
    """EMA shows the patient's side of the line settled what is owed: one line (the patient's money not split across
    several), the patient paid on it, and either the patient paid all of it, or the line closed with the patient's payments
    and (adjusted) the adjustments EMA took off the patient's balance covering all of it. A line closed by a non-contractual
    write-off is not the patient's side, however much the patient paid: that write-off needs a person's decision (the
    owner's rule 2; review r1-6)."""
    if not tot["n"] or tot["several"] or tot["pat_paid"] <= 0 or owed <= 0:
        return False
    if tot["pat_paid"] >= owed:
        return True
    return tot["balance"] <= 0 and adjusted and tot["pat_paid"] + tot["pat_adj"] >= owed


# The owner's decision 2, its follow-ups (plans/owner_notes_2026-11-26.md section 5, built as written there).
# A Medicaid member is never billed for a missing referral (PR-288): Medicaid's payment is payment in full, and the referral
# is the practice's to fix, so the work is a retroactive referral and a corrected claim, or Done when the plan will not take
# one. The member is a Medicaid member when the item's payer group is a Medicaid plan (its kind), or the remittance names
# one: the plan's name (the 835's contract class, as fix7c reads it) or the payer's name in any spelling the name rules read
# (codes.normalize_payer, 'Medcaid' too), each line's claim filing indicator read with the name (codes.payer_group_kind).
# When neither tells the program (a company's plan with no program in its name, no plan named, and a filing indicator
# that a company's Medicaid plan uses as well as its other plans: an HMO, 'other non-federal', 'mutually defined', none),
# a person checks the member's coverage before any bill goes out: never a bill on a guess.
REFERRAL_CARC = "288"
UNTOLD_CFI = frozenset({"", "ZZ", "HM", "11"})
UNTOLD_KINDS = frozenset({"", "commercial", "other"})


def medicaid_member(fx: dict) -> str:
    """'yes' (the item's member is on a Medicaid plan), 'no', or 'unclear' (its remittance does not tell which of its
    company's plans the member is on)."""
    from .codes import normalize_payer
    f = fx["item"]
    if f.get("payer_kind") == "medicaid":
        return "yes"
    own = [x for x in own_rows(fx) if x.get("status") != "22"]
    kinds = {str(f.get("payer_kind") or "")} | {row_group(x)[1] for x in own if x.get("payer")}
    if "medicaid" in kinds:
        return "yes"
    names = [f.get("plan"), f.get("payer_name")] + [x.get("payer") for x in own]
    if any(normalize_payer(str(n))[1] == "medicaid" for n in names if n and str(n).strip()):
        return "yes"
    cfis = {str(x.get("cfi") or "").strip().upper() for x in own} or {str(f.get("claim_filing_indicator") or "").strip().upper()}
    if kinds <= UNTOLD_KINDS and not str(f.get("plan") or "").strip() and cfis <= UNTOLD_CFI:
        return "unclear"
    return "no"


def referral_member(fx: dict) -> str:
    """For a missing-referral denial (PR-288) that the owner's decision 2 would bill: medicaid_member's answer; 'no' for
    any other patient-group denial (a Medicaid member's other reasons are billed as decided)."""
    f = fx["item"]
    return medicaid_member(fx) if str(f.get("carc") or "") == REFERRAL_CARC else "no"


def next_payer_settled(fx: dict) -> dict | None:
    """A patient-group denial whose next payer (next_payer_answer: a supplement, a Medicaid plan) answered the service
    with a contractual adjustment only: nothing paid and nothing put on the patient, the service put on the practice under
    that payer's contract. Billing the patient would break that contract, so the item is done with that answer as its
    proof (the owner's decision 2, its follow-up: straight to Done with the reason shown). Its words name the member, not
    the patient, and say the other payer settled it, as Results reads a closure's basis (results._closure: "Settled by
    another payer"). The proof as paid_proof gives one ({'rule', 'cents', 'date', 'payer', 'check_tail', 'source',
    'text'}), or None."""
    nxt = next_payer_answer(fx)
    if not nxt or nxt["co"] <= 0 or nxt["pr"] > 0 or nxt["paid"] > 0:
        return None
    x = nxt["row"]
    # a contractual adjustment only (the owner's words): no other group beside it, and the amount shown is the net the
    # practice carries (a negative adjustment beside a positive one nets out), so the reason reconciles to the cent
    co = [(c, a) for g, c, a in x["adj"] if a and g == "CO"]
    net = sum(a for _c, a in co)
    if net <= 0 or any(a and g != "CO" for g, _c, a in x["adj"]):
        return None
    name, kind = row_group(x)
    name = name if name and name != "Unknown" else str(x.get("payer") or "the next payer")
    medicaid = " (a Medicaid plan: what it pays is payment in full)" if kind == "medicaid" else ""
    text = (f"{name} answered after it on {day(x.get('date'))} and put the service on the practice under its contract "
            f"({', '.join(sorted({f'CO-{c}' for c, _a in co}))}, {usd(net)}), paying nothing and leaving nothing to the member{medicaid}: "
            "settled by another payer, so the member is not billed")
    return {"rule": "next_payer_contractual", "cents": 0, "date": x.get("date"), "payer": x.get("payer"),
            "check_tail": x.get("check_tail"), "source": "835", "text": text}


def _owed(f: dict, fx: dict) -> int:
    """What the item is about, in cents: its shortfall, else its expected amount, else the line's charge less its routine
    reductions."""
    for v in (f.get("shortfall_cents"), f.get("expected_cents")):
        if v:
            return int(v)
    own = [x for x in own_rows(fx) if x.get("line_key")]
    if own:
        co = sum(a for g, c, a in own[0]["adj"] if g == "CO" and c in CONTRACTUAL_CARCS)
        return max(0, (own[0]["charge"] or 0) - co)
    return int(f.get("charge_cents") or 0)


def settled_in_ema(fx: dict) -> dict | None:
    """A denial that EMA shows settled with money on the line, while the denying payer paid nothing on its 835 and no rule
    proves who paid: {'kind': 'insurer' (insurance money: find which payer paid), 'patient_pr' (the patient paid a share
    the payer put on the patient), 'patient_co' (the patient paid what the payer put on the practice: refund or appeal),
    'ins', 'pat', 'open', 'group'}, else None. EMA's line, never its claim status (C02, H6)."""
    f = fx["item"]
    if f.get("category") not in ("denial", "bundling") or f.get("signal") in OWN_RULES or str(f.get("carc") or "") in COB_CARCS:
        return None             # a coordination-of-benefits denial is a task to find the first payer, whatever EMA shows
    tot = _ema_line_totals(fx)
    if not tot["n"] or tot["balance"] > 0:
        return None
    if (f.get("paid_cents") or 0) > 0 or any((x["paid"] or 0) > 0 for x in own_rows(fx) if x["status"] != "22"):
        return None
    group = _carc_group(fx)
    # a patient-group denial (decision 2) is the patient's to pay in full, so what EMA took off the patient's balance
    # settles it with the patient's payment; any other denial is settled by the patient's payment alone
    adjusted = patient_denial(f)
    out = {"ins": tot["ins_paid"], "pat": tot["pat_paid"], "open": tot["balance"], "group": group,
           "adj": tot["pat_adj"] if adjusted and tot["pat_adj"] > 0 else 0}
    if tot["ins_paid"] > 0:
        return dict(out, kind="insurer")
    owed = _owed(f, fx)
    if not tot["several"] and tot["pat_paid"] > 0 and (tot["pat_paid"] * 2 >= owed or (adjusted and tot["pat_paid"] + tot["pat_adj"] >= owed)):
        # the patient's payment is the patient's share when a later answer of the line put it on the patient (the payer
        # reprocessed it to the deductible), when this denial only repeats a claim already answered (a duplicate), or when
        # the payer's answer put nothing on the practice (group PR or OA, or only the patient's share and the contract
        # reduction): only a CO or PI denial the patient paid is the practice's to appeal or refund
        mine = str(f.get("payment_date") or "")[:10]
        code, svc = f.get("procedure_code"), str(f.get("service_date") or "")[:10]
        later = [x for x in standing_rows(fx["r835"]["rows"]) if x.get("line_key") and (x["date"] or "") > mine
                 and x.get("code") == code and (x.get("svc") or "")[:10] == svc and any(g == "PR" and a > 0 for g, _c, a in x["adj"])]
        if later:
            x = max(later, key=lambda r: (r["date"] or "", r.get("line_key") or ""))
            return dict(out, kind="patient_pr", why="later_pr", date=x["date"],
                        pr=", ".join(f"{g}-{c}" for g, c, a in x["adj"] if g == "PR" and a > 0))
        if str(f.get("carc") or "") in DUP_CARCS:
            return dict(out, kind="patient_pr", why="duplicate")
        own_adj = [(g, c) for x in own_rows(fx) for g, c, a in x["adj"] if a]
        if group in ("PR", "OA") or (not group and own_adj and all(g == "PR" or (g == "CO" and c in ("45", "253")) for g, c in own_adj)):
            return dict(out, kind="patient_pr", why="pr" if group in ("PR", "") else "oa")
        return dict(out, kind="patient_co")
    return None


def _pending_action(fx: dict) -> str | None:
    """EMA's current claim action on the item's line when it shows work at the payer (an appeal, a corrected claim,
    records, a reprocessing request): the newest Production report's, else the newest A/R row's."""
    acts = [ln.get("action") for ln in fx["ema"]["lines"] if ln.get("action")]
    if not acts and fx["ar"]:
        newest = max(fx["ar"], key=lambda a: a.get("as_of") or "")
        acts = [newest.get("action")] if newest.get("action") else []
    return next((a for a in acts if action_kind(a) in PENDING_ACTIONS), None)


# words a period follows without ending the sentence ("ch. 12 sec. 40.2", "e.g. a modifier")
_ABBREV = frozenset({"ch", "sec", "secs", "no", "nos", "vs", "e.g", "i.e", "approx", "p", "pp", "para", "art", "fig", "inc", "co", "corp",
                     "st", "dr", "mr", "mrs", "ms", "jr", "sr", "cf", "al", "dept", "vol", "rev", "ref", "u.s"})


def sentence_end(text: str, semicolon: bool = True) -> int | None:
    """Where a text's first sentence ends: the index of its '.' (or its first ';' when semicolon), or None when it is one
    sentence. A period after an abbreviation (ch. 12, sec. 40.2, e.g.) or before a lower-case word or a number ends nothing."""
    for m in re.finditer(r"(?<=[a-z0-9)])([.;]) ", text):
        if m.group(1) == ";":
            if semicolon:
                return m.start()
            continue
        w = re.search(r"([A-Za-z.]+)$", text[:m.start()])
        if w and w.group(1).lower().strip(".") in _ABBREV:
            continue
        nxt = text[m.end():m.end() + 1]
        if nxt and not (nxt.isupper() or nxt in "(\"'"):
            continue
        return m.start()
    return None


def _short_label(basis: Any, n: int = 120) -> str:
    """A window's basis cut to its first clause, for sentences that name it."""
    b = str(basis or "").strip()
    cut = sentence_end(b)
    b = b[:cut] if cut is not None else b.rstrip(".")
    return b if len(b) <= n else b[:n].rsplit(" ", 1)[0] + "..."


def _policy_words(key: str) -> str:
    """An owner's write-off policy key in words: 'group|||reason' is the payer's reason on every code, 'group|code|mod|reason'
    one code (and modifier)."""
    parts = (str(key or "").split("|") + ["", "", "", ""])[:4]
    grp, code, mod, reason = parts
    who = f"{grp}'s" if grp and not grp.endswith("s") else (f"{grp}'" if grp else "the payer's")
    what = f"{reason} denials" if reason else "write-offs"
    return f"{who} {what} " + (f"of {code}{('-' + mod) if mod else ''}" if code else "on every code")


def same_day_lines(conn: sqlite3.Connection, f: dict) -> list[dict]:
    """The other procedures of the item's patient on its date of service, as the 835s answered them (the item's bill, and
    the patient's other claims that day by the payer's member record; standing answers, the best allowed amount of each
    code): what a multiple-procedure reduction ranks the item's code against. The member record is only matched, never
    shown."""
    svc, code = f.get("service_date"), f.get("procedure_code")
    if not svc or not _table(conn, "claims") or not _table(conn, "lines"):
        return []
    lcols, ccols = _cols(conn, "lines"), _cols(conn, "claims")
    allowed = "l.allowed_cents" if "allowed_cents" in lcols else "NULL"
    rows = []
    b = bill_of(f.get("patient_control_number"))
    if b:
        rows += conn.execute(
            f"""SELECT c.patient_control_number, c.claim_status_code, l.procedure_code, {allowed}, l.paid_cents, l.charge_cents, 1
                FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key JOIN lines l ON l.claim_key = c.claim_key
                WHERE c.patient_control_number >= ? AND c.patient_control_number < ? AND COALESCE(l.service_date, c.statement_from) = ?
                  AND COALESCE(t.superseded, 0) = 0""", (b, b + "~", svc)).fetchall()
    if "patient_id" in ccols and f.get("claim_key") and "service_date" in lcols:
        pid = conn.execute("SELECT patient_id FROM claims WHERE claim_key = ?", (f["claim_key"],)).fetchone()
        if pid and pid[0]:
            rows += conn.execute(
                f"""SELECT c.patient_control_number, c.claim_status_code, l.procedure_code, {allowed}, l.paid_cents, l.charge_cents, 0
                    FROM lines l JOIN claims c ON c.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = c.transaction_key
                    WHERE l.service_date = ? AND c.patient_id = ? AND COALESCE(t.superseded, 0) = 0""", (svc, pid[0])).fetchall()
    best: dict[str, dict] = {}
    for pcn, st_, cd, al, paid, chg, same_bill in rows:
        if (same_bill and bill_of(pcn) != b) or str(st_) == "22" or not cd or cd == code:
            continue
        cur = best.get(cd)
        if cur is None or (al or 0) > (cur["allowed"] or 0):
            best[cd] = {"code": cd, "allowed": al, "paid": paid, "charge": chg}
    return sorted(best.values(), key=lambda d: -(d["allowed"] or 0))


def short_check(conn: sqlite3.Connection | None, fx: dict) -> dict | None:
    """Why a short payment may be right: a higher-valued procedure the same day (the payer pays the second procedure at a
    reduced rate) or a surgery-center day (the payer pays its lower facility rate for the professional service), or why
    it is not (the day's highest-valued procedure was cut as if it were a second one). {'kind': 'reduced' | 'top_cut',
    'text'} or None. Memoized on the facts; the same-day lines are looked up only where a cut of about half, or a facility
    day, makes a reduction possible."""
    if "_short_check" in fx:
        return fx["_short_check"]
    f = fx["item"]
    out = None
    if f.get("category") in SHORT and (f.get("expected_cents") or 0) > 0:
        exp, allowed = f.get("expected_cents") or 0, f.get("allowed_cents")
        ratio = (allowed / exp) if allowed is not None and exp else None
        tot = _ema_line_totals(fx)
        facility = tot["several"] or bool(fx["ema"]["twins"]) or (f.get("pos_class") or "") == "facility"
        visit = str(f.get("procedure_code") or "").startswith("99")
        half = ratio is not None and 0.4 <= ratio <= 0.6
        if visit:
            # a visit is not reduced as a second procedure; on a surgery-center day the payer pays its lower facility rate
            if facility:
                out = {"kind": "reduced", "text": (f"this looks like a surgery-center day (EMA ties this patient, date and code to a second "
                                                   f"claim, or the place of service is a facility), where {f.get('payer_group') or 'the payer'} "
                                                   "pays its lower facility rate, so the difference may be smaller than shown")}
        elif conn is not None and (facility or half):
            others = same_day_lines(conn, f)
            higher = [d for d in others if (d["allowed"] or 0) > exp]
            if higher and (facility or half):
                h = higher[0]
                text = (f"{h['code']} was billed the same day and is valued higher (allowed {usd(h['allowed'])}), so the payer pays "
                        f"{f.get('procedure_code')} as a second procedure, usually at half its rate")
                if facility:
                    text += ("; and this looks like a surgery-center day (EMA ties this patient, date and code to a second claim, or "
                             "the place of service is a facility), where the payer pays its lower facility rate")
                out = {"kind": "reduced", "text": text}
            elif others and half and all((d["allowed"] or 0) < exp for d in others):
                low = ", ".join(f"{d['code']} (allowed {usd(d['allowed'])})" for d in others[:3])
                out = {"kind": "top_cut", "text": (f"{f.get('procedure_code')} was the highest-valued procedure of the day (the others: {low}) "
                                                   "and was still paid at about half: only the lower-valued procedures take that reduction")}
    fx["_short_check"] = out
    return out


def timely_proof(f: dict, fx: dict) -> dict:
    """For a timely-filing denial: the first day the payer received any claim of this bill (the 835's received dates), the
    first submission EMA's claim-status export shows, and the first-filing limit from the date of service."""
    from .codes import filing_rule, legal_deadline, rule_facts, window_end
    recv = sorted(x["received"] for x in fx["r835"]["rows"] if x.get("received"))
    sub = sorted(x["submitted"] for x in fx["exports"] if x.get("submitted"))
    r = filing_rule(f.get("payer_group"), f.get("payer_kind"), **rule_facts(f))
    svc = f.get("service_date")
    limit = _later(window_end(svc, r), legal_deadline(svc, r)) if svc else None
    first = min([d for d in (recv[:1] + sub[:1]) if d] or [None]) if (recv or sub) else None
    return {"received": recv[0] if recv else None, "submitted": sub[0] if sub else None, "limit": limit, "label": r["label"],
            "in_time": bool(first and limit and first <= limit)}


def no_appeal(f: dict) -> bool:
    """The item's payer takes no appeal of an ordinary claim denial (Kentucky Medicaid, 907 KAR 1:671): it is worked by a
    corrected or new claim inside the filing limit, never an appeal. A takeback is a determination the payer does hear."""
    return bool(no_appeal_step(f))


def no_appeal_step(f: dict) -> str | None:
    """For an item whose payer takes no appeal of an ordinary claim denial, the words of the work it takes instead ('send a
    corrected or new claim within 12 months of the date of service (907 KAR 1:673); Kentucky Medicaid takes no appeal ...'),
    from the payer's rule; None for every other item. The next step says this wherever it would say 'appeal' (deadlines
    review r2-2)."""
    from .codes import appeal_rule, rule_facts
    if f.get("signal") == "TAKEBACK":
        return None
    r = appeal_rule(f.get("payer_group"), f.get("payer_kind"), **rule_facts(f))
    return (r.get("no_appeal_step") or "send a corrected or new claim inside the filing limit; this payer takes no appeal of an "
            "ordinary claim denial") if r.get("no_appeal") else None


def route_of(fx: dict, st: dict, conn: sqlite3.Connection | None = None) -> str:
    """The kind of work the item takes, from its state and facts: appeal, corrected, call, post, bill_patient,
    bill_secondary, refund, contract, policy, fees, eob_first, cob, rate, repricing, decide, wait, nothing. The one place it is
    decided: the story's next step follows it, and a work-now item whose route is not an appeal carries an appeal_block
    (ROUTE_BLOCKS) so a bulk appeal never takes it."""
    f = fx["item"]
    state = st.get("work_state")
    sig, cat = f.get("signal") or "", f.get("category") or ""
    rule = st.get("rule") or ""
    if state in ("done", "accepted", "expired"):
        return "nothing"
    if state == "waiting":
        return "wait"
    if state == "cant_check":
        return "call"
    if rule == "credit_unposted_takeback":
        return "post"
    if rule in ("credit_refund", "possible_overpayment") or sig == "CREDIT_BALANCE" or cat == "refund":
        return "refund"
    if rule in ("bill_patient", "bill_patient_denial", "patient_unpaid"):
        return "bill_patient"
    if sig == "SECONDARY_NOT_BILLED" and rule in ("open", "open_late", "stored"):
        return "bill_secondary"         # a stored state on the dashboard's copy keeps the signal's own work
    if rule in ("work_answered", "work_stale", "follow_up_due", "forwarded_late", "person_billed_paid", "stored_person",
                "payment_never_arrived", "second_role_short"):
        return "call"
    if rule in ("check_first", "settled_in_ema", "patient_paid_co", "decided_no_appeal", "note_unread"):
        return "decide"
    if rule == "post_payment" or sig == "REMIT_NOT_POSTED":
        return "post"
    if sig == "EMA_DENIAL_AS_CONTRACTUAL":
        return "policy"
    if sig in BATCH:
        return "repricing"
    if sig in CONTRACT:
        return "contract"
    if sig in FEES:
        return "fees"
    if sig in ("NO_ANSWER", "DENIED_DUPLICATE_UNPAID"):
        return "call"
    if sig in ("REJECTED_PRE_PAYER", "NOT_SENT"):
        return "corrected"
    if sig == "EMA_DENIED":
        return "eob_first"
    carc = str(f.get("carc") or "")
    if carc in COB_CARCS:
        return "cob"
    o = fx.get("outcome") or {}
    if o.get("outcome") in ("paid_before", "lost_to_timely_filing"):
        return "decide"
    if str(f.get("hint") or "").startswith(CHECK_HINTS):
        return "decide"
    late = st.get("late_window") or {}
    auth = sig == "DENIED_AUTH" or carc in AUTH_CARCS
    if late.get("process") == "reopening":
        return "decide"                 # only a clerical error can be reopened: the step asks which it was
    if late.get("process") == "corrected" and not auth:
        return "corrected"
    if late.get("process") == "appeal" and f.get("actionable") == "correct_and_resubmit" and not no_appeal(f):
        return "appeal"                 # the corrected-claim limit passed; the payer's dispute window is still open
    if cat in SHORT:
        chk = short_check(conn, fx)
        if chk and chk["kind"] == "reduced":
            return "decide"
    if sig == "DENIED_TIMELY_FILING" or carc == "29":
        return "decide" if not timely_proof(f, fx)["in_time"] else ("corrected" if no_appeal(f) else "appeal")
    if auth:
        return "decide"                 # find the authorization first (amendment 8.2: these are 0-1% ever paid on a blind appeal)
    if sig in ("DENIED_NONCOVERED", "DENIED_OTHER", "CODE_CHANGED", "CLAIM_DENIED_UNPAID", "DENIED_MISSING_PRIMARY", "DENIED_PROVIDER"):
        return "decide"
    if f.get("actionable") == "correct_and_resubmit" or sig == "DENIED_MISSING_MODIFIER":
        return "corrected"
    if sig in ("UNDERPAID_VS_OWN_RATE", "MPPR_EXCESS", "SECOND_RATE") or (cat in SHORT and not sig.startswith("DOWNCODE")):
        return "rate"
    return "corrected" if no_appeal(f) else "appeal"


def decide(fx: dict, today: str, conn: sqlite3.Connection | None = None) -> dict:
    """The item's state (_decide) with the route its next step takes, and, for a work-now item whose route is not an
    appeal, the appeal_block that keeps it out of a bulk appeal."""
    res = _decide(fx, today)
    if res.get("work_state") == "work_now":
        route = route_of(fx, res, conn)
        res["route"] = route
        if res.get("appeal_block") is None and route in ROUTE_BLOCKS and not res.get("person"):
            res["appeal_block"] = ROUTE_BLOCKS[route]
    else:
        res["route"] = route_of(fx, res, conn)
    return res


def _decide(fx: dict, today: str) -> dict:
    f = fx["item"]
    ctx = fx["ctx"]
    sig, cat, st = f.get("signal") or "", f.get("category") or "", f.get("status") or "open"
    grp = f.get("payer_group")
    ev = _evidence(fx)
    res: dict = {"work_state": None, "follow_up_on": None, "state_basis": None, "evidence": [], "rule": None, "person": False,
                 "proof": None, "deadline": None, "deadline_basis": None, "work": None, "answered": None, "appeal_block": None,
                 "credit_cents": 0, "closed_on": None, "closed_basis": None, "last_window": None, "late_window": None,
                 "route": None, "takeback": None, "settled": None}
    deadline, dbasis = _deadline(f)
    res["deadline"], res["deadline_basis"] = deadline, dbasis
    wins = windows(f, deadline, dbasis, fx)
    lastw_ = last_window(f, deadline, dbasis, wins)
    lastw = lastw_["date"] if lastw_ else None
    res["last_window"] = lastw_
    legal = str(f.get("legal_deadline") or "")[:10]
    res["legal_deadline"] = legal if legal and (not deadline or legal > str(deadline)[:10]) else None
    own_last = _later(deadline, res["legal_deadline"])
    if own_last and today > own_last and lastw and lastw >= today and not lastw_["own"]:
        # past the item's own deadline and the payer's own limit, inside a later window: which one, and what it is for
        res["late_window"] = next((w for w in wins if w["date"] >= today and not w["own"]), lastw_)
    past_all = bool(lastw) and today > (_add(lastw, EXPIRE_GRACE_DAYS) or lastw)

    def put(state: str, rule: str, basis: str, rows: list | None = None, follow: str | None = None) -> dict:
        res.update(work_state=state, rule=rule, state_basis=basis[:300], follow_up_on=follow)
        res["evidence"] = list(rows or [])
        return res

    def expired(lead: str | None = None, rows: list | None = None) -> dict:
        res["closed_on"] = lastw
        res["closed_basis"] = (lastw_ or {}).get("basis") or dbasis
        what = _short_label(res["closed_basis"])
        return put("expired", "expired", (lead.rstrip(".") + ". " if lead else "") + f"The last window closed on {day(lastw)}"
                   + (f" ({what})" if what else "") + ", with no work pending", rows)
    proof = paid_proof(fx) if (cat in MONEY or sig in EMA_ONLY) and sig not in OWN_RULES else None
    credit = credit_of(fx) if cat in MONEY and sig not in OWN_RULES else 0
    res["proof"], res["credit_cents"] = proof, credit
    reproc = any(x["status"] == "22" and (x["date"] or "") > (f.get("payment_date") or "") for x in fx["r835"]["rows"])
    hint = _archive_hint(f)
    # ------------------------------------------------------------------ a person's status wins
    events = fx["rw"]["events"]
    manual = [e for e in events if e["kind"] == "manual"]
    last_manual = manual[-1] if manual else None
    who = (last_manual or {}).get("actor") or "a person"
    when = (last_manual or {}).get("at") or f.get("status_changed_at")
    conflict = f" {proof['text']}: mark it Paid." if proof and proof.get("rule") not in ("moved_to_patient_open",) else ""
    if st == "resolved":
        res["person"] = not f.get("auto_note")
        if f.get("auto_note"):
            return put("done", "auto_closed", str(f["auto_note"]), [{"source": "remitwatch", "kind": "auto_note", "text": f["auto_note"]}])
        rec = f" (recovered {usd(f['recovered_cents'])})" if f.get("recovered_cents") else ""
        done_note = str((last_manual or {}).get("note") or "") if (last_manual or {}).get("to") == "resolved" else ""
        if done_note.startswith(DONE_NOTE) and not f.get("recovered_cents"):
            # a person's Done (the owner's decision 2): done by that person, nothing recovered, never a payment
            rest = done_note[len(DONE_NOTE):].lstrip(" :").strip()
            text = f"Marked done by {who} on {day(when)}" + (f": {rest}" if rest else "")
            return put("done", "person_done", text, [{"source": "remitwatch", "kind": "status", "text": text}])
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
        sent = [e for e in ev if e["kind"] in ("sent", "fax", "billed") and e["source"] == "remitwatch"]
        anchor = max((e["date"] for e in sent if e.get("date")), default=None) or _pday(f.get("appealed_at") or f.get("status_changed_at"))
        if sig == "SECONDARY_NOT_BILLED":
            return _billed_second(res, fx, ev, anchor, who, today, put)
        newest = _newest_work(sent)
        if newest is not None and newest.get("patient") and (patient_denial(f) or second_share_back(f)):
            return _billed_patient(res, fx, newest, who, today, put)
        work = "sent_claim" if (f.get("actionable") == "correct_and_resubmit") else "sent"
        return _follow(res, fx, ev, anchor, work, f"Marked Sent by {who} on {day(anchor)}", today, deadline, confirmed=True,
                       conflict=conflict, person=True, put=put)
    # ------------------------------------------------------------------ a person reopened what RemitWatch closed on its own
    # (needs_B 0a.2): the person's 'open' wins over the payment that closed it (detection passes it as rejected_payment,
    # or it is the same proof again); the item stays work now with that payment named, never done and never kept out as
    # paid. A payment that came after the reopen closes it as usual.
    o_ = fx.get("outcome") or {}
    rejected = o_.get("rejected_payment") if o_.get("person_reopened") else None
    reopen_at = _pday((last_manual or {}).get("at"))           # the practice's day: a payment dated the next day is newer
    reopen_moment = _moment((last_manual or {}).get("at"))
    auto_reopened = (st == "open" and last_manual is not None and last_manual.get("to") == "open"
                     and last_manual.get("from") in ("resolved", "dismissed", "lost"))
    # a payment that came after the person's reopen is new money: the usual rules decide. An 835 counts by when its file
    # was read, never by the payment date it carries: a remittance read on Nov 24 for an EFT dated Nov 25 was on file when
    # the person reopened that evening, and is the payment they rejected. EMA dates a posting the day it is made, so a
    # posting dated after the reopen's day was made after it.
    newer = bool(reopen_at) and (any((x["paid"] or 0) > 0 and _came_after(x.get("read"), x["date"], reopen_moment, reopen_at)
                                     for x in standing_rows(fx["r835"]["rows"]) if x.get("line_key") and x.get("code") == f.get("procedure_code"))
                                 or any((p["cents"] or 0) > 0 and (p["posted"] or "") > reopen_at for p in fx["ema"]["pays"]))
    if (rejected or (auto_reopened and proof)) and not newer:
        res["person"] = True
        r_ = rejected if isinstance(rejected, dict) else {}
        what = (f"EMA shows {usd(r_.get('cents'))} by {r_.get('payer') or 'a payer'} on {day(r_.get('date'))}" if r_ else
                (proof or {}).get("text") or "a payment RemitWatch read as this line's")
        res["proof"], res["appeal_block"] = None, None
        return put("work_now", "person_reopened", f"Reopened by {who} on {day(reopen_at or when)} after RemitWatch closed it: {what}; "
                   "the person said it does not pay this line, so it is worked as open", [])
    # ------------------------------------------------------------------ no longer listed (a person's status above wins)
    if f.get("stale"):
        return put("done", "stale", "No longer listed: the rules that raised it no longer find it on the newest data; its notes are kept")
    # ------------------------------------------------------------------ automatic states
    if sig in INFO_ONLY:
        return put("accepted", "info_only", "For information: nothing to recover")
    if sig == "EMA_DENIAL_AS_CONTRACTUAL":
        pol, words = _c7_policy(f, ctx)
        if pol:
            return put("accepted", "policy", f"The owner's write-off policy accepts {words}" + (
                f" (set by {pol.get('by')} on {day(pol.get('at'))})" if pol.get("by") else ""))
    if sig in CONTRACT:
        res["appeal_block"] = "owner"
        return put("work_now", "owner_decision", "A contract or billing-policy question for the owner, not a claim to appeal")
    if sig in BATCH:
        if past_all:
            return expired()
        res["appeal_block"] = "batch"
        return put("work_now", "repricing", f"{grp or 'The payer'} priced the services listed on its earlier fee schedule after its new "
                   "rates took effect: one repricing request covers every claim still inside its window")
    if sig in FEES:
        tb = _ev(f).get("taken_back") if sig == "TAKEBACK" else None
        if isinstance(tb, dict) and tb.get("date"):
            # a service the recoupment left unpaid (detect._taken_back_items): its own payment came back to the payer. Paid
            # again since on a check no 835 on file carries (EMA posted it after the takeback, and its postings of the line
            # still hold the money): done
            pays = fx["ema"]["pays"]
            again = [p for p in pays if (p["cents"] or 0) > 0 and (p["posted"] or "") > str(tb["date"]) and not p.get("in_835")]
            if again and sum(p["cents"] or 0 for p in pays) >= 0.95 * int(tb.get("cents") or 0):
                p0 = again[0]
                res["appeal_block"] = "paid"
                text = (f"Paid again after the takeback: EMA shows {usd(sum(p['cents'] for p in again))} from {p0['payer'] or 'insurance'} "
                        f"posted {day(p0['posted'])} (check ending {p0['check_tail'] or '?'}, no 835)")
                return put("done", "repaid_after_takeback", text, [{"source": "production", "kind": "proof", "date": p0["posted"], "text": text}])
            if _ev(f).get("second_payer"):
                # a second payer's share taken back in full (F1's money-2; routed to F5): the coinsurance it had paid is the
                # patient's again unless the takeback is disputed, and no payer's window closes a patient's share (as decision
                # 2's patient-group denials, below): bill the patient, or dispute the takeback while its window is open. A
                # Medicaid plan's share is never its member's (payment in full): the work stays the takeback's own. Only as
                # far as the first payer's standing answer left it on the patient: a first payer that denied the service
                # (a contractual reason) or whose answer is not on file leaves the takeback's own work, never a bill on a guess
                owed = int(tb.get("cents") or 0)
                paid = (f"{grp or 'The second payer'} paid the patient's share as second payer ({usd(owed)}), then took "
                        f"it back on {day(tb['date'])}")
                if f.get("payer_kind") != "medicaid":
                    first = first_payer_answer(fx)
                    if first is not None and first["pr"] >= owed > 0:
                        res["appeal_block"] = "bill"
                        return put("work_now", "bill_patient", paid + (f": bill the patient (the window to dispute the takeback closed on "
                                                                         f"{day(lastw)})" if past_all else ": bill the patient, or dispute the takeback"))
                    lead = f"{grp or 'The second payer'} paid {usd(owed)} as second payer, then took it back on {day(tb['date'])}"
                    if first is None:
                        why = "; the first payer's answer is not on file, so whether the patient owes it cannot be checked"
                    else:
                        said = ", ".join(first["pr_codes"]) or (f"it answered {', '.join(first['co_codes'])}" if first["co_codes"] else "")
                        why = f"; the first payer ({first['group']}) left {usd(first['pr'])} on the patient" + (f" ({said})" if said else "")
                    if past_all:
                        return expired(lead + why)
                    res["appeal_block"] = "fees"
                    return put("work_now", "fees", lead + why + ": find out why, then dispute or bill the right payer")
                if not past_all:
                    res["appeal_block"] = "fees"
                    return put("work_now", "fees", paid + ": a Medicaid member is not billed for it; find out why, then dispute the takeback "
                               "or find the plan that covered the day")
        if past_all:
            return expired()
        res["appeal_block"] = "fees"
        if isinstance(tb, dict) and tb.get("date"):
            return put("work_now", "fees", f"Paid, then taken back by {grp or 'the payer'} on {day(tb['date'])}: find out why, then dispute "
                       "or bill the right payer")
        return put("work_now", "fees", "A takeback to match to the payer's overpayment letter, and to dispute if it was not owed"
                   if sig == "TAKEBACK" else "A payment fee the payer's vendor took: ask for standard EFT with no fee")
    if sig == "REMIT_NOT_POSTED":
        if _ev(f).get("informational"):
            # an informational remittance (BPR04 'NON' or a $0 deposit) reported it paid and no money came with it or after
            # it (C54): money to ask the payer for, never a posting
            res["appeal_block"] = "call"
            return put("work_now", "payment_never_arrived", "The payer's informational remittance reported this paid, but no payment "
                       f"came with it or after it: ask {f.get('payer_group') or 'the payer'} for it")
        res["appeal_block"] = "post"
        return put("work_now", "post_payment", "The 835 paid this and EMA never posted it")
    if sig == "CREDIT_BALANCE":
        e = _ev(f)
        res["appeal_block"] = "credit"
        whole = int(e.get("credit_cents") or f.get("shortfall_cents") or 0)
        left, tb = _credit_left(fx, whole)
        if tb["cents"] and not left:
            res["takeback"], res["credit_cents"] = tb, 0
            return put("work_now", "credit_unposted_takeback", f"EMA shows a credit of {usd(whole)} on this claim only because it never "
                       f"posted {_takeback_words(tb)}: post the takeback; nothing is owed back")
        res["credit_cents"] = left
        payer = e.get("overpaid_by")
        due = f" Medicare expects it back by {day(deadline)}." if deadline and f.get("payer_kind") == "medicare" else ""
        part = f" {_takeback_words(tb)} of it is a takeback EMA never posted; the rest was overpaid." if tb["cents"] else ""
        return put("work_now", "credit_refund", f"EMA shows a credit of {usd(whole)} on this claim: "
                   + (f"{payer} paid more than it was owed" if payer else "a payer or the patient paid more than the claim was owed")
                   + f"; decide refund or recoupment (owner).{part}{due}".rstrip())
    if sig == "SECONDARY_NOT_BILLED":
        got = _second_payer_paid(fx)
        if got:
            res["proof"] = got
            return put("done", got["rule"], got["text"], [{"source": got["source"], "kind": "proof", "date": got.get("date"), "text": got["text"]}])
    # the payer that denied it as primary paid as secondary less than it owes for the service in that role (the owner's
    # decision 3 of 2026-11-26): what is left stays listed, that answer as its proof, until the window to dispute the
    # answer closes. The detection run works it out again each time, so a later payment of the rest closes it there
    sr = _ev(f).get("second_role")
    if isinstance(sr, dict) and int(sr.get("left_cents") or 0) > 0 and cat in MONEY:
        return _second_role_short(res, f, sr, today, put)
    # the archive rules found the payer paid this line twice: a possible overpayment to settle, never more to ask for (C05)
    if hint and (hint.get("reclass") == "overpaid" or (hint.get("possible_overpayment_cents") or 0) > 0):
        res["appeal_block"] = "credit"
        res["credit_cents"] = int(hint.get("possible_overpayment_cents") or 0)
        return put("work_now", "possible_overpayment", _hint_text(hint) or "Paid twice by this payer: a possible overpayment")
    if proof and not credit and proof["rule"] != "moved_to_patient_open":
        res["appeal_block"] = "paid"
        # the payments themselves when the proof lists them (they add up to its amount), else the proof's one line
        rows = list(proof.get("rows") or []) or [{"source": proof["source"], "kind": "proof", "date": proof.get("date"), "text": proof["text"]}]
        return put("done", proof["rule"], proof["text"], rows)
    # the 835 archive proves this denial settled (archive_rules, proven): the same payer paid the line before this answer of
    # a copy, the primary paid it and nothing is left of the share, or the practice voided the claim. That holds whatever
    # EMA shows beside it: a credit on the line is a refund or recoupment of the claim (its own credit item), and the line
    # settled in EMA, a billing note or the payer's receipt of the void is no work left on this denial. An item a person
    # worked in RemitWatch (a note, a fax, a packet) is decided as below: it stays listed with the proof named on it.
    if hint and hint.get("strength") == "proven" and hint.get("state_hint") == "done" and hint.get("reclass") in SETTLED_BY_ARCHIVE \
            and cat in ("denial", "bundling") and not (f.get("paid_cents") or 0) and not _worked_here(fx):
        return _archive_done(res, hint, put, credit)
    if credit:
        res["appeal_block"] = "credit"
        left, tb = _credit_left(fx, credit)
        if tb["cents"] and not left:
            res["takeback"], res["credit_cents"] = tb, 0
            return put("work_now", "credit_unposted_takeback", f"EMA shows a credit of {usd(credit)} on this line only because it never "
                       f"posted {_takeback_words(tb)}: post the takeback; nothing is owed back")
        res["credit_cents"], res["takeback"] = left, (tb if tb["cents"] else None)
        part = f" {_takeback_words(tb)} of it is a takeback EMA never posted." if tb["cents"] else ""
        return put("work_now", "credit_refund", f"EMA shows a credit of {usd(credit)} on this line: insurance paid {usd(left)} more than the "
                   f"line was owed; decide refund or recoupment (owner).{part}")
    o = fx.get("outcome") or {}
    if o.get("outcome") == "written_off_by_policy":
        pol = (ctx.get("policy") or {}).get(o.get("pattern") or "") or {}
        return put("accepted", "policy", f"The owner's write-off policy accepts {_policy_words(o.get('pattern') or '')}"
                   + (f" (set by {pol.get('by')} on {day(pol.get('at'))})" if pol.get("by") else ""))
    if reproc:
        res["appeal_block"] = "reprocessed"
    if proof and proof["rule"] == "moved_to_patient_open":
        res["appeal_block"] = "reprocessed"
        share = f" ({usd(proof.get('patient_cents'))})" if proof.get("patient_cents") else ""
        return put("work_now", "bill_patient", f"{proof['text']}: bill the patient their share{share}",
                   [{"source": "835", "kind": "proof", "text": proof["text"]}])
    fwd = _forwarded(fx)
    if fwd and cat in MONEY:
        follow = _add(fwd["date"], FORWARD_WAIT_DAYS)
        basis = f"Forwarded to {fwd['carrier']} on {day(fwd['date'])}"
        if follow and follow >= today:
            res["appeal_block"] = "waiting"
            return put("waiting", "forwarded", basis + f": expect its payment; check on {day(follow)} if none comes", follow=follow)
        if past_all:
            return expired(basis + "; nothing came from it")
        tot = _ema_line_totals(fx)
        carrier_835 = (ctx.get("group_last_835") or {}).get(_grp_kind(fwd["carrier"])[0]) if fwd.get("carrier") else None
        if not carrier_835 and not (tot["n"] and _covered(ctx, f.get("service_date"))):
            why = ("EMA's Production reports do not cover this date of service" if ctx.get("prod_as_of") else "no EMA Production report is on file")
            return put("cant_check", "cant_check_carrier", f"{basis}: no 835 from {fwd['carrier']} is on file and {why}, so no source can "
                       f"show whether it paid. Check {fwd['carrier']}'s portal or EMA by hand", follow=follow)
        return put("work_now", "forwarded_late", basis + f": nothing has come by {day(follow)}; ask {fwd['carrier']} for it", follow=follow)
    # EMA shows the line settled with money on it while the denying payer paid nothing and no rule proves who paid
    se = settled_in_ema(fx)
    if se:
        res["settled"] = se
        if se["kind"] == "patient_pr":
            paid = (f"EMA shows {usd(se['pat'])} paid by the patient and {usd(se['adj'])} taken off the patient's balance, and the line closed"
                    if se.get("adj") else f"EMA shows {usd(se['pat'])} paid by the patient and the line closed")
            text = {"later_pr": f"Settled by the patient: the payer's later answer of {day(se.get('date'))} put this service on the patient "
                                f"({se.get('pr')}), and {paid}",
                    "duplicate": f"Settled by the patient: {paid}; this denial ({_carcs_text(fx)}) repeats a claim the payer already answered",
                    "oa": f"Settled by the patient: {paid}; the payer's answer ({_carcs_text(fx)}) put nothing on the practice"}.get(
                se.get("why") or "", f"Settled by the patient: {paid}, and the payer put this service on the patient ({_carcs_text(fx)})")
            pr = {"rule": "moved_to_patient", "cents": 0, "date": se.get("date"), "payer": None, "source": "production", "text": text}
            res["proof"], res["appeal_block"] = pr, "paid"
            return put("done", "moved_to_patient", pr["text"], [{"source": "production", "kind": "proof", "text": pr["text"]}])
        if se["kind"] == "patient_co":
            res["appeal_block"] = "patient_paid"
            return put("work_now", "patient_paid_co", f"EMA shows the patient paid {usd(se['pat'])} and the line settled, but the payer's denial "
                       f"({_carcs_text(fx)}) puts this service on the practice, not the patient: appeal it, or refund the patient")
        if past_all:
            return expired(f"EMA shows this line settled: insurance paid {usd(se['ins'])}, {usd(se['open'])} open")
        res["appeal_block"] = "settled_in_ema"
        return put("work_now", "settled_in_ema", f"EMA shows this line settled: insurance paid {usd(se['ins'])}, the patient paid "
                   f"{usd(se['pat'])}, {usd(se['open'])} open: find which payer paid and whether it settles this service")
    # the work seen on the item (RemitWatch's own marks only when no person set the item back to To do since)
    reopened = max((_pday(e["at"]) for e in manual if e["to"] == "open"), default="")
    pay_day = f.get("payment_date") or ""
    # work on or after the item's remittance; EMA's work already on the first report counts whatever its date (its own
    # date is unknown), but a billing note or follow-up date first seen before the remittance is about something earlier
    work_ev = [e for e in ev if e["kind"] in WORK_KINDS and ((e.get("date") or "") >= pay_day
                                                            or (e["kind"] in ("appeal", "corrected", "records", "reprocess", "secondary",
                                                                              "call", "resubmitted") and e.get("dated_by") == "first_report"))]
    work_ev = [e for e in work_ev if not (e["source"] == "remitwatch" and reopened and (e.get("date") or "") <= reopened)]
    decided = [e for e in ev if e["kind"] == "no_appeal" and (e.get("date") or "") >= pay_day]
    if sig == "SECONDARY_NOT_BILLED":
        # only work that reached the second payer counts: the first payer answering again is not that work
        second = _ev(f).get("second_payer")
        work_ev = [e for e in work_ev if e["kind"] in ("secondary", "sent", "fax", "call", "billed", "note", "follow_up")
                   or (e["kind"] == "resubmitted" and second and _same_payer(e.get("payer_group"), second))]
    if decided and (cat in MONEY or sig in EMA_ONLY):
        d = max(decided, key=lambda e: e.get("date") or "")
        if not [e for e in work_ev if (e.get("date") or "") > (d.get("date") or "") and e["kind"] not in ("note", "follow_up")]:
            res["appeal_block"] = "decided"
            return put("work_now", "decided_no_appeal", f"EMA's billing note records {WORK_WORDS[d.get('decided') or 'no_appeal']}"
                       + (f" (first on the A/R of {day(d['date'])})" if d.get("date") else ""), [d])
    if work_ev and (cat in MONEY or sig in EMA_ONLY):
        known = [e for e in work_ev if e["kind"] not in ("note", "follow_up")]
        fus = [e for e in work_ev if e["kind"] == "follow_up"]
        ema_follow = max((e.get("follow_up") or "" for e in fus), default="") or None
        if not known:
            return _note_or_follow(res, fx, work_ev, today, put, expired, past_all)
        # work with a known date first: an undated note or action (dated only by the first report that showed it) most
        # often records the same work a payer's received date or a person's mark dates exactly
        dated = [e for e in known if e.get("dated_by") != "first_report"]
        last = max(dated or known, key=lambda e: (e.get("date") or "", e["source"]))
        work = {"appeal": "appeal", "records": "records", "reprocess": "reprocess", "corrected": "corrected", "resubmitted": "resubmitted",
                "secondary": "secondary", "call": "call", "sent": "sent", "fax": "fax", "billed": "secondary"}.get(last["kind"], "appeal")
        text = last["text"]
        if all(e.get("dated_by") == "first_report" for e in known) and past_all:
            pending = _pending_action(fx)
            if not pending:
                return expired(f"{last['text']}; nothing shows that work was done in time or is still pending", [last])
            # EMA's claim action still shows the work at the payer, and no answer to it is on file: follow it up, never
            # expire it while it may be pending
            text = f"{last['text']}; EMA's claim action still shows '{pending}' and every window has closed (the last on {day(lastw)})"
        return _follow(res, fx, ev, last.get("date"), work, text, today, deadline, confirmed=bool(last.get("confirmed")),
                       conflict="", person=False, put=put, ev_row=last, ema_follow=ema_follow)
    if hint and hint["state_hint"] == "done" and hint.get("strength") == "proven" and not credit:
        return _archive_done(res, hint, put)
    if hint and hint["state_hint"] == "waiting":
        since = max((str(r.get("date") or "")[:10] for r in hint.get("proof") or [] if r.get("date")), default="") or f.get("payment_date")
        follow = _add(since, _wait_of(fx, "claim", None)) if since else None
        basis = _hint_text(hint) or "The payer reversed its answer and has not answered again"
        if follow and follow >= today:
            res["appeal_block"] = "waiting"
            return put("waiting", "archive_waiting", basis + f"; check on {day(follow)} if no new answer comes", follow=follow)
        return put("work_now", "follow_up_due", basis + f"; no new answer by {day(follow)}: ask the payer where it stands", follow=follow)
    # a patient-group denial (the owner's decision 2 of 2026-11-26): the payer put the service on the patient, so the work is
    # billing the patient, which no payer's window closes (as a payer's reprocessing to the patient's share, above)
    if patient_denial(f):
        # decision 2's follow-ups (fx-people): a next payer that put the service on the practice settles it (done, its
        # answer the proof); a Medicaid member's missing referral is fixed with the plan, never billed (below)
        settled = next_payer_settled(fx)
        if settled:
            res["proof"] = settled
            return put("done", settled["rule"], settled["text"], [{"source": "835", "kind": "proof", "date": settled["date"],
                                                                   "text": settled["text"]}])
        share = patient_share(fx)
        if share["cents"] > 0:
            res["patient_share"] = share
            if share["patient_paid"] >= share["cents"]:
                text = f"Paid by the patient: EMA shows {usd(share['patient_paid'])} paid by the patient"
                res["proof"] = {"rule": "patient_paid", "cents": 0, "date": None, "payer": None, "source": "production", "text": text}
                res["appeal_block"] = "paid"
                return put("done", "patient_paid", text, [{"source": "production", "kind": "proof", "text": text}])
            res["appeal_block"] = "bill"
            words = ", ".join(f"{g}-{c}" for g, c, _a in share["pr"]) or f"PR-{f.get('carc')}"
            member = referral_member(fx)
            if member == "yes":
                # never billed: the work is a corrected claim with a retroactive referral, which the plan's windows close
                lead = (f"{f.get('payer_group') or 'The payer'} put this service on the patient ({words}: no referral on file), but "
                        "the member is on a Medicaid plan, whose payment is payment in full: never a bill to the patient")
                if past_all:
                    return expired(lead)
                return put("work_now", "bill_patient_denial", lead + "; get a retroactive referral and send a corrected claim")
            if not _ema_line_totals(fx)["n"] and not _covered(ctx, f.get("service_date")):
                # RemitWatch sees the patient's side of a service only on EMA's line for it. With no line and no Production
                # report covering the date of service it cannot tell whether the patient paid, was billed or had the share
                # written off: a can't-check with its dollars (the owner's rule 3), never a bill on a guess. A person checks
                # the patient's account in EMA, then presses Billed the patient or Done. Its age is not decided here: the
                # payer's windows never close a patient's share, and an age limit is the owner's call
                why = ("EMA's Production reports do not cover this date of service" if ctx.get("prod_as_of") else
                       "no EMA Production report is on file")
                return put("cant_check", "cant_check_patient", f"{f.get('payer_group') or 'The payer'} put this service on the patient "
                           f"({words}, {usd(share['cents'])}), but {why}, so RemitWatch cannot see whether the patient paid it, was "
                           "billed or had it written off: check the patient's account in EMA before billing")
            ask = (f"the {usd(share['left'])} left (EMA shows {usd(share['patient_paid'])} of {usd(share['cents'])} paid by the patient)"
                   if share["patient_paid"] > 0 else usd(share["cents"]))
            if member == "unclear":
                return put("work_now", "bill_patient_denial", f"{f.get('payer_group') or 'The payer'} put this service on the patient "
                           f"({words}: no referral on file), and its remittance does not say whether the member's plan is a "
                           f"Medicaid plan: check the member's coverage before any bill goes out (a Medicaid member is never "
                           f"billed for it; any other member is billed {ask})")
            return put("work_now", "bill_patient_denial", f"{f.get('payer_group') or 'The payer'} put this service on the patient "
                       f"({words}): bill the patient {ask}")
    # past every window: nothing can be recovered (no grace in the work queue)
    if past_all and (cat in MONEY or sig in EMA_ONLY):
        return expired()
    # the archive rules found a payment the archive cannot prove pays this service: check it before anything else
    # (orchestrator decision 4: a 'likely' done never closes an item, and it is never appealed in bulk)
    if hint and ((hint["state_hint"] == "done" and hint.get("strength") != "proven") or hint.get("reclass") == "secondary_paid"):
        res["appeal_block"] = "likely_paid"
        return put("work_now", "check_first", _hint_text(hint) or "The 835 archive shows it was probably paid already")
    # no source can show the answer
    cc = _cant_check(fx, today)
    if cc:
        return put("cant_check", cc[0], cc[1])
    late = res.get("late_window")
    if late:
        return put("work_now", "open_late", f"Past its deadline of {day(own_last)}, but {late['what']} is open until {day(late['date'])} "
                   f"({_short_label(late['basis'])}): " + _lower(_open_basis(fx, late=True)))
    return put("work_now", "open", _open_basis(fx))


def _note_or_follow(res: dict, fx: dict, work_ev: list, today: str, put, expired, past_all: bool) -> dict:
    """An item whose only work is a billing note RemitWatch cannot read (an A/R reader that does not classify notes) or
    a follow-up date a person set in EMA: never 'nobody has worked it'. A follow-up date still ahead holds the item until
    that day; one that passed is a follow-up due; a note alone is read first, and never appealed in bulk."""
    notes = [e for e in work_ev if e["kind"] == "note"]
    fus = [e for e in work_ev if e["kind"] == "follow_up"]
    note = max(notes, key=lambda e: e.get("date") or "") if notes else None
    fu = max(fus, key=lambda e: (e.get("follow_up") or "", e.get("date") or "")) if fus else None
    bits = []
    if note:
        bits.append(note["text"])
    if fu:
        bits.append(f"a follow-up date of {day(fu['follow_up'])} set in EMA" if note else f"EMA has a follow-up date of {day(fu['follow_up'])} on it")
    lead = " and ".join(bits)
    rows = [e for e in (note, fu) if e]
    anchor = max((e.get("date") or "" for e in rows), default="") or None
    answers = _payer_answers_after(fx, anchor) if anchor else []
    if answers:
        a = answers[-1]
        res["answered"] = a
        res["appeal_block"] = "answered"
        what = (f"paid {usd(a['paid'])}" if a["paid"] > 0 else ("reversed its answer" if a.get("status") == "22" else
                                                                 ("answered " + (", ".join(a["codes"]) if a["codes"] else "with $0.00"))))
        return put("work_now", "work_answered", f"{lead}; the payer answered on {day(a['date'])} ({what}): read the note, then work its answer",
                   rows + [{"source": a["source"], "kind": "answer", "date": a["date"], "text": f"answer of {day(a['date'])}: {what}"}])
    if fu and fu["follow_up"] >= today:
        res["appeal_block"] = "waiting"
        res["work"] = fu
        return put("waiting", "ema_follow_up", f"{lead}: a person is working it in EMA; it waits until that day", rows, follow=fu["follow_up"])
    if past_all:
        return expired(lead, rows)
    res["work"] = fu or note
    if fu:
        res["appeal_block"] = "note" if note else "follow_up"
        return put("work_now", "follow_up_due", f"{lead}; that day passed with no answer from the payer on file: read the claim's notes in "
                   "EMA, then follow up", rows, follow=fu["follow_up"])
    res["appeal_block"] = "note"
    return put("work_now", "note_unread", f"{lead}: read it before working (RemitWatch cannot read what it says)", rows)


def _billed_patient(res: dict, fx: dict, billed: dict, who: str, today: str, put) -> dict:
    """A patient-group denial a person marked "Billed the patient" (the owner's decision 2): it waits for the patient's
    payment in EMA and is done when EMA shows it (patient_side_settled: the patient paid what was billed, or the line closed
    on the patient's side, never by a write-off); with it not paid by PATIENT_WAIT_DAYS after the billing it is work again
    (send another statement, or mark it Done). The person's status stands throughout."""
    f = fx["item"]
    anchor = billed.get("date")
    share = patient_share(fx)
    tot = _ema_line_totals(fx)
    lead = billed.get("text") or f"Patient billed by {who} on {day(anchor)}"
    rows = [billed]
    paid = tot["pat_paid"] if tot["n"] else 0
    res["patient_share"] = share
    if patient_side_settled(tot, share["cents"], adjusted=True):
        closed = (f" and {usd(tot['pat_adj'])} taken off the patient's balance, and the line closed" if tot["pat_adj"] > 0 else " and the line closed")
        text = f"Paid by the patient: EMA shows {usd(paid)} paid by the patient" + ("" if tot["balance"] > 0 else closed)
        res["proof"] = {"rule": "patient_paid", "cents": 0, "date": None, "payer": None, "source": "production", "text": text}
        res["appeal_block"] = "paid"
        return put("done", "patient_paid", f"{text} ({_lower(lead)})", rows + [{"source": "production", "kind": "proof", "text": text}])
    follow = _add(anchor, PATIENT_WAIT_DAYS) if anchor else None
    res["work"] = billed
    if tot["n"] and tot["balance"] <= 0 and (tot["noncontractual"] or 0) > 0 and paid < share["cents"]:
        # EMA closed the line by writing the rest off as non-contractual: a write-off with no owner (the owner's rule 2), on
        # Work now at what is left, never closed as paid by the patient (review r1-6)
        res["appeal_block"] = "bill"
        left = max(share["cents"] - paid, 0)
        return put("work_now", "patient_unpaid", f"{lead}; EMA shows {usd(tot['noncontractual'])} written off on this line with no decision "
                   f"recorded" + (f" and {usd(paid)} paid by the patient" if paid > 0 else "") + f": the {usd(left)} left is a write-off "
                   "without an owner. Reverse it and send the patient a statement, or mark it Done with the reason", rows, follow=follow)
    so_far = (f"; EMA shows {usd(paid)} of {usd(share['cents'])} paid so far" if paid > 0 else
              "; EMA shows no line for this service, so the patient's payment cannot be seen here" if not tot["n"] else "")
    if follow and follow >= today:
        res["appeal_block"] = "waiting"
        return put("waiting", "billed_patient", f"{lead}; it waits for the patient's payment in EMA, checked again on {day(follow)}{so_far}",
                   rows, follow=follow)
    res["appeal_block"] = "bill"
    if not tot["n"]:
        # the patient's payment cannot be seen here: never "nothing paid", and never another statement on a guess
        return put("work_now", "patient_unpaid", f"{lead}; no payment from the patient can be seen by {day(follow)}{so_far}: check the "
                   "patient's account in EMA, then send another statement or mark it Done", rows, follow=follow)
    short = "not all of it paid" if paid > 0 else "nothing paid by the patient"
    return put("work_now", "patient_unpaid", f"{lead}; {short} by {day(follow)}{so_far}: send another statement, or mark it Done",
               rows, follow=follow)


def _second_role_short(res: dict, f: dict, sr: dict, today: str, put) -> dict:
    """What the payer still owes as secondary (detect.py's settled-as-secondary closure or archive rule 3 measured it):
    work now, to ask the payer for the rest, until its window to dispute that answer closes (counted from the answer)."""
    from .codes import appeal_rule, legal_deadline
    text = str(sr.get("text") or "The payer paid as secondary less than it owes for the service in that role")
    row = {"source": "835", "kind": "proof", "date": sr.get("date"), "text": text}
    ar_ = appeal_rule(f.get("payer_group"), f.get("payer_kind"))
    closes = _later(_add(sr.get("date"), ar_["days"]), legal_deadline(sr.get("date"), ar_)) if sr.get("date") else None
    if closes and today > closes:
        res["closed_on"], res["closed_basis"] = closes, ar_["label"]
        return put("expired", "expired", f"{text.rstrip('.')}. The window to dispute that answer closed on {day(closes)}, with no work "
                   "pending", [row])
    res["appeal_block"] = "call"
    res["deadline"], res["deadline_basis"] = closes, f"{ar_['label']}, from the answer as secondary of {day(sr.get('date'))}"
    return put("work_now", "second_role_short", text, [row])


def _billed_second(res: dict, fx: dict, ev: list, anchor: str | None, who: str, today: str, put) -> dict:
    """A SECONDARY_NOT_BILLED item a person marked billed (the page's Mark billed sets Sent): the second payer's payment
    closes the loop (the person's status stands, the item says to mark it Paid), else it waits on the SECOND payer's
    answer, never the first payer's (H4)."""
    f = fx["item"]
    e = _ev(f)
    second = e.get("second_payer") or "the second payer"
    owed = int(f.get("shortfall_cents") or 0)
    billed = next((x for x in reversed(ev) if x["kind"] in ("billed", "sent") and x["source"] == "remitwatch"), None)
    lead = f"Billed to {second} by {who} on {day(anchor)}"
    rows = [billed] if billed else []
    got = _second_payer_paid(fx)
    if got:
        res["proof"], res["appeal_block"] = got, "paid"
        return put("work_now", "person_billed_paid", f"{lead}; {got['text']}: mark it Paid",
                   rows + [{"source": got["source"], "kind": "proof", "date": got.get("date"), "text": got["text"]}])
    res["work"] = billed or {"date": anchor, "kind": "billed", "text": lead}
    res["appeal_block"] = "waiting"
    answers = [a for a in _payer_answers_after(fx, anchor, [second], only=True)] if anchor and second else []
    if answers:
        a = answers[-1]
        res["answered"], res["appeal_block"] = a, "answered"
        what = (f"paid {usd(a['paid'])}" if a["paid"] > 0 else ("answered " + (", ".join(a["codes"]) if a["codes"] else "with $0.00")))
        return put("work_now", "work_answered", f"{lead}; {second} answered on {day(a['date'])} ({what}): work its answer",
                   rows + [{"source": a["source"], "kind": "answer", "date": a["date"], "text": f"answer of {day(a['date'])}: {what}"}])
    from .codes import normalize_payer
    wait = response_days(fx["_conn"], normalize_payer(second)[0], "secondary") if fx.get("_conn") is not None else \
        DEFAULT_RESPONSE_DAYS + CLAIM_TRANSIT_DAYS
    follow = _add(anchor, wait) if anchor else None
    share = f" ({usd(owed)})" if owed else ""
    if follow and follow >= today:
        return put("waiting", "work_waiting", f"{lead}; follow up with {second} on {day(follow)} if it has not paid{share}", rows, follow=follow)
    res["appeal_block"] = "call"
    return put("work_now", "follow_up_due", f"{lead}; nothing from {second} by {day(follow)}{share}: ask {second} whether it received "
               "the claim", rows, follow=follow)


def _follow(res: dict, fx: dict, ev: list, anchor: str | None, work: str, text: str, today: str, deadline: str | None, *,
            confirmed: bool, conflict: str, person: bool, put, ev_row: dict | None = None, ema_follow: str | None = None) -> dict:
    """Waiting on work seen on `anchor`: until the payer's answer time has passed (the note's own "allow N days" when
    it says; a later follow-up date a person set in EMA when there is one), then work now; at once when the payer
    answered since (paid: the answer to close on; anything else: the answer to work); and when the work's own date is
    unknown (it was already in the first report) and nothing came from the payer for longer than its answer time before
    that report, the follow-up is due now. Work nobody can confirm falls due at least two weeks before the deadline."""
    f = fx["item"]
    grp, kind = f.get("payer_group"), f.get("payer_kind")
    conn_free_wait = _wait_of(fx, work, ev_row)
    rows = [ev_row] if ev_row else [e for e in ev if e.get("source") == "remitwatch" and e["kind"] in ("sent", "fax")][-1:]
    res["work"] = ev_row or {"date": anchor, "kind": work, "text": text}
    res["appeal_block"] = "waiting"
    answers = _payer_answers_after(fx, anchor, [ev_row["payer_group"]] if ev_row and ev_row.get("payer_group") else []) if anchor else []
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
        # the payer answered the work already done: its answer is worked from the item's panel (a second level, the new
        # reason), never sent again as a first-level appeal in bulk
        res["appeal_block"] = "answered"
        what = (f"paid {usd(a['paid'])}" if a["paid"] > 0 else
                ("reversed its answer" if a.get("status") == "22" else
                 ("answered " + (", ".join(a["codes"]) if a["codes"] else "with $0.00"))))
        lead = (f"{text}. The payer answered the same claim again on {day(a['date'])} with the same reason ({', '.join(a['codes'])}): most "
                "likely its answer to that work, upheld" if a.get("inferred") else f"{text}; the payer answered on {day(a['date'])} ({what})")
        # the answer's own window: the newest answer to the work is a denial; a person works it from that answer's date
        if a.get("status") != "22":
            ag = a.get("group") or (_grp_kind(a["payer"])[0] if a.get("payer") else None) or grp
            ak = a.get("kind") or (_grp_kind(a["payer"])[1] if a.get("payer") else None) or kind
            from .codes import appeal_rule, corrected_rule, corrected_window, legal_deadline, rule_at, rule_facts, window_end
            facts = rule_facts(f) if _same_payer(ag, grp) else {}
            ar_ = appeal_rule(ag, ak, **facts)
            no_appeal = bool(ar_.get("no_appeal"))
            closes = None if no_appeal else _later(window_end(a["date"], ar_), legal_deadline(a["date"], ar_))
            label, what_w = rule_at(a["date"], ar_)["label"], f"the window to dispute {ag or 'the payer'}'s answer"
            # an answer a corrected claim can fix (an authorization or referral, missing information) keeps the corrected
            # claim's own clock (C14: a referral-absent answer, a Medicaid plan's 365 days from the date of service); a payer
            # that takes no appeal of a denial (Kentucky Medicaid) is answered by a corrected or new claim only
            fixable = [cc for cc in (str(x).split("-")[-1] for x in a.get("codes") or []) if cc in AUTH_CARCS or cc in ("16", "252")]
            cr = (corrected_rule(ag, ak, f.get("rarc"), None, fixable[0] if fixable else None, **facts)
                  if (fixable or no_appeal) and ak != "medicare" else None)
            if cr:
                cl = _later(*corrected_window(cr, f.get("service_date"), a["date"]))
                if cl and cl > (closes or ""):
                    closes, label, what_w = cl, cr["label"], f"the window to send {ag or 'the payer'} a corrected claim"
            ar_ = dict(ar_, label=label)
            res["answer_window"] = {"date": closes, "basis": label, "what": what_w}
            other = bool(ag) and bool(grp) and not _same_payer(ag, grp)
            lastw = _last_window(f, deadline)
            # expired when that window closed and the item's own windows have too, or the answer came from another payer (the
            # claim was rebilled to the right one: the first denial no longer matters), C60. A person's Sent keeps it.
            if not person and closes and today > closes and (other or (lastw and today > lastw)):
                res["appeal_block"] = None
                res["closed_on"], res["closed_basis"] = closes, ar_["label"]
                return put("expired", "expired", f"{lead}. {ag or 'The payer'}'s window to dispute that answer closed on {day(closes)}, with no "
                                                 "work pending", rows + [{"source": a["source"], "kind": "answer", "date": a["date"],
                                                                          "text": f"answer of {day(a['date'])}: {what}"}])
        return put("work_now", "work_answered", f"{lead}: work its answer" + (f".{conflict}" if conflict else ""),
                   rows + [{"source": a["source"], "kind": "answer", "date": a["date"], "text": f"answer of {day(a['date'])}: {what}"}])
    follow = _add(anchor, conn_free_wait) if anchor else None
    if ema_follow and (not follow or ema_follow > follow):
        follow = ema_follow             # a later follow-up date a person set in EMA
    if not confirmed and deadline:
        cap = _add(deadline, -FOLLOW_UP_MARGIN)
        if follow and cap and cap < follow and not (ema_follow and follow == ema_follow):
            follow = max(cap, anchor or cap)
    if first and not person:
        last_act = _last_payer_activity(fx, anchor)
        quiet = _days(last_act, anchor) if last_act and anchor else None
        if quiet is not None and quiet > conn_free_wait:
            res["appeal_block"] = "call"
            return put("work_now", "work_stale", f"{text}. Nothing has come from the payer since {day(last_act)}, {quiet} days before that "
                                                 "report: check where it stands before doing more", rows, follow=anchor)
    if follow and follow >= today:
        return put("waiting", "work_waiting", f"{text}; follow up on {day(follow)} if the payer has not answered" + (f".{conflict}" if conflict else ""),
                   rows, follow=follow)
    res["appeal_block"] = "call"
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


def _hint_text(hint: dict, n: int = 240) -> str:
    """The archive rule's label and sentence as one basis ("Already paid by this payer, denied again: ..."), cut at the
    last sentence end before n characters (never mid-sentence), the payer's name kept as written."""
    label, text = str(hint.get("label") or "").strip(), str(hint.get("hint") or "").strip()
    out = f"{label}: {_lower(text)}" if label and text else (label or text)
    if len(out) > n:
        cut = max(out.rfind(". ", 0, n), out.rfind("; ", 0, n))
        out = out[:cut] if cut > len(label) else out[:n].rsplit(" ", 1)[0]
    return out.rstrip(" .;")


# the words that start a sentence the states continue after a colon: only these lose their capital (a payer's name never)
_SENTENCE_WORDS = ("The", "This", "That", "A", "An", "Not", "No", "Nothing", "Paid", "Already", "Settled", "Forwarded", "Moved",
                   "Marked", "Every", "Past", "Its", "It", "EMA's")


def _lower(t: str) -> str:
    w = str(t or "").split(" ", 1)[0]
    if w in _SENTENCE_WORDS and w != "EMA's":
        return t[:1].lower() + t[1:]
    return t


def _second_payer_paid(fx: dict) -> dict | None:
    """A SECONDARY_NOT_BILLED item the second payer (or the patient) settled: EMA shows its payment after the first payer's
    remittance for at least half of what was owed."""
    f = fx["item"]
    e = _ev(f)
    owed = int(f.get("shortfall_cents") or 0)
    second = e.get("second_payer")
    since = f.get("payment_date") or ""
    if owed <= 0:
        return None
    pays = [p for p in fx["ema"]["pays"] if (p["cents"] or 0) > 0 and (p["posted"] or "") >= since
            and second and _same_payer(p.get("group") or row_group({"payer": p.get("payer")})[0], second)]
    cents = sum(p["cents"] for p in pays)
    if cents * 2 >= owed:
        p0 = pays[0]
        return {"rule": "second_payer_paid", "cents": cents, "date": p0["posted"], "payer": p0["payer"], "check_tail": p0["check_tail"],
                "source": "production", "text": f"Paid by {second}: EMA shows {usd(cents)} from it from {day(p0['posted'])} (check ending "
                                                f"{p0['check_tail'] or '?'}), against the {usd(owed)} the first payer left"}
    tot = _ema_line_totals(fx)
    if tot["n"] and tot["pat_paid"] * 2 >= owed and tot["balance"] <= 0:
        return {"rule": "moved_to_patient", "cents": 0, "date": None, "payer": None, "source": "production",
                "text": f"Settled by the patient: EMA shows {usd(tot['pat_paid'])} paid by the patient and the line closed"}
    return None


def _c7_policy(f: dict, ctx: dict) -> tuple[dict | None, str]:
    """The owner's 'accept' for a C7 item (a payer group x reason): the group-wide pattern 'group|||reason', or every top
    code's pattern 'group|code||reason' accepted."""
    pol = ctx.get("policy") or {}
    e = _ev(f)
    grp, reason = f.get("payer_group") or "", str(e.get("reason") or f.get("carc") or "")
    wide = pol.get(f"{grp}|||{reason}")
    if wide and wide.get("decision") == "accept":
        return wide, _policy_words(f"{grp}|||{reason}")
    codes = [c for c, _v in e.get("top_codes") or []]
    got = [pol.get(f"{grp}|{c}||{reason}") for c in codes]
    if codes and all(g and g.get("decision") == "accept" for g in got):
        return got[0], (f"{grp}'s" if not grp.endswith("s") else f"{grp}'") + f" {reason} denials of " + ", ".join(codes)
    return None, ""


def _carcs_text(fx: dict) -> str:
    """The item's reasons with their groups, as its own remittance gives them (CO-97, not 97)."""
    f = fx["item"]
    o = fx.get("outcome") or {}
    if o.get("carcs"):
        return ", ".join(o["carcs"])
    carc = str(f.get("carc") or "")
    g = _carc_group(fx)
    return f"{g}-{carc}" if g and carc else (f"reason {carc}" if carc else "the denial")


def _open_basis(fx: dict, late: bool = False) -> str:
    """The basis of an automatic Work now: what the item is and what EMA did with it, and the date to act by (not when
    the item is open only under a later window: that window's date leads the basis then)."""
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
    second = _ev(f).get("second_payer") or "the second payer"
    head = {"NO_ANSWER": "No answer from the payer on file", "REJECTED_PRE_PAYER": "Never reached the payer",
            "NOT_SENT": "Never left EMA", "EMA_DENIED": "Denied in EMA, no remittance on file",
            "SECONDARY_NOT_BILLED": f"Nothing from {second} on file: bill it for the share the first payer left"}.get(f.get("signal") or "",
                                                                                                                 "Not paid and nobody has worked it")
    tail_ = f"; {words[oc]}" if oc in words else ""
    part = fx.get("paid_first_partial")
    if part:
        # the first payer paid part of what Medicaid allows (paid_first_elsewhere): the rest is still Medicaid's to pay
        tail_ = (f"; {part['payer'] or 'the first payer'} paid {usd(part['paid'])} first ({day(part['date'])}"
                 + ("" if part.get("in_835") else ", no 835") + f"), under the {usd(part['worth'])} this service should bring: "
                 f"Medicaid owes up to {usd(part['owed'])} as second payer ({usd(part['open'])} open in EMA)")
    hint = _archive_hint(f)
    if hint and f.get("signal") not in CLAIM_SIGNALS and _hint_text(hint):
        # the 835 archive's reading of the item (a likely 'done' is never done without a person: check it first)
        tail_ = "; " + _hint_text(hint) + ("; check it before working the item" if hint["state_hint"] == "done" else "") + tail_
    return head + tail_ + (f"; act by {day(deadline)}" if deadline and not late else "")


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
                out[f["finding_id"]] = _public(decide(fx, t, conn))
                if fx.get("paid_first_partial"):
                    # what the second payer still owes after a first payer's partial payment, for the size the detection run
                    # stores (detect._set_states; F1's routed need, money-9's second half)
                    out[f["finding_id"]]["paid_first_partial"] = dict(fx["paid_first_partial"])
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
