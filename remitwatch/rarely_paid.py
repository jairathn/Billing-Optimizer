"""
Codes a payer almost never pays (owner rule 4.2; the owner's decision 4 of 2026-11-26: "Keep pursuing them and find the
reason they aren't paying, so that we can adapt our notes to get them paid if possible").

Two parts, kept apart on purpose:
  - the payer's published rule for a payer group and code, researched by hand with every source fetched twice:
    remitwatch/data/payer_code_rules.json. Public facts only: the verdict (note, claim, never_payable, unclear), the
    rule, what the note or the claim must show, and the sources with the sentence quoted. Never a count, a dollar or
    anything of the practice's (tests/test_payer_code_rules.py holds it to that);
  - the evidence from the store, worked out by each detection run on the store it analyses, on both hosts
    (detect.Detector.explain_rarely_paid, meta rarely_paid:why): how many lines the payer answered as the primary payer
    and paid, by year of service; the reasons and remarks on the unpaid ones; their modifiers and places of service; the
    codes billed on the same claims; how often the payer paid another line of the same claim; the code it answered
    instead. Counts, codes and years only: no patient, claim or check is named in it.

Nothing here decides an item's state or closes it (states.py does; the owner's policy page folds a pattern only when the
owner accepts it). A never-payable verdict says so plainly and the item stays pursued, on Work now, until the owner
accepts the pattern on the write-off policy page.

  rules() -> the data file, read once
  entry_for(group, code, service_date) -> the researched entry for this payer group and code on that date, resolved
      (its verdict, rule, what to show and sources for the date), or None
  pattern_key(group, code, service_date) -> the owner's key for the pair, 'group|code||payer_policy', with the research's
      period in the modifier's place where the rule is bound to dates ('group|code|from 2026-01-01|payer_policy')
  item_codes(f) -> the (code, service date) pairs an item is about that can be such a pair: the code the practice billed
      on an unpaid denial line (a code the payer changed and paid nothing on included)
  evidence(lines, ...) -> the store's evidence for the pairs a run asks for (detect.py)
  why(conn, f) -> the item drawer's blocks: "Why <payer> does not pay <code>"
  checklist(f, lines) / enclosures(f, lines) -> what the appeal packet adds (checked before sending / sent with it)
  review_input(f, lines) -> what the note review is told about the payer's rule
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Any, Iterable

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "payer_code_rules.json")
WHY_META = "rarely_paid:why"            # meta: the run's evidence per pattern key (counts, codes and years only)
SINCE = "2025-01-01"                    # the window the payer-policy rule counts (ema_checks.POLICY_SINCE)
MIN_ANSWERED = 20                       # ema_checks.POLICY_MIN_ANSWERED: fewer answered lines tell nothing
MAX_PAID_SHARE = 0.05                   # ema_checks.POLICY_MAX_PAID_SHARE
MAX_PAIRS = 400                         # a run keeps the evidence of this many pairs at most (the biggest first)
VERDICTS = ("note", "claim", "never_payable", "unclear")
VERDICT_WORDS = {
    "never_payable": "Not payable under the payer's published rule",
    "claim": "Fixable on the claim",
    "note": "Fixable by what the note shows",
    "unclear": "No published rule found that explains it",
}
CONDITIONS = ("em_with_modifier_25",)
EM_VISITS = frozenset(f"992{n:02d}" for n in (2, 3, 4, 5, 11, 12, 13, 14, 15))
# signals whose line is no denial of the code by this payer: a claim sent on to another carrier, a second payer never
# billed
SKIP_SIGNALS = frozenset({"FORWARDED_TO_CARRIER", "SECONDARY_NOT_BILLED"})
DENIAL_CATEGORIES = ("denial", "bundling")


# ------------------------------------------------------------------------------------------------------ the data file
@lru_cache(maxsize=1)
def rules() -> dict:
    """The researched entries, read once a process. A file that is missing or cannot be read gives no entries (and says
    so in the run's log): the evidence from the store still shows, with no rule beside it."""
    try:
        with open(DATA_FILE, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict) or not isinstance(d.get("rules"), list) or not isinstance(d.get("sources"), dict):
            raise ValueError("payer_code_rules.json has an unexpected shape")
        return d
    except (OSError, ValueError) as exc:
        from . import runlog
        runlog.caught(exc, "rarely_paid.rules")
        return {"rules": [], "sources": {}, "version": None}


@lru_cache(maxsize=1)
def _by_code() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for e in rules().get("rules") or []:
        if isinstance(e, dict) and e.get("code") and e.get("payer_group"):
            out[str(e["code"]).strip().upper()].append(e)
    return dict(out)


def version() -> str | None:
    return rules().get("version")


def _family(group: Any) -> str:
    from .ema_checks import _family as fam      # one rule for the two vocabularies of payer groups (the 835s', EMA's)
    return fam(group)


def _group_rank(e: dict, group: str) -> int | None:
    """0 when the entry names the group, 1 when it lists it as another spelling, 2 when the two are one company's plans
    of one kind (ema_checks._family: Medicare Advantage and Medicaid products), None otherwise."""
    if e.get("payer_group") == group:
        return 0
    aliases = [a for a in e.get("payer_group_aliases") or [] if isinstance(a, str)]
    if group in aliases:
        return 1
    fam = _family(group)
    if fam and fam[:1] != "=" and any(_family(g) == fam for g in [e.get("payer_group")] + aliases):
        return 2
    return None


def _in(day: str | None, frm: str | None, to: str | None) -> bool:
    d = str(day or "")[:10]
    return bool(d) and (not frm or d >= frm) and (not to or d <= to)


def _sources(e: dict) -> list[dict]:
    known = rules().get("sources") or {}
    out = []
    for s in e.get("sources") or []:
        src = known.get(s.get("source")) if isinstance(s, dict) else None
        if not isinstance(src, dict):
            continue
        out.append({"title": src.get("title"), "publisher": src.get("publisher"), "url": src.get("url"), "date": src.get("date"),
                    "quote": s.get("quote"), "checked_twice": bool(src.get("checked_twice"))})
    return out


def _resolve(e: dict, service_date: str | None) -> dict | None:
    """The entry as it stands on that date: its own verdict, or the one of its period where the rule changed on a date;
    None when the date is outside the entry (or no date is known for an entry bound to dates)."""
    split = False
    if e.get("service_dates"):
        sd = e["service_dates"]
        if not _in(service_date, sd.get("from"), sd.get("to")):
            return None
        period, verdict, rule, show = {"from": sd.get("from"), "to": sd.get("to")}, e.get("verdict"), e.get("rule"), e.get("what_to_show")
    elif e.get("by_service_date"):
        sub = next((s for s in e["by_service_date"] if isinstance(s, dict) and _in(service_date, s.get("from"), s.get("to"))), None)
        if sub is None:
            return None
        split = True
        period, verdict, rule, show = {"from": sub.get("from"), "to": sub.get("to")}, sub.get("verdict"), sub.get("rule"), sub.get("what_to_show")
    else:
        period, verdict, rule, show = None, e.get("verdict"), e.get("rule"), e.get("what_to_show")
    if verdict not in VERDICTS:
        return None
    return {"payer_group": e.get("payer_group"), "aliases": list(e.get("payer_group_aliases") or []), "code": e.get("code"),
            "verdict": verdict, "rule": rule, "what_to_show": show, "period": period, "split": split,
            "applies_when": e.get("applies_when") if e.get("applies_when") in CONDITIONS else None,
            "sources": _sources(e), "researched": version()}


MATCHES = ("exact", "alias", "family")


def entry_for(group: str | None, code: str | None, service_date: str | None = None) -> dict | None:
    """The researched entry for this payer group and code on that date: the entry that names the group first, then one
    that lists it as another spelling, then one of the same company's plans of the same kind ('match' says which). A copy
    each call. Only an exact one stands on its own (an exact one of the Medicaid catch-all aside): the others apply where
    the run found the pair rarely paid too (applies), since a state's Medicaid program, say, pays most codes when the
    claim is right."""
    if not group or not code:
        return None
    best, rank = None, 9
    for e in _by_code().get(str(code).strip().upper(), ()):
        r = _group_rank(e, group)
        if r is None or r >= rank:
            continue
        got = _resolve(e, service_date)
        if got is not None:
            best, rank = dict(got, match=MATCHES[r]), r
    return best


def applies(entry: dict | None, rare: bool) -> bool:
    """Whether a researched entry speaks for an item: always when it names the item's payer group, else only for a pair
    the run found rarely paid (5% or less of at least 20 answered services). The Medicaid catch-all ('Medicaid (other)',
    states.MEDICAID_CATCH_ALL) holds many payers at once (a state's program under a spelling the name rules do not
    place, the companies' Medicaid plans), so an entry that names it speaks only for a pair the run found rarely paid,
    as another spelling's does: a code those payers pay in the end is never shown as one they do not pay, nor put in
    the owner's table where an Accept would write its services off (review fx-ema-r1-5)."""
    if not entry:
        return False
    from .states import MEDICAID_CATCH_ALL
    if entry.get("match") == "exact" and entry.get("payer_group") != MEDICAID_CATCH_ALL:
        return True
    return bool(rare)


def is_rare(paid: Any, answered: Any) -> bool:
    try:
        p, n = int(paid or 0), int(answered or 0)
    except (TypeError, ValueError):
        return False
    return n >= MIN_ANSWERED and p <= MAX_PAID_SHARE * n


def period_words(period: dict | None) -> str:
    """'from 2026-01-01', 'to 2025-12-31', '2025-01-01 to 2025-12-31', or '' for no period."""
    p = period or {}
    frm, to = p.get("from"), p.get("to")
    if frm and to:
        return f"{frm} to {to}"
    return f"from {frm}" if frm else (f"to {to}" if to else "")


def pattern_key(group: str | None, code: str | None, service_date: str | None = None) -> str:
    """The owner's key for a code a payer almost never pays. Where the research binds the rule to dates (a rule that
    changed, such as CPT 2026 for 77600, or a code that ended), the key names the period, so the owner accepts exactly
    what no note can change and keeps pursuing the rest (Anthem paid 77600 in 2025: those services are not the 2026
    pattern)."""
    e = entry_for(group, code, service_date)
    period = period_words(e["period"]) if e and e.get("period") else ""
    return f"{group or ''}|{code or ''}|{period}|payer_policy"


def parse_key(key: str) -> dict:
    """A pattern key's parts: group, code and the period ({'from', 'to'}) its modifier's place holds, if any."""
    parts = (str(key or "").split("|") + ["", "", "", ""])[:4]
    period: dict = {}
    m = re.fullmatch(r"(?:(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})|from (\d{4}-\d{2}-\d{2})|to (\d{4}-\d{2}-\d{2}))", parts[2] or "")
    if m:
        period = {"from": m.group(1) or m.group(3), "to": m.group(2) or m.group(4)}
    return {"group": parts[0], "code": parts[1], "period": period or None, "reason": parts[3]}


def whole_key(key: str) -> str | None:
    """The key of the same payer and code at every date ('group|code||payer_policy', the only payer-policy key before the
    owner's decision 4 split a pair by period) for a key that names a period; None for any other key."""
    p = parse_key(key)
    if p["reason"] != "payer_policy" or not p["period"]:
        return None
    return f"{p['group']}|{p['code']}||payer_policy"


def decision_for(decisions: dict | None, key: str) -> dict | None:
    """The owner's decision for a payer-policy pattern, or None when the owner never decided it. A key that names a period
    also reads the decision made for the same payer and code at every date (the only key before decision 4: an owner's
    accept or pursue made then covers every period) and the newer of the two wins, so that decision is never overwritten
    by the change of key and a later decision of either row takes effect. The one read is marked 'from_key'."""
    pol = decisions if isinstance(decisions, dict) else {}
    own = pol.get(key)
    own = dict(own, from_key=key) if isinstance(own, dict) and own.get("decision") else None
    whole = whole_key(key)
    old = pol.get(whole) if whole else None
    old = dict(old, from_key=whole) if isinstance(old, dict) and old.get("decision") else None
    if own and old:
        return old if str(old.get("at") or "") > str(own.get("at") or "") else own
    return own or old


# ------------------------------------------------------------------------------------------------- which items count
def _ev(f: dict) -> dict:
    ev = f.get("evidence")
    if isinstance(ev, dict):
        return ev
    try:
        got = json.loads(ev or "{}")
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def billed_code(f: dict) -> str | None:
    """The code the practice billed: the original of a code the payer changed, else the line's code."""
    if f.get("signal") == "CODE_CHANGED" and f.get("original_procedure_code"):
        return f["original_procedure_code"]
    return f.get("procedure_code")


def item_codes(f: dict) -> list[tuple[str, str | None]]:
    """(code, service date) of each service an item is about that can be a code its payer almost never pays: the billed
    code of a denial line that paid nothing (a denial, a bundling, or a code the payer changed and paid nothing on), each
    line of a denial only EMA saw, each unpaid code of a whole-claim denial (up to six), and G2211 for the payer's G2211
    denials. Paid lines, short payments and claims no payer answered are not."""
    sig = f.get("signal") or ""
    if sig in SKIP_SIGNALS:
        return []
    out: list[tuple[str, str | None]] = []

    def add(code: Any, day: Any) -> None:
        c = str(code or "").strip().upper()
        if c and (c, day) not in out and len(out) < 6:
            out.append((c, str(day)[:10] if day else None))
    if sig == "G2211_DENIED":
        add("G2211", f.get("service_date"))
        return out
    if f.get("line_key"):
        if (f.get("paid_cents") or 0) > 0:
            return []
        if f.get("category") in DENIAL_CATEGORIES or sig == "CODE_CHANGED":
            add(billed_code(f), f.get("service_date"))
        return out
    if sig == "EMA_DENIED":
        for x in _ev(f).get("lines") or []:
            if isinstance(x, list) and x:
                add(x[0], x[1] if len(x) > 1 else f.get("service_date"))
        return out
    if f.get("claim_key") and f.get("category") in DENIAL_CATEGORIES:
        ev = _ev(f)
        for p in ev.get("unpaid") or []:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                add(p[0], p[1] or f.get("service_date"))
        if not out:
            for c in ev.get("codes") or []:
                add(c, f.get("service_date"))
        if not out:
            add(f.get("procedure_code"), f.get("service_date"))
    return out


# --------------------------------------------------------------------------------------------- the store's evidence
def spec_for(group: str, code: str, entry: dict | None, period: dict | None = None) -> dict:
    """What the evidence of one pair counts: the payer groups (the item's, and the entry's own name and other spellings),
    the code, and the period (the entry's dates, else the payer-policy window from 2025)."""
    groups = {group}
    if entry:
        groups.add(entry.get("payer_group"))
        groups.update(entry.get("aliases") or [])
    p = period or (entry or {}).get("period") or {}
    frm = max(p.get("from") or SINCE, SINCE)
    return {"group": group, "code": code, "groups": sorted(g for g in groups if g), "family": _family(group),
            "from": frm, "to": p.get("to")}


def _top(c: Counter, n: int) -> list[list]:
    return [[k, v] for k, v in sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0])))[:n]]


