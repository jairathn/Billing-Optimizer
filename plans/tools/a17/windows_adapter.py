"""Turn the researched payer window table (rows keyed by payer group, each window with its source) into the auditor's
window table (schema a17-payer-windows/1, see claim_audit_a17.py: PayerWindows).

The researched table names payers by group ("rows": [{"groups": [...], "kinds": [...], "window": "first_filing" |
"corrected_claim" | "appeal_1 ..." | "appeal_2 ..." | combinations, "days" | "months", "counted_from", "receipt_days",
"source_title", "section", "url", "verified"}, ...]). The groups' payer names (as the 835s spell them) and claim filing
indicators come from a second file ([{"group", "payer_names": [...], "cfi": {CLP06: lines}}, ...]). Each auditor entry
matches a payer by its exact name and, when the group is one product (its claim filing indicators), by product.

Kept: verified rows with a source and a usable starting point. A window counted from the remittance (or the notice the
remittance carries) starts at the remittance; one counted from a decision starts at the decision; a presumed mail time is
added to the days. Rows counted from an unknown point, or not verified, are left out (the auditor then keeps A16's window
for that payer and kind) and counted on stderr.

The output can name payers: it belongs in the private folder, never in the repository.
Usage: python3 -I -B windows_adapter.py RESEARCHED.json PAYER_GROUPS.json OUT.json"""
import json
import os
import re
import sys
from collections import Counter

SCHEMA = "a17-payer-windows/1"
CLASS_OF_CFI = {"MA": "MCR", "MB": "MCR", "16": "MAP", "MC": "MCD", "CH": "GOV", "VA": "GOV", "OF": "GOV", "WC": "GOV"}


def norm_name(n):
    w = re.sub(r"[^A-Z0-9]+", " ", (n or "").upper()).split()
    while w and w[-1] in {"INC", "LLC", "CO", "COMPANY", "CORP", "CORPORATION", "THE", "LTD"}:
        w.pop()
    return " ".join(w)


def kinds_of(window):
    """The auditor's window kinds a researched window names: [("first_filing" | "corrected_claim" | "appeal", level)]."""
    w = (window or "").lower()
    out = []
    if w.startswith("first_filing") or " first_filing" in w:
        out.append(("first_filing", 0))
    if "corrected_claim" in w:
        out.append(("corrected_claim", 0))
    if "appeal_1" in w:
        out.append(("appeal", 1))
    if "appeal_2" in w:
        out.append(("appeal", 2))
    return out


def anchor_of(counted_from):
    c = (counted_from or "").lower()
    if c.startswith("date_of_service") or c.startswith("date_of_loss"):
        return "dos"
    if "decision" in c or "redetermination" in c:
        return "decision"
    if any(k in c for k in ("remittance", "initial_determination", "eob", "pra", "notice", "denial", "payment")):
        return "remit"
    return None


def window_of(row):
    frm = anchor_of(row.get("counted_from"))
    if frm is None:
        return None, "unknown starting point"
    if not row.get("verified"):
        return None, "not verified"
    src = "; ".join(x for x in (row.get("source_title"), row.get("section"), row.get("url"), row.get("revision")) if x)
    if not src:
        return None, "no source"
    rd = int(row.get("receipt_days") or 0)
    if row.get("days") is not None:
        return {"days": int(row["days"]) + rd, "from": frm, "source": src}, None
    if row.get("months") is not None:
        if rd:
            return None, "months with a mail time"
        return {"months": int(row["months"]), "from": frm, "source": src}, None
    return None, "no length"


def main(researched, groups_path, out):
    table = json.load(open(researched))
    groups = {g["group"]: g for g in json.load(open(groups_path))}
    entries = {}
    skipped = Counter()
    for row in table.get("rows") or []:
        kinds = kinds_of(row.get("window"))
        if not kinds:
            skipped["window kind not used by the auditor"] += 1
            continue
        w, why = window_of(row)
        if w is None:
            skipped[why] += 1
            continue
        for grp in row.get("groups") or []:
            g = groups.get(grp)
            if not g or not g.get("payer_names"):
                skipped["group with no payer names"] += 1
                continue
            cfis = sorted(k for k in (g.get("cfi") or {}) if k)
            classes = sorted({CLASS_OF_CFI.get(k, "COM") for k in cfis})
            e = entries.setdefault(grp, {"id": re.sub(r"[^a-z0-9]+", "_", grp.lower()).strip("_"), "label": grp,
                                         "match": {"name_patterns": ["^" + re.escape(norm_name(n)) + "$"
                                                                     for n in g["payer_names"] if norm_name(n)],
                                                   "classes": classes if len(classes) == 1 else []},
                                         "appeals": []})
            for kind, level in kinds:
                if kind == "appeal":
                    if not any(a.get("level") == level for a in e["appeals"]):
                        e["appeals"].append(dict(w, level=level))
                elif kind not in e:
                    e[kind] = dict(w)
    out_t = {"schema": SCHEMA, "version": "adapted from the researched table built " + str(table.get("built")),
             "payers": sorted(entries.values(), key=lambda e: e["id"])}
    with open(out, "w") as fh:
        json.dump(out_t, fh, indent=1)
    os.chmod(out, 0o600)
    print(f"entries: {len(entries)}; rows left out: {dict(skipped)}", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:4])
