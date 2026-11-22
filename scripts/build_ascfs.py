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
    remitwatch/cms/asc_meta.json      the source and the day CMS posted it, the years, the payment indicator definitions
                                      (Addendum DD1), the indicators that are not wage-adjusted, the states

CMS's own files are the reference. CMS posts them, and posts them again when it corrects them, at
https://www.cms.gov/medicare/payment/prospective-payment-systems/ambulatory-surgical-center-asc (the ASC payment rates
addenda, and the ASC wage index files that come with each rule). Each zip holds the CSVs read here:

    --cms YEAR=AA.csv,BB.csv   Addenda AA and BB: once for the rule year, once for the year before (its latest quarter)
    --counties XWALK.csv       the rule year's "ASC Wage Index County to CBSA Crosswalk": every county's final CBSA and
                               final wage index (after the 5% cap), and its wage index the year before
    --dd1 DD1.csv              the rule year's payment indicator definitions
    --ff FF.csv                the rule year's Addendum FF, for the APC of each surgical procedure
    --addendum-b B.csv         the rule year's OPPS Addendum B (from CMS's hospital outpatient pages), for the APC of
                               every other code: the ASC files do not hold it
    --released 2026-11-10      the day CMS posted, or last updated, the rule year's addenda, as its page says

Two ways to build the tables:

    from CMS's CSVs alone      the usual way. Without --addendum-b the APCs already in remitwatch/cms/asc_rates.csv are
                               kept when that table is for the same rule year, and left empty when it is not (an APC can
                               move from one year to the next)
    --asca WORKBOOK            from ASCA's "Medicare ASC final rule rate calculator", one workbook holding a copy of the
                               same addenda, the wage index, a county crosswalk and Addendum B. Only CMS's own columns are
                               read, by their headers. The workbook can be behind CMS: version 77 was set up on 2026-11-06,
                               CMS updated the CY 2027 addenda on 2026-11-10, and 68 codes and 5 counties differed. So
                               CMS's CSVs are given all the same, and the build lists every difference and writes nothing
                               if there is one. The workbook has no wage index for the year before: the crosswalk gives it,
                               or --wage.

Either way the tables are written to a scratch folder first, read back, and compared with CMS's CSVs: every code's payment
indicator, rate, copayment, discounting and comment indicator, every county's CBSA and wage index, the indicator
definitions, the APCs Addendum FF gives. Nothing in the repository is replaced unless they are equal. (--unchecked, with
--asca only, skips the comparison and says so in asc_meta.json.)

The older way builds ascfs.py alone from CMS's addenda CSVs (--aa YEAR=AA.csv[,EE.csv], with --wage for every year). It
leaves remitwatch/cms as it is, so it refuses years other than the ones those tables hold.

Payment in an ASC = national rate x (labor share x wage index + (1 - labor share)), labor share 50% (42 CFR 416.172);
the highest-rated procedure of a session at 100% and every other procedure flagged for discounting at 50%; indicator N1
codes (for example the extra Mohs stages) are packaged, with no payment of their own; office-based indicators (P2, P3,
R2) are already the lesser of the ASC rate and the PFS non-facility PE amount. The physician is paid the MPFS facility
amount in an ASC (remitwatch.mpfs).

Code numbers and amounts only: CPT and CDT descriptions are the AMA's and the ADA's and are not reproduced.

    python scripts/build_ascfs.py --cms 2027=AA27.csv,BB27.csv --cms 2026=AA26oct.csv,BB26oct.csv \
        --counties crosswalk27.csv --dd1 DD1_27.csv --ff FF27.csv --released 2026-11-10

    python scripts/build_ascfs.py --asca medicare-asc-final-rule-rate-calculator-2027.xlsx \
        --cms 2027=AA27.csv,BB27.csv --cms 2026=AA26oct.csv,BB26oct.csv --counties crosswalk27.csv --released 2026-11-10

    python scripts/build_ascfs.py --aa 2026=AA26.csv,EE26.csv --aa 2027=AA27.csv,EE27.csv \
        --wage 2026:newburgh=0.9727,owensboro=0.9018,henderson=0.8270 --wage 2027:newburgh=0.9555,owensboro=0.8650,henderson=0.8145
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import os
import re
import runpy
import shutil
import tempfile
from decimal import ROUND_HALF_UP, Decimal

RANGES = [(17311, 17315), (11102, 11107), (11400, 11646), (12031, 12057), (13100, 13160), (14000, 14350),
          (15040, 15261), (15271, 15278), (15570, 15770), (17000, 17004), (17110, 17111), (17260, 17286), (11300, 11313),
          (12001, 12021)]     # 15271-15278: skin-substitute application (CMS pays 15271 G2 and 15275 P3 in an ASC)
