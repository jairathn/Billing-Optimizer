"""
Every item's story, its verdict and its exact next step (amendment 9), worked out from the remittances and EMA's
postings, never from EMA's claim status alone.

  item_story(conn, finding_id=None, *, claim_key=None, ledger=None, today=None)
      {'rows': [...], 'balance': {...}, 'reconciles': bool, 'gap_cents': int, 'twin': {...} or None, 'notes': [...]}
      One chronological list of every event on the item's line (its claim's, where the line has none), each row tagged
      with its source: '835' (each adjudication: payer, paid, allowed, every reason with group, amount and meaning, every
      remark, the claim status, reversal pairs, the code paid against the code billed, units; the takebacks naming the
      claim), 'production' (EMA's postings: the charge, each payment and whether its check has an 835 on file, each
      adjustment with its reason, kind and whether it was posted on a remittance's posting day, patient money), 'ar'
      (the A/R row as of its pull: balance, denial amount, status, action, whether a note exists; and the A/R's 30-day
      window), 'weekly' (the charge-line and ERA-line exports), 'claim_status' (the claim-status export) and 'remitwatch'
      (the item raised, statuses, faxes, notes). It ends with a balance line that reconciles to the cent (charge =
      insurer paid + patient paid + contractual + written off + open, a credit as a negative open) or shows the gap and
      its cause. On a server with the posting ledger every posting is listed; without it (a serverless dashboard) the
      store's summaries are, and the story says the detail is kept on the server. The surgery center's facility claim
      for the same patient, date and code is shown apart ('twin') and never counted on the line.
  item_verdict(conn, finding_id, *, ledger=None, today=None)
      {'state', 'sentence', 'rows', 'strength', 'rule', ...}: the state the detection run stored (work_state), else
      states.state_of's answer, as plain sentences naming the decisive story rows, with how strong the proof is:
      'proven' (two sources agree), 'one_source', or 'inferred' (the rule is named).
  next_step(conn, finding_id, *, ledger=None, today=None)
      {'action', 'who', 'deadline', 'basis', 'expected_cents', 'buttons', 'instructions', ...}: what to do, who, by
      when and why, what it is worth, the buttons that do it in RemitWatch (the ids below), and the steps for work done
      outside it. Never an appeal for a line paid, settled, credited, reprocessed or waiting ('appeal_ok').
  explain(conn, finding_id, *, ledger=None, today=None)
      the three at once, from one gathering of the facts (what the item drawer asks for).

Button ids: build_packet, attach_note, find_note, upload_note, fax_payer, copy_instructions, mark_sent, log_outcome,
mark_posted, mark_billed, accept_pattern, let_go, reopen, request_era, open_in_ema.

Dates are shown as the pages show them (behind the sign-in); patient details are left to the pages' names switch: the
story holds no patient name, member id or record number, and only the last four characters of a check number.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Sequence

from . import states as S
from .states import day, tail, usd

SOURCES = ("835", "production", "ar", "weekly", "claim_status", "remitwatch")
BUTTONS = ("build_packet", "attach_note", "find_note", "upload_note", "fax_payer", "copy_instructions", "mark_sent", "log_outcome",
           "mark_posted", "mark_billed", "accept_pattern", "let_go", "reopen", "request_era", "open_in_ema")
STATUS_WORDS = {"1": "processed as primary", "2": "processed as secondary", "3": "processed as tertiary", "4": "denied",
                "19": "processed as primary and forwarded to the next payer", "20": "processed as secondary and forwarded",
                "21": "processed as tertiary and forwarded", "22": "a reversal of an earlier answer",
                "23": "not its claim, forwarded", "25": "a predetermination"}
AR_WINDOW = ("The A/R export lists only lines with activity in its last 30 days and lines with a denial; a line missing "
             "from it is not closed or paid.")
SERVER_ONLY = "Each of EMA's postings is kept on the server; this site shows EMA's summaries of this line."
_SRC_ORDER = {"835": 0, "production": 1, "weekly": 2, "claim_status": 3, "ar": 4, "remitwatch": 5}


# ------------------------------------------------------------------------------------------------------------ helpers
def _meaning(code: str) -> str:
    from .codes import carc_text, rarc_text
    c = str(code or "")
    if c[:2] in ("CO", "PR", "OA", "PI", "CR") and "-" in c:
        return carc_text(c.split("-", 1)[1]) or ""
    return rarc_text(c) or carc_text(c) or ""


def _ckey(code: Any) -> str:
    """A reason code as letters and digits only (CO-97, CO 97 and co97 are one code)."""
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def _codes(adj: Sequence, rmk: Sequence = (), status: str | None = None) -> list[dict]:
    out = []
    for g, c, a in adj:
        k = f"{g}-{c}" if g else str(c)
        out.append({"code": k, "meaning": _meaning(k), "amount_cents": a})
    for r in rmk:
        out.append({"code": r, "meaning": _meaning(r)})
    if status:
        out.append({"code": f"claim status {status}", "meaning": STATUS_WORDS.get(str(status), "")})
    return out


def _row(date: Any, source: str, kind: str, text: str, *, payer: Any = None, amount: int | None = None, codes: list | None = None,
         tags: Sequence = (), line: str | None = None, **extra: Any) -> dict:
    r = {"n": 0, "date": str(date)[:10] if date else None, "source": source, "kind": kind, "payer": payer or None,
         "amount_cents": amount, "codes": codes or [], "text": text, "decisive": False, "line": line, "tags": list(tags)}
    r.update(extra)
    return r


def _load(conn: sqlite3.Connection, finding_id: str) -> dict | None:
    if not S._table(conn, "findings"):
        return None
    cur = conn.execute("SELECT * FROM findings WHERE finding_id=?", (finding_id,))
    names = [d[0] for d in cur.description]
    r = cur.fetchone()
    return dict(zip(names, r)) if r else None


def _line_label(cpt: Any, svc: Any, mods: Any = None) -> str:
    m = ""
    if mods:
        try:
            ms = json.loads(mods) if isinstance(mods, str) and mods.startswith("[") else mods
        except ValueError:
            ms = mods
        if isinstance(ms, (list, tuple)):
            m = "-".join(str(x) for x in ms if x)
        else:
            m = str(ms).replace(",", "-").strip("-")
    return f"{cpt or '?'}{('-' + m) if m else ''} on {day(svc)}" if svc else f"{cpt or '?'}{('-' + m) if m else ''}"


# ------------------------------------------------------------------------------------------------- the story's rows
def _rows_835(fx: dict) -> list[dict]:
    f = fx["item"]
    out = []
    own_line = f.get("line_key")
    for x in fx["r835"]["rows"]:
        grp = S.row_group(x)[0]
        label = _line_label(x["code"], x["svc"], x.get("modifiers")) if x.get("line_key") else f"the claim of {day(x['svc'])}"
        parts = []
        if x["status"] == "22":
            parts.append(f"{x['payer']} reversed an earlier answer (claim status 22)")
        else:
            st = STATUS_WORDS.get(x["status"], f"claim status {x['status']}")
            if x["status"] == "19" and x.get("crossover"):
                st = f"processed as primary and forwarded to {x['crossover']}"
            parts.append(f"{x['payer']} answered ({st})")
        bits = []
        if x.get("line_key"):
            billed = x.get("ocode") if x.get("ocode") and x.get("ocode") != x["code"] else None
            bits.append(f"{label}" + (f", billed as {billed}, paid as {x['code']}" if billed else ""))
            bits.append(f"billed {usd(x['charge'])}")
            if x.get("allowed") is not None:
                bits.append(f"allowed {usd(x['allowed'])}")
            bits.append(f"paid {usd(x['paid'])}")
            if x.get("ounits") and x.get("units") is not None and abs(x["ounits"]) != abs(x["units"]):
                bits.append(f"units paid {x['units']:g} of {x['ounits']:g}")
        else:
            bits.append(f"claim billed {usd(x['claim_charge'])}, paid {usd(x['claim_paid'])}")
        adj = x["adj"] or x["cadj"]
        if adj:
            bits.append("; ".join(f"{g}-{c} {usd(a)}" + (f" ({_meaning(f'{g}-{c}')})" if _meaning(f'{g}-{c}') else "") for g, c, a in adj))
        rmk = list(x["rmk"]) + [r for r in x["crmk"] if r not in x["rmk"]]
        if rmk:
            bits.append("remarks " + ", ".join(f"{r}" + (f" ({_meaning(r)})" if _meaning(r) else "") for r in rmk))
        extra = []
        if x.get("freq") == "7":
            extra.append("a corrected (replacement) claim")
        if x.get("received"):
            extra.append(f"received by the payer on {day(x['received'])}")
        if x.get("check_tail"):
            extra.append(f"check ending {x['check_tail']}")
        if str(x.get("method") or "").upper() == "NON":
            extra.append("this remittance moved no money (payment method NON)")
        if x.get("reversed_by"):
            extra.append("reversed later")
        text = parts[0] + ": " + "; ".join(bits) + (f" ({', '.join(extra)})" if extra else "") + "."
        tags = [f"r835:{x.get('line_key') or x['claim_key']}"]
        if own_line and x.get("line_key") == own_line:
            tags.append("own_remit")
        elif not own_line and x["claim_key"] == f.get("claim_key"):
            tags.append("own_remit")
        if x["status"] == "22":
            tags.append("reversal")
        out.append(_row(x["date"], "835", "reversal" if x["status"] == "22" else ("adjudication" if (x["paid"] or 0) == 0 else "payment"),
                        text, payer=grp, amount=x["paid"] if x.get("line_key") else x["claim_paid"], codes=_codes(adj, rmk, x["status"]),
                        tags=tags, line=label, claim_id=x["pcn"], check_tail=x.get("check_tail"), status=x["status"]))
    for p in fx["r835"].get("plb") or []:
        what = {"WO": "took back", "FB": "held back (forward balance)", "72": "took back (authorized return)", "J1": "adjusted",
                "L6": "added interest", "CS": "adjusted"}.get(p["reason"], "adjusted")
        out.append(_row(p["date"], "835", "takeback" if p["reason"] in ("WO", "72") else "plb",
                        f"{p['payer']} {what} {usd(abs(p['cents']))} on {day(p['date'])} (provider-level adjustment {p['reason']}, its "
                        f"reference names claim {p['claim_id']}; check ending {p['check_tail']}).", payer=S._grp_kind(p["payer"])[0],
                        amount=-abs(p["cents"]) if p["reason"] in ("WO", "72", "FB") else p["cents"], tags=["plb"]))
    return out


def _remit_days(fx: dict) -> dict[str, str]:
    """The days a payer's answer was posted in EMA on the item's line (a payment or a $0 row with a check), with the check."""
    out: dict[str, str] = {}
    for p in fx["ema"]["pays"]:
        if p["posted"] and (p["check"] or p["cents"]):
            out.setdefault(p["posted"], p["check_tail"])
    return out


def _era_writeoffs(conn: sqlite3.Connection, f: dict) -> list[dict]:
    """EMA's ERA-lines export for the item's line: how EMA's posting rule handled each reason (WRITE_OFF, IGNORE,
    TRANSFER)."""
    if not f.get("line_key") or not S._table(conn, "line_handling"):
        return []
    out = []
    for carc, status, cents, when in conn.execute(
            """SELECT carc, adjustment_status, adjustments_cents, transaction_date FROM line_handling WHERE line_key=?
               ORDER BY transaction_date""", (f["line_key"],)):
        out.append({"carc": carc, "status": (status or "").upper(), "cents": cents or 0, "date": (when or "")[:10] or None})
    return out


def _ledger_postings(conn: sqlite3.Connection, ledger: Any, fx: dict) -> tuple[list[dict], list[dict]] | None:
    """Every posting on the item's EMA lines from the server's ledger, and the patient's own postings for the same
    patient, date and code (patient rows carry no claim). None when there is no ledger."""
    if ledger is None or not fx["ema"]["lines"]:
        return None
    lc = ledger.conn if hasattr(ledger, "conn") else ledger
    rows, pat = [], []
    persons = {}
    if "person" in S._cols(conn, "ema_lines"):
        for ln in fx["ema"]["lines"]:
            r = conn.execute("SELECT person FROM ema_lines WHERE link=? AND svc=? AND cpt=?", (ln["link"], ln["svc"], ln["cpt"])).fetchone()
            if r and r[0] is not None:
                persons[(ln["svc"], ln["cpt"])] = r[0]
    q = """SELECT p.posted, p.svc, p.cpt, p.units, p.link, p.check_no, p.grp, p.code, k.name, s.name, a.name, py.name, pp.name,
                  p.charges, p.ins_charges, p.ins_paid, p.pat_paid, p.contractual, p.noncontractual, p.pat_adj, p.allowed, p.flags
           FROM postings p LEFT JOIN names k ON k.id = p.kind LEFT JOIN names s ON s.id = p.status LEFT JOIN names a ON a.id = p.action
           LEFT JOIN names py ON py.id = p.payer LEFT JOIN names pp ON pp.id = p.primary_payer"""
    cols = ("posted", "svc", "cpt", "units", "link", "check_no", "grp", "code", "kind", "status", "action", "payer", "primary_payer",
            "charges", "ins_charges", "ins_paid", "pat_paid", "contractual", "noncontractual", "pat_adj", "allowed", "flags")
    for ln in fx["ema"]["lines"]:
        for r in lc.execute(q + " WHERE p.link = ? AND p.svc = ? AND COALESCE(p.cpt, '') = ? ORDER BY p.posted, p.rowid",
                            (ln["link"], ln["svc"], ln["cpt"])):
            rows.append(dict(zip(cols, r)))
        person = persons.get((ln["svc"], ln["cpt"]))
        if person is not None:
            for r in lc.execute(q + " WHERE p.link IS NULL AND p.person = ? AND p.svc = ? AND COALESCE(p.cpt, '') = ? AND (p.flags & 16) <> 0"
                                    " ORDER BY p.posted, p.rowid", (person, ln["svc"], ln["cpt"])):
                d = dict(zip(cols, r))
                d["joined"] = ln.get("pat_join") == "one"
                pat.append(d)
    return rows, pat


def _check_in_835(conn: sqlite3.Connection, checks: Sequence[tuple]) -> dict:
    out: dict = {}
    if not S._table(conn, "ema_checks"):
        return out
    for chk, payer in set(checks):
        if not chk:
            continue
        r = conn.execute("SELECT match, cause FROM ema_checks WHERE check_no=? AND payer=?", (chk, payer or "")).fetchone()
        if r:
            out[(chk, payer or "")] = r[0] in ("exact", "loose") or r[1] == "misnumbered"
    return out


def _rows_production(conn: sqlite3.Connection, fx: dict, ledger: Any) -> tuple[list[dict], dict, list[str]]:
    """EMA's postings on the item's lines, the totals they add up to, and notes. With the ledger: every posting. Without
    it: the store's summaries (payments by check and day, reasons with their first and last posting days)."""
    f = fx["item"]
    out: list[dict] = []
    notes: list[str] = []
    lines = fx["ema"]["lines"]
    if not lines:
        return out, {}, notes
    rdays = _remit_days(fx)
    era = _era_writeoffs(conn, f)
    era_wo = {_ckey(h["carc"]): h for h in era if h["status"] == "WRITE_OFF"}
    first_remit = min(rdays) if rdays else None
    itemized = {"charges": 0, "ins_paid": 0, "contractual": 0, "noncontractual": 0, "pat_paid": 0, "pat_adj": 0}

    def adj_text(grp: str, code: str, kind: str | None, contr: int, nonc: int, posted: str | None) -> tuple[str, list]:
        k = f"{grp}-{code}" if grp else (code or kind or "")
        what = "Contractual" if contr and not nonc else ("Non-contractual" if nonc and not contr else "Contractual and non-contractual")
        cents = (contr or 0) + (nonc or 0)
        when = ""
        tags = []
        if posted:
            if posted in rdays:
                when = f", posted the day the remittance was posted (check ending {rdays[posted]})"
                tags.append("adj_at_posting")
            elif first_remit:
                gap = S._days(first_remit, posted)
                when = (f", {gap} days after the remittance was posted, on a day with no remittance posted" if gap and gap > 0
                        else ", before the remittance was posted")
                tags.append("adj_later" if gap and gap > 0 else "adj_before")
        rule = ""
        if grp and _ckey(k) in era_wo and "adj_at_posting" in tags:
            rule = ". EMA's posting rule wrote this off (EMA's ERA export shows WRITE_OFF)"
            tags.append("rule_proven")
        meaning = _meaning(k) if grp else ""
        return (f"{what} adjustment {usd(cents)}, reason {k}" + (f" ({meaning})" if meaning else "") +
                (f", EMA kind '{kind}'" if kind and kind != code else "") + when + rule + "."), tags

    led = _ledger_postings(conn, ledger, fx)
    pay_835 = {}
    if led is not None:
        rows, pat = led
        pay_835 = _check_in_835(conn, [(r["check_no"], r["payer"]) for r in rows if r["check_no"]])
        for r in rows:
            label = _line_label(r["cpt"], r["svc"])
            if r["charges"]:
                itemized["charges"] += r["charges"]
                if r["charges"] < 0:
                    out.append(_row(r["posted"], "production", "charge_void", f"EMA reversed the charge of {label}: {usd(r['charges'])}: the "
                                    "claim was voided or re-created, so this service may be billed on another claim.",
                                    amount=r["charges"], tags=["charge", "charge_void"], line=label))
                else:
                    out.append(_row(r["posted"], "production", "charge", f"Charged {label}: {usd(r['charges'])}"
                                    + (f", {r['units']:g} units" if r.get("units") and r["units"] != 1 else "")
                                    + (f"; primary payer {r['primary_payer']}" if r.get("primary_payer") else "") + ".",
                                    amount=r["charges"], tags=["charge"], line=label))
            if r["ins_paid"] or r["check_no"]:
                itemized["ins_paid"] += r["ins_paid"] or 0
                in835 = pay_835.get((r["check_no"], r["payer"] or ""))
                has = "an 835 is on file for this check" if in835 else "no 835 is on file for this check"
                kind = "payment" if (r["ins_paid"] or 0) > 0 else ("takeback" if (r["ins_paid"] or 0) < 0 else "zero_answer")
                verb = {"payment": f"paid {usd(r['ins_paid'])}", "takeback": f"took back {usd(-(r['ins_paid'] or 0))}",
                        "zero_answer": "answered $0.00"}[kind]
                out.append(_row(r["posted"], "production", kind, f"{r['payer'] or 'Insurance'} {verb} on {label}, posted {day(r['posted'])} "
                                f"(check ending {tail(r['check_no']) or '?'}; {has}).", payer=r["payer"], amount=r["ins_paid"] or 0,
                                tags=[f"pay:{r['posted']}:{tail(r['check_no'])}", "pay_no835" if not in835 else "pay_835"], line=label,
                                check_tail=tail(r["check_no"]), in_835=bool(in835)))
            if r["contractual"] or r["noncontractual"]:
                itemized["contractual"] += r["contractual"] or 0
                itemized["noncontractual"] += r["noncontractual"] or 0
                text, tags = adj_text(r["grp"] or "", r["code"] or "", r["kind"], r["contractual"] or 0, r["noncontractual"] or 0, r["posted"])
                code = f"{r['grp']}-{r['code']}" if r["grp"] else (r["code"] or r["kind"] or "")
                out.append(_row(r["posted"], "production", "adjustment", text, payer=r["payer"],
                                amount=(r["contractual"] or 0) + (r["noncontractual"] or 0),
                                codes=[{"code": code, "meaning": _meaning(code)}] if code else [],
                                tags=tags + [f"adj:{code}"], line=label))
            if r["pat_paid"] or r["pat_adj"]:
                itemized["pat_paid"] += r["pat_paid"] or 0
                itemized["pat_adj"] += r["pat_adj"] or 0
                out.append(_row(r["posted"], "production", "patient_payment" if r["pat_paid"] else "patient_adjustment",
                                f"Patient {'paid' if r['pat_paid'] else 'adjustment'} {usd(r['pat_paid'] or r['pat_adj'])} on {label}.",
                                amount=r["pat_paid"] or r["pat_adj"], tags=["patient"], line=label))
        for r in pat:
            label = _line_label(r["cpt"], r["svc"])
            if not (r["pat_paid"] or r["pat_adj"]):
                continue
            joined = r.get("joined")
            if joined:
                itemized["pat_paid"] += r["pat_paid"] or 0
                itemized["pat_adj"] += r["pat_adj"] or 0
            amt = r["pat_paid"] or r["pat_adj"]
            what = "paid" if r["pat_paid"] else "had an adjustment of"
            out.append(_row(r["posted"], "production", "patient_payment" if r["pat_paid"] else "patient_adjustment",
                            f"The patient {what} {usd(amt)} for {label}, posted {day(r['posted'])}" + (
                                "" if joined else " (EMA ties patient money to no claim, and two claims share this patient, date and "
                                                  "code, so it is not counted on this line)") + ".",
                            amount=amt, tags=["patient"] + ([] if joined else ["patient_unjoined"]), line=label, counted=bool(joined)))
    else:
        notes.append(SERVER_ONLY)
        for ln in lines:
            label = _line_label(ln["cpt"], ln["svc"])
            if ln.get("charges"):
                itemized["charges"] += ln["charges"]
                out.append(_row(ln.get("first_charge_posted") or ln.get("first_posted"), "production", "charge",
                                f"Charged {label}: {usd(ln['charges'])}" + (f", {ln['units']:g} units" if ln.get("units") and ln["units"] != 1 else "")
                                + (f"; primary payer {ln['primary_payer']}" if ln.get("primary_payer") else "") + ".",
                                amount=ln["charges"], tags=["charge"], line=label))
        for p in fx["ema"]["pays"]:
            label = _line_label(p["cpt"], p["svc"])
            itemized["ins_paid"] += p["cents"] or 0
            has = "an 835 is on file for this check" if p["in_835"] else "no 835 is on file for this check"
            kind = "payment" if (p["cents"] or 0) > 0 else ("takeback" if (p["cents"] or 0) < 0 else "zero_answer")
            verb = {"payment": f"paid {usd(p['cents'])}", "takeback": f"took back {usd(-(p['cents'] or 0))}", "zero_answer": "answered $0.00"}[kind]
            out.append(_row(p["posted"], "production", kind, f"{p['payer'] or 'Insurance'} {verb} on {label}, posted {day(p['posted'])} "
                            f"(check ending {p['check_tail'] or '?'}; {has}).", payer=p["payer"], amount=p["cents"] or 0,
                            tags=[f"pay:{p['posted']}:{p['check_tail']}", "pay_no835" if not p["in_835"] else "pay_835"], line=label,
                            check_tail=p["check_tail"], in_835=bool(p["in_835"])))
        listed = {"contractual": 0, "noncontractual": 0}
        for z in fx["ema"]["reasons"]:
            label = _line_label(z["cpt"], z["svc"])
            if not (z["contr"] or z["nonc"]) and z["grp"] == "PR":
                continue
            listed["contractual"] += z["contr"] or 0
            listed["noncontractual"] += z["nonc"] or 0
            text, tags = adj_text(z["grp"], z["code"], None, z["contr"] or 0, z["nonc"] or 0, z["first"])
            if z.get("last") and z["last"] != z["first"]:
                text = text[:-1] + f" (first posted {day(z['first'])}, last {day(z['last'])})."
                if z["last"] not in rdays and "adj_at_posting" in tags:
                    tags = [t for t in tags if t != "rule_proven"] + ["adj_later"]
            code = f"{z['grp']}-{z['code']}" if z["grp"] else z["code"]
            out.append(_row(z["first"], "production", "adjustment", text, payer=z["payer"], amount=(z["contr"] or 0) + (z["nonc"] or 0),
                            codes=[{"code": code, "meaning": _meaning(code)}] if code else [], tags=tags + [f"adj:{code}"], line=label))
        tot = S._ema_line_totals(fx)
        rest_c = tot["contractual"] - listed["contractual"]
        rest_n = tot["noncontractual"] - listed["noncontractual"]
        if rest_c or rest_n:
            out.append(_row(None, "production", "adjustment_summary",
                            f"Other adjustments EMA posted on this line: contractual {usd(rest_c)}, non-contractual {usd(rest_n)} "
                            "(routine reductions such as CO-45; each is listed on the server).", amount=rest_c + rest_n, tags=["summary"]))
        itemized["contractual"] = tot["contractual"]
        itemized["noncontractual"] = tot["noncontractual"]
        if tot["pat_paid"] or tot["pat_adj"]:
            out.append(_row(None, "production", "patient_summary",
                            f"The patient paid {usd(tot['pat_paid'])}" + (f" and had adjustments of {usd(tot['pat_adj'])}" if tot["pat_adj"] else "")
                            + " on this line in all (the days of each are kept on the server).", amount=tot["pat_paid"], tags=["patient", "summary"]))
            itemized["pat_paid"] = tot["pat_paid"]
            itemized["pat_adj"] = tot["pat_adj"]
    # status and action, as context only
    seen = set()
    for e in fx["ema"]["events"]:
        k = (e["kind"], e["value"])
        if k in seen or not e.get("value"):
            continue
        seen.add(k)
        kind = S.action_kind(e["value"]) if e["kind"] == "action" else None
        word = "claim action" if e["kind"] == "action" else "claim status"
        out.append(_row(e.get("first_seen"), "production", "ema_" + e["kind"],
                        f"EMA {word} '{e['value']}', first seen on the Production report of {day(e.get('first_seen'))}"
                        + ("; a claim status is claim-level and undated: it never says how this line ended" if e["kind"] == "status" else "")
                        + (f" (work: {S.WORK_WORDS.get(kind, kind)})" if kind else "") + ".", tags=["ema_" + e["kind"]] + (["work"] if kind else [])))
    for h in era:
        out.append(_row(h["date"], "weekly", "era_line", f"EMA's ERA-lines export: {h['carc'] or 'a reason'} {usd(h['cents'])} handled "
                        f"{h['status'] or '?'}" + (" (EMA's posting rule wrote it off)" if h["status"] == "WRITE_OFF" else
                                                    " (left open for a person)" if h["status"] == "IGNORE" else "") + ".",
                        amount=h["cents"], codes=[{"code": h["carc"], "meaning": _meaning(h["carc"] or "")}] if h["carc"] else [],
                        tags=["era", f"era:{_ckey(h['carc'])}"]))
    return out, itemized, notes


