"""
The collections calculator: what a clinician's schedule is worth at each payer's own rates, now and with the contracts
that pay below Medicare brought up to it.

Everything comes from tables the dashboard's copy already holds (remitwatch.cloud SERVING_TABLES), so the calculator
needs no extra analysis run:

    provider_services   what each clinician billed, by code and month of service, folded (one service per billed line
                        across payers, reversals and second payers): units, and what was finally allowed and paid
    provider_days       one row per clinician and day: patients seen, biopsy lesions, pathology the biopsies brought in
    provider_payers     allowed and paid (second payers included) by kind of payer: the share a patient is left to pay
    exposure            primary-claim lines by payer, clinician and code: which payers a service line is billed to
    payer_rates         each payer's usual allowed amount per code, year and tier (physician or not), and Medicare's

The page does the arithmetic as the sliders move. This module hands it, for one clinician (or the average physician or
APP of the practice), over a window of complete months of service:

    levers      the schedule now, per clinic day, in the terms a clinician plans in (Mohs cases, stages, share with a
                flap or graft, visits and the share at 99214 or higher, biopsies, frozen lesions...), and how each lever
                turns into billed codes (the clinician's own code mix inside it)
    codes       per code: units billed in the window, what was allowed per unit, the payer mix of its service line, and
                k = allowed per unit / what the payers' usual rates predict for that mix. k carries what the usual rate
                does not: multiple-procedure reductions, denials that stayed denied, units, underpayments. With the
                schedule left as it is and no contract corrected, the page reproduces what was allowed.
    rates       each payer's usual rate per code for the current year, and Medicare's
    payers      kind, the share of the allowed amount payers paid (second payers included), whether a contract can be
                corrected

Collections = allowed x [payers' share + patients' share x the share of patient balances collected]. The 835s say what
the patient was left to pay, not what the patient paid, so that last share is an input on the page.
"""
from __future__ import annotations

import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import date

from . import activity
from .mpfs import MPFS, MPFS_YEARS
from .pfs import PFS_INDICATORS

CLINIC_DAY_MIN = activity.CLINIC_DAY_MIN
MIN_K_UNITS = 25            # fewer units than this in the window: k comes from the lever's codes together, then 1
K_RANGE = (0.25, 2.0)       # k outside this means the rates do not describe the line (a facility setting, a code billed
                            # with another meaning, a product priced per unit): it is clamped, and the page says so
MIN_CLINIC_DAYS = 10        # clinicians with fewer clinic days in the window are not offered on their own
FULL_TIME_DAYS = 60         # clinic days in the window for a clinician to set the pace of a tier's average year
AK_LESIONS_15_PLUS = 20     # lesions assumed for a patient billed 17004 (15 or more); used only to turn lesions into patients
CORRECTABLE_KINDS = ("commercial", "medicare_advantage", "medicaid", "other")
LAYERED_WEIGHT = (0.21, 0.75)   # share of Mohs cases and of excisions closed in layers, practice-wide: used only to share a
                                 # clinician's layered repairs between the two (their own total is kept)
APP_MEDICARE_SHARE = 0.85   # Medicare pays an NP or PA 85% of the physician amount when the service is billed under their own number
MPPR_SHARE = 0.5            # second and later procedures of the same day: 50% of the usual rate (Medicare's standard rule)


def _add_on(code: str) -> bool:
    return (PFS_INDICATORS.get(code) or ("", ""))[0] == "ZZZ"


def _is_flap_graft(c: str) -> bool:
    """Adjacent tissue transfers, flaps and grafts (14000-15770), not the skin-substitute application codes (15271-15278),
    the wound-bed preparation codes (15002-15005) or the cosmetic and suture-removal codes above 15770."""
    if len(c) != 5 or not c.isdigit():
        return False
    n = int(c)
    return 14000 <= n <= 14350 or (15040 <= n <= 15261) or (15570 <= n <= 15770)