def evidence(lines: Iterable[dict], *, adj: dict, rem: dict, claim_adj: dict, claim_rem: dict, standing: dict,
             by_claim: dict, wanted: dict[str, dict], conn: sqlite3.Connection | None = None) -> dict[str, dict]:
    """The store's evidence for each wanted pair (pattern key -> spec_for): the remittance lines of the code (the code the
    practice billed: a line the payer answered under another code counts for the code billed) that the payer group
    answered as the primary payer (claim status 1, 4 or 19) in the period, from 2025. A line counts as paid while a
    payment of it still stands. Fewer than MIN_ANSWERED lines under the groups' own names: the same company's plans of
    the same kind count too (said so: widened). With EMA's books in the store (ema_lines), what they show for the pair
    is kept beside it, for a payer whose remittances never reach the store."""
    if not wanted:
        return {}
    codes = {s["code"] for s in wanted.values()}
    by_code: dict[str, list[dict]] = defaultdict(list)
    for l in lines:
        if l.get("claim_status_code") not in ("1", "4", "19"):
            continue
        svc = l.get("service_date") or ""
        if svc < SINCE:
            continue
        c1, c0 = l.get("procedure_code"), l.get("original_procedure_code")
        billed = c0 if (c0 and c0 != c1) else c1
        if billed in codes:
            by_code[billed].append(l)
    out: dict[str, dict] = {}
    for key, s in wanted.items():
        frm, to = s.get("from") or SINCE, s.get("to")
        cand = [l for l in by_code.get(s["code"], ()) if _in(l.get("service_date"), frm, to)]
        groups = set(s.get("groups") or [])
        sel = [l for l in cand if l.get("payer_group") in groups]
        widened = False
        fam = s.get("family")
        if len(sel) < MIN_ANSWERED and fam and fam[:1] != "=":
            wide = [l for l in cand if l.get("payer_group") in groups or _family(l.get("payer_group")) == fam]
            if len(wide) > len(sel):
                sel, widened = wide, True
        ev = _from_lines(sel, s["code"], adj=adj, rem=rem, claim_adj=claim_adj, claim_rem=claim_rem, standing=standing, by_claim=by_claim)
        ev.update(code=s["code"], groups=sorted({l.get("payer_group") for l in sel} | groups) if widened else sorted(groups),
                  widened=widened, **{"from": frm, "to": to})
        if conn is not None:
            ema = _from_ema(conn, s, groups, frm, to)
            if ema:
                ev["ema"] = ema
        out[key] = ev
    return out


