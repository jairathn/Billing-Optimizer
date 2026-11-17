"""
Build the Medicare ambulatory surgery center (ASC) payment tables.

    remitwatch/ascfs.py               the dermatologic surgery codes, for the strategy lab's office-or-ASC comparison and
                                      the ASC view's estimate of the rule year (payment indicator, national unadjusted rate
                                      in cents, subject to multiple-procedure discounting), with the ASC wage index of the
                                      practice's offices by year
    remitwatch/cms/asc_rates.csv      every code on Addenda AA and BB, for the rule year and the year before: APC, payment
                                      indicator, national rate, the beneficiary's copayment, discounting, comment indicator
    remitwatch/cms/asc_counties.csv   every county's CBSA and ASC wage index for the rule year (Guam and the Virgin Islands
                                      by name, as the calculator takes them)
    remitwatch/cms/asc_meta.json      the sources, the years, the payment indicator definitions (Addendum DD1), the
                                      indicators that are not wage-adjusted, the states

Source: ASCA's "Medicare ASC final rule rate calculator" workbook, one file holding CMS's Addenda AA, BB and DD1 for the
rule year, the latest quarter's AA and BB for the year before, the ASC wage index, the county-to-CBSA crosswalk and
Addendum B (for the APC). Only CMS's own columns are read, by their headers, so next year's workbook drops in. CMS
publishes the addenda at https://www.cms.gov/medicare/payment/prospective-payment-systems/ambulatory-surgical-center-asc
(asc-payment-rates-addenda). The workbook has no wage index for the year before: give the offices' with --wage.

The older way builds ascfs.py alone from CMS's addenda CSVs (--aa YEAR=AA.csv[,EE.csv], with --wage for every year).

Payment in an ASC = national rate x (labor share x wage index + (1 - labor share)), labor share 50% (42 CFR 416.172);
the highest-rated procedure of a session at 100% and every other procedure flagged for discounting at 50%; indicator N1
codes (for example the extra Mohs stages) are packaged, with no payment of their own; office-based indicators (P2, P3,
R2) are already the lesser of the ASC rate and the PFS non-facility PE amount. The physician is paid the MPFS facility
amount in an ASC (remitwatch.mpfs).

Code numbers and amounts only: CPT and CDT descriptions are the AMA's and the ADA's and are not reproduced.

    python scripts/build_ascfs.py --asca medicare-asc-final-rule-rate-calculator-2027.xlsx \
        --wage 2026:newburgh=0.9727,owensboro=0.9018,henderson=0.8270

    python scripts/build_ascfs.py --aa 2026=AA26.csv,EE26.csv --aa 2027=AA27.csv,EE27.csv \
        --wage 2026:newburgh=0.9727,owensboro=0.9018,henderson=0.8270 --wage 2027:newburgh=0.9555,owensboro=0.8650,henderson=0.8145
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re

RANGES = [(17311, 17315), (11102, 11107), (11400, 11646), (12031, 12057), (13100, 13160), (14000, 14350),
          (15040, 15261), (15271, 15278), (15570, 15770), (17000, 17004), (17110, 17111), (17260, 17286), (11300, 11313),
          (12001, 12021)]     # 15271-15278: skin-substitute application (CMS pays 15271 G2 and 15275 P3 in an ASC)
LABOR_SHARE = 0.50
# The practice's offices, by county (FIPS): the ASC wage index follows the county's CBSA
OFFICES = {"newburgh": "18173", "owensboro": "21059", "henderson": "21101"}
# Indicators paid without the wage adjustment, as the calculator has them (its Pay_Rates sheet, column M), when the
# workbook's formula cannot be read
NOT_WAGE_ADJUSTED = ("H2", "J7", "K2", "K5", "K7", "L6")
CODE = re.compile(r"^[A-Z0-9]{5}$")
MONTHS = {"Jan": "January", "Apr": "April", "Jul": "July", "Oct": "October"}
HERE = os.path.dirname(os.path.abspath(__file__))


def in_ranges(code: str) -> bool:
    return code.isdigit() and any(a <= int(code) <= b for a, b in RANGES)


def cents(s) -> int | None:
    """Dollars as CMS writes them (a number, or text with $ and commas) in whole cents; None for a blank."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(round(float(s) * 100))
    s = str(s).replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return None