def _is_layered(c: str) -> bool:
    """Intermediate (12031-12057) and complex (13100-13160) repairs."""
    if len(c) != 5 or not c.isdigit():
        return False
    n = int(c)
    return 12031 <= n <= 12057 or 13100 <= n <= 13160


def _is_excision(c: str) -> bool:
    """Benign (11400-11446) and malignant (11600-11646) lesion excisions."""
    if len(c) != 5 or not c.isdigit():
        return False
    n = int(c)
    return 11400 <= n <= 11446 or 11600 <= n <= 11646


BIOPSY_ADD = ("11103", "11105", "11107")
BIOPSY_FIRST = tuple(c for c in activity.BIOPSY if c not in BIOPSY_ADD)
EM_NEW = activity.EM_NEW
EM_EST = activity.EM_EST


def lever_of(code: str) -> str:
    """Which lever of the page a billed code belongs to. Codes no lever describes are 'other' (scaled as a whole)."""
    if code in ("17311", "17313"):
        return "mohs_first"
    if code in ("17312", "17314"):
        return "mohs_stage"
    if code == "17315":
        return "mohs_block"
    if _is_flap_graft(code):
        return "flap_graft"
    if _is_layered(code):
        return "layered"
    if _is_excision(code):
        return "excision"
    if code in EM_NEW:
        return "visit_new"
    if code in EM_EST:
        return "visit_est"
    if code == "G2211":
        return "g2211"
    if code in BIOPSY_ADD:
        return "biopsy_add"
    if code in BIOPSY_FIRST:
        return "biopsy_first"
    if code in ("17000", "17003", "17004"):
        return "ak"
    if code in ("17110", "17111"):
        return "benign"
    return "other"


# --------------------------------------------------------------------------- rates