LABOR_SHARE = 0.50
# The practice's offices, by county (FIPS): the ASC wage index follows the county's CBSA
OFFICES = {"newburgh": "18173", "owensboro": "21059", "henderson": "21101"}
# Indicators paid without the wage adjustment, as the calculator has them (its Pay_Rates sheet, column M), when the
# workbook's formula cannot be read
NOT_WAGE_ADJUSTED = ("H2", "J7", "K2", "K5", "K7", "L6")
# Skin substitute supplies (S2) are paid at the OPPS rate and are not subject to the ASC wage index: CY 2026 OPPS/ASC
# final rule, 90 FR 53843 ("payment for these skin substitute products would not be subject to the ASC wage index") and
# 90 FR 53887-53888 ("these prospective rates would not be subject to the ASC wage index adjustment ... we are finalizing
# as proposed"), kept for CY 2027 (91 FR 70652: "at the same rate as is provided under the OPPS"). The contractor's ASC
# fee files show it: its January 2026 files pay every S2 code one amount in four CBSAs whose wage indexes run from 0.8565
# to 1.0022. ASCA's calculator (version 77) wage-adjusts them; the tables follow CMS.
CMS_NOT_WAGE_ADJUSTED = ("S2",)
CODE = re.compile(r"^[A-Z0-9]{5}$")
MONTHS = {"Jan": "January", "Apr": "April", "Jul": "July", "Oct": "October"}
# The picker's states, as ASCA's States sheet names them; CMS's crosswalk has the abbreviations only
STATES = {
    "AL": "ALABAMA", "AK": "ALASKA", "AZ": "ARIZONA", "AR": "ARKANSAS", "CA": "CALIFORNIA", "CO": "COLORADO", "CT": "CONNECTICUT",
    "DE": "DELAWARE", "DC": "DISTRICT OF COLUMBIA", "FL": "FLORIDA", "GA": "GEORGIA", "GU": "GUAM", "HI": "HAWAII", "ID": "IDAHO",
    "IL": "ILLINOIS", "IN": "INDIANA", "IA": "IOWA", "KS": "KANSAS", "KY": "KENTUCKY", "LA": "LOUISIANA", "ME": "MAINE",
    "MD": "MARYLAND", "MA": "MASSACHUSETTS", "MI": "MICHIGAN", "MN": "MINNESOTA", "MS": "MISSISSIPPI", "MO": "MISSOURI",
    "MT": "MONTANA", "NE": "NEBRASKA", "NV": "NEVADA", "NH": "NEW HAMPSHIRE", "NJ": "NEW JERSEY", "NM": "NEW MEXICO",
    "NY": "NEW YORK", "NC": "NORTH CAROLINA", "ND": "NORTH DAKOTA", "OH": "OHIO", "OK": "OKLAHOMA", "OR": "OREGON",
    "PA": "PENNSYLVANIA", "PR": "PUERTO RICO", "RI": "RHODE ISLAND", "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA",
    "TN": "TENNESSEE", "TX": "TEXAS", "UT": "UTAH", "VT": "VERMONT", "VA": "VIRGINIA", "VI": "VIRGIN ISLANDS",
    "WA": "WASHINGTON", "WV": "WEST VIRGINIA", "WI": "WISCONSIN", "WY": "WYOMING",
}
TABLES = ("asc_rates.csv", "asc_counties.csv", "asc_meta.json")
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
    """A CSV's rows. CMS's files are UTF-8, or Windows-1252 where a name has an accent (Mayagüez) or a note a curly
    quote."""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return list(csv.reader(io.StringIO(text, newline="")))


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


# --------------------------------------------------------------------------- CMS's tables, in a workbook sheet or a CSV
def _txt(v) -> str:
    return "" if v is None else " ".join(str(v).split())


def _cell(r, i):
    """A row's cell, None past its end (a note line in a CSV is shorter than the table)."""
    return r[i] if i is not None and i < len(r) else None


def _sheet(wb, pattern: str):
    """The one sheet whose name matches; its name's groups."""
    hits = [(ws, m) for ws in wb.worksheets for m in [re.fullmatch(pattern, ws.title.strip())] if m]
    if len(hits) != 1:
        raise SystemExit(f"expected one sheet matching {pattern!r}, found {[ws.title for ws, _ in hits]}")
    return hits[0]


def _grid(ws) -> list[tuple]:
    """A sheet's rows, as values."""
    return list(ws.iter_rows(values_only=True))


def _header(grid: list, name: str, first: str, limit: int = 40) -> tuple[int, list[str]]:
    """The index of the row whose first cell reads `first` (case and spacing ignored), and its headers, lower-cased."""
    for i, r in enumerate(grid[:limit]):
        if r and _txt(r[0]).lower() == first.lower():
            return i, [_txt(h).lower() for h in r]
    raise SystemExit(f"no header row starting {first!r} in {name!r}")


def _col(heads: list[str], *words: str, skip: tuple = ("lookup", "check", "length")) -> int | None:
    """The first column whose header holds all the words and none of ECG's helper words."""
    for i, h in enumerate(heads):
        if all(w in h for w in words) and not any(s in h for s in skip):
            return i
    return None


def _period(heads: list[str], name: str) -> tuple[str, str]:
    """The year and the release an addendum's own headers name: ('2027', 'final rule') from 'Final CY 2027 Payment
    Indicator', ('2026', 'October') from 'October 2026 Payment Indicator'."""
    h = heads[_col(heads, "payment indicator")]
    m = re.search(r"(final|proposed)\s+cy\s+(\d{4})", h)
    if m:
        return m.group(2), f"{m.group(1)} rule"
    m = re.search(r"(january|april|july|october)\s+(\d{4})", h)
    if m:
        return m.group(2), m.group(1).title()
    raise SystemExit(f"{name!r}: no year in the payment indicator's header {h!r}")


def _label(year: str, release: str) -> str:
    """How the page names a year's rates: 'October 2026', 'the CY 2027 final rule'."""
    month = MONTHS.get(release[:3])
    return f"{month} {year}" if month else f"the CY {year} {release}"