def _from_lines(sel: list[dict], code: str, *, adj: dict, rem: dict, claim_adj: dict, claim_rem: dict, standing: dict,
                by_claim: dict) -> dict:
    paid_lines = [l for l in sel if (standing.get(l["line_key"]) or 0) > 0]
    unpaid = [l for l in sel if not (standing.get(l["line_key"]) or 0) > 0]
    by_year: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for l in sel:
        y = (l.get("service_date") or "")[:4]
        by_year[y][1] += 1
        by_year[y][0] += 1 if (standing.get(l["line_key"]) or 0) > 0 else 0
    recoded: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for l in sel:
        if l.get("procedure_code") and l.get("procedure_code") != code:
            r = recoded[l["procedure_code"]]
            r[0] += 1
            r[1] += 1 if (standing.get(l["line_key"]) or 0) > 0 else 0
    reasons, remarks, mods_u, mods_p, pos_u, pos_p, with_codes = Counter(), Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    other_paid = 0
    for l in unpaid:
        own = {f"{g}-{rc}" for g, rc, a in adj.get(l["line_key"], ()) if g and rc and a}
        if not own:             # a whole-claim denial: the claim's own reasons
            own = {f"{g}-{rc}" for g, rc, a in claim_adj.get(l.get("claim_key"), ()) if g and rc and a}
        reasons.update(own)
        remarks.update(set(rem.get(l["line_key"], ())) | set(claim_rem.get(l.get("claim_key"), ())))
        ms = l.get("modifiers_list") if isinstance(l.get("modifiers_list"), list) else _mods(l.get("modifiers"))
        mods_u.update(ms or ["none"])
        pos_u[l.get("pos") or l.get("facility_type_code") or "not given"] += 1
        others = [o for o in by_claim.get(l.get("claim_key"), ()) if o.get("line_key") != l["line_key"]]
        with_codes.update({o.get("procedure_code") for o in others if o.get("procedure_code") and o.get("procedure_code") != code})
        if any((standing.get(o.get("line_key")) or 0) > 0 for o in others):
            other_paid += 1
    for l in paid_lines:
        ms = l.get("modifiers_list") if isinstance(l.get("modifiers_list"), list) else _mods(l.get("modifiers"))
        mods_p.update(ms or ["none"])
        pos_p[l.get("pos") or l.get("facility_type_code") or "not given"] += 1
    return {"answered": len(sel), "paid": len(paid_lines), "unpaid": len(unpaid),
            "by_year": {y: v for y, v in sorted(by_year.items()) if y},
            "recoded": {k: v for k, v in sorted(recoded.items(), key=lambda kv: (-kv[1][0], kv[0]))[:3]},
            "reasons": _top(reasons, 6), "remarks": _top(remarks, 6), "modifiers_unpaid": _top(mods_u, 4), "modifiers_paid": _top(mods_p, 4),
            "pos_unpaid": _top(pos_u, 3), "pos_paid": _top(pos_p, 3), "with_codes": _top(with_codes, 5), "other_line_paid": other_paid}


