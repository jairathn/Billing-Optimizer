"""
What EMA's A/R and Production reports add to RemitWatch's items, and what they show that the remittances never did.

The reports reach the store through remitwatch.ema_reports (the A/R, the catalog) and remitwatch.ema_ledger (Production's
postings, summarised into ema_lines, ema_line_payments, ema_line_reasons, ema_checks, ema_months, ema_link_map and
ema_link_events). This module reads those tables and the 835 tables and answers, check by check:

  C1  classify_checks       insurance payments EMA posted under a check no 835 carries, each with its likely cause
  C2  written_off_in_ema    what EMA wrote off on an item's own line (Build 1 v2), never added to the ERA-lines figure
  C3  claim_statuses, not_sent, rejected_claims, claim_answers, answers_through, unanswered_claims
                            claims that never left EMA, claims EMA shows answered, and claims no payer answered (G8)
  C4  unposted_remits       835 payments EMA never posted, and payments EMA posted short
  C5  ema_denials           denials EMA posted that no remittance showed
  C6  paid_in_ema           items EMA shows paid by a later payment no 835 carries (close, or a hint)
  C7  denial_as_contractual a payer's denials EMA writes off as contractual
  C8  credits               credit balances on the A/R, and Production's insurance credits the A/R leaves out
  C9  timely_writeoffs      timely-filing write-offs (report)
  W2-W4  overlays, unworked, ar_accounted: worked in EMA, the unworked number, the A/R accounted for
  W5  fax_vouch             whether EMA's reports vouch for an item the 835s alone would hold before a fax

The rules every check follows (spec 7.3 and the tech lead's amendments):
  - The 835 decides wherever it exists. EMA's money counts only where no 835 carries it, and is never added to an 835
    payment for the same check.
  - A claim status is claim-level and undated. It routes an item; it never closes one, never answers a claim on its
    own, and is never shown as the item's outcome. The outcome comes from the postings on the item's own line
    (line_outcomes), read one way for every check.
  - A write-off is never a payment and never closes an item. Closed in EMA is not paid.
  - The ASC's facility claims (twin links) never stand in for the professional claim.
  - A conclusion drawn from an absence (not posted, unanswered) needs Production at most 14 days old.
  - Nothing here loads a whole EMA table into Python: work goes through temporary key tables, 500 keys at a time.
  - The store holds keys, never names, record numbers, dates of birth or notes. No log line or message here holds a
    claim id, check number or claim number.

The detection wiring calls these functions in the order of amendment 4.5. This module never imports detect.py: the
Detector passes its own rules in as callables, rate(payer_group, code, year), ratio(payer_group, code), code_once(finding)
and has_835_finding(bill, service_date, code). store_rules(conn) builds stand-ins from the store's own tables, for use
outside a detection run.

Every list-returning check returns proto-findings: dicts the wiring hands to Detector._emit (see _proto).
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator, Sequence

from . import ema_ledger as el
from . import ema_reports as er
from .codes import EM_CODES, appeal_rule, filing_rule
from .ema_reports import status_class  # noqa: F401  (re-exported: the spec's ema_checks.status_class)

# ----------------------------------------------------------------------------------------------------------- constants
TURNAROUND_KEY = "ema:turnaround"
LAG_KEY = "ema:posting_lag"
RECONCILE_KEY = "ema:reconcile"
AR_ACCOUNTED_KEY = "ema:ar_accounted"
POLICY_KEY = "ema:writeoff_policy"          # owner decision O11: {pattern: {decision, by, at}}
UNWORKED_KEY = "ema:unworked"
UNPOSTED_KEY = "ema:unposted"
C7_KEY = "ema:denial_as_contractual"

# An item's EMA outcome, from the postings on its own line. Precedence (amendment 8.1, strict reading): paid after >
# patient > paid before > lost to timely filing > written off while pending > auto written off > the other write-offs >
# a claim action > closed unexplained > open.
OUTCOMES = ("paid_after", "paid_after_other", "paid_after_partial", "paid_before", "patient", "lost_to_timely_filing",
            "written_off_while_pending", "auto_written_off", "written_off_at_posting", "contractual_at_posting",
            "written_off_later", "contractual_later", "written_off_by_policy", "action", "closed_unexplained", "open",
            "shared_line", "not_found", "not_covered")
WRITE_OFF_OUTCOMES = ("lost_to_timely_filing", "written_off_while_pending", "auto_written_off", "written_off_at_posting",
                      "contractual_at_posting", "written_off_later", "contractual_later", "written_off_by_policy")

NO_ANSWER_DAYS = int(os.environ.get("REMITWATCH_NO_ANSWER_DAYS", "30") or 30)   # the same setting detect.py reads
STALE_DAYS = int(os.environ.get("REMITWATCH_EMA_STALE_DAYS", "8") or 8)       # a report older than this is stale
PRODUCTION_FRESH_DAYS = 14          # conclusions from an absence need Production at most this old (G13)
AR_FRESH_DAYS = 8
CHUNK = 500
MONEY_CATEGORIES = ("denial", "bundling", "downcoding", "underpayment")
CLAIM_SIGNALS = ("NO_ANSWER", "REJECTED_PRE_PAYER", "NOT_SENT")         # claim-level items with no remittance line
SHORT_CATEGORIES = ("downcoding", "underpayment")                       # a payment closes them only at 95% of the shortfall
ROUTINE_CLASSES = tuple(el.ROUTINE_CLASSES)                             # none, routine, patient, ema_kind
NOT_DENIAL_CODES = frozenset({"24"})        # CO-24 alone is a capitation note, not a denial (amendment 8)
# never an EMA-only denial (C5): a secondary's "the provider cannot bill the patient" (209) and a code reported for
# information only (246); detect.py lists neither (spec section 0, design 3's weakness)
C5_SKIP_CODES = frozenset({"24", "209", "246"})
ROUTINE_SHORT_CODES = frozenset({"45", "59", "131"})   # the contractual reduction that absorbs an underpayment or downcode
PENDING_CLASSES = ("information",)          # OA-133, CO-16 and the other "send what is missing" reasons (amendment 8.1)
TIMELY_CLASS = "timely"
POLICY_CODES = frozenset({"G2211", "99024"})                            # never itemized (detect.py's zero-paid rule)
WORK_ACTIONS = re.compile(r"APPEAL|CORRECTED|REPROCESS|RESUBMIT|RECORDS SENT|SENT MEDICAL RECORDS|ADJUSTMENT REQUEST", re.I)

DEFAULT_TURNAROUND = 45             # days, for a payer group with fewer than MIN_GROUP answers
MIN_GROUP = 20
TURNAROUND_YEARS = 2                # the 835 answers measured: service in the two years before the newest remittance
DEFAULT_LAG_P50, DEFAULT_LAG_P95 = 3, 8

# C1
AFTER_FEED_DAYS = 3                 # checks first posted after the newest 835 + 3 days are "since the remittances stopped"
MISNUMBER_BEFORE, MISNUMBER_AFTER, MISNUMBER_CENTS = 45, 10, 100
SEPARATE_SHARE = 0.80
HISTORY_DAYS = 365
FEED_GAP_MIN, FEED_GAP_SHAPE = 5, 0.20
REMITS_MISSING_MIN, REMITS_MISSING_SHARE = 500_000, 0.02
ALERT_FEED_GAP_CENTS, ALERT_FEED_GAP_CHECKS = 200_000, 2

# C4
UNPOSTED_MIN_CENTS = 100
UNPOSTED_DAYS = 365
UNPOSTED_SERVICE_LAG = 21
UNPOSTED_POSTED = (-3, 21)
UNPOSTED_HIGH = 100_000
DIFF_MIN, DIFF_SHARE, SHORT_MIN = 100, 0.005, 2_500
OFFSET_DAYS = 60                    # a payer's over- and under-postings this close offset
# PLB reasons by which a payer keeps money it owes (a recovery, a return, a balance carried forward, a levy): a remittance
# they bring to $0 moved its money all the same, so it is not an informational copy
OFFSET_PLB = frozenset({"WO", "72", "FB", "J1", "WU", "OB", "L3", "50", "IR", "LE", "GO", "BD", "B2"})
MANY_PAYER_GROUPS = ("Medicare Supplement carriers", "Self-funded plan TPAs")    # within one, only the payer's name says who

# C5
DENIAL_MIN = 500
PAID_SINCE_SHARE = 0.50
FALLBACK_SHARE = 0.45
HIGH_DAYS, HIGH_EXPECTED = 30, 10_000

# C6
CLOSE_SHARE, SHORT_SHARE = 0.50, 0.95

# C7
AS_CONTRACTUAL_MIN, AS_CONTRACTUAL_LINES, AS_CONTRACTUAL_HIGH = 100_000, 10, 2_500_000

# C8
CREDIT_DAYS, OTHER_CREDIT_MIN = 60, 50_000

# W3
UNWORKED_DAYS = 21

# C3b
NOT_SENT_DAYS, INVALID_DAYS = 14, 3


# ------------------------------------------------------------------------------------------------------------ helpers
def _today(today: str | None) -> str:
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
    """Days from a to b (b - a)."""
    da, db = _d(a), _d(b)
    return (db - da).days if da and db else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    try:
        r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    if not r or r[0] is None:
        return default
    try:
        return json.loads(r[0])
    except ValueError:
        return r[0]


def _set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value, sort_keys=True, default=str)))


def _chunks(seq: Sequence, n: int = CHUNK) -> Iterator[Sequence]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _temp(conn: sqlite3.Connection, name: str, cols: Sequence[str], rows: Iterable[Sequence] = (),
          indexes: Sequence[str] = ()) -> None:
    """A temporary key table (dropped first if it exists), filled with `rows`, with the given indexes."""
    conn.execute(f"DROP TABLE IF EXISTS temp.{name}")
    conn.execute(f"CREATE TEMP TABLE {name} ({', '.join(cols)})")
    rows = list(rows)
    if rows:
        conn.executemany(f"INSERT INTO temp.{name} VALUES ({','.join('?' * len(cols))})", rows)
    for i, ix in enumerate(indexes):
        conn.execute(f"CREATE INDEX temp.ix_{name}_{i} ON {name}({ix})")


def _drop(conn: sqlite3.Connection, *names: str) -> None:
    for n in names:
        conn.execute(f"DROP TABLE IF EXISTS temp.{n}")


def _usd(cents: Any) -> str:
    c = int(cents or 0)
    return f"-${abs(c) / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"


def _tail(check: Any) -> str:
    """The last four letters or digits of a check or trace number: enough to find it, never the whole number."""
    return re.sub(r"[^A-Za-z0-9]", "", str(check or ""))[-4:]


def _day(iso: Any) -> str:
    d = _d(iso)
    return f"{d:%b} {d.day}, {d.year}" if d else str(iso or "")


def _ev(f: dict) -> dict:
    ev = f.get("evidence")
    if isinstance(ev, dict):
        return ev
    try:
        return json.loads(ev) if ev else {}
    except (TypeError, ValueError):
        return {}


def _bill(pcn: Any) -> str | None:
    return er.bill_of_claim(pcn) if pcn else None


def _full_store(conn: sqlite3.Connection) -> bool:
    """Whether this is the whole store (it holds the key salt): the figures are worked out from its tables. The dashboard's
    copy (cloud.build_serving) holds part of the store and never the salt: there the pages read what detection stored
    (meta ema:reconcile, ema:c1_cells, ema:unposted, ema_ar.bucket), and nothing is written."""
    return er.stored_salt(conn) is not None


def _prepare(conn: sqlite3.Connection) -> None:
    """The EMA tables and the SQL functions the checks use. Never writes to a database that has no key salt (the
    dashboard's copy): the salt is never made here (the readers make it), and the schema is brought up to date only on
    the whole store, or where the EMA tables are missing altogether."""
    if _full_store(conn) or not _table(conn, "ema_report_files"):
        er.ensure_schema(conn)
    er.register_functions(conn, create=False)
    conn.create_function("rw_ckey", 1, el.check_key, deterministic=True)
    conn.create_function("rw_is_facility", 1, lambda c: 1 if er.is_facility_claim(c) else 0, deterministic=True)
    conn.create_function("rw_payer_family", 1, _family, deterministic=True)


_FAMILY_ALIASES = {"unitedhealthcare": "uhc", "united": "uhc", "uhc": "uhc", "aarp": "uhc", "bcbs": "anthem", "blue": "anthem"}


def _family(group: Any) -> str:
    """A payer group as two vocabularies can both spell it: the 835's (codes.payer_group_kind, which may append "Medicare
    Advantage" or "Medicaid" to a company's group) and EMA's (ema_reports.payer_group_of). Medicare Advantage and Medicaid
    products compare by company; every other group by its exact name."""
    g = " ".join(str(group or "").lower().split())
    if not g:
        return ""
    tag = "ma" if "medicare advantage" in g else "medicaid" if "medicaid" in g else ""
    if not tag:
        return "=" + g
    first = re.split(r"[^a-z0-9]+", g)[0]
    return _FAMILY_ALIASES.get(first, first) + "|" + tag


def _same_payer(a: Any, b: Any) -> bool:
    return bool(a) and bool(b) and (a == b or _family(a) == _family(b))


def _by_group(d: dict, group: Any, default: Any) -> Any:
    if not d:
        return default
    if group in d:
        return d[group]
    fam = _family(group)
    for g, v in d.items():
        if _family(g) == fam:
            return v
    return default


def _sql_percentiles(conn: sqlite3.Connection, sub: str, args: Sequence = ()) -> dict:
    """Per group: n, p50, p90 and p95 (nearest rank) of a subquery yielding (grp, d), worked out in SQL so no row list
    is held in Python. The group '' holds every row."""
    q = f"""WITH x AS (SELECT grp, d FROM ({sub}) WHERE d IS NOT NULL),
            y AS (SELECT g, d, ROW_NUMBER() OVER (PARTITION BY g ORDER BY d) AS rn, COUNT(*) OVER (PARTITION BY g) AS n
                  FROM (SELECT 'g:' || COALESCE(grp, 'Unknown') AS g, d FROM x UNION ALL SELECT '*', d FROM x))
            SELECT g, MAX(n), MAX(CASE WHEN rn = (n * 50 + 99) / 100 THEN d END), MAX(CASE WHEN rn = (n * 90 + 99) / 100 THEN d END),
                   MAX(CASE WHEN rn = (n * 95 + 99) / 100 THEN d END), SUM(d < 0)
            FROM y GROUP BY g"""
    out = {}
    for g, n, p50, p90, p95, early in conn.execute(q, tuple(args)):
        out["" if g == "*" else g[2:]] = {"n": n, "p50": p50, "p90": p90, "p95": p95, "early": early}
    return out


# ------------------------------------------------------------------------------------------- the Detector's stand-ins
class StoreRules:
    """Stand-ins for the Detector's rules, read from the store's own tables, for running the checks outside a detection
    run (the acceptance scripts, the CLI, reconcile_summary). In a detection run the Detector passes its own."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lines: dict[str, Counter] = {}
        self._rates: dict[tuple, int | None] = {}
        self._ratios: dict[tuple, float | None] = {}
        self._f835: set | None = None
        self._newest_month: str | None = None

    def code_once(self, f: dict) -> bool:
        """The claim carries the item's code once on its date (EMA's rows are by claim, code and date)."""
        ck = f.get("claim_key")
        if not ck or not _table(self.conn, "lines"):
            return True
        cnt = self._lines.get(ck)
        if cnt is None:
            if len(self._lines) > 50_000:
                self._lines.clear()
            cnt = self._lines[ck] = Counter((r[0], r[1]) for r in self.conn.execute(
                "SELECT service_date, procedure_code FROM lines WHERE claim_key=?", (ck,)))
        return cnt[(f.get("service_date"), f.get("procedure_code"))] <= 1

    def rate(self, payer_group: str | None, code: str | None, year: str | None) -> int | None:
        """The payer's usual allowed amount for the code (payer_rates: the office setting, a physician first), that year
        or the year before."""
        key = (payer_group, code, year)
        if key in self._rates:
            return self._rates[key]
        got = None
        if payer_group and code and year and _table(self.conn, "payer_rates"):
            for y in (str(year), str(int(year) - 1) if str(year).isdigit() else None):
                if not y:
                    continue
                r = self.conn.execute("""SELECT mode_cents FROM payer_rates WHERE payer_group=? AND code=? AND year=?
                                         ORDER BY (pos_class='office') DESC, (tier='physician') DESC, n DESC LIMIT 1""",
                                      (payer_group, code, y)).fetchone()
                if r and r[0]:
                    got = int(r[0])
                    break
        self._rates[key] = got
        return got

    def ratio(self, payer_group: str | None, code: str | None) -> float | None:
        """What the payer group paid of the charge for the code over the last 12 months of remittances, from at least
        10 paid lines (the exposure table)."""
        key = (payer_group, code)
        if key in self._ratios:
            return self._ratios[key]
        got = None
        if payer_group and code and _table(self.conn, "exposure"):
            if self._newest_month is None:
                self._newest_month = self.conn.execute("SELECT MAX(month) FROM exposure").fetchone()[0] or ""
            since = _add((self._newest_month or "") + "-01", -365) or ""
            r = self.conn.execute("""SELECT SUM(paid_cents), SUM(charge_cents), SUM(lines - zero_paid) FROM exposure
                                     WHERE payer_group=? AND code=? AND month >= ?""", (payer_group, code, since[:7])).fetchone()
            if r and r[2] and r[2] >= 10 and r[1]:
                got = max(0.0, min(1.0, (r[0] or 0) / r[1]))
        self._ratios[key] = got
        return got

    def has_835_finding(self, bill: str | None, svc: str | None, code: str | None) -> bool:
        """An item the 835 rules made (any status) for this bill, date and code, or for the whole claim on that date."""
        if self._f835 is None:
            self._f835 = set()
            if _table(self.conn, "findings"):
                for pcn, svc_, code_, line in self.conn.execute(
                        """SELECT patient_control_number, service_date, procedure_code, line_key IS NOT NULL FROM findings
                           WHERE patient_control_number IS NOT NULL AND claim_key IS NOT NULL"""):
                    self._f835.add((_bill(pcn), svc_, code_ if line else None))
        return (bill, svc, code) in self._f835 or (bill, svc, None) in self._f835


def store_rules(conn: sqlite3.Connection) -> StoreRules:
    return StoreRules(conn)


# ------------------------------------------------------------------------------------------------------- freshness
def freshness(conn: sqlite3.Connection, today: str | None = None) -> dict:
    """The reports' as-of dates and whether each is fresh enough for conclusions drawn from an absence: Production at most
    14 days old, the A/R at most 8 (G13). Also the 835s' first and newest payment dates and Production's boxes."""
    t = _today(today)
    cov = er.coverage(conn)
    prod = cov.get("production_as_of")
    ar = max((v.get("as_of") for v in (cov.get("ar") or {}).values() if v.get("as_of")), default=None)
    newest = first = None
    if _table(conn, "transactions"):
        newest, first = conn.execute("SELECT MAX(payment_date), MIN(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()
    pd, ad = _days(prod, t), _days(ar, t)
    return {"today": t, "production_as_of": prod, "production_days": pd,
            "production_fresh": pd is not None and pd <= PRODUCTION_FRESH_DAYS, "ar_as_of": ar, "ar_days": ad,
            "ar_fresh": ad is not None and ad <= AR_FRESH_DAYS, "newest_835": newest, "first_835": first,
            "boxes": cov.get("production") or [], "ar_years": cov.get("ar") or {}}


def _svc_covered(boxes: Sequence[dict], svc: str | None, posted: str | None = None) -> bool:
    """Whether Production's current boxes hold the postings of a line of this service date (around `posted`)."""
    for b in boxes:
        if b.get("scope") == "posted":
            d = posted or svc
            if d and (b.get("posted_lo") or "") <= d <= (b.get("posted_hi") or ""):
                return True
        elif svc and (b.get("svc_lo") or "") <= svc <= (b.get("svc_hi") or ""):
            return True
    return False


def _ledger_rev(conn: sqlite3.Connection) -> str:
    return f"{_meta(conn, el.META_SEQ, '')}|{_meta(conn, el.META_REV, '')}"


# --------------------------------------------------------------------------------------------- turnaround, posting lag
def turnaround(conn: sqlite3.Connection, *, ledger: Any = None, today: str | None = None) -> dict:
    """How long each payer group takes to answer, as {group: {'svc_p95', 'charge_p95', 'n'}}, written to meta
    ema:turnaround:
      svc_p95     the 835s: days from the date of service to a bill's first answer (claim status 1, 19 or 4), service in
                  the last two years; groups with at least 20 bills, else 45 days
      charge_p95  Production: days from a claim's first charge posting to its first insurance posting (a payment, a $0
                  answer or any reason code); at least 20 claims, else 45 days. Read from the posting ledger when one is
                  given (every claim), else kept from the last time it was (while the ledger has not changed since),
                  else from the store's kept lines (which leave out most claims paid cleanly, so it runs long)"""
    _prepare(conn)
    svc: dict = {}
    if _table(conn, "claims") and _table(conn, "transactions"):
        newest = conn.execute("SELECT MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()[0]
        since = _add(newest, -365 * TURNAROUND_YEARS) or "0000"
        conn.execute("DROP TABLE IF EXISTS temp.ec_tat")
        conn.execute("""CREATE TEMP TABLE ec_tat AS
            SELECT rw_bill(c.patient_control_number) AS bill, t.payment_date AS ans, c.payer_name AS payer,
                   COALESCE(c.statement_from, (SELECT MIN(l.service_date) FROM lines l WHERE l.claim_key = c.claim_key)) AS svc
            FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
            WHERE COALESCE(t.superseded, 0) = 0 AND c.claim_status_code IN ('1', '19', '4') AND c.patient_control_number IS NOT NULL
              AND t.payment_date IS NOT NULL""")
        svc = _sql_percentiles(conn, """SELECT rw_payer_group(payer) AS grp, CAST(julianday(ans) - julianday(svc0) AS INTEGER) AS d FROM (
                SELECT ans, payer, MIN(svc) OVER (PARTITION BY bill) AS svc0,
                       ROW_NUMBER() OVER (PARTITION BY bill ORDER BY ans, payer) AS rn FROM temp.ec_tat)
                WHERE rn = 1 AND svc0 >= ? AND ans >= svc0""", (since,))
        _drop(conn, "ec_tat")
    stored = _meta(conn, TURNAROUND_KEY, {}) or {}
    rev = _ledger_rev(conn)
    charge: dict = {}
    if ledger is not None:
        basis = "ledger"
        lc = ledger.conn
        names = {i: n for i, n in lc.execute("SELECT id, name FROM names")}
        cache: dict = {}

        def grp_of(i: Any) -> str:
            if i not in cache:
                cache[i] = er.payer_group_of(conn, names.get(i))[0]
            return cache[i]
        lc.create_function("ec_grp", 1, grp_of)
        newest_post = lc.execute("SELECT MAX(posted) FROM postings").fetchone()[0]
        since = _add(newest_post, -365 * TURNAROUND_YEARS) or "0000"
        charge = _sql_percentiles(lc, """SELECT ec_grp(pp) AS grp, CAST(julianday(fa) - julianday(fc) AS INTEGER) AS d FROM (
                SELECT g.pp AS pp, g.fc AS fc, (SELECT MIN(q.posted) FROM postings q WHERE q.link = g.link AND q.posted >= g.fc
                       AND (COALESCE(q.ins_paid, 0) <> 0 OR q.check_no IS NOT NULL OR q.grp IS NOT NULL)) AS fa
                FROM (SELECT p.link AS link, MIN(CASE WHEN COALESCE(p.charges, 0) > 0 THEN p.posted END) AS fc,
                             MAX(p.primary_payer) AS pp
                      FROM postings p WHERE p.link IS NOT NULL AND (COALESCE(p.flags, 0) & 17) = 0 GROUP BY p.link) g
                WHERE g.fc >= ?) WHERE fa IS NOT NULL""", (since,))
    elif stored.get("charge_basis") == "ledger" and stored.get("ledger_rev") == rev and stored.get("charge"):
        basis = "ledger"
        charge = stored["charge"]
    else:
        basis = "kept_lines"
        newest_post = conn.execute("SELECT MAX(last_posted) FROM ema_lines").fetchone()[0]
        since = _add(newest_post, -365 * TURNAROUND_YEARS) or "0000"
        charge = _sql_percentiles(conn, """SELECT grp, CAST(julianday(fa) - julianday(fc) AS INTEGER) AS d FROM (
                SELECT MAX(payer_group) AS grp, MIN(first_charge_posted) AS fc,
                       MIN(CASE WHEN first_ins_posting >= first_charge_posted THEN first_ins_posting END) AS fa
                FROM ema_lines WHERE (COALESCE(flags, 0) & 17) = 0 GROUP BY link)
                WHERE fc >= ? AND fa IS NOT NULL""", (since,))
    out: dict = {}
    for g in sorted(set(svc) | set(charge)):
        if not g:
            continue
        s, c = svc.get(g) or {}, charge.get(g) or {}
        out[g] = {"svc_p95": s["p95"] if (s.get("n") or 0) >= MIN_GROUP else DEFAULT_TURNAROUND,
                  "charge_p95": c["p95"] if (c.get("n") or 0) >= MIN_GROUP else DEFAULT_TURNAROUND,
                  "n": s.get("n") or 0, "n_charge": c.get("n") or 0}
    _set_meta(conn, TURNAROUND_KEY, {"groups": out, "default": DEFAULT_TURNAROUND, "charge_basis": basis, "ledger_rev": rev,
                                     "charge": charge, "all": {"svc": svc.get(""), "charge": charge.get("")}, "at": _now()})
    conn.commit()
    return out


def _turnaround(conn: sqlite3.Connection) -> dict:
    """The stored turnaround (meta ema:turnaround), worked out once when nothing is stored yet."""
    got = _meta(conn, TURNAROUND_KEY, None)
    if not got or "groups" not in got:
        turnaround(conn)
        got = _meta(conn, TURNAROUND_KEY, {}) or {}
    return got


def turnaround_days(conn: sqlite3.Connection, group: str | None, which: str = "svc_p95") -> int:
    """A payer group's 95th-percentile answer time (svc_p95 or charge_p95), 45 days when it is not known."""
    g = _by_group((_turnaround(conn).get("groups") or {}), group, {}) or {}
    return int(g.get(which) or DEFAULT_TURNAROUND)


def turnaround_text(group: str | None, days: int) -> str:
    return f"{group or 'This payer'} answers 95% of claims within {days} days"


def posting_lag(conn: sqlite3.Connection) -> dict:
    """How long EMA takes to post a remittance after its payment date, per payer group: {group: p95 days} (8 days when a
    group has fewer than 20 matched checks), from the checks EMA posted under an 835 trace. Meta ema:posting_lag also
    keeps the median (the outcome anchor's estimate), p90 and the share posted early."""
    _prepare(conn)
    st = _sql_percentiles(conn, """SELECT payer_group AS grp, CAST(round(julianday(first_posted) - julianday(trace_payment_date)) AS INTEGER) AS d
                                   FROM ema_checks WHERE match IN ('exact', 'loose') AND trace_payment_date IS NOT NULL
                                     AND first_posted IS NOT NULL""")
    allv = st.get("") or {}
    d95 = allv.get("p95") if (allv.get("n") or 0) >= MIN_GROUP else DEFAULT_LAG_P95
    d50 = allv.get("p50") if (allv.get("n") or 0) >= MIN_GROUP else DEFAULT_LAG_P50
    d95 = max(0, d95 if d95 is not None else DEFAULT_LAG_P95)
    d50 = max(0, d50 if d50 is not None else DEFAULT_LAG_P50)
    p95, p50, n = {}, {}, {}
    for g, v in st.items():
        if not g:
            continue
        ok = (v.get("n") or 0) >= MIN_GROUP
        p95[g] = max(0, v["p95"]) if ok and v.get("p95") is not None else d95
        p50[g] = max(0, v["p50"]) if ok and v.get("p50") is not None else d50
        n[g] = v.get("n") or 0
    _set_meta(conn, LAG_KEY, {"p95": p95, "p50": p50, "n": n, "default_p95": d95, "default_p50": d50,
                              "all": allv, "at": _now()})
    conn.commit()
    return p95


def _lags(conn: sqlite3.Connection) -> dict:
    got = _meta(conn, LAG_KEY, None)
    if not got or "p95" not in got:
        posting_lag(conn)
        got = _meta(conn, LAG_KEY, {}) or {}
    return got


def _lag(lags: dict, group: str | None, which: str = "p95") -> int:
    d = lags.get("default_" + which, DEFAULT_LAG_P95 if which == "p95" else DEFAULT_LAG_P50)
    return int(_by_group(lags.get(which) or {}, group, d) or 0)


# -------------------------------------------------------------------------------------------- C1: missing remittances
def _ema_check_keys(conn: sqlite3.Connection) -> None:
    """temp.ec_ck: every EMA check as the 835 match compares it (exact key, loose key, last 10 characters in a group)."""
    conn.execute("DROP TABLE IF EXISTS temp.ec_ck")
    conn.execute("""CREATE TEMP TABLE ec_ck AS SELECT DISTINCT CAST(rw_ckey(check_no) AS TEXT) AS ckey,
                        CAST(rw_check_loose(check_no) AS TEXT) AS lkey, CAST(substr(trim(check_no), -10) AS TEXT) AS last10,
                        CAST(length(trim(check_no)) AS INTEGER) AS len, CAST(payer_group AS TEXT) AS grp
                    FROM ema_checks WHERE check_no IS NOT NULL AND check_no <> ''""")
    conn.execute("CREATE INDEX temp.ix_ec_ck_c ON ec_ck(ckey)")
    conn.execute("CREATE INDEX temp.ix_ec_ck_l ON ec_ck(lkey)")
    conn.execute("CREATE INDEX temp.ix_ec_ck_t ON ec_ck(last10, grp)")


def _unmatched_traces(conn: sqlite3.Connection) -> None:
    """temp.ec_ut: the 835 payments (not superseded) whose trace matches no EMA check, exactly or loosely."""
    _ema_check_keys(conn)
    conn.execute("DROP TABLE IF EXISTS temp.ec_ut")
    conn.execute("""CREATE TEMP TABLE ec_ut AS
        SELECT t.transaction_key AS tk, t.payment_date AS paid_on, CAST(rw_payer_group(t.payer_name) AS TEXT) AS grp,
               rw_payer_kind(t.payer_name) AS kind, t.payer_name AS payer, COALESCE(t.claim_paid_total_cents, 0) AS paid,
               t.payment_amount_cents AS deposit, t.trace_number AS trace
        FROM transactions t
        WHERE COALESCE(t.superseded, 0) = 0 AND t.trace_number IS NOT NULL AND trim(t.trace_number) <> ''
          AND NOT EXISTS (SELECT 1 FROM temp.ec_ck k WHERE k.ckey = rw_ckey(t.trace_number))
          AND NOT EXISTS (SELECT 1 FROM temp.ec_ck k WHERE k.lkey = rw_check_loose(t.trace_number))
          AND NOT (length(trim(t.trace_number)) >= 10 AND EXISTS (SELECT 1 FROM temp.ec_ck k
                   WHERE k.last10 = substr(trim(t.trace_number), -10) AND k.len >= 10 AND k.grp = rw_payer_group(t.payer_name)))""")
    conn.execute("CREATE INDEX temp.ix_ec_ut ON ec_ut(grp, paid_on)")
    conn.execute("CREATE INDEX temp.ix_ec_ut_tk ON ec_ut(tk)")


C1_CAUSES = ("misnumbered", "separate_payee", "no_era_va", "no_era", "feed_gap", "other")
C1_OTHER = ("after_feed", "before_feed", "damaged")


def classify_checks(conn: sqlite3.Connection) -> dict:
    """C1: give each insurance check EMA posted under a number no 835 carries its likely cause, in ema_checks.cause (and
    pair_trace), and the money by month, EMA payer and cause in ema_months (measure no835). Checks first posted between
    the first 835 payment and the newest 835 payment + 3 days are judged; later ones are 'after_feed' (since the
    remittances stopped), earlier ones 'before_feed'. A check with a number a spreadsheet damaged is 'damaged'. Status-only
    checks (no insurance money) are not judged. The first cause that applies:
      misnumbered     an 835 of the same payer group whose own trace matches no EMA check, dated 45 days before to 10
                      days after the check's first posting, paying within $1 of it: the same money under another number
                      (paired one to one, closest date first; C4 then lists neither)
      separate_payee  80% of the check's dollars are on the ASC's facility claims (a facility link, or a line whose twin
                      maps to an 835 bill): the ASC's remittances go to another payee
      no_era_va       VA community care, and the group had no matched check in the 365 days before
      no_era          no matched check under that EMA payer name in the 365 days before (the payer sends no 835 here)
      feed_gap        at least 5 matched checks in the 365 days before, at least 20% of them sharing this check's number
                      shape (digits 9, letters A): the 835 exists and never reached the feed. The EMA payer name's own
                      matched checks are compared when it has 5, else its payer group's (a group of many payers, such as
                      the supplement carriers, mixes their number series)
      other
    Returns the judged window's totals by cause."""
    _prepare(conn)
    c = conn
    c.execute("UPDATE ema_checks SET cause = NULL, pair_trace = NULL")
    first, newest = (None, None)
    if _table(c, "transactions"):
        first, newest = c.execute("SELECT MIN(payment_date), MAX(payment_date) FROM transactions WHERE COALESCE(superseded,0)=0").fetchone()
    out: dict = {"window": None, "by_cause": {}, "matched": {}, "pairs": 0}
    if not newest:
        c.execute("DELETE FROM ema_months WHERE measure='no835'")
        c.commit()
        return out
    end = _add(newest, AFTER_FEED_DAYS)
    out["window"] = [first, end]
    judged = "match = 'none' AND COALESCE(ins_paid, 0) <> 0 AND cause IS NULL AND first_posted BETWEEN ? AND ?"
    c.execute("UPDATE ema_checks SET cause = 'after_feed' WHERE match = 'none' AND COALESCE(ins_paid, 0) <> 0 AND first_posted > ?", (end,))
    c.execute("UPDATE ema_checks SET cause = 'before_feed' WHERE match = 'none' AND COALESCE(ins_paid, 0) <> 0 AND first_posted < ?", (first,))
    c.execute(f"UPDATE ema_checks SET cause = 'damaged' WHERE {judged} AND COALESCE(damaged, 0) = 1", (first, end))
    # 1. misnumbered: paired one to one with an unmatched 835 of the same group, closest date, then closest amount
    _unmatched_traces(c)
    cand = c.execute(f"""SELECT e.check_no, e.payer, u.tk,
                                abs(julianday(u.paid_on) - julianday(e.first_posted)) AS dd, abs(u.paid - e.ins_paid) AS da
                         FROM ema_checks e JOIN temp.ec_ut u ON u.grp = e.payer_group
                              AND u.paid_on BETWEEN date(e.first_posted, '-{MISNUMBER_BEFORE} days') AND date(e.first_posted, '+{MISNUMBER_AFTER} days')
                              AND abs(u.paid - e.ins_paid) <= {MISNUMBER_CENTS}
                         WHERE e.match = 'none' AND COALESCE(e.ins_paid, 0) <> 0 AND e.cause IS NULL AND e.first_posted BETWEEN ? AND ?
                         ORDER BY dd, da, e.check_no, e.payer, u.tk""", (first, end)).fetchall()
    used_c, used_t, pairs = set(), set(), []
    for chk, payer, tk, _dd, _da in cand:
        if (chk, payer) in used_c or tk in used_t:
            continue
        used_c.add((chk, payer))
        used_t.add(tk)
        pairs.append((tk, chk, payer))
    c.executemany("UPDATE ema_checks SET cause = 'misnumbered', pair_trace = ? WHERE check_no = ? AND payer = ?", pairs)
    # 1b. misnumbered in part: EMA posted part of an 835 under its own number and the rest under another. A check of the
    # same payer family, in the same window, within $1 of what that 835 still lacks in EMA, is that rest (C4 then sees
    # the 835 posted in full and C1 does not count the money as having no remittance)
    c.execute("DROP TABLE IF EXISTS temp.ec_short")
    c.execute("""CREATE TEMP TABLE ec_short AS
        SELECT t.transaction_key AS tk, t.payment_date AS paid_on, CAST(rw_payer_family(rw_payer_group(t.payer_name)) AS TEXT) AS fam,
               COALESCE(t.claim_paid_total_cents, 0) - x.ema AS short
        FROM transactions t JOIN (SELECT trace_key AS tk, SUM(ins_paid) AS ema FROM ema_checks WHERE trace_key IS NOT NULL
                                  GROUP BY trace_key) x ON x.tk = t.transaction_key
        WHERE COALESCE(t.superseded, 0) = 0 AND COALESCE(t.claim_paid_total_cents, 0) - x.ema > ?""", (MISNUMBER_CENTS,))
    c.execute("CREATE INDEX temp.ix_ec_short ON ec_short(fam, paid_on)")
    cand = c.execute(f"""SELECT e.check_no, e.payer, u.tk,
                                abs(julianday(u.paid_on) - julianday(e.first_posted)) AS dd, abs(u.short - e.ins_paid) AS da
                         FROM ema_checks e JOIN temp.ec_short u ON u.fam = rw_payer_family(e.payer_group)
                              AND u.paid_on BETWEEN date(e.first_posted, '-{MISNUMBER_BEFORE} days') AND date(e.first_posted, '+{MISNUMBER_AFTER} days')
                              AND abs(u.short - e.ins_paid) <= {MISNUMBER_CENTS}
                         WHERE e.match = 'none' AND COALESCE(e.ins_paid, 0) > 0 AND e.cause IS NULL AND e.first_posted BETWEEN ? AND ?
                         ORDER BY dd, da, e.check_no, e.payer, u.tk""", (first, end)).fetchall()
    part_pairs = []
    for chk, payer, tk, _dd, _da in cand:
        if (chk, payer) in used_c or tk in used_t:
            continue
        used_c.add((chk, payer))
        used_t.add(tk)
        part_pairs.append((tk, chk, payer))
    c.executemany("UPDATE ema_checks SET cause = 'misnumbered', pair_trace = ? WHERE check_no = ? AND payer = ?", part_pairs)
    _drop(c, "ec_short")
    out["pairs"] = len(pairs) + len(part_pairs)
    out["pairs_in_part"] = len(part_pairs)
    # 2. separate_payee: the check's dollars on the ASC's facility claims
    c.execute("DROP TABLE IF EXISTS temp.ec_sep")
    c.execute("""CREATE TEMP TABLE ec_sep AS
        SELECT y.check_no AS check_no, y.payer AS payer, SUM(y.ins_paid) AS cents
        FROM ema_line_payments y JOIN ema_lines l ON l.link = y.link AND l.svc = y.svc AND l.cpt = y.cpt
        WHERE y.check_no <> '' AND (COALESCE(l.facility, 0) = 1 OR EXISTS (SELECT 1 FROM ema_link_map m WHERE m.link = l.twin_link
              AND COALESCE(m.conflict, 0) = 0 AND m.bill IS NOT NULL AND COALESCE(m.facility, 0) = 0))
        GROUP BY y.check_no, y.payer""")
    c.execute("CREATE INDEX temp.ix_ec_sep ON ec_sep(check_no, payer)")
    c.execute(f"""UPDATE ema_checks SET cause = 'separate_payee' WHERE {judged} AND ins_paid > 0
                  AND COALESCE((SELECT s.cents FROM temp.ec_sep s WHERE s.check_no = ema_checks.check_no AND s.payer = ema_checks.payer), 0)
                      >= {SEPARATE_SHARE} * ins_paid""", (first, end))
    # 3-5. against the matched checks of the 365 days before the check's first posting
    c.execute("DROP TABLE IF EXISTS temp.ec_mt")
    c.execute("""CREATE TEMP TABLE ec_mt AS SELECT CAST(payer AS TEXT) AS payer, CAST(payer_group AS TEXT) AS grp,
                        CAST(first_posted AS TEXT) AS posted, CAST(shape AS TEXT) AS shape
                 FROM ema_checks WHERE match IN ('exact', 'loose')""")
    c.execute("CREATE INDEX temp.ix_ec_mt_p ON ec_mt(payer, posted)")
    c.execute("CREATE INDEX temp.ix_ec_mt_ps ON ec_mt(payer, shape, posted)")
    c.execute("CREATE INDEX temp.ix_ec_mt_g ON ec_mt(grp, posted)")
    c.execute("CREATE INDEX temp.ix_ec_mt_s ON ec_mt(grp, shape, posted)")
    win = f"m.posted BETWEEN date(ema_checks.first_posted, '-{HISTORY_DAYS} days') AND ema_checks.first_posted"
    c.execute(f"""UPDATE ema_checks SET cause = 'no_era_va' WHERE {judged} AND payer_kind = 'va'
                  AND NOT EXISTS (SELECT 1 FROM temp.ec_mt m WHERE m.grp = ema_checks.payer_group AND {win})""", (first, end))
    c.execute(f"""UPDATE ema_checks SET cause = 'no_era' WHERE {judged}
                  AND NOT EXISTS (SELECT 1 FROM temp.ec_mt m WHERE m.payer = ema_checks.payer AND {win})""", (first, end))
    by_name = f"(SELECT COUNT(*) FROM temp.ec_mt m WHERE m.payer = ema_checks.payer AND {win})"
    by_name_shape = f"(SELECT COUNT(*) FROM temp.ec_mt m WHERE m.payer = ema_checks.payer AND m.shape = ema_checks.shape AND {win})"
    by_grp = f"(SELECT COUNT(*) FROM temp.ec_mt m WHERE m.grp = ema_checks.payer_group AND {win})"
    by_grp_shape = f"(SELECT COUNT(*) FROM temp.ec_mt m WHERE m.grp = ema_checks.payer_group AND m.shape = ema_checks.shape AND {win})"
    c.execute(f"""UPDATE ema_checks SET cause = 'feed_gap' WHERE {judged} AND CASE
                    WHEN {by_name} >= {FEED_GAP_MIN} THEN {by_name_shape} >= {FEED_GAP_SHAPE} * {by_name}
                    ELSE {by_grp} >= {FEED_GAP_MIN} AND {by_grp_shape} >= {FEED_GAP_SHAPE} * {by_grp} END""", (first, end))
    c.execute(f"UPDATE ema_checks SET cause = 'other' WHERE {judged}", (first, end))
    c.execute("DELETE FROM ema_months WHERE measure='no835'")
    c.execute("""INSERT INTO ema_months (month, payer, measure, detail, cents, rows)
                 SELECT substr(first_posted, 1, 7), COALESCE(payer, ''), 'no835', cause, SUM(ins_paid), COUNT(*) FROM ema_checks
                 WHERE cause IS NOT NULL AND first_posted IS NOT NULL GROUP BY 1, 2, 4""")
    _drop(c, "ec_sep", "ec_mt")
    c.commit()
    for cause, n, cents in c.execute("""SELECT cause, COUNT(*), SUM(ins_paid) FROM ema_checks WHERE cause IS NOT NULL
                                        AND first_posted BETWEEN ? AND ? GROUP BY cause""", (first, end)):
        out["by_cause"][cause] = {"checks": n, "cents": cents or 0}
    n, cents = c.execute("""SELECT COUNT(*), SUM(ins_paid) FROM ema_checks WHERE match IN ('exact', 'loose')
                            AND first_posted BETWEEN ? AND ?""", (first, end)).fetchone()
    out["matched"] = {"checks": n, "cents": cents or 0}
    out["payer_equivalence"] = len(learn_payer_equivalence(c))
    return out


C1_CELLS_KEY = "ema:c1_cells"


def _c1_cells(conn: sqlite3.Connection) -> dict:
    """C1's figures by month, matched or not, cause and payer group (checks and dollars), and per month what EMA posted
    as insurance payments in all and on checks: the small table c1_summary adds up (stored by reconcile_summary for the
    dashboard's copy, which holds only the checks no 835 carries)."""
    cells = [[m, int(matched or 0), cause, grp, n, cents] for m, matched, cause, grp, n, cents in conn.execute(
        """SELECT substr(first_posted, 1, 7), match IN ('exact', 'loose'), COALESCE(cause, ''), COALESCE(payer_group, ''),
                  COUNT(*), COALESCE(SUM(ins_paid), 0)
           FROM ema_checks WHERE first_posted IS NOT NULL AND COALESCE(ins_paid, 0) <> 0 GROUP BY 1, 2, 3, 4""")]
    paid = {m: c for m, c in conn.execute("""SELECT month, COALESCE(SUM(cents), 0) FROM ema_months WHERE measure = 'ins_paid'
                                             GROUP BY month""") if m}
    on_checks = {m: c for m, c in conn.execute("""SELECT substr(first_posted, 1, 7), COALESCE(SUM(ins_paid), 0) FROM ema_checks
                                                  WHERE first_posted IS NOT NULL GROUP BY 1""") if m}
    return {"cells": cells, "paid": paid, "on_checks": on_checks}


def c1_summary(conn: sqlite3.Connection, date_from: str | None = None, date_to: str | None = None) -> dict:
    """C1 by month, cause and payer group (dollars and checks only), for /api/ema/reconcile and the Data tab, over the
    whole months the range touches. On the dashboard's copy it is added up from the cells detection stored."""
    _prepare(conn)
    data = _c1_cells(conn) if _full_store(conn) else (_meta(conn, C1_CELLS_KEY, None) or {})
    lo, hi = (date_from or "")[:7], (date_to or "")[:7]

    def inside(m: Any) -> bool:
        return bool(m) and (not lo or m >= lo) and (not hi or m <= hi)
    out: dict = {"months": {}, "causes": {}, "payers": {}, "matched_cents": 0, "unmatched_cents": 0}
    for month, matched, cause, grp, n, cents in data.get("cells") or []:
        if not inside(month):
            continue
        cents = cents or 0
        m = out["months"].setdefault(month, {"matched": 0, "unmatched": 0, "causes": {}})
        if matched:
            m["matched"] += cents
            out["matched_cents"] += cents
            continue
        m["unmatched"] += cents
        out["unmatched_cents"] += cents
        key = cause or "unjudged"
        m["causes"][key] = m["causes"].get(key, 0) + cents
        cc = out["causes"].setdefault(key, {"checks": 0, "cents": 0})
        cc["checks"] += n
        cc["cents"] += cents
        p = out["payers"].setdefault(grp or "Unknown", {})
        p[key] = p.get(key, 0) + cents
    # insurance payments EMA posted with no check number at all: no remittance can be matched to them. ema_months holds
    # whole months, so both sides are taken over the whole months the range touches
    all_paid = sum(c or 0 for m, c in (data.get("paid") or {}).items() if inside(m))
    on_checks = sum(c or 0 for m, c in (data.get("on_checks") or {}).items() if inside(m))
    out["no_check_cents"] = all_paid - on_checks
    out["posted_cents"] = all_paid
    return out


def checks_csv(conn: sqlite3.Connection, *, cause: str | None = None, date_from: str | None = None,
               date_to: str | None = None) -> Iterator[list]:
    """/api/ema/checks.csv: the checks no 835 carries (check number, first posted date, EMA payer, amount, cause), what
    TriZetto or the payer needs to send the remittances again. No patient data."""
    _prepare(conn)
    yield ["check_number", "first_posted", "last_posted", "ema_payer", "payer_group", "amount", "cause", "claims"]
    where, args = ["match = 'none'", "COALESCE(ins_paid, 0) <> 0"], []
    if cause:
        where.append("cause = ?")
        args.append(cause)
    if date_from:
        where.append("first_posted >= ?")
        args.append(date_from)
    if date_to:
        where.append("first_posted <= ?")
        args.append(date_to)
    cur = conn.execute(f"""SELECT check_no, first_posted, last_posted, payer, payer_group, ins_paid, cause, links FROM ema_checks
                           WHERE {' AND '.join(where)} ORDER BY first_posted, check_no""", args)
    while True:
        rows = cur.fetchmany(CHUNK)
        if not rows:
            break
        for chk, fp, lp, payer, grp, cents, cs, links in rows:
            yield [chk, fp, lp, payer, grp, f"{(cents or 0) / 100:.2f}", cs or "", links or 0]


# ----------------------------------------------------------------------------------------- C4: 835 payments not posted
def _claim_dates_of(conn: sqlite3.Connection, tks: Sequence[str]) -> dict[str, dict]:
    """For these 835 payments: their claims' service dates and claim ids."""
    out: dict[str, dict] = {}
    for part in _chunks(list(tks)):
        _temp(conn, "ec_tk", ["tk TEXT PRIMARY KEY"], [(t,) for t in part])
        for tk, lo, hi, pcn in conn.execute("""SELECT c.transaction_key,
                    COALESCE(c.statement_from, (SELECT MIN(l.service_date) FROM lines l WHERE l.claim_key = c.claim_key)),
                    COALESCE(c.statement_to, c.statement_from, (SELECT MAX(l.service_date) FROM lines l WHERE l.claim_key = c.claim_key)),
                    c.patient_control_number
                FROM claims c WHERE c.transaction_key IN (SELECT tk FROM temp.ec_tk)"""):
            d = out.setdefault(tk, {"svcs": [], "pcns": []})
            for x in (lo, hi):
                if x:
                    d["svcs"].append(x)
            if pcn:
                d["pcns"].append(pcn)
    _drop(conn, "ec_tk")
    return out


def _remit_covered(boxes: Sequence[dict], paid_on: str, svcs: Sequence[str]) -> str | None:
    """Whether Production must hold this 835 payment's postings: 'service' when each of its claims' service dates is in a
    current service-date box whose as_of is at least 21 days after the payment, 'posted' when posted-date boxes cover the
    payment date - 3 to + 21 days, else None."""
    need = _add(paid_on, UNPOSTED_SERVICE_LAG) or ""
    sboxes = [b for b in boxes if b.get("scope") != "posted"]
    if svcs and all(any((b.get("svc_lo") or "") <= s <= (b.get("svc_hi") or "") and (b.get("as_of") or "") >= need
                        for b in sboxes) for s in svcs):
        return "service"
    lo, hi = _add(paid_on, UNPOSTED_POSTED[0]), _add(paid_on, UNPOSTED_POSTED[1])
    if not lo:
        return None
    cur = lo
    for a, z in sorted((b.get("posted_lo") or "", b.get("posted_hi") or "") for b in boxes if b.get("scope") == "posted"):
        if a > cur:
            break
        cur = max(cur, _add(z, 1) or cur)
        if cur > hi:
            return "posted"
    return None


def _proto(signal: str, category: str, severity: str, actionable: str, title: str, *, key_parts: tuple, claim: dict,
           expected: int | None, shortfall: int | None, evidence: dict, carc: str | None = None, rarc: str | None = None,
           id_signal: str | None = None, deadline: str | None = None, basis: str | None = None, hint: str | None = None,
           source: str = "ema", link: int | None = None, heir_keys: Sequence[tuple] = (), **extra: Any) -> dict:
    """A proto-finding: what the wiring passes to Detector._emit(signal, category, severity, actionable, title, None,
    expected, shortfall, carc, rarc, evidence, key_parts=..., claim=..., id_signal=...), then sets appeal_deadline,
    deadline_basis, hint, source and ema_link on the stored item. `heir_keys` are (id signal, *key parts) of items whose
    work this one inherits."""
    out = {"signal": signal, "id_signal": id_signal, "key_parts": tuple(key_parts), "category": category, "severity": severity,
           "actionable": actionable, "title": title, "claim": claim, "expected_cents": expected, "shortfall_cents": shortfall,
           "carc": carc, "rarc": rarc, "evidence": evidence, "appeal_deadline": deadline, "deadline_basis": basis, "hint": hint,
           "source": source, "ema_link": link, "heir_keys": [tuple(h) for h in heir_keys]}
    out.update(extra)
    return out


def unposted_remits(conn: sqlite3.Connection, *, today: str | None = None, save: bool = True) -> list[dict]:
    """C4: the 835 payments EMA did not post. One REMIT_NOT_POSTED item per 835 payment (not superseded) dated within 365
    days that Production covers (a current service-date box holding every claim's service date with an as_of at least
    21 days after the payment, or posted-date boxes covering 3 days before to 21 days after it), when either:
      - its trace matches no EMA check exactly or loosely, its claims paid over $1, and C1 did not pair it with a
        misnumbered EMA check ("not posted in EMA"); or
      - EMA posted less under it than its claims paid (CLP04, never the deposit) by $25 or more ("posted short").
    Differences of one payer that offset within 60 days (EMA posted one payment's money under another of its checks, or
    left out both a takeback and the payment that replaced it) are reported, never itemized. A difference counts only
    above max($1, 0.5%). An 835 EMA may have posted under a check number a spreadsheet damaged (scientific notation) is
    not listed: it is counted apart (H7 holds a file with many of them). Needs Production at most 14 days old: an absence
    proves nothing otherwise. With `save`, the summary (counts and dollars) and the CSV's rows (no patient data) go to
    meta ema:unposted, which the dashboard's copy reads."""
    _prepare(conn)
    fr = freshness(conn, today)
    t = fr["today"]
    summary: dict = {"as_of": fr["production_as_of"], "fresh": fr["production_fresh"], "n": 0, "cents": 0, "short_n": 0,
                     "short_cents": 0, "offset_n": 0, "offset_cents": 0, "over_n": 0, "over_cents": 0, "damaged_n": 0,
                     "damaged_cents": 0, "elsewhere_n": 0, "elsewhere_cents": 0, "informational_n": 0, "informational_cents": 0,
                     "unfunded_n": 0, "unfunded_cents": 0, "by_group": {}, "by_month": {}, "rows": []}
    if not _full_store(conn) or not fr["production_fresh"] or not _table(conn, "transactions"):
        if save and _full_store(conn):
            _set_meta(conn, UNPOSTED_KEY, summary)
            conn.commit()
        return []
    boxes = fr["boxes"]
    since = _add(t, -UNPOSTED_DAYS)
    _unmatched_traces(conn)
    paired_cents = {tk: cents or 0 for tk, cents in conn.execute(
        "SELECT pair_trace, SUM(ins_paid) FROM ema_checks WHERE pair_trace IS NOT NULL GROUP BY pair_trace")}
    paired = set(paired_cents)
    damaged = [(chk, grp, posted, cents or 0) for chk, grp, posted, cents in conn.execute(
        """SELECT check_no, payer_group, first_posted, ins_paid FROM ema_checks WHERE COALESCE(damaged, 0) = 1
           AND COALESCE(ins_paid, 0) > 0 AND first_posted IS NOT NULL""")]
    conn.execute("DROP TABLE IF EXISTS temp.ec_tt")
    conn.execute("""CREATE TEMP TABLE ec_tt AS SELECT trace_key AS tk, SUM(ins_paid) AS ema FROM ema_checks
                    WHERE trace_key IS NOT NULL GROUP BY trace_key""")
    conn.execute("CREATE INDEX temp.ix_ec_tt ON ec_tt(tk)")
    # a remittance that moved no money (BPR04 'NON', or a $0 deposit) unless a recovery the payer took (PLB WO, 72, ...) used
    # the money up: an informational copy (a plan's administrator reporting what the plan pays), never money to post
    offset_sql = (f"""EXISTS (SELECT 1 FROM plb p WHERE p.transaction_key = t.transaction_key
                              AND upper(COALESCE(p.reason_code, '')) IN ({','.join("'" + c + "'" for c in sorted(OFFSET_PLB))}))"""
                  if _table(conn, "plb") else "0")
    rows = conn.execute(f"""SELECT t.transaction_key, t.payment_date, rw_payer_group(t.payer_name), rw_payer_kind(t.payer_name),
                                  t.payer_name, COALESCE(t.claim_paid_total_cents, 0), t.payment_amount_cents, t.trace_number, x.ema,
                                  t.transaction_key IN (SELECT tk FROM temp.ec_ut),
                                  (upper(COALESCE(t.payment_method, '')) = 'NON' OR COALESCE(t.payment_amount_cents, 0) = 0)
                                      AND NOT {offset_sql}
                           FROM transactions t LEFT JOIN temp.ec_tt x ON x.tk = t.transaction_key
                           WHERE COALESCE(t.superseded, 0) = 0 AND t.payment_date >= ? AND t.trace_number IS NOT NULL
                             AND trim(t.trace_number) <> ''
                           ORDER BY t.payment_date, t.transaction_key""", (since,)).fetchall()
    diffs = []
    for tk, paid_on, grp, kind, payer, paid, deposit, trace, ema, unmatched, informational in rows:
        if unmatched and tk in paired:
            continue                    # C1 paired it with an EMA check under another number
        if not unmatched and ema is None:
            continue                    # matched by a key no EMA check carries as its trace (another payment of that trace)
        if unmatched and damaged and _damaged_could_hold(damaged, trace, grp, paid_on, paid):
            summary["damaged_n"] += 1
            summary["damaged_cents"] += paid
            continue                    # EMA may have posted it under a number a spreadsheet changed: never itemized
        posted = 0 if unmatched else (ema or 0) + paired_cents.get(tk, 0)       # the rest under a misnumbered check
        diff = paid - posted
        if abs(diff) <= max(DIFF_MIN, DIFF_SHARE * abs(paid)):
            continue
        diffs.append({"tk": tk, "paid_on": paid_on, "grp": grp, "kind": kind, "payer": payer, "paid": paid, "deposit": deposit,
                      "trace": trace, "posted": posted, "diff": diff, "unmatched": bool(unmatched), "left": diff,
                      "informational": bool(informational) and diff > 0})
    dates = _claim_dates_of(conn, [d["tk"] for d in diffs])
    for d in diffs:
        info = dates.get(d["tk"]) or {"svcs": [], "pcns": []}
        d["covered"] = _remit_covered(boxes, d["paid_on"], info["svcs"])
        d["svcs"], d["pcns"] = info["svcs"], sorted(set(info["pcns"]))
    diffs = [d for d in diffs if d["covered"]]
    # an informational remittance whose every paid claim another, funded 835 also paid (the plan's own payment beside its
    # administrator's copy), or whose amounts EMA holds under any check: the money is accounted for, nothing to post or chase
    info = [d for d in diffs if d["informational"]]
    if info:
        done = _funded_twins(conn, info)
        done |= _posted_elsewhere(conn, [d for d in info if d["tk"] not in done], any_check=True)
        for d in info:
            if d["tk"] in done:
                summary["informational_n"] += 1
                summary["informational_cents"] += d["diff"]
        diffs = [d for d in diffs if d["tk"] not in done]
    # a payment EMA posted under another check number: every claim of the trace has EMA insurance payments of its amount
    # (within $1) on its own EMA claims, 7 days before to 120 days after the 835's date. Not "never posted": counted apart
    elsewhere = _posted_elsewhere(conn, [d for d in diffs if d["unmatched"]])
    for d in diffs:
        if d["tk"] in elsewhere:
            summary["elsewhere_n"] += 1
            summary["elsewhere_cents"] += d["diff"]
    diffs = [d for d in diffs if d["tk"] not in elsewhere]
    # differences of one payer that offset within 60 days are one payment posted under another number
    who = lambda d: d["payer"] if d["grp"] in MANY_PAYER_GROUPS else d["grp"]      # noqa: E731
    by_payer: dict[str, list] = defaultdict(list)
    for d in diffs:
        by_payer[who(d) or ""].append(d)
    for group in by_payer.values():
        overs = sorted((d for d in group if d["diff"] < 0), key=lambda d: (d["paid_on"], d["tk"]))
        shorts = [d for d in group if d["diff"] > 0]
        for o in overs:
            room = -o["diff"]
            near = sorted((s for s in shorts if s["left"] > 0 and abs(_days(s["paid_on"], o["paid_on"]) or 999) <= OFFSET_DAYS),
                          key=lambda s: (abs(_days(s["paid_on"], o["paid_on"]) or 0), s["paid_on"], s["tk"]))
            for s in near:
                if room <= 0:
                    break
                take = min(room, s["left"])
                s["left"] -= take
                room -= take
            o["left"] = -room
    out: list[dict] = []
    for d in diffs:
        if d["diff"] < 0:
            summary["over_n"] += 1
            summary["over_cents"] += -d["diff"]
            continue
        if d["left"] < d["diff"]:
            summary["offset_n"] += 1
            summary["offset_cents"] += d["diff"] - d["left"]
        left = d["left"]
        svc0 = min(d["svcs"]) if d["svcs"] else None
        claim = {"transaction_key": d["tk"], "payment_date": d["paid_on"], "payer_name": d["payer"], "payer_group": d["grp"],
                 "payer_kind": d["kind"], "patient_control_number": d["pcns"][0] if len(d["pcns"]) == 1 else None,
                 "service_date": svc0, "year": (svc0 or d["paid_on"] or "")[:4] or None, "procedure_code": None, "charge_cents": None}
        ev = {"trace_tail": _tail(d["trace"]), "claims": len(d["pcns"]), "claim_paid_cents": d["paid"], "deposit_cents": d["deposit"],
              "ema_posted_cents": d["posted"], "payment_date": d["paid_on"], "production_as_of": fr["production_as_of"],
              "covered_by": d["covered"], "offset_cents": d["diff"] - left}
        payer = d["payer"] or d["grp"]
        if d["informational"]:
            # an informational remittance reported the claims paid and moved no money, and no funded 835 or EMA posting
            # shows the payment: the money never arrived. Never "post it": ask the payer for it (R14, PM case C54)
            if left <= UNPOSTED_MIN_CENTS:
                continue
            title = (f"{payer} reported {_usd(d['paid'])} paid on {d['paid_on']} (trace ending {_tail(d['trace'])}) on an "
                     "informational remittance that moved no money: the payment never arrived")
            ev.update(informational=True, payment_method="NON" if not (d["deposit"] or 0) else "other")
            ev["steps"] = [f"Ask {payer} (or the plan behind it) whether the payment it reported was issued: date, amount, EFT trace or check number.",
                           "If it was issued, find the deposit and post it in EMA under that trace.",
                           "If it was not, ask them to issue it; the patient's share it reported can be billed meanwhile."]
            hint = (f"The remittance was informational only (no money moved) and no payment followed: ask {payer} for the payment it reported; "
                    "there is nothing to post yet")
            summary["unfunded_n"] += 1
            summary["unfunded_cents"] += left
        elif d["unmatched"]:
            if left <= UNPOSTED_MIN_CENTS:
                continue
            title = f"Paid {_usd(d['paid'])} by {payer} on {d['paid_on']} (trace ending {_tail(d['trace'])}): not posted in EMA"
            ev["steps"] = ["Find the deposit in the bank statement or the payer's portal.",
                           "If EMA posted it under another number, fix the number in EMA.",
                           "If it never arrived, ask the payer to reissue it."]
            hint = "Post the payment: find the deposit, then post it under this trace in EMA"
            summary["n"] += 1
            summary["cents"] += left
            g = summary["by_group"].setdefault(d["grp"] or "Unknown", {"n": 0, "cents": 0})
            g["n"] += 1
            g["cents"] += left
            m = summary["by_month"].setdefault(d["paid_on"][:7], {"n": 0, "cents": 0})
            m["n"] += 1
            m["cents"] += left
        else:
            if left < SHORT_MIN:
                continue
            title = (f"Paid {_usd(d['paid'])} by {payer} on {d['paid_on']} (trace ending {_tail(d['trace'])}): EMA posted "
                     f"{_usd(d['posted'])}, {_usd(left)} short")
            ev.update(short=True, short_cents=left)
            hint = "EMA posted less than the remittance paid: find which claims are missing and post them"
            summary["short_n"] += 1
            summary["short_cents"] += left
        out.append(_proto("REMIT_NOT_POSTED", "fees", "high" if left >= UNPOSTED_HIGH else "medium", "review", title,
                          key_parts=(d["tk"],), claim=claim, expected=left, shortfall=left, evidence=ev, source="835",
                          hint=hint))
        summary["rows"].append([ev["trace_tail"], d["paid_on"], d["payer"], d["grp"], ev["claims"], f"{left / 100:.2f}",
                                "never_arrived" if ev.get("informational") else "short" if ev.get("short") else "not_posted"])
    _drop(conn, "ec_tt", "ec_ut", "ec_ck")
    if save:
        _set_meta(conn, UNPOSTED_KEY, summary)
        conn.commit()
    return out


def _funded_twins(conn: sqlite3.Connection, diffs: list[dict]) -> set[str]:
    """The informational 835 payments (transaction keys) among these whose every paid claim (its claim id) another 835
    paid too, for its amount within $1, from 30 days before to 90 days after: one that moved money (not BPR04 'NON', a
    deposit above $0) and was not superseded. A plan's administrator and the plan itself report one payment twice."""
    out: set[str] = set()
    tks = sorted({d["tk"] for d in diffs})
    if not tks:
        return out
    when = {d["tk"]: d["paid_on"] for d in diffs}
    claims: dict[str, list] = defaultdict(list)
    for part in _chunks(tks):
        _temp(conn, "ec_ft2", ["tk TEXT PRIMARY KEY"], [(x,) for x in part])
        for tk, pcn, paid in conn.execute("""SELECT transaction_key, patient_control_number, COALESCE(paid_cents, 0) FROM claims
                                             WHERE transaction_key IN (SELECT tk FROM temp.ec_ft2)"""):
            claims[tk].append((pcn, paid))
    _drop(conn, "ec_ft2")
    pcns = sorted({pcn for v in claims.values() for pcn, paid in v if pcn and paid > 0})
    funded: dict[str, list] = defaultdict(list)
    for part in _chunks(pcns):
        _temp(conn, "ec_fp", ["pcn TEXT PRIMARY KEY"], [(x,) for x in part])
        for pcn, paid, tk2, on in conn.execute("""SELECT c.patient_control_number, COALESCE(c.paid_cents, 0), t.transaction_key, t.payment_date
                                                  FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key
                                                  WHERE c.patient_control_number IN (SELECT pcn FROM temp.ec_fp)
                                                    AND COALESCE(t.superseded, 0) = 0 AND upper(COALESCE(t.payment_method, '')) <> 'NON'
                                                    AND COALESCE(t.payment_amount_cents, 0) > 0"""):
            funded[pcn].append((paid, tk2, on or ""))
    _drop(conn, "ec_fp")
    for tk, cl in claims.items():
        lo, hi = _add(when.get(tk), -30) or "", _add(when.get(tk), 90) or ""
        paid_claims = [(pcn, paid) for pcn, paid in cl if paid > 0]
        if paid_claims and all(any(tk2 != tk and abs(p2 - paid) <= 100 and lo <= on <= hi for p2, tk2, on in funded.get(pcn or "", []))
                               for pcn, paid in paid_claims):
            out.add(tk)
    return out


def _posted_elsewhere(conn: sqlite3.Connection, diffs: list[dict], *, any_check: bool = False) -> set[str]:
    """The 835 payments (transaction keys) among these whose every claim EMA posted, for its amount within $1, under
    another check number no 835 carries, on the claim's own EMA claims (the map: no conflict, never the ASC's facility
    claim), from 7 days before to 120 days after the 835's payment date. With `any_check`, a check an 835 carries counts
    too (an informational remittance whose money came on the plan's own funded payment, which EMA posted)."""
    out: set[str] = set()
    tks = sorted({d["tk"] for d in diffs})
    if not tks:
        return out
    when = {d["tk"]: d["paid_on"] for d in diffs}
    claims: dict[str, list] = defaultdict(list)
    for part in _chunks(tks):
        _temp(conn, "ec_pe", ["tk TEXT PRIMARY KEY"], [(x,) for x in part])
        for tk, pcn, paid in conn.execute("""SELECT c.transaction_key, c.patient_control_number, COALESCE(c.paid_cents, 0) FROM claims c
                                             WHERE c.transaction_key IN (SELECT tk FROM temp.ec_pe)"""):
            claims[tk].append((pcn, paid))
    _drop(conn, "ec_pe")
    bills = sorted({_bill(pcn) for v in claims.values() for pcn, _p in v if pcn})
    links: dict[str, set] = defaultdict(set)
    for part in _chunks(bills):
        _temp(conn, "ec_peb", ["bill TEXT PRIMARY KEY"], [(b,) for b in part])
        for b, lk in conn.execute("""SELECT bill, link FROM ema_link_map WHERE bill IN (SELECT bill FROM temp.ec_peb)
                                     AND COALESCE(conflict, 0) = 0 AND COALESCE(facility, 0) = 0"""):
            links[b].add(lk)
    _drop(conn, "ec_peb")
    pays: dict[int, list] = defaultdict(list)
    lks = sorted({lk for v in links.values() for lk in v})
    for part in _chunks(lks):
        _temp(conn, "ec_pel", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
        for lk, posted, cents in conn.execute(f"""SELECT link, posted, SUM(COALESCE(ins_paid, 0)) FROM ema_line_payments
                                                 WHERE link IN (SELECT link FROM temp.ec_pel) AND COALESCE(ins_paid, 0) <> 0
                                                   {'' if any_check else 'AND COALESCE(in_835, 0) = 0'}
                                                 GROUP BY link, posted, check_no"""):
            pays[lk].append((posted, cents or 0))
    _drop(conn, "ec_pel")
    for tk, cl in claims.items():
        paid_on = when.get(tk)
        lo, hi = _add(paid_on, -7) or "", _add(paid_on, 120) or ""
        ok = bool(cl)
        for pcn, paid in cl:
            if paid <= 0:
                continue
            got = sum(c for lk in links.get(_bill(pcn) or "", ()) for posted, c in pays.get(lk, []) if lo <= (posted or "") <= hi)
            if abs(got - paid) > 100:
                ok = False
                break
        if ok and any(paid > 0 for _p, paid in cl):
            out.add(tk)
    return out


_SCI_CHECK = re.compile(r"^(\d)(?:\.(\d+))?E\+?(\d{1,2})$", re.I)


def _sci_could_be(check: Any, trace: Any) -> bool:
    """Whether a check number a spreadsheet wrote in scientific notation (9.00001E+11) could be this trace: an all-digit
    trace of that many digits whose leading digits round to the ones shown."""
    m = _SCI_CHECK.match(str(check or "").strip())
    t = str(trace or "").strip().lstrip("0")
    if not m or not t.isdigit():
        return False
    shown = m.group(1) + (m.group(2) or "")
    if len(t) != int(m.group(3)) + 1 or len(shown) > len(t):
        return False
    k = len(shown)
    lead = int(t[:k]) + (1 if len(t) > k and t[k] >= "5" else 0)
    return lead == int(shown)


def _damaged_could_hold(damaged: list[tuple], trace: Any, grp: Any, paid_on: Any, paid: int) -> bool:
    """A check a spreadsheet damaged, first posted in the misnumbered window of this 835 (10 days before to 45 after its
    payment), that could be it: its digits round to the damaged number, or (digits lost) the same payer group posted at
    least the 835's amount under it."""
    lo, hi = _add(paid_on, -MISNUMBER_AFTER) or "", _add(paid_on, MISNUMBER_BEFORE) or ""
    for chk, g, posted, cents in damaged:
        if not (lo <= (posted or "") <= hi):
            continue
        if _sci_could_be(chk, trace):
            return True
        if not _SCI_CHECK.match(str(chk or "").strip()) and g and g == grp and cents >= paid - MISNUMBER_CENTS:
            return True
    return False


def unposted_csv(conn: sqlite3.Connection, *, today: str | None = None) -> Iterator[list]:
    """/api/ema/unposted.csv: the 835 payments EMA has not posted (trace ending, date, payer, amount). No patient data.
    Worked out on the whole store (nothing is written); on the dashboard's copy, the rows detection stored."""
    yield ["trace_ending", "payment_date", "payer", "payer_group", "claims", "amount", "kind"]
    if not _full_store(conn):
        for r in (_meta(conn, UNPOSTED_KEY, {}) or {}).get("rows") or []:
            yield list(r)
        return
    for p in unposted_remits(conn, today=today, save=False):
        ev = p["evidence"]
        yield [ev.get("trace_tail"), ev.get("payment_date"), p["claim"].get("payer_name"), p["claim"].get("payer_group"),
               ev.get("claims"), f"{(p['expected_cents'] or 0) / 100:.2f}", "short" if ev.get("short") else "not_posted"]


# ---------------------------------------------------------------------------------------------- C3: claim statuses
def claim_statuses(conn: sqlite3.Connection, *, claim_ids: Iterable[str] = (), links: Iterable[int] = ()) -> dict:
    """C3a: each claim's newest status across the A/R, Production and the Claim & Claim Status export, by as_of (the A/R
    first on a tie, then Production): {claim id or claim number: {'status', 'class', 'source', 'as_of'}}. A claim status
    is claim-level and undated: it routes an item, it never closes one."""
    _prepare(conn)
    out: dict = {}

    def offer(key: Any, status: Any, source: str, as_of: Any, answered: bool | None = None) -> None:
        if key is None or status is None:
            return
        cur = out.get(key)
        if cur is None or _status_order(source, as_of) > _status_order(cur["source"], cur["as_of"]):
            out[key] = {"status": status, "class": status_class(status, answered), "source": source, "as_of": as_of}
    ids = sorted({str(x) for x in claim_ids if x})
    lks = sorted({int(x) for x in links if x is not None})
    has_rows = _table(conn, "ema_rows")
    src_at = "COALESCE(s.created_at, substr(s.recorded_at, 1, 10))" if _table(conn, "ema_sources") else "NULL"
    src_join = "LEFT JOIN ema_sources s ON s.sha256 = e.source_sha256" if _table(conn, "ema_sources") else ""
    for part in _chunks(ids):
        _temp(conn, "ec_ids", ["cid TEXT PRIMARY KEY"], [(x,) for x in part])
        for cid, st, as_of in conn.execute("""SELECT a.claim_id, a.claim_status, a.as_of FROM ema_ar a
                                              WHERE a.claim_id IN (SELECT cid FROM temp.ec_ids) AND a.claim_status IS NOT NULL"""):
            offer(cid, st, "ar", as_of)
        for cid, st, as_of, ans in conn.execute("""SELECT k.claim_id, l.status, l.as_of, l.ins_answer_rows > 0 FROM ema_claim_links k
                    JOIN ema_lines l ON l.link = k.link WHERE k.claim_id IN (SELECT cid FROM temp.ec_ids) AND l.status IS NOT NULL
                    ORDER BY l.last_posted"""):
            offer(cid, st, "production", as_of, bool(ans))
        if has_rows:
            for cid, st, as_of in conn.execute(f"""SELECT e.claim_id, e.claim_status, {src_at} FROM ema_rows e {src_join}
                        WHERE e.family = 'claim_status' AND e.claim_id IN (SELECT cid FROM temp.ec_ids) AND e.claim_status IS NOT NULL"""):
                offer(cid, st, "export", as_of)
    for part in _chunks(lks):
        _temp(conn, "ec_lk", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
        for lk, st, as_of in conn.execute("""SELECT a.link, a.claim_status, a.as_of FROM ema_ar a
                                             WHERE a.link IN (SELECT link FROM temp.ec_lk) AND a.claim_status IS NOT NULL"""):
            offer(lk, st, "ar", as_of)
        for lk, st, as_of, ans in conn.execute("""SELECT l.link, l.status, l.as_of, l.ins_answer_rows > 0 FROM ema_lines l
                    WHERE l.link IN (SELECT link FROM temp.ec_lk) AND l.status IS NOT NULL ORDER BY l.last_posted"""):
            offer(lk, st, "production", as_of, bool(ans))
    _drop(conn, "ec_ids", "ec_lk")
    return out


_STATUS_RANK = {"ar": 2, "production": 1, "export": 0}


def _status_order(source: Any, as_of: Any) -> tuple:
    """Which status is newer: by the day of its as_of (an export's is a timestamp, a report's a date), the A/R first on a
    tie, then Production, then the Claim & Claim Status export."""
    return (str(as_of or "")[:10], _STATUS_RANK.get(source, -1))


def _claim_id_of_links(conn: sqlite3.Connection, links: Iterable[int]) -> dict[int, str]:
    """Each claim number's claim id, from the A/R's pairs (the newest), else from the map."""
    out: dict[int, str] = {}
    lks = sorted({int(x) for x in links if x is not None})
    for part in _chunks(lks):
        _temp(conn, "ec_lk2", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
        for lk, cid in conn.execute("""SELECT m.link, m.claim_id FROM ema_link_map m WHERE m.link IN (SELECT link FROM temp.ec_lk2)
                                       AND m.claim_id IS NOT NULL AND COALESCE(m.conflict, 0) = 0"""):
            out[lk] = cid
        for lk, cid in conn.execute("""SELECT link, claim_id FROM ema_claim_links WHERE link IN (SELECT link FROM temp.ec_lk2)
                                       ORDER BY last_seen"""):
            out[lk] = cid
    _drop(conn, "ec_lk2")
    return out


def _first_charges(conn: sqlite3.Connection, links: Iterable[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    lks = sorted({int(x) for x in links if x is not None})
    for part in _chunks(lks):
        _temp(conn, "ec_fc", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
        for lk, fc in conn.execute("""SELECT link, MIN(first_charge_posted) FROM ema_lines WHERE link IN (SELECT link FROM temp.ec_fc)
                                      GROUP BY link"""):
            if fc:
                out[lk] = fc
    _drop(conn, "ec_fc")
    return out


def _bill_created(conn: sqlite3.Connection, claim_ids: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if not _table(conn, "ema_rows"):
        return out
    ids = sorted({x for x in claim_ids if x})
    for part in _chunks(ids):
        _temp(conn, "ec_bc", ["cid TEXT PRIMARY KEY"], [(x,) for x in part])
        for cid, made in conn.execute("""SELECT claim_id, MIN(substr(bill_created, 1, 10)) FROM ema_rows
                                         WHERE claim_id IN (SELECT cid FROM temp.ec_bc) AND bill_created IS NOT NULL GROUP BY claim_id"""):
            if made:
                out[cid] = made
    _drop(conn, "ec_bc")
    return out


def _status_claims(conn: sqlite3.Connection, cls: str) -> dict[str, dict]:
    """Claims whose A/R rows or Production lines carry a status of this class, grouped by claim (the claim id when known,
    else 'link:<n>'), with their lines."""
    claims: dict[str, dict] = {}
    for cid, lk, svc, cpt, amt, st, as_of, grp, kind, payer, fac in conn.execute(
            """SELECT claim_id, link, svc, cpt, exposure_cents, claim_status, as_of, payer_group, payer_kind,
                      CASE WHEN lower(coverage) LIKE 'secondary%' AND secondary_payer IS NOT NULL THEN secondary_payer ELSE primary_payer END,
                      facility
               FROM ema_ar WHERE lower(party) LIKE 'insur%' AND status_class = ? AND (claim_id IS NOT NULL OR link IS NOT NULL)""", (cls,)):
        key = cid or f"link:{lk}"
        k = claims.setdefault(key, {"claim_id": cid, "links": set(), "lines": {}, "statuses": set(), "group": grp, "kind": kind,
                                    "payer": payer, "facility": 0, "sources": set()})
        if lk:
            k["links"].add(lk)
        k["lines"][(cpt, svc)] = max(k["lines"].get((cpt, svc), 0), amt or 0)
        k["statuses"].add(st)
        k["facility"] = max(k["facility"], fac or 0)
        k["sources"].add("ar")
    for lk, svc, cpt, bal, chg, st, grp, kind, payer, fac, cid in conn.execute(
            """SELECT l.link, l.svc, l.cpt, l.balance, l.charges, l.status, l.payer_group, l.payer_kind, l.primary_payer,
                      COALESCE(l.facility, 0), l.claim_id
               FROM ema_lines l WHERE l.status_class = ? AND (COALESCE(l.flags, 0) & 17) = 0 AND COALESCE(l.charges, 0) > 0""", (cls,)):
        key = cid or f"link:{lk}"
        if key not in claims:
            # a claim the A/R also lists under its id is the same claim
            key = next((k for k, v in claims.items() if lk in v["links"]), key)
        k = claims.setdefault(key, {"claim_id": cid, "links": set(), "lines": {}, "statuses": set(), "group": grp, "kind": kind,
                                    "payer": payer, "facility": 0, "sources": set()})
        k["links"].add(lk)
        if (cpt, svc) not in k["lines"]:
            k["lines"][(cpt, svc)] = bal if bal and bal > 0 else (chg or 0)
        k["statuses"].add(st)
        k["facility"] = max(k["facility"], fac or 0)
        k["sources"].add("production")
        k["group"] = k["group"] or grp
        k["kind"] = k["kind"] or kind
        k["payer"] = k["payer"] or payer
    return claims


def _filing(grp: str | None, kind: str | None, svc: str | None) -> tuple[str | None, str]:
    rule = filing_rule(grp, kind)
    limit = rule["label"] + ("" if rule["verified"] else ", not confirmed against the payer's current manual")
    return _add(svc, rule["days"]), limit


def _priced(rate: Callable | None, grp: str | None, lines: Iterable[tuple]) -> int | None:
    """What the payer usually pays for these lines (code, date, units), or None when no code has a usual rate."""
    if rate is None:
        return None
    got, any_ = 0, False
    for cpt, svc, units in lines:
        try:
            r = rate(grp, cpt, (svc or "")[:4])
        except Exception:       # noqa: BLE001  (a rule that cannot price a code prices nothing)
            r = None
        if r:
            got += int(r) * max(1, int(abs(units or 1)))
            any_ = True
    return got if any_ else None


def not_sent(conn: sqlite3.Connection, *, today: str | None = None, rate: Callable | None = None,
             skip_claim_ids: Iterable[str] = ()) -> list[dict]:
    """C3b: claims that never left EMA. A claim (A/R rows or Production lines) whose newest status, across the reports,
    is Batched for Printing, Batched for Sending or Invalid, for 14 days or more (3 for Invalid) since its first charge
    posting, else the bill's creation date, else the date of service, as of that status's own date. The status must be
    at most 14 days old. One NOT_SENT item per claim, keyed EMA_OPEN_CLAIM by its claim id (else 'link:<n>'), so work
    follows the claim between NO_ANSWER, REJECTED_PRE_PAYER and NOT_SENT. Dated by the payer's first-filing limit from
    the date of service; high severity past 30 days."""
    _prepare(conn)
    t = _today(today)
    skip = {x for x in skip_claim_ids if x}
    claims = _status_claims(conn, "not_sent")
    if not claims:
        return []
    statuses = claim_statuses(conn, claim_ids=[k["claim_id"] for k in claims.values() if k["claim_id"]],
                              links=[lk for k in claims.values() for lk in k["links"]])
    first_charge = _first_charges(conn, [lk for k in claims.values() for lk in k["links"]])
    made = _bill_created(conn, [k["claim_id"] for k in claims.values() if k["claim_id"]])
    out = []
    for key, k in sorted(claims.items()):
        if k["claim_id"] and k["claim_id"] in skip:
            continue
        st = statuses.get(k["claim_id"]) if k["claim_id"] else None
        if st is None:
            st = max((statuses[lk] for lk in k["links"] if lk in statuses), key=lambda s: _status_order(s["source"], s["as_of"]),
                     default=None)
        if st is None or st["class"] != "not_sent":
            continue                    # a newer report shows the claim moved on
        as_of = st["as_of"] or t
        if (_days(as_of, t) or 0) > PRODUCTION_FRESH_DAYS:
            continue                    # too old to say the claim is still waiting in EMA
        days_ = sorted({svc for (_c, svc) in k["lines"] if svc})
        if not days_:
            continue
        fc = min((first_charge[lk] for lk in k["links"] if lk in first_charge), default=None)
        clock, clock_basis = ((fc, "the first charge posting") if fc else (made.get(k["claim_id"] or ""), "the date EMA created the bill")
                              if made.get(k["claim_id"] or "") else (days_[0], "the date of service"))
        word = (st["status"] or "").strip()
        need = INVALID_DAYS if word.lower() == "invalid" else NOT_SENT_DAYS
        age = _days(clock, as_of)
        if age is None or age < need:
            continue
        grp, kind = k["group"] or "Unknown", k["kind"] or "other"
        file_by, limit = _filing(grp, kind, days_[0])
        lines = sorted(k["lines"].items(), key=lambda kv: (kv[0][1] or "", kv[0][0] or ""))
        codes = sorted({c for (c, _s) in k["lines"] if c})
        label = ", ".join(codes[:5]) + ("…" if len(codes) > 5 else "")
        balance = sum(v for v in k["lines"].values() if v)
        expected = _priced(rate, grp, [(c, s, 1) for (c, s) in k["lines"]])
        age_now = _days(clock, t) or age
        top = max(lines, key=lambda kv: kv[1] or 0)[0][0] if lines else None
        lk0 = min(k["links"]) if k["links"] else None
        title = f"Never left EMA: {word} since {clock}: {label}"
        if k["facility"]:
            title = "ASC facility claim: " + title
        out.append(_proto(
            "NOT_SENT", "denial", "high" if age_now > 30 else "medium", "correct_and_resubmit", title,
            id_signal="EMA_OPEN_CLAIM", key_parts=(k["claim_id"] or key,),
            claim={"patient_control_number": k["claim_id"], "payer_name": k["payer"], "payer_group": grp, "payer_kind": kind,
                   "service_date": days_[0], "year": days_[0][:4], "procedure_code": top, "charge_cents": balance or None},
            expected=expected, shortfall=expected, deadline=file_by,
            basis=f"Send it so that it reaches the payer by this date: {limit}",
            evidence={"codes": codes, "lines": [[c, s, v] for (c, s), v in lines], "ema_status": word,
                      "status_source": st["source"], "status_as_of": st["as_of"], "since": clock, "since_basis": clock_basis,
                      "days_waiting": age_now, "balance_cents": balance, "ema_links": sorted(k["links"]), "not_sent": True},
            hint=f"EMA shows the claim as {word} since {clock}: send it, or fix what holds it",
            link=lk0, heir_keys=[("EMA_OPEN_CLAIM", f"link:{lk}") for lk in sorted(k["links"])] if k["claim_id"] else []))
    return out


def rejected_claims(conn: sqlite3.Connection, *, today: str | None = None, rate: Callable | None = None,
                    skip_claim_ids: Iterable[str] = ()) -> list[dict]:
    """C3f: claims the A/R (or Production) newest calls Rejected that the weekly exports did not already list: one
    REJECTED_PRE_PAYER item each, keyed EMA_OPEN_CLAIM by the claim id, dated by the first-filing limit. The status must
    be at most 14 days old."""
    _prepare(conn)
    t = _today(today)
    skip = {x for x in skip_claim_ids if x}
    claims = _status_claims(conn, "rejected")
    if not claims:
        return []
    statuses = claim_statuses(conn, claim_ids=[k["claim_id"] for k in claims.values() if k["claim_id"]],
                              links=[lk for k in claims.values() for lk in k["links"]])
    out = []
    for key, k in sorted(claims.items()):
        if k["claim_id"] and k["claim_id"] in skip:
            continue
        st = statuses.get(k["claim_id"]) if k["claim_id"] else None
        if st is None:
            st = max((statuses[lk] for lk in k["links"] if lk in statuses), key=lambda s: _status_order(s["source"], s["as_of"]),
                     default=None)
        if st is None or st["class"] != "rejected" or (_days(st["as_of"] or t, t) or 0) > PRODUCTION_FRESH_DAYS:
            continue
        days_ = sorted({svc for (_c, svc) in k["lines"] if svc})
        if not days_:
            continue
        grp, kind = k["group"] or "Unknown", k["kind"] or "other"
        file_by, limit = _filing(grp, kind, days_[0])
        codes = sorted({c for (c, _s) in k["lines"] if c})
        label = ", ".join(codes[:5]) + ("…" if len(codes) > 5 else "")
        expected = _priced(rate, grp, [(c, s, 1) for (c, s) in k["lines"]])
        soon = bool(file_by) and file_by <= (_add(t, 30) or "")
        title = f"Rejected before reaching {grp} (EMA: {st['status']}): {label}"
        if k["facility"]:
            title = "ASC facility claim: " + title
        out.append(_proto(
            "REJECTED_PRE_PAYER", "denial", "high" if soon else "medium", "correct_and_resubmit", title,
            id_signal="EMA_OPEN_CLAIM", key_parts=(k["claim_id"] or key,),
            claim={"patient_control_number": k["claim_id"], "payer_name": k["payer"], "payer_group": grp, "payer_kind": kind,
                   "service_date": days_[0], "year": days_[0][:4],
                   "procedure_code": max(k["lines"].items(), key=lambda kv: kv[1] or 0)[0][0],
                   "charge_cents": sum(v for v in k["lines"].values() if v) or None},
            expected=expected, shortfall=expected, deadline=file_by,
            basis=f"Correct it and send it again so that it reaches the payer by this date: {limit}",
            evidence={"codes": codes, "lines": [[c, s, v] for (c, s), v in sorted(k["lines"].items(), key=lambda kv: (kv[0][1] or "", kv[0][0] or ""))],
                      "ema_status": st["status"], "status_source": st["source"], "status_as_of": st["as_of"],
                      "ema_links": sorted(k["links"])},
            link=min(k["links"]) if k["links"] else None,
            heir_keys=[("EMA_OPEN_CLAIM", f"link:{lk}") for lk in sorted(k["links"])] if k["claim_id"] else []))
    return out


_LAST_POST: dict = {}


def _group_last_posting(conn: sqlite3.Connection) -> dict:
    """Each payer group's newest EMA insurance posting (from every check EMA posted)."""
    key = (id(conn), conn.total_changes)
    got = _LAST_POST.get(key)
    if got is None:
        got = {g: d for g, d in conn.execute("SELECT payer_group, MAX(last_posted) FROM ema_checks GROUP BY payer_group") if g}
        _LAST_POST.clear()
        _LAST_POST[key] = got
    return got


def answers_through(conn: sqlite3.Connection, payer_group: str | None, newest_835: str | None, *,
                    today: str | None = None) -> str | None:
    """C3d: the day up to which a payer group's answers are known: the newest 835, or EMA's postings through Production's
    as_of less 8 days when Production is at most 14 days old and the group has an EMA insurance posting in the 30 days
    before that as_of (later of the two)."""
    _prepare(conn)
    fr = freshness(conn, today)
    prod = fr["production_as_of"]
    if not prod or not fr["production_fresh"]:
        return newest_835
    last = _by_group(_group_last_posting(conn), payer_group, None)
    if not last or last < (_add(prod, -30) or ""):
        return newest_835
    cand = _add(prod, -DEFAULT_LAG_P95)
    return max(x for x in (newest_835, cand) if x)


def _separate_checks(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS temp.ec_sp")
    conn.execute("""CREATE TEMP TABLE ec_sp AS SELECT check_no, payer FROM ema_checks WHERE cause = 'separate_payee'""")
    conn.execute("CREATE INDEX temp.ix_ec_sp ON ec_sp(check_no, payer)")


def claim_answers(conn: sqlite3.Connection, claims: list[dict]) -> dict:
    """C3e: whether EMA shows each claim answered after it was sent. claims: [{'claim_id', 'sent', 'lines': [(svc, cpt,
    charge cents)], 'payer_group'}]. The claim's EMA claim is found by its claim id (the A/R's pairs), else its bill (the
    map), else the content bridge: the claim's (date, code, charge) rows equal the charge rows of exactly one link that
    is not the ASC's facility claim, with the payer group agreeing (E8). Answered means a net insurance payment, a $0 check
    row (a $0 answer) or a payer's non-routine reason, posted after the claim was sent, on that EMA claim. Payments under
    the ASC's separate-payee checks never count, and a status alone never answers.
    Returns {claim_id: {'answered', 'how', 'on', 'paid_cents', 'link', 'bridge'}}."""
    _prepare(conn)
    out: dict = {}
    items = [c for c in claims if c.get("claim_id")]
    _separate_checks(conn)
    for part in _chunks(items):
        found: dict[str, tuple] = {}
        _temp(conn, "ec_ca", ["cid TEXT PRIMARY KEY", "sent TEXT", "grp TEXT"],
              [(c["claim_id"], c.get("sent"), c.get("payer_group")) for c in part])
        agg: dict[tuple, int] = defaultdict(int)       # (claim id, date, code) -> charge: EMA keeps one line per date and code
        for c in part:
            for ln in c.get("lines") or []:
                if ln and len(ln) >= 3 and ln[0] and ln[1]:
                    agg[(c["claim_id"], ln[0], ln[1])] += int(ln[2] or 0)
        rows = [(cid, svc, cpt, chg) for (cid, svc, cpt), chg in sorted(agg.items())]
        _temp(conn, "ec_cl", ["cid TEXT", "svc TEXT", "cpt TEXT", "charge INTEGER"], rows, ["cid", "svc, cpt, charge"])
        for cid, lk in conn.execute("""SELECT a.cid, k.link FROM temp.ec_ca a JOIN ema_claim_links k ON k.claim_id = a.cid
                                       WHERE k.link IS NOT NULL AND rw_is_facility(k.claim_id) = 0
                                         AND k.link NOT IN (SELECT link FROM ema_link_map WHERE COALESCE(facility, 0) = 1)"""):
            found[cid] = (lk, "claim_id")
        for cid, lk in conn.execute("""SELECT a.cid, MIN(m.link) FROM temp.ec_ca a JOIN ema_link_map m ON m.bill = rw_bill(a.cid)
                                       AND COALESCE(m.conflict, 0) = 0 AND COALESCE(m.facility, 0) = 0 GROUP BY a.cid"""):
            if cid not in found and lk is not None:
                found[cid] = (lk, "bill")
        need = {c["claim_id"]: c for c in part if c["claim_id"] not in found}
        if need:
            n_keys = Counter(r[0] for r in rows if r[0] in need)
            hits = conn.execute("""SELECT x.cid, l.link, COUNT(*), MAX(l.payer_group) FROM temp.ec_cl x
                                   JOIN ema_lines l ON l.svc = x.svc AND l.cpt = x.cpt AND l.charges = x.charge
                                   WHERE COALESCE(l.facility, 0) = 0 AND (COALESCE(l.flags, 0) & 17) = 0
                                   GROUP BY x.cid, l.link""").fetchall()
            cand = [h for h in hits if h[0] in need and h[2] == n_keys.get(h[0])]
            links = sorted({h[1] for h in cand})
            charge_lines: dict[int, int] = {}
            for lpart in _chunks(links):
                _temp(conn, "ec_bl", ["link INTEGER PRIMARY KEY"], [(x,) for x in lpart])
                for lk, n in conn.execute("""SELECT link, COUNT(*) FROM ema_lines WHERE link IN (SELECT link FROM temp.ec_bl)
                                             AND COALESCE(charges, 0) > 0 GROUP BY link"""):
                    charge_lines[lk] = n
            by_claim: dict[str, list] = defaultdict(list)
            for cid, lk, n, grp in cand:
                if charge_lines.get(lk) == n and (not need[cid].get("payer_group") or not grp or _same_payer(grp, need[cid]["payer_group"])):
                    by_claim[cid].append(lk)
            for cid, lks in by_claim.items():
                if len(lks) == 1:
                    found[cid] = (lks[0], "content")
        for c in part:
            cid = c["claim_id"]
            got = found.get(cid)
            res = {"answered": False, "how": None, "on": None, "paid_cents": 0, "link": got[0] if got else None,
                   "bridge": got[1] if got else None}
            out[cid] = res
        _temp(conn, "ec_al", ["cid TEXT", "link INTEGER", "sent TEXT"],
              [(cid, lk, next((c.get("sent") for c in part if c["claim_id"] == cid), None) or "")
               for cid, (lk, _b) in found.items()], ["link"])
        for cid, posted, cents, zero in conn.execute("""SELECT a.cid, y.posted, y.ins_paid, y.check_no <> '' AND COALESCE(y.ins_paid, 0) = 0
                    FROM temp.ec_al a JOIN ema_line_payments y ON y.link = a.link
                    WHERE y.posted > a.sent AND NOT EXISTS (SELECT 1 FROM temp.ec_sp s WHERE s.check_no = y.check_no AND s.payer = y.payer)
                    ORDER BY y.posted"""):
            r = out[cid]
            if zero:
                if not r["answered"]:
                    r.update(answered=True, how="zero_check", on=posted)
            else:
                r["paid_cents"] += cents or 0
        for cid, r in out.items():
            if r["paid_cents"] > 0 and r["how"] != "paid":
                first = conn.execute("""SELECT MIN(y.posted) FROM temp.ec_al a JOIN ema_line_payments y ON y.link = a.link
                                        WHERE a.cid = ? AND y.posted > a.sent AND COALESCE(y.ins_paid, 0) > 0
                                        AND NOT EXISTS (SELECT 1 FROM temp.ec_sp s WHERE s.check_no = y.check_no AND s.payer = y.payer)""",
                                     (cid,)).fetchone()[0]
                if not r["answered"] or (first and first <= (r["on"] or "9999")):
                    r.update(answered=True, how="paid", on=first)
        for cid, posted in conn.execute(f"""SELECT a.cid, MIN(z.first_posted) FROM temp.ec_al a JOIN ema_line_reasons z ON z.link = a.link
                    WHERE z.first_posted > a.sent AND z.grp NOT IN ('', 'PR') AND z.reason_class NOT IN ({_routine_sql()})
                    GROUP BY a.cid"""):
            r = out[cid]
            if not r["answered"] or (posted and posted < (r["on"] or "9999")):
                r.update(answered=True, how="carc", on=posted)
    _drop(conn, "ec_ca", "ec_cl", "ec_bl", "ec_al", "ec_sp")
    return out


def _routine_sql() -> str:
    return ",".join(f"'{c}'" for c in ROUTINE_CLASSES)


def unanswered_claims(conn: sqlite3.Connection, *, today: str | None = None, skip_claim_ids: Iterable[str] = (),
                      rate: Callable | None = None) -> list[dict]:
    """G8: claims no payer answered, found in Production (the A/R lists only 30 days of activity). One NO_ANSWER item per
    EMA claim when all hold: it has insurance charge rows (no product or patient-only rows); no insurance payment, $0
    answer or reason code was ever posted on it; its balance is above $0; its status is not one of the not-sent,
    rejected or voided classes; its bill has no 835 claim (by the map, or by an 835 line for its patient, date and code);
    it is due (first charge posting + the longer of NO_ANSWER_DAYS and the group's p95 from charge to first answer) by
    Production's as_of; Production is at most 14 days old and the payer group has an EMA insurance posting in the 30 days
    before that as_of. Keyed EMA_OPEN_CLAIM by the claim id when the A/R knows it, else 'link:<n>'; a claim id the weekly
    exports already listed is skipped (that path wins). The ASC's facility claims are listed and titled as such."""
    _prepare(conn)
    fr = freshness(conn, today)
    t = fr["today"]
    prod = fr["production_as_of"]
    if not prod or not fr["production_fresh"]:
        return []
    skip = {x for x in skip_claim_ids if x}
    last = _group_last_posting(conn)
    tat = _turnaround(conn).get("groups") or {}
    rows = conn.execute("""SELECT link, MIN(svc), MIN(first_charge_posted), SUM(COALESCE(charges, 0)), SUM(COALESCE(balance, 0)),
                                  SUM(COALESCE(ins_answer_rows, 0)), MAX(COALESCE(flags, 0) & 17), MAX(payer_group), MAX(payer_kind),
                                  MAX(primary_payer), MAX(COALESCE(facility, 0)), MAX(bill), MAX(name_key),
                                  SUM(COALESCE(ins_charges, 0))
                           FROM ema_lines GROUP BY link
                           HAVING SUM(COALESCE(ins_answer_rows, 0)) = 0 AND SUM(COALESCE(balance, 0)) > 0
                              AND MAX(COALESCE(flags, 0) & 17) = 0 AND SUM(COALESCE(charges, 0)) > 0
                              AND MIN(first_charge_posted) IS NOT NULL""").fetchall()
    cands = []
    for (lk, svc0, fc, charges, bal, _ans, _fl, grp, kind, payer, fac, bill, nk, ins_chg) in rows:
        if not ins_chg:
            continue
        p95 = int(_by_group(tat, grp, {}).get("charge_p95") or DEFAULT_TURNAROUND) if tat else DEFAULT_TURNAROUND
        due = _add(fc, max(NO_ANSWER_DAYS, p95))
        if not due or due > prod:
            continue
        lp = _by_group(last, grp, None)
        if not lp or lp < (_add(prod, -30) or ""):
            continue
        cands.append({"link": lk, "svc": svc0, "fc": fc, "charges": charges, "balance": bal, "group": grp or "Unknown",
                      "kind": kind or "other", "payer": payer, "facility": fac, "bill": bill, "p95": p95, "due": due})
    if not cands:
        return []
    statuses = claim_statuses(conn, links=[c["link"] for c in cands])
    cands = [c for c in cands if (statuses.get(c["link"]) or {}).get("class") not in ("not_sent", "rejected", "void")]
    # the claim's lines, and whether an 835 answered its bill or its patient-day-codes
    lines: dict[int, list] = defaultdict(list)
    for part in _chunks([c["link"] for c in cands]):
        _temp(conn, "ec_ul", ["link INTEGER PRIMARY KEY"], [(c,) for c in part])
        for lk, svc, cpt, chg, units, nk in conn.execute("""SELECT link, svc, cpt, charges, units, name_key FROM ema_lines
                                                            WHERE link IN (SELECT link FROM temp.ec_ul) ORDER BY svc, cpt"""):
            lines[lk].append((svc, cpt, chg or 0, units, nk))
    _drop(conn, "ec_ul")
    bills835 = _bills_with_835(conn, {c["bill"] for c in cands if c["bill"]})
    keys = {(ln[4], ln[0], ln[1]) for c in cands for ln in lines[c["link"]] if ln[4] is not None}
    seen835 = _name_day_codes_835(conn, keys)
    cid_of = _claim_id_of_links(conn, [c["link"] for c in cands])
    out = []
    for c in cands:
        lk = c["link"]
        ls = lines[lk]
        if c["bill"] and c["bill"] in bills835:
            continue
        if any((ln[4], ln[0], ln[1]) in seen835 for ln in ls if ln[4] is not None):
            continue
        cid = cid_of.get(lk)
        if cid and cid in skip:
            continue
        codes = sorted({ln[1] for ln in ls if ln[1]})
        label = ", ".join(codes[:5]) + ("…" if len(codes) > 5 else "")
        grp, kind = c["group"], c["kind"]
        file_by, limit = _filing(grp, kind, c["svc"])
        waited = _days(c["fc"], prod)
        expected = _priced(rate, grp, [(ln[1], ln[0], ln[3]) for ln in ls if (ln[2] or 0) > 0])
        soon = bool(file_by) and file_by <= (_add(t, 30) or "")
        title = f"Charged {c['fc']}, no answer from {grp} in {waited} days: {label}"
        if c["facility"]:
            title = "ASC facility claim: " + title
        out.append(_proto(
            "NO_ANSWER", "denial", "high" if soon else "medium", "review", title, id_signal="EMA_OPEN_CLAIM",
            key_parts=(cid or f"link:{lk}",),
            claim={"patient_control_number": cid, "payer_name": c["payer"], "payer_group": grp, "payer_kind": kind,
                   "service_date": c["svc"], "year": (c["svc"] or "")[:4] or None,
                   "procedure_code": max(ls, key=lambda ln: ln[2] or 0)[1] if ls else None, "charge_cents": c["charges"] or None},
            expected=expected, shortfall=expected, deadline=file_by,
            basis=f"If the payer has no record of the claim, it must reach the payer by this date: {limit}",
            evidence={"codes": codes, "lines": [[ln[1], ln[0], ln[2]] for ln in ls], "charged": c["fc"], "answer_due": c["due"],
                      "days_waiting": waited, "production_as_of": prod, "turnaround_days": c["p95"],
                      "turnaround": turnaround_text(grp, c["p95"]) + " of the charge being posted",
                      "balance_cents": c["balance"], "source": "production", "ema_link": lk},
            hint=("No payment, $0 answer or reason code is posted in EMA for this claim: look it up on the payer's portal "
                  "before sending it again"),
            link=lk, heir_keys=[("EMA_OPEN_CLAIM", f"link:{lk}")] if cid else []))
    return out


def _bills_with_835(conn: sqlite3.Connection, bills: Iterable[str]) -> set[str]:
    """The bills among these that have an 835 claim (any submission)."""
    got: set[str] = set()
    bl = sorted({b for b in bills if b})
    for part in _chunks(bl):
        _temp(conn, "ec_b", ["bill TEXT PRIMARY KEY", "hi TEXT"], [(b, b + "~") for b in part])
        for (b,) in conn.execute("""SELECT DISTINCT b.bill FROM temp.ec_b b JOIN claims c ON c.patient_control_number >= b.bill
                                    AND c.patient_control_number < b.hi WHERE rw_bill(c.patient_control_number) = b.bill"""):
            got.add(b)
    _drop(conn, "ec_b")
    return got


def _paid_on_other_links(conn: sqlite3.Connection, keys: set[tuple]) -> dict[tuple, dict[int, int]]:
    """{(name key, date, code): {EMA claim number: insurance money}} for these keys, on claims that are not the ASC's
    facility claims: what each of EMA's claims for that patient, date and code was paid."""
    out: dict[tuple, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    nks = sorted({k[0] for k in keys if k[0] is not None})
    if not nks:
        return out
    for part in _chunks(nks):
        _temp(conn, "ec_pk", ["name_key INTEGER PRIMARY KEY"], [(x,) for x in part])
        for nk, svc, cpt, lk, cents in conn.execute(
                """SELECT y.name_key, y.svc, y.cpt, y.link, SUM(COALESCE(y.ins_paid, 0)) FROM ema_line_payments y
                   LEFT JOIN ema_link_map m ON m.link = y.link
                   WHERE y.name_key IN (SELECT name_key FROM temp.ec_pk) AND COALESCE(m.facility, 0) = 0
                   GROUP BY y.name_key, y.svc, y.cpt, y.link"""):
            if (nk, svc, cpt) in keys and cents:
                out[(nk, svc, cpt)][lk] += cents
    _drop(conn, "ec_pk")
    return out


def _name_day_codes_835(conn: sqlite3.Connection, keys: set[tuple]) -> set[tuple]:
    """The (name key, date, code) keys among these that an 835 line carries (its code or original code; the corrected
    name counts too)."""
    if not keys or not _table(conn, "lines"):
        return set()
    dates = sorted({k[1] for k in keys if k[1]})
    codes = sorted({k[2] for k in keys if k[2]})
    _temp(conn, "ec_nd", ["svc TEXT PRIMARY KEY"], [(d,) for d in dates])
    _temp(conn, "ec_nc", ["cpt TEXT PRIMARY KEY"], [(c,) for c in codes])
    found: set[tuple] = set()
    cur = conn.execute("""SELECT rw_name_key(c.patient_last, c.patient_first),
                                 CASE WHEN c.corrected_patient_last IS NOT NULL OR c.corrected_patient_first IS NOT NULL
                                      THEN rw_name_key(COALESCE(c.corrected_patient_last, c.patient_last),
                                                       COALESCE(c.corrected_patient_first, c.patient_first)) END,
                                 l.service_date, l.procedure_code, l.original_procedure_code
                          FROM lines l JOIN claims c ON c.claim_key = l.claim_key
                          WHERE l.service_date IN (SELECT svc FROM temp.ec_nd)
                            AND (l.procedure_code IN (SELECT cpt FROM temp.ec_nc) OR l.original_procedure_code IN (SELECT cpt FROM temp.ec_nc))""")
    while True:
        rows = cur.fetchmany(5000)
        if not rows:
            break
        for nk, cnk, svc, code, ocode in rows:
            for n in (nk, cnk):
                if n is None:
                    continue
                for cd in (code, ocode):
                    if cd and (n, svc, cd) in keys:
                        found.add((n, svc, cd))
    _drop(conn, "ec_nd", "ec_nc")
    return found


# ------------------------------------------------------------------------------------------ G3: an item's line outcome
def _targets(f: dict) -> list[tuple]:
    """The EMA lines an item is about, as (index, service date, [codes in the order EMA may hold them], link or None):
    the billed code first, then the original code, then the paid code (G3)."""
    ev = _ev(f)
    if f.get("signal") == "EMA_DENIED":
        lk = f.get("ema_link") or ev.get("ema_link")
        out = []
        for i, ln in enumerate(ev.get("lines") or []):
            if isinstance(ln, (list, tuple)) and len(ln) >= 2 and ln[0] and ln[1]:
                out.append((i, ln[1], [ln[0]], lk))
        return out
    svc = f.get("service_date")
    if f.get("line_key"):
        codes = []
        for c in (f.get("billed_code"), f.get("original_procedure_code"), f.get("procedure_code")):
            if c and c not in codes:
                codes.append(c)
        return [(0, svc, codes, None)] if codes and svc else []
    pairs = [p for p in ev.get("unpaid") or [] if isinstance(p, (list, tuple)) and len(p) == 2 and p[0]]
    if pairs:
        return [(i, p[1] or svc, [p[0]], None) for i, p in enumerate(pairs) if (p[1] or svc)]
    return [(i, svc, [c], None) for i, c in enumerate(ev.get("codes") or []) if c and svc]


def _wanted(f: dict) -> bool:
    if f.get("stale"):
        return False
    if f.get("signal") == "EMA_DENIED":
        return True
    return f.get("category") in MONEY_CATEGORIES and f.get("signal") not in CLAIM_SIGNALS


def _threshold(f: dict) -> int:
    """What a later payment must reach to count as paying the item: half of what it is expected to bring, or for a
    downcoding or underpayment 95% of the shortfall; any payment when neither is known."""
    if f.get("category") in SHORT_CATEGORIES:
        s = f.get("shortfall_cents") or 0
        return int(math.ceil(SHORT_SHARE * s)) if s > 0 else 1
    e = f.get("expected_cents") or f.get("shortfall_cents") or 0
    return int(math.ceil(CLOSE_SHARE * e)) if e > 0 else 1


def _pattern(f: dict, carc: str | None) -> str:
    mods = (f.get("modifiers") or "").split(",")[0].strip()
    return f"{f.get('payer_group') or ''}|{f.get('procedure_code') or ''}|{mods}|{carc or ''}"


_CARC = re.compile(r"^\s*(CO|PR|OA|PI|CR)?\s*-?\s*([A-Z0-9]{1,4})\s*$", re.I)


def _carc_key(text: Any) -> tuple[str, str]:
    m = _CARC.match(str(text or ""))
    return ((m.group(1) or "").upper(), m.group(2).upper()) if m else ("", str(text or "").strip().upper())


def line_outcomes(conn: sqlite3.Connection, findings: Iterable[dict], *, code_once: Callable[[dict], bool],
                  today: str | None = None) -> dict[str, dict]:
    """G3: one timeline per item, from the postings on its own EMA line, so every check reads one dollar one way. The
    line is the item's bill's EMA claim (the map, no conflict, never the ASC's facility claim) under the billed code
    first, then the original code, then the paid code; an EMA-only item names its own claim. The anchor is the day EMA
    posted the item's own remittance on that line, else the 835's payment date plus the group's median posting lag.
    Payments are split: EMA-only (no 835 carries their check) after the anchor by the same payer group or another,
    carried by an 835 after the item's own remittance, and before it; the ASC's separate-payee checks never count for a
    professional item. Write-offs are the payer's non-routine reasons (or the item's own reason) outside group PR,
    netted, at posting (on or before the anchor's day) or later.

    Returns {finding id: {'link', 'bill', 'as_of', 'covered', 'facility', 'shared_line', 'anchor', 'anchor_basis',
    'outcome', 'outcome_on', 'paid_after_same', 'paid_after_other', 'paid_after_835', 'paid_before', 'first_paid_after',
    'written_off', 'written_off_kind', 'written_off_when', 'written_off_on', 'reasons', 'patient_after', 'balance',
    'status', 'status_class', 'action', 'evidence_level', 'pattern', 'payments', ...}} for the money items and EMA-only
    denials (claim-level chases are C3's)."""
    _prepare(conn)
    fr = freshness(conn, today)
    lags = _lags(conn)
    policy = _meta(conn, POLICY_KEY, {}) or {}
    items = [f for f in findings if _wanted(f)]
    out: dict[str, dict] = {}
    has_handling = _table(conn, "line_handling")
    for part in _chunks(items):
        out.update(_outcome_chunk(conn, part, code_once, fr, lags, policy, has_handling))
    return out


_LINE_COLS = ("link", "svc", "cpt", "bill", "claim_id", "facility", "charges", "ins_paid", "contractual", "noncontractual",
              "balance", "pat_after_denial", "pat_paid_joined", "status", "status_class", "action", "action_on", "as_of",
              "denial_first_posted", "payer_group", "allowed", "units", "last_posted", "first_charge_posted", "flags", "twin_link")


def _outcome_chunk(conn, part, code_once, fr, lags, policy, has_handling) -> dict:
    targets: dict[str, list] = {}
    rows = []
    for f in part:
        ts = _targets(f)
        targets[f["finding_id"]] = ts
        bill = _bill(f.get("patient_control_number"))
        for k, svc, codes, lk in ts:
            for pref, code in enumerate(codes):
                rows.append((f["finding_id"], k, svc, code, pref, None if lk else bill, lk))
    _temp(conn, "ec_ft", ["fid TEXT", "k INTEGER", "svc TEXT", "cpt TEXT", "pref INTEGER", "bill TEXT", "link INTEGER"], rows,
          ["bill", "link, svc, cpt"])
    cols = ", ".join("l." + c for c in _LINE_COLS)
    cand: dict[tuple, list] = defaultdict(list)
    for r in conn.execute(f"""SELECT f.fid, f.k, f.pref, {cols} FROM temp.ec_ft f
            JOIN ema_link_map m ON m.bill = f.bill AND COALESCE(m.conflict, 0) = 0 AND COALESCE(m.facility, 0) = 0
            JOIN ema_lines l ON l.link = m.link AND l.svc = f.svc AND l.cpt = f.cpt
            WHERE f.link IS NULL
            UNION ALL
            SELECT f.fid, f.k, f.pref, {cols} FROM temp.ec_ft f JOIN ema_lines l ON l.link = f.link AND l.svc = f.svc AND l.cpt = f.cpt
            WHERE f.link IS NOT NULL"""):
        cand[(r[0], r[1])].append((r[2], dict(zip(_LINE_COLS, r[3:]))))
    mapped = {r[0] for r in conn.execute("""SELECT DISTINCT f.fid FROM temp.ec_ft f JOIN ema_link_map m ON m.bill = f.bill
                                            AND COALESCE(m.conflict, 0) = 0 AND COALESCE(m.facility, 0) = 0""")}
    # the items' own remittances, as the check match compares them
    tks = sorted({f.get("transaction_key") for f in part if f.get("transaction_key")})
    own: dict[str, str] = {}
    own_payer: dict[str, str] = {}
    for tpart in _chunks(tks):
        _temp(conn, "ec_tk", ["tk TEXT PRIMARY KEY"], [(t,) for t in tpart])
        for tk, trace, payer in conn.execute("""SELECT transaction_key, trace_number, payer_name FROM transactions
                                                WHERE transaction_key IN (SELECT tk FROM temp.ec_tk)"""):
            own[tk] = el.check_key(trace)
            own_payer[tk] = payer
    lines_wanted = {(ln["link"], ln["svc"], ln["cpt"]) for v in cand.values() for _p, ln in v}
    _temp(conn, "ec_lw", ["link INTEGER", "svc TEXT", "cpt TEXT"], sorted(lines_wanted), ["link, svc, cpt"])
    pays: dict[tuple, list] = defaultdict(list)
    for (lk, svc, cpt, chk, posted, payer, grp, cents, in835, match, cause, tdate, pdate, pair, damaged) in conn.execute(
            """SELECT y.link, y.svc, y.cpt, y.check_no, y.posted, y.payer, y.payer_group, y.ins_paid, y.in_835,
                      c.match, c.cause, c.trace_payment_date, t2.payment_date, c.pair_trace, c.damaged
               FROM temp.ec_lw w JOIN ema_line_payments y ON y.link = w.link AND y.svc = w.svc AND y.cpt = w.cpt
               LEFT JOIN ema_checks c ON c.check_no = y.check_no AND c.payer = y.payer
               LEFT JOIN transactions t2 ON t2.transaction_key = c.pair_trace"""):
        if damaged and cause != "misnumbered":
            cause = "damaged"           # whatever C1 made of it: a number a spreadsheet changed is most likely an 835's
        pays[(lk, svc, cpt)].append({"check": chk, "ckey": el.check_key(chk), "posted": posted, "payer": payer, "group": grp,
                                     "cents": cents or 0, "in_835": in835, "match": match, "cause": cause,
                                     "pair": pair if cause == "misnumbered" else None,
                                     "date_835": tdate if match in ("exact", "loose") else pdate if cause == "misnumbered" else None})
    reasons: dict[tuple, list] = defaultdict(list)
    for (lk, svc, cpt, grp, code, cls, payer, contr, nonc, first, last) in conn.execute(
            """SELECT z.link, z.svc, z.cpt, z.grp, z.code, z.reason_class, z.payer, z.contractual, z.noncontractual,
                      z.first_posted, z.last_posted
               FROM temp.ec_lw w JOIN ema_line_reasons z ON z.link = w.link AND z.svc = w.svc AND z.cpt = w.cpt"""):
        reasons[(lk, svc, cpt)].append({"grp": grp or "", "code": code or "", "cls": cls, "payer": payer, "contr": contr or 0,
                                        "nonc": nonc or 0, "first": first, "last": last})
    handled: dict[str, list] = defaultdict(list)
    lks = sorted({f["line_key"] for f in part if f.get("line_key")})
    if has_handling and lks:
        _temp(conn, "ec_lkh", ["lk TEXT PRIMARY KEY"], [(x,) for x in lks])
        for lk_, carc in conn.execute("""SELECT line_key, carc FROM line_handling WHERE line_key IN (SELECT lk FROM temp.ec_lkh)
                                         AND upper(COALESCE(adjustment_status, '')) = 'WRITE_OFF'"""):
            handled[lk_].append(_carc_key(carc))
    _drop(conn, "ec_ft", "ec_tk", "ec_lw", "ec_lkh")
    out = {}
    same = _payer_matcher(conn, own_payer)
    for f in part:
        out[f["finding_id"]] = _outcome_of(f, targets[f["finding_id"]], cand, mapped, own, pays, reasons, handled, code_once, fr,
                                            lags, policy, same=same)
    # E16: patient money after the denial is the patient's share only when no later remittance moved it there itself
    moved = [f for f in part if out[f["finding_id"]].get("outcome") == "patient" and f.get("signal") != "EMA_DENIED"]
    if moved:
        stand = _moved_stands(conn, moved)
        for f in moved:
            if f["finding_id"] not in stand:
                out[f["finding_id"]] = _outcome_of(f, targets[f["finding_id"]], cand, mapped, own, pays, reasons, handled, code_once,
                                                    fr, lags, policy, no_patient=True, same=same)
    return out


def _moved_stands(conn: sqlite3.Connection, items: list[dict]) -> set[str]:
    """E16: of these items, those no 835 contradicts: no remittance for the bill, date and code assigns an amount to the
    patient (group PR) after the item's own denial."""
    if not _table(conn, "adjustments"):
        return {f["finding_id"] for f in items}
    by_bill = _lines_835_by_bill(conn, {_bill(f.get("patient_control_number")) for f in items if f.get("patient_control_number")})
    keys = sorted({ln[6] for lines in by_bill.values() for ln in lines if ln[6]})
    pr: set[str] = set()
    for part in _chunks(keys):
        _temp(conn, "ec_pr", ["lk TEXT PRIMARY KEY"], [(x,) for x in part])
        for (lk,) in conn.execute("""SELECT DISTINCT a.line_key FROM temp.ec_pr k JOIN adjustments a ON a.line_key = k.lk
                                     WHERE a.group_code = 'PR' AND COALESCE(a.amount_cents, 0) > 0"""):
            pr.add(lk)
    _drop(conn, "ec_pr")
    keep = set()
    for f in items:
        bill = _bill(f.get("patient_control_number"))
        mine = f.get("payment_date") or ""
        codes = {f.get("procedure_code"), f.get("original_procedure_code"), f.get("billed_code")} - {None}
        if any(ln[0] == f.get("service_date") and (ln[1] in codes or ln[2] in codes) and (ln[4] or "") > mine and ln[6] in pr
               for ln in by_bill.get(bill, [])):
            continue
        keep.add(f["finding_id"])
    return keep


def _outcome_of(f, ts, cand, mapped, own, pays, reasons, handled, code_once, fr, lags, policy, no_patient: bool = False,
                same: Callable | None = None) -> dict:
    fid = f["finding_id"]
    same = same or (lambda group, f_: _same_payer(group, f_.get("payer_group")) if f_.get("payer_group") else False)
    ema_item = f.get("signal") == "EMA_DENIED"
    bill = _bill(f.get("patient_control_number"))
    res: dict = {"link": None, "bill": bill, "as_of": None, "covered": False, "facility": 0, "shared_line": False,
                 "anchor": None, "anchor_basis": None, "outcome": None, "outcome_on": None, "paid_after_same": 0,
                 "paid_after_other": 0, "paid_after_835": 0, "paid_before": 0, "first_paid_after": None, "paid_before_first": None,
                 "written_off": 0, "written_off_kind": None, "written_off_when": None, "written_off_on": None, "reasons": [],
                 "patient_after": 0, "balance": None, "status": None, "status_class": None, "action": None,
                 "evidence_level": None, "pattern": None, "payments": [], "lines": [], "carcs": []}
    svc0 = ts[0][1] if ts else f.get("service_date")
    res["covered"] = ema_item or _svc_covered(fr["boxes"], svc0, f.get("payment_date"))
    if not res["covered"]:
        res["outcome"] = "not_covered"
        return res
    own_ck = own.get(f.get("transaction_key") or "")
    chosen = []
    for k, svc, codes, lk in ts:
        opts = cand.get((fid, k)) or []
        if not opts:
            continue
        best = min(p for p, _ln in opts)
        lines = [ln for p, ln in opts if p == best]
        # several EMA claims of one bill hold the line: the one that carries the item's own remittance
        lines.sort(key=lambda ln: (not any(p["ckey"] == own_ck for p in pays.get((ln["link"], ln["svc"], ln["cpt"]), [])) if own_ck else True,
                                   ln["link"]))
        chosen.append((k, lines[0]))
    if not chosen:
        res["outcome"] = "not_found"
        res["mapped"] = fid in mapped
        return res
    res["lines"] = [[ln["link"], ln["svc"], ln["cpt"]] for _k, ln in chosen]
    ln0 = chosen[0][1]
    res.update(link=ln0["link"], as_of=max(ln["as_of"] or "" for _k, ln in chosen) or None,
               facility=max(ln["facility"] or 0 for _k, ln in chosen), status=ln0["status"], status_class=ln0["status_class"],
               action=ln0["action"], balance=sum(ln["balance"] or 0 for _k, ln in chosen),
               charges=sum(ln["charges"] or 0 for _k, ln in chosen))
    if bill is None:
        res["bill"] = ln0["bill"]
    # the same code twice on the claim's date: EMA's figures are by claim, code and date, so they are not this line's own
    tcode = {k: codes[-1] for k, _svc, codes, _lk in ts}
    for k, ln in chosen:
        probe = f if f.get("line_key") else {"claim_key": f.get("claim_key"), "service_date": ln["svc"],
                                            "procedure_code": tcode.get(k) or ln["cpt"]}
        try:
            once = True if ema_item else bool(code_once(probe))
        except Exception:       # noqa: BLE001  (a rule that cannot tell leaves the line as its own)
            once = True
        if not once:
            res["shared_line"] = True
            res["outcome"] = "shared_line"
            return res
    grp = f.get("payer_group")
    own_carc = _carc_key(f.get("carc"))[1] if f.get("carc") else None
    thr = _threshold(f)
    after_ema: list = []
    first_wo: str | None = None
    counted: list = []
    routine_co: list = []
    others_later = False
    any_after = False
    paid_before_rows: list = []
    anchors = []
    takeback = 0
    own_tk = f.get("transaction_key")
    own_paid = int(f.get("paid_cents") or 0)
    for _k, ln in chosen:
        key = (ln["link"], ln["svc"], ln["cpt"])
        prows = pays.get(key, [])

        def is_own(p: dict) -> bool:
            # the item's own remittance: its trace, or another number C1 paired with that trace (EMA keyed it wrong)
            return bool(own_ck and p["ckey"] == own_ck) or bool(own_tk and p.get("pair") == own_tk)
        mine = [p["posted"] for p in prows if is_own(p)]
        if ema_item:
            anchor, basis = _ev(f).get("first_denial_posted") or ln["denial_first_posted"] or f.get("payment_date"), "own_posting"
        elif mine:
            anchor, basis = min(mine), "own_posting"
        else:
            anchor, basis = _add(f.get("payment_date"), _lag(lags, grp, "p50")), "remit_plus_lag"
        anchors.append((anchor, basis))
        remit_day = f.get("payment_date") or anchor
        # with no posting of its own trace, the item's own payment may sit under a number EMA mistyped (or left out): an
        # EMA-only payment of exactly its own paid amount, posted within the group's usual delay of the remittance, is
        # taken for it, never for a later payment (it would close an underpayment with its own money)
        prows = sorted(prows, key=lambda q: (q["posted"] or "", q["cents"] < 0, q["check"] or ""))
        own_guess = None
        if not mine and not ema_item and own_paid > 0 and f.get("payment_date"):
            last = _add(f.get("payment_date"), _lag(lags, grp, "p95")) or ""
            own_guess = next((i for i, p in enumerate(prows)
                              if p["cents"] == own_paid and f["payment_date"] <= (p["posted"] or "") <= last
                              and not (p["in_835"] or p["match"] in ("exact", "loose") or p["cause"] in ("misnumbered", "damaged"))),
                             None)
            if own_guess is not None:
                res["own_by_amount"] = True
        after_pos = {"835": 0, "same": 0, "other": 0}         # positive payments after the anchor not yet taken back
        for i, p in enumerate(prows):
            if i == own_guess:
                continue
            if is_own(p):
                if p["cents"] < 0:
                    takeback += -p["cents"]         # the item's own remittance took back an earlier payment
                continue
            if not p["cents"]:
                continue
            sep = p["cause"] == "separate_payee" and not (ln["facility"] or 0)
            if sep:
                continue
            # a check a spreadsheet damaged is most likely an 835's: like a misnumbered one, it never closes an item
            via835 = bool(p["in_835"]) or p["match"] in ("exact", "loose") or p["cause"] in ("misnumbered", "damaged")
            if via835:
                when = p["date_835"] or p["posted"]
                after = bool(when) and bool(remit_day) and when > remit_day
            else:
                after = bool(anchor) and (p["posted"] or "") >= anchor
            row = {"check": p["check"], "posted": p["posted"], "payer": p["payer"], "group": p["group"], "cents": p["cents"],
                   "kind": "835" if via835 else "ema", "same": same(p["group"], f),
                   "link": ln["link"], "date_835": p["date_835"] if via835 else None}
            pool = "835" if via835 else ("same" if row["same"] else "other")
            cents = p["cents"]
            if after and cents < 0:
                # a takeback after the anchor cancels a later payment first; the rest cancels one made before the item
                back = min(-cents, after_pos[pool])
                after_pos[pool] -= back
                takeback += -cents - back
                cents = -back
                if not cents:
                    continue
                row = dict(row, cents=cents)
            if after:
                any_after = True
                res["payments"].append(row)
                if cents > 0:
                    after_pos[pool] += cents
                if via835:
                    res["paid_after_835"] += cents
                else:
                    after_ema.append(row)
                    if row["same"]:
                        res["paid_after_same"] += cents
                    else:
                        res["paid_after_other"] += cents
            else:
                res["paid_before"] += cents
                if cents > 0:
                    paid_before_rows.append(row)
        for z in reasons.get(key, []):
            if z["grp"] in ("", "PR"):
                continue
            mine_reason = own_carc is not None and z["code"] == own_carc
            if z["cls"] in ROUTINE_CLASSES and not mine_reason:
                if z["grp"] == "CO" and z["code"] in ROUTINE_SHORT_CODES and (z["contr"] or 0) > 0:
                    routine_co.append(dict(z, anchor=anchor))
                continue
            cents = (z["contr"] or 0) + (z["nonc"] or 0)
            res["reasons"].append([z["grp"], z["code"], cents, z["first"]])
            counted.append(dict(z, anchor=anchor, cents=cents, own=mine_reason))
            if z["last"] and anchor and z["last"] > anchor:
                others_later = True
        res["patient_after"] += ln["pat_after_denial"] or 0
    res["anchor"], res["anchor_basis"] = anchors[0] if anchors else (None, None)
    # what was paid before the item and still stands: takebacks (by the item's own remittance, or later) net it out
    res["taken_back"] = takeback
    res["paid_before"] = max(0, res["paid_before"] - takeback)
    if not res["paid_before"]:
        paid_before_rows = []
    after_ema.sort(key=lambda r: (r["posted"] or "", r["check"] or ""))
    if after_ema:
        a = after_ema[0]
        res["first_paid_after"] = {"date": a["posted"], "check_tail": _tail(a["check"]), "payer": a["payer"], "cents": a["cents"],
                                   "same_payer": a["same"]}
    if paid_before_rows:
        b = min(paid_before_rows, key=lambda r: (r["posted"] or "", r["check"] or ""))
        res["paid_before_first"] = {"date": b["posted"], "check_tail": _tail(b["check"]), "payer": b["payer"], "cents": b["cents"],
                                    "kind": b["kind"]}
    net = sum(z["cents"] for z in counted)
    contr = sum(z["contr"] for z in counted)
    nonc = sum(z["nonc"] for z in counted)
    res["carcs"] = sorted({f"{z['grp']}-{z['code']}" for z in counted if z["cents"]})
    category = "denial" if ema_item else f.get("category")
    if net > 0:
        res["written_off"] = net
        res["written_off_kind"] = "contractual" if contr >= nonc else "noncontractual"
        pos = [z for z in counted if z["cents"] > 0]
        first_wo = min((z["first"] for z in pos if z["first"]), default=None)
        res["written_off_on"] = max((z["last"] for z in counted if z["last"]), default=None)
        anchor0 = res["anchor"]
        # at posting: every posting of the write-off falls on or before the day EMA posted the payer's answer; anything
        # posted to it later (a reversal and a new write-off included) is a person's doing
        last_wo = max((z["last"] or z["first"] or "" for z in counted if z["cents"] or z["contr"] or z["nonc"]), default=None)
        res["written_off_when"] = "at_posting" if (first_wo and anchor0 and first_wo <= anchor0 and (last_wo or "") <= anchor0) else "later"
        if res["written_off_when"] == "at_posting" and _has_handled(handled.get(f.get("line_key") or "", []), counted):
            res["evidence_level"] = "rule_proven"
        elif res["written_off_when"] == "at_posting":
            res["evidence_level"] = "posting_day"
    # the outcome (strict reading: a write-off at posting is never counted as work)
    denial_type = any(z["cls"] not in ROUTINE_CLASSES and z["code"] not in NOT_DENIAL_CODES and z["cents"] > 0 for z in counted)
    pending = any(z["cls"] in PENDING_CLASSES and z["cents"] > 0 for z in counted)
    timely = [z for z in counted if z["cls"] == TIMELY_CLASS and z["cents"] > 0]
    earlier_other = [z for z in counted if z["cls"] not in (TIMELY_CLASS,) + ROUTINE_CLASSES]
    lost_timely = bool(timely) and (bool(earlier_other) and min(z["first"] or "" for z in timely) > min(z["first"] or "" for z in earlier_other)
                                    or (own_carc is not None and own_carc != "29" and f.get("category") in ("denial", "bundling")))
    patient_kinds = ("denial", "bundling")
    co_closed = any(z["grp"] == "CO" and z["cents"] > 0 for z in counted)
    moved = category in patient_kinds and res["patient_after"] > 0 and co_closed and not no_patient
    res["moved_to_patient"] = moved
    nothing_after = not any_after and res["patient_after"] == 0 and not others_later
    pay_after = res["paid_after_same"] + res["paid_after_other"]
    on = None
    if res["paid_after_same"] > 0 and res["paid_after_same"] >= thr:
        out, on = "paid_after", (res["first_paid_after"] or {}).get("date")
    elif res["paid_after_other"] > 0 and res["paid_after_other"] >= thr:
        out, on = "paid_after_other", (res["first_paid_after"] or {}).get("date")
    elif moved:
        out, on = "patient", res["written_off_on"]
    elif res["paid_before"] > 0 and category in patient_kinds and (f.get("paid_cents") or 0) == 0:
        out, on = "paid_before", (res["paid_before_first"] or {}).get("date")
    elif net > 0:
        at = res["written_off_when"] == "at_posting"
        on = first_wo if at else res["written_off_on"]
        if lost_timely:
            out = "lost_to_timely_filing"
        elif at and pending:
            out = "written_off_while_pending"
        elif at and denial_type and nothing_after:
            out = "auto_written_off"
        elif at:
            out = "written_off_at_posting" if denial_type or res["written_off_kind"] == "noncontractual" else "contractual_at_posting"
        else:
            out = "written_off_later" if denial_type or res["written_off_kind"] == "noncontractual" else "contractual_later"
    elif pay_after > 0:
        out, on = "paid_after_partial", (res["first_paid_after"] or {}).get("date")
    elif routine_co and (res["balance"] or 0) <= 0:
        # closed by the routine contractual reduction (CO-45 and the like): for an underpayment or a downcode that is
        # the shortfall accepted as contractual; for a denial, EMA posted it under a routine reason
        first_r = min((z["first"] for z in routine_co if z["first"]), default=None)
        at = bool(first_r) and bool(res["anchor"]) and first_r <= res["anchor"]
        out, on = ("contractual_at_posting" if at else "contractual_later"), first_r
        res["written_off_kind"] = "contractual"
        res["written_off_when"] = "at_posting" if at else "later"
        short = f.get("shortfall_cents") or 0
        routine = sum(z["contr"] for z in routine_co)
        res["contractual_routine"] = min(routine, short) if short > 0 else routine
    elif res["action"] and WORK_ACTIONS.search(res["action"] or ""):
        out, on = "action", ln0.get("action_on")
    elif (res["balance"] or 0) <= 0:
        out, on = "closed_unexplained", ln0.get("last_posted")
    else:
        out = "open"
    if out in WRITE_OFF_OUTCOMES:
        # the reason that took the most; a line closed by the routine reduction alone is named by that reason (CO-45)
        top = max(counted, key=lambda z: z["cents"], default=None) or max(routine_co, key=lambda z: z["contr"] or 0, default=None)
        res["pattern"] = _pattern(f, f"{top['grp']}-{top['code']}" if top else None)
        if (policy.get(res["pattern"]) or {}).get("decision") == "accept":
            out = "written_off_by_policy"
    res["outcome"], res["outcome_on"] = out, on
    return res


EQUIV_KEY = "ema:payer_equivalence"
EQUIV_MIN_CHECKS, EQUIV_MIN_SHARE = 3, 0.05


def learn_payer_equivalence(conn: sqlite3.Connection) -> dict:
    """Which EMA payer groups are the same payer as each 835 payer, learned from the checks EMA posted under a number that
    is exactly an 835 trace of that payer (the same money): {835 payer name: [EMA payer groups]}. A group counts when at
    least 3 such checks and 5% of the payer's matched checks carry it (one check keyed under the wrong payer does not).
    The two vocabularies name some payers differently (a TPA, a payer's Medicare Advantage plan), so a payment by the
    item's own payer is not read as another payer's. Kept in meta ema:payer_equivalence; classify_checks refreshes it."""
    out: dict[str, list] = {}
    if not _table(conn, "transactions"):
        return out
    per: dict[str, Counter] = defaultdict(Counter)
    for name, grp, n in conn.execute("""SELECT t.payer_name, c.payer_group, COUNT(*) FROM ema_checks c
                                        JOIN transactions t ON t.transaction_key = c.trace_key
                                        WHERE c.match = 'exact' AND c.payer_group IS NOT NULL AND t.payer_name IS NOT NULL
                                        GROUP BY 1, 2"""):
        per[name][grp] += n
    for name, groups in per.items():
        total = sum(groups.values())
        keep = sorted(g for g, n in groups.items() if n >= EQUIV_MIN_CHECKS and n >= EQUIV_MIN_SHARE * total)
        if keep:
            out[name] = keep
    _set_meta(conn, EQUIV_KEY, out)
    conn.commit()
    return out


def _payer_matcher(conn: sqlite3.Connection, own_payer: dict) -> Callable[[Any, dict], bool]:
    """same(EMA payer group, item): the item's own payer, by the group names (_same_payer) or by the EMA groups seen paying
    the item's 835 payer's own traces (learn_payer_equivalence)."""
    equiv = {k: set(v) for k, v in (_meta(conn, EQUIV_KEY, {}) or {}).items() if isinstance(v, list)}

    def same(group: Any, f: dict) -> bool:
        if not group:
            return False
        if f.get("payer_group") and _same_payer(group, f.get("payer_group")):
            return True
        name = own_payer.get(f.get("transaction_key") or "") or f.get("payer_name")
        return bool(name) and group in equiv.get(name, ())
    return same


def _has_handled(rows: list[tuple], counted: list[dict]) -> bool:
    """EMA's ERA-lines export recorded its posting rule writing off one of these reasons (handling WRITE_OFF)."""
    if not rows:
        return False
    want = {(z["grp"], z["code"]) for z in counted if z["cents"] > 0}
    codes = {c for _g, c in want}
    return any((g, c) in want or (not g and c in codes) for g, c in rows)


# ------------------------------------------------------------------------------------------------------- C6: paid in EMA
def paid_in_ema(conn: sqlite3.Connection, findings: Iterable[dict], outcomes: dict, *, closes: bool = True) -> dict[str, dict]:
    """C6 (amendment E5): close or confirm what EMA shows paid. A payment counts only when no 835 carries its check (not
    matched, not misnumbered), it is not the ASC's separate-payee money, it is on the item's own mapped line (not a
    facility claim), the claim carries the code once on that date, and EMA posted it after the item's own remittance.
    Same payer group and at least half of what the item is expected to bring (95% of the shortfall for downcoding and
    underpayment) closes it; another payer's payment gives a hint about a second payer; less gives a "partly paid per
    EMA" hint; a payment before the item's remittance gives the paid-before hint (G4), never a closure. Later payments
    an 835 carries are the archive rules' (the 835 decides). Patient payments never count.
    Returns {finding id: {'action': 'close'|'hint_other_payer'|'hint_partial'|'hint_paid_before', 'paid_cents', 'date',
    'check_tail', 'payer', 'note', 'hint', 'used': (check, posted, link)}}. The wiring calls _mark_paid for 'close' when
    REMITWATCH_EMA_CLOSES is on, else sets the hint; with closes=False the note is worded as a hint."""
    out: dict[str, dict] = {}
    for f in findings:
        fid = f.get("finding_id")
        o = outcomes.get(fid)
        if not o or f.get("auto_status") or f.get("signal") in CLAIM_SIGNALS or not _wanted(f):
            continue
        if not o.get("covered") or o.get("shared_line") or not o.get("link"):
            continue
        if o.get("facility") and f.get("signal") != "EMA_DENIED":
            continue
        thr = _threshold(f)
        same, other = o.get("paid_after_same") or 0, o.get("paid_after_other") or 0
        rows = [p for p in o.get("payments") or [] if p["kind"] == "ema"]
        as_of = o.get("as_of")
        if same > 0 and same >= thr:
            first = min((p for p in rows if p["same"]), key=lambda p: (p["posted"] or "", p["check"] or ""))
            payer = first["payer"] or first["group"]
            note = (f"EMA shows {_usd(same)} paid by {payer} on {first['posted']} (check ending {_tail(first['check'])}, "
                    f"postings through {as_of}). Reopen it if that payment is for something else.")
            out[fid] = {"action": "close", "paid_cents": same, "date": first["posted"], "check_tail": _tail(first["check"]),
                        "payer": payer, "note": note,
                        "hint": f"EMA shows {_usd(same)} paid by {payer} on {first['posted']} (no 835): check before marking it paid",
                        "used": (first["check"], first["posted"], first["link"])}
            if not closes:
                out[fid]["note"] = out[fid]["hint"]
        elif other > 0 and other >= thr:
            first = min((p for p in rows if not p["same"]), key=lambda p: (p["posted"] or "", p["check"] or ""))
            payer = first["payer"] or first["group"]
            hint = (f"EMA shows {_usd(other)} paid by {payer}, another payer, on {first['posted']} (check ending "
                    f"{_tail(first['check'])}): a second payer may have paid it; confirm before marking it paid")
            out[fid] = {"action": "hint_other_payer", "paid_cents": other, "date": first["posted"],
                        "check_tail": _tail(first["check"]), "payer": payer, "note": hint, "hint": hint,
                        "used": (first["check"], first["posted"], first["link"])}
        elif same + other > 0:
            first = min(rows, key=lambda p: (p["posted"] or "", p["check"] or ""))
            short = f.get("category") in SHORT_CATEGORIES
            hint = (f"Partly paid per EMA: {_usd(same + other)} posted on {first['posted']} (check ending {_tail(first['check'])}), "
                    + ("less than the shortfall" if short else "less than half of what this is expected to bring"))
            out[fid] = {"action": "hint_partial", "paid_cents": same + other, "date": first["posted"],
                        "check_tail": _tail(first["check"]), "payer": first["payer"] or first["group"], "note": hint, "hint": hint,
                        "used": (first["check"], first["posted"], first["link"])}
        elif (o.get("paid_before") or 0) > 0 and o.get("paid_before_first") and f.get("category") in ("denial", "bundling") \
                and not (f.get("paid_cents") or 0):
            b = o["paid_before_first"]
            payer = b.get("payer") or "insurance"
            hint = (f"EMA shows this line paid {_usd(o['paid_before'])} by {payer} on {b.get('date')}, before this denial: "
                    "check whether the denial is a duplicate or a second payer's answer")
            out[fid] = {"action": "hint_paid_before", "paid_cents": o["paid_before"], "date": b.get("date"),
                        "check_tail": b.get("check_tail"), "payer": payer, "note": hint, "hint": hint, "used": None}
    return out


# ---------------------------------------------------------------------------------------------- C2: written off in EMA
def written_off_in_ema(conn: sqlite3.Connection, findings: Iterable[dict], outcomes: dict) -> dict[str, dict]:
    """C2 (Build 1 v2): what EMA wrote off on an item's own line. The postings counted are the payer's insurance
    adjustments (contractual or not) with a non-routine reason or the item's own reason, outside group PR, on the item's
    line, netted so reversals cancel; the total counts only above $0, and only where the claim carries the code once on
    that date. written_off_cents = the larger of the ERA-lines figure already on the item and min(net, the item's value):
    the two are never added. The value is the shortfall, else the expected amount, else (detect could not price the
    item) the line's charge less what was paid on it before; written_off_basis says which. Never on the ASC's facility
    claims, never a payment, never a closure.
    Returns {finding id: {'written_off_cents', 'written_off_on', 'written_off_source', 'written_off_kind',
    'written_off_when', 'evidence_level', 'written_off_basis'}}."""
    out: dict[str, dict] = {}
    for f in findings:
        fid = f.get("finding_id")
        o = outcomes.get(fid)
        if not o or f.get("auto_status") or not _wanted(f) or f.get("signal") in CLAIM_SIGNALS:
            continue
        if not o.get("covered") or o.get("shared_line") or not o.get("link"):
            continue
        if o.get("facility") and f.get("signal") != "EMA_DENIED":
            continue
        net = o.get("written_off") or 0
        era = f.get("written_off_cents") or 0
        short = f.get("shortfall_cents") or 0
        expected = f.get("expected_cents") or 0
        # the item's value caps the figure: its shortfall, else what it is expected to bring, else (an item detect could not
        # price) what was billed on the line and not paid before it; never the line's whole write-off from every payer
        if short > 0:
            cap, basis = short, "shortfall"
        elif expected > 0:
            cap, basis = expected, "expected"
        else:
            cap, basis = max(0, (o.get("charges") or 0) - (o.get("paid_before") or 0) - (f.get("paid_cents") or 0)), "charge_less_paid"
        prod = max(0, min(net, cap))
        cents = max(era, prod)
        if cents <= 0:
            continue
        source = "production" if prod >= era and prod > 0 else "era_lines"
        kind = "moved_to_patient" if o.get("moved_to_patient") and o.get("outcome") == "patient" else o.get("written_off_kind")
        out[fid] = {"written_off_cents": cents,
                    "written_off_on": o.get("written_off_on") if source == "production" else f.get("written_off_on"),
                    "written_off_source": source, "written_off_kind": kind,
                    "written_off_when": o.get("written_off_when") if source == "production" else None,
                    "evidence_level": o.get("evidence_level"), "written_off_basis": basis if source == "production" else "era_lines"}
    return out


# ------------------------------------------------------------------------------------- the write-off policy (O11)
def set_policy(conn: sqlite3.Connection, pattern: str, decision: str, user: str) -> dict:
    """Owner decision O11: mark a write-off pattern (payer group|code|modifier|reason) 'accept' (EMA's rule is right: its
    items leave the work queue as written off by policy, still findable) or 'pursue' (the default). Records who and when."""
    if decision not in ("accept", "pursue"):
        raise ValueError("decision is accept or pursue")
    pol = _meta(conn, POLICY_KEY, {}) or {}
    pol[str(pattern)[:200]] = {"decision": decision, "by": str(user or "")[:80], "at": _now()}
    _set_meta(conn, POLICY_KEY, pol)
    conn.commit()
    return pol[str(pattern)[:200]]


def policy_summary(conn: sqlite3.Connection, findings: Iterable[dict], outcomes: dict, *, today: str | None = None) -> list[dict]:
    """The pattern table for owner decision O11 (G5, amendment 8): payer group x code x modifier x reason of the items EMA
    wrote off, with items, dollars, how many are still appealable, how many were written off at posting, and the share of
    the archive's zero-paid items of the pattern later closed as paid. Largest first."""
    t = _today(today)
    pol = _meta(conn, POLICY_KEY, {}) or {}
    pats: dict[str, dict] = {}
    for f in findings:
        o = outcomes.get(f.get("finding_id")) or {}
        if o.get("outcome") not in WRITE_OFF_OUTCOMES or not o.get("pattern"):
            continue
        p = pats.setdefault(o["pattern"], {"pattern": o["pattern"], "items": 0, "cents": 0, "appealable": 0, "appealable_cents": 0,
                                           "at_posting": 0, "auto": 0, "rule_proven": 0})
        cents = f.get("shortfall_cents") or 0
        p["items"] += 1
        p["cents"] += cents
        if f.get("appeal_deadline") and f["appeal_deadline"] >= t:
            p["appealable"] += 1
            p["appealable_cents"] += cents
        p["at_posting"] += o.get("written_off_when") == "at_posting"
        p["auto"] += o.get("outcome") == "auto_written_off"
        p["rule_proven"] += o.get("evidence_level") == "rule_proven"
    if pats and _table(conn, "findings"):
        hist: dict[str, list] = {}
        for grp, code, mods, carc, resolved in conn.execute(
                """SELECT payer_group, procedure_code, modifiers, carc, status = 'resolved' AND auto_note IS NOT NULL
                   FROM findings WHERE COALESCE(paid_cents, 0) = 0 AND line_key IS NOT NULL"""):
            m = (mods or "").split(",")[0].strip()
            for g in ("CO", "OA", "PI", "CR"):
                key = f"{grp or ''}|{code or ''}|{m}|{g}-{carc or ''}"
                if key in pats:
                    h = hist.setdefault(key, [0, 0])
                    h[0] += 1
                    h[1] += 1 if resolved else 0
        for key, p in pats.items():
            h = hist.get(key)
            p["later_paid_share"] = round(h[1] / h[0], 3) if h and h[0] else None
    for key, p in pats.items():
        p.setdefault("later_paid_share", None)
        p["decision"] = (pol.get(key) or {}).get("decision", "pursue")
    return sorted(pats.values(), key=lambda p: (-p["cents"], p["pattern"]))


# ------------------------------------------------------------------------------------------ C5: denials only EMA saw
def _lines_835_by_bill(conn: sqlite3.Connection, bills: Iterable[str]) -> dict[str, list]:
    """Every 835 line of these bills: (date, code, original code, claim status, payment date, claim key, line key)."""
    out: dict[str, list] = defaultdict(list)
    if not _table(conn, "claims"):
        return out
    bl = sorted({b for b in bills if b})
    for part in _chunks(bl):
        _temp(conn, "ec_b5", ["bill TEXT PRIMARY KEY", "hi TEXT"], [(b, b + "~") for b in part])
        for b, svc, code, ocode, st, paid_on, ck, lk in conn.execute("""SELECT b.bill, COALESCE(l.service_date, c.statement_from),
                    l.procedure_code, l.original_procedure_code, c.claim_status_code, c.payment_date, c.claim_key, l.line_key
                FROM temp.ec_b5 b JOIN claims c ON c.patient_control_number >= b.bill AND c.patient_control_number < b.hi
                JOIN transactions t ON t.transaction_key = c.transaction_key
                LEFT JOIN lines l ON l.claim_key = c.claim_key
                WHERE rw_bill(c.patient_control_number) = b.bill AND COALESCE(t.superseded, 0) = 0"""):
            out[b].append((svc, code, ocode, st, paid_on, ck, lk))
    _drop(conn, "ec_b5")
    return out


def _class_category(cls: str, code: str | None) -> str:
    if cls == "bundling":
        return "bundling"
    if cls == "level" and code in EM_CODES:
        return "downcoding"
    return "denial"


def _actionable(cls: str) -> str:
    if cls in ("coding", "information"):
        return "correct_and_resubmit"
    if cls in ("auth", "medical_necessity", "frequency", "level", "bundling", "timely"):
        return "appeal"
    return "review"


_CLASS_WORDS = {"auth": "no authorization", "medical_necessity": "medical necessity", "frequency": "frequency or units",
                "noncovered": "not covered", "information": "information missing", "coding": "a coding error",
                "bundling": "bundled", "level": "the level of service", "cob": "another payer is primary",
                "provider": "the clinician's enrollment", "duplicate": "a duplicate", "timely": "filed too late", "other": "another reason"}


def ema_denials(conn: sqlite3.Connection, *, today: str | None = None, rate: Callable | None = None,
                ratio: Callable | None = None, has_835_finding: Callable | None = None) -> list[dict]:
    """C5: denials only EMA saw. Per EMA line (claim, date, code), all of:
      1. a denial: the payer's non-routine adjustments (group not PR) net to at least $5, or an A/R Denied row carries a
         denial amount and no reason was posted;
      2. no 835 coverage: no 835 line for (bill, date, code or original code), no 835 claim-level denial and no 835 item
         for that bill and date. A claim the map could not tie to a bill counts as having none only when none of its
         checks is an 835 trace and no 835 line exists for its patient, date and code (E17; the ASC's facility claims,
         whose twin carries that key, are judged by their own checks);
      3. not paid since: insurance payments after the first denial posting, and the denying payer's own payments posted that
         same day (C6 counts them too: a line paid on the posting that carried the adjustment was paid, not denied), are
         under half of what it is expected to bring;
      4. not paid on another claim: insurance payments for the same patient, date and code on another of EMA's claims (a
         resubmission; never the ASC's facility claim) are under half of it too.
    Left out: G2211, 99024 and charges of $1 or less, products, patient-side rows, voided lines, denials that net to 0.
    One EMA_DENIED item per (claim number, reason class). Expected: the usual rate x units, else EMA's allowed amount,
    else the charge x the group's paid/charge ratio, else 45% of the charge, capped at the charge; when another payer
    paid the line before the denial, the balance left at the denial (E17). Deadline: the first denial posting less the
    group's p95 posting lag, plus the appeal window. The ASC's facility claims are titled "ASC facility claim"."""
    _prepare(conn)
    fr = freshness(conn, today)
    t = fr["today"]
    lags = _lags(conn)
    rows = conn.execute(f"""SELECT {', '.join(_LINE_COLS)}, nonroutine, name_key, map_method FROM ema_lines
                            WHERE COALESCE(nonroutine, 0) >= ? AND (COALESCE(flags, 0) & 17) = 0
                              AND COALESCE(status_class, '') <> 'void' AND COALESCE(charges, 0) > 100
                              AND cpt NOT IN ('G2211', '99024')""", (DENIAL_MIN,)).fetchall()
    lines = [dict(zip(_LINE_COLS + ("nonroutine", "name_key", "map_method"), r)) for r in rows]
    # A/R Denied rows with a denial amount and no reason posted
    ar_only = conn.execute("""SELECT a.link, a.svc, a.cpt, a.denial_cents, a.aging_date, a.payer_group, a.payer_kind, a.primary_payer,
                                     a.claim_id, a.as_of, a.facility
                              FROM ema_ar a WHERE lower(a.party) LIKE 'insur%' AND a.status_class = 'denied' AND COALESCE(a.denial_cents, 0) > 0
                                AND a.link IS NOT NULL AND COALESCE(a.product, 0) = 0 AND a.cpt NOT IN ('G2211', '99024')""").fetchall()
    have = {(ln["link"], ln["svc"], ln["cpt"]) for ln in lines}
    extra_keys = [(r[0], r[1], r[2]) for r in ar_only if (r[0], r[1], r[2]) not in have]
    ar_info = {(r[0], r[1], r[2]): r for r in ar_only}
    if extra_keys:
        _temp(conn, "ec_ax", ["link INTEGER", "svc TEXT", "cpt TEXT"], extra_keys, ["link, svc, cpt"])
        for r in conn.execute(f"""SELECT {', '.join('l.' + c for c in _LINE_COLS)}, l.nonroutine, l.name_key, l.map_method
                                  FROM temp.ec_ax x JOIN ema_lines l ON l.link = x.link AND l.svc = x.svc AND l.cpt = x.cpt"""):
            ln = dict(zip(_LINE_COLS + ("nonroutine", "name_key", "map_method"), r))
            no_adjustment = (ln["contractual"] or 0) == 0 and (ln["noncontractual"] or 0) == 0
            if no_adjustment and (ln["flags"] or 0) & 17 == 0 and ln["status_class"] != "void" and (ln["charges"] or 0) > 100:
                ln["ar_denied"] = ar_info[(ln["link"], ln["svc"], ln["cpt"])]
                lines.append(ln)
        _drop(conn, "ec_ax")
    if not lines:
        return []
    keys = sorted({(ln["link"], ln["svc"], ln["cpt"]) for ln in lines})
    reasons: dict[tuple, list] = defaultdict(list)
    pays: dict[tuple, list] = defaultdict(list)
    link_835: set[int] = set()
    for part in _chunks(keys):
        _temp(conn, "ec_dl", ["link INTEGER", "svc TEXT", "cpt TEXT"], part, ["link, svc, cpt"])
        for lk, svc, cpt, grp, code, cls, payer, contr, nonc, first, last in conn.execute(
                """SELECT z.link, z.svc, z.cpt, z.grp, z.code, z.reason_class, z.payer, z.contractual, z.noncontractual, z.first_posted,
                          z.last_posted FROM temp.ec_dl x JOIN ema_line_reasons z ON z.link = x.link AND z.svc = x.svc AND z.cpt = x.cpt"""):
            reasons[(lk, svc, cpt)].append((grp or "", code or "", cls, payer, contr or 0, nonc or 0, first, last))
        for lk, svc, cpt, chk, posted, payer, grp, cents, in835, match, cause in conn.execute(
                """SELECT y.link, y.svc, y.cpt, y.check_no, y.posted, y.payer, y.payer_group, y.ins_paid, y.in_835, c.match, c.cause
                   FROM temp.ec_dl x JOIN ema_line_payments y ON y.link = x.link AND y.svc = x.svc AND y.cpt = x.cpt
                   LEFT JOIN ema_checks c ON c.check_no = y.check_no AND c.payer = y.payer"""):
            pays[(lk, svc, cpt)].append((chk, posted, payer, grp, cents or 0, in835, match, cause))
        _temp(conn, "ec_dk", ["link INTEGER PRIMARY KEY"], [(x,) for x in sorted({k[0] for k in part})])
        for (lk,) in conn.execute("""SELECT DISTINCT y.link FROM ema_line_payments y WHERE y.link IN (SELECT link FROM temp.ec_dk)
                                     AND (y.in_835 = 1 OR y.check_no IN (SELECT check_no FROM ema_checks WHERE match IN ('exact', 'loose')
                                          OR cause = 'misnumbered'))"""):
            link_835.add(lk)
    _drop(conn, "ec_dl", "ec_dk")
    mapped = [ln for ln in lines if ln["bill"] and ln["map_method"] != "conflict"]
    by_bill = _lines_835_by_bill(conn, {ln["bill"] for ln in mapped})
    nk_keys = {(ln["name_key"], ln["svc"], ln["cpt"]) for ln in lines if not (ln["facility"] or 0) and ln["name_key"] is not None}
    seen_nk = _name_day_codes_835(conn, nk_keys)
    paid_elsewhere = _paid_on_other_links(conn, nk_keys)
    claim_ids = _claim_id_of_links(conn, {ln["link"] for ln in lines})
    items: dict[tuple, dict] = {}
    for ln in lines:
        key = (ln["link"], ln["svc"], ln["cpt"])
        rs = [r for r in reasons.get(key, []) if r[0] not in ("", "PR") and r[2] not in ROUTINE_CLASSES and r[1] not in C5_SKIP_CODES]
        by_reason: dict[tuple, int] = defaultdict(int)
        for r in rs:
            by_reason[(r[0], r[1], r[2], r[3])] += r[4] + r[5]
        net = sum(by_reason.values())
        ar_row = ln.get("ar_denied")
        if net < DENIAL_MIN and not ar_row:
            continue
        # 2. no 835 coverage (the patient-day key of a professional line too: a corrected claim under a new bill)
        if not (ln["facility"] or 0) and ln["name_key"] is not None and (ln["name_key"], ln["svc"], ln["cpt"]) in seen_nk:
            continue
        if ln["bill"] and ln["map_method"] != "conflict":
            codes = {ln["cpt"]}
            if any(x[0] == ln["svc"] and (x[1] in codes or x[2] in codes) for x in by_bill.get(ln["bill"], [])):
                continue
            if any(x[3] == "4" and (x[0] == ln["svc"] or x[0] is None) for x in by_bill.get(ln["bill"], [])):
                continue
            if has_835_finding is not None:
                try:
                    if has_835_finding(ln["bill"], ln["svc"], ln["cpt"]):
                        continue
                except Exception:       # noqa: BLE001
                    pass
        elif ln["link"] in link_835:
            continue
        if ar_row and net < DENIAL_MIN:
            top = ("", "", "other", ar_row[7], 0)
            first_denial = ar_row[4] or ar_row[9]
            dgrp, dkind = ar_row[5], ar_row[6]
        else:
            (g, code, cls, payer), _c = max(by_reason.items(), key=lambda kv: (kv[1], str(kv[0])))
            top = (g, code, cls, payer, _c)
            # dated by the reasons that make the item (those still standing), never by an earlier reason a reversal undid,
            # one C5 leaves out, or another payer's: that date sets the window and what "paid since" means
            first_denial = (min((r[6] for r in rs if r[6] and by_reason[(r[0], r[1], r[2], r[3])] > 0), default=None)
                            or min((r[6] for r in rs if r[6]), default=None) or ln["denial_first_posted"])
            dgrp, dkind = er.payer_group_of(conn, payer) if payer else (ln["payer_group"], None)
        cls = top[2]
        dgrp = dgrp or ln["payer_group"] or "Unknown"
        dkind = dkind or "other"
        # 3. not paid since; and what the line is expected to bring
        paid_after, paid_before = 0, []
        for p in pays.get(key, []):
            if not p[4] or not first_denial:
                continue
            day = p[1] or ""
            if day > first_denial or (day == first_denial and _same_payer(p[3], dgrp)):
                paid_after += p[4]
            else:
                paid_before.append(p)
        charge = ln["charges"] or 0
        units = ln["units"] or 1
        expected, basis = None, None
        r_ = _priced(rate, dgrp, [(ln["cpt"], ln["svc"], units)])
        if r_:
            expected, basis = r_, f"the payer's usual rate for {ln['cpt']} x {max(1, int(abs(units)))}"
        elif (ln["allowed"] or 0) > 0:
            expected, basis = ln["allowed"], "EMA's allowed amount"
        else:
            q = None
            if ratio is not None:
                try:
                    q = ratio(dgrp, ln["cpt"])
                except Exception:       # noqa: BLE001
                    q = None
            if q:
                expected, basis = int(round(charge * q)), f"the charge x what {dgrp} paid of the charge for {ln['cpt']} over 12 months"
            else:
                expected, basis = int(round(charge * FALLBACK_SHARE)), "45% of the charge"
        expected = min(expected, charge) if charge > 0 else expected
        other_first = [p for p in paid_before if not _same_payer(p[3], dgrp)]
        if other_first:
            routine_contr = (ln["contractual"] or 0) - sum(r[4] for r in rs)
            left = charge - sum(p[4] for p in paid_before) - max(0, routine_contr)
            expected, basis = max(0, left), "the balance left when this payer denied it, after the first payer's payment"
        if expected and paid_after >= PAID_SINCE_SHARE * expected:
            continue
        if not expected:
            continue
        # 4. not paid on another claim: the same patient, date and code paid on another of EMA's claims (a resubmission;
        # never the ASC's facility claim)
        if not (ln["facility"] or 0) and ln["name_key"] is not None:
            got = sum(v for lk2, v in paid_elsewhere.get((ln["name_key"], ln["svc"], ln["cpt"]), {}).items() if lk2 != ln["link"])
            if got >= PAID_SINCE_SHARE * expected:
                continue
        ik = (ln["link"], cls)
        it = items.setdefault(ik, {"lines": [], "group": dgrp, "kind": dkind, "payer": top[3], "facility": ln["facility"] or 0,
                                   "bill": ln["bill"] if ln["map_method"] != "conflict" else None, "link": ln["link"],
                                   "claim_id": claim_ids.get(ln["link"]), "first": first_denial, "reasons": Counter(),
                                   "expected": 0, "charge": 0, "bases": set(), "status": ln["status"]})
        it["lines"].append([ln["cpt"], ln["svc"], charge, f"{top[0]}-{top[1]}" if top[0] else "A/R Denied", first_denial,
                            sum(r[4] for r in rs), expected])
        it["reasons"][f"{top[0]}-{top[1]}" if top[0] else ""] += top[4]
        it["expected"] += expected
        it["charge"] += charge
        it["bases"].add(basis)
        if first_denial and (not it["first"] or first_denial < it["first"]):
            it["first"] = first_denial
    out = []
    for (lk, cls), it in sorted(items.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        ls = sorted(it["lines"], key=lambda x: (x[1] or "", x[0] or ""))
        top_reason = max(it["reasons"].items(), key=lambda kv: (kv[1], kv[0]))[0] if it["reasons"] else ""
        carc = top_reason.split("-", 1)[1] if "-" in top_reason else None
        grp, kind = it["group"], it["kind"]
        lag = _lag(lags, grp, "p95")
        start = _add(it["first"], -lag) if it["first"] else None
        rule = appeal_rule(grp, kind)
        deadline = _add(start, rule["days"]) if start else None
        unconfirmed = "" if rule["verified"] else " (not confirmed against the payer's current manual)"
        dbasis = (f"{rule['label']}{unconfirmed}. Counted from EMA's posting ({it['first']}) less its usual delay of {lag} days; "
                  "the EOB's date can be earlier")
        soon = bool(deadline) and deadline <= (_add(t, HIGH_DAYS) or "") and deadline >= t
        codes = sorted({x[0] for x in ls})
        label = ", ".join(codes[:5]) + ("…" if len(codes) > 5 else "")
        top_line = max(ls, key=lambda x: x[2] or 0)
        word = _CLASS_WORDS.get(cls, "another reason")
        title = f"Denied in EMA ({top_reason or 'A/R Denied'}: {word}), no remittance on file: {label} on {ls[0][1]}"
        if it["facility"]:
            title = f"ASC facility claim: denied in EMA ({top_reason or 'A/R Denied'}: {word}): {label} on {ls[0][1]}"
        pcn = it["claim_id"] or it["bill"]
        heirs = [("EMA_OPEN_CLAIM", it["claim_id"])] if it["claim_id"] else []
        out.append(_proto(
            "EMA_DENIED", _class_category(cls, top_line[0]), "high" if soon and it["expected"] >= HIGH_EXPECTED else "medium",
            _actionable(cls), title, key_parts=(lk, cls),
            claim={"patient_control_number": pcn, "payer_name": it["payer"], "payer_group": grp, "payer_kind": kind,
                   "service_date": ls[0][1], "year": (ls[0][1] or "")[:4] or None, "procedure_code": top_line[0],
                   "charge_cents": it["charge"] or None, "payment_date": it["first"]},
            expected=it["expected"], shortfall=it["expected"], carc=carc, deadline=deadline, basis=dbasis,
            evidence={"lines": [[x[0], x[1], x[2], x[3], x[4], x[5]] for x in ls], "ema_link": lk, "reason_class": cls,
                      "first_denial_posted": it["first"], "expected_basis": sorted(it["bases"]), "posting_lag_days": lag,
                      "ema_status": it["status"], "facility": bool(it["facility"]), "source": "ema"},
            hint="No remittance on file: get the EOB from the payer's portal before appealing",
            link=lk, heir_keys=heirs, ema_heir_keys=[(it["bill"], x[1], x[0]) for x in ls if it["bill"]]))
    return out


# ------------------------------------------------------------------------- C7: denial reasons posted as contractual
def denial_as_contractual(conn: sqlite3.Connection, *, today: str | None = None, ratio: Callable | None = None) -> list[dict]:
    """C7: one EMA_DENIAL_AS_CONTRACTUAL item per payer group x reason x posted year when EMA posted a payer's denial
    reason (non-routine, outside group PR) as a contractual adjustment for at least $1,000 on at least 10 lines. Outside
    the money totals (no expected amount). Evidence: the top codes; the dollars split, in this order, into lines under an
    item on file (C2's marks), lines no remittance covers (C5's ground), lines out by policy (G2211, 99024, $1 or less)
    and other 835 lines no item lists; the part still inside its window; the value at the group's paid/charge ratio (45%
    when none). High severity from $25,000."""
    _prepare(conn)
    t = _today(today)
    lags = _lags(conn)
    rows = conn.execute(f"""SELECT z.link, z.svc, z.cpt, z.grp, z.code, z.payer, z.contractual, z.first_posted, l.charges, l.bill,
                                   l.map_method, l.name_key, COALESCE(l.facility, 0)
                            FROM ema_line_reasons z LEFT JOIN ema_lines l ON l.link = z.link AND l.svc = z.svc AND l.cpt = z.cpt
                            WHERE COALESCE(z.contractual, 0) <> 0 AND z.grp NOT IN ('', 'PR')
                              AND z.reason_class NOT IN ({_routine_sql()})""").fetchall()
    fkeys = _finding_line_keys(conn)
    ekeys = _ema_item_links(conn)
    by_bill = _lines_835_by_bill(conn, {r[9] for r in rows if r[9] and r[10] != "conflict"})
    nk835 = _name_day_codes_835(conn, {(r[11], r[1], r[2]) for r in rows if r[11] is not None and not r[12]})
    gcache: dict = {}
    groups: dict[tuple, dict] = {}
    totals = Counter()
    for lk, svc, cpt, g, code, payer, contr, first, charges, bill, method, nk, fac in rows:
        if payer not in gcache:
            gcache[payer] = er.payer_group_of(conn, payer)
        grp, kind = gcache[payer]
        year = (first or "")[:4]
        k = (grp, f"{g}-{code}", year)
        it = groups.setdefault(k, {"cents": 0, "lines": set(), "codes": Counter(), "split": Counter(), "inside": 0, "kind": kind})
        it["cents"] += contr
        it["lines"].add((lk, svc, cpt))
        it["codes"][cpt] += contr
        mapped = bool(bill) and method != "conflict"
        covered = (mapped and any(x[0] == svc and cpt in (x[1], x[2]) for x in by_bill.get(bill, []))) or \
                  (nk is not None and not fac and (nk, svc, cpt) in nk835)
        if mapped and (bill, svc, cpt) in fkeys:
            part = "c2"
        elif lk in ekeys or not covered:
            part = "c5"                 # no remittance covers it: C5's ground (G2211 and 99024 lines among them are never items)
        elif cpt in POLICY_CODES or (charges or 0) <= 100:
            part = "policy"
        else:
            part = "other_835"
        it["split"][part] += contr
        totals[part] += contr
        start = _add(first, -_lag(lags, grp, "p95"))
        if start and (_add(start, appeal_rule(grp, kind)["days"]) or "") >= t:
            it["inside"] += contr
    out = []
    for (grp, reason, year), it in sorted(groups.items()):
        if it["cents"] < AS_CONTRACTUAL_MIN or len(it["lines"]) < AS_CONTRACTUAL_LINES:
            continue
        top = [[c, v] for c, v in it["codes"].most_common(5)]
        value = 0
        for c, v in it["codes"].items():
            q = None
            if ratio is not None:
                try:
                    q = ratio(grp, c)
                except Exception:       # noqa: BLE001  (a rule that cannot price a code prices nothing)
                    q = None
            value += int(round(v * (q if q else FALLBACK_SHARE)))
        title = f"{grp}: {reason} denials posted as contractual in {year}, {_usd(it['cents'])} on {len(it['lines'])} lines"
        out.append(_proto(
            "EMA_DENIAL_AS_CONTRACTUAL", "contract", "high" if it["cents"] >= AS_CONTRACTUAL_HIGH else "medium", "review", title,
            key_parts=(grp, reason, year),
            claim={"patient_control_number": None, "payer_name": grp, "payer_group": grp, "payer_kind": it["kind"],
                   "service_date": None, "year": year, "procedure_code": top[0][0] if top else None, "charge_cents": it["cents"]},
            expected=None, shortfall=None, carc=reason.split("-", 1)[1] if "-" in reason else None,
            evidence={"reason": reason, "year": year, "written_off_cents": it["cents"], "lines": len(it["lines"]), "top_codes": top,
                      "split_cents": dict(it["split"]), "inside_window_cents": it["inside"], "value_cents": value,
                      "next_step": ("Have EMA post this reason as non-contractual so it reaches the denial queue; then decide per "
                                    "code: appeal, bill it differently, or stop billing it.")},
            hint="EMA writes these denials off as contractual, so they never reach a denial queue"))
    _set_meta(conn, C7_KEY, {"cents": sum(totals.values()), "split_cents": dict(totals), "items": len(out), "at": _now()})
    conn.commit()
    return out


def _finding_line_keys(conn: sqlite3.Connection) -> set[tuple]:
    """(bill, date, code) of the 835 items on file (any code the line carries)."""
    out: set[tuple] = set()
    if not _table(conn, "findings"):
        return out
    have = _cols(conn, "findings")
    if not {"patient_control_number", "service_date", "procedure_code"} <= have:
        return out
    pick = ", ".join(c if c in have else "NULL" for c in ("original_procedure_code", "billed_code"))
    where = " AND claim_key IS NOT NULL" if "claim_key" in have else ""
    where += " AND COALESCE(stale, 0) = 0" if "stale" in have else ""
    for pcn, svc, code, ocode, bcode in conn.execute(
            f"""SELECT patient_control_number, service_date, procedure_code, {pick} FROM findings
                WHERE patient_control_number IS NOT NULL{where}"""):
        b = _bill(pcn)
        for c in (code, ocode, bcode):
            if c:
                out.add((b, svc, c))
    return out


def _ema_item_links(conn: sqlite3.Connection) -> set[int]:
    if not _table(conn, "findings") or "ema_link" not in _cols(conn, "findings"):
        return set()
    return {r[0] for r in conn.execute("SELECT DISTINCT ema_link FROM findings WHERE signal='EMA_DENIED' AND ema_link IS NOT NULL")}


def g2211_figures(conn: sqlite3.Connection) -> dict:
    """For G2211_DENIED's evidence: what EMA wrote off on G2211 lines, by payer group and posted year (contractual and
    not), and on how many lines."""
    _prepare(conn)
    out: dict = {}
    gcache: dict = {}
    for payer, year, contr, nonc, n in conn.execute(
            f"""SELECT payer, substr(first_posted, 1, 4), SUM(contractual), SUM(noncontractual), COUNT(*) FROM ema_line_reasons
                WHERE cpt = 'G2211' AND grp NOT IN ('', 'PR') AND reason_class NOT IN ({_routine_sql()}) GROUP BY 1, 2"""):
        if payer not in gcache:
            gcache[payer] = er.payer_group_of(conn, payer)[0]
        d = out.setdefault(gcache[payer], {}).setdefault(year, {"contractual": 0, "noncontractual": 0, "lines": 0})
        d["contractual"] += contr or 0
        d["noncontractual"] += nonc or 0
        d["lines"] += n
    return out


def timely_writeoffs(conn: sqlite3.Connection, *, today: str | None = None) -> dict:
    """C9: timely-filing write-offs (CO-29 and the like) by posted month and payer group, with how much sits on a line an
    item covers and how much is still inside an appeal window (recoverable only with proof of timely filing)."""
    _prepare(conn)
    t = _today(today)
    lags = _lags(conn)
    fkeys = _finding_line_keys(conn)
    ekeys = _ema_item_links(conn)
    out: dict = {"cents": 0, "rows": 0, "by_month": {}, "by_group": {}, "covered_cents": 0, "inside_cents": 0}
    gcache: dict = {}
    for lk, svc, cpt, payer, cents, first, bill in conn.execute(
            """SELECT z.link, z.svc, z.cpt, z.payer, COALESCE(z.contractual, 0) + COALESCE(z.noncontractual, 0), z.first_posted, l.bill
               FROM ema_line_reasons z LEFT JOIN ema_lines l ON l.link = z.link AND l.svc = z.svc AND l.cpt = z.cpt
               WHERE z.reason_class = 'timely' AND z.grp NOT IN ('', 'PR')"""):
        if payer not in gcache:
            gcache[payer] = er.payer_group_of(conn, payer)
        grp, kind = gcache[payer]
        out["cents"] += cents
        out["rows"] += 1
        m = (first or "")[:7]
        out["by_month"][m] = out["by_month"].get(m, 0) + cents
        out["by_group"][grp] = out["by_group"].get(grp, 0) + cents
        if (bill and (bill, svc, cpt) in fkeys) or lk in ekeys:
            out["covered_cents"] += cents
        start = _add(first, -_lag(lags, grp, "p95"))
        if start and (_add(start, appeal_rule(grp, kind)["days"]) or "") >= t:
            out["inside_cents"] += cents
    return out


# ------------------------------------------------------------------------------- W2: worked in EMA, the item overlays
def overlays(conn: sqlite3.Connection, findings: Iterable[dict], outcomes: dict, *, today: str | None = None) -> dict[str, dict]:
    """W2 and the EMA display columns of each item: ema_link, ema_status (the claim's newest status, with its source and
    as_of; shown second, never as the outcome), ema_balance_cents, ema_outcome and ema_outcome_on (the line's own result,
    shown first), and ema_worked_on / ema_worked_how: the newest of the A/R's worked marks (a note changed, a follow-up
    date set or moved, a claim action set, a resubmission), a higher submission of the claim after the item's
    remittance, a Production claim action of appeal, correction or reprocessing, and a non-routine posting after the
    item's remittance day. A write-off on the posting day is EMA's rule, never work. Never changes an item's status."""
    _prepare(conn)
    fl = [f for f in findings if f.get("finding_id")]
    claim_ids = {f.get("patient_control_number") for f in fl if f.get("patient_control_number")}
    links = {(outcomes.get(f["finding_id"]) or {}).get("link") or f.get("ema_link") for f in fl}
    links.discard(None)
    statuses = claim_statuses(conn, claim_ids=claim_ids, links=links)
    worked: dict[str, list] = defaultdict(list)       # claim id -> [(date, how, cpt, svc)]
    ar_bal: dict[str, int] = defaultdict(int)
    ids = sorted(claim_ids)
    for part in _chunks(ids):
        _temp(conn, "ec_wid", ["cid TEXT PRIMARY KEY"], [(x,) for x in part])
        for cid, on, how, cpt, svc in conn.execute("""SELECT claim_id, worked_on, worked_how, cpt, svc FROM ema_ar_items
                                                      WHERE claim_id IN (SELECT cid FROM temp.ec_wid) AND worked_on IS NOT NULL"""):
            worked[cid].append((on, how, cpt, svc))
        for cid, bal in conn.execute("""SELECT claim_id, SUM(exposure_cents) FROM ema_ar WHERE claim_id IN (SELECT cid FROM temp.ec_wid)
                                        GROUP BY claim_id"""):
            ar_bal[cid] = bal or 0
    subs: dict[str, list] = defaultdict(list)       # bill -> [(claim id, first seen)]
    bills = sorted({_bill(c) for c in claim_ids if c})
    for part in _chunks(bills):
        _temp(conn, "ec_wb", ["bill TEXT PRIMARY KEY"], [(x,) for x in part])
        for cid, bill, seen in conn.execute("SELECT claim_id, bill, first_seen FROM ema_claim_links WHERE bill IN (SELECT bill FROM temp.ec_wb)"):
            subs[bill].append((cid, seen))
    actions: dict[int, list] = defaultdict(list)
    for part in _chunks(sorted(links)):
        _temp(conn, "ec_wl", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
        for lk, value, seen in conn.execute("""SELECT link, value, first_seen FROM ema_link_events WHERE kind = 'action'
                                               AND link IN (SELECT link FROM temp.ec_wl)"""):
            if WORK_ACTIONS.search(value or ""):
                actions[lk].append((seen, value))
    _drop(conn, "ec_wid", "ec_wb", "ec_wl")
    sub_no = lambda c: int(m.group(1)) if (m := re.search(r"C(\d{1,3})$", c or "")) else 0      # noqa: E731
    out: dict[str, dict] = {}
    for f in fl:
        fid = f["finding_id"]
        o = outcomes.get(fid) or {}
        pcn = f.get("patient_control_number")
        lk = o.get("link") or f.get("ema_link")
        st = statuses.get(pcn) if pcn else None
        if lk in statuses and (st is None or _status_order(statuses[lk]["source"], statuses[lk]["as_of"])
                               > _status_order(st["source"], st["as_of"])):
            st = statuses[lk]
        after = f.get("payment_date") or ""
        marks: list[tuple] = []
        for on, how, cpt, svc in worked.get(pcn or "", []):
            if f.get("line_key") and cpt and svc and (cpt not in {f.get("procedure_code"), f.get("original_procedure_code"),
                                                                f.get("billed_code")} or svc != f.get("service_date")):
                continue
            marks.append((on, how or "ar"))
        for cid, seen in subs.get(_bill(pcn) or "", []):
            if sub_no(cid) > sub_no(pcn) and seen and seen > after:
                marks.append((seen, "resubmitted"))
        for seen, value in actions.get(lk, []):
            if seen and seen > after:
                marks.append((seen, "claim_action"))
        anchor = o.get("anchor") or after
        for r in o.get("reasons") or []:
            if r[3] and anchor and r[3] > anchor:
                marks.append((r[3], "posting"))
        worked_on, worked_how = (max(marks) if marks else (None, None))
        bal = o.get("balance")
        if bal is None and pcn in ar_bal:
            bal = ar_bal[pcn]
        out[fid] = {"ema_link": lk, "ema_status": (st or {}).get("status"), "ema_status_src": (st or {}).get("source"),
                    "ema_as_of": o.get("as_of") or (st or {}).get("as_of"), "ema_balance_cents": bal,
                    "ema_worked_on": worked_on, "ema_worked_how": worked_how, "ema_outcome": o.get("outcome"),
                    "ema_outcome_on": o.get("outcome_on")}
    return out


# ---------------------------------------------------------------------------------------------------- W3: unworked
def unworked_sql(data_through: str) -> tuple[str, list]:
    """The W3 rule as a condition on the findings table (api._filters uses it, so the count equals the list): a money
    item, To do, not stale, inside its window (or with no window), and no EMA work in the 21 days before data_through."""
    return ("category IN ('denial','bundling','downcoding','underpayment') AND status = 'open' AND COALESCE(stale, 0) = 0 "
            "AND (appeal_deadline IS NULL OR appeal_deadline >= ?) AND (ema_worked_on IS NULL OR ema_worked_on < ?)",
            [data_through, _add(data_through, -UNWORKED_DAYS)])


def unworked(conn: sqlite3.Connection, findings: Iterable[dict], *, data_through: str) -> dict:
    """W3: the items nobody has touched: in a money category, To do, inside their window, not stale, with no EMA work in
    the 21 days before data_through. Written-off items are included, with the split shown. Written to meta
    ema:unworked for Today's header box."""
    since = _add(data_through, -UNWORKED_DAYS) or ""
    out = {"n": 0, "cents": 0, "written_off": {"n": 0, "cents": 0, "at_posting": 0, "later": 0}, "data_through": data_through,
           "by_category": {}}
    for f in findings:
        if f.get("category") not in MONEY_CATEGORIES or (f.get("status") or "open") != "open" or f.get("stale"):
            continue
        if f.get("appeal_deadline") and f["appeal_deadline"] < data_through:
            continue
        if f.get("ema_worked_on") and f["ema_worked_on"] >= since:
            continue
        cents = f.get("shortfall_cents") or 0
        out["n"] += 1
        out["cents"] += cents
        c = out["by_category"].setdefault(f["category"], {"n": 0, "cents": 0})
        c["n"] += 1
        c["cents"] += cents
        if (f.get("written_off_cents") or 0) > 0:
            out["written_off"]["n"] += 1
            out["written_off"]["cents"] += cents
            if f.get("written_off_when") in ("at_posting", "later"):
                out["written_off"][f["written_off_when"]] += 1
    _set_meta(conn, UNWORKED_KEY, out)
    conn.commit()
    return out


# ------------------------------------------------------------------------------------------- W5: vouching before a fax
NO_REMITTANCE_MESSAGE = "No remittance on file: get the EOB from the payer's portal, then Fax it anyway."


def fax_vouch(conn: sqlite3.Connection, findings: Iterable[dict], outcomes: dict, *, today: str | None = None) -> dict[str, dict]:
    """W5: whether EMA's reports vouch for an item that the 835s alone would hold as stale before a fax. appeals_send
    holds an item only when neither the 835s nor Production can vouch for it. Production vouches when all of these hold:
      - Production is at most 14 days old;
      - the item's line is on an EMA claim tied to its bill, with no conflict;
      - no insurance payment was posted on the line after the item's remittance;
      - the line is on an A/R at most 8 days old, or its EMA balance is over $0, or EMA closed it with a write-off or a
        contractual adjustment (a write-off is not a payment: the item is still unpaid and still appealable).
    An EMA_DENIED item has no remittance at all: it is always held, code no_remittance. outcomes: line_outcomes' result.
    Returns {finding id: {'vouched': bool, 'code': None or why not, 'message'}}."""
    _prepare(conn)
    fr = freshness(conn, today)
    t = fr["today"]
    fs = [f for f in findings if f.get("finding_id")]
    links = sorted({int(o["link"]) for f in fs for o in [outcomes.get(f["finding_id"]) or {}] if o.get("link") is not None})
    mapped: set[int] = set()
    on_ar: set[int] = set()
    since = _add(t, -AR_FRESH_DAYS) or ""
    for part in _chunks(links):
        _temp(conn, "ec_fv", ["link INTEGER PRIMARY KEY"], [(x,) for x in part])
        mapped.update(r[0] for r in conn.execute("""SELECT m.link FROM temp.ec_fv v JOIN ema_link_map m ON m.link = v.link
                                                    WHERE m.bill IS NOT NULL AND COALESCE(m.conflict, 0) = 0"""))
        on_ar.update(r[0] for r in conn.execute("""SELECT DISTINCT a.link FROM temp.ec_fv v JOIN ema_ar a ON a.link = v.link
                                                   WHERE a.as_of >= ? AND a.as_of <= ?""", (since, t)))
    _drop(conn, "ec_fv")
    out: dict[str, dict] = {}
    for f in fs:
        o = outcomes.get(f["finding_id"]) or {}
        lk = o.get("link")
        if f.get("signal") == "EMA_DENIED":
            code, msg = "no_remittance", NO_REMITTANCE_MESSAGE
        elif not fr["production_fresh"]:
            code = "production_old"
            msg = (f"EMA's Production report is {fr['production_days']} days old: pull a new one so it can vouch for this item."
                   if fr["production_days"] is not None else "No EMA Production report on file to vouch for this item.")
        elif lk is None or int(lk) not in mapped:
            code, msg = "not_mapped", "This item's line is not tied to its EMA claim, so EMA cannot vouch for it."
        elif (o.get("paid_after_same") or 0) + (o.get("paid_after_other") or 0) + (o.get("paid_after_835") or 0) > 0:
            code, msg = "paid_after", "EMA shows a payment on this line after its remittance: check it before you fax."
        elif int(lk) not in on_ar and (o.get("balance") or 0) <= 0 and o.get("outcome") not in WRITE_OFF_OUTCOMES:
            # a balance EMA wrote off is still unpaid (amendment 8): only a line closed some other way is held
            code, msg = "no_balance", "EMA shows no balance on this line and it is not on an A/R from the last 8 days."
        else:
            code, msg = None, f"EMA's postings through {fr['production_as_of']} vouch for this item."
        out[f["finding_id"]] = {"vouched": code is None, "code": code, "message": msg}
    return out


# ------------------------------------------------------------------------------------------------------ C8: credits
def credits(conn: sqlite3.Connection, *, today: str | None = None) -> dict:
    """C8 (E3): credit balances. A credit is a negative exposure on the A/R (Current A/R, else the Denial Amount, never
    both); its age counts from the aging date (the line's last posting) when there is one, else the date of service, to
    the A/R's as_of. Medicare primary credits over 60 days (Medicare's 60-day overpayment rule) and Medicare Advantage
    credits are listed apart. Data health 'credits' is not ok on any Medicare primary credit over 60 days, or other
    credits over 60 days of $500 or more. Also the insurance-side credit balances Production shows that the A/R leaves
    out (its lean lines only: a credit on a line paid cleanly may be pruned). On the dashboard's copy (which holds only
    the lines the pages show) the figures detection stored in meta ema:reconcile are given."""
    _prepare(conn)
    if not _full_store(conn):
        stored = (_meta(conn, RECONCILE_KEY, {}) or {}).get("c8")
        if stored:
            return stored
    out: dict = {"insurance": {"n": 0, "cents": 0}, "patient": {"n": 0, "cents": 0}, "medicare_primary_over60": {"n": 0, "cents": 0},
                 "medicare_advantage": {"n": 0, "cents": 0}, "other_over60": {"n": 0, "cents": 0},
                 "patient_over60": {"n": 0, "cents": 0}, "by_coverage": {}, "production_not_on_ar": {"n": 0, "cents": 0,
                                                                                                    "medicare_over60": {"n": 0, "cents": 0}}}
    for party, cov, kind, cents, age in conn.execute(
            """SELECT CASE WHEN lower(party) LIKE 'insur%' THEN 'insurance' ELSE 'patient' END, COALESCE(coverage, ''), payer_kind,
                      exposure_cents, CAST(julianday(as_of) - julianday(COALESCE(aging_date, svc)) AS INTEGER)
               FROM ema_ar WHERE COALESCE(exposure_cents, 0) < 0"""):
        out[party]["n"] += 1
        out[party]["cents"] += cents
        c = out["by_coverage"].setdefault(f"{party}|{cov}", {"n": 0, "cents": 0})
        c["n"] += 1
        c["cents"] += cents
        over = (age or 0) > CREDIT_DAYS
        primary = cov.lower().startswith("primary")
        if party == "insurance" and kind == "medicare" and primary and over:
            out["medicare_primary_over60"]["n"] += 1
            out["medicare_primary_over60"]["cents"] += cents
        elif over:
            out["other_over60"]["n"] += 1
            out["other_over60"]["cents"] += cents
            if party == "patient":
                out["patient_over60"]["n"] += 1
                out["patient_over60"]["cents"] += cents
        if party == "insurance" and kind == "medicare_advantage":
            out["medicare_advantage"]["n"] += 1
            out["medicare_advantage"]["cents"] += cents
    for kind, cents, age in conn.execute(
            """SELECT l.payer_kind, l.balance, CAST(julianday(l.as_of) - julianday(l.last_posted) AS INTEGER) FROM ema_lines l
               WHERE COALESCE(l.balance, 0) < 0 AND (COALESCE(l.flags, 0) & 17) = 0 AND COALESCE(l.ins_paid, 0) > 0
                 AND NOT EXISTS (SELECT 1 FROM ema_ar a WHERE a.link = l.link AND a.svc = l.svc AND a.cpt = l.cpt)"""):
        p = out["production_not_on_ar"]
        p["n"] += 1
        p["cents"] += cents
        if kind == "medicare" and (age or 0) > CREDIT_DAYS:
            p["medicare_over60"]["n"] += 1
            p["medicare_over60"]["cents"] += cents
    out["ok"] = out["medicare_primary_over60"]["n"] == 0 and -out["other_over60"]["cents"] < OTHER_CREDIT_MIN
    return out


def credits_csv(conn: sqlite3.Connection) -> Iterator[list]:
    """/api/ema/credits.csv: initials, service date, code, amount, age, party, coverage. No names, no record number."""
    _prepare(conn)
    yield ["initials", "service_date", "code", "amount", "age_days", "party", "coverage", "payer_group"]
    cur = conn.execute("""SELECT initials, svc, cpt, exposure_cents, CAST(julianday(as_of) - julianday(COALESCE(aging_date, svc)) AS INTEGER),
                                 party, coverage, payer_group FROM ema_ar WHERE COALESCE(exposure_cents, 0) < 0 ORDER BY svc, cpt""")
    while True:
        rows = cur.fetchmany(CHUNK)
        if not rows:
            break
        for ini, svc, cpt, cents, age, party, cov, grp in rows:
            yield [ini or "", svc, cpt or "", f"{(cents or 0) / 100:.2f}", age, party, cov or "", grp or ""]


# ---------------------------------------------------------------------------------------- W4: the A/R accounted for
AR_BUCKETS = ("waiting", "chase", "paid_elsewhere", "denied_todo", "denied_started", "denied_sent", "denied_worked",
              "denied_no_item", "not_sent", "settled", "facility", "unbilled", "other", "credits")
AR_BUCKET_LABELS = {
    "waiting": "Waiting inside the payer's usual time", "chase": "Past it: no answer from the payer (chase)",
    "paid_elsewhere": "Paid elsewhere, still open in EMA", "denied_todo": "Denied, with an item To do",
    "denied_started": "Denied, with an item Started", "denied_sent": "Denied, with an item Sent",
    "denied_worked": "Denied, worked in EMA", "denied_no_item": "Denied, with no item", "not_sent": "Rejected or never sent",
    "settled": "Settled since the pull (Production shows it)", "facility": "The ASC's facility claims",
    "unbilled": "Held for insurance, no claim created", "other": "Other statuses", "credits": "Credits"}


def ar_accounted(conn: sqlite3.Connection, findings: Iterable[dict] | None = None, *, today: str | None = None,
                 save: bool = True) -> dict:
    """W4: the insurance A/R (exposure: Current A/R, else the Denial Amount) in buckets that add up exactly to its total.
    Each insurance row goes to the first bucket that fits: credits; held for insurance with no claim; the ASC's facility
    claims; settled since the pull (Production shows the line's balance at $0 with a posting after the A/R's as_of);
    rejected or never sent; denied (by the item on it: To do, Started, Sent; worked in EMA; or no item); pending (paid
    elsewhere, still open in EMA; past the payer's usual time with a NO_ANSWER item; or waiting); other statuses. Also the
    line the A/R leaves out ("not in this export"), from Production, outside the total. With `save` (detection), written
    to meta ema:ar_accounted, and each A/R row's bucket to ema_ar.bucket, so the list a bucket opens (ar_list) is the
    same rows on the dashboard's copy, which cannot work the buckets out (it holds no keys and only part of the lines).
    `findings`: the items (finding_id, signal, status, patient_control_number, service_date, procedure_code, ema_link);
    the store's own when None. On the copy it gives what detection stored."""
    _prepare(conn)
    if not _full_store(conn):
        return dict(_meta(conn, AR_ACCOUNTED_KEY, None) or {}, _rows=[])
    if findings is None:
        findings = _stored_findings(conn)
    open_items: dict[tuple, str] = {}         # (bill, svc, code) -> best status among items on it
    chase_ids: set = set()
    ema_links: dict[int, str] = {}
    rank = {"appealed": 3, "in_progress": 2, "open": 1}
    for f in findings:
        st = f.get("status") or "open"
        if f.get("stale") or st not in rank:
            continue
        if f.get("signal") in ("NO_ANSWER",):
            chase_ids.add(f.get("patient_control_number") or "")
            for kp in [f.get("ema_link")]:
                if kp:
                    chase_ids.add(f"link:{kp}")
            continue
        if f.get("signal") == "EMA_DENIED" and f.get("ema_link"):
            if rank[st] > rank.get(ema_links.get(f["ema_link"], ""), 0):
                ema_links[f["ema_link"]] = st
            continue
        pcn = f.get("patient_control_number")
        if not pcn:
            continue
        for code in {f.get("procedure_code"), f.get("original_procedure_code"), f.get("billed_code")} - {None}:
            k = (_bill(pcn), f.get("service_date"), code)
            if rank[st] > rank.get(open_items.get(k, ""), 0):
                open_items[k] = st
        if not f.get("line_key"):
            k = (_bill(pcn), f.get("service_date"), None)
            if rank[st] > rank.get(open_items.get(k, ""), 0):
                open_items[k] = st
    tat = _turnaround(conn).get("groups") or {}
    buckets = {b: {"n": 0, "cents": 0} for b in AR_BUCKETS}
    total = {"n": 0, "cents": 0}
    by_row: list[tuple] = []
    paid_keys = _paid_elsewhere_keys(conn)
    cur = conn.execute("""SELECT a.file_id, a.row_no, a.claim_id, a.bill, a.link, a.svc, a.cpt, a.exposure_cents, a.status_class,
                                 a.as_of, a.facility, a.payer_group, a.name_key, i.worked_on,
                                 l.balance, l.last_posted, l.ins_paid
                          FROM ema_ar a LEFT JOIN ema_ar_items i ON i.item_key = a.item_key
                          LEFT JOIN ema_lines l ON l.link = a.link AND l.svc = a.svc AND l.cpt = a.cpt
                          WHERE lower(a.party) LIKE 'insur%'""")
    while True:
        rows = cur.fetchmany(CHUNK)
        if not rows:
            break
        for (fid_, row_no, cid, bill, lk, svc, cpt, cents, cls, as_of, fac, grp, nk, worked_on, bal, last, ins_paid) in rows:
            cents = cents or 0
            total["n"] += 1
            total["cents"] += cents
            if cents < 0:
                b = "credits"
            elif not cid:
                b = "unbilled"
            elif fac:
                b = "facility"
            elif bal is not None and bal <= 0 and last and as_of and last > as_of:
                b = "settled"
            elif cls in ("rejected", "not_sent"):
                b = "not_sent"
            elif cls == "denied":
                st = open_items.get((bill, svc, cpt)) or open_items.get((bill, svc, None)) or (ema_links.get(lk) if lk else None)
                if st == "appealed":
                    b = "denied_sent"
                elif st == "in_progress":
                    b = "denied_started"
                elif st == "open":
                    b = "denied_todo"
                elif worked_on:
                    b = "denied_worked"
                else:
                    b = "denied_no_item"
            elif cls in ("pending", "none", "answered"):
                if nk is not None and (nk, svc, cpt) in paid_keys and paid_keys[(nk, svc, cpt)] != lk:
                    b = "paid_elsewhere"
                elif cid in chase_ids or (lk and f"link:{lk}" in chase_ids):
                    b = "chase"
                else:
                    b = "waiting"
            else:
                b = "other"
            buckets[b]["n"] += 1
            buckets[b]["cents"] += cents
            by_row.append((fid_, row_no, b))
    not_in = conn.execute("""SELECT COUNT(*), COALESCE(SUM(l.balance), 0) FROM ema_lines l
                             WHERE COALESCE(l.balance, 0) > 0 AND (COALESCE(l.flags, 0) & 17) = 0 AND COALESCE(l.ins_charges, 0) > 0
                               AND COALESCE(l.status_class, '') NOT IN ('denied', 'void', 'paid', 'settled', 'gave_up')
                               AND NOT EXISTS (SELECT 1 FROM ema_ar a WHERE a.link = l.link AND a.svc = l.svc AND a.cpt = l.cpt)""").fetchone()
    fr = freshness(conn, today)
    out = {"as_of": fr["ar_as_of"], "total": total, "buckets": buckets, "labels": AR_BUCKET_LABELS,
           "adds_up": sum(v["cents"] for v in buckets.values()) == total["cents"] and sum(v["n"] for v in buckets.values()) == total["n"],
           "not_in_export": {"n": not_in[0], "cents": not_in[1],
                             "label": "Not in this export (no activity in 30 days, not denied), from Production"},
           "turnaround_groups": len(tat), "at": _now()}
    if save:
        _set_meta(conn, AR_ACCOUNTED_KEY, out)
        conn.execute("UPDATE ema_ar SET bucket = NULL WHERE bucket IS NOT NULL")
        conn.executemany("UPDATE ema_ar SET bucket = ? WHERE file_id = ? AND row_no = ?", [(b, f, r) for f, r, b in by_row])
        conn.commit()
    out["_rows"] = by_row
    return out


def _paid_elsewhere_keys(conn: sqlite3.Connection) -> dict:
    """(name key, date, code) -> the claim number insurance paid it under, for the A/R's insurance lines (another EMA
    claim of the same patient, date and code that insurance paid, never the ASC's facility claim)."""
    out: dict = {}
    conn.execute("DROP TABLE IF EXISTS temp.ec_arn")
    conn.execute("""CREATE TEMP TABLE ec_arn AS SELECT DISTINCT CAST(name_key AS INTEGER) AS nk, CAST(svc AS TEXT) AS svc,
                        CAST(cpt AS TEXT) AS cpt FROM ema_ar WHERE lower(party) LIKE 'insur%' AND name_key IS NOT NULL""")
    conn.execute("CREATE INDEX temp.ix_ec_arn ON ec_arn(nk, svc, cpt)")
    for nk, svc, cpt, lk in conn.execute("""SELECT l.name_key, l.svc, l.cpt, l.link FROM ema_lines l
            JOIN temp.ec_arn a ON a.nk = l.name_key AND a.svc = l.svc AND a.cpt = l.cpt
            WHERE COALESCE(l.ins_paid, 0) > 0 AND COALESCE(l.facility, 0) = 0"""):
        out[(nk, svc, cpt)] = lk
    _drop(conn, "ec_arn")
    return out


def _stored_findings(conn: sqlite3.Connection) -> list[dict]:
    if not _table(conn, "findings"):
        return []
    cols = _cols(conn, "findings")
    want = [c for c in ("finding_id", "signal", "category", "status", "stale", "patient_control_number", "service_date",
                        "procedure_code", "original_procedure_code", "billed_code", "line_key", "ema_link", "shortfall_cents",
                        "appeal_deadline", "written_off_cents", "written_off_when", "ema_worked_on") if c in cols]
    cur = conn.execute(f"SELECT {', '.join(want)} FROM findings WHERE COALESCE(stale, 0) = 0 AND status IN ('open','in_progress','appealed')")
    return [dict(zip(want, r)) for r in cur.fetchall()]


def ar_list(conn: sqlite3.Connection, bucket: str, page: int = 1, *, size: int = 100) -> dict:
    """/api/ema/ar?bucket=...: one page of the A/R rows in a W4 bucket: claim id, claim number, payer group, service date,
    code, balance, status, days since service, whether a note exists. No names. The rows come by the buckets detection
    stored (ema_ar.bucket), so the list holds what the W4 count it opens from counted, on the server and on the dashboard's
    copy alike; on the whole store, an A/R read since detection is bucketed afresh (never written). `pending`: rows of an
    A/R read since detection that the copy cannot bucket yet. Never writes."""
    if bucket not in AR_BUCKETS:
        raise ValueError("unknown bucket")
    _prepare(conn)
    page = max(1, int(page or 1))
    insurance = "lower(a.party) LIKE 'insur%'"
    has_col = "bucket" in _cols(conn, "ema_ar")
    unset = conn.execute(f"SELECT COUNT(*) FROM ema_ar a WHERE {insurance}" + (" AND a.bucket IS NULL" if has_col else "")).fetchone()[0]
    cols = "a.claim_id, a.link, a.payer_group, a.svc, a.cpt, a.exposure_cents, a.claim_status, a.as_of, a.has_note"
    order = "ORDER BY a.svc, a.claim_id, a.file_id, a.row_no LIMIT ? OFFSET ?"
    pending = 0
    if _full_store(conn) and unset:
        acc = ar_accounted(conn, save=False)
        keys = [(f, r) for f, r, b in acc.pop("_rows") if b == bucket]
        count, cents = len(keys), acc["buckets"][bucket]["cents"]
        _temp(conn, "ec_arl", ["file_id TEXT", "row_no INTEGER"], keys, ["file_id, row_no"])
        got = conn.execute(f"""SELECT {cols} FROM temp.ec_arl k JOIN ema_ar a ON a.file_id = k.file_id AND a.row_no = k.row_no
                               {order}""", (size, (page - 1) * size)).fetchall()
        _drop(conn, "ec_arl")
    elif has_col:
        pending = unset
        count, cents = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(a.exposure_cents), 0) FROM ema_ar a WHERE {insurance} "
                                    "AND a.bucket = ?", (bucket,)).fetchone()
        got = conn.execute(f"SELECT {cols} FROM ema_ar a WHERE {insurance} AND a.bucket = ? {order}",
                           (bucket, size, (page - 1) * size)).fetchall()
    else:
        pending, count, cents, got = unset, 0, 0, []
    rows = [{"claim_id": cid, "link": lk, "payer_group": grp, "service_date": svc, "code": cpt, "balance_cents": c, "status": st,
             "days": _days(svc, as_of), "has_note": bool(note)} for cid, lk, grp, svc, cpt, c, st, as_of, note in got]
    return {"bucket": bucket, "label": AR_BUCKET_LABELS[bucket], "page": page, "size": size, "count": count, "cents": cents,
            "rows": rows, "pending": pending}


# ----------------------------------------------------------------------------------------- the reconcile summary
def reconcile_summary(conn: sqlite3.Connection, *, today: str | None = None, save: bool = True) -> dict:
    """Everything /api/ema/reconcile shows, in counts and dollars only: freshness and map coverage; C1 by month, cause and
    payer group (and what was posted since the remittances stopped); the C4 summary; C7 by payer group and reason; C8
    credits; C9 timely-filing write-offs; the G5 policy table counts; the W4 buckets; data health's remits_missing.
    Detection calls it with `save` on the whole store: it is written to meta ema:reconcile, with the cells c1_summary adds
    up (ema:c1_cells), for the dashboard's copy, which holds part of the store. A page calls it with save=False; on the
    copy it gives what detection stored and never writes."""
    _prepare(conn)
    if not _full_store(conn):
        return _meta(conn, RECONCILE_KEY, None) or {}
    fr = freshness(conn, today)
    c1 = c1_summary(conn)
    after = conn.execute("""SELECT COUNT(*), COALESCE(SUM(ins_paid), 0) FROM ema_checks WHERE cause = 'after_feed'""").fetchone()
    out = {"at": _now(), "freshness": {k: v for k, v in fr.items() if k not in ("boxes", "ar_years")},
           "map": _meta(conn, el.META_MAP_COVERAGE, None), "c1": c1,
           "since_feed_stopped": {"checks": after[0], "cents": after[1]},
           "c4": _meta(conn, UNPOSTED_KEY, None), "c7": _meta(conn, C7_KEY, None),
           "c8": credits(conn, today=today), "c9": timely_writeoffs(conn, today=today),
           "w4": _meta(conn, AR_ACCOUNTED_KEY, None), "unworked": _meta(conn, UNWORKED_KEY, None),
           "turnaround": (_meta(conn, TURNAROUND_KEY, {}) or {}).get("groups"),
           "posting_lag": {k: v for k, v in (_meta(conn, LAG_KEY, {}) or {}).items() if k in ("p95", "default_p95", "all")}}
    if _table(conn, "findings") and "ema_outcome" in _cols(conn, "findings"):
        pol: dict = {}
        for outcome, when, n, cents in conn.execute(
                """SELECT ema_outcome, COALESCE(written_off_when, ''), COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings
                   WHERE COALESCE(stale, 0) = 0 AND status IN ('open','in_progress','appealed') AND ema_outcome IS NOT NULL
                   GROUP BY 1, 2"""):
            pol.setdefault(outcome, {})[when or "-"] = {"n": n, "cents": cents}
        out["outcomes"] = pol
    out["remits_missing"] = _remits_missing(conn, fr)
    if save:
        _set_meta(conn, RECONCILE_KEY, out)
        _set_meta(conn, C1_CELLS_KEY, _c1_cells(conn))
        conn.commit()
    return out


# --------------------------------------------------------------------------------------------------- data health
def data_health(conn: sqlite3.Connection, *, today: str | None = None) -> list[dict]:
    """The EMA items of the Data tab's health list, in insights.data_health's shape ({key, ok, title, detail, who, ...}):
    ema_reports_missing, ema_ar_stale, ema_production_stale, ema_report_held, ema_columns, ema_map, ema_link_conflicts,
    remits_missing, credits, ema_status_words."""
    _prepare(conn)
    fr = freshness(conn, today)
    t = fr["today"]
    items: list[dict] = []
    have = {r[0]: r[1] for r in conn.execute("""SELECT report, COUNT(*) FROM ema_report_files
                                               WHERE status IN ('current','partly_replaced') GROUP BY report""")}
    missing = [n for r, n in (("ar_report", "A/R report"), ("production", "Production report")) if not have.get(r)]
    items.append({"key": "ema_reports_missing", "ok": not missing, "missing": missing,
                  "title": "EMA's A/R and Production reports are on file" if not missing else f"No {' or '.join(missing)} on file yet",
                  "detail": "" if not missing else "The checks against EMA need both reports, pulled every Monday.",
                  "who": "Billing lead: export the reports from EMA and put them in the Dropbox folder or on the Upload page."})
    for key, label, days in (("ema_ar_stale", "A/R report", fr["ar_days"]), ("ema_production_stale", "Production report", fr["production_days"])):
        if days is None:
            continue
        ok = days <= STALE_DAYS
        items.append({"key": key, "ok": ok, "days": days,
                      "title": f"The {label} is current" if ok else f"The {label} is {days} days old",
                      "detail": "" if ok else "Checks that rest on what EMA has not posted wait until a newer one arrives.",
                      "who": "Billing lead: pull a new one from EMA."})
    held = conn.execute("SELECT COUNT(*) FROM ema_report_files WHERE status='held'").fetchone()[0]
    items.append({"key": "ema_report_held", "ok": held == 0, "n": held,
                  "title": "No EMA report is held" if not held else f"{held} EMA {'report waits' if held == 1 else 'reports wait'} for a decision",
                  "detail": "" if not held else "A held report is read only when someone presses Apply (or Discard) on the Data tab.",
                  "who": "Billing lead: open the Data tab and decide."})
    need = {"production": ("claim status", "payment allowed amount", "primary payer", "secondary payer"),
            "ar_report": ("denial amount", "aging date", "claim action", "bill follow up date", "last billing note")}
    short_cols: list[str] = []
    for report, cols_json in conn.execute("""SELECT report, columns FROM ema_report_files WHERE status IN ('current','partly_replaced')
                                             ORDER BY as_of DESC"""):
        try:
            names = {er.norm(c) for c in json.loads(cols_json or "[]")}
        except ValueError:
            continue
        for n in need.get(report, ()):
            if n not in names and n not in short_cols:
                short_cols.append(n)
    items.append({"key": "ema_columns", "ok": not short_cols, "columns": short_cols,
                  "title": "EMA's reports carry every column the checks use" if not short_cols else
                           f"EMA's reports lack {len(short_cols)} column{'s' if len(short_cols) != 1 else ''} the checks use",
                  "detail": "" if not short_cols else "Missing: " + ", ".join(short_cols) + ".",
                  "who": "Billing lead: export the reports with every column."})
    cov = _meta(conn, el.META_MAP_COVERAGE, None) or {}
    if cov:
        share = cov.get("share")
        ok = share is None or share >= 0.90
        items.append({"key": "ema_map", "ok": ok, "share": share,
                      "title": f"{(share or 0):.0%} of the remittance bills Production covers are tied to their EMA claim",
                      "detail": "" if ok else "Under 90%: checks that need the tie (paid in EMA, written off) miss the rest.",
                      "who": "Practice administrator: tell RemitWatch's maintainer."})
        links, conflicts = cov.get("links") or 0, cov.get("conflicts") or 0
        share_c = conflicts / links if links else 0
        items.append({"key": "ema_link_conflicts", "ok": share_c <= 0.02, "share": round(share_c, 4), "n": conflicts,
                      "title": f"{conflicts:,} EMA claims tie to two remittance bills",
                      "detail": "" if share_c <= 0.02 else "Over 2%: some EMA claims are not used until the tie is clear.",
                      "who": "Practice administrator: tell RemitWatch's maintainer."})
    full = _full_store(conn)
    stored_rm = None if full else (_meta(conn, RECONCILE_KEY, {}) or {}).get("remits_missing")
    items.append(stored_rm or _remits_missing(conn, fr))
    cr = credits(conn, today=t)
    items.append({"key": "credits", "ok": cr["ok"], "medicare_primary_over60": cr["medicare_primary_over60"],
                  "other_over60": cr["other_over60"],
                  "title": "No credit balance needs a refund decision" if cr["ok"] else
                           (f"{cr['medicare_primary_over60']['n']} Medicare primary credit{'s' if cr['medicare_primary_over60']['n'] != 1 else ''} "
                            f"over 60 days ({_usd(cr['medicare_primary_over60']['cents'])})" if cr["medicare_primary_over60"]["n"]
                            else f"Credits over 60 days total {_usd(cr['other_over60']['cents'])}"),
                  "detail": "" if cr["ok"] else "Medicare expects an overpayment back within 60 days of finding it. A person decides each one.",
                  "who": "Billing lead with the compliance lead: review the credits list (Data tab, credits.csv)."})
    words = _meta(conn, er.META_UNKNOWN_STATUS, []) or []
    items.append({"key": "ema_status_words", "ok": not words, "words": words[:20],
                  "title": "Every EMA claim status is known" if not words else f"{len(words)} EMA claim status{'es' if len(words) != 1 else ''} RemitWatch does not know",
                  "detail": "" if not words else "Read with the older rules until they are added: " + ", ".join(words[:10]) + ".",
                  "who": "Practice administrator: tell RemitWatch's maintainer what each means."})
    return items


def _remits_missing(conn: sqlite3.Connection, fr: dict) -> dict:
    """Data health remits_missing: not ok when the last full month's checks with no remittance for a feed reason
    (feed_gap, no_era, no_era_va) exceed the larger of $5,000 and 2% of the matched checks."""
    newest = fr.get("newest_835")
    # the last full month the remittances reach: the newest 835's month when it is that month's last day, else the one before
    month = (_add((_add(newest, 1) or "")[:7] + "-01", -1) or "")[:7] if newest else None
    if not month:
        return {"key": "remits_missing", "ok": True, "title": "No remittances to compare yet", "detail": "", "who": ""}
    gap, matched = conn.execute("""SELECT COALESCE(SUM(CASE WHEN cause IN ('feed_gap','no_era','no_era_va') THEN ins_paid END), 0),
                                          COALESCE(SUM(CASE WHEN match IN ('exact','loose') THEN ins_paid END), 0)
                                   FROM ema_checks WHERE substr(first_posted, 1, 7) = ?""", (month,)).fetchone()
    limit = max(REMITS_MISSING_MIN, REMITS_MISSING_SHARE * (matched or 0))
    ok = (gap or 0) <= limit
    return {"key": "remits_missing", "ok": ok, "month": month, "cents": gap, "matched_cents": matched,
            "title": f"Payments without a remittance in {month}: {_usd(gap)}",
            "detail": "" if ok else "EMA posted these payments, and no 835 for them reached RemitWatch: the remittance or the payer's ERA enrollment is missing.",
            "who": "Billing lead, with TriZetto: send checks.csv (Data tab) and ask for the missing remittances; enroll VA ERAs."}


def alert_candidates(conn: sqlite3.Connection, *, today: str | None = None) -> list[dict]:
    """Alerts for alerts.AlertEngine to raise: REMITS_MISSING (per payer group and posted month, when checks with a feed
    gap reach $2,000 on at least 2 checks) and EMA_STALE (either report more than 8 days old; it clears itself)."""
    _prepare(conn)
    fr = freshness(conn, today)
    out = []
    for grp, month, n, cents in conn.execute("""SELECT payer_group, substr(first_posted, 1, 7), COUNT(*), SUM(ins_paid) FROM ema_checks
                                               WHERE cause = 'feed_gap' GROUP BY 1, 2
                                               HAVING SUM(ins_paid) >= ? AND COUNT(*) >= ? ORDER BY 2, 1""",
                                            (ALERT_FEED_GAP_CENTS, ALERT_FEED_GAP_CHECKS)):
        out.append({"kind": "REMITS_MISSING", "severity": "high" if cents >= 5 * ALERT_FEED_GAP_CENTS else "medium",
                    "payer_group": grp, "key": ("REMITS_MISSING", grp, month), "n": n, "cents": cents,
                    "title": f"{grp}: {_usd(cents)} paid in {month} on {n} checks whose remittances never arrived",
                    "detail": "The check numbers follow this payer's own 835 series, so the remittances exist: ask TriZetto to send them (checks.csv)."})
    for label, days in (("A/R report", fr["ar_days"]), ("Production report", fr["production_days"])):
        if days is not None and days > STALE_DAYS:
            out.append({"kind": "EMA_STALE", "severity": "medium", "payer_group": None, "key": ("EMA_STALE", label), "n": days,
                        "cents": None, "title": f"EMA's {label} is {days} days old",
                        "detail": "Pull a new one every Monday; checks that rest on an absence wait for it."})
    return out


# --------------------------------------------------------------------------------------------------- the claim view
def claim_block(conn: sqlite3.Connection, *, claim_id: str | None = None, bill: str | None = None, links: Sequence[int] = (),
                ledger: Any = None) -> dict:
    """The 'ema' block of /api/findings/{id} and /api/claims/view: the claim's EMA claim numbers, its A/R rows (with first
    seen and the worked marks), its lines (payments marked carried by an 835 or not), write-offs by reason, the balance,
    status and as_of, and on the server the ledger's postings (newest 500, no keys); on a serverless host a note that
    they are kept on the server. No names, initials or keys."""
    _prepare(conn)
    lks = {int(x) for x in links if x is not None}
    b = bill or _bill(claim_id)
    if claim_id:
        lks |= {r[0] for r in conn.execute("SELECT link FROM ema_claim_links WHERE claim_id=? AND link IS NOT NULL", (claim_id,))}
    if b:
        lks |= {r[0] for r in conn.execute("SELECT link FROM ema_link_map WHERE bill=? AND COALESCE(conflict, 0) = 0", (b,))}
    out: dict = {"links": sorted(lks), "claim_url": er.claim_url_template(conn), "ar": [], "lines": [], "writeoffs": [],
                 "statuses": {}, "postings": None, "postings_note": None}
    if not lks and not claim_id:
        return out
    q = ",".join("?" * len(lks)) or "NULL"
    ar_where = ([f"a.link IN ({q})"] if lks else []) + (["a.claim_id = ?"] if claim_id else [])
    for r in conn.execute(f"""SELECT a.claim_id, a.link, a.coverage, a.svc, a.cpt, a.exposure_cents, a.denial_cents, a.claim_status,
                                     a.status_class, a.as_of, a.follow_up, a.claim_action, a.has_note, a.payer_group,
                                     i.first_seen, i.worked_on, i.worked_how, i.note_changed
                              FROM ema_ar a LEFT JOIN ema_ar_items i ON i.item_key = a.item_key
                              WHERE {' OR '.join(ar_where)} ORDER BY a.svc, a.cpt""", sorted(lks) + ([claim_id] if claim_id else [])):
        out["ar"].append(dict(zip(("claim_id", "link", "coverage", "service_date", "code", "balance_cents", "denial_cents", "status",
                                   "status_class", "as_of", "follow_up", "claim_action", "has_note", "payer_group", "first_seen",
                                   "worked_on", "worked_how", "note_changed"), r)))
    if lks:
        for r in conn.execute(f"""SELECT link, svc, cpt, charges, ins_paid, ins_paid_no835, contractual, noncontractual, balance,
                                         status, status_class, action, as_of, facility, map_method
                                  FROM ema_lines WHERE link IN ({q}) ORDER BY svc, cpt""", sorted(lks)):
            out["lines"].append(dict(zip(("link", "service_date", "code", "charges_cents", "ins_paid_cents", "ins_paid_no835_cents",
                                          "contractual_cents", "noncontractual_cents", "balance_cents", "status", "status_class",
                                          "action", "as_of", "facility", "map_method"), r)))
        pays: dict[tuple, list] = defaultdict(list)
        for lk, svc, cpt, chk, posted, payer, cents, in835 in conn.execute(
                f"""SELECT link, svc, cpt, check_no, posted, payer, ins_paid, in_835 FROM ema_line_payments
                    WHERE link IN ({q}) ORDER BY posted""", sorted(lks)):
            pays[(lk, svc, cpt)].append({"posted": posted, "payer": payer, "cents": cents, "check_tail": _tail(chk),
                                         "remittance": "835" if in835 else "no 835"})
        for ln in out["lines"]:
            ln["payments"] = pays.get((ln["link"], ln["service_date"], ln["code"]), [])
        for r in conn.execute(f"""SELECT link, svc, cpt, grp, code, reason_class, payer, contractual, noncontractual, first_posted, last_posted
                                  FROM ema_line_reasons WHERE link IN ({q}) ORDER BY first_posted""", sorted(lks)):
            out["writeoffs"].append(dict(zip(("link", "service_date", "code", "group", "reason", "reason_class", "payer",
                                              "contractual_cents", "noncontractual_cents", "first_posted", "last_posted"), r)))
    out["statuses"] = {str(k): v for k, v in claim_statuses(conn, claim_ids=[claim_id] if claim_id else [], links=lks).items()}
    if ledger is not None and lks:
        out["postings"] = [{k: v for k, v in p.items() if k not in ("person", "name_key")} for p in el.ledger_postings(ledger, sorted(lks))]
    elif lks:
        out["postings_note"] = "EMA's postings are kept on the server; this site shows the summaries."
    return out
