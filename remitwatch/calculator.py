"""
The collections calculator: what a clinician's schedule is worth at each payer's own rates, now and with the contracts
that pay below Medicare brought up to it.

Everything comes from tables the dashboard's copy already holds (remitwatch.cloud SERVING_TABLES), so the calculator
needs no extra analysis run:

    provider_services   what each clinician billed, by code and month of service, folded (one service per billed line
                        across payers, reversals and second payers): units, and what was finally allowed and paid; and
                        the same services by the payer that decided them (payers), for the payer mix of each code
    provider_days       one row per clinician and day: patients seen, biopsy lesions, pathology the biopsies brought in
    provider_payers     allowed and paid (second payers included) by kind of payer: the share a patient is left to pay;
                        and the Mohs closures and excisions done in the clinic or at the ASC (asc_cases), for the strategy lab
    exposure            primary-claim lines by payer, clinician and code, by payment month: the payer mix where the
                        dashboard's copy was built before provider_services kept its payers
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

import calendar
import json
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import activity, runtime
from .ascfs import ASC, WAGE_INDEX, asc_amount
from .codes import payer_group_kind
from .mpfs import MPFS, MPFS_YEARS, WORK_RVU
from .pfs import PFS_INDICATORS

CLINIC_DAY_MIN = activity.CLINIC_DAY_MIN
MIN_K_UNITS = 25            # fewer units than this in the window: k comes from the lever's codes together, then 1
K_RANGE = (0.25, 2.0)       # k outside this means the rates do not describe the line (a facility setting, a code billed
                            # with another meaning, a product priced per unit): it is clamped, and the page says so
MIN_CODE_MIX_LINES = 100   # primary-claim lines of a code that give it its own payer mix
CASE_LEVERS = ("mohs_first", "mohs_stage", "mohs_block", "flap_graft", "layered")   # billed with the Mohs case, to its payer
MIN_CLINIC_DAYS = 10        # clinicians with fewer clinic days in the window are not offered on their own
FULL_TIME_DAYS = 60         # clinic days in the window for a clinician to set the pace of a tier's average year
AK_LESIONS_15_PLUS = 20     # lesions assumed for a patient billed 17004 (15 or more); used only to turn lesions into patients
CORRECTABLE_KINDS = ("commercial", "medicare_advantage")     # contracts the page offers to correct. A Medicaid managed-care
                                                             # plan pays about the state's schedule and its contract is rarely
                                                             # negotiable, though not impossible (payers[g].negotiable is
                                                             # 'rarely'); the state's own Medicaid program is not, nor is
                                                             # 'other': workers' comp, auto and unknown payers
LAYERED_WEIGHT = (0.30, 0.79)   # layered (intermediate and complex) repairs per Mohs case and per excision (no Mohs that
                                 # day) on the same patient-day, across the clinic's and the ASC's claims, practice-wide over
                                 # the twelve months to September 2026 (the Mohs rate moves from year to year; a per-bill count
                                 # misses the closures billed on the ASC's own claim). Used only where the database cannot pair
                                 # a clinician's closures with their own Mohs cases and excisions (_paired_closures), to share
                                 # their layered repairs between the two (their own total is kept)
APP_MEDICARE_SHARE = 0.85   # Medicare pays an NP or PA 85% of the physician amount when the service is billed under their own number
MPPR_SHARE = 0.5            # second to fifth procedures of the same day: 50% of the usual rate (Medicare's standard rule;
                            # the sixth and later are paid 'by report', priced by the contractor, and taken at 50% here too)
PACE_GAP_DAYS = 42          # no clinic day this long before the pace window starts (or before the window ends): the
                            # clinician started (or stopped) inside it, longer than any holiday
ASC_SITE = {"office": "newburgh", "state": "IN", "label": "Newburgh"}    # where the practice's ASC is: Newburgh, Indiana (WPS,
                                                                         # CBSA 21780). Only cases seen at the Indiana office can
                                                                         # move to it the same day.
# An ASC that does not meet quality reporting (ASCQR) is paid on a lower conversion factor for A2, G2 and P2 codes (P3
# office-based codes are unchanged): CY2026 $55.224 against $56.322, CY2027 $56.592 against $57.721 (OPPS/ASC final rules)
ASCQR_FACTOR = {"2026": round(55.224 / 56.322, 6), "2027": round(56.592 / 57.721, 6)}


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
        self.year = years[-1] if years else runtime.practice_today()[:4]
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

    def medicare(self, code: str, year: str, tier: str | None = None, facility: bool = False) -> int | None:
        """Medicare's published amount for one unit in the office (or a facility), weighted by the clinicians' states; 85%
        for an NP or PA."""
        tier = tier or self.tier
        tot = w = 0.0
        for st, share in self.states.items():
            v = (MPFS.get(year, {}).get(st, {}).get(code) or (None, None))[1 if facility else 0]
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

    def indexed(self, g: str, year: str) -> bool:
        """Does this payer's schedule look like a flat percentage of Medicare (so next year it moves with Medicare's
        amounts)? Medicare itself and Medicare Advantage plans: yes; others when their own rates sit within a narrow band
        of Medicare across at least five codes."""
        kind = self.kind.get(g)
        if kind in ("medicare", "medicare_advantage"):
            return True
        ratios = sorted(v / m for (t, gg, code, y), v in self.r.items() if t == self.tier and gg == g and y == year
                        for m in [self.medicare(code, year)] if m)
        if len(ratios) < 5:
            return False
        return ratios[int(0.9 * (len(ratios) - 1))] - ratios[int(0.1 * (len(ratios) - 1))] <= 0.04

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

ROLES = {"mohs": ("physician", "Mohs surgeon"), "general": ("physician", "General dermatologist"), "apps": ("nonphysician", "APP")}
ANCILLARY_SHARE_MAX = 0.8   # a clinician whose allowed is mostly pathology or radiation is left out of an average
MOHS_PER_DAY_MIN = 1.0      # Mohs cases per clinic day that make a physician a Mohs surgeon for the averages


def _profiles(conn, d0: str, d1: str, a: str, b: str) -> dict[str, dict]:
    """Per clinician in the window: clinic days, Mohs cases, and the share of allowed in pathology and radiation."""
    out: dict[str, dict] = defaultdict(lambda: {"days": 0, "mohs": 0.0, "allowed": 0, "ancillary": 0})
    for pid, n in conn.execute("SELECT provider_id, COUNT(*) FROM provider_days WHERE service_date BETWEEN ? AND ? AND patients >= ? GROUP BY 1",
                               (d0, d1, CLINIC_DAY_MIN)):
        out[pid]["days"] = n
    for pid, code, fam, units, items, al in conn.execute(
            "SELECT provider_id, code, family, SUM(units), SUM(items), SUM(allowed_cents) FROM provider_services WHERE month BETWEEN ? AND ? GROUP BY 1, 2, 3",
            (a, b)):
        o = out[pid]
        o["allowed"] += al or 0
        if fam in ("pathology", "radiation"):
            o["ancillary"] += al or 0
        if activity.clean_code(code) in ("17311", "17313"):
            o["mohs"] += units or items or 0
    return out


def _group(conn, provider: str, d0: str, d1: str, a: str, b: str, window_months: int) -> tuple[list[str], str, dict]:
    """The clinicians a request covers, their tier, and how to name them. 'mohs', 'general' and 'apps' are the practice's
    average Mohs surgeon, general dermatologist or APP: only clinicians of that role who held clinic on a fair number of
    days in the window and whose work is not mostly pathology or radiation, their clinic days pooled."""
    roster = {r["provider_id"]: dict(r) for r in conn.execute("SELECT * FROM provider_roster WHERE kind='person'")}
    if provider == "physicians":
        provider = "mohs"
    if provider in ROLES:
        tier, label = ROLES[provider]
        prof = _profiles(conn, d0, d1, a, b)
        need = full_time_days(window_months)
        ids = []
        for pid, r in roster.items():
            p = prof.get(pid)
            if r.get("tier") != tier or not p or p["days"] < need:
                continue
            if p["allowed"] and p["ancillary"] / p["allowed"] > ANCILLARY_SHARE_MAX:
                continue
            mohs_day = p["mohs"] / p["days"] if p["days"] else 0
            if provider == "mohs" and mohs_day < MOHS_PER_DAY_MIN or provider == "general" and mohs_day >= MOHS_PER_DAY_MIN:
                continue
            ids.append(pid)
        names = sorted(roster[i].get("name") or i for i in ids)
        return ids, tier, {"id": provider, "name": f"{label} (average)", "tier": tier, "role": label, "clinicians": len(ids), "members": names}
    r = roster.get(provider)
    if not r:
        return [], "physician", {}
    tier = r.get("tier") if r.get("tier") in ("physician", "nonphysician") else "physician"
    return [provider], tier, {"id": provider, "name": r.get("name"), "tier": tier, "role": r.get("role"), "clinicians": 1}


def full_time_days(window_months: int) -> int:
    """Clinic days in the window for a clinician to count as working there regularly: 60 a year, pro rata."""
    return max(20, round(FULL_TIME_DAYS * window_months / 12))


def _run_window(conn, month_from: str | None, month_to: str | None, months: int) -> tuple[dict, str, str]:
    """The months of service the calculator reads: what was asked for, or the last `months` complete months (the run-rate
    now; a longer window reaches back past changes such as January 2026, when skin-substitute payment collapsed)."""
    if month_from or month_to:
        cov, a, b = activity._window(conn, month_from, month_to)
    else:
        cov = activity.coverage(conn)
        b = cov.get("default_to") or cov.get("last_month")
        if b:
            a = _month_plus(b, -(max(1, months) - 1))
            if cov.get("first_month") and a < cov["first_month"]:
                a = cov["first_month"]
            return cov, a, b
        a, b = "0000-01", "9999-12"
    if a == "0000-01" or b == "9999-12":
        # no remittance months to go by (the build has no payment dates yet): the months the clinic days cover
        r = conn.execute("SELECT MIN(service_date), MAX(service_date) FROM provider_days WHERE patients > 0").fetchone()
        if r and r[0] and r[1]:
            if b == "9999-12":
                b = r[1][:7]
            if a == "0000-01":
                a = max(r[0][:7], _month_plus(b, -(max(1, months) - 1)))
            a, b = min(a, b), max(a, b)
    return cov, a, b


def _data_from(conn, cov: dict) -> str | None:
    """The first day the archive covers: the first month whose remittances it holds from its start, else the first
    clinic day it holds at all."""
    if cov.get("first_month"):
        return f"{cov['first_month']}-01"
    r = conn.execute("SELECT MIN(service_date) FROM provider_days WHERE patients > 0").fetchone()
    return r[0][:10] if r and r[0] else None


def _in(ids: list[str]) -> str:
    return ",".join("?" * len(ids))


def _month_plus(month: str, k: int) -> str:
    y, m = int(month[:4]), int(month[5:7]) - 1 + k
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def _month_end(month: str) -> date:
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _real_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _schedule(conn, ids: list[str], d0: str, d1: str, full_days: int = 60, pace_from: str | None = None,
              data_from: str | None = None) -> dict:
    """Clinic days in the window, and the pace of the year: days a week and weeks a year over the twelve months ending
    with the window (a shorter window would miss the holiday weeks), pooled over the clinicians.

    Each clinician's weeks a year count only the weeks they were working and the archive covers. Their pace runs from the
    later of the pace window's start and the archive's first covered day (data_from), or from the week of their first
    clinic day when they held none in the PACE_GAP_DAYS before (they started inside it); and to the window's end, or to
    the week of their last clinic day when they held none in its last PACE_GAP_DAYS (they left or went on leave). So a
    new hire, or an archive shorter than a year, is not measured against weeks before there was anything to see."""
    days = [(r[0], r[1]) for r in conn.execute(
        f"SELECT provider_id, service_date FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ? AND patients >= ?",
        (*ids, d0, d1, CLINIC_DAY_MIN))]
    end = _month_end(d1[:7])
    # the later of the pace window's start and the archive's first covered day, of those that are real dates (a window
    # with nothing to bound it starts at '0000-01'): else the clinicians' first clinic day in the window
    starts = [x for x in (_real_date(pace_from or d0), _real_date(data_from)) if x]
    p0 = max(starts) if starts else min((x for x in (_real_date(d) for _, d in days) if x), default=None) or \
        min(end, date.fromisoformat(runtime.practice_today()))
    gap = timedelta(days=PACE_GAP_DAYS)
    per: dict[str, list] = defaultdict(list)
    before: set[str] = set()
    for p, d in conn.execute(
            f"SELECT provider_id, service_date FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ? AND patients >= ?",
            (*ids, (p0 - gap).isoformat(), d1, CLINIC_DAY_MIN)):
        day = date.fromisoformat(d[:10])
        if day < p0:
            before.add(p)
        elif day <= end:
            per[p].append(day)
    span_all = ((end - p0).days + 1) / 7.0
    window_weeks = activity._weeks(d0[:7], d1[:7])
    # a fair number of clinic days over the months the pace reads: the window's, pro rata
    full_days = round(full_days * span_all / window_weeks) if window_weeks else full_days

    def pace(p: str, ds: list) -> tuple[float, float, float]:
        first, last = min(ds), max(ds)
        start = p0 if p in before else max(p0, first - timedelta(days=first.weekday()))
        stop = end if (end - last) < gap else min(end, last + timedelta(days=6 - last.weekday()))
        weeks = ((stop - start).days + 1) / 7.0
        wk = len({d.isocalendar()[:2] for d in ds})
        return (len(ds) / wk if wk else 0.0), (min(52.0, wk * 52 / weeks) if weeks > 0 else 0.0), weeks

    # one clinician: their own pace. A tier: the median of the clinicians who held a fair number of clinic days, so a
    # part-timer, or someone seen only for a few weeks, does not set the average year
    full = [pace(p, ds) for p, ds in per.items() if len(ds) >= full_days] or [pace(p, ds) for p, ds in per.items()]
    dpw = statistics.median(x[0] for x in full) if full else 0.0
    wpy = statistics.median(x[1] for x in full) if full else 0.0
    # weeks the pace was read over (under a year, the holiday weeks may not show)
    pace_weeks = statistics.median(x[2] for x in full) if full else 0.0
    # what a clinic day brought in (allowed for the clinician's own services seen in clinic, before pathology, radiation and
    # products): where a target per day sits against this clinician's own days
    vals = sorted(r[0] for r in conn.execute(
        f"SELECT clinic_allowed_cents FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ? AND patients >= ?",
        (*ids, d0, d1, CLINIC_DAY_MIN)) if r[0] is not None)
    q = (lambda f: vals[min(len(vals) - 1, int(f * (len(vals) - 1) + 0.5))] if vals else None)
    # what a patient slot brings: allowed for the clinician's own clinic services per patient seen on clinic days (the
    # value a return visit gives up when it takes a slot another patient would have filled)
    al, pts = conn.execute(
        f"SELECT COALESCE(SUM(clinic_allowed_cents),0), COALESCE(SUM(patients),0) FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ? AND patients >= ?",
        (*ids, d0, d1, CLINIC_DAY_MIN)).fetchone()
    return {"clinic_days": len(days), "clinicians_with_days": len(per),
            "days_per_week": round(dpw, 2), "weeks_per_year": round(wpy, 1), "clinic_days_per_year": round(dpw * wpy, 1),
            "pace_from": p0.isoformat(), "pace_to": end.isoformat(), "pace_weeks": round(pace_weeks, 1),
            "clinic_day_allowed": {"p25": q(0.25), "p50": q(0.5), "p75": q(0.75), "p90": q(0.9), "n": len(vals)},
            "allowed_per_patient": round(al / pts) if pts else None, "patients_per_day": round(pts / len(days), 1) if days else None}


def _services_by_payer(conn, a: str, b: str) -> list | None:
    """(clinician, code, {payer group: [kind, services, units, allowed, ...]}) for the months of service a to b, as the
    activity build keeps them (provider_services.payers: each service at the payer that decided it), or None where the
    tables were built before the column was kept."""
    try:
        rows = conn.execute("SELECT provider_id, code, payers FROM provider_services WHERE month BETWEEN ? AND ?", (a, b)).fetchall()
    except sqlite3.Error:                     # tables built before the column was kept
        return None
    if not any(r[2] is not None for r in rows):
        return None
    out = []
    for pid, code, js in rows:
        try:
            v = json.loads(js) if js else {}
        except ValueError:
            v = {}
        out.append((pid, activity.clean_code(code), v if isinstance(v, dict) else {}))
    return out


def _payer_mix(conn, ids: list[str], tier: str, a: str, b: str) -> tuple[dict, dict]:
    """Share of services by payer, per service family, for the clinicians; a family with few services takes the mix of
    every clinician of the tier, then of the practice.

    The services are those the units come from: the activity build's fold of the window's months of service, each
    service once (reversals and reprocessed claims netted), at the payer that decided it (provider_services.payers). A
    copy built before the build kept them falls back on the exposure table: primary-claim lines by payment month, a+1 to
    b+2, every status 1 or 19 line (a reprocessed claim counts twice, and slow payers' services fall out of the window).
    mix_info['from'] says which ('services' or 'exposure').

    Also, per code (mix_info['code']), the clinicians' own shares for a code billed on at least MIN_CODE_MIX_LINES
    services, outside the Mohs case and its closures (the page bills a case's closures to the payer of its first stage, so
    those keep the family's mix). Visits, biopsies and destructions are not billed to the same payers in the same
    proportions, and a family's pooled mix misstates each payer's share of them. And per code and payer group, for
    _by_kind: services (code_lines), units (code_units), allowed (code_allowed) and, for a visit code, the services billed
    with modifier 25 or 57 beside a 0- or 90-day procedure (code_visit: [services, units, allowed])."""
    by_code: dict[str, Counter] = defaultdict(Counter)
    units_code: dict[str, Counter] = defaultdict(Counter)
    allowed_code: dict[str, Counter] = defaultdict(Counter)
    visit_code: dict[str, dict] = defaultdict(dict)
    tier_ids = [r[0] for r in conn.execute("SELECT provider_id FROM provider_roster WHERE kind='person' AND tier=?", (tier,))]
    kinds: dict[str, str] = {}
    folded = _services_by_payer(conn, a, b)
    own: dict[str, Counter] = defaultdict(Counter)
    peers: dict[str, Counter] = defaultdict(Counter)
    practice: dict[str, Counter] = defaultdict(Counter)
    if folded is not None:
        mine, tier_set = set(ids), set(tier_ids)
        for pid, c, by_g in folded:
            fam = activity.service_family(c)
            for g, v in by_g.items():
                if not isinstance(v, list) or len(v) < 4:
                    continue
                kinds.setdefault(g, v[0])
                n = v[1] or 0
                practice[fam][g] += n
                if pid in tier_set:
                    peers[fam][g] += n
                if pid in mine:
                    own[fam][g] += n
                    by_code[c][g] += n
                    units_code[c][g] += v[2] or 0
                    allowed_code[c][g] += v[3] or 0
                    if len(v) >= 7:
                        w = visit_code[c].setdefault(g, [0, 0.0, 0])
                        w[0] += v[4] or 0
                        w[1] += v[5] or 0
                        w[2] += v[6] or 0
        source_from = "services"
    else:
        pa, pb = _month_plus(a, 1), _month_plus(b, 2)

        def mix(where: str, args: tuple, keep: bool = False) -> dict[str, Counter]:
            out: dict[str, Counter] = defaultdict(Counter)
            for code, g, n, al in conn.execute(
                    f"SELECT code, payer_group, SUM(lines), SUM(allowed_cents) FROM exposure WHERE month BETWEEN ? AND ? {where} GROUP BY 1, 2",
                    (pa, pb, *args)):
                c = activity.clean_code(code)
                out[activity.service_family(c)][g] += n or 0
                if keep:
                    by_code[c][g] += n or 0
                    allowed_code[c][g] += al or 0
            return out

        own = mix(f"AND npi IN ({_in(ids)})", tuple(ids), keep=True) if ids else {}
        peers = mix(f"AND npi IN ({_in(tier_ids)})", tuple(tier_ids)) if tier_ids else {}
        practice = mix("", ())
        kinds = {g: k for g, k in conn.execute("SELECT DISTINCT payer_group, payer_kind FROM exposure WHERE month BETWEEN ? AND ?", (pa, pb))}
        units_code = by_code             # units shared out by lines
        visit_code = {}
        source_from = "exposure"
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
    code_mix = {c: {g: v / sum(cnt.values()) for g, v in cnt.items() if v > 0} for c, cnt in by_code.items()
                if sum(cnt.values()) >= MIN_CODE_MIX_LINES and lever_of(c) not in CASE_LEVERS}
    return out, {"source": source, "kinds": kinds, "code": code_mix, "code_lines": by_code, "code_units": units_code,
                 "code_allowed": allowed_code, "code_visit": visit_code if source_from == "services" else None, "from": source_from}


def _by_kind(units: float, allowed: int, lines: Counter, al: Counter, kinds: dict) -> dict:
    """A code's units and allowed amount shared out by kind of payer, in proportion to its units (or, from the exposure
    table, its primary-claim lines) and allowed amounts at each kind, and scaled so the kinds add up to the code's own
    totals: the kind with the most units takes the rounding. Allowed is shared by units where the payers allowed
    nothing. Empty when the code has none there. From the activity build's services the kinds are the code's own (a
    service only a second payer reported is the only part shared out); from the exposure table, an estimate."""
    ln: Counter = Counter()
    aw: Counter = Counter()
    for g, n in lines.items():
        ln[kinds.get(g) or "other"] += max(0, n or 0)
    for g, v in al.items():
        aw[kinds.get(g) or "other"] += max(0, v or 0)
    n_all = sum(ln.values())
    if n_all <= 0:
        return {}
    weight = aw if sum(aw.values()) > 0 else ln
    w_all = sum(weight.values())
    ks = sorted(k for k in set(ln) | set(aw) if ln[k] > 0 or aw[k] > 0)
    top = max(ks, key=lambda k: (ln[k], k))
    out = {k: {"units": round(units * ln[k] / n_all, 4), "allowed_cents": int(round(allowed * weight[k] / w_all))} for k in ks if k != top}
    out[top] = {"units": round(units - sum(v["units"] for v in out.values()), 4),
                "allowed_cents": int(allowed - sum(v["allowed_cents"] for v in out.values()))}
    return dict(sorted(out.items()))


def _by_kind_visit(by_kind: dict, units: Counter, allowed: Counter, visit: dict, kinds: dict) -> dict | None:
    """A visit code's by_kind split in two: 'procedure_day', the services billed with modifier 25 or 57 on the
    patient-day of a 0- or 90-day procedure (where a plan's denials of the visit beside a Mohs case sit), and 'other',
    every other service of the code. Each kind's two parts add up exactly to by_kind[kind] (units to rounding, allowed
    to the cent); each part is that kind's units and allowed shared out as the build counted them. None when the
    database cannot say (tables built before the build kept them) or the code has no split by kind."""
    if visit is None or not by_kind:
        return None
    u: Counter = Counter()
    al: Counter = Counter()
    pu: Counter = Counter()
    pal: Counter = Counter()
    for g, n in units.items():
        u[kinds.get(g) or "other"] += max(0, n or 0)
    for g, v in allowed.items():
        al[kinds.get(g) or "other"] += max(0, v or 0)
    for g, (_, n, v) in visit.items():
        pu[kinds.get(g) or "other"] += max(0, n or 0)
        pal[kinds.get(g) or "other"] += max(0, v or 0)
    out: dict = {"procedure_day": {}, "other": {}}
    for k, v in by_kind.items():
        p_units = round(v["units"] * min(1.0, pu[k] / u[k]), 4) if u[k] > 0 else 0.0
        p_allowed = int(round(v["allowed_cents"] * min(1.0, pal[k] / al[k]))) if al[k] > 0 else 0
        out["procedure_day"][k] = {"units": p_units, "allowed_cents": p_allowed}
        out["other"][k] = {"units": round(v["units"] - p_units, 4), "allowed_cents": v["allowed_cents"] - p_allowed}
    return out


def calculator(conn: sqlite3.Connection, provider: str = "mohs", month_from: str | None = None, month_to: str | None = None,
               months: int = 6) -> dict | None:
    conn.row_factory = sqlite3.Row
    cov, a, b = _run_window(conn, month_from, month_to, months)
    d0, d1 = f"{a}-01", f"{b}-31"
    window_months = (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7]) + 1
    ids, tier, who = _group(conn, provider, d0, d1, a, b, window_months)
    if not who:
        return None
    window_weeks = activity._weeks(a, b)
    pace_from = f"{_month_plus(b, -11)}-01" if window_months < 12 else None
    sched = _schedule(conn, ids, d0, d1, full_time_days(window_months), pace_from, _data_from(conn, cov)) if ids else {"clinic_days": 0}
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
    # by_kind: the same units and allowed by kind of payer, so one payer's denials can stay with that payer
    # by_kind_visit (visit codes): the same split again, the visits billed -25 or -57 beside a 0- or 90-day procedure apart
    # from every other visit, so the denials of the first stay off the visits a plan adds or corrects (None where the
    # database cannot say)
    codes = {}
    for code, s_ in svc.items():
        units_g, allowed_g = mix_info["code_units"].get(code) or Counter(), mix_info["code_allowed"].get(code) or Counter()
        codes[code] = {"family": s_["family"], "lever": lever_of(code), "units": round(s_["units"], 2), "items": s_["items"],
                       "denied": s_["denied"], "allowed_cents": s_["allowed"], "paid_cents": s_["paid"],
                       "by_kind": _by_kind(round(s_["units"], 2), s_["allowed"], units_g, allowed_g, kinds)}
        if code in EM_NEW or code in EM_EST:
            visit = mix_info["code_visit"]
            codes[code]["by_kind_visit"] = _by_kind_visit(codes[code]["by_kind"], units_g, allowed_g,
                                                          visit.get(code) or {} if visit is not None else None, kinds)
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
        m = dict.fromkeys(family_mix(code))
        m.update(dict.fromkeys(mix_info["code"].get(code) or {}))
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
    medicare_fac = {code: {y: v for y in MPFS_YEARS for v in [rates.medicare(code, y, facility=True)] if v} for code in codes}
    asc_in = _asc_cases(conn, ids, d0, d1)
    asc_codes = set(codes) | {c.split("x")[0] for rows in (asc_in["mohs"], asc_in["excision"]) for r in rows for c in r[0].split()}
    asc: dict = {y: {c: [ASC[y][c][0], ASC[y][c][2], {o: asc_amount(y, o, c) for o in WAGE_INDEX.get(y, {})}] for c in sorted(asc_codes)
                     if c in ASC.get(y, {})} for y in ASC}
    asc.update(_asc_payload(asc_codes, asc_in, sched.get("clinic_days") or 0))
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
        groups[g] = {"kind": kind, "paid_share": paid_share.get(kind), "correctable": kind in CORRECTABLE_KINDS, "negotiable": _negotiable(g, kind),
                     "share": round(mix.get("_overall", {}).get(g, 0.0), 4), "indexed": rates.indexed(g, year)}
    fallback_paid = None
    if paid_share:
        tot_a = sum(1 for _ in paid_share)
        fallback_paid = round(sum(paid_share.values()) / tot_a, 4)

    levers = _levers(svc, sched.get("clinic_days") or 0, _paired_closures(asc_in))
    extras = _strategy_inputs(conn, ids, d0, d1, window_months)
    extras.update(_visit_shares(conn, ids, d0, d1))
    path = conn.execute(
        f"SELECT COALESCE(SUM(path_generated_cents),0), COALESCE(SUM(biopsy_lesions),0) FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ?",
        (*ids, d0, d1)).fetchone() if ids else (0, 0)
    own_read, own_read_from = _own_read_share(conn, ids, d0, d1) if ids else (None, None)
    totals = {"allowed_cents": sum(s["allowed"] for s in svc.values()), "paid_cents": sum(s["paid"] for s in svc.values())}
    by_family: dict[str, list] = defaultdict(lambda: [0, 0])
    for s in svc.values():
        by_family[s["family"]][0] += s["allowed"]
        by_family[s["family"]][1] += s["paid"]
    return {
        "roles": {role: len(_group(conn, role, d0, d1, a, b, window_months)[0]) for role in ROLES},
        "who": who, "tier": tier, "from": a, "to": b, "window_weeks": round(window_weeks, 1), "rate_year": year, "years": years,
        "next_year": MPFS_YEARS[-1] if MPFS_YEARS[-1] > year else None, "states": states,
        "clinicians": [dict(r) for r in conn.execute(
            """SELECT r.provider_id, r.name, r.tier, r.role FROM provider_roster r WHERE r.kind='person' AND
               (SELECT COUNT(*) FROM provider_days d WHERE d.provider_id = r.provider_id AND d.service_date BETWEEN ? AND ? AND d.patients >= ?) >= ?
               ORDER BY r.tier, r.name""", (d0, d1, CLINIC_DAY_MIN, MIN_CLINIC_DAYS))],
        "coverage": {"last_complete_month": cov.get("last_complete_month"), "last_payment": cov.get("last_payment")},
        "schedule": sched, "levers": levers, "codes": codes,
        "mix": {f: [[g, round(v, 5)] for g, v in sorted(m.items(), key=lambda kv: -kv[1])] for f, m in mix.items()},
        # a code's own payer mix where it has enough lines (the page prefers it to its family's)
        "mix_code": {c: [[g, round(v, 5)] for g, v in sorted(m.items(), key=lambda kv: -kv[1])] for c, m in mix_info["code"].items() if c in codes},
        "mix_source": mix_info["source"],
        # where the payer mix and by_kind come from: 'services' (the activity build's services, by month of service) or
        # 'exposure' (primary-claim lines by payment month, in a copy built before the build kept them)
        "mix_from": mix_info["from"], "rates": rate_card, "medicare": medicare, "medicare_fac": medicare_fac, "asc": asc,
        "rate_sources": dict(sources),
        "payers": groups, "paid_share": paid_share, "paid_share_fallback": fallback_paid,
        "paid_share_class": paid_share_class, "paid_share_all": round(tot_pd / tot_al, 4) if tot_al else None,
        # own_read_cents: the part of generated_cents the biopsy-taker read and billed under their own number, already in
        # their 'path' line (None in a copy made before the activity build kept it)
        "pathology": {"generated_cents": path[0], "biopsy_lesions": path[1],
                      "per_lesion_cents": round(path[0] / path[1]) if path[1] else None,
                      "own_read_share": own_read, "own_read_cents": round(path[0] * own_read) if own_read is not None else None,
                      "own_read_from": own_read_from},
        "actual": {**totals, "families": {f: {"allowed_cents": v[0], "paid_cents": v[1]} for f, v in by_family.items()}},
        "constants": {"ak_lesions_15_plus": AK_LESIONS_15_PLUS, "correctable_kinds": list(CORRECTABLE_KINDS), "k_range": list(K_RANGE),
                      "min_k_units": MIN_K_UNITS, "mppr_share": MPPR_SHARE, "app_medicare_share": APP_MEDICARE_SHARE},
        "add_on": sorted(c for c in codes if _add_on(c)),
        "work_rvu": {y: {c: WORK_RVU[y][c] for c in codes if c in WORK_RVU.get(y, {})} for y in WORK_RVU},
        "strategy": extras,
        "mppr": sorted(c for c in codes if (PFS_INDICATORS.get(c) or ("", ""))[1] == "2"),
        "global_days": {c: (PFS_INDICATORS.get(c) or ("", ""))[0] for c in codes if PFS_INDICATORS.get(c)},
    }


def _negotiable(g: str, kind: str) -> str:
    """Can the practice negotiate this payer's rates: 'yes' (commercial and Medicare Advantage contracts), 'rarely' (a
    Medicaid managed-care plan: its contract can go above the state's schedule, though that seldom succeeds) or 'no'
    (Medicare, the state's own Medicaid program, workers' comp, auto and unknown payers)."""
    if kind in CORRECTABLE_KINDS:
        return "yes"
    if kind == "medicaid" and not g.startswith("Medicaid "):
        return "rarely"
    return "no"


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


def _strategy_inputs(conn, ids: list[str], d0: str, d1: str, months: int = 0) -> dict:
    """What the strategy lab needs beyond the schedule: money still recoverable on open findings this clinician
    rendered, where the clinic days were (by office, from the EMA charge exports), and the fees payment vendors took.

    A role average plans one average clinician's year, so its open findings are the members' total divided by the
    number of members (the total is kept apart). Offices are the pooled clinic days, like schedule.clinic_days. Vendor
    fees are those taken from payments dated within the window's months, so a year of them is the sum times 12 over
    vendor_fees_months."""
    out: dict = {"open_findings": {}, "open_findings_total": {}, "open_findings_clinicians": len(ids), "offices": {},
                 "vendor_fees": {}, "vendor_fees_from": d0[:7], "vendor_fees_to": d1[:7], "vendor_fees_months": months}
    if not ids:
        return out
    try:
        today = runtime.practice_today()       # still recoverable until midnight where the practice is, as the lists count
        for cat, n, cents in conn.execute(
                f"""SELECT category, COUNT(*), COALESCE(SUM(shortfall_cents), 0) FROM findings
                    WHERE rendering_provider_npi IN ({_in(ids)}) AND status IN ('open', 'in_progress') AND COALESCE(stale, 0) = 0
                      AND category IN ('denial', 'bundling', 'downcoding', 'underpayment') AND (appeal_deadline IS NULL OR appeal_deadline >= ?)
                    GROUP BY 1""", (*ids, today)):
            out["open_findings_total"][cat] = {"n": n, "cents": cents}
            out["open_findings"][cat] = {"n": round(n / len(ids), 1) if len(ids) > 1 else n, "cents": round(cents / len(ids))}
    except sqlite3.Error:
        pass
    try:
        for loc, days, pts in conn.execute(
                f"""SELECT location, COUNT(*), SUM(patients) FROM provider_ema_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ?
                    AND location IS NOT NULL GROUP BY 1""", (*ids, d0, d1)):
            out["offices"][loc] = {"days": days, "patients": pts or 0}
    except sqlite3.Error:
        pass
    try:
        # one finding per payer and month of payment, dated with the month's first payment
        for g, cents in conn.execute(
                """SELECT payer_group, COALESCE(SUM(shortfall_cents), 0) FROM findings WHERE signal = 'FEE_SKIM' AND COALESCE(stale, 0) = 0
                   AND substr(payment_date, 1, 10) BETWEEN ? AND ? GROUP BY 1""", (d0, d1)):
            out["vendor_fees"][g or "unknown"] = cents
    except sqlite3.Error:
        pass
    return out


VISIT_SHARE_MIN_DAYS = 20   # Medicare patient-days of a kind a clinician needs for their own share of visits billed beside it
LINES_COMPLETE = 0.8        # remittance lines held for the window against the services folded from them: below this the
                            # database holds only some claims' lines (the dashboard's copy keeps those with findings)


def _lines_complete(conn, d0: str, d1: str) -> bool:
    """Does the database hold every remittance line of the window (the full store), or only some claims' (the
    dashboard's copy)?"""
    held = conn.execute("SELECT COUNT(*) FROM lines WHERE service_date BETWEEN ? AND ?", (d0, d1)).fetchone()[0]
    folded = conn.execute("SELECT COALESCE(SUM(items), 0) FROM provider_services WHERE month BETWEEN ? AND ?", (d0[:7], d1[:7])).fetchone()[0]
    return bool(folded) and held >= LINES_COMPLETE * folded


def _own_read_share(conn, ids: list[str], d0: str, d1: str, route: str | None = None) -> tuple[float | None, str | None]:
    """Of the pathology read for the clinicians' own biopsies (the specimen taken from a patient on a day, the slide
    billed for the same patient and day), the share that the biopsy-taker read and billed under their own NPI. That part
    is in their own 'path' line already, so it must not be counted again as pathology their biopsies generate. Returns
    (share, where it was read). Pooled over the clinicians of a role.

    Where the database holds the window's remittance lines (the full store) it is read from them, pairing as
    remitwatch.activity does (the patient's name on the claim, kept in memory only). Elsewhere (the dashboard's copy)
    from provider_days.path_generated_own_cents, which the activity build keeps beside path_generated_cents. `route`
    ('lines' or 'provider_days') forces one, for comparing them."""
    try:
        lines = _lines_complete(conn, d0, d1) if route is None else route == "lines"
    except sqlite3.Error:
        lines = False
    if not lines:
        try:
            gen, own = conn.execute(
                f"""SELECT SUM(path_generated_cents), SUM(path_generated_own_cents) FROM provider_days
                    WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ?""", (*ids, d0, d1)).fetchone()
        except sqlite3.Error:               # a copy made before the build kept the column
            return None, None
        return (round((own or 0) / gen, 4), "provider_days") if gen and own is not None else (None, None)
    try:
        rows = conn.execute(
            f"""SELECT COALESCE(NULLIF(l.rendering_provider_npi, ''), cl.rendering_provider_npi), substr(l.patient_control_number, 1, 9),
                       cl.patient_last, cl.patient_first, l.service_date, COALESCE(NULLIF(l.original_procedure_code, ''), l.procedure_code),
                       l.allowed_cents, l.payment_date, cl.claim_key
                FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
                WHERE t.superseded = 0 AND cl.claim_status_code IN ('1', '19') AND l.service_date BETWEEN ? AND ?
                  AND (substr(l.procedure_code, 1, 1) = '8' OR substr(l.procedure_code, 1, 5) IN ({_in(activity.BIOPSY)}))""",
            (d0, d1, *activity.BIOPSY)).fetchall()
    except sqlite3.Error:
        return None, None
    mine = set(ids)
    took: dict[tuple, str] = {}
    reads: dict[tuple, list] = {}                     # (bill, date, code, claim) -> [payment date, allowed, reader, patient]
    for npi, bill, last, first, day, raw, allowed, paid_on, claim in rows:
        code = activity.clean_code(raw)
        fam = activity.service_family(code)
        patient = (re.sub(r"[^A-Z0-9]", "", last.upper()), re.sub(r"[^A-Z0-9]", "", (first or "").upper())) if (last or "").strip() else ("#", bill)
        if fam == "biopsy":
            if npi in mine:
                took.setdefault((patient, day), npi)
        elif fam == "pathology":
            r = reads.setdefault((bill, day, code, claim), [paid_on or "", 0, npi, patient])
            r[1] += allowed or 0
    latest: dict[tuple, list] = {}                    # one decision per bill, date and code: the latest primary claim's
    for (bill, day, code, _), r in reads.items():
        k = (bill, day, code)
        if k not in latest or r[0] > latest[k][0]:
            latest[k] = r
    own = every = 0
    for (_, day, _), (_, allowed, reader, patient) in latest.items():
        who = took.get((patient, day))
        if who:
            every += allowed
            own += allowed if reader == who else 0
    return (round(own / every, 4), "lines") if every > 0 else (None, None)


def _visit_counts_lines(conn, ids: list[str], d0: str, d1: str) -> dict[str, Counter]:
    """Per clinician: Medicare fee-for-service patient-days with a Mohs first stage (mohs) and of those with an E/M billed
    with modifier 57 (m57) or 25 (m25); with a first biopsy (bx) and of those with an E/M-25 (b25), and of those, the days
    that also carry 17000 or 17004 (b25ak) and the allowed cents of 17110, shaves (11300-11313) and 11900/11901 billed
    those days (b25other: each bill, code and day once, at the newest Medicare decision). From the remittance lines: a
    line's rendering NPI, else its claim's; the patient-day is the patient's name on the claim (kept in memory only; the
    bill, the first nine characters of the patient control number, where there is no name) and the date, so a visit
    billed apart from the procedure the same day still counts; the modifiers as billed where the payer reported a
    change."""
    extra = tuple(activity.AK_FIRST) + tuple(sorted(activity.BX_DAY_OTHER))
    rows = conn.execute(
        f"""SELECT COALESCE(NULLIF(l.rendering_provider_npi, ''), cl.rendering_provider_npi) npi, substr(l.patient_control_number, 1, 9),
                   l.service_date, COALESCE(NULLIF(l.original_procedure_code, ''), l.procedure_code),
                   COALESCE(NULLIF(NULLIF(l.original_modifiers, ''), '[]'), l.modifiers), cl.payer_name, cl.claim_filing_indicator,
                   cl.patient_last, cl.patient_first, l.allowed_cents, l.paid_cents, l.payment_date, cl.claim_key
            FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key LEFT JOIN transactions t ON t.transaction_key = l.transaction_key
            WHERE l.service_date BETWEEN ? AND ? AND cl.claim_status_code IN ('1', '19') AND COALESCE(t.superseded, 0) = 0
              AND (substr(l.procedure_code, 1, 3) IN ('992', '173') OR substr(l.original_procedure_code, 1, 3) IN ('992', '173')
                   OR substr(l.procedure_code, 1, 5) IN ({_in(BIOPSY_FIRST)}) OR substr(l.original_procedure_code, 1, 5) IN ({_in(BIOPSY_FIRST)})
                   OR substr(l.procedure_code, 1, 5) IN ({_in(extra)}) OR substr(l.original_procedure_code, 1, 5) IN ({_in(extra)}))
              AND COALESCE(NULLIF(l.rendering_provider_npi, ''), cl.rendering_provider_npi) IN ({_in(ids)})""",
        (d0, d1, *BIOPSY_FIRST, *BIOPSY_FIRST, *extra, *extra, *ids)).fetchall()
    em = set(EM_NEW) | set(EM_EST)
    kinds: dict[tuple, str] = {}
    day: dict[tuple, list] = defaultdict(lambda: [False, False, False, False, False])      # Mohs, biopsy, E/M-57, E/M-25, 17000/17004
    other: dict[tuple, dict] = defaultdict(dict)        # patient-day -> (bill, code) -> claim -> [paid on, allowed]
    for npi, bill, sd, raw, mods, payer, cfi, last, first, allowed, paid, paid_on, claim in rows:
        if not sd:
            continue
        k = (payer, cfi)
        if k not in kinds:
            kinds[k] = payer_group_kind(payer, cfi)[1]
        if kinds[k] != "medicare":
            continue
        code = activity.clean_code(raw)
        patient = (re.sub(r"[^A-Z0-9]", "", last.upper()), re.sub(r"[^A-Z0-9]", "", (first or "").upper())) if (last or "").strip() else ("#", bill)
        key = (npi, patient, sd)
        if code in activity.BX_DAY_OTHER:
            c = other[key].setdefault((bill, code), {}).setdefault(claim, [paid_on or "", 0])
            c[1] += (allowed or 0) if (allowed or 0) > 0 else max(paid or 0, 0)
            continue
        if code not in em and code not in ("17311", "17313") and code not in BIOPSY_FIRST and code not in activity.AK_FIRST:
            continue
        m = set(re.findall(r"[0-9A-Z]{2}", (mods or "") + " " + " ".join((raw or "").upper().split()[1:])))
        f = day[key]
        f[0] = f[0] or code in ("17311", "17313")
        f[1] = f[1] or code in BIOPSY_FIRST
        f[4] = f[4] or code in activity.AK_FIRST
        if code in em:
            f[2] = f[2] or "57" in m
            f[3] = f[3] or "25" in m
    per: dict[str, Counter] = defaultdict(Counter)
    for key, (mohs, bx, e57, e25, ak) in day.items():
        c = per[key[0]]
        c["ak_measured"] = 1
        if mohs:
            c["mohs"] += 1
            c["m57"] += e57
            c["m25"] += e25
        if bx:
            c["bx"] += 1
            c["b25"] += e25
            if e25:
                c["b25ak"] += ak
                # each bill and code once, at its newest Medicare decision (a reprocessed claim replaces the first)
                c["b25other"] += sum(max(by_claim.values())[1] for by_claim in (other.get(key) or {}).values())
    return per


def _visit_counts_stored(conn, ids: list[str], d0: str, d1: str) -> dict[str, Counter]:
    """The same counts as _visit_counts_lines, as the activity build keeps them in provider_days (its own attribution of
    each encounter to a clinician and its own patient-days). b25ak and b25other only where the build kept them (version
    6 on): ak_measured says so."""
    per: dict[str, Counter] = {}
    try:
        rows = conn.execute(
            f"""SELECT provider_id, SUM(mcare_mohs_patients), SUM(mcare_mohs_e57), SUM(mcare_mohs_e25), SUM(mcare_bx_patients), SUM(mcare_bx_e25),
                       SUM(mcare_bx25_ak), SUM(mcare_bx25_other_cents), COUNT(mcare_bx25_ak)
                FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ? GROUP BY 1""", (*ids, d0, d1)).fetchall()
    except sqlite3.Error:                     # tables built before the build kept the biopsy days' destructions
        rows = [tuple(r) + (None, None, 0) for r in conn.execute(
            f"""SELECT provider_id, SUM(mcare_mohs_patients), SUM(mcare_mohs_e57), SUM(mcare_mohs_e25), SUM(mcare_bx_patients), SUM(mcare_bx_e25)
                FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ? GROUP BY 1""", (*ids, d0, d1))]
    for pid, mohs, m57, m25, bx, b25, ak, oth, kept in rows:
        per[pid] = Counter({"mohs": mohs or 0, "m57": m57 or 0, "m25": m25 or 0, "bx": bx or 0, "b25": b25 or 0,
                            "b25ak": ak or 0, "b25other": oth or 0, "ak_measured": 1 if kept else 0})
    return per


def _visit_shares(conn, ids: list[str], d0: str, d1: str, route: str | None = None) -> dict:
    """On Medicare fee-for-service patient-days in the window: the share of Mohs patient-days (a first stage billed) on
    which the clinician also billed an E/M with modifier 57 (mohs_visit57_share) or 25 (mohs_visit25_share), and the
    share of biopsy patient-days (a first biopsy code) with an E/M with modifier 25 (bx_visit_share). Of those biopsy
    days with an E/M-25, the share that also carry a destruction of premalignant lesions, 17000 or 17004 (bx_ak_share),
    and the mean allowed cents a day, as allowed in the window, of 17110, shaves (11300-11313) and 11900/11901 billed on
    them (bx_day_other_cents). A role average gives the mean of its members' own figures (members with
    VISIT_SHARE_MIN_DAYS such days).

    Read from the remittance lines where the database holds the window's (the full store), else from the counts the
    activity build keeps in provider_days (the dashboard's copy); visit_shares_from says which. `route` ('lines' or
    'provider_days') forces one, for comparing them. Each figure is None where neither can say (bx_ak_share and
    bx_day_other_cents in a copy built before the build kept them), and the page keeps its default. Counts only: nothing
    about a patient leaves here."""
    keys = ("mohs_visit57_share", "mohs_visit25_share", "bx_visit_share", "bx_ak_share", "bx_day_other_cents")
    out: dict = {k: None for k in keys}
    out.update({"visit_shares_days": {"mohs": 0, "biopsy": 0}, "visit_shares_payer": None, "visit_shares_from": None})
    if not ids:
        return out
    try:
        lines = _lines_complete(conn, d0, d1) if route is None else route == "lines"
    except sqlite3.Error:                    # no remittance lines at all
        lines = False
    try:
        per = _visit_counts_lines(conn, ids, d0, d1) if lines else _visit_counts_stored(conn, ids, d0, d1)
    except sqlite3.Error:                    # a copy made before the build kept the counts
        return out
    out.update({"visit_shares_payer": "medicare", "visit_shares_from": "lines" if lines else "provider_days"})
    out["visit_shares_days"] = {"mohs": sum(c["mohs"] for c in per.values()), "biopsy": sum(c["bx"] for c in per.values())}
    for key, num, den in (("mohs_visit57_share", "m57", "mohs"), ("mohs_visit25_share", "m25", "mohs"), ("bx_visit_share", "b25", "bx")):
        own = [c[num] / c[den] for c in per.values() if c[den] >= VISIT_SHARE_MIN_DAYS]
        if own:
            out[key] = round(sum(own) / len(own), 4)
    kept = [c for c in per.values() if c["ak_measured"] and c["b25"] >= VISIT_SHARE_MIN_DAYS]
    if kept:
        out["bx_ak_share"] = round(sum(c["b25ak"] / c["b25"] for c in kept) / len(kept), 4)
        out["bx_day_other_cents"] = round(sum(c["b25other"] / c["b25"] for c in kept) / len(kept))
    return out


def _asc_items_lines(conn, ids: list[str], d0: str, d1: str) -> dict[tuple, list]:
    """The lines asc_case reads, per clinician, patient and day, from the remittance lines, decided as the activity build
    decides a service (remitwatch.activity._fold and _settle): for each bill, day and code, the first payer to decide it as
    primary that still has a decision allowing something, and that decision (the newest still standing: a reversal takes
    back the decision of its own payer it matches), with its units and its payer's kind and state; the place of service
    of any claim of the line that states one, an ambulatory surgical center's (24) wherever one says so. A Mohs line
    nobody paid comes as '1731x' (the day is still a Mohs day); another code nobody paid is left out. The patient is the
    name on the claim (kept in memory only; the bill where there is no name), so a closure billed on an ASC claim joins
    the Mohs billed on the clinic's claim the same day, as in remitwatch.activity."""
    rows = conn.execute(
        f"""SELECT COALESCE(NULLIF(l.rendering_provider_npi, ''), cl.rendering_provider_npi), substr(l.patient_control_number, 1, 9),
                   l.service_date, COALESCE(NULLIF(l.original_procedure_code, ''), l.procedure_code),
                   COALESCE(NULLIF(l.original_units, 0), l.paid_units), l.allowed_cents, l.paid_cents, cl.facility_type_code,
                   l.payer_name, cl.claim_filing_indicator, cl.patient_last, cl.patient_first, l.payment_date, cl.claim_key,
                   l.claim_status_code, NULLIF(l.payer_id, '')
            FROM lines l JOIN claims cl ON cl.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = l.transaction_key
            WHERE t.superseded = 0 AND l.service_date BETWEEN ? AND ?
              AND (substr(COALESCE(NULLIF(l.original_procedure_code, ''), l.procedure_code), 1, 2) IN ('11', '12', '13', '14', '15', '17'))
              AND COALESCE(NULLIF(l.rendering_provider_npi, ''), cl.rendering_provider_npi) IN ({_in(ids)})
            ORDER BY l.payment_date, CASE WHEN l.claim_status_code = '22' THEN 0 ELSE 1 END, l.transaction_key, cl.claim_seq, l.line_seq""",
        (d0, d1, *ids)).fetchall()
    kinds: dict[tuple, tuple] = {}
    # (npi, bill, day, code) -> claim -> [status, units, allowed, paid, place of service, payer, (group, kind), patient],
    # claims in the order their lines arrive (payment date, a reversal before anything else paid that day)
    claims: dict[tuple, dict] = defaultdict(dict)
    for npi, bill, day, raw, units, allowed, paid, pos, payer, cfi, last, first, _paid_on, claim, status, payer_id in rows:
        code = activity.clean_code(raw)
        if not day or not (activity.asc_code(code) or code[:4] == "1731"):
            continue
        k = (payer, cfi)
        if k not in kinds:
            kinds[k] = payer_group_kind(payer, cfi)
        patient = (re.sub(r"[^A-Z0-9]", "", last.upper()), re.sub(r"[^A-Z0-9]", "", (first or "").upper())) if (last or "").strip() else ("#", bill)
        c = claims[(npi, bill, day[:10], code)].setdefault(claim, [status, 0.0, 0, 0, pos, payer_id or kinds[k][0], kinds[k], patient])
        c[1] += abs(units or 1)
        c[2] += allowed or 0
        c[3] += paid or 0
    out: dict[tuple, list] = defaultdict(list)
    for (npi, _, day, code), by_claim in claims.items():
        decisions: dict = {}                 # payer -> [(group, kind), [[allowed, paid, units] per decision still standing]]
        pos_any = None
        patient = None
        for status, units, allowed, paid, pos, who, gk, pt in by_claim.values():
            if patient is None or patient[0] == "#":
                patient = pt                 # the name on any claim of the bill, as the build takes it
            if status == "22":
                slot = decisions.get(who)
                if slot and slot[1]:
                    activity._take_back(slot[1], abs(allowed), abs(paid))
                continue
            if pos and (pos_any is None or pos == activity.ASC_POS):
                pos_any = pos
            if status in activity.PRIMARY or status == "4":
                slot = decisions.setdefault(who, [gk, []])
                if status in activity.PRIMARY:
                    slot[1].append([allowed if allowed > 0 else max(paid, 0), paid, units])
        pick = None
        for gk, open_ in decisions.values():
            dec = next((x for x in reversed(open_) if x[0] > 0), None)
            if dec:
                pick = (gk, dec)
                break
        if pick is None and code[:4] != "1731":
            continue                         # nobody paid it: not part of a case
        (group, kind), units = (pick[0], pick[1][2]) if pick else (next(iter(decisions.values()))[0] if decisions else (None, None), 0.0)
        m = activity._STATE.match(group or "")
        paid_line = pick is not None and activity.asc_code(code)
        out[(npi, patient, day)].append((code if paid_line else "1731x", units or 1.0, pos_any == activity.ASC_POS, kind, m.group(1) if m else None))
    return out


def _asc_cases(conn, ids: list[str], d0: str, d1: str, route: str | None = None) -> dict:
    """The clinicians' Mohs cases with a closure the same day, and excisions with a repair (no Mohs that day), over the
    window, by the codes billed, where the closure was billed (clinic or ASC), the kind of payer and the state the case
    was seen in (Medicare's contractor; for other payers, the clinic day's state). Pooled over the clinicians of a role.

        mohs      [[codes, site, kind of payer, state, cases, extra stages]]
        excision  [[codes, site, kind of payer, state, cases, 1 for a physician's cases, 0 for an APP's]]

    Read from the remittance lines where the database holds the window's (the full store), else from what the activity
    build keeps in provider_payers.asc_cases (the dashboard's copy); 'from' says which, or is None where neither can say
    (a copy built before the column was kept). `route` ('lines' or 'provider_payers') forces one, for comparing them.
    Codes and counts only: nothing about a patient leaves here."""
    out: dict = {"from": None, "mohs": [], "excision": []}
    if not ids:
        return out
    try:
        lines = _lines_complete(conn, d0, d1) if route is None else route == "lines"
    except sqlite3.Error:
        lines = False
    tiers = {r[0]: r[1] for r in conn.execute(f"SELECT provider_id, tier FROM provider_roster WHERE provider_id IN ({_in(ids)})", ids)}
    agg: Counter = Counter()
    extra: Counter = Counter()
    try:
        if lines:
            place = {(p, d[:10]): pl for p, d, pl in conn.execute(
                f"SELECT provider_id, service_date, place FROM provider_days WHERE provider_id IN ({_in(ids)}) AND service_date BETWEEN ? AND ?",
                (*ids, d0, d1))}
            for (npi, _, day), items in _asc_items_lines(conn, ids, d0, d1).items():
                case = activity.asc_case(items)
                if case:
                    what, kind, state, site, codes, stages = case
                    k = (what, npi, kind, state or place.get((npi, day)) or "", site, codes)
                    agg[k] += 1
                    extra[k] += stages
        else:
            for pid, kind, js in conn.execute(
                    f"""SELECT provider_id, payer_kind, asc_cases FROM provider_payers WHERE provider_id IN ({_in(ids)}) AND month BETWEEN ? AND ?
                        AND asc_cases IS NOT NULL""", (*ids, d0[:7], d1[:7])):
                v = json.loads(js)
                for state, site, codes, n, stages in v.get("mohs", []):
                    agg[("mohs", pid, kind, state, site, codes)] += n
                    extra[("mohs", pid, kind, state, site, codes)] += stages
                for state, site, codes, n in v.get("excision", []):
                    agg[("excision", pid, kind, state, site, codes)] += n
    except (sqlite3.Error, ValueError):       # a copy made before the build kept the cases
        return out
    pooled: Counter = Counter()
    stages_of: Counter = Counter()
    for (what, pid, kind, state, site, codes), n in agg.items():
        # only a physician operates in an ASC (42 CFR 416.42); a clinician the roster cannot place counts as a physician, as
        # the rest of the calculator takes them
        tail = round(extra[(what, pid, kind, state, site, codes)], 2) if what == "mohs" else int(tiers.get(pid) != "nonphysician")
        k = (what, codes, site, kind, state) + (() if what == "mohs" else (tail,))
        pooled[k] += n
        if what == "mohs":
            stages_of[k] += tail
    for k, n in sorted(pooled.items()):
        what, rest = k[0], list(k[1:])
        out[what].append(rest + [n, round(stages_of[k], 2)] if what == "mohs" else rest[:4] + [n, rest[4]])
    out["from"] = "lines" if lines else "provider_payers"
    return out


def _asc_payload(codes: set, cases: dict, clinic_days: int) -> dict:
    """What the strategy lab's ASC comparison reads besides the ASC's rates by year (asc[year]): where the ASC is, Medicare's
    Indiana amounts (office and facility, physician) of every code it may price, and its Kentucky amounts (medicare_ky,
    for a Kentucky clinic's case whose closure is sent to the ASC), which of them the multiple-procedure rule ranks and
    which are add-ons, the factor for an ASC failing quality reporting, and the cases (_asc_cases) with the clinic days
    they were counted over: a Kentucky clinic's same-day closures are the mohs rows with state 'KY' and site 'clinic'."""
    st = ASC_SITE["state"]
    return {
        "site": dict(ASC_SITE),
        "medicare": {c: {y: list(MPFS[y][st][c]) for y in MPFS_YEARS if MPFS.get(y, {}).get(st, {}).get(c)} for c in sorted(codes)},
        # Medicare's Kentucky amounts, [office, facility] by code and year, for Kentucky clinic cases (the Mohs stays in the
        # Kentucky clinic when the closure is sent to the ASC)
        "medicare_ky": {c: {y: list(MPFS[y]["KY"][c]) for y in MPFS_YEARS if MPFS.get(y, {}).get("KY", {}).get(c)} for c in sorted(codes)},
        "mppr": sorted(c for c in codes if (PFS_INDICATORS.get(c) or ("", ""))[1] == "2"),
        "add_on": sorted(c for c in codes if _add_on(c)),
        "ascqr": dict(ASCQR_FACTOR),
        "cases": dict(cases, clinic_days=clinic_days),
    }


# --------------------------------------------------------------------------- the ASC view: what the coming year does
ASC_MOHS_CODES = ("17311", "17312", "17313", "17314", "17315")
ASC_RULE_MIN_LINES = 20     # fewer lines than this on the cases' own codes: a kind of payer's share comes from the codes
                            # of the same service families


def _kind_ratios(conn, codes: set) -> dict:
    """What each kind of payer pays the practice's physicians for these codes as a share of what Medicare pays it for
    them: each payer's usual allowed amount per code and setting (payer_rates; office and facility apart, each against
    Medicare's own usual amount for that code, year and setting), weighted by the lines behind it, in the newest year
    with a fair number of rates. Codes of the same service families stand in where these codes have too few lines."""
    try:
        years = [r[0] for r in conn.execute("SELECT year FROM payer_rates GROUP BY 1 HAVING COUNT(*) >= 100 ORDER BY 1")] \
            or [r[0] for r in conn.execute("SELECT DISTINCT year FROM payer_rates ORDER BY 1")]
    except sqlite3.Error:
        return {}
    if not years:
        return {}
    year = years[-1]
    rows = [tuple(r) for r in conn.execute(
        """SELECT payer_kind, code, mode_cents, medicare_cents, n FROM payer_rates
           WHERE year = ? AND tier = 'physician' AND mode_cents > 0 AND medicare_cents > 0 AND n > 0""", (year,))]
    fams = {activity.service_family(c) for c in codes}
    out: dict = {}
    for kind in sorted({r[0] for r in rows if r[0]}):
        def tally(keep) -> tuple[int, int, int]:
            num = den = n = 0
            for k, code, mode, med, cnt in rows:
                if k == kind and keep(activity.clean_code(code)):
                    num += cnt * mode
                    den += cnt * med
                    n += cnt
            return num, den, n
        num, den, n = tally(lambda c: c in codes)
        basis = "codes"
        if n < ASC_RULE_MIN_LINES:
            num, den, n = tally(lambda c: activity.service_family(c) in fams)
            basis = "families"
        if n and den:
            out[kind] = {"ratio": round(num / den, 4), "lines": n, "year": year, "basis": basis}
    return out


def asc_rule(conn: sqlite3.Connection, months: int = 12, month_from: str | None = None, month_to: str | None = None) -> dict:
    """The practice's cases at its ASC, for the ASC view (static/asc.js), which prices each case in the year before and in
    the rule year with the strategy lab's own arithmetic (LabModel.ascCase) to show what the rule year's Medicare rates
    do to what is paid for them: the ASC's facility fee (the OPPS/ASC final rule) and, beside it, the surgeons' fees
    (the physician fee schedule).

        cases     every case of a Mohs first stage with its closure the same day, and of an excision with its repair,
                  over the window, all clinicians pooled (_asc_cases): the codes billed, where the closure was billed (site
                  'asc': on a claim with the ASC's place of service, 24), the kind of payer and the state
        asc       the ASC's rates both years, wage-adjusted for the ASC's office (remitwatch.ascfs, from CMS's Addendum
                  AA), and _asc_payload's Medicare physician amounts, ranked codes and add-ons
        kinds     what each kind of payer pays the practice for these procedures against Medicare (_kind_ratios): the
                  page's starting share of Medicare's ASC rates for a plan, and the price of a plan's professional fees
        codes     per code, both years: the payment indicator, discounting, the national rate and the ASC's

    The ASC's own remittances are not in the store (the 835s are the practice's professional claims), so what the ASC is
    paid is priced from Medicare's rates, not read. Codes, counts and rates only: nothing about a patient."""
    from . import ascrates
    cov, a, b = _run_window(conn, month_from, month_to, months)
    d0, d1 = f"{a}-01", f"{b}-31"
    window_months = (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7]) + 1
    ids = [r[0] for r in conn.execute("SELECT provider_id FROM provider_roster WHERE kind='person' ORDER BY provider_id")]
    cases = _asc_cases(conn, ids, d0, d1)
    codes = {c.split("x")[0] for rows in (cases["mohs"], cases["excision"]) for r in rows for c in r[0].split()} | set(ASC_MOHS_CODES)
    py, ry = ascrates.prior_year(), ascrates.rule_year()
    years = [y for y in (py, ry) if y in ASC]
    asc: dict = {y: {c: [ASC[y][c][0], ASC[y][c][2], {o: asc_amount(y, o, c) for o in WAGE_INDEX.get(y, {})}]
                     for c in sorted(codes) if c in ASC.get(y, {})} for y in years}
    asc.update(_asc_payload(codes, cases, 0))
    office = ASC_SITE["office"]
    where = ascrates.office_place(office) or {}
    per_code: dict = {}
    for c in sorted(codes):
        row = {}
        for y in years:
            r = ASC.get(y, {}).get(c)
            row[y] = None if r is None else {"pi": r[0], "mpd": r[2], "national": r[1] if r[0] != "N1" else None,
                                              "local": asc_amount(y, office, c)}
        per_code[c] = row
    pis = {r[y]["pi"] for r in per_code.values() for y in years if r.get(y)}
    meta = ascrates.tables().meta
    return {
        "from": a, "to": b, "months": window_months, "per_year": round(12 / window_months, 6),
        "years": years, "prior_year": py, "rule_year": ry, "labels": meta.get("years", {}), "source": meta.get("source"),
        "site": {**ASC_SITE, "county": where.get("county"), "fips": where.get("fips"), "cbsa": where.get("cbsa"),
                 "cbsa_name": where.get("cbsa_name"), "wage_index": {y: WAGE_INDEX.get(y, {}).get(office) for y in years}},
        "cases": cases, "asc": asc, "kinds": _kind_ratios(conn, codes), "codes": per_code,
        "pi": {p: t for p, t in ascrates.definitions().items() if p in pis},
        "constants": {"mppr_share": MPPR_SHARE, "coinsurance": meta.get("coinsurance", 0.2), "sequestration": meta.get("sequestration", 0.02)},
        "mppr": sorted(c for c in codes if (PFS_INDICATORS.get(c) or ("", ""))[1] == "2"),
        "add_on": sorted(c for c in codes if _add_on(c)),
        "coverage": {"last_complete_month": cov.get("last_complete_month"), "last_payment": cov.get("last_payment")},
    }


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