class Rates:
    """Each payer's usual allowed amount per unit, by tier, code and year, and what to use when a payer has none.

    A payer's contract is usually a percentage of Medicare, often with one percentage for visits and another for
    procedures, so a code a payer rarely pays is priced at that payer's own percentage of Medicare times Medicare's
    published amount. Only a payer with no usable rates at all borrows from the payers of its kind."""

    def __init__(self, conn: sqlite3.Connection, tier: str, states: dict[str, float]):
        self.tier, self.states = tier, states
        self.r: dict[tuple, int] = {}
        self.kind: dict[str, str] = {}
        n_year: Counter = Counter()
        for g, kind, year, code, t, mode in conn.execute(
                "SELECT payer_group, payer_kind, year, code, tier, mode_cents FROM payer_rates WHERE pos_class='office' AND mode_cents > 0"):
            self.r[(t, g, code, year)] = mode
            self.kind[g] = kind
            n_year[year] += 1
        # the current year is the newest with a fair number of rates (early in January a year has only a few)
        years = sorted(y for y, n in n_year.items() if n >= 100) or sorted(n_year)
        self.year = years[-1] if years else str(date.today().year)
        self._pct: dict[tuple, float | None] = {}
        by_kind: dict[tuple, list] = defaultdict(list)
        for (t, g, code, year), v in self.r.items():
            if t == tier:
                by_kind[(self.kind.get(g), code, year)].append(v)
        self.kind_median = {k: statistics.median(v) for k, v in by_kind.items()}
        ratios: dict[str, list] = defaultdict(list)          # an APP's rate as a share of a physician's at the same payer
        for (t, g, code, year), v in self.r.items():
            if t == "nonphysician":
                p = self.r.get(("physician", g, code, year))
                if p:
                    ratios[g].append(v / p)
        self.app_ratio = {g: statistics.median(v) for g, v in ratios.items() if len(v) >= 3}

    def medicare(self, code: str, year: str, tier: str | None = None) -> int | None:
        """Medicare's published amount for one unit in the office, weighted by the clinicians' states; 85% for an NP or PA."""
        tier = tier or self.tier
        tot = w = 0.0
        for st, share in self.states.items():
            v = (MPFS.get(year, {}).get(st, {}).get(code) or (None,))[0]
            if v:
                tot += share * v
                w += share
        if not w:
            return None
        return round(tot / w * (APP_MEDICARE_SHARE if tier == "nonphysician" else 1.0))

    def _medicare_group(self, g: str, code: str, year: str) -> int | None:
        """A Medicare contractor pays its own state's amount."""
        st = "IN" if " IN " in f" {g} " else "KY" if " KY " in f" {g} " else None
        if st and MPFS.get(year, {}).get(st, {}).get(code):
            v = MPFS[year][st][code][0]
            return round(v * (APP_MEDICARE_SHARE if self.tier == "nonphysician" else 1.0))
        return self.medicare(code, year)

    def pct_of_medicare(self, g: str, year: str, family: str) -> float | None:
        """The payer's own rates as a share of Medicare: the median over the codes of the same service family when it has
        at least three, else over all its codes (at least three)."""
        key = (g, year, family)
        if key in self._pct:
            return self._pct[key]
        fam, every = [], []
        for (t, gg, code, y), v in self.r.items():
            if t != self.tier or gg != g or y != year:
                continue
            m = self.medicare(code, year)
            if not m:
                continue
            every.append(v / m)
            if activity.service_family(code) == family:
                fam.append(v / m)
        out = statistics.median(fam) if len(fam) >= 3 else statistics.median(every) if len(every) >= 3 else None
        self._pct[key] = out
        return out

    def rate(self, g: str, code: str, year: str) -> tuple[int | None, str]:
        """(cents, how it was found): this payer's own usual rate; for Medicare, the published amount; the payer's own
        percentage of Medicare times Medicare's amount; last year's own rate; a physician's rate at the payer scaled to
        an APP's; the median of the payers of the same kind."""
        tier = self.tier
        v = self.r.get((tier, g, code, year))
        if v:
            return v, "own"
        if self.kind.get(g) == "medicare":
            v = self._medicare_group(g, code, year)
            if v:
                return v, "medicare_schedule"
        m = self.medicare(code, year)
        pct = self.pct_of_medicare(g, year, activity.service_family(code)) if m else None
        if m and pct:
            return round(m * pct), "payer_pct_of_medicare"
        v = self.r.get((tier, g, code, str(int(year) - 1)))
        if v:
            return v, "last_year"
        if tier == "nonphysician":
            p = self.r.get(("physician", g, code, year))
            if p:
                return round(p * (self.app_ratio.get(g) or APP_MEDICARE_SHARE)), "physician_rate"
        v = self.kind_median.get((self.kind.get(g), code, year))
        if v:
            return round(v), "kind_median"
        if m:
            return m, "medicare_amount"
        return None, "none"


# --------------------------------------------------------------------------- the clinician's history

def _group(conn, provider: str) -> tuple[list[str], str, dict]:
    """The clinicians a request covers, their tier, and how to name them. 'physicians' and 'apps' are the practice's
    average physician or APP: their days pooled, so a lever per clinic day is the average clinic day of that tier."""
    roster = {r["provider_id"]: dict(r) for r in conn.execute("SELECT * FROM provider_roster WHERE kind='person'")}
    if provider in ("physicians", "apps"):
        tier = "physician" if provider == "physicians" else "nonphysician"
        ids = [p for p, r in roster.items() if r.get("tier") == tier]
        return ids, tier, {"id": provider, "name": "Average physician" if tier == "physician" else "Average APP", "tier": tier,
                           "role": None, "clinicians": len(ids)}
    r = roster.get(provider)
    if not r:
        return [], "physician", {}
    tier = r.get("tier") if r.get("tier") in ("physician", "nonphysician") else "physician"
    return [provider], tier, {"id": provider, "name": r.get("name"), "tier": tier, "role": r.get("role"), "clinicians": 1}