def _rows_ar(fx: dict) -> list[dict]:
    out = []
    for a in fx["ar"]:
        label = _line_label(a.get("cpt"), a.get("svc"))
        bits = [f"balance {usd(a.get('exposure'))}"]
        if a.get("denial"):
            bits.append(f"denial {usd(a['denial'])}")
        bits.append(f"status {a.get('status') or 'none'}")
        if a.get("action"):
            bits.append(f"claim action '{a['action']}'")
        if a.get("follow_up"):
            bits.append(f"follow-up {day(a['follow_up'])}")
        if a.get("denial_category"):
            bits.append(f"denial category '{a['denial_category']}'")
        if a.get("has_note"):
            kinds = [k for k in str(a.get("note_kind") or "").split(",") if k]
            first = a.get("hist_first_seen")
            bits.append("a billing note exists" + (f" (about {', '.join(S.WORK_WORDS.get(k, k.replace('_', ' ')) for k in kinds)})" if kinds else "")
                        + (f", first on the A/R of {day(first)}" if first else ""))
        out.append(_row(a.get("as_of"), "ar", "ar_row", f"On EMA's A/R of {day(a.get('as_of'))} ({a.get('party') or 'insurance'}, "
                        f"{a.get('coverage') or 'coverage not named'}): {label}: " + "; ".join(bits) + ".",
                        amount=a.get("exposure"), tags=["ar"] + (["ar_note"] if a.get("has_note") else []), line=label,
                        claim_id=a.get("claim_id")))
    return out


