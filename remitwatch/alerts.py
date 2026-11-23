"""
Alerts: what changed in payer behaviour since the last look.

Runs after every detection run and compares the most recent four weeks of
payments with the six months before them. Payment dates are measured from the
newest remittance in the store, not from today's date, so a late upload does
not read as a quiet payer.

Kinds
-----
RAMP               a payer is doing more of something (downcoding, bundling, denials, underpayment) than its own recent rate
RISING             the slow version: the last quarter against the year before it, for a climb too gradual to trip RAMP
RESUMED            a payer went quiet for the whole baseline period and has started again
NEW_BEHAVIOR       a payer and signal combination never seen before
PROVIDER_TARGETED  one provider is downcoded by a payer far more often than the rest of the practice
RATE_CUT           a payer's usual allowed amount for a code dropped
NEW_REASON_CODE    a payer started using an adjustment reason code it never used before
DEADLINE           open appealable money that reaches its filing deadline in the next 30 days (one alert per payer per week)
STALE_FEED         no new remittance files have arrived

An alert keeps one row for as long as the condition holds. When the condition
ends it is marked cleared, and if it comes back it is raised again as new.
Notifications (email and an optional webhook) contain payer names, counts and
dollars only.

SWING (one analysis run added or removed far more open items or dollars than a
run normally does) is the one kind that does not clear when its condition ends:
it stays at the top of every page, and leads every alert mail, for seven days
after the run that moved, unless a person acknowledges it. See swing_rule.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from .codes import carc_text, payer_group_kind
from .detect import DOWNCODE_SIGNALS, EM_SQL, ensure_schema, feed_date

RECENT_DAYS = int(os.environ.get("REMITWATCH_ALERT_RECENT_DAYS", "28"))
BASELINE_DAYS = int(os.environ.get("REMITWATCH_ALERT_BASELINE_DAYS", "182"))
STALE_DAYS = int(os.environ.get("REMITWATCH_STALE_DAYS", "10"))

ALERTS_DDL = """
CREATE TABLE IF NOT EXISTS alerts (
    alert_id TEXT PRIMARY KEY, kind TEXT, severity TEXT, title TEXT, detail TEXT,
    payer_group TEXT, family TEXT, scope TEXT,
    metric REAL, baseline REAL, n_recent INTEGER, cents_recent INTEGER,
    window_start TEXT, window_end TEXT,
    first_seen TEXT, last_seen TEXT, status TEXT DEFAULT 'new',
    acknowledged_at TEXT, notified_at TEXT, run_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts(status);
"""

# signal families measured against their own exposure: visits for downcoding, every line for the rest
_DOWN = ",".join("'%s'" % x for x in DOWNCODE_SIGNALS)
FAMILIES: dict[str, dict] = {
    # visit downcoding is measured against visit lines, so only findings on a billed visit code belong to it
    "downcoding": {"label": "visit downcoding", "where": f"signal IN ({_DOWN}) AND billed_code IN ({EM_SQL})", "scope": "em"},
    "code_change": {"label": "procedure downcodes and code changes",
                    "where": f"(signal='CODE_CHANGED' OR (signal IN ({_DOWN}) AND (billed_code IS NULL OR billed_code NOT IN ({EM_SQL}))))", "scope": "all"},
    "bundling": {"label": "bundling", "where": "category='bundling'", "scope": "all"},
    "denial": {"label": "line denials", "where": "category='denial' AND line_key IS NOT NULL", "scope": "all"},
    "underpayment": {"label": "underpayment against its own rate", "where": "category='underpayment'", "scope": "all"},
}
LIVE = "status != 'dismissed' AND COALESCE(stale,0) = 0"       # findings that count toward a rate


def _aid(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _shift(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for a Poisson count with mean lam."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    if lam > 400:       # exp(-lam) underflows for large means; the normal approximation is accurate there
        z = (k - 0.5 - lam) / math.sqrt(lam)
        return 0.5 * math.erfc(z / math.sqrt(2))
    term = math.exp(-lam)
    cdf = term
    for i in range(1, k):
        term *= lam / i
        cdf += term
    return max(0.0, 1.0 - cdf)


def money(c) -> str:
    return f"${(c or 0) / 100:,.0f}"


# --------------------------------------------------------------------------- SWING: one run moved the worklist far more than runs do
# An interim guard, alert only: it holds nothing and changes no item. Whatever the reason (one stray remittance, a file
# dated in the future, a release, something nobody has thought of yet), a detection run that adds or removes far more open
# items or dollars than a run normally does is put at the top of every page and in the alert mail, with its numbers,
# before anyone works the new list. Each run is compared with the run before it from what the store keeps per run:
# progress_snapshots (items and dollars by category and status) and detection_runs (when, and under which rules). What
# people changed between the two runs (a Paid, a Let go, a reason hidden for a payer) is their work, not the run's, and is
# taken out first.
#
# The levels come from data, not guesses: the live store (copy of 2026-11-22). Its own history holds four runs, all of them
# release re-analyses with no new remittance in between: -948, -3 and +170 open items (-$18,358, -$238, +$47,361), at most
# -681 items in one category. It holds no ordinary day, so a day of remittances was measured from the archive: the open
# items each payment date created, minus the items that date's payments closed, over the last twelve months (330 payment
# days, 53 weeks), all categories but the context rows. A day: -77 to +290 items (99th percentile -45 and +63), -$32,209
# to +$103,563; in one category -97 to +179 items (denials both), dollars down to -$33,273 and up to +$102,721 (fees: one
# takeback). A week: -74 to +421 items, -$25,471 to +$154,871; in one category -88 to +190 items, -$34,801 (denials) to
# +$162,083 (fees). Normal falls are far smaller than normal rises (items close a few at a time, new remittances come in
# batches), so each direction has its own level, set above the largest normal change in that direction. A catch-up of
# several weeks of remittances in one run can pass the rise levels; that is meant: a list that grew by a month at once
# deserves a look before it is worked.
SWING_RISE_ITEMS = 500                  # open items one run adds, all categories (largest normal: +421, a week)
SWING_RISE_CENTS = 25_000_000           # $250,000 of open items added, all categories (largest normal: +$154,871, a week)
SWING_RISE_ITEMS_CATEGORY = 300         # open items added in one category (largest normal: +190, a week)
SWING_RISE_CENTS_CATEGORY = 20_000_000  # $200,000 added in one category (largest normal: +$162,083 of fees, a week)
SWING_FALL_ITEMS = 200                  # open items one run removes, all categories (largest normal: -77, a day)
SWING_FALL_CENTS = 10_000_000           # $100,000 removed, all categories (largest normal: -$32,209, a day)
SWING_FALL_ITEMS_CATEGORY = 150         # open items removed in one category (largest normal: -97, a day)
SWING_FALL_CENTS_CATEGORY = 7_500_000   # $75,000 removed in one category (largest normal: -$34,801, a week)
SWING_DAYS = 7                          # on every page for seven days, counting the day of the run, unless acknowledged
SWING_OPEN = ("open", "in_progress", "appealed")       # To do, Started, Sent: an item someone can still work
SWING_CONTACT = "the practice owner"    # who the setup page says to tell when something is wrong
SWING_CATEGORY_LABELS = {"denial": "Denials", "bundling": "Bundling and modifiers", "downcoding": "Downcoding",
                         "underpayment": "Underpayment", "contract": "Contract rates", "fees": "Fees and takebacks"}
# File kinds in plain words. Only these words are used: no text read from a file (its name, its folder) reaches an alert.
SWING_FILE_KINDS = {"835": ("remittance (835) file", "remittance (835) files"), "837": ("claim (837) file", "claim (837) files"),
                    "ema:charge_lines": ("EMA Encounter & Charge Lines export", "EMA Encounter & Charge Lines exports"),
                    "ema:claim_status": ("EMA Claim & Claim Status export", "EMA Claim & Claim Status exports"),
                    "ema:era_lines": ("EMA ERA & Payment Line Adjustments export", "EMA ERA & Payment Line Adjustments exports"),
                    "ema": ("other EMA export", "other EMA exports"), "other": ("other file", "other files")}


def _signed(n: int) -> str:
    return f"{n:+,}"


def _signed_money(c) -> str:
    return ("-" if (c or 0) < 0 else "+") + money(abs(c or 0))


def _utc(iso: str | None) -> str:
    """A run's time as people read it: '2026-11-22T10:17:55+00:00' is '2026-11-22 10:17 UTC'."""
    return f"{iso[:10]} {iso[11:16]} UTC" if iso and len(iso) >= 16 else "an unknown time"


def _and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _swing_over(n: int, cents: int, rise_n: int, rise_c: int, fall_n: int, fall_c: int) -> list[str]:
    return [w for w, hit in (("items", n > rise_n or -n > fall_n), ("dollars", cents > rise_c or -cents > fall_c)) if hit]


def swing_between(conn: sqlite3.Connection, before: dict, after: dict) -> dict | None:
    """What one detection run (`after`) did to the open worklist against the run before it (`before`): the open items and
    dollars each run saw, by category; people's own status changes between the two (every change in finding_events the
    analysis did not make); and what is left once those are taken out, the run's own change. `over` names the alarm levels
    the change passes (empty for an ordinary run); `files` counts the files read between the two runs, by kind. None when a
    run's snapshot cannot be known: no row for it and none from any run before it (a store older than progress_snapshots,
    or a first run on an empty store, whose next run is the first load and not a swing). A run with no row while earlier runs
    have rows left no open item at all: that is everything gone, the largest fall there is, and it counts as zero."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    seen = []
    for run in (before, after):
        rows = conn.execute("SELECT category, status, n, cents FROM progress_snapshots WHERE run_id=?", (run["run_id"],)).fetchall()
        if not rows and conn.execute("SELECT 1 FROM progress_snapshots WHERE at < ? LIMIT 1", (run["started_at"],)).fetchone() is None:
            return None
        got: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for cat, status, n, cents in rows:
            if cat != "info" and status in SWING_OPEN:         # the context rows are not work
                got[cat][0] += n or 0
                got[cat][1] += cents or 0
        seen.append(got)
    t0, t1 = before["started_at"], after["started_at"]
    people: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    if {"finding_events", "findings"} <= have:
        for cat, was, now, cents in conn.execute("""SELECT f.category, e.from_status, e.to_status, COALESCE(f.shortfall_cents, 0)
                                                    FROM finding_events e JOIN findings f ON f.finding_id = e.finding_id
                                                    WHERE e.at > ? AND e.at <= ? AND COALESCE(e.actor, '') != 'system'""", (t0, t1)):
            step = (now in SWING_OPEN) - (was in SWING_OPEN)      # +1 back onto the open list, -1 off it
            if step and cat != "info":
                people[cat][0] += step
                people[cat][1] += step * cents
    cats = []
    for cat in sorted(set(seen[0]) | set(seen[1]) | set(people)):
        (bn, bc), (an, ac), (pn, pc) = seen[0].get(cat, (0, 0)), seen[1].get(cat, (0, 0)), people.get(cat, (0, 0))
        c = {"category": cat, "label": SWING_CATEGORY_LABELS.get(cat, str(cat).replace("_", " ").capitalize()),
             "before_n": bn, "after_n": an, "before_cents": bc, "after_cents": ac, "people_n": pn, "people_cents": pc,
             "change_n": an - bn - pn, "change_cents": ac - bc - pc}
        c["over"] = _swing_over(c["change_n"], c["change_cents"], SWING_RISE_ITEMS_CATEGORY, SWING_RISE_CENTS_CATEGORY,
                                SWING_FALL_ITEMS_CATEGORY, SWING_FALL_CENTS_CATEGORY)
        cats.append(c)
    total = {k: sum(c[k] for c in cats) for k in ("before_n", "after_n", "before_cents", "after_cents", "people_n", "people_cents",
                                                    "change_n", "change_cents")}
    over = [f"all {w}" for w in _swing_over(total["change_n"], total["change_cents"], SWING_RISE_ITEMS, SWING_RISE_CENTS,
                                            SWING_FALL_ITEMS, SWING_FALL_CENTS)]
    over += [f"{c['label']} {w}" for c in cats for w in c["over"]]
    files: Counter = Counter()
    if "files" in have:
        for k, n in conn.execute("SELECT kind, COUNT(*) FROM files WHERE ingested_at > ? AND ingested_at <= ? GROUP BY kind", (t0, t1)):
            files[k if k in SWING_FILE_KINDS else ("ema" if str(k).startswith("ema:") else "other")] += n
    return {"before": before, "after": after, "by_category": cats, "total": total, "over": over, "files": dict(files)}


def _swing_lead(m: dict) -> str:
    """The first words of the alert: the change that passed an alarm level, all categories first."""
    t = m["total"]
    verb = lambda x: "added" if x > 0 else "removed"       # noqa: E731
    if "all items" in m["over"]:
        return f"One analysis run {verb(t['change_n'])} {abs(t['change_n']):,} open items ({_signed_money(t['change_cents'])})"
    if "all dollars" in m["over"]:
        return f"One analysis run {verb(t['change_cents'])} {money(abs(t['change_cents']))} of open items ({_signed(t['change_n'])} items)"
    c = max((c for c in m["by_category"] if c["over"]), key=lambda c: (abs(c["change_n"]), abs(c["change_cents"])))
    if "items" in c["over"]:
        return f"One analysis run {verb(c['change_n'])} {abs(c['change_n']):,} open items in {c['label']} ({_signed_money(c['change_cents'])})"
    return f"One analysis run {verb(c['change_cents'])} {money(abs(c['change_cents']))} of open items in {c['label']} ({_signed(c['change_n'])} items)"


def _swing_files(files: dict, since: str) -> str:
    if not files:
        return "No files were read between the two runs."
    parts = []
    for k in sorted(files, key=lambda k: (k != "835", k)):
        one, many = SWING_FILE_KINDS[k]
        parts.append(f"{files[k]:,} {one if files[k] == 1 else many}")
    return f"Read since the run before ({since}): {_and(parts)}."


def _swing_levels() -> str:
    return (f"Alarm levels for one run: more than {SWING_RISE_ITEMS:,} open items or {money(SWING_RISE_CENTS)} added, or more than "
            f"{SWING_FALL_ITEMS:,} items or {money(SWING_FALL_CENTS)} removed, in all; in one category, more than "
            f"{SWING_RISE_ITEMS_CATEGORY:,} items or {money(SWING_RISE_CENTS_CATEGORY)} added, or more than "
            f"{SWING_FALL_ITEMS_CATEGORY:,} items or {money(SWING_FALL_CENTS_CATEGORY)} removed. Changes people made themselves "
            "between the two runs do not count.")


class AlertEngine:
    def __init__(self, conn: sqlite3.Connection, run_id: str | None = None, today: str | None = None, asof: str | None = None):
        self.conn = conn
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        conn.executescript(ALERTS_DDL)
        self.run_id = run_id
        self.now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.today = today or date.today().isoformat()
        # the day the remittances reach for most payers (detect.feed_date), not the newest payment of any one: one stray file
        # must not move every window and clear the stale-feed alert. asof can be set to replay history
        self.asof = asof or feed_date(conn, self.today) or self.today
        self.recent_start = _shift(self.asof, -RECENT_DAYS)
        self.base_start = _shift(self.recent_start, -BASELINE_DAYS)
        self.active: dict[str, dict] = {}
        self.names = {r["npi"]: r["name"] for r in conn.execute("SELECT npi, name FROM providers")}

    # ------------------------------------------------------------------ helpers
    def _raise(self, kind, severity, title, detail, payer=None, family=None, scope=None, metric=None, baseline=None, n=None, cents=None, key=None):
        aid = _aid(kind, *(key or (payer, family, scope)))
        self.active[aid] = {"alert_id": aid, "kind": kind, "severity": severity, "title": title, "detail": json.dumps(detail, default=str),
                            "payer_group": payer, "family": family, "scope": scope, "metric": metric, "baseline": baseline,
                            "n_recent": n, "cents_recent": cents, "window_start": self.recent_start, "window_end": self.asof}

    def _exposure(self, scope: str, start: str, end: str, by_npi: bool = False) -> dict:
        """Line counts in (start, end] by payer (and provider)."""
        cond = {"em": "AND scope IN ('em','em4')", "em4": "AND scope='em4'"}.get(scope, "")
        cols = "payer_group, npi" if by_npi else "payer_group"
        out = {}
        for r in self.conn.execute(f"SELECT {cols}, SUM(lines) n FROM exposure_daily WHERE day > ? AND day <= ? {cond} GROUP BY {cols}", (start, end)):
            out[(r["payer_group"], r["npi"]) if by_npi else r["payer_group"]] = r["n"] or 0
        return out

    # ------------------------------------------------------------------ rules
    def ramp_rules(self) -> None:
        for fam, spec in FAMILIES.items():
            exp_recent = self._exposure(spec["scope"], self.recent_start, self.asof)
            exp_base = self._exposure(spec["scope"], self.base_start, self.recent_start)
            recent, base, ever, last_before = Counter(), Counter(), Counter(), {}
            cents = Counter()
            sig_recent: dict[str, Counter] = defaultdict(Counter)
            for r in self.conn.execute(f"""SELECT payer_group, signal, payment_date d, shortfall_cents c FROM findings
                                           WHERE {spec['where']} AND {LIVE} AND payment_date IS NOT NULL AND payer_group IS NOT NULL"""):
                p, d = r["payer_group"], r["d"]
                if self.recent_start < d <= self.asof:
                    recent[p] += 1
                    cents[p] += max(r["c"] or 0, 0)
                    sig_recent[p][r["signal"]] += 1
                elif self.base_start < d <= self.recent_start:
                    base[p] += 1
                elif d <= self.base_start:
                    ever[p] += 1
                if d <= self.recent_start and d > last_before.get(p, ""):
                    last_before[p] = d
            for p, k in recent.items():
                n_recent, n_base = exp_recent.get(p, 0), exp_base.get(p, 0)
                if k < 3 or n_recent <= 0:
                    continue
                rate_recent = k / n_recent
                detail = {"recent_count": k, "recent_lines": n_recent, "baseline_count": base[p], "baseline_lines": n_base,
                          "recent_dollars": cents[p], "signals": dict(sig_recent[p].most_common(5)),
                          "recent_window": [self.recent_start, self.asof], "baseline_window": [self.base_start, self.recent_start]}
                sev = "high" if (cents[p] >= 250000 or k >= 10) else "medium"
                if base[p] == 0:
                    if n_base < 20:
                        continue        # no baseline volume with this payer: nothing to compare against
                    if ever[p] > 0:
                        quiet = (date.fromisoformat(self.recent_start) - date.fromisoformat(last_before[p])).days if p in last_before else None
                        detail["quiet_days"] = quiet
                        self._raise("RESUMED", sev, f"{p} has restarted {spec['label']}: {k} lines worth {money(cents[p])} in the last {RECENT_DAYS} days "
                                    f"after about {quiet} quiet days", detail, p, fam, None, rate_recent, 0.0, k, cents[p])
                    else:
                        self._raise("NEW_BEHAVIOR", sev, f"{p} has started {spec['label']}: {k} lines worth {money(cents[p])} in the last {RECENT_DAYS} days, "
                                    "never seen from this payer before", detail, p, fam, None, rate_recent, 0.0, k, cents[p])
                    continue
                if k < 4 or n_base <= 0:
                    continue
                rate_base = base[p] / n_base
                p_value = poisson_sf(k, rate_base * n_recent)
                detail.update({"recent_rate": round(rate_recent, 4), "baseline_rate": round(rate_base, 4), "p_value": round(p_value, 5)})
                if rate_recent >= 2 * rate_base and p_value < 0.01:
                    self._raise("RAMP", sev, f"{p} is ramping up {spec['label']}: {k} of {n_recent:,} lines ({rate_recent:.1%}) in the last {RECENT_DAYS} days "
                                f"against {rate_base:.1%} over the prior six months, worth {money(cents[p])}", detail, p, fam, None, rate_recent, rate_base, k, cents[p])

    def rising_rule(self) -> None:
        """A slow climb: the last 91 days against the 365 days before them. A payer that adds a little every month never
        doubles its own recent rate, so the four-week rule alone would miss it."""
        recent_start = _shift(self.asof, -91)
        base_start = _shift(recent_start, -365)
        covered = {(a["payer_group"], a["family"]) for a in self.active.values() if a["kind"] in ("RAMP", "RESUMED", "NEW_BEHAVIOR")}
        for fam, spec in FAMILIES.items():
            exp_recent = self._exposure(spec["scope"], recent_start, self.asof)
            exp_base = self._exposure(spec["scope"], base_start, recent_start)
            recent, base, cents = Counter(), Counter(), Counter()
            for r in self.conn.execute(f"""SELECT payer_group, payment_date d, shortfall_cents c FROM findings
                                           WHERE {spec['where']} AND {LIVE} AND payment_date > ? AND payment_date <= ? AND payer_group IS NOT NULL""",
                                       (base_start, self.asof)):
                if r["d"] > recent_start:
                    recent[r["payer_group"]] += 1
                    cents[r["payer_group"]] += max(r["c"] or 0, 0)
                else:
                    base[r["payer_group"]] += 1
            for p, k in recent.items():
                n_recent, n_base = exp_recent.get(p, 0), exp_base.get(p, 0)
                if (p, fam) in covered or k < 6 or n_recent < 30 or n_base < 100 or base[p] == 0:
                    continue
                rate_recent, rate_base = k / n_recent, base[p] / n_base
                p_value = poisson_sf(k, rate_base * n_recent)
                if rate_recent >= 1.75 * rate_base and p_value < 0.01:
                    self._raise("RISING", "high" if (cents[p] >= 250000 or k >= 15) else "medium",
                                f"{p} has been climbing on {spec['label']}: {k} of {n_recent:,} lines ({rate_recent:.1%}) over the last three months "
                                f"against {rate_base:.1%} over the year before, worth {money(cents[p])}",
                                {"recent_count": k, "recent_lines": n_recent, "baseline_count": base[p], "baseline_lines": n_base, "recent_dollars": cents[p],
                                 "recent_rate": round(rate_recent, 4), "baseline_rate": round(rate_base, 4), "p_value": round(p_value, 5),
                                 "recent_window": [recent_start, self.asof], "baseline_window": [base_start, recent_start]},
                                p, fam, None, rate_recent, rate_base, k, cents[p], key=(p, fam, "rising"))

    def provider_rule(self) -> None:
        """One provider carrying a payer's downcoding: the lists payers keep are per provider."""
        start = _shift(self.asof, -90)
        spec = FAMILIES["downcoding"]
        exp = self._exposure("em4", start, self.asof, by_npi=True)
        hits: Counter = Counter()
        cents: Counter = Counter()
        for r in self.conn.execute(f"""SELECT payer_group, rendering_provider_npi npi, shortfall_cents c FROM findings
                                       WHERE {spec['where']} AND {LIVE} AND payment_date > ? AND payment_date <= ?
                                         AND rendering_provider_npi IS NOT NULL AND billed_code IN ('99204','99205','99214','99215')""", (start, self.asof)):
            hits[(r["payer_group"], r["npi"])] += 1
            cents[(r["payer_group"], r["npi"])] += max(r["c"] or 0, 0)
        for (payer, npi), k in hits.items():
            n = exp.get((payer, npi), 0)
            if k < 4 or n < k:
                continue
            k_other = sum(v for (p, x), v in hits.items() if p == payer and x != npi)
            n_other = sum(v for (p, x), v in exp.items() if p == payer and x != npi)
            if n_other < 30:
                continue
            rate, rate_other = k / n, k_other / n_other
            p_value = poisson_sf(k, max(rate_other, 0.002) * n)
            if rate >= 3 * max(rate_other, 0.002) and p_value < 0.01:
                name = self.names.get(npi) or npi
                self._raise("PROVIDER_TARGETED", "high" if k >= 8 else "medium",
                            f"{payer} is downcoding {name} far more than the rest of the practice: {k} of {n} level 4 and 5 visits ({rate:.0%}) "
                            f"in 90 days against {rate_other:.1%} for everyone else", {"npi": npi, "provider": name, "count": k, "visits": n, "others_count": k_other,
                                                                            "others_visits": n_other, "dollars": cents[(payer, npi)], "p_value": round(p_value, 5),
                                                                            "window": [start, self.asof]},
                            payer, "downcoding", npi, rate, rate_other, k, cents[(payer, npi)])

    def swing_rule(self) -> None:
        """SWING: one detection run added or removed far more open items or dollars than a run normally does (the levels,
        and how they were set, are above SWING_RISE_ITEMS). Every pair of consecutive runs whose later run is less than
        SWING_DAYS old is compared, so the alert is raised again by every run for seven days after the run that moved and
        never clears itself at the next quiet run; one a person acknowledged keeps that status (run() never resets it).
        When the rules version changed between the two runs (a release re-analyses every claim once) the alert says so
        and is info, not high."""
        have = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"detection_runs", "progress_snapshots"} <= have:
            return
        today = date.fromisoformat(self.today)

        def recent(iso) -> bool:
            try:
                return (today - date.fromisoformat(str(iso)[:10])).days < SWING_DAYS
            except ValueError:
                return False
        runs = [dict(r) for r in self.conn.execute("""SELECT run_id, started_at, version FROM detection_runs WHERE started_at IS NOT NULL
                                                     ORDER BY finished_at, started_at, run_id""")]
        for before, after in zip(runs, runs[1:]):
            if recent(after["started_at"]):
                m = swing_between(self.conn, before, after)
                if m and m["over"]:
                    self._raise_swing(m)
        # One raised already stays raised all week with the numbers it was raised with, even when its two runs can no
        # longer be compared the same way (a journal entry folded late, a snapshot that is gone).
        for r in self.conn.execute("SELECT * FROM alerts WHERE kind='SWING' AND status != 'cleared'").fetchall():
            if r["alert_id"] not in self.active and recent(r["window_end"]):
                self.active[r["alert_id"]] = {k: r[k] for k in ("alert_id", "kind", "severity", "title", "detail", "payer_group", "family",
                                                               "scope", "metric", "baseline", "n_recent", "cents_recent",
                                                               "window_start", "window_end")}

    def _raise_swing(self, m: dict) -> None:
        before, after, t = m["before"], m["after"], m["total"]
        changed = (before.get("version") or "") != (after.get("version") or "")
        since, at = _utc(before["started_at"]), _utc(after["started_at"])
        lead = _swing_lead(m)
        title = f"{lead} under new rules from a release" if changed else f"{lead}, far more than a run normally does"
        lines = [f"Open items in all categories (To do, Started and Sent): {t['before_n']:,} at the run before ({since}), "
                 f"{t['after_n']:,} after this run ({at}); {money(t['before_cents'])} before, {money(t['after_cents'])} after."]
        if t["people_n"] or t["people_cents"]:
            moved = (f"took {abs(t['people_n']):,} items ({money(abs(t['people_cents']))}) off the open list" if t["people_n"] < 0 else
                     f"put {t['people_n']:,} items ({money(abs(t['people_cents']))}) back on the open list" if t["people_n"] > 0 else
                     f"changed the open dollars by {_signed_money(t['people_cents'])}")
            lines.append(f"People's own status changes between the two runs {moved}; that is left out of what this run did.")
        lines.append(f"What this run did: {_signed(t['change_n'])} open items, {_signed_money(t['change_cents'])}.")
        for c in sorted(m["by_category"], key=lambda c: (-abs(c["change_n"]), -abs(c["change_cents"]), c["category"])):
            if c["change_n"] or c["change_cents"]:
                lines.append(f"{c['label']}: {c['before_n']:,} to {c['after_n']:,} open items, this run {_signed(c['change_n'])} "
                             f"({_signed_money(c['change_cents'])}).")
        lines.append(_swing_files(m["files"], since))
        if changed:
            lines.append(f"The rules changed between the two runs (version {before.get('version') or 'unknown'} to "
                         f"{after.get('version') or 'unknown'}): a release re-checks every claim once, so part or all of this "
                         "change can come from the release.")
        lines.append(f"This alert stays at the top of every page for {SWING_DAYS} days after the run, unless someone acknowledges it "
                     "once the files are checked.")
        lines.append(_swing_levels())
        action = (f"Before working new items, open the Data tab and check the files read since {since}. "
                  f"If one is wrong, tell {SWING_CONTACT} and do not work the new items yet.")
        detail = {"run": after["run_id"], "run_before": before["run_id"], "at": after["started_at"], "since": before["started_at"],
                  "rules": [before.get("version"), after.get("version")], "rules_changed": changed,
                  "before": {"n": t["before_n"], "cents": t["before_cents"]}, "after": {"n": t["after_n"], "cents": t["after_cents"]},
                  "people": {"n": t["people_n"], "cents": t["people_cents"]}, "change": {"n": t["change_n"], "cents": t["change_cents"]},
                  "by_category": m["by_category"], "over": m["over"], "files": m["files"], "headline": lead, "lines": lines,
                  "action": action}
        self._raise("SWING", "info" if changed else "high", title, detail, None, "swing", after["run_id"], t["change_n"], None,
                    abs(t["change_n"]), abs(t["change_cents"]), key=("swing", after["run_id"]))
        a = self.active[_aid("SWING", "swing", after["run_id"])]
        a["window_start"], a["window_end"] = before["started_at"], after["started_at"]       # the two runs, not a payment window

    def rate_cut_rule(self) -> None:
        recent_start = _shift(self.asof, -60)
        base_start = _shift(recent_start, -180)
        recent: dict[tuple, Counter] = defaultdict(Counter)
        base: dict[tuple, Counter] = defaultdict(Counter)
        first_seen: dict[tuple, str] = {}
        kinds: dict[str, str] = {}
        for r in self.conn.execute("SELECT * FROM rate_points WHERE day > ? AND day <= ?", (base_start, self.asof)):
            key = (r["payer_group"], r["code"], r["tier"], r["pos_class"])
            kinds[r["payer_group"]] = r["payer_kind"]
            if r["day"] > recent_start:
                recent[key][r["allowed_cents"]] += r["n"]
                fk = key + (r["allowed_cents"],)
                if r["day"] < first_seen.get(fk, "9999"):
                    first_seen[fk] = r["day"]
            else:
                base[key][r["allowed_cents"]] += r["n"]
        cuts: dict[str, list[dict]] = defaultdict(list)
        for key, cnt in recent.items():
            n_r, n_b = sum(cnt.values()), sum(base[key].values())
            if n_r < 12 or n_b < 30:
                continue
            mode_r, share_r = cnt.most_common(1)[0][0], cnt.most_common(1)[0][1] / n_r
            mode_b, share_b = base[key].most_common(1)[0][0], base[key].most_common(1)[0][1] / n_b
            if share_r < 0.5 or share_b < 0.5 or mode_r >= mode_b * 0.98:
                continue
            payer, code, tier, pos = key
            cuts[payer].append({"code": code, "tier": tier, "pos_class": pos, "old_cents": mode_b, "new_cents": mode_r,
                                "pct": round(mode_r / mode_b - 1, 4), "since": first_seen.get(key + (mode_r,), recent_start),
                                "recent_lines": n_r, "baseline_lines": n_b, "annual_cents": int((mode_b - mode_r) * (n_r + n_b) * 365 / 240)})
        for payer, items in cuts.items():
            annual = sum(i["annual_cents"] for i in items)
            if annual < 25000:
                continue
            items.sort(key=lambda i: -i["annual_cents"])
            since = min(i["since"] for i in items)
            follows_medicare = kinds.get(payer) in ("medicare", "medicare_advantage", "medicaid") and since[5:7] == "01"
            codes = sorted({i["code"] for i in items})
            lo, hi = min(i["pct"] for i in items), max(i["pct"] for i in items)
            span = f"{-hi:.0%} to {-lo:.0%}" if round(lo, 2) != round(hi, 2) else f"{-lo:.0%}"
            shown = "; ".join(f"{i['code']} ${i['old_cents']/100:.2f} to ${i['new_cents']/100:.2f}" for i in items[:4])
            self._raise("RATE_CUT", "info" if follows_medicare else ("high" if annual >= 500000 else "medium"),
                        f"{payer} cut its usual allowed amount on {len(codes)} code{'s' if len(codes) != 1 else ''} by {span} from about {since} "
                        f"({shown}), roughly {money(annual)} a year at current volume",
                        {"codes": items, "since": since, "annual_cents": annual,
                         "note": "January changes at Medicare-based payers usually follow the fee schedule update." if follows_medicare else
                                 "No contract change explains this unless the payer sent notice; ask for the fee schedule in effect."},
                        payer, "rate", None, None, None, sum(i["recent_lines"] for i in items), annual)

    def new_code_rule(self) -> None:
        seen: dict[tuple, list] = {}
        for r in self.conn.execute("""SELECT l.payer_name, cl.claim_filing_indicator cfi, a.reason_code rc, a.group_code g, MIN(l.payment_date) first, COUNT(*) n,
                                             SUM(CASE WHEN l.payment_date > ? THEN 1 ELSE 0 END) recent, SUM(CASE WHEN l.payment_date > ? THEN a.amount_cents ELSE 0 END) cents
                                      FROM adjustments a JOIN lines l ON l.line_key = a.line_key JOIN claims cl ON cl.claim_key = l.claim_key
                                      JOIN transactions t ON t.transaction_key = l.transaction_key
                                      WHERE t.superseded = 0 AND a.group_code IN ('CO','PI','OA') GROUP BY 1,2,3""", (self.recent_start, self.recent_start)):
            grp, kind = payer_group_kind(r["payer_name"], r["cfi"])
            s = seen.setdefault((grp, r["rc"]), ["9999", 0, 0, 0])
            s[0] = min(s[0], r["first"] or "9999")
            s[1] += r["n"]
            s[2] += r["recent"] or 0
            s[3] += r["cents"] or 0
        payer_first: dict[str, str] = {}
        for (grp, _rc), s in seen.items():
            payer_first[grp] = min(payer_first.get(grp, "9999"), s[0])
        for (grp, rc), s in seen.items():
            if s[0] > self.recent_start and s[2] >= 5 and payer_first[grp] <= self.base_start:
                self._raise("NEW_REASON_CODE", "medium" if s[3] >= 50000 else "info",
                            f"{grp} started using reason code {rc} ({carc_text(rc) or 'no description'}): {s[2]} lines, {money(s[3])} since {s[0]}",
                            {"reason_code": rc, "text": carc_text(rc), "lines": s[2], "cents": s[3], "first_seen": s[0]}, grp, "reason_code", rc, None, None, s[2], s[3])

    def deadline_rule(self) -> None:
        week = date.fromisoformat(self.today).isocalendar()
        horizon = _shift(self.today, 30)
        rows = [dict(r) for r in self.conn.execute(
            """SELECT payer_group, COUNT(*) n, SUM(shortfall_cents) cents, MIN(appeal_deadline) first FROM findings
               WHERE status IN ('open','in_progress') AND COALESCE(stale,0) = 0 AND actionable IN ('appeal','correct_and_resubmit')
                 AND appeal_deadline >= ? AND appeal_deadline <= ? GROUP BY 1 ORDER BY cents DESC""", (self.today, horizon))]
        # a payer's routine lower rate counts for its claims whose own windows close in the period
        from .detect import pattern_windows
        by_payer = {r["payer_group"]: r for r in rows}
        counted: set = set()
        for fid, grp, _code, d, _n, cents in pattern_windows(self.conn, ("open", "in_progress")):
            if d and self.today <= d <= horizon:
                r = by_payer.setdefault(grp, {"payer_group": grp, "n": 0, "cents": 0, "first": d})
                r["cents"] = (r["cents"] or 0) + cents
                r["first"] = min(r["first"] or d, d)
                if fid not in counted:
                    counted.add(fid)
                    r["n"] += 1
        rows = sorted(by_payer.values(), key=lambda r: -(r["cents"] or 0))
        n, cents = sum(r["n"] for r in rows), sum(r["cents"] or 0 for r in rows)
        if cents >= 100000:
            top = ", ".join(f"{r['payer_group']} {money(r['cents'])}" for r in rows[:4])
            self._raise("DEADLINE", "high" if cents >= 1000000 else "medium",
                        f"{n} open findings worth {money(cents)} reach their filing deadline by {horizon}: {top}",
                        {"count": n, "cents": cents, "horizon": horizon, "by_payer": rows[:15]},
                        None, "deadline", None, None, None, n, cents, key=("deadline", week[0], week[1]))

    def stale_rule(self) -> None:
        r = self.conn.execute("SELECT MAX(ingested_at) last FROM files WHERE kind='835'").fetchone()
        last_ingest = (r["last"] or "")[:10] if r else ""
        behind = (date.fromisoformat(self.today) - date.fromisoformat(self.asof)).days
        since_ingest = (date.fromisoformat(self.today) - date.fromisoformat(last_ingest)).days if last_ingest else None
        if behind > max(STALE_DAYS, 14) or (since_ingest is not None and since_ingest > STALE_DAYS):
            self._raise("STALE_FEED", "high", f"No new remittances: the remittances on file reach {self.asof} for most payers ({behind} days ago) and the last new "
                        f"file arrived {last_ingest or 'never'}. Check the TriZetto download and the Dropbox upload.",
                        {"newest_payment": self.asof, "days_behind": behind, "last_file_ingested": last_ingest, "days_since_ingest": since_ingest},
                        None, "feed", None, behind, STALE_DAYS, None, None, key=("feed",))

    # ------------------------------------------------------------------ run
    def evaluate(self) -> dict[str, dict]:
        """Compute the alerts that hold now without storing anything."""
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "findings" in tables and "exposure_daily" in tables:
            self.ramp_rules()
            self.rising_rule()
            self.provider_rule()
            self.rate_cut_rule()
            self.new_code_rule()
            self.deadline_rule()
            self.swing_rule()
        self.stale_rule()
        return self.active

    def run(self) -> dict:
        self.evaluate()
        existing = {r["alert_id"]: dict(r) for r in self.conn.execute("SELECT * FROM alerts")}
        new_ids = []
        for aid, a in self.active.items():
            prev = existing.get(aid)
            if prev and prev["status"] != "cleared":
                self.conn.execute("""UPDATE alerts SET severity=?, title=?, detail=?, metric=?, baseline=?, n_recent=?, cents_recent=?, window_start=?,
                                     window_end=?, last_seen=?, run_id=? WHERE alert_id=?""",
                                  (a["severity"], a["title"], a["detail"], a["metric"], a["baseline"], a["n_recent"], a["cents_recent"],
                                   a["window_start"], a["window_end"], self.now, self.run_id, aid))
            else:
                new_ids.append(aid)
                self.conn.execute("""INSERT OR REPLACE INTO alerts (alert_id, kind, severity, title, detail, payer_group, family, scope, metric, baseline,
                                     n_recent, cents_recent, window_start, window_end, first_seen, last_seen, status, acknowledged_at, notified_at, run_id)
                                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new', NULL, NULL, ?)""",
                                  (aid, a["kind"], a["severity"], a["title"], a["detail"], a["payer_group"], a["family"], a["scope"], a["metric"], a["baseline"],
                                   a["n_recent"], a["cents_recent"], a["window_start"], a["window_end"], self.now, self.now, self.run_id))
        cleared = [aid for aid, prev in existing.items() if aid not in self.active and prev["status"] != "cleared"]
        self.conn.executemany("UPDATE alerts SET status='cleared', last_seen=? WHERE alert_id=?", [(self.now, aid) for aid in cleared])
        self.conn.commit()
        return {"active": len(self.active), "new": len(new_ids), "cleared": len(cleared), "asof": self.asof,
                "by_kind": dict(Counter(a["kind"] for a in self.active.values()))}