def _in(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


def _month_plus(month: str, k: int) -> str:
    y, m = int(month[:4]), int(month[5:7]) - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def _schedule(conn, ids: list[str], d0: str, d1: str, window_weeks: float) -> dict:
    """Clinic days in the window and how they spread over weeks, pooled over the clinicians."""
    days = [(r[0], r[1]) for r in conn.execute(
        f"SELECT provider_id, service_date FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ? AND patients >= ?",
        (*ids, d0, d1, CLINIC_DAY_MIN))]
    per: dict[str, list] = defaultdict(list)
    for p, d in days:
        per[p].append(d)

    def pace(ds: list) -> tuple[float, float]:
        wk = len({date.fromisoformat(d).isocalendar()[:2] for d in ds})
        return (len(ds) / wk if wk else 0.0), (min(52.0, wk * 52 / window_weeks) if window_weeks else 0.0)

    # one clinician: their own pace. A tier: the median of the clinicians who held a fair number of clinic days, so a
    # part-timer or someone who joined late does not shorten the average year
    full = [pace(ds) for ds in per.values() if len(ds) >= FULL_TIME_DAYS] or [pace(ds) for ds in per.values()]
    dpw = statistics.median(x[0] for x in full) if full else 0.0
    wpy = statistics.median(x[1] for x in full) if full else 0.0
    return {"clinic_days": len(days), "clinicians_with_days": len(per),
            "days_per_week": round(dpw, 2), "weeks_per_year": round(wpy, 1), "clinic_days_per_year": round(dpw * wpy, 1)}


def _payer_mix(conn, ids: list[str], tier: str, a: str, b: str) -> tuple[dict, dict]:
    """Share of primary-claim lines by payer, per service family, for the clinicians; a family with few lines takes the
    mix of every clinician of the tier, then of the practice. Payment months run a month behind the months of service."""
    pa, pb = _month_plus(a, 1), _month_plus(b, 2)

    def mix(where: str, args: tuple) -> dict[str, Counter]:
        out: dict[str, Counter] = defaultdict(Counter)
        for code, g, n in conn.execute(
                f"SELECT code, payer_group, SUM(lines) FROM exposure WHERE month BETWEEN ? AND ? {where} GROUP BY 1, 2", (pa, pb, *args)):
            out[activity.service_family(activity.clean_code(code))][g] += n or 0
        return out

    own = mix(f"AND npi IN ({_in(ids)})", tuple(ids)) if ids else {}
    tier_ids = [r[0] for r in conn.execute("SELECT provider_id FROM provider_roster WHERE kind='person' AND tier=?", (tier,))]
    peers = mix(f"AND npi IN ({_in(tier_ids)})", tuple(tier_ids)) if tier_ids else {}
    practice = mix("", ())
    kinds = {g: k for g, k in conn.execute(f"SELECT DISTINCT payer_group, payer_kind FROM exposure WHERE month BETWEEN ? AND ?", (pa, pb))}
    out, source = {}, {}
    overall: Counter = Counter()
    for fam_counts in own.values():
        overall.update(fam_counts)
    for fam in set(own) | set(peers) | set(practice):
        for name, src in (("own", own), ("tier", peers), ("practice", practice)):
            c = src.get(fam) or Counter()
            n = sum(c.values())
            if n >= 30:
                out[fam] = {g: v / n for g, v in c.items() if v > 0}
                source[fam] = name
                break
    n_all = sum(overall.values())
    out["_overall"] = {g: v / n_all for g, v in overall.items()} if n_all else {}
    return out, {"source": source, "kinds": kinds}


def calculator(conn: sqlite3.Connection, provider: str = "physicians", month_from: str | None = None, month_to: str | None = None) -> dict | None:
    conn.row_factory = sqlite3.Row
    ids, tier, who = _group(conn, provider)
    if not who:
        return None
    cov, a, b = activity._window(conn, month_from, month_to)
    d0, d1 = f"{a}-01", f"{b}-31"
    window_weeks = activity._weeks(a, b)
    sched = _schedule(conn, ids, d0, d1, window_weeks) if ids else {"clinic_days": 0}
    states = _states(conn, ids, d0, d1)
    rates = Rates(conn, tier, states)
    year = rates.year

    # what was billed, by code, per month of service (for the rates of each month's year)
    units_m: dict[str, Counter] = defaultdict(Counter)
    svc: dict[str, dict] = {}
    if ids:
        for r in conn.execute(
                f"""SELECT code, month, family, SUM(items) items, SUM(units) units, SUM(denied) denied, SUM(allowed_cents) allowed, SUM(paid_cents) paid
                    FROM provider_services WHERE provider_id IN ({_in(ids)}) AND month BETWEEN ? AND ? GROUP BY 1, 2""", (*ids, a, b)):
            code = activity.clean_code(r["code"])
            s = svc.setdefault(code, {"family": r["family"], "items": 0, "units": 0.0, "denied": 0, "allowed": 0, "paid": 0})
            u = r["units"] or r["items"] or 0
            s["items"] += r["items"] or 0
            s["units"] += u
            s["denied"] += r["denied"] or 0
            s["allowed"] += r["allowed"] or 0
            s["paid"] += r["paid"] or 0
            units_m[code][r["month"][:4]] += u
    mix, mix_info = _payer_mix(conn, ids, tier, a, b)
    kinds = dict(rates.kind)
    kinds.update(mix_info["kinds"])
    for g, k in kinds.items():
        rates.kind.setdefault(g, k)

    def family_mix(code: str) -> dict:
        return mix.get(activity.service_family(code)) or mix.get("_overall") or {}

    # what was billed and allowed per code. The page compares it with what the payers' usual rates predict for the same
    # schedule (k), so it can reproduce what happened before it changes anything.
    codes = {}
    for code, s_ in svc.items():
        codes[code] = {"family": s_["family"], "lever": lever_of(code), "units": round(s_["units"], 2), "items": s_["items"],
                       "denied": s_["denied"], "allowed_cents": s_["allowed"], "paid_cents": s_["paid"]}
    # the codes of every lever the tier bills, so a clinician can plan a service not billed today
    for code in _lever_codes(conn, tier, a, b):
        codes.setdefault(code, {"family": activity.service_family(code), "lever": lever_of(code), "units": 0, "items": 0, "denied": 0,
                                "allowed_cents": 0, "paid_cents": 0})

    # every payer's usual rate in each code's mix, for each year of the window and the current year; Medicare's published
    # amount for the clinician's states (KY and IN), this year and next
    years = sorted({y for code in units_m for y in units_m[code]} | {year})
    groups_used: set[str] = set()
    rate_card: dict[str, dict] = {}
    sources: Counter = Counter()
    for code in codes:
        m = family_mix(code)
        row = {}
        for g in m:
            per_year = {}
            for yr in years:
                v, how = rates.rate(g, code, yr)
                if v:
                    per_year[yr] = v
                    if yr == year:
                        sources[how] += 1
                        per_year["how"] = how
            if per_year:
                row[g] = per_year
            groups_used.add(g)
        rate_card[code] = row
    medicare = {code: {y: v for y in MPFS_YEARS for v in [rates.medicare(code, y)] if v} for code in codes}
    for c in codes:
        codes[c]["units_by_year"] = {y: round(v, 2) for y, v in units_m.get(c, {}).items()}

    # the payers' share of the allowed amount (second payers included), per kind of payer
    paid_share = {}
    if ids:
        for kind, al, pd in conn.execute(
                f"SELECT payer_kind, SUM(allowed_cents), SUM(paid_cents) FROM provider_payers WHERE provider_id IN ({_in(ids)}) AND month BETWEEN ? AND ? GROUP BY 1",
                (*ids, a, b)):
            if al and al > 0:
                paid_share[kind] = round(min(max(pd / al, 0.0), 1.0), 4)
    # ...and by class of service: patients owe more of a visit (copayments) than of a Mohs case, so the page adjusts the
    # payer kind's share by how this clinician's own classes compare with their whole
    by_class: dict[str, list] = defaultdict(lambda: [0, 0])
    for s_ in svc.values():
        cl = paid_class(s_["family"])
        by_class[cl][0] += s_["allowed"]
        by_class[cl][1] += s_["paid"]
    tot_al = sum(v[0] for v in by_class.values())
    tot_pd = sum(v[1] for v in by_class.values())
    paid_share_class = {cl: round(v[1] / v[0], 4) for cl, v in by_class.items() if v[0] >= 100000}
    groups = {}
    for g in sorted(groups_used):
        kind = kinds.get(g) or "other"
        groups[g] = {"kind": kind, "paid_share": paid_share.get(kind), "correctable": kind in CORRECTABLE_KINDS,
                     "share": round(mix.get("_overall", {}).get(g, 0.0), 4)}
    fallback_paid = None
    if paid_share:
        tot_a = sum(1 for _ in paid_share)
        fallback_paid = round(sum(paid_share.values()) / tot_a, 4)

    levers = _levers(svc, sched.get("clinic_days") or 0)
    path = conn.execute(
        f"SELECT COALESCE(SUM(path_generated_cents),0), COALESCE(SUM(biopsy_lesions),0) FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ?",
        (*ids, d0, d1)).fetchone() if ids else (0, 0)
    totals = {"allowed_cents": sum(s["allowed"] for s in svc.values()), "paid_cents": sum(s["paid"] for s in svc.values())}
    by_family: dict[str, list] = defaultdict(lambda: [0, 0])
    for s in svc.values():
        by_family[s["family"]][0] += s["allowed"]
        by_family[s["family"]][1] += s["paid"]
    return {
        "who": who, "tier": tier, "from": a, "to": b, "window_weeks": round(window_weeks, 1), "rate_year": year, "years": years,
        "next_year": MPFS_YEARS[-1] if MPFS_YEARS[-1] > year else None, "states": states,
        "clinicians": [dict(r) for r in conn.execute(
            """SELECT r.provider_id, r.name, r.tier, r.role FROM provider_roster r WHERE r.kind='person' AND
               (SELECT COUNT(*) FROM provider_days d WHERE d.provider_id = r.provider_id AND d.service_date BETWEEN ? AND ? AND d.patients >= ?) >= ?
               ORDER BY r.tier, r.name""", (d0, d1, CLINIC_DAY_MIN, MIN_CLINIC_DAYS))],
        "coverage": {"last_complete_month": cov.get("last_complete_month"), "last_payment": cov.get("last_payment")},
        "schedule": sched, "levers": levers, "codes": codes,
        "mix": {f: [[g, round(v, 5)] for g, v in sorted(m.items(), key=lambda kv: -kv[1])] for f, m in mix.items()},
        "mix_source": mix_info["source"], "rates": rate_card, "medicare": medicare, "rate_sources": dict(sources),
        "payers": groups, "paid_share": paid_share, "paid_share_fallback": fallback_paid,
        "paid_share_class": paid_share_class, "paid_share_all": round(tot_pd / tot_al, 4) if tot_al else None,
        "pathology": {"generated_cents": path[0], "biopsy_lesions": path[1],
                      "per_lesion_cents": round(path[0] / path[1]) if path[1] else None},
        "actual": {**totals, "families": {f: {"allowed_cents": v[0], "paid_cents": v[1]} for f, v in by_family.items()}},
        "constants": {"ak_lesions_15_plus": AK_LESIONS_15_PLUS, "correctable_kinds": list(CORRECTABLE_KINDS), "k_range": list(K_RANGE),
                      "min_k_units": MIN_K_UNITS, "mppr_share": MPPR_SHARE, "app_medicare_share": APP_MEDICARE_SHARE},
        "add_on": sorted(c for c in codes if _add_on(c)),
        "mppr": sorted(c for c in codes if (PFS_INDICATORS.get(c) or ("", ""))[1] == "2"),
        "global_days": {c: (PFS_INDICATORS.get(c) or ("", ""))[0] for c in codes if PFS_INDICATORS.get(c)},
    }


def _states(conn, ids: list[str], d0: str, d1: str) -> dict[str, float]:
    """Share of the clinicians' clinic days in each state the fee schedule covers (KY, IN); Kentucky when unknown."""
    n: Counter = Counter()
    if ids:
        for place, k in conn.execute(
                f"SELECT place, COUNT(*) FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ? AND patients >= ? GROUP BY 1",
                (*ids, d0, d1, CLINIC_DAY_MIN)):
            st = (place or "").upper()
            if st in ("KY", "IN"):
                n[st] += k
    tot = sum(n.values())
    return {st: round(v / tot, 4) for st, v in n.items()} if tot else {"KY": 1.0}


def _lever_codes(conn, tier: str, a: str, b: str) -> list[str]:
    """Every code of the page's levers that the tier billed in the window, so a lever can be priced for anyone."""
    out = []
    for (code,) in conn.execute(
            """SELECT DISTINCT s.code FROM provider_services s JOIN provider_roster r ON r.provider_id = s.provider_id
               WHERE r.tier=? AND s.month BETWEEN ? AND ?""", (tier, a, b)):
        c = activity.clean_code(code)
        if lever_of(c) != "other":
            out.append(c)
    return sorted(set(out))


def _levers(svc: dict, clinic_days: int) -> dict:
    """The schedule now, per clinic day, in the page's terms, and how each lever turns into billed codes. The page turns
    the levers back into units with the same rules, so the levers as they come here give back, code for code, the units
    billed in the window.

    Layered closures (intermediate and complex repairs) follow Mohs cases and excisions alike, and a month's totals do
    not say which. They are shared out with the rates at which each kind of case is closed in layers across the archive
    (LAYERED_WEIGHT), scaled so the clinician's own total comes back exactly."""
    u = {c: s["units"] for c, s in svc.items() if s["units"] > 0}
    cd = clinic_days or 0

    def per_day(x: float) -> float:
        return x / cd if cd else 0.0

    def shares(codes) -> dict[str, float]:
        tot = sum(u.get(c, 0) for c in codes)
        return {c: u[c] / tot for c in codes if u.get(c)} if tot else {}

    by: dict[str, list] = defaultdict(list)
    for c in u:
        by[lever_of(c)].append(c)

    def primary_mix(codes) -> tuple[float, dict, dict]:
        """(primary units, share of each primary code, add-on units per primary)."""
        prim = [c for c in codes if not _add_on(c)]
        n = sum(u[c] for c in prim)
        return n, ({c: u[c] / n for c in prim} if n else {}), ({c: u[c] / n for c in codes if _add_on(c)} if n else {})

    hn, tr = u.get("17311", 0), u.get("17313", 0)
    cases = hn + tr
    # closures follow Mohs cases and excisions; a clinician who bills them with neither keeps them under 'everything else'
    if not cases:
        by["other"] += by.pop("flap_graft", [])
    if not cases and not sum(u[c] for c in by["excision"]):
        by["other"] += by.pop("layered", [])
    e_hn = u.get("17312", 0) / hn if hn else None
    e_tr = u.get("17314", 0) / tr if tr else None
    fg_n, fg_mix, fg_add = primary_mix(by["flap_graft"])
    lay_n, lay_mix, lay_add = primary_mix(by["layered"])
    exc_n = sum(u[c] for c in by["excision"])
    wm, we = LAYERED_WEIGHT
    scale = lay_n / (wm * cases + we * exc_n) if (wm * cases + we * exc_n) else 0.0
    new = {c: u.get(c, 0) for c in EM_NEW}
    est = {c: u.get(c, 0) for c in EM_EST}
    n_new, n_est = sum(new.values()), sum(est.values())
    visits = n_new + n_est
    bx_first = sum(u[c] for c in by["biopsy_first"])
    bx_add = sum(u[c] for c in by["biopsy_add"])
    ak0, ak3, ak4 = u.get("17000", 0), u.get("17003", 0), u.get("17004", 0)
    ak_pat = ak0 + ak4
    ak_n = 1 + ak3 / ak0 if ak0 else 4.0
    ak_f = ak4 / ak_pat if ak_pat else 0.07
    ben = u.get("17110", 0) + u.get("17111", 0)
    return {
        "mohs": {"cases": per_day(cases), "head_neck_share": hn / cases if cases else 0.62,
                 "stages": 1 + (u.get("17312", 0) + u.get("17314", 0)) / cases if cases else 1.47,
                 # extra stages per case at each site, relative to the average: kept as the slider moves the average
                 "extra_head_neck": e_hn, "extra_trunk": e_tr, "blocks": u.get("17315", 0) / cases if cases else 0.0},
        "flap_graft": {"share": min(fg_n / cases, 2.0) if cases else 0.40, "mix": fg_mix, "add_ons": fg_add,
                       "billed_without_mohs": fg_n if not cases else 0},
        "layered": {"mohs_share": min(wm * scale, 2.0) if cases else wm, "excision_share": min(we * scale, 2.0) if exc_n else we,
                    "mix": lay_mix, "add_ons": lay_add, "weights": [wm, we]},
        "excision": {"per_day": per_day(exc_n), "mix": shares(by["excision"])},
        "visits": {"per_day": per_day(visits), "new_share": n_new / visits if visits else 0.15,
                   "new_mix": {c: v / n_new for c, v in new.items() if v} if n_new else {"99203": 0.75, "99204": 0.25},
                   "est_mix": {c: v / n_est for c, v in est.items() if v} if n_est else {"99212": 0.09, "99213": 0.69, "99214": 0.22},
                   "g2211_per_visit": u.get("G2211", 0) / visits if visits else 0.0},
        "biopsy": {"lesions": per_day(bx_first + bx_add), "per_patient": (bx_first + bx_add) / bx_first if bx_first else 1.6,
                   "first_mix": shares(by["biopsy_first"]) or {"11102": 1.0}, "add_mix": shares(by["biopsy_add"]) or {"11103": 1.0}},
        "ak": {"lesions": per_day(ak_pat * (ak_f * AK_LESIONS_15_PLUS + (1 - ak_f) * ak_n)), "per_patient": ak_n, "share_15_plus": ak_f},
        "benign": {"patients": per_day(ben), "share_15_plus": u.get("17111", 0) / ben if ben else 0.02},
        "other": {"codes": {c: per_day(u[c]) for c in by["other"]}, "groups": {c: other_group(c) for c in by["other"]}},
    }


def paid_class(family: str | None) -> str:
    """Visits, Mohs, pathology, or other procedures: the classes whose patient shares differ most."""
    if family in ("new_visit", "est_visit", "visit_addon"):
        return "visit"
    if family in ("mohs", "pathology"):
        return family
    return "procedure"


def other_group(code: str) -> str:
    """Where a code the levers do not describe belongs on the page: radiation, skin substitutes (the product and its
    application), pathology, drugs, or the rest."""
    fam = activity.service_family(code)
    if fam == "radiation":
        return "radiation"
    if fam == "skin_substitute" or (len(code) == 5 and code.isdigit() and (15271 <= int(code) <= 15278 or 15002 <= int(code) <= 15005)):
        return "skin"
    if fam == "pathology":
        return "path"
    if fam == "drug":
        return "drug"
    return "rest"