def _rows_weekly(conn: sqlite3.Connection, fx: dict) -> list[dict]:
    """The weekly exports' charge lines for the item's services, and the claim-status rows of its bill."""
    f = fx["item"]
    out = []
    keys = set(S._service_keys(f))
    b = S.bill_of(f.get("patient_control_number"))
    if b and S._table(conn, "ema_rows"):
        cols = S._cols(conn, "ema_rows")
        icd = "icd10" if "icd10" in cols else "NULL"
        for cid, svc, cpt, mods, units, chg, pay, st, when, fam in conn.execute(
                f"""SELECT claim_id, service_date, cpt, modifiers, units, charges_cents, insurance_payments_cents, claim_status,
                           COALESCE(transaction_date, posted_date), family
                    FROM ema_rows WHERE claim_id >= ? AND claim_id < ? AND family = 'charge_lines'""", (b, b + "~")):
            if S.bill_of(cid) != b or ((svc or "")[:10], cpt) not in keys and keys:
                continue
            label = _line_label(cpt, svc, mods)
            out.append(_row(when, "weekly", "charge_line", f"EMA's charge-line export: {label}, {units or 1:g} units, charge {usd(chg)}"
                            + (f", insurance paid {usd(pay)}" if pay else "") + (f", claim status {st}" if st else "") + ".",
                            amount=chg, tags=["weekly"], line=label, claim_id=cid))
        if icd != "NULL":
            dx = {}
            for cid, svc, cpt, dxs in conn.execute("""SELECT claim_id, service_date, cpt, icd10 FROM ema_rows WHERE claim_id >= ? AND claim_id < ?
                                                     AND family = 'charge_lines' AND icd10 IS NOT NULL""", (b, b + "~")):
                if S.bill_of(cid) == b and ((svc or "")[:10], cpt) in keys:
                    dx[cpt] = dxs
            if dx:
                out.append(_row(None, "weekly", "diagnosis", "Diagnoses on the charge lines: " + "; ".join(f"{k}: {v}" for k, v in sorted(dx.items())) + ".",
                                tags=["weekly", "diagnosis"]))
    for x in fx["exports"]:
        bits = [f"status {x['status'] or 'none'}"]
        if x.get("submitted"):
            bits.append(f"submitted {day(x['submitted'])}")
        if x.get("denied"):
            bits.append(f"denied {day(x['denied'])}")
        out.append(_row(x.get("submitted") or x.get("denied"), "claim_status", "claim_status",
                        f"EMA's claim-status export, claim {x['claim_id']} ({_line_label(x.get('cpt'), x.get('svc'))}): " + ", ".join(bits)
                        + ". A claim status is claim-level: it never says how this line ended.", tags=["claim_status"], claim_id=x["claim_id"]))
    return out


def _rows_remitwatch(fx: dict) -> list[dict]:
    f = fx["item"]
    out = [_row(f.get("first_seen"), "remitwatch", "raised", f"RemitWatch listed this item: {f.get('title') or f.get('signal')}.",
                amount=f.get("shortfall_cents"), tags=["raised"])]
    words = {"open": "To do", "in_progress": "Started", "appealed": "Sent", "resolved": "Paid", "lost": "Lost", "dismissed": "Let go"}
    for e in fx["rw"]["events"]:
        k = e["kind"]
        if k == "manual":
            text = f"{e['actor'] or 'A person'} set it to {words.get(e['to'], e['to'])}" + (f": {e['note']}" if e.get("note") else "")
        elif k == "paid_on_later_remit":
            text = f"Closed automatically: {e.get('note') or 'paid on a later remittance'}"
        elif k == "reopened":
            text = "Reopened automatically: the payment it was closed on no longer stands"
        elif k == "hidden":
            text = f"Let go by a hide rule: {e.get('note') or ''}".rstrip(": ")
        elif k == "unhidden":
            text = "Brought back: the hide rule was removed"
        elif k == "signal_changed":
            text = f"Carried over from an earlier item: {e.get('note') or ''}".rstrip(": ")
        elif k == "packet":
            text = "Appeal packet built" + (f": {e['note']}" if e.get("note") else "")
        elif k == "note_lookup":
            text = "Visit note looked up" + (f": {e['note']}" if e.get("note") else "")
        elif k in ("override_carried", "confirmed_carried"):
            continue
        else:
            text = e.get("note") or k.replace("_", " ")
        out.append(_row(e["at"], "remitwatch", k, text + ".", tags=["rw:" + k], amount=e.get("cents") if k == "manual" else None))
    for x in fx["rw"]["faxes"]:
        when = x.get("sent_at") or x.get("created_at")
        if (x.get("direction") or "out") == "in":
            out.append(_row(when, "remitwatch", "fax_in", f"Fax received from {x.get('payer_group') or 'the payer'}.", tags=["fax_in"]))
            continue
        out.append(_row(when, "remitwatch", "fax", f"Faxed to {x.get('payer_group') or 'the payer'} ({(x.get('purpose') or 'appeal').replace('_', ' ')}, "
                        f"level {x.get('level') or 1}" + (f", fax line ending {x['number_tail']}" if x.get("number_tail") else "")
                        + f"): {x.get('status') or 'sent'}.", tags=["fax"]))
    if f.get("notes"):
        out.append(_row(f.get("status_changed_at"), "remitwatch", "note", f"Note on the item: {f['notes']}", tags=["note"]))
    if f.get("auto_note") and f.get("status") == "resolved":
        out.append(_row(f.get("status_changed_at"), "remitwatch", "auto_note", f"Closed by the analysis: {f['auto_note']}", tags=["auto_note"]))
    if f.get("hint"):
        out.append(_row(None, "remitwatch", "hint", f"RemitWatch's hint: {f['hint']}.", tags=["hint"]))
    return out


# ------------------------------------------------------------------------------------------------- the balance line
def _balance(conn: sqlite3.Connection, fx: dict, itemized: dict, rows: list[dict]) -> dict:
    """charge = insurer paid + patient paid + contractual + written off + open (a credit is a negative open), from EMA's
    postings when EMA holds the line, else from the 835's standing answer; then checked against what else states an
    open amount: the itemized rows, the A/R (when no posting came after its pull), the 835's payments (each must be
    posted in EMA), patient money EMA cannot tie to one claim, and a code billed twice on the claim's date."""
    f = fx["item"]
    tot = S._ema_line_totals(fx)
    out: dict = {"basis": None, "as_of": None, "charge_cents": 0, "insurer_paid_cents": 0, "patient_paid_cents": 0,
                 "contractual_cents": 0, "written_off_cents": 0, "patient_adjusted_cents": 0, "open_cents": 0, "credit": False,
                 "checks": [], "reconciles": True, "gap_cents": 0, "gap_cause": None, "text": ""}
    gaps: list[tuple[int, str]] = []
    if tot["n"]:
        out.update(basis="production", as_of=max((ln.get("as_of") or "" for ln in fx["ema"]["lines"]), default=None),
                   charge_cents=tot["charges"], insurer_paid_cents=tot["ins_paid"], patient_paid_cents=tot["pat_paid"],
                   contractual_cents=tot["contractual"], written_off_cents=tot["noncontractual"], patient_adjusted_cents=tot["pat_adj"])
        computed = tot["charges"] - tot["ins_paid"] - tot["pat_paid"] - tot["contractual"] - tot["noncontractual"] - tot["pat_adj"]
        out["open_cents"] = computed
        if computed != tot["balance"]:
            gaps.append((tot["balance"] - computed, f"EMA's summary shows {usd(tot['balance'])} open; its parts add up to {usd(computed)}"))
        # the rows listed add up to the summary
        for k, label in (("charges", "charges"), ("ins_paid", "insurance payments"), ("contractual", "contractual adjustments"),
                         ("noncontractual", "non-contractual adjustments"), ("pat_paid", "patient payments")):
            want = {"charges": tot["charges"], "ins_paid": tot["ins_paid"], "contractual": tot["contractual"],
                    "noncontractual": tot["noncontractual"], "pat_paid": tot["pat_paid"]}[k]
            if itemized and itemized.get(k, 0) != want:
                gaps.append((want - itemized.get(k, 0), f"the {label} listed ({usd(itemized.get(k, 0))}) differ from EMA's summary ({usd(want)}): "
                                                         "the summary and the ledger were read at different times"))
        out["checks"].append({"source": "rows", "agrees": not any("listed" in g[1] for g in gaps)})
        # the A/R's balance, when no posting on the line came after its pull
        last_post = max((ln.get("last_posted") or "" for ln in fx["ema"]["lines"]), default="")
        ar_now: dict[tuple, dict] = {}
        for a in fx["ar"]:
            if (a.get("party") or "").lower().startswith("insur") or (a.get("party") or "").lower().startswith("pat"):
                k = (a.get("svc"), a.get("cpt"))
                if k not in ar_now or (a.get("as_of") or "") > (ar_now[k].get("as_of") or ""):
                    ar_now[k] = a
        lines_keys = {(ln["svc"], ln["cpt"]) for ln in fx["ema"]["lines"]}
        ar_rows = [a for k, a in ar_now.items() if k in lines_keys]
        if ar_rows:
            as_of = max(a.get("as_of") or "" for a in ar_rows)
            ar_open = sum(a.get("exposure") or 0 for a in ar_rows)
            if last_post <= as_of:
                agrees = ar_open == computed
                out["checks"].append({"source": "ar", "as_of": as_of, "open_cents": ar_open, "agrees": agrees})
                if not agrees:
                    gaps.append((computed - ar_open, f"EMA's A/R of {day(as_of)} shows {usd(ar_open)} open on these lines; the postings add up to "
                                                     f"{usd(computed)}"))
            else:
                out["checks"].append({"source": "ar", "as_of": as_of, "open_cents": ar_open, "agrees": None,
                                      "why": f"postings after the A/R of {day(as_of)}"})
        # every 835 payment of the service should be in EMA under its check
        posted_checks = {p["check_tail"] for p in fx["ema"]["pays"]}
        posted_traces = {p.get("trace_key") for p in fx["ema"]["pays"] if p.get("trace_key")}
        for x in S.standing_rows(fx["r835"]["rows"]):
            if (x["paid"] or 0) > 0 and x.get("line_key") and x.get("check_tail") and x.get("tk") not in posted_traces \
                    and x["check_tail"] not in posted_checks and str(x.get("method") or "").upper() != "NON":
                gaps.append((x["paid"], f"the 835 of {day(x['date'])} paid {usd(x['paid'])} on {x['code']} (check ending {x['check_tail']}), "
                                        "and EMA has not posted it"))
        if tot["several"]:
            unj = sum(r["amount_cents"] or 0 for r in rows if "patient_unjoined" in r["tags"])
            why = ("patient money for this patient, date and code cannot be tied to one claim (the surgery center's facility claim, or "
                   "the same code billed twice, shares the key)")
            if unj:
                gaps.append((unj, f"{why}: {usd(unj)} of it is on file and not counted on this line"))
            else:
                out["checks"].append({"source": "patient", "agrees": None, "why": why + "; any such money is listed on the server"})
        if not S.code_once(fx):
            own = S.own_rows(fx)
            own_charge = sum(x["charge"] or 0 for x in own if x.get("line_key"))
            if own_charge and own_charge != tot["charges"]:
                gaps.append((tot["charges"] - own_charge, f"the claim carries {f.get('procedure_code')} more than once on {day(f.get('service_date'))}: "
                                                          f"EMA's figures ({usd(tot['charges'])} charged) cover all of them"))
    else:
        stand = [x for x in S.standing_rows(fx["r835"]["rows"]) if x.get("line_key")]
        own = S.own_rows(fx)
        base = [x for x in own if x.get("line_key") and not x.get("reversed_by")] or stand
        if not base and any(x.get("line_key") for x in own):
            base = [x for x in own if x.get("line_key")]
            out["reversed_only"] = True
        if base:
            x = max(base, key=lambda r: (r["date"] or "", r["line_key"] or ""))
            co = sum(a for g, c, a in x["adj"] if g == "CO" and c in ("45", "253", "144", "59", "131", "94", "237", "2", "23"))
            pr = sum(a for g, c, a in x["adj"] if g == "PR")
            other = sum(a for g, c, a in x["adj"]) - co - pr
            out.update(basis="835", as_of=x["date"], charge_cents=x["charge"] or 0, insurer_paid_cents=x["paid"] or 0,
                       contractual_cents=co, written_off_cents=0, patient_paid_cents=0)
            out["patient_owes_cents"] = pr
            out["denied_cents"] = other
            out["open_cents"] = (x["charge"] or 0) - (x["paid"] or 0) - co - pr
            gap = (x["charge"] or 0) - (x["paid"] or 0) - sum(a for g, c, a in x["adj"])
            out["checks"].append({"source": "835", "agrees": gap == 0})
            if gap:
                gaps.append((gap, f"the 835 line of {day(x['date'])} does not balance: billed {usd(x['charge'])}, paid {usd(x['paid'])}, "
                                  f"adjusted {usd(sum(a for g, c, a in x['adj']))}"))
            if not fx["ema"]["lines"]:
                o = fx.get("outcome") or {}
                why = {"not_covered": "EMA's Production reports do not cover this date of service",
                       "not_found": "this line is not tied to its EMA claim", "shared_line": "the claim carries this code more than once"}.get(
                    o.get("outcome") or "", "EMA's postings for this line are not on file here")
                out["checks"].append({"source": "production", "agrees": None, "why": why})
        else:
            out["basis"] = "none"
    out["gap_cents"] = sum(g for g, _w in gaps)
    out["gap_items"] = [{"cents": g, "cause": w} for g, w in gaps]
    out["reconciles"] = not gaps
    out["gap_cause"] = "; ".join(w for _g, w in gaps) or None
    out["credit"] = out["open_cents"] < 0
    out["text"] = _balance_text(out)
    return out


