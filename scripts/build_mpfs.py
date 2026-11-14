"""
Build remitwatch/mpfs.py: Medicare physician fee schedule amounts for Kentucky (CGS, carrier 15102, locality 00) and
Indiana (WPS, carrier 08102, locality 00), for the codes a dermatology practice bills.

    published years   from CMS's locality payment files (PFKY<yy>A.TXT, PFIN<yy>A.TXT, non-qualifying-APM amounts):
                   https://www.cms.gov/medicare/payment/fee-schedules/physician/carrier-specific-files
    next year      computed the way CMS does from the national relative value file released with the final rule
                   (PPRRVU<yyyy>_Jan.csv), that year's GPCIs (GPCI<yyyy>.csv) and the OPPS caps (OPPSCAP_Jan.csv):
                   [(work RVU x work GPCI) + (PE RVU x PE GPCI) + (MP RVU x MP GPCI)] x conversion factor, then the lower
                   of that and the OPPS cap. https://www.cms.gov/medicare/payment/fee-schedules/physician/pfs-relative-value-files

The computation is checked against the current year before the next year is written: the current year's relative value
file must reproduce the current year's locality payment amounts (--check-rvu-dir).

Code numbers and payment amounts only; CPT descriptions are not reproduced.

    python scripts/build_mpfs.py --pf 2025=cs2025 --pf 2026=cs2026 --rvu-dir rvu27a --next-year 2027 --check-rvu-dir rvu26a

The last --pf year is the one the computation is checked against (--check-rvu-dir must hold that year's RVU files).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from remitwatch.pfs import PFS_INDICATORS  # noqa: E402

STATES = {"KY": ("15102", "00"), "IN": ("08102", "00")}
EXTRA = {f"992{n:02d}" for n in (2, 3, 4, 5, 11, 12, 13, 14, 15)} | {"G2211", "96372", "99024"} \
    | {f"G60{n:02d}" for n in range(1, 18)} | {str(n) for n in range(96900, 97000)} | {"96567", "96573", "96574"}
CODES = set(PFS_INDICATORS) | EXTRA


def read_pf(path: str, carrier: str, locality: str) -> dict[str, tuple[float, float]]:
    out = {}
    with open(path, newline="") as fh:
        for row in csv.reader(fh):
            if len(row) < 7 or row[1] != carrier or row[2] != locality or row[4].strip():
                continue
            if row[3] in CODES:
                out[row[3]] = (float(row[5]), float(row[6]))
    return out


def _find(d: str, prefix: str) -> str:
    names = sorted(f for f in os.listdir(d) if f.upper().startswith(prefix.upper()) and f.lower().endswith(".csv"))
    names = [n for n in names if "nonqpp" in n.lower()] or [n for n in names if "_qpp" not in n.lower()] or names
    if not names:
        raise SystemExit(f"no {prefix}*.csv in {d}")
    return os.path.join(d, names[0])


def read_rvu(d: str) -> tuple[dict[str, tuple], float]:
    """Work, non-facility PE, facility PE and MP RVUs per code (no modifier), and the non-qualifying-APM conversion factor."""
    path = _find(d, "PPRRVU")
    with open(path, newline="", encoding="latin-1") as fh:
        rows = list(csv.reader(fh))
    start = next(i for i, r in enumerate(rows) if r and r[0] == "HCPCS")
    width = max(len(r) for r in rows[start - 4:start + 1])
    heads = [" ".join(rows[j][c].strip() for j in range(max(0, start - 4), start + 1) if c < len(rows[j]) and rows[j][c].strip())
             for c in range(width)]
    cf_col = next(c for c, h in enumerate(heads) if "CONV" in h and "QPP" not in h)
    out, cf = {}, None
    for r in rows[start + 1:]:
        if len(r) <= cf_col or r[1].strip() or r[0] not in CODES:
            continue
        try:
            out[r[0]] = (float(r[5] or 0), float(r[6] or 0), float(r[8] or 0), float(r[10] or 0), r[3])
            cf = cf or float(r[cf_col])
        except ValueError:
            continue
    return out, cf


def read_gpci(d: str) -> dict[str, tuple[float, float, float]]:
    """Work, PE and MP GPCIs. Where the file carries the work GPCI both with and without the 1.0 floor, the floor applies
    (it is in the year's payment amounts); a year whose file has only the unfloored column has no floor."""
    out = {}
    with open(_find(d, "GPCI"), newline="", encoding="latin-1") as fh:
        rows = list(csv.reader(fh))
    head = next(r for r in rows if any("PE GPCI" in c for c in r))
    col = {h: i for i, h in enumerate(head)}
    floored = [i for h, i in col.items() if "PW GPCI" in h and "with 1.0" in h]
    work = floored[0] if floored else next(i for h, i in col.items() if "PW GPCI" in h)
    pe = next(i for h, i in col.items() if "PE GPCI" in h)
    mp = next(i for h, i in col.items() if "MP GPCI" in h)
    for r in rows:
        for st, (carrier, loc) in STATES.items():
            if len(r) > mp and r[0].strip() == carrier and r[2].strip() == loc:
                out[st] = (float(r[work]), float(r[pe]), float(r[mp]))
    return out


def read_caps(d: str) -> dict[tuple, tuple[float, float]]:
    out = {}
    path = _find(d, "OPPSCAP")
    with open(path, newline="", encoding="latin-1") as fh:
        for r in csv.reader(fh):
            if len(r) >= 7 and not r[1].strip() and r[0] in CODES:
                for st, (carrier, loc) in STATES.items():
                    if r[3].strip() == carrier and r[4].strip() == loc:
                        out[(st, r[0])] = (float(r[6]), float(r[5]))        # non-facility, facility
    return out


def compute(rvu: dict, cf: float, gpci: dict, caps: dict) -> dict[str, dict[str, tuple[float, float]]]:
    out: dict[str, dict] = {st: {} for st in STATES}
    for code, (work, pe_nf, pe_f, mp, status) in rvu.items():
        if status not in ("A", "T", "R"):          # paid under the fee schedule
            continue
        for st, (gw, gpe, gmp) in gpci.items():
            nf = round((work * gw + pe_nf * gpe + mp * gmp) * cf, 2)
            fa = round((work * gw + pe_f * gpe + mp * gmp) * cf, 2)
            cap = caps.get((st, code))
            if cap:
                nf, fa = min(nf, cap[0]) if cap[0] else nf, min(fa, cap[1]) if cap[1] else fa
            if nf or fa:
                out[st][code] = (nf, fa)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pf", action="append", required=True, help="YEAR=DIR of that year's locality payment files")
    ap.add_argument("--rvu-dir", required=True)
    ap.add_argument("--next-year", required=True)
    ap.add_argument("--check-rvu-dir", required=True)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "remitwatch", "mpfs.py"))
    a = ap.parse_args()
    published = {}
    for item in a.pf:
        year, d = item.split("=", 1)
        yy = year[2:]
        published[year] = {st: read_pf(os.path.join(d, f"PF{st}{yy}A.TXT"), *STATES[st]) for st in STATES}
    a.year = max(published)
    current = published[a.year]

    # the method must reproduce this year's published amounts before it is trusted with next year's
    rvu0, cf0 = read_rvu(a.check_rvu_dir)
    check = compute(rvu0, cf0, read_gpci(a.check_rvu_dir), read_caps(a.check_rvu_dir))
    bad, n = [], 0
    for st in STATES:
        for code, (nf, fa) in current[st].items():
            c = check[st].get(code)
            if not c:
                continue
            n += 1
            if abs(c[0] - nf) > 0.02 or abs(c[1] - fa) > 0.02:
                bad.append((st, code, (nf, fa), c))
    print(f"check: {n} amounts compared, {len(bad)} differ by more than 2 cents")
    for b in bad[:20]:
        print("  ", b)
    if n == 0 or len(bad) > 0.01 * n:
        raise SystemExit("the computation does not reproduce the published amounts; not writing")

    rvu1, cf1 = read_rvu(a.rvu_dir)
    nxt = compute(rvu1, cf1, read_gpci(a.rvu_dir), read_caps(a.rvu_dir))
    years = sorted(published)
    lines = [
        '"""',
        "Medicare physician fee schedule amounts (participating, non-qualifying-APM) for Kentucky (CGS, carrier 15102, locality 00)",
        "and Indiana (WPS, carrier 08102, locality 00), for the codes a dermatology practice bills. Generated by",
        "scripts/build_mpfs.py from CMS files; see that script to refresh. Amounts in cents: (non-facility, facility).",
        f"{', '.join(years)}: CMS locality payment files PFKY<yy>A / PFIN<yy>A. {a.next_year}: computed from the {a.next_year} national relative",
        f"value file (conversion factor {cf1}), {a.next_year} GPCIs and OPPS caps, the same way CMS computes the locality amounts.",
        "Code numbers and amounts only; CPT descriptions are not reproduced.",
        '"""',
        "from __future__ import annotations",
        "",
        f"MPFS_YEARS = {tuple(years + [a.next_year])!r}",
        f"CONVERSION_FACTOR = {{{a.year!r}: {cf0}, {a.next_year!r}: {cf1}}}",
        "# The current year's amounts include the 1.0 floor on the work GPCI; the next year's file has no floor, so its amounts",
        "# assume the floor lapses (Congress has extended it before, at the last minute).",
        "MPFS: dict[str, dict[str, dict[str, tuple[int, int]]]] = {",
    ]
    for year, table in [(y, published[y]) for y in years] + [(a.next_year, nxt)]:
        lines.append(f"    {year!r}: {{")
        for st in STATES:
            items = sorted(table[st].items())
            lines.append(f"        {st!r}: {{")
            row = []
            for code, (nf, fa) in items:
                row.append(f"{code!r}: ({round(nf * 100)}, {round(fa * 100)})")
                if len(row) == 6:
                    lines.append("            " + ", ".join(row) + ",")
                    row = []
            if row:
                lines.append("            " + ", ".join(row) + ",")
            lines.append("        },")
        lines.append("    },")
    lines.append("}")
    lines.append("")
    lines.append("")
    lines.append("def medicare_amount(year: str, state: str, code: str, facility: bool = False) -> int | None:")
    lines.append('    """Cents Medicare allows a physician for one unit in that year and state; None when the code is not priced here."""')
    lines.append("    v = MPFS.get(year, {}).get(state, {}).get(code)")
    lines.append("    return (v[1] if facility else v[0]) if v else None")
    lines.append("")
    with open(a.out, "w") as fh:
        fh.write("\n".join(lines))
    print("wrote", a.out, {y: {st: len(t[st]) for st in STATES} for y, t in [(y, published[y]) for y in years] + [(a.next_year, nxt)]})


if __name__ == "__main__":
    main()
