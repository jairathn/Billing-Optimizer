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
from .detect import DOWNCODE_SIGNALS, EM_SQL, ensure_schema

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


class AlertEngine:
    def __init__(self, conn: sqlite3.Connection, run_id: str | None = None, today: str | None = None, asof: str | None = None):
        self.conn = conn
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        conn.executescript(ALERTS_DDL)
        self.run_id = run_id
        self.now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.today = today or date.today().isoformat()
        r = conn.execute("SELECT MAX(payment_date) d FROM transactions WHERE superseded=0").fetchone()
        self.asof = asof or (r["d"] if r else None) or self.today      # asof can be set to replay history
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
            self._raise("STALE_FEED", "high", f"No new remittances: the newest payment in the store is dated {self.asof} ({behind} days ago) and the last new "
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
    rows = [dict(r) for r in conn.execute("SELECT * FROM alerts WHERE status='new' AND notified_at IS NULL AND severity IN ('high','medium')")]
    return sorted(rows, key=lambda a: (SEV_ORDER.get(a["severity"], 9), -(a["cents_recent"] or 0)))


def digest(alerts: list[dict]) -> str:
    lines = [f"RemitWatch: {len(alerts)} new alert{'s' if len(alerts) != 1 else ''}", ""]
    for a in alerts:
        lines.append(f"[{a['severity'].upper()}] {a['title']}")
    lines += ["", "Open the dashboard, Alerts tab, for the lines behind each alert. This message contains no patient information."]
    return "\n".join(lines)


def notify(conn: sqlite3.Connection, log=print) -> dict:
    """Send new alerts by email (SMTP settings of the weekly report) and to ALERT_WEBHOOK_URL when set. Each alert is sent once."""
    alerts = pending_notifications(conn)
    if not alerts:
        return {"sent": 0}
    text = digest(alerts)
    sent_email = sent_hook = False
    try:
        from .report import email_report
        sent_email = email_report(text, subject=f"RemitWatch alert: {alerts[0]['title'][:90]}")
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