def _balance_text(b: dict) -> str:
    if b["basis"] == "production":
        parts = [f"insurer paid {usd(b['insurer_paid_cents'])}", f"patient paid {usd(b['patient_paid_cents'])}",
                 f"contractual {usd(b['contractual_cents'])}", f"written off {usd(b['written_off_cents'])}"]
        if b.get("patient_adjusted_cents"):
            parts.append(f"patient adjustments {usd(b['patient_adjusted_cents'])}")
        open_ = (f"a credit of {usd(-b['open_cents'])}" if b["open_cents"] < 0 else f"open {usd(b['open_cents'])}")
        head = f"Charge {usd(b['charge_cents'])} = " + " + ".join(parts) + f" + {open_} (EMA's postings through {day(b['as_of'])})."
    elif b["basis"] == "835":
        head = (f"Charge {usd(b['charge_cents'])} = paid {usd(b['insurer_paid_cents'])} + contractual {usd(b['contractual_cents'])} + "
                f"patient's share {usd(b.get('patient_owes_cents'))} + denied {usd(b.get('denied_cents'))} (the 835 of {day(b['as_of'])}; "
                "EMA's postings for it are not on file).")
    else:
        return "No line of money to balance: this item is about a payer, a rate or a fee, not one service."
    if b["reconciles"]:
        return head + " It reconciles to the cent."
    return head + f" It does not reconcile: {usd(b['gap_cents'])} apart, because " + (b["gap_cause"] or "of a difference no source explains") + "."


# ---------------------------------------------------------------------------------------------- gathering one item
def _gather(conn: sqlite3.Connection, f: dict, today: str | None) -> dict:
    t = S._today(today)
    fx = S.facts_of(conn, [f], today=t)[f["finding_id"]]
    fx["ctx"]["response"] = S.response_table(conn)
    return fx


def _story(conn: sqlite3.Connection, fx: dict, ledger: Any) -> dict:
    rows: list[dict] = []
    rows += _rows_835(fx)
    prod, itemized, notes = _rows_production(conn, fx, ledger)
    rows += prod
    rows += _rows_ar(fx)
    rows += _rows_weekly(conn, fx)
    rows += _rows_remitwatch(fx)
    rows.sort(key=lambda r: (r["date"] is None, r["date"] or "", _SRC_ORDER.get(r["source"], 9), r["kind"]))
    for i, r in enumerate(rows, 1):
        r["n"] = i
    if fx["ar"]:
        notes.append(AR_WINDOW)
    o = fx.get("outcome") or {}
    if o.get("outcome") == "not_covered":
        notes.append("EMA's Production reports do not cover this date of service (they hold 2025 services on), so EMA must be "
                     "checked by hand for this line.")
    bal = _balance(conn, fx, itemized, rows)
    twin = _twin(fx)
    if twin:
        notes.append(twin["note"])
    return {"rows": rows, "balance": bal, "reconciles": bal["reconciles"], "gap_cents": bal["gap_cents"], "twin": twin, "notes": notes,
            "mode": "server" if ledger is not None else "summary", "finding_id": fx["item"]["finding_id"]}


def _twin(fx: dict) -> dict | None:
    tw = fx["ema"]["twins"]
    if not tw:
        return None
    paid = sum(t.get("ins_paid") or 0 for t in tw)
    rows = [{"line": _line_label(t["cpt"], t["svc"]), "charges_cents": t.get("charges"), "insurer_paid_cents": t.get("ins_paid"),
             "contractual_cents": t.get("contractual"), "written_off_cents": t.get("noncontractual"), "patient_paid_cents": t.get("pat_paid_joined"),
             "open_cents": t.get("balance"), "status": t.get("status"), "link": t["link"]} for t in tw]
    return {"rows": rows, "insurer_paid_cents": paid, "note": (
        "The surgery center billed its own facility claim for the same patient, date and code"
        + (f"; insurance paid it {usd(paid)}" if paid else "") + ". That claim is the facility's, shown apart, and never counted on this line.")}


def item_story(conn: sqlite3.Connection, finding_id: str | None = None, *, claim_key: str | None = None, ledger: Any = None,
               today: str | None = None) -> dict:
    """The story of one item (finding_id) or of a whole claim (claim_key: an 835 claim key, a claim id, or 'link:<n>' for
    an EMA claim number): rows, the balance line, whether it reconciles and the gap. See the module docstring."""
    if finding_id:
        f = _load(conn, finding_id)
        if f is None:
            return {"rows": [], "balance": {}, "reconciles": False, "gap_cents": 0, "twin": None, "notes": ["No such item."]}
        return _story(conn, _gather(conn, f, today), ledger)
    if not claim_key:
        return {"rows": [], "balance": {}, "reconciles": False, "gap_cents": 0, "twin": None, "notes": ["Nothing named."]}
    f = _claim_item(conn, claim_key)
    out = _story(conn, _gather(conn, f, today), ledger)
    out["finding_id"] = None
    out["rows"] = [r for r in out["rows"] if r["source"] != "remitwatch" or r["kind"] != "raised"]
    return out


def _claim_item(conn: sqlite3.Connection, claim_key: str) -> dict:
    """A stand-in item for a whole claim: every code and date the claim's lines carry."""
    f: dict = {"finding_id": f"claim:{claim_key}", "signal": "CLAIM", "category": "claim", "status": "open"}
    lines: list = []
    if str(claim_key).startswith("link:"):
        lk = int(str(claim_key)[5:])
        f["ema_link"] = lk
        if S._table(conn, "ema_lines"):
            for svc, cpt, cid in conn.execute("SELECT svc, cpt, claim_id FROM ema_lines WHERE link=?", (lk,)):
                lines.append([cpt, svc])
                f.setdefault("patient_control_number", cid)
    else:
        r = conn.execute("SELECT claim_key, patient_control_number, payer_name, statement_from FROM claims WHERE claim_key=? OR patient_control_number=? "
                         "ORDER BY payment_date DESC LIMIT 1", (claim_key, claim_key)).fetchone() if S._table(conn, "claims") else None
        if r:
            f.update(claim_key=r[0], patient_control_number=r[1], payer_name=r[2], service_date=r[3])
            for svc, cpt in conn.execute("SELECT service_date, procedure_code FROM lines WHERE claim_key=?", (r[0],)):
                lines.append([cpt, svc])
        else:
            f["patient_control_number"] = claim_key
    f["evidence"] = json.dumps({"lines": lines})
    f["payer_group"] = S._grp_kind(f.get("payer_name"))[0] if f.get("payer_name") else None
    return f


# ------------------------------------------------------------------------------------------------------- the verdict
def _mark(rows: list[dict], *tags: str, kinds: Sequence[str] = (), limit: int | None = None) -> list[int]:
    hit = [r for r in rows if any(t in r["tags"] for t in tags) or (kinds and r["kind"] in kinds)]
    if limit:
        hit = hit[-limit:]
    for r in hit:
        r["decisive"] = True
    return [r["n"] for r in hit]


def _refs(ns: Sequence[int]) -> str:
    ns = sorted(set(ns))
    if not ns:
        return ""
    if len(ns) == 1:
        return f"(row {ns[0]})"
    if ns == list(range(ns[0], ns[-1] + 1)):
        return f"(rows {ns[0]}-{ns[-1]})"
    return "(rows " + ", ".join(str(n) for n in ns) + ")"


def _state_of(conn: sqlite3.Connection, fx: dict, today: str | None) -> dict:
    """The state the detection run stored on the item, with state_of's reasons; state_of's own answer when none is stored."""
    t = S._today(today)
    fx["_conn"] = conn
    try:
        live = S._public(S._decide(fx, t))
    finally:
        fx.pop("_conn", None)
    stored = S.stored_state(fx["item"])
    if stored:
        live = dict(live, stored=True, work_state=stored["work_state"], follow_up_on=stored.get("follow_up_on") or live.get("follow_up_on"),
                    state_basis=stored.get("state_basis") or live.get("state_basis"), label=stored["label"])
    return live