def copay_of(rate: int | None, marker: str, ira: int | None) -> int | None:
    """The beneficiary's copayment, as the calculator takes it: none where the rate is blank; nothing for a preventive
    service CMS marks '**' (coinsurance waived); the inflation-adjusted copayment Addendum BB gives a drug; else 20% of the
    rate, rounded to the cent (a fifth of a whole number of cents is never exactly half a cent, so no tie to break)."""
    if rate is None:
        return None
    if marker == "**":
        return 0
    if ira is not None:
        return ira
    return (2 * rate + 5) // 10


def rows(path: str) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        return list(csv.reader(fh))


def parse_aa(path: str) -> dict[str, tuple]:
    rs = rows(path)
    hi = next(i for i, r in enumerate(rs) if r and r[0].strip() == "HCPCS Code")
    col = {}
    for i, h in enumerate(rs[hi]):
        hl = " ".join(h.lower().split())
        if hl.startswith("subject to multiple"):
            col["mpd"] = i
        elif "payment indicator" in hl:
            col["pi"] = i
        elif "payment rate" in hl:
            col["rate"] = i
    out = {}
    for r in rs[hi + 1:]:
        if r and in_ranges(r[0].strip()):
            out[r[0].strip()] = (r[col["pi"]].strip(), cents(r[col["rate"]]), r[col["mpd"]].strip() == "Y")
    return out


def parse_ee(path: str) -> set[str]:
    rs = rows(path)
    hi = next(i for i, r in enumerate(rs) if r and r[0].strip() == "HCPCS Code")
    return {r[0].strip() for r in rs[hi + 1:] if r and in_ranges(r[0].strip())}


# --------------------------------------------------------------------------- ASCA's workbook
def _txt(v) -> str:
    return "" if v is None else " ".join(str(v).split())


def _sheet(wb, pattern: str):
    """The one sheet whose name matches; its name's groups."""
    hits = [(ws, m) for ws in wb.worksheets for m in [re.fullmatch(pattern, ws.title.strip())] if m]
    if len(hits) != 1:
        raise SystemExit(f"expected one sheet matching {pattern!r}, found {[ws.title for ws, _ in hits]}")
    return hits[0]


def _header(ws, first: str, limit: int = 40) -> tuple[int, list[str]]:
    """The row (1-based) whose column A reads `first` (case and spacing ignored), and its headers, lower-cased."""
    for i, r in enumerate(ws.iter_rows(min_row=1, max_row=limit, values_only=True), start=1):
        if r and _txt(r[0]).lower() == first.lower():
            return i, [_txt(h).lower() for h in r]
    raise SystemExit(f"no header row starting {first!r} in {ws.title!r}")


def _col(heads: list[str], *words: str, skip: tuple = ("lookup", "check", "length")) -> int | None:
    """The first column whose header holds all the words and none of ECG's helper words."""
    for i, h in enumerate(heads):
        if all(w in h for w in words) and not any(s in h for s in skip):
            return i
    return None


def read_addendum(ws, kind: str) -> dict[str, dict]:
    """Addendum AA (kind 'AA') or BB ('BB') as CMS wrote it: per code, the payment indicator, the national rate (cents),
    the beneficiary's copayment (cents), subject to discounting (AA only), the comment indicator where the sheet has one,
    and which addendum. CMS's columns only, by their headers; the '**' preventive marker sits between the code and the
    descriptor."""
    top, heads = _header(ws, "HCPCS Code")
    c_pi, c_rate = _col(heads, "payment indicator"), _col(heads, "payment rate")
    c_mpd = _col(heads, "subject to multiple procedure discounting")
    c_ci = _col(heads, "comment indicator")
    c_ira = _col(heads, "adjusted copayment")
    c_desc = _col(heads, "descriptor")
    if c_pi is None or c_rate is None or (kind == "AA" and c_mpd is None):
        raise SystemExit(f"{ws.title!r}: payment indicator, payment rate or discounting column not found in {heads}")
    out: dict[str, dict] = {}
    for r in ws.iter_rows(min_row=top + 1, values_only=True):
        code = _txt(r[0]).upper() if r else ""
        if not CODE.match(code) or code in out:
            continue
        # '**' exactly: a preventive service. '***' marks a non-opioid alternative (CAA 2023 sec. 4135), which keeps its
        # coinsurance, as the calculator has it
        marker = "**" if c_desc and any(_txt(v) == "**" for v in r[1:c_desc]) else ""
        rate = cents(r[c_rate])
        ira = cents(r[c_ira]) if c_ira is not None else None
        out[code] = {"pi": _txt(r[c_pi]).upper(), "rate": rate, "copay": copay_of(rate, marker, ira),
                     "mpd": (_txt(r[c_mpd]).upper()[:1] if c_mpd is not None else ""),
                     "ci": _txt(r[c_ci]).upper() if c_ci is not None else "", "add": kind}
    return out