def read_addendum(grid: list, name: str, kind: str) -> dict[str, dict]:
    """Addendum AA (kind 'AA') or BB ('BB') as CMS wrote it: per code, the payment indicator, the national rate (cents),
    the beneficiary's copayment (cents), subject to discounting (AA only), the comment indicator where the sheet has one,
    and which addendum. CMS's columns only, by their headers; the '**' preventive marker sits between the code and the
    descriptor."""
    top, heads = _header(grid, name, "HCPCS Code")
    c_pi, c_rate = _col(heads, "payment indicator"), _col(heads, "payment rate")
    c_mpd = _col(heads, "subject to multiple procedure discounting")
    c_ci = _col(heads, "comment indicator")
    c_ira = _col(heads, "adjusted copayment")
    c_desc = _col(heads, "descriptor")
    if c_pi is None or c_rate is None or (kind == "AA" and c_mpd is None):
        raise SystemExit(f"{name!r}: payment indicator, payment rate or discounting column not found in {heads}")
    out: dict[str, dict] = {}
    for r in grid[top + 1:]:
        code = _txt(r[0]).upper() if r else ""
        if not CODE.match(code) or code in out:
            continue
        # '**' exactly: a preventive service. '***' marks a non-opioid alternative (CAA 2023 sec. 4135), which keeps its
        # coinsurance, as the calculator has it
        marker = "**" if c_desc and any(_txt(v) == "**" for v in r[1:c_desc]) else ""
        rate = cents(_cell(r, c_rate))
        ira = cents(_cell(r, c_ira)) if c_ira is not None else None
        out[code] = {"pi": _txt(_cell(r, c_pi)).upper(), "rate": rate, "copay": copay_of(rate, marker, ira),
                     "mpd": (_txt(_cell(r, c_mpd)).upper()[:1] if c_mpd is not None else ""),
                     "ci": _txt(_cell(r, c_ci)).upper() if c_ci is not None else "", "add": kind}
    return out


def read_apc(grid: list, name: str) -> dict[str, str]:
    """Each code's APC: OPPS Addendum B's 'APC' column, or Addendum FF's 'Final CY ... APC'."""
    top, heads = _header(grid, name, "HCPCS Code")
    c_apc = next((i for i, h in enumerate(heads) if re.fullmatch(r"(.* )?apc", h)), None)
    out = {}
    for r in grid[top + 1:]:
        code = _txt(r[0]).upper() if r else ""
        if CODE.match(code) and c_apc is not None and code not in out:
            apc = _txt(_cell(r, c_apc))
            if apc and apc.lower() != "n/a":
                out[code] = apc.zfill(4) if apc.isdigit() else apc
    return out


def read_definitions(grid: list, name: str) -> dict[str, str]:
    """Addendum DD1: what each payment indicator means."""
    top, _ = _header(grid, name, "Indicator")
    return {_txt(r[0]).upper(): _txt(r[1]) for r in grid[top + 1:] if r and len(r) > 1 and _txt(r[0]) and _txt(r[1])}


# --------------------------------------------------------------------------- ASCA's workbook
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
        t = read_addendum(_grid(b), b.title, "BB")
        t.update(read_addendum(_grid(a), a.title, "AA"))           # a code on both: the calculator reads Addendum AA first
        tables[y] = t
    pi = read_definitions(_grid(dd1), dd1.title)
    wage = {}
    grid = _grid(wi_ws)
    top, heads = _header(grid, wi_ws.title, "CBSA")
    for r in grid[top + 1:]:
        if r and _txt(r[0]) and _cell(r, 2) is not None:
            wage[_txt(r[0])] = (_txt(r[1]), float(r[2]))
    # the crosswalk: CMS's columns (FIPS, county, state) and the calculator's own CBSA and wage index for each county
    grid = _grid(xw)
    top, heads = _header(grid, xw.title, "FIPS County Code")
    c_cbsa, c_wi = _col(heads, "cbsa for calculator", skip=()), _col(heads, "wage index for calculator", skip=())
    c_name = _col(heads, "cbsa name for calculator", skip=())
    counties = []
    for r in grid[top + 1:]:
        if not r or not _txt(r[0]) or not _txt(r[1]):
            continue
        cbsa = _txt(r[c_cbsa])
        wi = float(r[c_wi]) if r[c_wi] not in (None, "") else (wage.get(cbsa) or (None, None))[1]
        if wi is None:
            raise SystemExit(f"no wage index for county {r[:3]}")
        counties.append((_txt(r[2]).upper(), _txt(r[1]).upper(), _txt(r[0]).zfill(5), cbsa, _txt(r[c_name]), wi))
    states = []
    st_ws, _ = _sheet(wb, r"States")
    grid = _grid(st_ws)
    top, _ = _header(grid, st_ws.title, "State Name")
    for r in grid[top + 1:]:
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
    name = os.path.basename(path)
    return {"year": year, "prior": prior, "releases": {year: "final rule", prior: quarter}, "tables": tables, "pi": pi,
            "counties": counties, "states": states, "apc": read_apc(_grid(adb), adb.title), "no_wage": no_wage,
            "wage_year": wm.group(1), "prior_wage": None, "file": name,
            "files": {year: [f"{name} (CY {year} ASC AA, BB)"], prior: [f"{name} ({quarter} {prior} ASC AA, BB)"]}}