def _verdict(conn: sqlite3.Connection, fx: dict, story: dict, st: dict, today: str | None) -> dict:
    f = fx["item"]
    rows = story["rows"]
    o = fx.get("outcome") or {}
    state, rule = st["work_state"], st.get("rule") or ""
    proof = st.get("proof") or {}
    t = S._today(today)
    deadline = st.get("deadline")
    strength, why = "one_source", None
    sent = ""
    ns: list[int] = []
    oc = o.get("outcome")
    ema_resolved = (o.get("status_class") in ("paid", "settled")) or str(o.get("status") or "").lower().startswith("resolved")
    if state == "done":
        if rule == "paid_later_ema":
            ns = _mark(rows, f"pay:{proof.get('date')}:{proof.get('check_tail')}")
            sent = f"Paid later: {usd(proof.get('cents'))} by {proof.get('payer') or 'the payer'} on a check with no 835 {_refs(ns)}."
            strength = "one_source"
            if any("ar" in r["tags"] for r in rows) or story["balance"].get("open_cents", 1) <= 0:
                strength = "proven" if story["balance"].get("open_cents", 1) <= 0 and any(r["source"] == "835" for r in rows) else "one_source"
        elif rule == "rebilled_paid":
            ns = _mark(rows, f"pay:{proof.get('date')}:{proof.get('check_tail')}")
            sent = (f"Rebilled to {proof.get('payer') or 'another payer'}, paid: {usd(proof.get('cents'))} on {day(proof.get('date'))} on a check "
                    f"with no 835 {_refs(ns)}; EMA shows the line settled.")
        elif rule in ("paid_later_835", "paid_in_full_split"):
            ns = [r["n"] for r in rows if r["source"] == "835" and r["kind"] == "payment" and (r["date"] or "") >= (f.get("payment_date") or "")]
            for r in rows:
                if r["n"] in ns:
                    r["decisive"] = True
            sent = proof.get("text", "Paid") + f" {_refs(ns)}."
            strength = "proven" if any(r["source"] == "production" and r["kind"] == "payment" for r in rows) else "one_source"
        elif rule in ("moved_to_patient",):
            ns = _mark(rows, "reversal") + [r["n"] for r in rows if r["source"] == "835" and r["date"] == proof.get("date")]
            ns += _mark(rows, "patient", limit=3)
            for r in rows:
                if r["n"] in ns:
                    r["decisive"] = True
            sent = "Moved to the patient: " + proof.get("text", "") + f" {_refs(ns)}."
            strength = "proven" if any("patient" in r["tags"] for r in rows) else "one_source"
        elif rule in ("paid_before_other_payer", "paid_before_duplicate", "replacement_denied_paid"):
            ns = [r["n"] for r in rows if (r["source"] in ("835", "production") and r["kind"] == "payment" and r["date"] == proof.get("date"))]
            ns += _mark(rows, "own_remit")
            for r in rows:
                if r["n"] in ns:
                    r["decisive"] = True
            sent = proof.get("text", "Already paid") + f" {_refs(ns)}."
            strength = "proven" if proof.get("source") == "835" and any(r["source"] == "production" and r["kind"] == "payment" for r in rows) else "one_source"
        elif rule in ("claim_paid_in_ema", "paid_in_full_ema"):
            ns = _mark(rows, "pay_no835", "pay_835")
            sent = proof.get("text", "Paid") + f" {_refs(ns)}."
        elif rule == "auto_closed":
            ns = _mark(rows, "auto_note", "rw:paid_on_later_remit")
            sent = f"Closed by the analysis: {f.get('auto_note')} {_refs(ns)}."
            strength = "one_source"
        elif rule == "person_paid":
            ns = _mark(rows, "rw:manual", limit=1)
            sent = f"{st['state_basis']} {_refs(ns)}."
            strength, why = "one_source", "a person's status"
        else:
            sent = st.get("state_basis") or "Done."
    elif state == "waiting":
        ns = _mark(rows, "work", "fax", "rw:manual", "ar_note", limit=2)
        sent = f"Waiting: {st['state_basis']} {_refs(ns)}."
    elif state == "accepted":
        ns = _mark(rows, "rw:manual", "rw:hidden", limit=1)
        sent = f"{st['state_basis']} {_refs(ns)}.".replace(" .", ".")
        strength = "one_source"
        if rule == "policy":
            why = "the owner's write-off policy"
    elif state == "expired":
        ns = _mark(rows, "own_remit")
        sent = (f"Expired: the last window closed on {day(deadline)} ({_short(st.get('deadline_basis')) or 'the payer window'}), more than 30 "
                f"days ago, and nothing is pending {_refs(ns)}.")
        strength, why = "inferred", "past every window plus 30 days"
    elif state == "cant_check":
        sent = f"Can't be checked: {st['state_basis']}"
        strength, why = "inferred", "no source carries this money"
    elif st.get("rule") in ("owner_decision", "fees", "post_payment") or f.get("signal") in S.CONTRACT + S.FEES:
        ns = _mark(rows, "raised", "own_remit", limit=2)
        sent = f"{st.get('state_basis')}: {f.get('title') or ''} {_refs(ns)}."
        strength = "one_source"
    else:
        sent, ns, strength, why = _work_now_sentence(conn, fx, story, st, t)
    dup = _duplicates(conn, f)
    if dup:
        sent += f" Listed twice: the same service as item {dup[0]['finding_id']} ({dup[0]['signal']})."
    tw = story.get("twin")
    if tw and tw["insurer_paid_cents"] and state != "done" and not (fx["ema"]["pays"] and any((p["cents"] or 0) > 0 for p in fx["ema"]["pays"])):
        sent += f" The payment on this day belongs to the surgery center's facility claim ({usd(tw['insurer_paid_cents'])}), not this line."
    if st.get("person") and st.get("proof") and state != "done":
        sent += " A person's status decides this item: " + (st["proof"].get("text") or "") + ": mark it Paid if that settles it."
    return {"state": state, "label": S.LABELS.get(state, ""), "sentence": _clean(sent), "rows": sorted(set(ns)), "strength": strength,
            "rule": why or rule, "code": rule, "follow_up_on": st.get("follow_up_on"), "state_basis": st.get("state_basis"),
            "stored": bool(st.get("stored")), "deadline": deadline}


def _short(basis: Any, n: int = 160) -> str:
    """A deadline's basis cut to its first sentence, for sentences that name it."""
    b = str(basis or "").strip()
    m = re.search(r"(?<=[a-z0-9)])[.;] ", b)
    b = b[:m.start()] if m else b.rstrip(".")
    return b if len(b) <= n else b[:n].rsplit(" ", 1)[0] + "..."


def _carcs(fx: dict) -> str:
    """The item's reasons with their groups, as its own remittance gives them (CO-97, not 97)."""
    f = fx["item"]
    o = fx.get("outcome") or {}
    if o.get("carcs"):
        return ", ".join(o["carcs"])
    carc = str(f.get("carc") or "")
    for x in S.own_rows(fx):
        for g, c, _a in list(x["adj"]) + list(x["cadj"]):
            if c == carc:
                return f"{g}-{c}"
    return f"reason {carc}" if carc else "the denial"


def _clean(s: str) -> str:
    s = re.sub(r"\s+\(\)", "", s)
    s = re.sub(r"\s+\.", ".", s)
    s = s.replace("..", ".").replace("—", ",").replace("–", "-")
    return re.sub(r"\s{2,}", " ", s).strip()


def _work_now_sentence(conn: sqlite3.Connection, fx: dict, story: dict, st: dict, today: str) -> tuple[str, list[int], str, str | None]:
    f = fx["item"]
    rows = story["rows"]
    o = fx.get("outcome") or {}
    oc = o.get("outcome")
    rule = st.get("rule") or ""
    sig = f.get("signal") or ""
    deadline = st.get("deadline")
    ns: list[int] = []
    strength, why = "one_source", None
    window = (f" You can still appeal until {day(deadline)}." if deadline and deadline >= today else
              (f" The appeal window closed on {day(deadline)}." if deadline else ""))
    resolved = str(o.get("status") or "").lower().startswith("resolved") or o.get("status_class") in ("paid", "settled")
    carcs = _carcs(fx)
    wo = usd(o.get("written_off") or o.get("contractual_routine"))
    void = [r for r in rows if r["kind"] == "charge_void"]
    hint = str(f.get("hint") or "")
    if void and story["balance"].get("charge_cents", 1) <= 0:
        ns = [r["n"] for r in void] + _mark(rows, "own_remit")
        for r in void:
            r["decisive"] = True
        return (f"Check first: EMA reversed this charge on {day(void[0]['date'])} {_refs(ns)}, so the claim was voided or re-created and the "
                "service may be billed and paid on another claim" + (f" ({hint})" if hint else "") + ".", ns, "one_source", None)
    if hint.startswith(("Possibly paid", "Processed on claim", "Partly paid")):
        ns = _mark(rows, "hint", "own_remit")
        return (f"Check first: {hint} {_refs(ns)}. The analysis did not close it because the proof was not complete.", ns, "inferred",
                "a payment found that does not meet every closing test")
    if rule in ("work_answered", "work_stale", "follow_up_due"):
        ns = _mark(rows, "work", "fax", "ar_note", "rw:manual", limit=2)
        if st.get("answered"):
            ns += [r["n"] for r in rows if r["date"] == st["answered"].get("date") and r["source"] in ("835", "production")]
        for r in rows:
            if r["n"] in ns:
                r["decisive"] = True
        return f"Work now: {st['state_basis']} {_refs(ns)}.", ns, "one_source", None
    if rule == "credit_refund":
        ns = _mark(rows, "pay_no835", "pay_835", limit=3)
        return (f"Overpaid: {st['state_basis']} {_refs(ns)}. The owner decides whether to refund it or let the payer recoup it.", ns,
                "one_source", None)
    if rule in ("bill_patient",):
        ns = _mark(rows, "reversal") + _mark(rows, "patient", limit=2)
        return f"Moved to the patient, not yet collected: {st['state_basis']} {_refs(ns)}.", ns, "one_source", None
    if rule in ("forwarded", "forwarded_late"):
        ns = _mark(rows, "own_remit")
        return f"{st['state_basis']} {_refs(ns)}.", ns, "one_source", None
    if sig == "NO_ANSWER":
        ev = S._ev(f)
        waited = ev.get("days_waiting") or S._days(ev.get("sent") or f.get("service_date"), today)
        tat = ev.get("turnaround_days") or _turnaround(conn, f.get("payer_group"))
        both = not fx["r835"]["rows"] and not fx["ema"]["pays"]
        ns = _mark(rows, "charge", "claim_status", limit=2)
        return (f"Unanswered: no answer from any payer in {waited} days; this payer usually answers within {tat} days (95% of its claims) "
                f"{_refs(ns)}.", ns,
                "proven" if both and fx["ema"]["lines"] else "one_source", None)
    if sig == "REMIT_NOT_POSTED":
        ns = _mark(rows, "own_remit")
        return (f"The 835 paid {usd(f.get('shortfall_cents'))} {_refs(ns)}, but EMA never posted it.", ns, "proven", None)
    if sig in ("REJECTED_PRE_PAYER", "NOT_SENT"):
        ns = _mark(rows, "ar", "ema_status", "claim_status", limit=2)
        word = "Never reached the payer" if sig == "REJECTED_PRE_PAYER" else "Never left EMA"
        return f"{word}: {f.get('title') or ''} {_refs(ns)}.", ns, "one_source", None
    if oc == "auto_written_off":
        proven = o.get("evidence_level") == "rule_proven"
        ns = _mark(rows, "adj_at_posting") + _mark(rows, "own_remit") + (_mark(rows, "era") if proven else [])
        lead = ("EMA shows this claim as Resolved, but nobody worked this line." if resolved else
                "EMA shows this line closed, but nobody worked it.")
        evid = "Recorded by EMA's ERA export." if proven else "Inferred from the posting date."
        return (f"{lead} EMA's posting rule wrote off the denial ({carcs}, {wo}) on {day(o.get('outcome_on'))}, the day the payer's remittance "
                f"was posted. The payer paid nothing.{window} {evid} {_refs(ns)}", ns, "proven" if proven else "inferred",
                None if proven else "posting day: an adjustment posted the day the remittance posted is EMA's posting rule")
    if oc == "written_off_while_pending":
        ns = _mark(rows, "adj_at_posting") + _mark(rows, "own_remit")
        asked = "information" if any(c.endswith(("-16", "-252", "-226")) for c in (o.get("carcs") or [])) else "a review"
        return (f"EMA wrote this off the day it posted, but the payer had not finished: it asked for {asked}. This can usually be corrected "
                f"and sent again.{window} {_refs(ns)}", ns, "inferred", "posting day")
    if oc == "lost_to_timely_filing":
        first = next((r for r in rows if r["source"] == "835" and r["kind"] == "adjudication"), None)
        first_carc = (first["codes"][0]["code"] if first and first["codes"] else f.get("carc")) or "another reason"
        ns = _mark(rows, "adj:CO-29") + ([first["n"]] if first else [])
        return (f"This was written off for late filing. The payer first denied it for {first_carc}; it ran out of time before anyone fixed it. "
                f"{_refs(ns)}", ns, "one_source", None)
    if oc in ("written_off_later", "contractual_later"):
        ns = _mark(rows, "adj_later") + _mark(rows, "own_remit")
        return (f"A person wrote this off in EMA on {day(o.get('outcome_on'))} ({carcs}, {wo}), on a day with no remittance posted, while the "
                f"payer had paid nothing; RemitWatch keeps it on the list while it can still be recovered (owner policy: pursue).{window} {_refs(ns)}",
                ns, "one_source", None)
    if oc in ("written_off_at_posting", "contractual_at_posting", "contractual_later") and o.get("contractual_routine") and not o.get("written_off"):
        ns = _mark(rows, "adj_at_posting", "adj_later") + _mark(rows, "own_remit")
        when = "the day the remittance posted" if o.get("written_off_when") == "at_posting" else f"on {day(o.get('outcome_on'))}, later"
        if f.get("category") in S.SHORT:
            return (f"EMA posted the shortfall as a routine contractual adjustment {when} {_refs(ns)}: nobody asked the payer about the rate.{window}",
                    ns, "one_source", None)
        return (f"EMA closed this line with a routine contractual adjustment ({usd(o.get('contractual_routine'))}) {when} {_refs(ns)}, while "
                f"the payer paid nothing for it ({carcs}).{window}", ns, "one_source", None)
    if oc in ("written_off_at_posting", "contractual_at_posting"):
        ns = _mark(rows, "adj_at_posting") + _mark(rows, "own_remit")
        if f.get("category") in S.SHORT:
            return (f"EMA posted the shortfall as a routine contractual adjustment the day the remittance posted {_refs(ns)}: nobody asked the "
                    f"payer about the rate.{window}", ns, "one_source", None)
        return (f"EMA wrote off {carcs} ({wo}) the day the remittance posted {_refs(ns)}; the payer paid nothing.{window}", ns, "inferred",
                "posting day")
    if oc == "paid_after_partial":
        ns = _mark(rows, "pay_no835", "pay_835", limit=2) + _mark(rows, "own_remit")
        return (f"Partly paid: EMA shows {usd((o.get('paid_after_same') or 0) + (o.get('paid_after_other') or 0))} paid since {_refs(ns)}, less "
                f"than half of what this should bring.{window}", ns, "one_source", None)
    if oc == "paid_before" or rule == "paid_before":
        ns = _mark(rows, "own_remit") + [r["n"] for r in rows if r["kind"] == "payment" and (r["date"] or "") < (f.get("payment_date") or "")]
        for r in rows:
            if r["n"] in ns:
                r["decisive"] = True
        return (f"Check first: this line was paid before this denial {_refs(ns)}; the denial may be a duplicate or a second payer's answer.",
                ns, "one_source", None)
    ns = _mark(rows, "own_remit")
    open_ = story["balance"].get("open_cents")
    if story["balance"].get("basis") == "production":
        ns += _mark(rows, "ar", limit=1)
        return (f"Unpaid and nobody has worked it: {f.get('payer_group') or 'the payer'} answered {carcs} {_refs(ns)}, and EMA shows "
                f"{usd(open_)} still open.{window}", ns, "proven" if fx["r835"]["rows"] else "one_source", None)
    return (f"Unpaid and nobody has worked it: {f.get('payer_group') or 'the payer'} answered {carcs} {_refs(ns)}.{window}", ns, "one_source", None)