def read_apc(ws) -> dict[str, str]:
    top, heads = _header(ws, "HCPCS Code")
    c_apc = _col(heads, "apc")
    out = {}
    for r in ws.iter_rows(min_row=top + 1, values_only=True):
        code = _txt(r[0]).upper() if r else ""
        if CODE.match(code) and c_apc is not None and code not in out:
            apc = _txt(r[c_apc])
            if apc and apc.lower() != "n/a":
                out[code] = apc.zfill(4) if apc.isdigit() else apc
    return out


def read_workbook(path: str) -> dict:
    """Everything the tables need from ASCA's workbook."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    aa, m = _sheet(wb, r"CY (\d{4}) ASC AA")
    year = m.group(1)
    bb, _ = _sheet(wb, rf"CY {year} ASC BB")
    paa, pm = _sheet(wb, r"((?:Jan|January|Apr|April|Jul|July|Oct|October)\w*) (\d{4}) ASC AA")
    prior, quarter = pm.group(2), pm.group(1)
    pbb, _ = _sheet(wb, rf"{quarter} {prior} ASC BB")
    dd1, _ = _sheet(wb, rf"CY {year} ASC DD1")
    wi_ws, wm = _sheet(wb, r"(\d{4})_ASC_Wage_Index")
    xw, _ = _sheet(wb, r".*Cnty_to_CBSA_Xwalk")
    adb, _ = _sheet(wb, r".*Addendum B")
    tables = {}
    for y, a, b in ((year, aa, bb), (prior, paa, pbb)):
        t = read_addendum(b, "BB")
        t.update(read_addendum(a, "AA"))           # a code on both: the calculator reads Addendum AA first
        tables[y] = t
    pi = {}
    top, _ = _header(dd1, "Indicator")
    for r in dd1.iter_rows(min_row=top + 1, values_only=True):
        if r and _txt(r[0]) and _txt(r[1]):
            pi[_txt(r[0]).upper()] = _txt(r[1])
    wage = {}
    top, heads = _header(wi_ws, "CBSA")
    for r in wi_ws.iter_rows(min_row=top + 1, values_only=True):
        if r and _txt(r[0]) and r[2] is not None:
            wage[_txt(r[0])] = (_txt(r[1]), float(r[2]))
    # the crosswalk: CMS's columns (FIPS, county, state) and the calculator's own CBSA and wage index for each county
    top, heads = _header(xw, "FIPS County Code")
    c_cbsa, c_wi = _col(heads, "cbsa for calculator", skip=()), _col(heads, "wage index for calculator", skip=())
    c_name = _col(heads, "cbsa name for calculator", skip=())
    counties = []
    for r in xw.iter_rows(min_row=top + 1, values_only=True):
        if not r or not _txt(r[0]) or not _txt(r[1]):
            continue
        cbsa = _txt(r[c_cbsa])
        wi = float(r[c_wi]) if r[c_wi] not in (None, "") else (wage.get(cbsa) or (None, None))[1]
        if wi is None:
            raise SystemExit(f"no wage index for county {r[:3]}")
        counties.append((_txt(r[2]).upper(), _txt(r[1]).upper(), _txt(r[0]).zfill(5), cbsa, _txt(r[c_name]), wi))
    states = []
    st_ws, _ = _sheet(wb, r"States")
    top, _ = _header(st_ws, "State Name")
    for r in st_ws.iter_rows(min_row=top + 1, values_only=True):
        if r and _txt(r[0]) and _txt(r[2]):
            states.append((_txt(r[2]).upper(), _txt(r[0]).upper()))
    # places the crosswalk does not list are wage-indexed by name, as the calculator's fallback does (Guam, the Virgin
    # Islands): a state with no county of its own whose name is a wage index area
    listed = {c[0] for c in counties}
    by_name = {name.upper(): (cbsa, wi) for cbsa, (name, wi) in wage.items()}
    for st, name in states:
        if st not in listed and name in by_name:
            cbsa, wi = by_name[name]
            counties.append((st, name, "", cbsa, name, wi))
    # the indicators the calculator leaves unadjusted, read from its own formula where it can be
    no_wage = NOT_WAGE_ADJUSTED
    try:
        wf = openpyxl.load_workbook(path, read_only=True, data_only=False)
        pay, _ = _sheet(wf, r"Pay_Rates_\d{4}")
        for r in pay.iter_rows(min_row=1, max_row=12, values_only=True):
            f = next((v for v in r if isinstance(v, str) and "Wage Adjustment Not Applied" in v and v.startswith("=")), None)
            if f:
                got = tuple(re.findall(r'=\s*"([A-Z][0-9])"', f))
                if got:
                    no_wage = got
                break
    except SystemExit:
        pass
    return {"year": year, "prior": prior, "quarter": quarter, "tables": tables, "pi": pi, "counties": counties,
            "states": states, "apc": read_apc(adb), "no_wage": no_wage, "wage_year": wm.group(1),
            "file": os.path.basename(path)}


# --------------------------------------------------------------------------- writers
def write_ascfs(out: str, years: dict, wage: dict, sources: dict, note: str, office_county: dict | None) -> None:
    lines = ['"""', "Medicare ASC payment inputs for dermatologic surgery codes, generated by scripts/build_ascfs.py from CMS's ASC",
             "payment addenda (AA, EE) and ASC wage index files. Per code: (payment indicator, national unadjusted rate in cents,",
             "subject to multiple-procedure discounting). Code numbers and amounts only.", '"""', "from __future__ import annotations", "",
             f"LABOR_SHARE = {LABOR_SHARE}",
             f"SOURCES = {sources!r}",
             f"WAGE_INDEX = {wage!r}      # by year and office: {note}"]
    if office_county:
        lines.append(f"OFFICE_COUNTY = {office_county!r}      # the offices' counties (FIPS), for the wage index of the year to come")
    lines += [f"EXCLUDED = {({y: v['ee'] for y, v in sorted(years.items())})!r}",
              "ASC: dict[str, dict[str, tuple[str, int | None, bool]]] = {"]
    for y, v in sorted(years.items()):
        lines.append(f"    {y!r}: {{")
        items = sorted(v["aa"].items())
        for i in range(0, len(items), 4):
            lines.append("        " + ", ".join(f"{c!r}: {t!r}" for c, t in items[i:i + 4]) + ",")
        lines.append("    },")
    lines += ["}", "", "",
              "def asc_amount(year: str, office: str, code: str) -> int | None:",
              '    """Cents Medicare pays the ASC for one procedure in that year at that office, before multiple-procedure',
              '    discounting; None when packaged (no separate payment), excluded, or not listed."""',
              "    row = ASC.get(year, {}).get(code)",
              "    wi = WAGE_INDEX.get(year, {}).get(office)",
              "    if not row or row[1] is None or row[0] == 'N1' or wi is None:",
              "        return None",
              "    return round(row[1] * (LABOR_SHARE * wi + (1 - LABOR_SHARE)))", ""]
    with open(out, "w") as fh:
        fh.write("\n".join(lines))


