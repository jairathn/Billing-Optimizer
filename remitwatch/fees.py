"""
Fee-schedule inference from the practice's own remittance history.

The 835 echoes the submitted charge for every service line (SVC02), even when
the payer paid a different code (SVC01). The practice bills from a small set
of fee schedules (a Medicare-rate schedule and a commercial schedule), so each
CPT code has only a few distinct charge amounts per year. That makes the
charge amount a fingerprint of the code that was actually billed:

    paid code 99213, charge $223.65, year 2026 -> $223.65 is the practice's
    99214 commercial fee and is not a 99213 fee -> the line was downcoded.

This signal does not need the payer to populate SVC06 and does not need the
EMA export. On this practice's data it agreed with SVC06 in 103 of 103 lines
where both were available.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

EM_NEW = ["99202", "99203", "99204", "99205"]
EM_EST = ["99211", "99212", "99213", "99214", "99215"]
EM_ALL = set(EM_NEW) | set(EM_EST)

# families in which a level change is a "downcode": same family, lower level
LEVEL_FAMILIES: dict[str, list[str]] = {
    "em_new": EM_NEW,
    "em_est": EM_EST,
    "biopsy_tangential": ["11102", "11103"],
    "biopsy_punch": ["11104", "11105"],
    "biopsy_incisional": ["11106", "11107"],
    "repair_intermediate_trunk": ["12031", "12032", "12034", "12035", "12036", "12037"],
    "repair_intermediate_face": ["12051", "12052", "12053", "12054", "12055", "12056", "12057"],
    "repair_complex_trunk": ["13100", "13101"],
    "repair_complex_face": ["13131", "13132"],
    "excision_benign_trunk": ["11400", "11401", "11402", "11403", "11404", "11406"],
    "excision_benign_scalp": ["11420", "11421", "11422", "11423", "11424", "11426"],
    "excision_benign_face": ["11440", "11441", "11442", "11443", "11444", "11446"],
    "excision_malignant_trunk": ["11600", "11601", "11602", "11603", "11604", "11606"],
    "excision_malignant_scalp": ["11620", "11621", "11622", "11623", "11624", "11626"],
    "excision_malignant_face": ["11640", "11641", "11642", "11643", "11644", "11646"],
    "destruction_premalignant": ["17000", "17004"],
    "destruction_benign": ["17110", "17111"],
}
CODE_FAMILY: dict[str, tuple[str, int]] = {}
for _fam, _codes in LEVEL_FAMILIES.items():
    for _i, _c in enumerate(_codes):
        CODE_FAMILY[_c] = (_fam, _i)


def family_of(code: str | None) -> tuple[str, int] | None:
    return CODE_FAMILY.get(code or "")


def is_downcode(original: str | None, paid: str | None) -> bool:
    """True when paid is a lower level of the same family as original."""
    a, b = family_of(original), family_of(paid)
    return bool(a and b and a[0] == b[0] and b[1] < a[1])


class FeeTable:
    """Known charge amounts per (year, code), learned from lines the payer did not alter."""

    def __init__(self, min_share: float = 0.02, min_count: int = 12):
        self.min_share = min_share
        self.min_count = min_count
        self._counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
        self.fees: dict[tuple[str, str], dict[int, float]] = {}
        self.charge_index: dict[str, dict[int, set[str]]] = {}

    def observe(self, year: str, code: str, charge_cents: int | None, units: float | None, altered: bool) -> None:
        if not year or not code or not charge_cents or charge_cents <= 0 or altered:
            return
        if units not in (None, 1, 1.0):
            return
        self._counts[(year, code)][charge_cents] += 1

    def observe_lines(self, lines: Iterable[dict]) -> None:
        for l in lines:
            year = (l.get("service_date") or l.get("payment_date") or "")[:4]
            self.observe(year, l.get("procedure_code"), l.get("charge_cents"), l.get("paid_units"),
                         bool(l.get("original_procedure_code")))

    def finalize(self) -> "FeeTable":
        self.fees = {}
        self.charge_index = defaultdict(lambda: defaultdict(set))
        for key, cnt in self._counts.items():
            total = sum(cnt.values())
            if total < self.min_count:
                continue
            known = {chg: n / total for chg, n in cnt.items() if n >= self.min_count and n / total >= self.min_share}
            if known:
                self.fees[key] = known
                year, code = key
                for chg in known:
                    self.charge_index[year][chg].add(code)
        return self

    def known_fee(self, year: str, code: str, charge_cents: int | None) -> bool:
        return charge_cents in self.fees.get((year, code), {})

    def codes_with_fee(self, year: str, charge_cents: int | None) -> set[str]:
        if charge_cents is None:
            return set()
        return set(self.charge_index.get(year, {}).get(charge_cents, set()))

    def implied_original(self, year: str, paid_code: str | None, charge_cents: int | None) -> str | None:
        """If the charge is a known fee for a higher code in the paid code's family and is
        not a known fee for the paid code itself, return that higher code."""
        if not paid_code or not charge_cents:
            return None
        fam = family_of(paid_code)
        if fam is None:
            return None
        if self.known_fee(year, paid_code, charge_cents):
            return None
        candidates = [c for c in self.codes_with_fee(year, charge_cents)
                      if family_of(c) and family_of(c)[0] == fam[0] and family_of(c)[1] > fam[1]]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            # ambiguous: pick the closest higher level
            return sorted(candidates, key=lambda c: family_of(c)[1])[0]
        return None

    def to_rows(self) -> list[dict]:
        out = []
        for (year, code), known in sorted(self.fees.items()):
            for chg, share in sorted(known.items()):
                out.append({"year": year, "code": code, "charge_cents": chg, "share": round(share, 4)})
        return out


class AllowedTable:
    """Typical allowed amount per (payer, year, code): the payer's own revealed rate.

    Built from cleanly paid primary lines (one unit, paid > 0, no code change).
    The mode is used rather than the mean so a few odd lines cannot move it.
    """

    def __init__(self, min_count: int = 5):
        self.min_count = min_count
        self._counts: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
        self.rates: dict[tuple[str, str, str], dict] = {}

    def observe(self, payer_key: str, year: str, code: str, allowed_cents: int | None, units, altered: bool,
                paid_cents: int | None, claim_status: str | None) -> None:
        if not payer_key or not year or not code or not allowed_cents or allowed_cents <= 0:
            return
        if altered or units not in (None, 1, 1.0) or (paid_cents is not None and paid_cents <= 0):
            return
        if claim_status not in (None, "1", "19"):
            return
        self._counts[(payer_key, year, code)][allowed_cents] += 1

    def finalize(self) -> "AllowedTable":
        self.rates = {}
        for key, cnt in self._counts.items():
            n = sum(cnt.values())
            if n < self.min_count:
                continue
            mode, mode_n = cnt.most_common(1)[0]
            vals = sorted(cnt.elements())
            median = vals[len(vals) // 2]
            self.rates[key] = {"mode_cents": mode, "mode_share": mode_n / n, "median_cents": median, "n": n,
                               "p90_cents": vals[int(0.9 * (len(vals) - 1))], "p10_cents": vals[int(0.1 * (len(vals) - 1))]}
        return self

    def expected(self, payer_key: str, year: str, code: str) -> dict | None:
        r = self.rates.get((payer_key, year, code))
        if r:
            return r
        # fall back to adjacent years for the same payer and code
        for y in (str(int(year) - 1), str(int(year) + 1)) if year.isdigit() else ():
            r = self.rates.get((payer_key, y, code))
            if r:
                return dict(r, fallback_year=y)
        return None

    def to_rows(self) -> list[dict]:
        return [{"payer_key": k[0], "year": k[1], "code": k[2], **v} for k, v in sorted(self.rates.items())]