def _turnaround(conn: sqlite3.Connection, group: str | None) -> int:
    """The payer group's 95th-percentile answer time from the date of service (ema_checks.turnaround)."""
    try:
        from . import ema_checks as ec
        return ec.turnaround_days(conn, group, "svc_p95")
    except Exception:       # noqa: BLE001  (no EMA tables or meta on this copy)
        return S.response_days(conn, group)


def _duplicates(conn: sqlite3.Connection, f: dict) -> list[dict]:
    """Other open items for the same service (bill, date and code): the same service listed twice."""
    if not f.get("patient_control_number") or not f.get("procedure_code") or not f.get("service_date"):
        return []
    b = S.bill_of(f["patient_control_number"])
    out = []
    for fid, sig, pcn, pd in conn.execute(
            """SELECT finding_id, signal, patient_control_number, payment_date FROM findings
               WHERE patient_control_number >= ? AND patient_control_number < ? AND service_date = ? AND procedure_code = ?
                 AND finding_id <> ? AND COALESCE(stale, 0) = 0 AND status IN ('open','in_progress','appealed')""",
            (b, b + "~", f["service_date"], f["procedure_code"], f["finding_id"])):
        if S.bill_of(pcn) == b:
            out.append({"finding_id": fid, "signal": sig, "payment_date": pd})
    return sorted(out, key=lambda d: d.get("payment_date") or "")


def item_verdict(conn: sqlite3.Connection, finding_id: str, *, ledger: Any = None, today: str | None = None) -> dict:
    """The item's state as plain sentences naming the decisive story rows, with the strength of the proof:
    {'state', 'sentence', 'rows', 'strength', 'rule', 'label', 'follow_up_on', 'state_basis', 'stored'}."""
    return explain(conn, finding_id, ledger=ledger, today=today)["verdict"]


# ----------------------------------------------------------------------------------------------------- the next step
def _fax_number(conn: sqlite3.Connection, f: dict) -> tuple[bool, str]:
    """Whether a confirmed fax number for the payer's appeals is on file (a send then goes there, preview first)."""
    try:
        from . import fax
        nb = fax.number_for(conn, f.get("payer_group"), f.get("payer_kind"), 1)
        if fax.shared_group(f.get("payer_group")):
            return False, fax.shared_why(f.get("payer_group"))
        if nb.get("number") and nb.get("auto_ok", 1):
            return True, f"Fax to {f.get('payer_group')} appeals at the confirmed number ending {nb['number'][-4:]}, preview first."
        if nb.get("number"):
            return False, f"{f.get('payer_group')}'s appeal fax number was changed and nobody has confirmed it: confirm it on the Faxes page."
        return False, f"No fax number is saved for {f.get('payer_group') or 'this payer'}: add it on the Faxes page, or send the packet the way the payer takes it."
    except Exception:       # noqa: BLE001  (no fax tables on this copy)
        return False, "No fax number is on file for this payer."


def _note_on_file(conn: sqlite3.Connection, f: dict) -> bool:
    try:
        if S._table(conn, "appeal_packets") and conn.execute("SELECT 1 FROM appeal_packets WHERE finding_id=? AND note_sha256 IS NOT NULL LIMIT 1",
                                                            (f["finding_id"],)).fetchone():
            return True
        if S._table(conn, "uploaded_notes") and f.get("patient_control_number"):
            return conn.execute("SELECT 1 FROM uploaded_notes WHERE status='matched' AND service_date=? AND claims LIKE ? LIMIT 1",
                                (f.get("service_date"), f"%{f['patient_control_number']}%")).fetchone() is not None
    except sqlite3.Error:
        return False
    return False


def _modmed_configured() -> bool:
    try:
        from . import modmed
        return modmed.Settings.from_env().configured()
    except Exception:       # noqa: BLE001
        return False


def _claim_url(conn: sqlite3.Connection, fx: dict) -> str | None:
    try:
        from . import ema_reports as er
        tpl = er.claim_url_template(conn)
    except Exception:       # noqa: BLE001
        tpl = None
    links = fx["ema"]["links"]
    if not tpl or not links:
        return None
    try:
        return tpl.format(n=links[0])
    except (KeyError, IndexError, ValueError):
        return None


def _payer_claim_number(fx: dict) -> str | None:
    own = S.own_rows(fx)
    for x in reversed(own):
        if x.get("pccn"):
            return x["pccn"]
    return None


def _same_day(conn: sqlite3.Connection, f: dict) -> list[str]:
    ev = S._ev(f)
    return [c for c in (ev.get("same_day_codes") or []) if c]


def _rarc_words(rarc: str | None) -> str:
    r = str(rarc or "")
    if not r:
        return ""
    return "; ".join(f"{x.strip()} ({_meaning(x.strip())})" if _meaning(x.strip()) else x.strip() for x in r.split(",") if x.strip())


_DIAGNOSIS_RARCS = {"MA63", "M64", "M76", "M81", "MA65"}
_RECORDS_RARCS = {"M127", "N29", "N706", "N393", "N395", "N457", "N461", "N791"}
_REFERRER_RARCS = {"N285", "N286", "N264", "N265"}


def _corrected_change(f: dict, fx: dict) -> str:
    """The exact change a corrected claim makes, by signal, reason and remark."""
    code = f.get("procedure_code") or "the line"
    sig = f.get("signal") or ""
    rarcs = {x.strip() for x in str(f.get("rarc") or "").split(",") if x.strip()}
    mods = str(f.get("modifiers") or "")
    pccn = _payer_claim_number(fx)
    tail_ = (f"; resubmit as a corrected claim, frequency 7, with the original claim number {pccn}" if pccn else
             "; resubmit as a corrected claim, frequency 7, with the payer's original claim number")
    if sig == "EM_BUNDLED_NO_MODIFIER":
        return f"Add modifier 25 to {code} (when the note shows a separate problem){tail_}"
    if sig == "EM_BUNDLED_MOD57_MINOR":
        return f"Change modifier 57 to 25 on {code}: the same-day procedures have a 0- or 10-day global period{tail_}"
    if sig == "DENIED_MISSING_MODIFIER":
        return f"Add or correct the modifier on {code} that the remark names ({_rarc_words(f.get('rarc')) or 'see the remittance'}){tail_}"
    if sig == "DENIED_CODING":
        if str(f.get("carc")) == "5" or "M77" in rarcs:
            return f"Fix the place of service on the claim of {code} (compare it with a claim of the same code this payer paid){tail_}"
        if "79" in mods:
            return (f"Check modifier 79 on {code}: a repair or closure of the same wound in the post-op period takes 58, an unrelated one keeps 79, "
                    f"a complication of the same surgery is in its global period{tail_}")
        return f"Correct the code or modifier combination on {code} ({f.get('carc') or ''} {_rarc_words(f.get('rarc'))}){tail_}".replace("( ", "(")
    if sig == "DENIED_MISSING_INFO":
        if rarcs & _DIAGNOSIS_RARCS:
            return f"List the treated condition's diagnosis first on {code} (the remark says the first diagnosis is missing or invalid){tail_}; repeat every line unchanged"
        if "N255" in rarcs:
            return f"Correct the billing provider's taxonomy on the claim of {code}{tail_}"
        if rarcs & _REFERRER_RARCS:
            return f"Add the referring or ordering provider's name and NPI to the claim of {code}{tail_}"
        if rarcs & _RECORDS_RARCS or str(f.get("carc")) in ("252", "226"):
            return f"Send the records the remark asks for ({_rarc_words(f.get('rarc'))}) with the claim of {code}"
        return f"Supply what the remark asks for ({_rarc_words(f.get('rarc')) or 'see the remittance'}) and resubmit{tail_}"
    if sig == "REJECTED_PRE_PAYER":
        return ("Open the claim in EMA and read the rejection message (claim history or the clearinghouse report); fix the field it names and "
                "send it again as a new claim (frequency 1): a rejected claim never reached the payer")
    if sig == "NOT_SENT":
        return "Send the claim from EMA (or fix what holds it in the batch) as a new claim (frequency 1)"
    return f"Correct the claim as the reason and remark codes say ({f.get('carc') or ''} {_rarc_words(f.get('rarc'))}){tail_}".replace("( ", "(")


def _appeal_needs(f: dict, fx: dict) -> tuple[list[str], str]:
    """What an appeal packet needs, and the argument, by signal, reason and code."""
    sig = f.get("signal") or ""
    code = f.get("procedure_code") or "the service"
    svc = day(f.get("service_date"))
    same = ", ".join(_same_day(None, f)) if _same_day(None, f) else ""
    carc = str(f.get("carc") or "")
    rarcs = {x.strip() for x in str(f.get("rarc") or "").split(",") if x.strip()}
    note = f"the visit note of {svc}"
    if sig.startswith("DOWNCODE") or sig == "CODE_CHANGED":
        return ([note, "the AMA CPT E/M table (medical decision making or time) with the rows that apply highlighted"],
                f"The note supports {f.get('original_procedure_code') or code} as billed: medical decision making (or time) meets that level.")
    if sig == "EM_BUNDLED_MOD25":
        return ([note + " showing a significant, separately identifiable problem", "the procedure note"],
                f"Modifier 25: {code} was a separate problem evaluated and managed beyond the pre-procedure work of {same or 'the same-day procedure'} "
                "(NCCI Policy Manual, chapter I).")
    if sig == "EM_BUNDLED_MOD57_MAJOR":
        return ([note + " showing the decision for surgery was made at this visit", "the operative note"],
                f"Modifier 57: the decision for the 90-day procedure ({same or 'the same-day surgery'}) was made at this visit, so the visit is not in "
                "the global package (CMS Claims Processing Manual ch. 12 sec. 40.2). If a biopsy visit before it made the decision, let it go.")
    if sig == "EM_BUNDLED_MOD24":
        return ([note + " showing a problem unrelated to the earlier surgery"],
                "Modifier 24: the visit treated a problem unrelated to the surgery whose post-op period it falls in; a wound check or suture "
                "removal of that surgery is in its global package (billed as 99024).")
    if sig in ("PROCEDURE_BUNDLED", "ADDON_BUNDLED"):
        if carc == "97" and "N525" in rarcs:
            return (["the operative note showing the procedure was unrelated to the earlier surgery"],
                    f"CO-97 with N525 puts {code} inside another surgery's global period: when it was unrelated, the right modifier is 79 "
                    "(not 59 or XS); send a corrected claim with 79 or appeal with the note.")
        return (["the operative or procedure note", "the NCCI edit table excerpt for the code pair (no edit, or modifier indicator 1)"],
                f"{code} is separately reportable: distinct lesion, site or session as documented; Mohs stages and the same surgeon's repairs are "
                "separately reportable under CPT and NCCI.")
    if sig == "DENIED_MEDICAL_NECESSITY":
        return ([note, "the pathology report", "the coverage policy (LCD) with the criteria met highlighted"],
                "The service meets the coverage criteria: the diagnosis on the claim and the record show the indication. Check the diagnosis "
                "first: a wrong diagnosis code is fixed by a corrected claim (or a Medicare reopening), not an appeal.")
    if sig == "DENIED_LEVEL_OF_SERVICE":
        return (["the procedure note", "the pathology report"], "The records document each stage or component of the procedure as billed.")
    if sig == "DENIED_FREQUENCY":
        return ([note, "the documentation of each unit, site or session"], "The units billed were rendered and documented.")
    if sig == "DENIED_AUTH":
        return (["the authorization number or the portal page showing it"], "The service was authorized (or needed no authorization).")
    if sig == "DENIED_TIMELY_FILING":
        return (["the clearinghouse acceptance report (or 277CA) with the original submission date"], "The claim reached the payer in time.")
    if sig == "DENIED_PROVIDER":
        return (["the clinician's enrollment record with its effective date"], "The clinician was enrolled on the date of service.")
    return ([note], "The service is payable as billed; the attached record supports it.")