def _mods(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(m) for m in raw if m]
    try:
        got = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        got = [m for m in str(raw).split(",") if m]
    return [str(m) for m in got if m] if isinstance(got, list) else []


def _from_ema(conn: sqlite3.Connection, s: dict, groups: set, frm: str, to: str | None) -> dict | None:
    """What EMA's books show for the pair (its claim lines a payer answered, ema_checks.line_answered_sql): lines answered
    and paid, and the reasons posted on the unpaid ones. None without EMA's summaries in the store."""
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ema_lines', 'ema_line_reasons')")}
        if "ema_lines" not in have:
            return None
        from .ema_checks import line_answered_sql
        q = ",".join("?" * len(groups))
        args = [s["code"], frm] + ([to] if to else []) + sorted(groups)
        rows = conn.execute(f"""SELECT l.link, l.svc, COALESCE(l.ins_paid, 0) > 0 FROM ema_lines l
                                WHERE l.cpt = ? AND l.svc >= ? {'AND l.svc <= ?' if to else ''} AND l.payer_group IN ({q})
                                  AND ({line_answered_sql('l')} OR l.denial_first_posted IS NOT NULL)""", args).fetchall()
        if not rows:
            return None
        paid = sum(1 for r in rows if r[2])
        out: dict = {"answered": len(rows), "paid": paid}
        if "ema_line_reasons" in have:
            reasons: Counter = Counter()
            unpaid = {(r[0], r[1]) for r in rows if not r[2]}
            for link, svc, grp, code in conn.execute(
                    f"""SELECT z.link, z.svc, z.grp, z.code FROM ema_line_reasons z
                        WHERE z.cpt = ? AND z.svc >= ? {'AND z.svc <= ?' if to else ''} AND COALESCE(z.grp, '') <> ''""",
                    [s["code"], frm] + ([to] if to else [])):
                if (link, svc) in unpaid:
                    reasons[f"{grp}-{code}"] += 1
            out["reasons"] = _top(reasons, 6)
        return out
    except sqlite3.Error as exc:
        from . import runlog
        runlog.caught(exc, "rarely_paid.ema")
        return None