def _paired_closures(cases: dict) -> dict | None:
    """The closure units (flaps and grafts, layered repairs) the clinicians billed on the patient-day of one of their own
    Mohs cases ('mohs') or of an excision with no Mohs that day ('excision'), per code, from the cases _asc_cases counted
    (across the clinic's and the ASC's claims). None where the database cannot pair them (a copy built before the build
    kept the cases) or found no case at all."""
    if not cases.get("from") or not (cases.get("mohs") or cases.get("excision")):
        return None
    out = {"mohs": Counter(), "excision": Counter()}
    for what in ("mohs", "excision"):
        for row in cases.get(what) or []:
            n = row[4] or 0
            for tok in (row[0] or "").split():
                c, _, k = tok.partition("x")
                if lever_of(c) in ("flap_graft", "layered"):
                    out[what][c] += n * (float(k) if k else 1.0)
    return out


def _levers(svc: dict, clinic_days: int, paired: dict | None = None) -> dict:
    """The schedule now, per clinic day, in the page's terms, and how each lever turns into billed codes. The page turns
    the levers back into units with the same rules, so the levers as they come here give back, code for code, the units
    billed in the window.

    Closures follow the cases they were billed with. Where the database pairs them (`paired`, _paired_closures: the
    clinician's own closures on the patient-day of their Mohs cases and of their excisions), flaps and grafts per Mohs
    case, and layered repairs (intermediate and complex) per Mohs case and per excision, are those billed with them, and
    closures billed on any other day stay under 'everything else' (a flap after an excision, a repair on a day with no
    Mohs or excision of that patient). Elsewhere every flap and graft follows a Mohs case, and the layered repairs, which a
    month's totals do not tie to either, are shared out with the rates at which each kind of case is closed in layers
    across the archive (LAYERED_WEIGHT), scaled so the clinician's own total comes back exactly. layered.from and
    flap_graft.from say which ('same_day' or 'shared').

    Units a lever cannot carry stay under 'everything else' (other.codes, which a code can share with its lever): an
    add-on billed without the code it adds to (17003 past 13 for each 17000, or with no 17000; an extra Mohs stage at a
    site with no first stage; a biopsy or closure add-on with no first one), and closures past the page's limit of two
    for each Mohs case or excision."""
    u = {c: s["units"] for c, s in svc.items() if s["units"] > 0}
    cd = clinic_days or 0
    spill: Counter = Counter()            # units of a lever's code that its lever cannot give back
    unpaired: Counter = Counter()         # ...of them, closures billed on a day with no case of the clinician's to pair with

    def per_day(x: float) -> float:
        return x / cd if cd else 0.0

    def shares(codes) -> dict[str, float]:
        tot = sum(u.get(c, 0) for c in codes)
        return {c: u[c] / tot for c in codes if u.get(c)} if tot else {}

    by: dict[str, list] = defaultdict(list)
    for c in u:
        by[lever_of(c)].append(c)

    def primary_mix(codes, of: dict | None = None) -> tuple[float, dict, dict]:
        """(primary units, share of each primary code, add-on units per primary), of the units billed or of `of`."""
        of = u if of is None else of
        prim = [c for c in codes if not _add_on(c) and of.get(c)]
        n = sum(of[c] for c in prim)
        return n, ({c: of[c] / n for c in prim} if n else {}), ({c: of[c] / n for c in codes if _add_on(c) and of.get(c)} if n else {})

    def carry(codes, n: float, room: float) -> None:
        """A closure lever gives back `room` primary closures and their add-ons; the rest of these codes' units spill."""
        if n > 0 and n <= room * (1 + 1e-9):
            return
        keep = min(1.0, room / n) if n > 0 else 0.0
        for c in codes:
            spill[c] += u[c] * (1 - keep)

    hn, tr = u.get("17311", 0), u.get("17313", 0)
    cases = hn + tr
    # extra stages and blocks follow a first stage at the same site
    for c, first in (("17312", hn), ("17314", tr), ("17315", cases)):
        if u.get(c) and not first:
            spill[c] += u[c]
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
    if paired is not None:
        # each closure code's units billed with a Mohs case (pm) and with an excision (pe), never more than were billed;
        # the page carries at most two flaps or grafts, and two layered closures, per Mohs case and per excision: past that
        # they stay under 'everything else'
        pm: Counter = Counter()
        pe: Counter = Counter()
        for c in by["flap_graft"]:
            pm[c] = min(paired["mohs"].get(c, 0.0), u[c]) if cases else 0.0
        for c in by["layered"]:
            m_, e_ = (paired["mohs"].get(c, 0.0) if cases else 0.0), (paired["excision"].get(c, 0.0) if exc_n else 0.0)
            if m_ + e_ > u[c]:
                m_, e_ = m_ * u[c] / (m_ + e_), e_ * u[c] / (m_ + e_)
            pm[c], pe[c] = m_, e_
        # billed on a day with no Mohs case or excision of that patient (or a flap after an excision): the day's own
        # procedure, not a later one on a case's day
        for c in by["flap_graft"] + by["layered"]:
            if u[c] - pm[c] - pe[c] > 1e-9:
                unpaired[c] = u[c] - pm[c] - pe[c]

        def cap(side: Counter, codes, n_cases: float) -> None:
            n = sum(side[c] for c in codes if not _add_on(c))
            if n_cases and n > 2.0 * n_cases:
                for c in codes:
                    side[c] *= 2.0 * n_cases / n
        cap(pm, by["flap_graft"], cases)
        cap(pm, by["layered"], cases)
        cap(pe, by["layered"], exc_n)
        n_fg, mix_fg, add_fg = primary_mix(by["flap_graft"], pm)
        fg = n_fg / cases if cases else 0.40
        if n_fg:
            fg_mix, fg_add = mix_fg, add_fg
        both = {c: pm[c] + pe[c] for c in by["layered"]}
        n_lay, mix_lay, add_lay = primary_mix(by["layered"], both)
        lm = sum(pm[c] for c in by["layered"] if not _add_on(c)) / cases if cases else wm
        le = sum(pe[c] for c in by["layered"] if not _add_on(c)) / exc_n if exc_n else we
        if n_lay:
            lay_mix, lay_add = mix_lay, add_lay
        # what each lever gives back is what was paired (an add-on with no paired first closure is not); the rest spills
        for c in by["flap_graft"]:
            back = pm[c] if n_fg else 0.0
            if u[c] - back > 1e-9:
                spill[c] += u[c] - back
        for c in by["layered"]:
            back = both[c] if n_lay else 0.0
            if u[c] - back > 1e-9:
                spill[c] += u[c] - back
    else:
        fg = min(fg_n / cases, 2.0) if cases else 0.40
        if cases:
            carry(by["flap_graft"], fg_n, fg * cases)
        scale = lay_n / (wm * cases + we * exc_n) if (wm * cases + we * exc_n) else 0.0
        lm, le = wm * scale, we * scale
        # the page carries at most two layered closures per Mohs case and per excision: past that on one side, the rest go
        # to the other side, and past both, to 'everything else'
        if le > 2.0 and cases:
            lm, le = (lay_n - 2.0 * exc_n) / cases, 2.0
        if lm > 2.0 and exc_n:
            lm, le = 2.0, min(2.0, (lay_n - 2.0 * cases) / exc_n)
        lm, le = min(lm, 2.0), min(le, 2.0)
        if cases or exc_n:
            carry(by["layered"], lay_n, lm * cases + le * exc_n)
    new = {c: u.get(c, 0) for c in EM_NEW}
    est = {c: u.get(c, 0) for c in EM_EST}
    n_new, n_est = sum(new.values()), sum(est.values())
    visits = n_new + n_est
    if u.get("G2211") and not visits:
        spill["G2211"] += u["G2211"]
    bx_first = sum(u[c] for c in by["biopsy_first"])
    bx_add = sum(u[c] for c in by["biopsy_add"])
    if bx_add and not bx_first:
        for c in by["biopsy_add"]:
            spill[c] += u[c]
        bx_add = 0
    ak0, ak3, ak4 = u.get("17000", 0), u.get("17003", 0), u.get("17004", 0)
    # 17003 is each lesion from the second to the fourteenth: at most 13 for each 17000
    if ak3 > 13 * ak0:
        spill["17003"] += ak3 - 13 * ak0
        ak3 = 13 * ak0
    ak_pat = ak0 + ak4
    ak_n = 1 + ak3 / ak0 if ak0 else 4.0
    ak_f = ak4 / ak_pat if ak_pat else 0.07
    ben = u.get("17110", 0) + u.get("17111", 0)
    x12, x14 = u.get("17312", 0) - spill["17312"], u.get("17314", 0) - spill["17314"]
    other = {c: per_day(u[c]) for c in by["other"]}
    for c, v in spill.items():
        if v > 1e-9:
            other[c] = other.get(c, 0.0) + per_day(v)
    return {
        "mohs": {"cases": per_day(cases), "head_neck_share": hn / cases if cases else 0.62,
                 "stages": 1 + (x12 + x14) / cases if cases else 1.47,
                 # extra stages per case at each site, relative to the average: kept as the slider moves the average
                 "extra_head_neck": e_hn, "extra_trunk": e_tr, "blocks": u.get("17315", 0) / cases if cases else 0.0},
        "flap_graft": {"share": fg, "mix": fg_mix, "add_ons": fg_add, "billed_without_mohs": fg_n if not cases else 0,
                       "from": "same_day" if paired is not None else "shared"},
        "layered": {"mohs_share": lm if cases else wm, "excision_share": le if exc_n else we,
                    "mix": lay_mix, "add_ons": lay_add, "weights": [wm, we], "from": "same_day" if paired is not None else "shared"},
        "excision": {"per_day": per_day(exc_n), "mix": shares(by["excision"])},
        "visits": {"per_day": per_day(visits), "new_share": n_new / visits if visits else 0.15,
                   "new_mix": {c: v / n_new for c, v in new.items() if v} if n_new else {"99203": 0.75, "99204": 0.25},
                   "est_mix": {c: v / n_est for c, v in est.items() if v} if n_est else {"99212": 0.09, "99213": 0.69, "99214": 0.22},
                   "g2211_per_visit": u.get("G2211", 0) / visits if visits else 0.0},
        "biopsy": {"lesions": per_day(bx_first + bx_add), "per_patient": (bx_first + bx_add) / bx_first if bx_first else 1.6,
                   "first_mix": shares(by["biopsy_first"]) or {"11102": 1.0}, "add_mix": shares(by["biopsy_add"]) or {"11103": 1.0}},
        "ak": {"lesions": per_day(ak_pat * (ak_f * AK_LESIONS_15_PLUS + (1 - ak_f) * ak_n)), "per_patient": ak_n, "share_15_plus": ak_f},
        "benign": {"patients": per_day(ben), "share_15_plus": u.get("17111", 0) / ben if ben else 0.02},
        # unpaired: the part of other.codes[c] that is closures billed on a day with no Mohs case or excision of the
        # clinician's for that patient (the day's own procedure, at each payer's full rate); the rest of a closure code's
        # units there are closures past what the levers carry (later procedures of a case's day)
        "other": {"codes": other, "groups": {c: other_group(c) for c in other},
                  "unpaired": {c: per_day(min(v, spill[c])) for c, v in sorted(unpaired.items()) if min(v, spill[c]) > 1e-9}},
    }


def paid_class(family: str | None) -> str:
    """Visits, Mohs, pathology, or other procedures: the classes whose patient shares differ most."""
    if family in ("new_visit", "est_visit", "visit_addon"):
        return "visit"
    if family in ("mohs", "pathology"):
        return family
    return "procedure"


def other_group(code: str) -> str:
    """Where a code the levers do not describe belongs. The clinician's own other procedures ('rest': shaves, ED&C,
    injections, skin-substitute application...) count as their production; radiation, skin-substitute products,
    pathology, drugs and staff services ('nurse': light treatments, injections given by staff) are the practice's
    ancillary and product revenue, shown apart."""
    fam = activity.service_family(code)
    if fam == "radiation":
        return "radiation"
    if fam == "skin_substitute":
        return "skin"
    if fam == "pathology":
        return "path"
    if fam == "drug":
        return "drug"
    if fam in ("phototherapy", "therapy_injection"):
        return "nurse"
    return "rest"