def write_data(data_dir: str, wb: dict, wage: dict) -> dict:
    """The rate lookup's tables: one row per code (every code on either year's addenda), one per county, and the rest."""
    os.makedirs(data_dir, exist_ok=True)
    years = sorted(wb["tables"])
    fields = ("pi", "rate", "copay", "mpd", "ci", "add")
    codes = sorted(set().union(*(set(t) for t in wb["tables"].values())))
    with open(os.path.join(data_dir, "asc_rates.csv"), "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["code", "apc"] + [f"{f}_{y}" for y in years for f in fields])
        for c in codes:
            row = [c, wb["apc"].get(c, "")]
            for y in years:
                r = wb["tables"][y].get(c)
                row += ["" if r is None or r[f] is None else r[f] for f in fields]
            w.writerow(row)
    with open(os.path.join(data_dir, "asc_counties.csv"), "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["state", "county", "fips", "cbsa", "cbsa_name", f"wi_{wb['wage_year']}"])
        for row in sorted(wb["counties"], key=lambda r: (r[0], r[1])):
            w.writerow(row[:5] + (f"{row[5]:.4f}",))
    meta = {
        "source": wb["file"],
        "rule_year": wb["year"], "prior_year": wb["prior"], "wage_year": wb["wage_year"],
        # how the page names each year's rates: 'October 2026', 'the CY 2027 final rule'
        "years": {wb["year"]: f"the CY {wb['year']} final rule", wb["prior"]: f"{MONTHS.get(wb['quarter'][:3], wb['quarter'])} {wb['prior']}"},
        "labor_share": LABOR_SHARE, "coinsurance": 0.2, "sequestration": 0.02,
        "not_wage_adjusted": list(wb["no_wage"]),
        "pi": wb["pi"],
        "states": dict(wb["states"]),
        # the offices' counties and every year's wage index for them (the year before from --wage: the workbook has none)
        "offices": {o: {"fips": f, "wage_index": {y: wage[y][o] for y in sorted(wage) if o in wage.get(y, {})}} for o, f in OFFICES.items()},
    }
    with open(os.path.join(data_dir, "asc_meta.json"), "w") as fh:
        json.dump(meta, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return {"codes": len(codes), "counties": len(wb["counties"]), "years": years}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asca", help="ASCA's Medicare ASC final rule rate calculator workbook (.xlsx)")
    ap.add_argument("--aa", action="append", default=[], help="YEAR=AA.csv[,EE.csv] (CMS's addenda CSVs, ascfs.py only)")
    ap.add_argument("--wage", action="append", default=[], help="YEAR:office=index,office=index")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "remitwatch", "ascfs.py"))
    ap.add_argument("--data-dir", default=os.path.join(HERE, "..", "remitwatch", "cms"))
    a = ap.parse_args()
    if not a.asca and not a.aa:
        ap.error("give --asca or --aa")
    wage: dict[str, dict] = {}
    for item in a.wage:
        year, pairs = item.split(":", 1)
        wage[year] = {k: float(v) for k, v in (p.split("=") for p in pairs.split(","))}
    years: dict[str, dict] = {}
    if a.asca:
        wb = read_workbook(a.asca)
        by_fips = {c[2]: c for c in wb["counties"] if c[2]}
        missing = [o for o, f in OFFICES.items() if f not in by_fips]
        if missing:
            raise SystemExit(f"offices not in the crosswalk: {missing}")
        wage[wb["wage_year"]] = {o: by_fips[f][5] for o, f in OFFICES.items()}
        if wb["prior"] not in wage or set(wage[wb["prior"]]) != set(OFFICES):
            raise SystemExit(f"the workbook has no wage index for {wb['prior']}: give --wage {wb['prior']}:office=index for {sorted(OFFICES)}")
        listed = {c for t in wb["tables"].values() for c, r in t.items() if r["add"] == "AA" and in_ranges(c)}
        for y, t in wb["tables"].items():
            aa = {c: (r["pi"], r["rate"], r["mpd"] == "Y") for c, r in t.items() if r["add"] == "AA" and in_ranges(c)}
            # not on the year's list, though on the other year's: not payable in an ASC that year
            years[y] = {"aa": aa, "ee": sorted(listed - set(aa)), "files": [f"{wb['file']} ({'CY ' + y if y == wb['year'] else wb['quarter'] + ' ' + y} ASC AA, BB)"]}
        names = {o: f"{by_fips[f][1].title()} County {by_fips[f][0]}, CBSA {by_fips[f][3]} {by_fips[f][4]}" for o, f in OFFICES.items()}
        note = "; ".join(f"{o.title()} ({names[o]})" for o in OFFICES)
        wage = {y: {o: wage[y][o] for o in OFFICES} for y in sorted(wage)}
        write_ascfs(a.out, years, wage, {y: v["files"] for y, v in sorted(years.items())}, note, dict(OFFICES))
        stats = write_data(a.data_dir, wb, wage)
        print("wrote", a.out, {y: len(v["aa"]) for y, v in years.items()}, "and", a.data_dir, stats)
        return
    for item in a.aa:
        year, paths = item.split("=", 1)
        parts = paths.split(",")
        aa = parse_aa(parts[0])
        ee = parse_ee(parts[1]) if len(parts) > 1 else set()
        years[year] = {"aa": aa, "ee": sorted(ee), "files": [os.path.basename(p) for p in parts]}
    note = "Newburgh (CBSA 21780 Evansville IN), Owensboro (36980), Henderson"
    write_ascfs(a.out, years, wage, {y: v["files"] for y, v in years.items()}, note, None)
    print("wrote", a.out, {y: len(v["aa"]) for y, v in years.items()})


if __name__ == "__main__":
    main()