def save(conn: sqlite3.Connection, pairs: dict[str, dict], *, at: str | None, run_id: str | None) -> None:
    """The run's evidence, kept in meta (the dashboard's copy carries meta, so both hosts' panels read the same)."""
    keep = dict(sorted(pairs.items(), key=lambda kv: (-(kv[1].get("unpaid") or 0), kv[0]))[:MAX_PAIRS])
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                 (WHY_META, json.dumps({"at": at, "run_id": run_id, "since": SINCE, "rules_version": version(), "pairs": keep},
                                       default=str, sort_keys=True)))


def _meta(conn: sqlite3.Connection, key: str) -> dict:
    """A meta value as a dict; {} when it is missing. A store that cannot be read or a value that is not JSON gives {}
    too, and the run's log says so (the panel then shows no evidence rather than failing)."""
    from . import runlog
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    except sqlite3.Error as exc:
        runlog.caught(exc, "rarely_paid.meta")
        return {}
    if not row or not row[0]:
        return {}
    try:
        got = json.loads(row[0])
    except (TypeError, ValueError) as exc:
        runlog.caught(exc, "rarely_paid.meta")
        return {}
    return got if isinstance(got, dict) else {}


# ------------------------------------------------------------------------------------------------------ in words
def _pct(p: int, n: int) -> str:
    return f"{round(100 * p / n)}%" if n else "0%"


def _list(pairs: Iterable, meaning: bool = False) -> str:
    from .codes import carc_text, rarc_text
    out = []
    for k, n in pairs or []:
        word = ""
        if meaning:
            c = str(k)
            word = carc_text(c.split("-", 1)[1]) if "-" in c else rarc_text(c)
        out.append(f"{k} ({n}{': ' + _short(word) if word else ''})")
    return ", ".join(out)