# --------------------------------------------------------------------------- CMS's own CSVs
def read_crosswalk(path: str) -> tuple[list[tuple], dict[str, float], str]:
    """CMS's county crosswalk for the rule year: the counties as the tables hold them (state, county, FIPS, final CBSA, its
    name, the final wage index after the 5% cap), each county's wage index the year before (by FIPS), and the rule year.
    A county keeps a transition CBSA of its own while the cap holds its wage index above its new area's. The STATEWIDE
    rows (a state's rural area) are not counties: only a state with no county of its own (Guam, the Virgin Islands) is
    listed from one, under the state's name and with no FIPS code, as the calculator has them. A rural area is named
    'RURAL <STATE>', as the calculator names it."""
    grid, name = rows(path), os.path.basename(path)
    top, heads = _header(grid, name, "FIPS Code")
    find = lambda pat: next((i for i, h in enumerate(heads) if re.fullmatch(pat, h)), None)
    c_county, c_state = find(r"county name"), find(r"state")
    c_cbsa, c_name, c_wi = find(r"final cy \d{4} cbsa"), find(r"final cy \d{4} cbsa name"), find(r"final cy \d{4} asc wage index.*")
    c_prior = find(r"cy \d{4} asc wage index")
    if None in (c_county, c_state, c_cbsa, c_name, c_wi):
        raise SystemExit(f"{name!r}: county, state, final CBSA or final wage index column not found in {heads}")
    year = re.search(r"\d{4}", heads[c_wi]).group(0)
    counties, statewide, prior = [], {}, {}
    for r in grid[top + 1:]:
        fips, state = _txt(_cell(r, 0)), _txt(_cell(r, c_state)).upper()
        if not fips.isdigit() or not state:
            continue                                        # the notes under the table
        county, cbsa, wi = _txt(_cell(r, c_county)).upper(), _txt(_cell(r, c_cbsa)), _txt(_cell(r, c_wi))
        area = _txt(_cell(r, c_name))
        if county == "STATEWIDE":
            if wi:
                statewide[state] = (state, STATES.get(state, area.upper()), "", cbsa, area, float(wi))
            continue
        if not wi:
            raise SystemExit(f"{name!r}: no wage index for county {fips} {county}, {state}")
        counties.append((state, county, fips.zfill(5), cbsa, f"RURAL {area}" if len(cbsa) <= 2 else area, float(wi)))
        if c_prior is not None and _txt(_cell(r, c_prior)):
            prior[fips.zfill(5)] = float(_txt(_cell(r, c_prior)))
    listed = {c[0] for c in counties}
    counties += [row for st, row in sorted(statewide.items()) if st not in listed]
    return counties, prior, year


def read_cms(cms: dict, data_dir: str) -> dict:
    """Everything the tables need from CMS's own CSVs (the files `cms_files` checked)."""
    if len(cms["years"]) != 2 or not cms["counties"] or not cms["dd1"]:
        raise SystemExit("to build from CMS's files give --cms twice (the rule year and the year before), --counties and --dd1")
    prior, year = sorted(cms["years"])
    tables, releases, files = {}, {}, {}
    for y, (aa, bb) in cms["years"].items():
        ga, gb = rows(aa), rows(bb)
        for grid, path in ((ga, aa), (gb, bb)):
            got, release = _period(_header(grid, os.path.basename(path), "HCPCS Code")[1], os.path.basename(path))
            if got != y:
                raise SystemExit(f"{os.path.basename(path)!r} is for {got}, not {y}")
            if releases.setdefault(y, release) != release:
                raise SystemExit(f"{y}: Addenda AA and BB are from different releases ({releases[y]}, {release})")
        t = read_addendum(gb, os.path.basename(bb), "BB")
        t.update(read_addendum(ga, os.path.basename(aa), "AA"))     # a code on both: Addendum AA first, as the calculator
        tables[y] = t
        files[y] = [os.path.basename(aa), os.path.basename(bb)]
    counties, prior_wi, wage_year = read_crosswalk(cms["counties"])
    # the APC: OPPS Addendum B's, which CMS's ASC files do not carry. Without that file, the APCs the tables already hold
    # for this same rule year (read from ASCA's copy of Addendum B); never another year's. build() lays Addendum FF's
    # over them
    apc: dict[str, str] = {}
    held, held_meta = os.path.join(data_dir, "asc_rates.csv"), os.path.join(data_dir, "asc_meta.json")
    if cms.get("addendum_b"):
        apc = read_apc(rows(cms["addendum_b"]), os.path.basename(cms["addendum_b"]))
    elif os.path.exists(held) and os.path.exists(held_meta):
        with open(held_meta, encoding="utf-8") as fh:
            same_year = json.load(fh).get("rule_year") == year
        if same_year:
            with open(held, newline="", encoding="utf-8") as fh:
                apc = {r["code"]: r["apc"] for r in csv.DictReader(fh) if r.get("apc")}
    states = sorted({c[0] for c in counties})
    return {"year": year, "prior": prior, "releases": releases, "tables": tables,
            "pi": read_definitions(rows(cms["dd1"]), os.path.basename(cms["dd1"])),
            "counties": counties, "states": [(st, STATES.get(st, st)) for st in states], "apc": apc,
            "no_wage": NOT_WAGE_ADJUSTED, "wage_year": wage_year,
            "prior_wage": {o: prior_wi[f] for o, f in OFFICES.items() if f in prior_wi} or None, "file": None, "files": files}


def cms_files(a) -> dict:
    """The CMS files named on the command line, each one found on disk."""
    years = {}
    for item in a.cms:
        year, _, paths = item.partition("=")
        parts = paths.split(",")
        if not re.fullmatch(r"\d{4}", year) or len(parts) != 2:
            raise SystemExit(f"--cms takes YEAR=AA.csv,BB.csv, not {item!r}")
        years[year] = (parts[0], parts[1])
    out = {"years": years, "counties": a.counties, "dd1": a.dd1, "ff": a.ff, "addendum_b": a.addendum_b, "released": a.released}
    for p in [p for pair in years.values() for p in pair] + [a.counties, a.dd1, a.ff, a.addendum_b]:
        if p and not os.path.isfile(p):
            raise SystemExit(f"no such file: {p}")
    if years and not a.released:
        raise SystemExit("--released: say which posting of the rule year's addenda these files are (the date on CMS's page, YYYY-MM-DD)")
    if a.released:
        try:
            datetime.date.fromisoformat(a.released)
        except ValueError:
            raise SystemExit(f"--released takes a date as YYYY-MM-DD, not {a.released!r}")
    return out