def _route_for(f: dict, fx: dict, st: dict) -> str:
    """The kind of work the item takes: appeal, corrected, call, post, bill_patient, refund, era, contract, decide, wait,
    nothing."""
    state = st["work_state"]
    sig = f.get("signal") or ""
    if state in ("done", "accepted", "expired"):
        return "nothing"
    if state == "waiting":
        return "wait"
    if state == "cant_check":
        return "call"
    rule = st.get("rule") or ""
    if rule == "credit_refund":
        return "refund"
    if rule == "bill_patient":
        return "bill_patient"
    if rule in ("work_answered", "work_stale", "follow_up_due", "forwarded_late"):
        return "call"
    if rule == "post_payment" or sig == "REMIT_NOT_POSTED":
        return "post"
    if sig in S.CONTRACT:
        return "contract"
    if sig in S.FEES:
        return "fees"
    if sig in ("NO_ANSWER", "DENIED_DUPLICATE_UNPAID"):
        return "call"
    if sig in ("REJECTED_PRE_PAYER", "NOT_SENT"):
        return "corrected"
    if sig == "EMA_DENIED":
        return "eob_first"
    if (fx.get("outcome") or {}).get("outcome") == "paid_before":
        return "decide"
    if str(f.get("hint") or "").startswith(("Possibly paid", "Processed on claim", "Partly paid")):
        return "decide"
    if sig in ("DENIED_NONCOVERED", "DENIED_OTHER", "CODE_CHANGED", "CLAIM_DENIED_UNPAID", "DENIED_MISSING_PRIMARY", "DENIED_PROVIDER",
               "DENIED_TIMELY_FILING", "DENIED_AUTH"):
        return "decide" if sig not in ("DENIED_AUTH", "DENIED_TIMELY_FILING") else "appeal"
    if f.get("actionable") == "correct_and_resubmit":
        return "corrected"
    if sig in ("UNDERPAID_VS_OWN_RATE", "MPPR_EXCESS", "SECOND_RATE"):
        return "rate"
    return "appeal"


def _next(conn: sqlite3.Connection, fx: dict, story: dict, st: dict, verdict: dict, today: str | None) -> dict:
    f = fx["item"]
    t = S._today(today)
    o = fx.get("outcome") or {}
    sig = f.get("signal") or ""
    grp = f.get("payer_group") or "the payer"
    route = _route_for(f, fx, st)
    deadline, basis = st.get("deadline"), st.get("deadline_basis")
    proof = st.get("proof") or {}
    buttons: list[str] = []
    steps: list[str] = []
    needs: list[str] = []
    who = "Biller"
    action = ""
    expected = _expected(f, fx)
    clocks = _clocks(f, fx, st)
    url = _claim_url(conn, fx)
    appeal_block = st.get("appeal_block")
    bal = story.get("balance") or {}
    if bal.get("credit") and not appeal_block:
        appeal_block = "credit"
    if route == "nothing":
        expected = 0
        if st["work_state"] == "done":
            action = "Nothing to do: " + (proof.get("text") or st.get("state_basis") or "it is settled") + "."
            if proof.get("watch"):
                steps.append("Watch for a takeback: if the payer recoups the payment, dispute it with the remittance that paid it.")
            if proof.get("rule") in ("paid_later_ema", "rebilled_paid", "claim_paid_in_ema") or (proof.get("source") == "production"):
                era = _era_request(fx, proof)
                if era:
                    steps.append(era)
                    buttons.append("request_era")
            if st.get("rule") in ("auto_closed",) or f.get("auto_note"):
                buttons.append("reopen")
            if f.get("status") == "open":
                steps.insert(0, "Close it in RemitWatch: Paid, with the amount and the note shown.")
        elif st["work_state"] == "accepted":
            action = "Nothing to do: " + (st.get("state_basis") or "a loss someone accepted") + "."
            buttons.append("reopen")
        else:
            action = f"Nothing to do: {st.get('state_basis') or 'past every window'}."
            if f.get("status") == "open":
                steps.append("Mark it Lost so it leaves the list (it stays findable).")
                buttons.append("let_go")
        who = "Biller"
        deadline, basis = None, None
    elif route == "wait":
        action = f"Wait for {grp}'s answer to the work already done; follow up on {day(st.get('follow_up_on'))} if nothing comes."
        steps.append(f"On {day(st.get('follow_up_on'))}, check the payer's portal or call about the claim, quoting what was sent and when.")
        buttons += ["log_outcome"]
        deadline, basis = st.get("follow_up_on"), "the payer's usual answer time after the work was seen"
        who = "A/R follow-up biller"
        expected = expected
    elif route == "refund":
        credit = st.get("credit_cents") or (-bal.get("open_cents", 0) if bal.get("open_cents", 0) < 0 else 0)
        payers = sorted({p["payer"] for p in fx["ema"]["pays"] if (p["cents"] or 0) > 0 and p["payer"]})
        action = (f"Overpaid by {usd(credit)}: decide whether to refund it or let {', '.join(payers) or grp} recoup it. Do not appeal or ask for "
                  "more on this line.")
        steps += [f"Find who overpaid: EMA shows insurance payments from {', '.join(payers) or grp}; compare them with each payer's allowed amount.",
                  "Call the payer (or use its portal) to confirm which plan is primary and whether it will recoup or wants a refund check.",
                  "The owner decides and signs the refund; post the refund or the recoupment in EMA so the credit clears."]
        who = "Billing lead; owner decides"
        buttons += ["copy_instructions", "log_outcome"]
        expected = 0
        deadline = None
        basis = "Commercial plans usually ask for an identified overpayment back within 30 days; Medicare within 60 days of finding it"
    elif route == "bill_patient":
        pr = proof.get("patient_cents") or bal.get("open_cents") or 0
        action = f"Bill the patient {usd(bal.get('open_cents') if bal.get('open_cents', 0) > 0 else pr)}: {proof.get('text') or st.get('state_basis')}."
        if f.get("payer_kind") == "medicaid":
            action = "Do not bill the patient: a Medicaid member cannot be billed for a covered service. " + (st.get("state_basis") or "")
        steps.append("Put the amount on the patient's next statement; the payer owes nothing more on this line.")
        who = "Patient accounts"
        buttons += ["mark_billed", "copy_instructions"]
        expected = max(0, bal.get("open_cents") or 0)
    elif route == "post":
        action = (f"Post the payment in EMA: {grp} paid {usd(f.get('shortfall_cents'))} on {day(f.get('payment_date'))} "
                  f"(trace ending {S._ev(f).get('trace_tail') or '?'}), and EMA has not posted it.")
        steps += ["Find the deposit on the bank statement (or the payer's portal) for that date and amount.",
                  "Post each claim line of the remittance in EMA under that trace: payment, contractual adjustment and patient share as the 835 shows.",
                  "If EMA posted it under another number, correct the number; if it never arrived, ask the payer to reissue it."]
        who = "Payment poster"
        buttons += ["mark_posted", "copy_instructions"]
        deadline, basis = None, "Post it now: the deposit is in the bank and nobody else will find it"
    elif route == "contract":
        action = ("A contract or billing-policy question for the owner, not a claim to appeal: bring it to the payer's contract review with the "
                  "rates and volumes shown." if sig != "G2211_DENIED" else
                  "Decide whether to keep billing G2211 to this payer (from 2027 it becomes modifier B1).")
        who = "Owner"
        buttons += ["copy_instructions"]
        deadline, basis = None, "the agreement's renewal or notice date"
    elif route == "fees":
        action = ("Ask the payer or its payment vendor for standard EFT with no fee, and for the fees withheld back." if sig == "FEE_SKIM" else
                  "Match the takeback to the payer's overpayment letter; if it was not owed, dispute it, and post it in EMA.")
        who = "Billing lead"
        buttons += ["build_packet", "copy_instructions", "mark_sent"]
    elif route == "call":
        action, steps, who = _call_step(f, fx, st, t)
        buttons += ["copy_instructions", "log_outcome"]
    elif route == "eob_first":
        action = (f"Get the EOB from {grp}'s portal (no 835 reached RemitWatch for this answer), then work the denial: "
                  + ("send a corrected claim." if f.get("actionable") == "correct_and_resubmit" else "appeal with the record."))
        steps += ["Download the EOB for the check that carried the denial (EMA shows the posting day).",
                  "Read its reason and remark codes; fix and resend, or appeal, as they say."]
        buttons += ["copy_instructions", "request_era", "mark_sent"]
    elif route == "corrected":
        action = _corrected_change(f, fx) + "."
        if f.get("payer_kind") == "medicare" and sig not in ("REJECTED_PRE_PAYER", "NOT_SENT"):
            from . import appeals
            nxt = appeals.next_step(dict(f, evidence=S._ev(f)))
            if nxt:
                action = nxt
        who = "Coder, then the biller" if sig in ("DENIED_MISSING_INFO", "DENIED_CODING") else "Biller"
        buttons += ["copy_instructions", "mark_sent"]
        steps.append("Send it from EMA through the clearinghouse and keep the acceptance report (proof of timely filing).")
    elif route == "rate":
        action = (f"Ask {grp} to reprocess {f.get('procedure_code')} at its usual rate ({usd(f.get('expected_cents'))}); one request can list "
                  "every claim for the code.")
        steps.append("Check first that no multiple-procedure reduction (a higher-valued procedure the same day) or facility rate (a surgery-center day) explains it.")
        who = "Biller"
        buttons += ["build_packet", "copy_instructions", "mark_sent"]
    elif route == "decide":
        action, steps, who = _decide_step(f, fx, st)
        buttons += ["copy_instructions", "log_outcome", "let_go"]
    else:
        needs, argument = _appeal_needs(f, fx)
        how = _appeal_how(f)
        action = f"Appeal to {grp}: {argument} {how}"
        steps += [f"Build the appeal packet; it needs {', '.join(needs)}.", "Read the letter, fill anything in brackets, then send it."]
        who = "Coder reads the note, then the biller sends it" if any("note" in n for n in needs) else "Biller"
        buttons += ["build_packet"]
        buttons += ["attach_note"] if _note_on_file(conn, f) else (["find_note"] if _modmed_configured() else [])
        buttons += ["upload_note"]
        ok, why = _fax_number(conn, f)
        if ok:
            buttons.append("fax_payer")
        steps.append(why)
        buttons += ["copy_instructions", "mark_sent"]
    if url:
        buttons.append("open_in_ema")
    # write-off decisions only through the owner's policy table or a person's Let go with a reason
    if st["work_state"] == "work_now" and o.get("pattern") and o.get("outcome") in ("auto_written_off", "written_off_at_posting",
                                                                                      "contractual_at_posting", "written_off_later"):
        buttons.append("accept_pattern")
        steps.append(f"If the owner accepts these write-offs as policy, Accept pattern ({o['pattern'].replace('|', ', ')}); otherwise work it. "
                     "A single item is let go only by a person, with a reason.")
    if st["work_state"] == "work_now" and route not in ("decide",) and "let_go" not in buttons:
        buttons.append("let_go")
    appeal_ok = route in ("appeal", "rate") and st["work_state"] == "work_now" and not appeal_block
    if appeal_block and route in ("appeal", "rate") and st["work_state"] != "work_now":
        appeal_ok = False
    instructions = _instructions(f, fx, action, steps, needs, deadline, basis)
    btn = []
    for b in buttons:
        if b in BUTTONS and b not in btn:
            btn.append(b)
    return {"action": _clean(action), "who": who, "deadline": deadline, "basis": basis, "expected_cents": expected, "buttons": btn,
            "instructions": instructions, "route": route, "steps": steps, "needs": needs, "clocks": clocks, "appeal_ok": appeal_ok,
            "appeal_block": appeal_block if not appeal_ok else None, "claim_url": url}


def _appeal_how(f: dict) -> str:
    from . import appeals
    kind = f.get("payer_kind")
    if kind == "medicare":
        return ("Send it as a Medicare redetermination through the contractor's portal or by mail (Medicare contractors take no faxed "
                "redeterminations), within 120 days of the remittance.")
    return appeals._process(dict(f, evidence=S._ev(f)))