def _short(text: str, n: int = 70) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "..."


def evidence_words(ev: dict, payer: str) -> list[str]:
    """The evidence as plain sentences (counts, codes and years only)."""
    if not ev:
        return []
    code = ev.get("code") or "this code"
    out = []
    n, p = int(ev.get("answered") or 0), int(ev.get("paid") or 0)
    when = period_words({"from": ev.get("from"), "to": ev.get("to")}) or f"from {SINCE}"
    who = payer if not ev.get("widened") else f"{payer} (with {', '.join(g for g in ev.get('groups') or [] if g != payer) or 'its other plans'})"
    # EMA's own count, one row per billed service after every answer: a claim sent again and denied again is one row there
    # but several remittance lines here, so the two can differ (a payer that denies until the claim is fixed, then pays).
    # Both are shown, EMA's second, so a short list (the policy page's first four sentences) still has it.
    ema = ev.get("ema") or {}
    books = None
    if ema.get("answered"):
        m, q = int(ema["answered"]), int(ema.get("paid") or 0)
        books = (f"EMA's books, one row per billed service: insurance paid {q} of the {m} it shows answered ({_pct(q, m)})"
                 + (f"; the reasons on the unpaid ones: {_list(ema.get('reasons'), True)}" if ema.get("reasons") else "") + ".")
    if n:
        out.append(f"{who} answered {n} {code} line{'s' if n != 1 else ''} as the primary payer for services {when}, and paid {p} ({_pct(p, n)}).")
        if books:
            out.append(books)
        years = ev.get("by_year") or {}
        if len(years) > 1:
            out.append("By year of service: " + "; ".join(f"{y}: {v[0]} of {v[1]} paid" for y, v in years.items()) + ".")
        rec = ev.get("recoded") or {}
        if rec:
            out.append(f"It answered {sum(v[0] for v in rec.values())} of them under another code ("
                       + ", ".join(f"{k} on {v[0]}, paid on {v[1]}" for k, v in rec.items()) + ").")
        u = int(ev.get("unpaid") or 0)
        if ev.get("reasons"):
            out.append(f"Its reasons on the {u} unpaid line{'s' if u != 1 else ''}: {_list(ev['reasons'], True)}.")
        if ev.get("remarks"):
            out.append(f"Remarks: {_list(ev['remarks'], True)}.")
        if ev.get("modifiers_unpaid"):
            out.append(f"Modifiers on the unpaid lines: {_list(ev['modifiers_unpaid'])}"
                       + (f"; on the paid ones: {_list(ev['modifiers_paid'])}" if ev.get("modifiers_paid") else "") + ".")
        if ev.get("pos_unpaid"):
            out.append(f"Place of service on the unpaid lines: {_list(ev['pos_unpaid'])}"
                       + (f"; on the paid ones: {_list(ev['pos_paid'])}" if ev.get("pos_paid") else "") + ".")
        if ev.get("with_codes"):
            out.append(f"Billed on the same claims: {_list(ev['with_codes'])}.")
        if ev.get("other_line_paid"):
            out.append(f"On {ev['other_line_paid']} of the {u} unpaid lines it paid another line of the same claim: it paid the "
                       "claim's other services and refused this code.")
    else:
        out.append(f"No remittance on file answers {code} for {payer} as the primary payer for services {when}.")
        if books:
            out.append(books)
    return out


def _condition(e: dict, f: dict, lines: list[dict] | None) -> tuple[bool | None, str | None]:
    """Whether the entry's condition holds on the item's own claim: (True, None), (False, why) or (None, why) when it
    cannot be checked. No condition: (True, None)."""
    cond = e.get("applies_when")
    if not cond:
        return True, None
    if cond == "em_with_modifier_25":
        if not lines:
            return None, ("The rule applies only when the visit's E/M is reported with modifier 25. This item's claim lines are "
                          "not on file to check it.")
        svc = f.get("service_date")
        hit = any(str(ln.get("procedure_code") or "") in EM_VISITS and "25" in _mods(ln.get("modifiers"))
                  and (not svc or not ln.get("service_date") or str(ln.get("service_date"))[:10] == str(svc)[:10]) for ln in lines)
        if hit:
            return True, None
        return False, ("The rule applies only when the visit's E/M is reported with modifier 25. This claim's E/M is not, so the "
                       "rule does not explain this denial: read the payer's reason.")
    return None, None


def _claim_lines(conn: sqlite3.Connection, f: dict) -> list[dict]:
    if not f.get("claim_key"):
        return []
    try:
        cur = conn.execute("SELECT procedure_code, modifiers, service_date FROM lines WHERE claim_key=?", (f["claim_key"],))
        return [{"procedure_code": r[0], "modifiers": r[1], "service_date": r[2]} for r in cur.fetchall()]
    except sqlite3.Error as exc:          # the condition is then said to be unchecked, never assumed
        from . import runlog
        runlog.caught(exc, "rarely_paid.claim_lines")
        return []