# --------------------------------------------------------------------------- the check against CMS's own CSVs
def _money(s: str) -> int | None:
    s = s.replace("$", "").replace(",", "").strip()
    return int((Decimal(s) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if s else None


def _cms_codes(path: str) -> dict[str, dict]:
    """One of CMS's addendum CSVs read again for the check, on purpose not with the reader the tables are built with: two
    readings of CMS's file have to agree. The asterisks are in the column after the code."""
    grid = rows(path)
    top = next((i for i, r in enumerate(grid) if r and r[0].strip() == "HCPCS Code"), None)
    if top is None:
        raise SystemExit(f"no 'HCPCS Code' header in {os.path.basename(path)!r}")
    names = [" ".join(h.split()).lower() for h in grid[top]]
    at = lambda *words: next((i for i, h in enumerate(names) if all(w in h for w in words)), None)
    i_pi, i_rate, i_mpd, i_ci, i_ira = at("payment indicator"), at("payment rate"), at("multiple procedure"), at("comment indicator"), at("adjusted copayment")
    out: dict[str, dict] = {}
    for r in grid[top + 1:]:
        code = r[0].strip().upper() if r else ""
        if len(code) != 5 or not code.isascii() or not code.isalnum() or code in out:
            continue
        get = lambda i: r[i].strip() if i is not None and i < len(r) else ""
        out[code] = {"pi": get(i_pi).upper(), "rate": _money(get(i_rate)), "mpd": get(i_mpd).upper()[:1], "ci": get(i_ci).upper(),
                     "ira": _money(get(i_ira)), "waived": get(1) == "**"}
    return out


def check_against_cms(out_py: str, data_dir: str, cms: dict) -> list[str]:
    """Every difference between the tables as written (read back from disk) and CMS's own CSVs, one line for each code,
    county or definition that differs; empty when they are equal. Compared: per year, the codes listed and each code's addendum, payment indicator, rate, copayment, discounting
    and comment indicator; every county's state, name, CBSA and wage index; the indicator definitions; the APCs Addendum
    FF gives; and ascfs.py against the tables it was written with."""
    diffs: list[str] = []
    with open(os.path.join(data_dir, "asc_rates.csv"), newline="", encoding="utf-8") as fh:
        table = {r["code"]: r for r in csv.DictReader(fh)}
    with open(os.path.join(data_dir, "asc_meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    num = lambda s: int(s) if s != "" else None
    for y, (aa_path, bb_path) in sorted(cms["years"].items()):
        if f"pi_{y}" not in next(iter(table.values()), {}):
            diffs.append(f"{y}: the tables have no columns for this year")
            continue
        aa, bb = _cms_codes(aa_path), _cms_codes(bb_path)
        for code in sorted(set(aa) | set(bb)):
            add = "AA" if code in aa else "BB"
            c, t = (aa if add == "AA" else bb)[code], table.get(code)
            if t is None or not (t[f"pi_{y}"] or t[f"rate_{y}"] or t[f"add_{y}"]):
                diffs.append(f"{y} {code}: on CMS's Addendum {add} ({c['pi']}), not in the tables")
                continue
            rate = c["rate"]
            copay = None if rate is None else 0 if c["waived"] else c["ira"] if c["ira"] is not None else \
                int((Decimal(rate) / 5).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            want = {"add": add, "pi": c["pi"], "rate": rate, "copay": copay, "mpd": c["mpd"] if add == "AA" else "", "ci": c["ci"]}
            have = {"add": t[f"add_{y}"], "pi": t[f"pi_{y}"], "rate": num(t[f"rate_{y}"]), "copay": num(t[f"copay_{y}"]),
                    "mpd": t[f"mpd_{y}"], "ci": t[f"ci_{y}"]}
            bad = [f"{k} {have[k]!r} (CMS: {want[k]!r})" for k in want if want[k] != have[k]]
            if bad:
                diffs.append(f"{y} {code}: " + ", ".join(bad))
        for code, t in sorted(table.items()):
            if (t[f"pi_{y}"] or t[f"rate_{y}"] or t[f"add_{y}"]) and code not in aa and code not in bb:
                diffs.append(f"{y} {code}: in the tables ({t[f'pi_{y}']}), not on CMS's addenda")
    if cms.get("counties"):
        with open(os.path.join(data_dir, "asc_counties.csv"), newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            wcol = next(c for c in rd.fieldnames if c.startswith("wi_"))
            places = list(rd)
        grid = rows(cms["counties"])
        top = next((i for i, r in enumerate(grid) if r and r[0].strip() == "FIPS Code"), None)
        names = [" ".join(h.split()).lower() for h in grid[top]] if top is not None else []
        at = lambda pat: next((i for i, h in enumerate(names) if re.fullmatch(pat, h)), None)
        i_county, i_state, i_cbsa, i_wi = at(r"county name"), at(r"state"), at(r"final cy \d{4} cbsa"), at(r"final cy \d{4} asc wage index.*")
        if None in (i_county, i_state, i_cbsa, i_wi):
            raise SystemExit(f"{os.path.basename(cms['counties'])!r}: not CMS's county crosswalk (headers {names})")
        if wcol != "wi_" + re.search(r"\d{4}", names[i_wi]).group(0):
            diffs.append(f"counties: the tables hold {wcol}, CMS's crosswalk is for {names[i_wi]!r}")
        by_fips = {p["fips"]: p for p in places if p["fips"]}
        seen, statewide, with_county = set(), {}, set()
        for r in grid[top + 1:]:
            if len(r) <= max(i_county, i_state, i_cbsa, i_wi) or not r[0].strip().isdigit() or not r[i_state].strip():
                continue
            fips, state, county = r[0].strip().zfill(5), r[i_state].strip().upper(), " ".join(r[i_county].split()).upper()
            if county == "STATEWIDE":
                statewide[state] = (r[i_cbsa].strip(), r[i_wi].strip())
                continue
            with_county.add(state)
            seen.add(fips)
            p = by_fips.get(fips)
            if p is None:
                diffs.append(f"county {fips} {county}, {state}: in CMS's crosswalk, not in the tables")
                continue
            want = {"state": state, "county": county, "cbsa": r[i_cbsa].strip(), "wage index": Decimal(r[i_wi].strip() or "NaN")}
            have = {"state": p["state"], "county": p["county"], "cbsa": p["cbsa"], "wage index": Decimal(p[wcol])}
            bad = [f"{k} {have[k]} (CMS: {want[k]})" for k in want if want[k] != have[k]]
            if bad:
                diffs.append(f"county {fips} {county}, {state}: " + ", ".join(bad))
        for fips, p in sorted(by_fips.items()):
            if fips not in seen:
                diffs.append(f"county {fips} {p['county']}, {p['state']}: in the tables, not in CMS's crosswalk")
        # a state with no county of its own (Guam, the Virgin Islands): its one area, by the state's name
        for p in places:
            if not p["fips"]:
                cbsa, wi = statewide.get(p["state"], ("", ""))
                if p["state"] in with_county or not wi or (p["cbsa"], Decimal(p[wcol])) != (cbsa, Decimal(wi)):
                    diffs.append(f"{p['state']} {p['county']}: the tables have CBSA {p['cbsa']} at {p[wcol]}, CMS has {cbsa or 'no such area'} {wi}")
        for state, (cbsa, wi) in sorted(statewide.items()):
            if state not in with_county and wi and not any(p["state"] == state for p in places):
                diffs.append(f"{state}: CMS's crosswalk has only a statewide area ({cbsa}), not in the tables")
    if cms.get("dd1"):
        want = read_definitions(rows(cms["dd1"]), os.path.basename(cms["dd1"]))
        for k in sorted(set(want) | set(meta.get("pi", {}))):
            if want.get(k) != meta.get("pi", {}).get(k):
                diffs.append(f"indicator {k}: the tables say {meta.get('pi', {}).get(k)!r}, CMS's DD1 says {want.get(k)!r}")
    if cms.get("ff"):
        for code, apc in sorted(read_apc(rows(cms["ff"]), os.path.basename(cms["ff"])).items()):
            if code in table and table[code]["apc"] != apc:
                diffs.append(f"{code} APC: the tables have {table[code]['apc']!r}, CMS's Addendum FF has {apc!r}")
    # ascfs.py is written from the same reading: every one of its codes as the tables have it, and the offices' wage index
    # the counties'
    mod = runpy.run_path(out_py)
    for y, codes in sorted(mod["ASC"].items()):
        for code, (pi, rate, mpd) in sorted(codes.items()):
            t = table.get(code, {})
            if (t.get(f"pi_{y}"), num(t.get(f"rate_{y}", "")), t.get(f"mpd_{y}") == "Y") != (pi, rate, mpd):
                diffs.append(f"ascfs.py {y} {code}: {(pi, rate, mpd)}, the tables have {(t.get(f'pi_{y}'), t.get(f'rate_{y}'), t.get(f'mpd_{y}'))}")
    if set(mod["ASC"]) != {meta["prior_year"], meta["rule_year"]}:
        diffs.append(f"ascfs.py holds {sorted(mod['ASC'])}, the tables {meta['prior_year']} and {meta['rule_year']}")
    return diffs


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
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _day(iso: str) -> str:
    d = datetime.date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def source_of(wb: dict, cms: dict, checked: bool) -> str:
    """What the page says the rates are from: CMS's addenda and the day CMS posted them; ASCA's workbook where the tables
    were read from it, and whether it was held against CMS's own files."""
    rule = _label(wb["year"], wb["releases"][wb["year"]]).removeprefix("the ")   # 'CY 2027 final rule'
    posted = f"as posted on {_day(cms['released'])}" if cms.get("released") else ""
    if not wb.get("file"):
        return f"CMS's {rule} addenda {posted}".strip()
    if checked:
        return f"ASCA's rate calculator, {wb['file']}, equal to CMS's {rule} addenda {posted}".strip()
    return f"ASCA's rate calculator, {wb['file']}, not compared with CMS's own files"


def write_data(data_dir: str, wb: dict, wage: dict, cms: dict | None = None, checked: bool = False) -> dict:
    """The rate lookup's tables: one row per code (every code on either year's addenda), one per county, and the rest."""
    os.makedirs(data_dir, exist_ok=True)
    cms = cms or {}
    years = sorted(wb["tables"])
    fields = ("pi", "rate", "copay", "mpd", "ci", "add")
    codes = sorted(set().union(*(set(t) for t in wb["tables"].values())))
    with open(os.path.join(data_dir, "asc_rates.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["code", "apc"] + [f"{f}_{y}" for y in years for f in fields])
        for c in codes:
            row = [c, wb["apc"].get(c, "")]
            for y in years:
                r = wb["tables"][y].get(c)
                row += ["" if r is None or r[f] is None else r[f] for f in fields]
            w.writerow(row)
    with open(os.path.join(data_dir, "asc_counties.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["state", "county", "fips", "cbsa", "cbsa_name", f"wi_{wb['wage_year']}"])
        for row in sorted(wb["counties"], key=lambda r: (r[0], r[1])):
            w.writerow(row[:5] + (f"{row[5]:.4f}",))
    meta = {
        # what the rates are from, as the page says it; the day CMS posted the rule year's addenda; the files read
        "source": source_of(wb, cms, checked),
        "cms_release": cms.get("released"),
        "checked_against_cms": bool(checked),
        "files": {y: list(v) for y, v in sorted(wb["files"].items())},
        "rule_year": wb["year"], "prior_year": wb["prior"], "wage_year": wb["wage_year"],
        # how the page names each year's rates: 'October 2026', 'the CY 2027 final rule'
        "years": {y: _label(y, wb["releases"][y]) for y in (wb["year"], wb["prior"])},
        "labor_share": LABOR_SHARE, "coinsurance": 0.2, "sequestration": 0.02,
        # the calculator's own list, and what CMS's rules add to it (skin substitute supplies)
        "not_wage_adjusted": sorted(set(wb["no_wage"]) | set(CMS_NOT_WAGE_ADJUSTED)),
        "pi": wb["pi"],
        "states": dict(wb["states"]),
        # the offices' counties and every year's wage index for them
        "offices": {o: {"fips": f, "wage_index": {y: wage[y][o] for y in sorted(wage) if o in wage.get(y, {})}} for o, f in OFFICES.items()},
    }
    with open(os.path.join(data_dir, "asc_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1, sort_keys=True)
        fh.write("\n")
    with_apc = sum(1 for c, r in wb["tables"][wb["year"]].items() if wb["apc"].get(c))
    return {"codes": len(codes), "counties": len(wb["counties"]), "years": years, "with an APC": with_apc}


# --------------------------------------------------------------------------- the build
def build(wb: dict, wage: dict, cms: dict, out: str, data_dir: str, unchecked: bool = False) -> dict:
    """Write ascfs.py and the tables from one reading (`wb`: ASCA's workbook or CMS's CSVs), compare them with CMS's
    own CSVs, and only then put them in place."""
    if wb["wage_year"] != wb["year"]:
        raise SystemExit(f"the wage index is for {wb['wage_year']}, the addenda for {wb['year']}")
    by_fips = {c[2]: c for c in wb["counties"] if c[2]}
    missing = [o for o, f in OFFICES.items() if f not in by_fips]
    if missing:
        raise SystemExit(f"offices not in the crosswalk: {missing}")
    wage = {y: dict(v) for y, v in wage.items()}
    wage[wb["wage_year"]] = {o: by_fips[f][5] for o, f in OFFICES.items()}
    # the year before: CMS's crosswalk carries each county's; --wage where it is not at hand, and the two have to agree
    known = wb.get("prior_wage")
    if known is None and cms.get("counties"):
        _, prior_wi, _ = read_crosswalk(cms["counties"])
        known = {o: prior_wi[f] for o, f in OFFICES.items() if f in prior_wi} or None
    given = wage.get(wb["prior"])
    if known and given and any(abs(given.get(o, -1) - v) > 1e-9 for o, v in known.items()):
        raise SystemExit(f"--wage {wb['prior']} gives {given}, CMS's crosswalk has {known} for that year")
    if known and not given:
        wage[wb["prior"]] = known
    if wb["prior"] not in wage or set(wage[wb["prior"]]) != set(OFFICES):
        raise SystemExit(f"no wage index for {wb['prior']}: give CMS's crosswalk with --counties, or --wage {wb['prior']}:office=index for {sorted(OFFICES)}")
    if cms.get("ff"):                    # a surgical procedure's APC is Addendum FF's wherever CMS gives one there
        wb = {**wb, "apc": {**wb["apc"], **read_apc(rows(cms["ff"]), os.path.basename(cms["ff"]))}}
    years: dict[str, dict] = {}
    listed = {c for t in wb["tables"].values() for c, r in t.items() if r["add"] == "AA" and in_ranges(c)}
    for y, t in wb["tables"].items():
        aa = {c: (r["pi"], r["rate"], r["mpd"] == "Y") for c, r in t.items() if r["add"] == "AA" and in_ranges(c)}
        posted = [f"as CMS posted them on {cms['released']}"] if cms.get("released") and y == wb["year"] else []
        # not on the year's list, though on the other year's: not payable in an ASC that year
        years[y] = {"aa": aa, "ee": sorted(listed - set(aa)), "files": list(wb["files"][y]) + posted}
    names = {o: f"{by_fips[f][1].title()} County {by_fips[f][0]}, CBSA {by_fips[f][3]} {by_fips[f][4]}" for o, f in OFFICES.items()}
    note = "; ".join(f"{o.title()} ({names[o]})" for o in OFFICES)
    wage = {y: {o: wage[y][o] for o in OFFICES} for y in sorted(wage)}
    check = bool(cms.get("years")) and not unchecked
    tmp = tempfile.mkdtemp(prefix="build_ascfs-")
    try:
        t_py, t_dir = os.path.join(tmp, "ascfs.py"), os.path.join(tmp, "cms")
        write_ascfs(t_py, years, wage, {y: v["files"] for y, v in sorted(years.items())}, note, dict(OFFICES))
        stats = write_data(t_dir, wb, wage, cms, check)
        if check:
            diffs = check_against_cms(t_py, t_dir, cms)
            if diffs:
                shown = "\n".join("  " + d for d in diffs[:40])
                more = f"\n  and {len(diffs) - 40} more" if len(diffs) > 40 else ""
                hint = "\nASCA's workbook is not CMS's latest posting: build from CMS's files alone (leave out --asca)." if wb.get("file") else ""
                raise SystemExit(f"The tables differ from CMS's own files in {len(diffs)} places; nothing was written.\n{shown}{more}{hint}")
        os.makedirs(data_dir, exist_ok=True)
        for name in TABLES:
            shutil.copyfile(os.path.join(t_dir, name), os.path.join(data_dir, name))
        shutil.copyfile(t_py, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {**stats, "ascfs": {y: len(v["aa"]) for y, v in years.items()}, "checked": check}


def build_aa(items: list[str], wage: dict, out: str, data_dir: str) -> dict:
    """The older way: ascfs.py alone, from CMS's Addendum AA (and EE) CSVs. The rate lookup's tables stay as they are, so
    the years have to be the ones they hold: the ASC view reads both."""
    years: dict[str, dict] = {}
    for item in items:
        year, paths = item.split("=", 1)
        parts = paths.split(",")
        aa = parse_aa(parts[0])
        ee = parse_ee(parts[1]) if len(parts) > 1 else set()
        years[year] = {"aa": aa, "ee": sorted(ee), "files": [os.path.basename(p) for p in parts]}
    meta_path = os.path.join(data_dir, "asc_meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        held = sorted({meta.get("prior_year"), meta.get("rule_year")} - {None})
        if sorted(years) != held:
            raise SystemExit(f"--aa would leave ascfs.py on {sorted(years)} and the rate lookup's tables in {data_dir} on {held}: "
                             f"build both from CMS's files (--cms)")
    lacking = [y for y in years if set(wage.get(y, {})) != set(OFFICES)]
    if lacking:
        raise SystemExit(f"--aa needs --wage YEAR:office=index for {sorted(OFFICES)} in {sorted(lacking)}")
    note = "Newburgh (CBSA 21780 Evansville IN), Owensboro (36980), Henderson"
    write_ascfs(out, years, wage, {y: v["files"] for y, v in years.items()}, note, dict(OFFICES))
    return {"ascfs": {y: len(v["aa"]) for y, v in years.items()}}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build the Medicare ASC payment tables from CMS's files.")
    ap.add_argument("--cms", action="append", default=[], help="YEAR=AA.csv,BB.csv: CMS's Addenda AA and BB (the rule year, and the year before)")
    ap.add_argument("--counties", help="CMS's ASC wage index county-to-CBSA crosswalk for the rule year (CSV)")
    ap.add_argument("--dd1", help="CMS's Addendum DD1 for the rule year (CSV)")
    ap.add_argument("--ff", help="CMS's Addendum FF for the rule year (CSV), for the APCs")
    ap.add_argument("--addendum-b", help="CMS's OPPS Addendum B for the rule year (CSV), for the APC of every other code")
    ap.add_argument("--released", help="the day CMS posted or last updated the rule year's addenda (YYYY-MM-DD)")
    ap.add_argument("--asca", help="ASCA's Medicare ASC final rule rate calculator workbook (.xlsx), compared with --cms")
    ap.add_argument("--unchecked", action="store_true", help="with --asca: do not compare with CMS's own files")
    ap.add_argument("--aa", action="append", default=[], help="YEAR=AA.csv[,EE.csv] (CMS's addenda CSVs, ascfs.py only)")
    ap.add_argument("--wage", action="append", default=[], help="YEAR:office=index,office=index")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "remitwatch", "ascfs.py"))
    ap.add_argument("--data-dir", default=os.path.join(HERE, "..", "remitwatch", "cms"))
    a = ap.parse_args(argv)
    wage: dict[str, dict] = {}
    for item in a.wage:
        year, pairs = item.split(":", 1)
        wage[year] = {k: float(v) for k, v in (p.split("=") for p in pairs.split(","))}
    if a.aa:
        if a.asca or a.cms:
            ap.error("--aa is the older way, on its own: leave out --asca and --cms")
        print("wrote", a.out, build_aa(a.aa, wage, a.out, a.data_dir)["ascfs"])
        return
    if not a.asca and not a.cms:
        ap.error("give CMS's files (--cms, --counties, --dd1), ASCA's workbook (--asca) with them, or --aa")
    cms = cms_files(a)
    if a.asca:
        if not a.unchecked and (len(cms["years"]) != 2 or not cms["counties"]):
            raise SystemExit("--asca is compared with CMS's own files: give --cms for both years, --counties and --released "
                             "(or --unchecked to skip the comparison)")
        wb = read_workbook(a.asca)
        if cms["years"] and sorted(cms["years"]) != sorted(wb["tables"]):
            raise SystemExit(f"--cms is for {sorted(cms['years'])}, the workbook for {sorted(wb['tables'])}")
    else:
        if a.unchecked:
            ap.error("--unchecked goes with --asca")
        wb = read_cms(cms, a.data_dir)
    stats = build(wb, wage, cms, a.out, a.data_dir, a.unchecked)
    print("wrote", a.out, stats["ascfs"], "and", a.data_dir, {k: stats[k] for k in ("codes", "counties", "years", "with an APC")},
          "equal to CMS's own files" if stats["checked"] else "NOT compared with CMS's own files")


if __name__ == "__main__":
    main()