def _call_step(f: dict, fx: dict, st: dict, today: str) -> tuple[str, list[str], str]:
    grp = f.get("payer_group") or "the payer"
    ev = S._ev(f)
    codes = ", ".join(sorted({c for _s, c in S._service_keys(f)})) or (f.get("procedure_code") or "")
    billed = ev.get("billed_cents") or f.get("charge_cents")
    pccn = _payer_claim_number(fx)
    facts = [f"date of service {day(f.get('service_date'))}", f"codes {codes}", f"billed {usd(billed)}"]
    if pccn:
        facts.append(f"the payer's claim number {pccn}")
    if f.get("carc"):
        facts.append(f"the denial codes {f.get('carc')}" + (f" with {f.get('rarc')}" if f.get("rarc") else ""))
    rule = st.get("rule") or ""
    a = st.get("answered") or {}
    if rule == "work_answered" and a:
        if (a.get("paid") or 0) > 0:
            steps = [f"Open the payer's answer of {day(a.get('date'))}: it paid {usd(a['paid'])}.",
                     f"If that covers what this item lists ({usd(f.get('shortfall_cents'))}), mark it Paid with that amount; if not, ask the "
                     "payer for the rest (a reconsideration of the new answer).", "Have ready: " + ", ".join(facts) + "."]
            return (f"Read {grp}'s answer of {day(a.get('date'))} to the work already done: it paid {usd(a['paid'])}. Close the item if that "
                    "settles it.", steps, "Biller")
        codes_ = ", ".join(a.get("codes") or []) or "no payment"
        steps = [f"Open the payer's answer of {day(a.get('date'))} ({codes_}).",
                 "Upheld after an appeal: take it to the next level (second-level appeal or reconsideration) with the same record. "
                 "Denied for something the resubmission still lacks: fix that and send it again.", "Have ready: " + ", ".join(facts) + "."]
        return (f"{grp} answered the work already done on {day(a.get('date'))} without paying ({codes_}): work that answer.", steps,
                "A/R follow-up biller")
    if rule in ("work_answered", "work_stale", "follow_up_due"):
        w = (st.get("work") or {}).get("text") or "the work already done"
        q = (f"Ask {grp}: what happened to {w.lower() if w and w[0].isupper() else w}? Is it on file, and what was decided?")
        steps = [q, "Have ready: " + ", ".join(facts) + ".",
                 "If it was denied again, work the new answer (a second-level appeal or a corrected claim); if it is not on file, send it again now."]
        return f"Follow up with {grp} on the work already done: {st.get('state_basis')}.", steps, "A/R follow-up biller"
    if rule == "forwarded_late":
        steps = [f"Ask the carrier named on the remittance whether it received the claim and when it will pay.", "Have ready: " + ", ".join(facts) + "."]
        return f"{st.get('state_basis')}.", steps, "A/R follow-up biller"
    if f.get("signal") == "DENIED_DUPLICATE_UNPAID":
        steps = [f"Ask {grp}: which claim and check paid this service the first time?", "Have ready: " + ", ".join(facts) + ".",
                 "If none was ever paid, ask the payer to reprocess this line."]
        return f"Call {grp} (or use its portal) to find the first payment this denial points to.", steps, "A/R follow-up biller"
    filing = st.get("deadline")
    steps = [f"Ask {grp}: \"Do you have a claim for {codes}, billed {usd(billed)}, date of service {day(f.get('service_date'))}? "
             "When was it received, and what is its status?\"",
             "Have ready: " + ", ".join(facts) + ", the member ID and the rendering NPI.",
             "Not on file: send it again now as a new claim and keep the clearinghouse acceptance report as proof of timely filing.",
             "Pending: ask what is missing and send it. Denied or paid: get the EOB and post or work it here."]
    return (f"Look the claim up on {grp}'s portal or call: is it on file?" + (f" If not, it must reach the payer by {day(filing)}." if filing else ""),
            steps, "A/R follow-up biller")


def _decide_step(f: dict, fx: dict, st: dict) -> tuple[str, list[str], str]:
    sig = f.get("signal") or ""
    carc = str(f.get("carc") or "")
    rarcs = {x.strip() for x in str(f.get("rarc") or "").split(",") if x.strip()}
    grp = f.get("payer_group") or "the payer"
    o = fx.get("outcome") or {}
    hint = str(f.get("hint") or "")
    if hint.startswith(("Possibly paid", "Processed on claim", "Partly paid")):
        what = ("a payment on another claim" if hint.startswith("Possibly") else "an answer on another claim" if hint.startswith("Processed")
                else "fewer units paid than billed")
        return (f"Check first: {hint}. Open that claim and see whether it pays this service; if it does, mark this item Paid with that "
                "payment; if not, work the denial.", [f"RemitWatch found {what} that does not meet every test for closing it."], "Biller")
    if o.get("outcome") == "paid_before":
        pb = o.get("paid_before_first") or {}
        return (f"Check first: EMA shows this line paid {usd(o.get('paid_before'))} by {pb.get('payer') or 'insurance'} on {day(pb.get('date'))}, "
                "before this denial. Find out whether this denial is a duplicate or a second payer's answer before doing anything else.",
                ["Compare the payer that paid with the one that denied, and the claim numbers.",
                 "Same payer, same service: a duplicate; nothing owed. Another payer: check which is primary."], "Biller")
    if sig == "DENIED_MISSING_PRIMARY":
        prim = [x for x in fx["r835"]["rows"] if x["claim_key"] == f.get("claim_key") and (x["paid"] or 0) > 0 and x["code"] != f.get("procedure_code")]
        if prim:
            return (f"Ask {grp} to reprocess the add-on {f.get('procedure_code')} now: its main procedure was paid on the same claim "
                    f"({usd(prim[0]['paid'])} on {day(prim[0]['date'])}), so B15 does not apply.",
                    ["File a reconsideration (or call) quoting the paid primary line.", "Going forward, list primary codes before their add-ons."], "A/R follow-up biller")
    if carc == "B20" or "M86" in rarcs:
        return ("Not a benefit exclusion: B20 with M86 means the payer counts the visit as already paid inside a time frame, usually the global "
                "package of a recent procedure by the same clinician. Appeal only with a diagnosis unrelated to that procedure; otherwise let it go. "
                "Do not bill the patient (group PI or CO).", ["Check the patient's procedures in the 10 or 90 days before.",
                                                             "Read the note: unrelated problem, appeal with modifier 24; same wound, let it go."], "Coder")
    if carc == "B9":
        return ("The patient was in hospice: the plan is not liable while the member is enrolled; Original Medicare pays non-hospice care. "
                "Bill Original Medicare (with GW where it applies), or let it go if it already answered.", [], "Biller")
    if sig == "DENIED_PROVIDER":
        return ("Fix the clinician's enrollment with the payer (effective date back to the start date), then ask the payer to reprocess every "
                "claim it denied for it.", ["Credentialing: confirm participation and the effective date.",
                                            "Then one reprocessing request listing every affected claim."], "Credentialing coordinator")
    if carc in ("129",) and "N770" in rarcs:
        return ("Nothing to work: CO-129 with N770 says the payer processed the adjustment request on the original claim. Let it go with that "
                "note.", [], "Biller")
    if sig == "DENIED_NONCOVERED":
        steps = ["Check the member's benefits for the service on the date (portal or call).",
                 "Covered after all, or another plan is primary: send it to the right payer, or appeal with the coverage rule.",
                 "Not covered: bill the patient only where allowed (group PR, or a signed advance notice); a CO denial and a Medicare Advantage "
                 "member without a pre-service determination cannot be billed; otherwise let it go with the reason."]
        return ("Check benefits: send it to the right payer, bill the patient only where allowed, or let it go with the reason.", steps, "Biller")
    if sig == "CLAIM_DENIED_UNPAID":
        if carc in ("18", "B13"):
            return ("A whole-claim duplicate denial: find the claim it duplicates (the same bill's other submission) and how that one was "
                    "answered; nothing more is owed on this copy.", [], "Biller")
        if carc == "29":
            return ("A timely-filing denial of the whole claim: appeal only with proof it reached the payer in time (the clearinghouse "
                    "acceptance report); otherwise let it go (provider liability: do not bill the patient).", [], "Biller")
    if sig == "CODE_CHANGED":
        return ("Compare the note with both codes: if the code billed was right, appeal; if the payer's code was, let it go and change how it is "
                "billed.", [], "Coder")
    return (f"Read the reason ({f.get('carc') or ''} {_rarc_words(f.get('rarc'))}): fix and resubmit if it names an error, appeal if the payer "
            "is wrong, let it go with the reason if the payer is right.".replace("( ", "("), [], "Biller")


def _era_request(fx: dict, proof: dict) -> str | None:
    if not proof.get("check_tail"):
        return None
    pay = next((p for p in fx["ema"]["pays"] if p["check_tail"] == proof.get("check_tail") and not p["in_835"]), None)
    if pay is None:
        return None
    return (f"Ask {proof.get('payer') or 'the payer'} (through its portal, or TriZetto's ERA re-send) for the ERA of the check ending "
            f"{proof['check_tail']}, dated {day(proof.get('date'))}, so the archive has it: one request covers every claim on that check.")


def _expected(f: dict, fx: dict) -> int:
    """What working the item is worth: its expected amount, never more than the charge, and never more than the payer's own
    allowed amount when its remittance shows one (the charge less its contractual reduction)."""
    exp = f.get("shortfall_cents") or f.get("expected_cents") or 0
    charge = f.get("charge_cents") or (S._ev(f).get("billed_cents") if f.get("signal") in S.CLAIM_SIGNALS else None)
    if charge and exp > charge:
        exp = charge
    if f.get("category") in ("denial", "bundling"):
        for x in S.own_rows(fx):
            co45 = sum(a for g, c, a in x["adj"] if g == "CO" and c == "45")
            denied = sum(a for g, c, a in x["adj"] if not (g == "CO" and c in ("45", "253")) and g != "PR")
            if co45 and denied and x.get("charge"):
                exp = min(exp, (x["charge"] or 0) - co45)
    return max(0, int(exp or 0))


def _clocks(f: dict, fx: dict, st: dict) -> list[dict]:
    """Every clock the item runs on: the appeal window and, where it differs, the corrected-claim or first-filing limit."""
    from .codes import appeal_rule, corrected_rule, filing_rule
    out = []
    if st.get("deadline"):
        out.append({"what": "the item's deadline", "date": st["deadline"], "basis": st.get("deadline_basis")})
    grp, kind = f.get("payer_group"), f.get("payer_kind")
    if f.get("payment_date") and f.get("signal") not in S.CLAIM_SIGNALS and f.get("category") in S.MONEY:
        a = appeal_rule(grp, kind)
        out.append({"what": "appeal", "date": S._add(f["payment_date"], a["days"]), "basis": a["label"]})
        cr = corrected_rule(grp, kind, f.get("rarc"), f.get("signal"), f.get("carc"))
        if cr:
            start = f.get("service_date") if cr["clock"] == "dos" else f.get("payment_date")
            out.append({"what": "corrected claim", "date": S._add(start, cr["days"]), "basis": cr["label"]})
    if f.get("signal") in S.CLAIM_SIGNALS and f.get("service_date"):
        r = filing_rule(grp, kind)
        out.append({"what": "first filing", "date": S._add(f["service_date"], r["days"]), "basis": r["label"]})
        if r.get("earliest"):
            out.append({"what": "first filing (shortest common limit)", "date": S._add(f["service_date"], r["earliest"]),
                        "basis": "the shortest common first-filing limit for this kind of payer; send it by then where possible"})
    seen, uniq = set(), []
    for c in out:
        k = (c["what"], c["date"])
        if c["date"] and k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def _instructions(f: dict, fx: dict, action: str, steps: list[str], needs: list[str], deadline: str | None, basis: str | None) -> str:
    lines = [action]
    for i, s in enumerate(steps, 1):
        lines.append(f"{i}. {s}")
    if needs:
        lines.append("It needs: " + "; ".join(needs) + ".")
    if deadline:
        lines.append(f"By {day(deadline)}" + (f" ({basis})" if basis else "") + ".")
    return _clean("\n".join(lines))


def next_step(conn: sqlite3.Connection, finding_id: str, *, ledger: Any = None, today: str | None = None) -> dict:
    """What to do next, who, by when and why, what it is worth, the buttons and the instructions:
    {'action', 'who', 'deadline', 'basis', 'expected_cents', 'buttons', 'instructions', 'route', 'steps', 'needs', 'clocks',
    'appeal_ok', 'appeal_block', 'claim_url'}."""
    return explain(conn, finding_id, ledger=ledger, today=today)["next_step"]


def explain(conn: sqlite3.Connection, finding_id: str, *, ledger: Any = None, today: str | None = None) -> dict:
    """The story, the verdict and the next step of one item from one gathering of its facts:
    {'story', 'verdict', 'next_step', 'state'}."""
    f = _load(conn, finding_id)
    if f is None:
        return {"story": {"rows": [], "balance": {}, "reconciles": False, "gap_cents": 0, "notes": ["No such item."]},
                "verdict": {"state": None, "sentence": "No such item.", "rows": [], "strength": "inferred", "rule": None},
                "next_step": {"action": "", "who": "", "deadline": None, "basis": None, "expected_cents": 0, "buttons": [], "instructions": ""},
                "state": None}
    fx = _gather(conn, f, today)
    st = _state_of(conn, fx, today)
    story = _story(conn, fx, ledger)
    verdict = _verdict(conn, fx, story, st, today)
    nxt = _next(conn, fx, story, st, verdict, today)
    return {"story": story, "verdict": verdict, "next_step": nxt, "state": {k: v for k, v in st.items() if k != "evidence"}}