def _decision(conn: sqlite3.Connection | None, key: str) -> dict:
    if conn is None:
        return {}
    return decision_for(_meta(conn, "ema:writeoff_policy"), key) or {}


def _single(f: dict) -> bool:
    """Whether the item is one line of its code (a remittance line, or a denial only EMA saw on one line): the owner's
    accepting the pattern takes it off Work now. A whole-claim item or several lines keep their other services."""
    if f.get("line_key"):
        return True
    return f.get("signal") == "EMA_DENIED" and len(_ev(f).get("lines") or []) == 1


def block_for(f: dict, code: str, service_date: str | None, *, lines: list[dict] | None = None, pairs: dict | None = None,
              conn: sqlite3.Connection | None = None, as_of: str | None = None) -> dict | None:
    """One "Why <payer> does not pay <code>" block for an item's code, or None when the code is no such pair: neither
    researched for this payer group, nor found rarely paid by the run (`pairs`, the run's evidence by pattern key; a
    researched entry of another spelling of the group or of the company's plans applies only to a pair the run found)."""
    group = f.get("payer_group") or ""
    key = pattern_key(group, code, service_date)
    ev = (pairs or {}).get(key)
    e = entry_for(group, code, service_date)
    if e is not None and not applies(e, ev is not None):
        e = None
    if e is None and ev is None:
        return None
    payer = group or "the payer"
    verdict = e.get("verdict") if e else None
    cond_holds, cond_why = _condition(e, f, lines) if e else (True, None)
    if e and cond_holds is not True:
        verdict = "unclear"
    dec = _decision(conn, key)
    accepted = dec.get("decision") == "accept"
    single = _single(f)
    if verdict == "never_payable":
        words = f"{VERDICT_WORDS['never_payable']}: {payer} does not pay {code} here, and no note changes that. "
        if not single:
            words += f"Only this item's {code} is concerned: its other services are worked as its reason says."
        elif accepted:
            words += (f"The owner accepted this pattern on the write-off policy page ({str(dec.get('at') or '')[:10]}): the next run "
                      "takes these items off Work now.")
        else:
            words += "It stays on Work now until the owner accepts the pattern on the write-off policy page."
    elif verdict == "claim":
        words = f"{VERDICT_WORDS['claim']}: the claim, not the note, has to change for {payer} to pay {code}."
    elif verdict == "note":
        words = f"{VERDICT_WORDS['note']}: {payer} pays {code} when the note shows what its rule asks for."
    elif verdict == "unclear":
        words = (f"{VERDICT_WORDS['unclear']}: ask {payer} for its written rule for {code}." if cond_holds is True else
                 "Unclear for this item: " + (cond_why or ""))
    else:
        words = (f"Not researched yet: no published rule of {payer} for {code} is on file. The evidence below is the store's "
                 "own; keep working it as its reason says.")
    return {
        "code": code, "payer_group": group, "title": f"Why {payer} does not pay {code}", "pattern": key, "single": single,
        "verdict": verdict, "verdict_text": words, "researched_verdict": (e or {}).get("verdict"), "match": (e or {}).get("match"),
        "condition": cond_why if e else None,
        "rule": (e or {}).get("rule"), "what_to_show": (e or {}).get("what_to_show"),
        "period": period_words((e or {}).get("period")) or None, "sources": (e or {}).get("sources") or [],
        "researched": (e or {}).get("researched"),
        "evidence": ev, "evidence_text": evidence_words(ev or {}, payer) if ev else [],
        "evidence_note": None if ev else "The next analysis works out the evidence from the remittances on file.",
        "as_of": as_of, "decision": dec.get("decision") or "pursue", "decided_by": dec.get("by"), "decided_at": dec.get("at"),
    }


# the routes whose work a never-payable rule leads (story._with_payer_rule): an appeal, a correction, a call, a decision, a
# rate request, and a denial only EMA saw whose EOB is fetched first (its appeal is what the rule says no note changes)
LEAD_ROUTES = ("appeal", "corrected", "call", "decide", "rate", "eob_first")


def never_payable_lead(conn: sqlite3.Connection | None, f: dict, blocks: list[dict] | None = None) -> dict | None:
    """The block that leads an item's next step, or None: a never-payable rule on a one-line item (no note changes it, so no
    appeal goes for it in bulk, and its panel offers no appeal packet). With `blocks` (the drawer's), read from them;
    else worked out from the store, cheaply for an item none of whose codes has a never-payable entry."""
    if blocks is None:
        grp = f.get("payer_group")
        if not any((entry_for(grp, code, day) or {}).get("verdict") == "never_payable" for code, day in item_codes(f)):
            return None
        blocks = why(conn, f) if conn is not None else []
    return next((b for b in blocks if b.get("verdict") == "never_payable" and b.get("single")), None)