def run_alerts(conn: sqlite3.Connection, run_id: str | None = None, today: str | None = None) -> dict:
    return AlertEngine(conn, run_id, today).run()


SEV_ORDER = {"high": 0, "medium": 1, "info": 2}


def pending_notifications(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    # a big change in one run is mailed whatever its severity: a release's is info, and it still moves the list people work
    rows = [dict(r) for r in conn.execute("""SELECT * FROM alerts WHERE status='new' AND notified_at IS NULL
                                             AND (severity IN ('high','medium') OR kind='SWING')""")]
    return sorted(rows, key=lambda a: (SEV_ORDER.get(a["severity"], 9), -(a["cents_recent"] or 0)))


def _swing_detail(a: dict) -> dict:
    d = a.get("detail")
    try:
        d = json.loads(d) if isinstance(d, str) else d
    except ValueError:
        d = None
    return d if isinstance(d, dict) else {}


def digest(alerts: list[dict], swings: list[dict] | None = None) -> str:
    """The alert mail. A big change in one run (`swings`: every one nobody has acknowledged, mailed before or not) leads it
    with its numbers and what to do; the other alerts follow, one line each."""
    lead = list(swings or [])
    known = {a.get("alert_id") for a in lead}
    lead += [a for a in alerts if a.get("kind") == "SWING" and a.get("alert_id") not in known]
    lines = [f"RemitWatch: {len(alerts)} new alert{'s' if len(alerts) != 1 else ''}", ""]
    for a in lead:
        d = _swing_detail(a)
        lines.append(f"[{a['severity'].upper()}] {a['title']}")
        lines += [f"  - {x}" for x in d.get("lines") or []]
        lines += [f"  What to do: {d['action']}", ""] if d.get("action") else [""]
    for a in alerts:
        if a.get("kind") != "SWING":
            lines.append(f"[{a['severity'].upper()}] {a['title']}")
    lines += ["", "Open the dashboard, Alerts tab, for the lines behind each alert. This message contains no patient information."]
    return "\n".join(lines)


def notify(conn: sqlite3.Connection, log=print) -> dict:
    """Send new alerts by email (SMTP settings of the weekly report) and to ALERT_WEBHOOK_URL when set. Each alert is sent once;
    a big change in one run that nobody has acknowledged also leads every later alert mail while it lasts."""
    alerts = pending_notifications(conn)
    if not alerts:
        return {"sent": 0}
    swings = [dict(r) for r in conn.execute("SELECT * FROM alerts WHERE kind='SWING' AND status='new' ORDER BY window_end DESC")]
    text = digest(alerts, swings)
    head = swings[0] if swings else alerts[0]
    sent_email = sent_hook = False
    try:
        from .report import email_report
        sent_email = email_report(text, subject=f"RemitWatch alert: {(_swing_detail(head).get('headline') or head['title'])[:90]}")
    except Exception as e:        # a mail failure must never stop the pipeline
        log(f"alert email failed: {e}")
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if url:
        try:
            import requests
            r = requests.post(url, json={"text": text}, timeout=15)
            sent_hook = r.status_code < 300
        except Exception as e:
            log(f"alert webhook failed: {type(e).__name__}")       # the error text would carry the address, which is the secret
    if sent_email or sent_hook:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.executemany("UPDATE alerts SET notified_at=? WHERE alert_id=?", [(now, a["alert_id"]) for a in alerts])
        conn.commit()
    return {"sent": len(alerts) if (sent_email or sent_hook) else 0, "email": sent_email, "webhook": sent_hook, "pending": len(alerts)}