def why(conn: sqlite3.Connection, f: dict) -> list[dict]:
    """The item drawer's blocks, one per code of the item that is such a pair (most items: none or one)."""
    codes = item_codes(f)
    if not codes:
        return []
    meta = _meta(conn, WHY_META)
    pairs = meta.get("pairs") if isinstance(meta.get("pairs"), dict) else {}
    table = _meta(conn, "ema:payer_policy").get("patterns")
    table = table if isinstance(table, dict) else {}
    lines = _claim_lines(conn, f)
    out = []
    for code, day in codes:
        b = block_for(f, code, day, lines=lines, pairs=pairs, conn=conn, as_of=meta.get("at"))
        if b:
            b["in_table"] = b["pattern"] in table          # the owner's table lists it: it can be accepted there
            out.append(b)
    return out


def researched_blocks(f: dict, lines: list[dict] | None = None, blocks: list[dict] | None = None) -> list[dict]:
    """The researched entries of an item's codes: from the drawer's blocks when the caller has them (they know the run's
    evidence), else the entries that name the item's payer group (no store at hand: the others need the run's word)."""
    if blocks is not None:
        return [b for b in blocks if isinstance(b, dict) and b.get("researched_verdict")]
    out = []
    for code, day in item_codes(f):
        b = block_for(f, code, day, lines=lines, pairs=None, conn=None)
        if b and b.get("researched_verdict"):
            out.append(b)
    return out


def _and(words: list[str]) -> str:
    words = [w for w in words if w]
    return ", ".join(words[:-1]) + " and " + words[-1] if len(words) > 1 else (words[0] if words else "")


def same_rule(blocks: list[dict]) -> list[dict]:
    """The researched blocks with each rule told once: blocks of one payer group with the same verdict, rule, what to show
    and condition (one state's enrollment rule for several codes of a claim, say) become one, naming its codes together
    ('codes'; 'code' in words: "99213, 11102 and 11103"). The first block's order is kept."""
    out: list[dict] = []
    seen: dict[tuple, dict] = {}
    for b in blocks:
        k = (b.get("payer_group"), b.get("verdict"), b.get("rule"), b.get("what_to_show"), b.get("condition"))
        g = seen.get(k)
        if g is None:
            g = seen[k] = dict(b, codes=[b.get("code")])
            out.append(g)
        elif b.get("code") not in g["codes"]:
            g["codes"].append(b.get("code"))
            g["code"] = _and(g["codes"])
    return out


def checklist(f: dict, lines: list[dict] | None = None, blocks: list[dict] | None = None) -> list[str]:
    """What the biller checks before the packet goes (never sent with it): the payer's rule for the code and what the note
    or the claim must show, a never-payable verdict plainly; one line per rule, naming every code of the item it covers."""
    out = []
    for b in same_rule(researched_blocks(f, lines, blocks)):
        src = (b.get("sources") or [{}])[0]
        cite = f" ({src.get('title')}, {src.get('date')})" if src.get("title") else ""
        if b["verdict"] == "never_payable":
            out.append(f"Before sending: {b['payer_group']} does not pay {b['code']} under its published rule{cite}, and an appeal "
                       f"does not change that. {b.get('what_to_show') or ''}".strip())
        elif b["verdict"] == "unclear" and b.get("condition"):
            out.append(f"{b['payer_group']}'s rule for {b['code']}{cite}: {b['condition']}")
        else:
            out.append(f"What the note or claim must show for {b['payer_group']} to pay {b['code']}{cite}: {b.get('what_to_show') or ''}".strip())
    return out


def enclosures(f: dict, lines: list[dict] | None = None, blocks: list[dict] | None = None) -> list[str]:
    """The payer's own published rule, enclosed with an appeal the rule supports (a note or a claim verdict): its title,
    date and address. Nothing for a never-payable or an unclear one (an appeal never encloses a rule against itself)."""
    out = []
    for b in researched_blocks(f, lines, blocks):
        if b["verdict"] not in ("note", "claim"):
            continue
        for s in (b.get("sources") or [])[:2]:
            if s.get("title") and s.get("url"):
                line = f"{s['title']} ({s.get('date') or 'undated'}), {s['url']}, with the passage the record meets highlighted"
                if line not in out:
                    out.append(line)
    return out


def review_input(f: dict, lines: list[dict] | None = None, blocks: list[dict] | None = None) -> list[dict]:
    """What the note review is told for each researched rule of the item (the codes it covers named together): the payer's
    rule, what the note or claim must show, and the research's verdict. Public text only."""
    return [{"code": b["code"], "codes": b["codes"], "payer": b["payer_group"], "verdict": b["verdict"],
             "verdict_words": VERDICT_WORDS.get(b["verdict"] or "", ""), "rule": b.get("rule") or "",
             "what_to_show": b.get("what_to_show") or "", "condition": b.get("condition") or ""}
            for b in same_rule(researched_blocks(f, lines, blocks))]
