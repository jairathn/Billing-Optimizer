"""
Every item's story, its verdict and its exact next step (amendment 9), worked out from the remittances and EMA's
postings, never from EMA's claim status alone.

  item_story(conn, finding_id=None, *, claim_key=None, ledger=None, today=None)
      {'rows': [...], 'balance': {...}, 'reconciles': bool, 'gap_cents': int, 'twin': {...} or None, 'notes': [...]}
      One chronological list of every event on the item's line (its claim's, where the line has none), each row tagged
      with its source: '837' (each send of the item's bill in the 837 claim files: the day, the payer and its place on
      the claim, an original, a replacement or a void, the item's services billed), '835' (each adjudication: payer, paid,
      allowed, every reason with group, amount and meaning, every
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

SOURCES = ("837", "835", "production", "ar", "weekly", "claim_status", "remitwatch")
BUTTONS = ("build_packet", "attach_note", "find_note", "upload_note", "fax_payer", "copy_instructions", "mark_sent", "log_outcome",
           "mark_posted", "mark_billed", "accept_pattern", "let_go", "reopen", "request_era", "open_in_ema")
STATUS_WORDS = {"1": "processed as primary", "2": "processed as secondary", "3": "processed as tertiary", "4": "denied",
                "19": "processed as primary and forwarded to the next payer", "20": "processed as secondary and forwarded",
                "21": "processed as tertiary and forwarded", "22": "a reversal of an earlier answer",
                "23": "not its claim, forwarded", "25": "a predetermination"}
AR_WINDOW = ("The A/R export lists only lines with activity in its last 30 days and lines with a denial; a line missing "
             "from it is not closed or paid.")
SERVER_ONLY = "Each of EMA's postings is kept on the server; this site shows EMA's summaries of this line."
# the dashboard's copy keeps every 835 claim of an item's bill (cloud.SERVING_BUILD 8) but not EMA's single postings
COPY_NOTE = ("This site keeps every remittance of the bill. The daily run works out each item's state from the full store, which also "
             "keeps each of EMA's postings.")
_SRC_ORDER = {"837": -1, "835": 0, "production": 1, "weekly": 2, "claim_status": 3, "ar": 4, "remitwatch": 5}
# on the dashboard's copy, which keeps no 837 claim file: the send the item's own evidence names
COPY_837_NOTE = "The 837 claim files are kept on the server; this site shows the send the analysis dated this item by."
_FREQ_WORDS = {"1": "an original claim", "7": "a replacement (corrected) claim", "8": "a void of the claim", "6": "a corrected claim"}
_PLACE_WORDS = {"P": "primary", "S": "secondary", "T": "tertiary"}


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


def _poss(name: Any) -> str:
    """A name as its owner: Anthem / BCBS's, UMWA Health & Retirement Funds' (never Funds's)."""
    n = str(name or "the payer")
    return n + ("'" if n.endswith("s") else "'s")


def _same_reason(a: Any, b: Any) -> bool:
    """Two reason codes are one when their codes agree and their groups agree or one has none (EMA's exports write
    'CO-197', 'CO 197' or '197')."""
    def split(x: Any) -> tuple[str, str]:
        t = _ckey(x)
        m = re.fullmatch(r"(CO|PR|OA|PI|CR)?([A-Z0-9]+)", t)
        return (m.group(1) or "", m.group(2)) if m else ("", t)
    (ga, ca), (gb, cb) = split(a), split(b)
    return bool(ca) and ca == cb and (not ga or not gb or ga == gb)


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
    # a stored day as written; a moment (a person's mark, a fax) on the practice's calendar, as the states date it
    r = {"n": 0, "date": S._pday(date) or None, "source": source, "kind": kind, "payer": payer or None,
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


def _rows_837(conn: sqlite3.Connection, fx: dict) -> tuple[list[dict], list[str]]:
    """Each send of the item's bill in the 837 claim files (every submission, every payer: what the practice sent, and
    when): the day it went out, the claim id, the payer and its place on the claim (SBR01; the payer before it and what it
    left the patient, where the 837 says), an original, a replacement or a void, and the item's own services billed on it
    with their charges. On the dashboard's copy, which keeps no 837 file, the send the item's evidence names (sent_837),
    with a note. ([rows], [notes])."""
    f = fx["item"]
    b = S.bill_of(f.get("patient_control_number"))
    keys = {(str(d or "")[:10], c) for d, c in S._service_keys(f)}
    out: list[dict] = []
    if b and S._table(conn, "claims_837"):
        cols = S._cols(conn, "claims_837")
        more = ("payer_resp, other_payer_name, other_paid_cents, other_left_cents, other_pr_cents" if "payer_resp" in cols
                else "NULL, NULL, NULL, NULL, NULL")
        sends = conn.execute(f"""SELECT claim_837_key, patient_control_number, bht_created, TRIM(COALESCE(frequency_code, '')), payer_name,
                                        total_charge_cents, {more}
                                 FROM claims_837 WHERE patient_control_number >= ? AND patient_control_number < ?
                                 ORDER BY bht_created, patient_control_number, claim_837_key""", (b, b + "~")).fetchall()
        lines: dict[str, list] = {}
        if S._table(conn, "lines_837"):
            for key, code, dos, mods, chg, units in conn.execute(
                    """SELECT claim_837_key, procedure_code, service_date, modifiers, charge_cents, units FROM lines_837
                       WHERE patient_control_number >= ? AND patient_control_number < ? ORDER BY claim_837_key, line_seq""", (b, b + "~")):
                lines.setdefault(key, []).append((code, str(dos or "")[:10], mods, chg, units))
        for key, cid, bht, freq, payer, total, resp, other, other_paid, left, pr in sends:
            if S.bill_of(cid) != b:
                continue
            own = [x for x in lines.get(key, []) if (x[1], x[0]) in keys] if keys else lines.get(key, [])
            rest = len(lines.get(key, [])) - len(own)
            billed = "; ".join(f"{_line_label(c, d, m)} billed {usd(chg)}" + (f" ({u:g} units)" if u and u != 1 else "")
                               for c, d, m, chg, u in own)
            what = _FREQ_WORDS.get(freq, "a claim")
            place = _PLACE_WORDS.get(str(resp or "").upper())
            bits = [f"Sent electronically to {payer or 'the payer'} as claim {cid}, {what}" + (f", as its {place} payer" if place and place != "primary" else "")]
            if other or other_paid is not None:
                lft = left if left is not None else pr
                bits.append(f"after {other or 'another payer'} answered it" + (f" (paid {usd(other_paid)}" if other_paid is not None else "")
                            + (f", left the patient {usd(lft)})" if lft is not None else (")" if other_paid is not None else "")))
            if billed:
                bits.append(billed)
            if rest > 0:
                bits.append(f"and {rest} other {'line' if rest == 1 else 'lines'}")
            bits.append(f"claim total {usd(total)}")
            tags = ["837"] + (["own_send"] if cid == f.get("patient_control_number") else [])
            out.append(_row(bht, "837", "void" if freq == "8" else "send", "; ".join(bits) + ".", payer=payer, amount=total,
                            tags=tags, claim_id=cid, line=_line_label(own[0][0], own[0][1], own[0][2]) if own else None))
        return out, []
    sent = (S._ev(f).get("sent_837") or {}) if f.get("evidence") else {}
    if sent.get("day"):
        what = _FREQ_WORDS.get(str(sent.get("frequency") or ""), "a claim")
        place = _PLACE_WORDS.get(str(sent.get("payer_resp") or "").upper())
        n = int(sent.get("sends") or 1)
        out.append(_row(sent["day"], "837", "send", f"Sent electronically to {sent.get('payer') or 'the payer'} as claim "
                        f"{f.get('patient_control_number')}, {what}" + (f", as its {place} payer" if place and place != "primary" else "")
                        + (f" (sent {n} times, first on {day(sent.get('first'))})" if n > 1 else "") + ".",
                        payer=sent.get("payer"), tags=["837", "own_send"], claim_id=f.get("patient_control_number")))
        return out, [COPY_837_NOTE]
    return out, []


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
    """{(check, payer): (its money is in an 835, the trace of the 835 a number no 835 carries stands for, that 835's date)}
    from the checks table: a number EMA posted that no 835 carries, paired one to one with an 835 EMA has under no number
    (C1 'misnumbered'), is that 835's payment."""
    out: dict = {}
    if not S._table(conn, "ema_checks"):
        return out
    for chk, payer in set(checks):
        if not chk:
            continue
        r = conn.execute("SELECT c.match, c.cause, c.pair_trace, t.payment_date FROM ema_checks c LEFT JOIN transactions t "
                         "ON t.transaction_key = c.pair_trace WHERE c.check_no=? AND c.payer=?", (chk, payer or "")).fetchone()
        if r:
            mis = r[1] == "misnumbered" and bool(r[2])
            out[(chk, payer or "")] = (r[0] in ("exact", "loose") or mis, r[2] if mis else None, r[3] if mis else None)
    return out


def _carried(pay_835: dict, chk: str | None, payer: str | None, in_835: Any) -> tuple[bool, str]:
    """(its money is in an 835, the words for it) for one EMA payment row."""
    got = pay_835.get((chk, payer or "")) if chk else None
    if got and got[1]:
        return True, (f"no 835 carries this number; it is the 835 payment of {day(got[2])}, posted under another number" if got[2]
                      else "no 835 carries this number; it is an 835 payment posted under another number")
    if (got and got[0]) or in_835:
        return True, "an 835 is on file for this check"
    return False, "no 835 is on file for this check"


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
    era_wo = [h for h in era if h["status"] == "WRITE_OFF"]
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
        if grp and any(_same_reason(k, h["carc"]) for h in era_wo) and "adj_at_posting" in tags:
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
                in835, has = _carried(pay_835, r["check_no"], r["payer"], False)
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
        pay_835 = _check_in_835(conn, [(p.get("check"), p.get("payer") or "") for p in fx["ema"]["pays"] if p.get("check")])
        for p in fx["ema"]["pays"]:
            label = _line_label(p["cpt"], p["svc"])
            itemized["ins_paid"] += p["cents"] or 0
            in835, has = _carried(pay_835, p.get("check"), p.get("payer"), p["in_835"])
            kind = "payment" if (p["cents"] or 0) > 0 else ("takeback" if (p["cents"] or 0) < 0 else "zero_answer")
            verb = {"payment": f"paid {usd(p['cents'])}", "takeback": f"took back {usd(-(p['cents'] or 0))}", "zero_answer": "answered $0.00"}[kind]
            out.append(_row(p["posted"], "production", kind, f"{p['payer'] or 'Insurance'} {verb} on {label}, posted {day(p['posted'])} "
                            f"(check ending {p['check_tail'] or '?'}; {has}).", payer=p["payer"], amount=p["cents"] or 0,
                            tags=[f"pay:{p['posted']}:{p['check_tail']}", "pay_no835" if not in835 else "pay_835"], line=label,
                            check_tail=p["check_tail"], in_835=in835))
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
                """SELECT claim_id, service_date, cpt, modifiers, units, charges_cents, insurance_payments_cents, claim_status,
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
        when = S.fax_day(x.get("sent_at"), x.get("created_at")) or None       # the day it went, as the states date it
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
CONTRACTUAL = frozenset({"45", "253", "144", "59", "131", "94", "237", "2", "23"})


def _balance(conn: sqlite3.Connection, fx: dict, itemized: dict, rows: list[dict], server: bool = False) -> dict:
    """charge = insurer paid + patient paid + contractual + written off + open (a credit is a negative open), from EMA's
    postings when EMA holds the line, else from the 835's standing answers of the line (every payer's money, reversals
    paired); then checked against what else states the line's money: the itemized rows, the A/R (every row of its newest
    pull, when no posting on the line came after it), the 835 check by check (EMA's money under each check against the
    835's net under it: a payment EMA never posted, a takeback EMA never posted, a payment posted short or long), patient
    money EMA cannot tie to one claim, and a code billed twice on the claim's date. Every difference is a gap with its
    amount and cause; where a figure is kept only on the server, the balance says it cannot be checked here (reconciles
    None), never that it reconciles."""
    f = fx["item"]
    tot = S._ema_line_totals(fx)
    out: dict = {"basis": None, "as_of": None, "charge_cents": 0, "insurer_paid_cents": 0, "patient_paid_cents": 0,
                 "contractual_cents": 0, "written_off_cents": 0, "patient_adjusted_cents": 0, "open_cents": 0, "credit": False,
                 "checks": [], "reconciles": True, "gap_cents": 0, "gap_cause": None, "text": "", "unknown": None}
    gaps: list[tuple[int, str]] = []
    if tot["n"]:
        out.update(basis="production", as_of=max((ln.get("as_of") or "" for ln in fx["ema"]["lines"]), default=None),
                   charge_cents=tot["charges"], insurer_paid_cents=tot["ins_paid"], patient_paid_cents=tot["pat_paid"],
                   contractual_cents=tot["contractual"], written_off_cents=tot["noncontractual"], patient_adjusted_cents=tot["pat_adj"])
        computed = tot["charges"] - tot["ins_paid"] - tot["pat_paid"] - tot["contractual"] - tot["noncontractual"] - tot["pat_adj"]
        out["open_cents"] = computed
        if computed != tot["balance"]:
            # EMA's summary is defined as this sum: a difference means the summaries themselves are damaged
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
        # the A/R's balance: every row of the newest pull of each line (primary, secondary, patient), when no posting on the
        # line came after that pull
        last_post = max((ln.get("last_posted") or "" for ln in fx["ema"]["lines"]), default="")
        lines_keys = {(ln["svc"], ln["cpt"]) for ln in fx["ema"]["lines"]}
        newest: dict[tuple, str] = {}
        for a in fx["ar"]:
            k = (a.get("svc"), a.get("cpt"))
            if k in lines_keys and (a.get("as_of") or "") > newest.get(k, ""):
                newest[k] = a.get("as_of") or ""
        ar_rows = [a for a in fx["ar"] if newest.get((a.get("svc"), a.get("cpt"))) == (a.get("as_of") or "") and (a.get("svc"), a.get("cpt")) in newest]
        if ar_rows:
            as_of = max(a.get("as_of") or "" for a in ar_rows)
            ar_open = sum(a.get("exposure") or 0 for a in ar_rows)
            if last_post <= as_of:
                agrees = ar_open == computed
                out["checks"].append({"source": "ar", "as_of": as_of, "open_cents": ar_open, "agrees": agrees, "rows": len(ar_rows)})
                if not agrees:
                    gaps.append((computed - ar_open, f"EMA's A/R of {day(as_of)} shows {usd(ar_open)} open on these lines; the postings add up to "
                                                     f"{usd(computed)}"))
            else:
                out["checks"].append({"source": "ar", "as_of": as_of, "open_cents": ar_open, "agrees": None,
                                      "why": f"postings after the A/R of {day(as_of)}"})
        # the 835, check by check: EMA's money under each check against the 835's net under it (reversals included). A
        # payment EMA posted under a number no 835 carries counts where the checks table pairs the two (C1 'misnumbered'),
        # or, as the checks' "posted elsewhere" count takes it, as a payment of its amount (within $1) on the line under a
        # number no 835 carries, from 7 days before to 120 days after the 835's date
        pays = fx["ema"]["pays"]
        mis = _check_in_835(conn, [(p.get("check"), p.get("payer") or "") for p in pays if p.get("check")])
        paired = {v[1] for v in mis.values() if v[1]}
        spare = [p for p in pays if (p["cents"] or 0) > 0 and not p.get("in_835") and not (mis.get((p.get("check"), p.get("payer") or "")) or (False,))[0]]
        code = f.get("procedure_code") or "the line"
        for d in S.check_money(fx):
            if d["net_835"] == d["ema"]:
                if d["ema_rows"] or d["net_835"]:
                    out["checks"].append({"source": "835", "agrees": True, "check_tail": d["trace_tail"], "date": d["date"]})
                continue
            tail_ = f"check ending {d['trace_tail']}" if d["trace_tail"] else "its check"
            if d["ema_rows"] == 0:
                if str(d.get("method") or "").upper() == "NON" or d["tk"] in paired:
                    continue        # an informational notice (the money comes on another remittance), or posted under another number
                if d["net_835"] > 0:
                    lo, hi = S._add(d["date"], -7) or "", S._add(d["date"], 120) or ""
                    hit = next((p for p in spare if abs((p["cents"] or 0) - d["net_835"]) <= 100 and lo <= (p["posted"] or "") <= hi), None)
                    if hit is not None:
                        spare.remove(hit)
                        out["checks"].append({"source": "835_elsewhere", "agrees": True,
                                              "why": f"the 835 of {day(d['date'])} paid {usd(d['net_835'])} on {code} ({tail_}); EMA posted "
                                                     f"{usd(hit['cents'])} on {day(hit['posted'])} under check ending {hit['check_tail'] or '?'}, "
                                                     "a number no 835 carries"})
                        continue
                    gaps.append((d["net_835"], f"the 835 of {day(d['date'])} paid {usd(d['net_835'])} on {code} ({tail_}), and EMA has not posted it"))
                else:
                    gaps.append((-d["net_835"], f"the 835 of {day(d['date'])} took back {usd(-d['net_835'])} on {code} ({tail_}, a reversal), and "
                                                "EMA has not posted the takeback"))
                continue
            diff = d["ema"] - d["net_835"]
            if diff > 0 and d["reversal"]:
                gaps.append((diff, f"EMA never posted {_poss(d['group'])} takeback of {usd(diff)} (the 835 of {day(d['date'])}, {tail_}, reverses "
                                   "an earlier answer of this line)"))
            elif diff > 0:
                gaps.append((diff, f"EMA posted {usd(d['ema'])} under {tail_}, {usd(diff)} more than the 835 of {day(d['date'])} paid "
                                   f"({usd(d['net_835'])})"))
            else:
                gaps.append((-diff, f"EMA posted {usd(d['ema'])} under {tail_}, {usd(-diff)} less than the 835 of {day(d['date'])} paid "
                                    f"({usd(d['net_835'])})"))
        if not any(d["reversal"] for d in S.check_money(fx)):
            for p in S.plb_unbooked(fx):
                gaps.append((abs(p["cents"] or 0), f"{p['payer']} took back {usd(abs(p['cents']))} on {day(p['date'])} (a provider-level "
                                                   f"adjustment {p['reason']} naming this claim), and EMA shows no takeback on this line"))
        if tot["several"]:
            unj = sum(r["amount_cents"] or 0 for r in rows if "patient_unjoined" in r["tags"])
            why = ("patient money for this patient, date and code cannot be tied to one claim (the surgery center's facility claim, or "
                   "the same code billed twice, shares the key)")
            if unj:
                gaps.append((unj, f"{why}: {usd(unj)} of it is on file and not counted on this line"))
            elif server:
                out["checks"].append({"source": "patient", "agrees": None, "why": why + "; none is on file"})
            else:
                out["unknown"] = ("patient money for this date and code is listed on the server and may belong to another claim (the "
                                  "surgery center's facility claim, or the same code billed twice)")
                out["checks"].append({"source": "patient", "agrees": None, "why": out["unknown"]})
        if not S.code_once(fx):
            own = S.own_rows(fx)
            own_charge = sum(x["charge"] or 0 for x in own if x.get("line_key"))
            # the claim's lines of the code on the date: separate services, or one service a payer split into pieces (a
            # duplicate denial restating the payment, the reductions and the patient's share as lines of their own)
            ck = own[0]["claim_key"] if own else None
            pieces = sum(abs(x["charge"] or 0) for x in fx["r835"]["rows"] if x["claim_key"] == ck and x.get("line_key") and x["status"] != "22")
            if own_charge and own_charge != tot["charges"] and pieces != tot["charges"]:
                gaps.append((tot["charges"] - own_charge, f"the claim carries {f.get('procedure_code')} more than once on {day(f.get('service_date'))}: "
                                                          f"EMA's figures ({usd(tot['charges'])} charged) cover all of them"))
            elif own_charge and own_charge != tot["charges"]:
                out["checks"].append({"source": "835_pieces", "agrees": True,
                                      "why": f"the payer split {f.get('procedure_code')} into lines that add up to EMA's charge of {usd(tot['charges'])}"})
    else:
        own = S.own_rows(fx)
        stand = [x for x in S.standing_rows(fx["r835"]["rows"]) if x.get("line_key")]
        own_lines = [x for x in own if x.get("line_key")]
        lcns = {x.get("lcn") for x in own_lines if x.get("lcn")}
        once = S.code_once(fx)
        own_keys = {x["line_key"] for x in own_lines}
        # the line's standing answers: its own, the same line on another submission (its line control number, or the code
        # billed once on the claim's date), every payer's
        line = [x for x in stand if x["line_key"] in own_keys or (lcns and x.get("lcn") in lcns) or (once and not lcns)]
        if not line and own_lines:
            line = list(own_lines)
            out["reversed_only"] = True
        if line:
            prim = [x for x in line if x["status"] not in S.SECOND_STATUS] or line
            paying = [x for x in prim if (x["paid"] or 0) > 0]
            mine = [x for x in prim if S._same_payer(S.row_group(x)[0], f.get("payer_group"))]
            # the answer whose reductions stand: the newest that paid (a later denial of a copy does not undo a payment), else
            # the item's payer's newest
            ref = max(paying or mine or prim, key=lambda r: (r["date"] or "", r["line_key"] or ""))
            co = sum(a for g, c, a in ref["adj"] if g == "CO" and c in CONTRACTUAL)
            pr = sum(a for g, c, a in ref["adj"] if g == "PR")
            charge = ref["charge"] or 0
            paid_total = sum(x["paid"] or 0 for x in line)
            same_grp = [x for x in line if S._same_payer(S.row_group(x)[0], S.row_group(ref)[0])]
            others_paid = paid_total - sum(x["paid"] or 0 for x in same_grp)
            # another payer's payment pays the patient's share first (a second payer's coinsurance)
            pr_left = max(0, pr - max(0, others_paid))
            open_ = charge - paid_total - co - pr_left
            payers = {}
            for x in line:
                if (x["paid"] or 0):
                    g = S.row_group(x)[0]
                    payers[g] = payers.get(g, 0) + (x["paid"] or 0)
            out.update(basis="835", as_of=max(x["date"] or "" for x in line), charge_cents=charge, insurer_paid_cents=paid_total,
                       contractual_cents=co, written_off_cents=0, patient_paid_cents=0, payers=payers)
            out["patient_owes_cents"] = pr_left
            out["denied_cents"] = open_
            out["open_cents"] = open_
            for x in line:
                gap = (x["charge"] or 0) - (x["paid"] or 0) - sum(a for g, c, a in x["adj"])
                out["checks"].append({"source": "835", "agrees": gap == 0, "date": x["date"], "check_tail": x.get("check_tail")})
                if gap:
                    gaps.append((gap, f"the 835 line of {day(x['date'])} does not balance: billed {usd(x['charge'])}, paid {usd(x['paid'])}, "
                                      f"adjusted {usd(sum(a for g, c, a in x['adj']))}"))
            if not fx["ema"]["lines"]:
                o = fx.get("outcome") or {}
                why = {"not_covered": "EMA's Production reports do not cover this date of service",
                       "not_found": "this line is not tied to its EMA claim", "shared_line": "the claim carries this code more than once"}.get(
                    o.get("outcome") or "", "EMA's postings for this line are not on file here")
                out["checks"].append({"source": "production", "agrees": None, "why": why})
        elif any(not x.get("line_key") for x in own):
            # a whole-claim answer with no service lines: the claim's own money balances instead
            x = max((r for r in own if not r.get("line_key") and r["status"] != "22"), default=None,
                    key=lambda r: (r["date"] or "", r.get("claim_key") or "")) or max(
                (r for r in own if not r.get("line_key")), key=lambda r: (r["date"] or "", r.get("claim_key") or ""))
            co = sum(a for g, c, a in x["cadj"] if g == "CO" and c in CONTRACTUAL)
            pr = sum(a for g, c, a in x["cadj"] if g == "PR")
            other = sum(a for g, c, a in x["cadj"]) - co - pr
            charge, paid = x.get("claim_charge") or 0, x.get("claim_paid") or 0
            out.update(basis="835_claim", as_of=x["date"], charge_cents=charge, insurer_paid_cents=paid, contractual_cents=co,
                       written_off_cents=0, patient_paid_cents=0)
            out["patient_owes_cents"], out["denied_cents"] = pr, other
            out["open_cents"] = charge - paid - co - pr
            gap = charge - paid - sum(a for _g, _c, a in x["cadj"])
            out["checks"].append({"source": "835", "agrees": gap == 0})
            if gap:
                gaps.append((gap, f"the 835's answer of {day(x['date'])} to the whole claim does not balance: billed {usd(charge)}, paid "
                                  f"{usd(paid)}, adjusted {usd(sum(a for _g, _c, a in x['cadj']))}"))
        elif (f.get("signal") or "") in S.CLAIM_SIGNALS and (f.get("charge_cents") or 0) > 0:
            # no payer answered the claim and no EMA posting is tied to it: all of its charge is open
            out.update(basis="unanswered", as_of=None, charge_cents=f["charge_cents"], open_cents=f["charge_cents"])
            out["checks"].append({"source": "production", "agrees": None, "why": "no EMA posting is tied to this claim"})
        else:
            out["basis"] = "none"
    out["gap_cents"] = sum(abs(g) for g, _w in gaps)
    out["gap_items"] = [{"cents": abs(g), "cause": w} for g, w in gaps]
    out["reconciles"] = (None if out["unknown"] else True) if not gaps else False
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
        payers = b.get("payers") or {}
        by = (" (" + ", ".join(f"{g} {usd(c)}" for g, c in sorted(payers.items(), key=lambda kv: -kv[1])) + ")") if len(payers) > 1 else ""
        head = (f"Charge {usd(b['charge_cents'])} = paid {usd(b['insurer_paid_cents'])}{by} + contractual {usd(b['contractual_cents'])} + "
                f"patient's share {usd(b.get('patient_owes_cents'))} + denied or open {usd(b.get('denied_cents'))} (the 835 answers through "
                f"{day(b['as_of'])}; EMA's postings for it are not on file).")
    elif b["basis"] == "835_claim":
        head = (f"Claim charge {usd(b['charge_cents'])} = paid {usd(b['insurer_paid_cents'])} + contractual {usd(b['contractual_cents'])} + "
                f"patient's share {usd(b.get('patient_owes_cents'))} + denied {usd(b.get('denied_cents'))} (the 835 of {day(b['as_of'])} "
                "answered the whole claim; EMA's postings for it are not on file).")
    elif b["basis"] == "unanswered":
        return (f"Charge {usd(b['charge_cents'])}, all of it open: no payer's answer to this claim is on file, and no EMA posting is tied "
                "to it.")
    else:
        return "No line of money to balance: this item is about a payer, a rate or a fee, not one service."
    if b["reconciles"] is None:
        return head + f" It cannot be checked to the cent on this site: {b['unknown']}."
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
    sends, notes837 = _rows_837(conn, fx)
    rows += sends
    rows += _rows_835(fx)
    prod, itemized, notes = _rows_production(conn, fx, ledger)
    notes += notes837
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
    bal = _balance(conn, fx, itemized, rows, server=ledger is not None)
    twin = _twin(fx)
    if twin:
        notes.append(twin["note"])
        start = len(rows)
        for tw in fx["ema"]["twins"]:
            label = _line_label(tw["cpt"], tw["svc"])
            rows.append(_row(tw.get("last_posted") or tw.get("first_posted"), "production", "twin",
                             f"The surgery center's facility claim for the same patient, date and code ({label}): charged {usd(tw.get('charges'))}, "
                             f"insurance paid {usd(tw.get('ins_paid'))}, open {usd(tw.get('balance'))}. It is the facility's claim, not this line, and "
                             "it is not counted here.", amount=tw.get("ins_paid"), tags=["twin"], line=label, counted=False))
        for i, r in enumerate(rows[start:], start + 1):
            r["n"] = i
    notes += _feed_notes(fx)
    if _is_copy(conn):
        notes.append(COPY_NOTE)
    return {"rows": rows, "balance": bal, "reconciles": bal["reconciles"], "gap_cents": bal["gap_cents"], "twin": twin, "notes": notes,
            "mode": "server" if ledger is not None else "summary", "finding_id": fx["item"]["finding_id"]}


def _feed_notes(fx: dict) -> list[str]:
    """What the sources cannot show for this item, said plainly: the 835s stopped before the item's later answers could
    arrive, EMA's Production report is old, or it does not cover the date of service."""
    f = fx["item"]
    ctx = fx["ctx"]
    out = []
    grp = f.get("payer_group") or ""
    lasts = ctx.get("group_last_835") or {}
    last = lasts.get(grp) or max((d for g, d in lasts.items() if grp and S._family(g) == S._family(grp)), default=None)
    newest = ctx.get("newest_835")
    t = ctx.get("today") or ""
    if newest and S._days(newest, t) and S._days(newest, t) > 21:
        out.append(f"The newest 835 on file is from {day(newest)}" + (f" ({grp}'s newest: {day(last)})" if last and last != newest else "")
                   + ": any answer after that reaches RemitWatch only through EMA's Production report"
                   + (f" (through {day(ctx.get('prod_as_of'))})" if ctx.get("prod_as_of") else ", and none is on file") + ".")
    if ctx.get("prod_as_of") and not ctx.get("prod_fresh"):
        out.append(f"EMA's Production report is from {day(ctx.get('prod_as_of'))}: postings after that day are not shown.")
    return out


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
DONE_835_PAY = ("paid_later_835", "paid_in_full_split", "paid_as_secondary")
DONE_PAID_BEFORE = ("paid_before_other_payer", "paid_before_duplicate", "replacement_denied_paid", "paid_before_same_payer",
                    "paid_first_other_payer")
DONE_EMA_PAY = ("claim_paid_in_ema", "paid_in_full_ema", "forwarded_paid")
WRITE_OFFS = ("auto_written_off", "written_off_while_pending", "written_off_at_posting", "contractual_at_posting",
              "written_off_later", "contractual_later")
COB_CARCS = frozenset({"22"})
AUTH_CARCS = frozenset({"197", "198", "243", "288", "15", "62", "39"})
BENIGN_CODES = re.compile(r"^(113\d\d|114[0-4]\d|1711[01]|1700[034])$")
RADIATION_CODES = frozenset({"77600", "77436", "77437", "77401", "G6001", "77280", "77290", "77300", "77334", "77417", "77427"})


def _mark(rows: list[dict], *tags: str, kinds: Sequence[str] = (), limit: int | None = None) -> list[int]:
    hit = [r for r in rows if any(t in r["tags"] for t in tags) or (kinds and r["kind"] in kinds)]
    if limit:
        hit = hit[-limit:]
    for r in hit:
        r["decisive"] = True
    return [r["n"] for r in hit]


def _decide_rows(rows: list[dict], ns: Sequence[int]) -> list[int]:
    for r in rows:
        if r["n"] in ns:
            r["decisive"] = True
    return list(ns)


def _refs(ns: Sequence[int]) -> str:
    ns = sorted(set(ns))
    if not ns:
        return ""
    if len(ns) == 1:
        return f"(row {ns[0]})"
    if ns == list(range(ns[0], ns[-1] + 1)):
        return f"(rows {ns[0]}-{ns[-1]})"
    return "(rows " + ", ".join(str(n) for n in ns) + ")"


def _is_copy(conn: sqlite3.Connection) -> bool:
    """The serverless dashboard's copy of the store (cloud.build_serving marks it): it keeps every remittance of an item's
    bill, but EMA's summaries of its postings rather than each posting."""
    try:
        return conn.execute("SELECT 1 FROM meta WHERE key = 'cache:built'").fetchone() is not None
    except sqlite3.Error:
        return False


def _state_of(conn: sqlite3.Connection, fx: dict, today: str | None) -> dict:
    """The state the detection run stored on the item, with state_of's reasons; state_of's own answer when none is stored.
    When the newest data no longer gives the stored state, the stored one is explained and the live one kept beside it; on
    the dashboard's copy, which keeps EMA's summaries rather than each posting, the stored state stands alone. The route then follows
    the stored rule (state_rule, when detection keeps it); with none, an item a person sent or started is followed up,
    never appealed afresh (M22)."""
    t = S._today(today)
    fx["_conn"] = conn
    try:
        live = S._public(S.decide(fx, t, conn))
    finally:
        fx.pop("_conn", None)
    f = fx["item"]
    stored = S.stored_state(f)
    if not stored:
        return live
    if stored["work_state"] == live["work_state"]:
        return dict(live, stored=True, follow_up_on=stored.get("follow_up_on") or live.get("follow_up_on"),
                    state_basis=stored.get("state_basis") or live.get("state_basis"))
    srule = f.get("state_rule")
    person = f.get("status") in ("appealed", "in_progress") and stored["work_state"] == "work_now"
    rule = srule or ("stored_person" if person else "stored")
    out = dict(live, stored=True, stored_differs=True, work_state=stored["work_state"], label=stored["label"], rule=rule,
               state_basis=stored.get("state_basis") or "", follow_up_on=stored.get("follow_up_on"), proof=None, answered=None,
               person=live.get("person") or person, appeal_block=f.get("appeal_block") if "appeal_block" in f else live.get("appeal_block"))
    out["route"] = S.route_of(fx, out, conn)
    if not _is_copy(conn):
        out["live"] = {"work_state": live["work_state"], "rule": live.get("rule"), "state_basis": live.get("state_basis"),
                       "label": live.get("label")}
    return out


def _verdict(conn: sqlite3.Connection, fx: dict, story: dict, st: dict, today: str | None) -> dict:
    f = fx["item"]
    rows = story["rows"]
    state, rule = st["work_state"], st.get("rule") or ""
    proof = st.get("proof") or {}
    t = S._today(today)
    deadline = st.get("deadline")
    strength, why = "one_source", None
    sent = ""
    ns: list[int] = []
    ema_pay = any(r["source"] == "production" and r["kind"] == "payment" for r in rows)
    if st.get("stored_differs"):
        live = st.get("live")
        sent = f"{S.LABELS.get(state, state)}: {st.get('state_basis') or 'as the last detection run stored it'}."
        if live:
            sent += (f" On the newest data RemitWatch would say {live.get('label') or live.get('work_state')}: "
                     f"{live.get('state_basis') or ''}; the next run updates it.")
        strength, why = ("inferred", "the state the last detection run stored") if live else ("one_source", "the daily run's state")
    elif state == "done":
        if rule == "paid_later_ema":
            ns = _mark(rows, f"pay:{proof.get('date')}:{proof.get('check_tail')}")
            sent = f"Paid later: {usd(proof.get('cents'))} by {proof.get('payer') or 'the payer'} on a check with no 835 {_refs(ns)}."
        elif rule == "rebilled_paid":
            ns = _mark(rows, f"pay:{proof.get('date')}:{proof.get('check_tail')}")
            sent = (f"Rebilled to {proof.get('payer') or 'another payer'}, paid: {usd(proof.get('cents'))} on {day(proof.get('date'))} on a check "
                    f"with no 835 {_refs(ns)}; EMA shows the line settled.")
        elif rule in DONE_835_PAY:
            ns = _decide_rows(rows, [r["n"] for r in rows if r["source"] == "835" and r["kind"] == "payment"
                                     and (r["date"] or "") >= (f.get("payment_date") or "")])
            sent = proof.get("text", "Paid") + f" {_refs(ns)}."
            strength = "proven" if ema_pay else "one_source"
        elif rule == "moved_to_patient":
            ns = _mark(rows, "reversal") + [r["n"] for r in rows if r["source"] == "835" and r["date"] == proof.get("date") and proof.get("date")]
            ns += _mark(rows, "patient", limit=3) + (_mark(rows, "own_remit") if proof.get("source") == "production" else [])
            _decide_rows(rows, ns)
            sent = ("Moved to the patient: " if proof.get("source") == "835" else "") + proof.get("text", "") + f" {_refs(ns)}."
            strength = "proven" if (proof.get("source") == "835" and any("patient" in r["tags"] for r in rows)) or (
                proof.get("source") == "production" and any("own_remit" in r["tags"] for r in rows)) else "one_source"
        elif rule in DONE_PAID_BEFORE:
            ns = [r["n"] for r in rows if r["source"] in ("835", "production") and r["kind"] == "payment" and r["date"] == proof.get("date")]
            ns += _mark(rows, "own_remit")
            _decide_rows(rows, ns)
            sent = proof.get("text", "Already paid") + f" {_refs(ns)}."
            own_dup = any(c["code"].split("-")[-1] in S.DUP_CARCS for r in rows if "own_remit" in r["tags"] for c in r["codes"])
            strength = "proven" if (proof.get("source") == "835" and ema_pay) or (proof.get("source") == "production" and own_dup) else "one_source"
        elif rule in DONE_EMA_PAY:
            ns = _mark(rows, "pay_no835", "pay_835") + _mark(rows, "own_remit")
            sent = proof.get("text", "Paid") + f" {_refs(ns)}."
            strength = "proven" if rule == "forwarded_paid" and any("own_remit" in r["tags"] for r in rows) else "one_source"
        elif rule == "auto_closed":
            ns = _mark(rows, "auto_note", "rw:paid_on_later_remit")
            sent = f"Closed by the analysis: {f.get('auto_note')} {_refs(ns)}."
        elif rule == "person_paid":
            ns = _mark(rows, "rw:manual", limit=1)
            sent = f"{st['state_basis']} {_refs(ns)}."
            why = "a person's status"
        else:
            sent = (st.get("state_basis") or "Done") + "."
    elif state == "waiting":
        ns = _mark(rows, "work", "fax", "rw:manual", "ar_note", limit=2) if rule != "forwarded" else _mark(rows, "own_remit")
        sent = f"Waiting: {st['state_basis']} {_refs(ns)}."
    elif state == "accepted":
        ns = _mark(rows, "rw:manual", "rw:hidden", limit=1)
        sent = f"{st['state_basis']} {_refs(ns)}."
        if rule == "policy":
            why = "the owner's write-off policy"
        elif st.get("person"):
            why = "a person's status"
    elif state == "expired":
        ns = _mark(rows, "own_remit")
        if st.get("answered"):
            ns += [r["n"] for r in rows if r["date"] == st["answered"].get("date") and r["source"] in ("835", "production")]
            _decide_rows(rows, ns)
        sent = f"Expired: {_lower(st['state_basis'])} {_refs(ns)}."
        strength, why = "inferred", "past every window"
    elif state == "cant_check":
        sent = f"Can't be checked: {st['state_basis']}" + ("" if str(st.get("state_basis") or "").endswith(".") else ".")
        strength, why = "inferred", "no source carries this money"
    elif rule in ("owner_decision", "fees") or (f.get("signal") in S.CONTRACT + S.FEES and rule != "person_started"):
        ns = _mark(rows, "raised", "own_remit", limit=2)
        sent = f"{st.get('state_basis')}: {f.get('title') or ''} {_refs(ns)}."
    else:
        sent, ns, strength, why = _work_now_sentence(conn, fx, story, st, t)
    dup = _duplicates(conn, f)
    if dup:
        if len(dup) == 1:
            sent += f" Listed twice: the same service as item {dup[0]['finding_id']}."
        else:
            ids = ", ".join(d["finding_id"] for d in dup[:6]) + (", ..." if len(dup) > 6 else "")
            sent += f" Listed {len(dup) + 1} times: the same service as items {ids}."
    tw = story.get("twin")
    if tw and tw["insurer_paid_cents"] and state != "done" and not any((p["cents"] or 0) > 0 for p in fx["ema"]["pays"]):
        tn = [r["n"] for r in rows if "twin" in r["tags"]]
        sent += (f" The payment on this day belongs to the surgery center's facility claim ({usd(tw['insurer_paid_cents'])}), not this line"
                 + (f" {_refs(tn)}" if tn else "") + ".")
    if st.get("person") and st.get("proof") and state != "done" and rule != "person_billed_paid":
        sent += " A person's status decides this item: " + (st["proof"].get("text") or "") + ": mark it Paid if that settles it."
    return {"state": state, "label": S.LABELS.get(state, ""), "sentence": _clean(sent), "rows": sorted(set(ns)), "strength": strength,
            "rule": why or rule, "code": rule, "follow_up_on": st.get("follow_up_on"), "state_basis": st.get("state_basis"),
            "stored": bool(st.get("stored")), "deadline": deadline, "route": st.get("route")}


def _lower(text: str) -> str:
    """A sentence continued after a colon: its first letter in lower case, unless it starts with a name or code."""
    t = str(text or "")
    return t[:1].lower() + t[1:] if t[:1].isupper() and (len(t) < 2 or t[1:2].islower()) and t.split(" ", 1)[0] in (
        "Every", "The", "Past", "Paid", "Already", "Settled", "Forwarded", "Moved", "Marked", "No", "Nothing", "A", "An", "Not",
        "Started", "Billed", "This", "It", "Its") else t


def _short(basis: Any, n: int = 160) -> str:
    """A deadline's basis cut to its first sentence, for sentences that name it."""
    b = str(basis or "").strip()
    cut = S.sentence_end(b)
    b = b[:cut] if cut is not None else b.rstrip(".")
    return b if len(b) <= n else b[:n].rsplit(" ", 1)[0] + "..."


def _carcs(fx: dict) -> str:
    """The item's reasons with their groups, as its own remittance gives them (CO-97, not 97)."""
    return S._carcs_text(fx)


def _reason_words(fx: dict) -> str:
    """The item's reason and remark codes with their meanings, for steps that quote them."""
    f = fx["item"]
    c = _carcs(fx)
    words = c + (f" ({_meaning(c)})" if "-" in c and _meaning(c) else "")
    r = _rarc_words(f.get("rarc"))
    return words + (f" with {r}" if r else "")


def _clean(s: str) -> str:
    s = re.sub(r"\s+\(\)", "", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\s+\.", ".", s)
    s = re.sub(r"\.\.(?!\.)", ".", s)
    s = s.replace("—", ",").replace("–", "-")
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _window(deadline: str | None, today: str, legal: str | None = None, route: str = "appeal", second: str | None = None,
            basis: str | None = None) -> str:
    """The item's window in one sentence, in the words of the work its route asks for (an appeal, a corrected claim, a
    bill to the second payer, or only the payer's window). legal: the payer's own limit when detection found it later than
    the safe date."""
    if not deadline:
        return ""
    if route in ("refund", "post", "bill_patient", "contract", "policy", "fees", "nothing", "wait"):
        return ""
    if route in ("appeal", "rate"):
        if deadline >= today:
            return f" You can still appeal until {day(deadline)}."
        if legal and legal >= today:
            return f" The safe date to appeal passed on {day(deadline)}; the payer's own limit is {day(legal)}, so it can still be sent."
        return f" The appeal window closed on {day(legal if legal and legal > deadline else deadline)}."
    if route == "corrected":
        if deadline >= today:
            return f" You can still send a corrected claim until {day(deadline)}."
        if legal and legal >= today:
            return f" The safe date passed on {day(deadline)}; the payer's own limit is {day(legal)}, so it can still be sent."
        return f" The window to send a corrected claim closed on {day(legal if legal and legal > deadline else deadline)}."
    if route == "bill_secondary":
        return (f" Bill it by {day(deadline)} ({_poss(second or 'the second payer')} filing limit)." if deadline >= today else
                f" {_poss(second or 'The second payer')} filing limit passed on {day(deadline)}.")
    if route == "cob":
        return f" Act by {day(deadline)}" + (f" ({_short(basis, 90)})." if basis else ".") if deadline >= today else ""
    if deadline >= today:
        return f" The payer's window closes on {day(deadline)}."
    if legal and legal >= today:
        return f" The safe date passed on {day(deadline)}; the payer's own limit is {day(legal)}."
    return f" The payer's window closed on {day(legal if legal and legal > deadline else deadline)}."


def _same_day_lines(conn: sqlite3.Connection, f: dict) -> list[dict]:
    """The other procedures of the item's patient on its date of service (states.same_day_lines)."""
    return S.same_day_lines(conn, f)


def _short_check(conn: sqlite3.Connection, fx: dict) -> dict | None:
    """Why a short payment may be right, or why it is not (states.short_check)."""
    return S.short_check(conn, fx)


def _late_words(st: dict) -> str:
    """'Its own deadline passed on <date>; <window> is open until <date>.' for an item open only under a later window."""
    late = st.get("late_window") or {}
    own = S._later(st.get("deadline"), st.get("legal_deadline"))
    if not late or not own:
        return ""
    return f"Its own deadline passed on {day(own)}; {late['what']} is open until {day(late['date'])} ({_short(late['basis'], 120)})."


def _work_now_sentence(conn: sqlite3.Connection, fx: dict, story: dict, st: dict, today: str) -> tuple[str, list[int], str, str | None]:
    f = fx["item"]
    rows = story["rows"]
    o = fx.get("outcome") or {}
    oc = o.get("outcome")
    rule = st.get("rule") or ""
    sig = f.get("signal") or ""
    deadline = st.get("deadline")
    route = st.get("route") or S.route_of(fx, st, conn)
    ns: list[int] = []
    started = rule == "person_started"
    late = _late_words(st) if rule == "open_late" else ""
    window = "" if late else _window(deadline, today, st.get("legal_deadline"), route, S._ev(f).get("second_payer"), st.get("deadline_basis"))
    resolved = str(o.get("status") or "").lower().startswith("resolved") or o.get("status_class") in ("paid", "settled")
    carcs = _carcs(fx)
    wo = usd(o.get("written_off") or o.get("contractual_routine"))
    void = [r for r in rows if r["kind"] == "charge_void"]
    hint = str(f.get("hint") or "")
    noted = any(a.get("has_note") for a in fx["ar"])
    nobody = ("" if started else ("; EMA's billing note names no appeal, call or resubmission" if noted else " and nobody has worked it"))

    def done(text: str, marks: list[int], strength: str = "one_source", why: str | None = None):
        if started:
            text = f"{_started_words(st)}. {text}"
        if late:
            text = f"{late} {text}"
        return text, marks, strength, why
    if void and story["balance"].get("charge_cents", 1) <= 0:
        ns = _decide_rows(rows, [r["n"] for r in void] + _mark(rows, "own_remit"))
        return done(f"Check first: EMA reversed this charge on {day(void[0]['date'])} {_refs(ns)}, so the claim was voided or re-created and the "
                    "service may be billed and paid on another claim" + (f" ({hint})" if hint else "") + ".", ns)
    if rule == "person_reopened":
        ns = _mark(rows, "own_remit")
        return done(f"{st.get('state_basis')}" + (f" {_refs(ns)}" if ns else "") + ".", ns)
    if rule == "check_first":
        h = S._archive_hint(f) or {}
        ns = _mark(rows, "own_remit")
        return done(f"Check first: {S._hint_text(h) or st.get('state_basis')} {_refs(ns)}. RemitWatch did not close it because the payment is not on "
                    "file to prove it.", ns, "inferred", f"the 835 archive's rule '{h.get('rule') or 'archive'}': a payment it cannot tie to this "
                    "service for sure")
    if hint.startswith(S.CHECK_HINTS):
        ns = _mark(rows, "hint", "own_remit")
        return done(f"Check first: {hint} {_refs(ns)}. The analysis did not close it because the proof was not complete.", ns, "inferred",
                    "a payment found that does not meet every closing test")
    if rule == "possible_overpayment":
        ns = _mark(rows, "own_remit") + _mark(rows, "pay_835", "pay_no835", limit=2)
        return done(f"Paid twice: {st.get('state_basis')} {_refs(ns)}. Nothing more is owed; the extra is the owner's decision to refund.", ns)
    if rule == "settled_in_ema":
        se = st.get("settled") or {}
        ns = _mark(rows, "pay_no835", "pay_835", "patient", limit=4) + _mark(rows, "own_remit")
        return done(f"Check first: EMA shows this line settled: insurance paid {usd(se.get('ins'))}, the patient paid {usd(se.get('pat'))}, "
                    f"{usd(se.get('open'))} open {_refs(ns)}. Find which payer paid and whether it settles this service; if it does, close it as "
                    "Paid.", ns)
    if rule == "patient_paid_co":
        se = st.get("settled") or {}
        ns = _mark(rows, "patient", limit=3) + _mark(rows, "own_remit")
        return done(f"EMA shows the patient paid {usd(se.get('pat'))} and the line settled {_refs(ns)}, but {_poss(f.get('payer_group') or 'the payer')} "
                    f"denial ({carcs}) puts this service on the practice, not the patient: appeal it and refund the patient if the payer pays, or "
                    "refund the patient if the denial stands.", ns)
    if rule == "decided_no_appeal":
        ns = _mark(rows, "ar_note", limit=1) + _mark(rows, "own_remit")
        return done(f"{st.get('state_basis')} {_refs(ns)}. If that decision stands, let it go with that reason.{window}", ns)
    if rule == "note_unread":
        ns = _mark(rows, "ar_note", limit=1) + _mark(rows, "own_remit")
        return done(f"Read EMA's billing note first: {_lower(st.get('state_basis') or '')} {_refs(ns)}.{window}", ns)
    if rule == "credit_unposted_takeback":
        ns = _mark(rows, "reversal", "takeback", "pay_835", "pay_no835", limit=4)
        return done(f"Not money owed back: {_lower(st.get('state_basis') or '')} {_refs(ns)}.", ns, "proven" if fx["r835"]["rows"] else "one_source")
    if rule in ("person_billed_paid",):
        ns = _mark(rows, "rw:manual", limit=1) + _mark(rows, "pay_no835", "pay_835", limit=2)
        return done(f"{st.get('state_basis')} {_refs(ns)}.", ns)
    if rule in ("work_answered", "work_stale", "follow_up_due"):
        ns = _mark(rows, "work", "fax", "ar_note", "rw:manual", limit=2)
        if st.get("answered"):
            ns += [r["n"] for r in rows if r["date"] == st["answered"].get("date") and r["source"] in ("835", "production")]
        _decide_rows(rows, ns)
        inferred = (st.get("answered") or {}).get("inferred")
        return done(f"Work now: {st['state_basis']} {_refs(ns)}.", ns, "inferred" if inferred else "one_source",
                    "a repeat answer of the same claim read as the answer to undated work" if inferred else None)
    if rule == "credit_refund":
        ns = _mark(rows, "pay_no835", "pay_835", limit=3)
        return done(f"Overpaid: {st['state_basis']} {_refs(ns)}.", ns)
    if rule == "bill_patient":
        ns = _mark(rows, "reversal") + _mark(rows, "patient", limit=2)
        return done(f"Moved to the patient, not yet collected: {st['state_basis']} {_refs(ns)}.", ns)
    if rule in ("forwarded", "forwarded_late"):
        ns = _mark(rows, "own_remit")
        return done(f"{st['state_basis']} {_refs(ns)}.", ns)
    if sig == "NO_ANSWER":
        ev = S._ev(f)
        waited = ev.get("days_waiting") or S._days(ev.get("sent") or f.get("service_date"), today)
        tat = ev.get("turnaround_days") or _turnaround(conn, f.get("payer_group"))
        both = not fx["r835"]["rows"] and not fx["ema"]["pays"]
        ns = _mark(rows, "charge", "claim_status", limit=2)
        try:
            from .ema_checks import turnaround_text
            usual = turnaround_text(f.get("payer_group"), tat)
        except Exception:       # noqa: BLE001  (an older checks module)
            usual = f"{f.get('payer_group') or 'This payer'} answers 95% of claims within {tat} days"
        return done(f"Unanswered: no answer from any payer in {waited} days; {usual} {_refs(ns)}.", ns,
                    "proven" if both and fx["ema"]["lines"] else "one_source")
    if rule == "payment_never_arrived":
        ns = _mark(rows, "own_remit")
        paid = f.get("paid_cents") or f.get("shortfall_cents")
        return done(f"The 835 reported {usd(paid)} paid on an informational remittance {_refs(ns)}, and no money came with it or after "
                    "it.", ns, "proven" if ns else "one_source")
    if sig == "REMIT_NOT_POSTED" or rule == "post_payment":
        ns = _mark(rows, "own_remit")
        paid = f.get("paid_cents") or f.get("shortfall_cents")
        return done(f"The 835 paid {usd(paid)} {_refs(ns)}, but EMA never posted it.", ns, "proven" if ns else "one_source")
    if sig == "SECONDARY_NOT_BILLED":
        return done(*_secondary_sentence(fx, story, st, today)[:2])
    if sig in ("REJECTED_PRE_PAYER", "NOT_SENT"):
        ns = _mark(rows, "ar", "ema_status", "claim_status", limit=2)
        word = "Never reached the payer" if sig == "REJECTED_PRE_PAYER" else "Never left EMA"
        return done(f"{word}: {f.get('title') or ''} {_refs(ns)}.", ns)
    if f.get("category") in S.SHORT:
        s_, n_, st_, why_ = _short_sentence(conn, fx, story, st, today)
        return done(s_, n_, st_, why_)
    if str(f.get("carc") or "") in COB_CARCS:
        s_, n_, st_, why_ = _cob_sentence(fx, story, st, today)
        return done(s_, n_, st_, why_)
    if oc == "auto_written_off":
        proven = o.get("evidence_level") == "rule_proven"
        ns = _mark(rows, "adj_at_posting") + _mark(rows, "own_remit") + (_mark(rows, "era") if proven else [])
        if started:
            lead = "EMA shows this claim as Resolved;" if resolved else "EMA shows this line closed;"
        else:
            lead = ("EMA shows this claim as Resolved, but nobody worked this line." if resolved else
                    "EMA shows this line closed, but nobody worked it.")
        evid = "Recorded by EMA's ERA export." if proven else "Inferred from the posting date."
        return done(f"{lead} EMA's posting rule wrote off the denial ({carcs}, {wo}) on {day(o.get('outcome_on'))}, the day the payer's remittance "
                    f"was posted. The payer paid nothing.{window} {evid} {_refs(ns)}", ns, "proven" if proven else "inferred",
                    None if proven else "posting day: an adjustment posted the day the remittance posted is EMA's posting rule")
    if oc == "written_off_while_pending":
        ns = _mark(rows, "adj_at_posting") + _mark(rows, "own_remit")
        asked = "information" if any(c.endswith(("-16", "-252", "-226")) for c in (o.get("carcs") or [])) else "a review"
        return done(f"EMA wrote this off the day it posted, but the payer had not finished: it asked for {asked}. This can usually be corrected "
                    f"and sent again.{window} {_refs(ns)}", ns, "inferred", "posting day")
    if oc == "lost_to_timely_filing":
        first = next((r for r in rows if r["source"] == "835" and r["kind"] == "adjudication"), None)
        first_carc = (first["codes"][0]["code"] if first and first["codes"] else f.get("carc")) or "another reason"
        ns = _mark(rows, "adj:CO-29") + ([first["n"]] if first else [])
        return done(f"This was written off for late filing. The payer first denied it for {first_carc}; it ran out of time before anyone fixed it. "
                    f"{_refs(ns)}", ns)
    if oc in ("written_off_later", "contractual_later"):
        ns = _mark(rows, "adj_later") + _mark(rows, "own_remit")
        return done(f"A person wrote this off in EMA on {day(o.get('outcome_on'))} ({carcs}, {wo}), on a day with no remittance posted, while the "
                    f"payer had paid nothing; RemitWatch keeps it on the list while it can still be recovered (owner policy: pursue).{window} {_refs(ns)}", ns)
    if oc in ("written_off_at_posting", "contractual_at_posting"):
        ns = _mark(rows, "adj_at_posting") + _mark(rows, "own_remit")
        if o.get("contractual_routine") and not o.get("written_off"):
            return done(f"EMA closed this line with a routine contractual adjustment ({usd(o.get('contractual_routine'))}) the day the remittance "
                        f"posted {_refs(ns)}, while the payer paid nothing for it ({carcs}).{window}", ns)
        return done(f"EMA wrote off {carcs} ({wo}) the day the remittance posted {_refs(ns)}; the payer paid nothing.{window}", ns, "inferred",
                    "posting day")
    if oc == "paid_after_partial":
        ns = _mark(rows, "pay_no835", "pay_835", limit=2) + _mark(rows, "own_remit")
        return done(f"Partly paid: EMA shows {usd((o.get('paid_after_same') or 0) + (o.get('paid_after_other') or 0))} paid since {_refs(ns)}, less "
                    f"than half of what this should bring.{window}", ns)
    if oc == "paid_before" or rule == "paid_before":
        ns = _decide_rows(rows, _mark(rows, "own_remit") + [r["n"] for r in rows if r["kind"] == "payment"
                                                            and (r["date"] or "") < (f.get("payment_date") or "")])
        return done(f"Check first: this line was paid before this denial {_refs(ns)}; the denial may be a duplicate or a second payer's answer.", ns)
    ns = _mark(rows, "own_remit")
    bal = story["balance"]
    open_ = bal.get("open_cents")
    head = "Unpaid" + nobody
    if bal.get("basis") == "production":
        ns += _mark(rows, "ar", limit=1)
        shows = (f"EMA shows {usd(open_)} still open" if (open_ or 0) > 0 else
                 "EMA shows the line closed with no payment on it" if (open_ or 0) == 0 else f"EMA shows a credit of {usd(-open_)}")
        return done(f"{head}: {f.get('payer_group') or 'the payer'} answered {carcs} {_refs(ns)}, and {shows}.{window}", ns,
                    "proven" if fx["r835"]["rows"] and (open_ or 0) > 0 else "one_source")
    return done(f"{head}: {f.get('payer_group') or 'the payer'} answered {carcs} {_refs(ns)}.{window}", ns)


def _started_words(st: dict) -> str:
    """'Started by <who> on <date>' from a person-started state."""
    b = str(st.get("state_basis") or "")
    return b.split(".", 1)[0] if b.startswith("Started by") else "Started by a person"


def _secondary_sentence(fx: dict, story: dict, st: dict, today: str) -> tuple[str, list[int]]:
    """A second payer never billed: what the first payer paid and left, how EMA closed the share instead of billing the
    second payer, and the second payer's filing limit (never the words of a denial to appeal, M14)."""
    f = fx["item"]
    rows = story["rows"]
    ev = S._ev(f)
    second = _second_name(fx)
    first = ev.get("first_payer") or f.get("payer_group") or "The first payer"
    share = int(f.get("shortfall_cents") or 0)
    word = {"coinsurance": "of coinsurance", "copay": "of copay", "deductible": "of deductible"}.get(str(ev.get("share") or ""), "")
    ns = _mark(rows, "own_remit", limit=1)
    wo_rows = [r for r in rows if r["source"] == "production" and r["kind"] == "adjustment"
               and not any(c["code"].split("-")[-1] in CONTRACTUAL for c in r["codes"])]
    how = ""
    if ev.get("ema_wrote_off_share") and wo_rows:
        w = wo_rows[-1]
        code = w["codes"][0]["code"] if w["codes"] else "an adjustment"
        how = f"; EMA wrote it off ({code}, {usd(w['amount_cents'])}) on {day(w['date'])} {_refs([w['n']])} instead of billing {second}"
        _decide_rows(rows, [w["n"]])
    elif ev.get("ema_wrote_off_share"):
        how = f"; EMA wrote it off instead of billing {second}"
    deadline = st.get("deadline")
    by = (f" Bill {second} by {day(deadline)} (its filing limit)." if deadline and deadline >= today else
          (f" {_poss(second)} filing limit passed on {day(deadline)}." if deadline else ""))
    return (f"Not billed to {second}: {first} paid {usd(f.get('paid_cents'))} on {day(f.get('payment_date'))} and left {usd(share)}"
            + (f" {word}" if word else "") + f" {_refs(ns)}{how}.{by}", ns)


def _second_name(fx: dict) -> str:
    """The second payer to bill: the item's, or EMA's own secondary payer when the item names a group of many insurers
    (a Medigap carrier, a Medicaid plan the group does not name)."""
    ev = S._ev(fx["item"])
    second = ev.get("second_payer") or "the second payer"
    try:
        from . import fax
        many = fax.shared_group(second)
    except Exception:       # noqa: BLE001
        many = False
    if many:
        named = next((ln.get("secondary_payer") for ln in fx["ema"]["lines"] if ln.get("secondary_payer")
                      and str(ln.get("secondary_payer")).strip().lower() not in ("none", "-", "")), None)
        if named:
            return f"{named} (one of the {second})"
    return second


def _short_sentence(conn: sqlite3.Connection, fx: dict, story: dict, st: dict, today: str) -> tuple[str, list[int], str, str | None]:
    """A short payment (an underpayment or a downcode): what the payer paid against what the line brings, how EMA booked
    the difference, and what may explain it."""
    f = fx["item"]
    rows = story["rows"]
    o = fx.get("outcome") or {}
    oc = o.get("outcome")
    grp = f.get("payer_group") or "the payer"
    code = f.get("procedure_code")
    billed = f.get("original_procedure_code") or f.get("billed_code")
    ns = _mark(rows, "own_remit")
    if f.get("category") == "downcoding" and billed and billed != code:
        head = (f"Paid at a lower level: billed {billed}, paid as {code} ({usd(f.get('paid_cents'))}) {_refs(ns)}, "
                f"{usd(f.get('shortfall_cents'))} less than {billed} brings.")
    else:
        head = (f"Paid short: {grp} allowed {usd(f.get('allowed_cents'))} for {code} against its usual {usd(f.get('expected_cents'))} "
                f"{_refs(ns)}, {usd(f.get('shortfall_cents'))} less.")
    ema = ""
    reason = _carcs(fx)
    reason = "as a contractual adjustment" if reason == "the denial" else f"({reason})"
    if oc in ("auto_written_off", "written_off_at_posting", "contractual_at_posting", "written_off_while_pending"):
        en = _mark(rows, "adj_at_posting")
        ns += en
        ema = f" EMA wrote the difference off {reason} the day the remittance posted {_refs(en)}: nobody asked the payer about it."
    elif oc in ("written_off_later", "contractual_later"):
        en = _mark(rows, "adj_later")
        ns += en
        ema = f" A person wrote the difference off in EMA on {day(o.get('outcome_on'))} {_refs(en)}."
    chk = _short_check(conn, fx)
    window = _window(st.get("deadline"), today, st.get("legal_deadline"), st.get("route") or "rate")
    if chk and chk["kind"] == "reduced":
        return (f"Check first: {chk['text']}. {head}{ema}", ns, "inferred", "a same-day procedure or the facility rate may explain the cut")
    if chk and chk["kind"] == "top_cut":
        return (f"{head}{ema} {chk['text']}.{window}", ns, "one_source", None)
    return (f"{head}{ema}{window}", ns, "one_source", None)


def _cob_sentence(fx: dict, story: dict, st: dict, today: str) -> tuple[str, list[int], str, str | None]:
    """A coordination-of-benefits denial (CARC 22): the payer says another plan pays first; what it said before, what the
    patient paid, and whether EMA posted the payer's reversal."""
    f = fx["item"]
    rows = story["rows"]
    grp = f.get("payer_group") or "the payer"
    ns = _mark(rows, "own_remit")
    rev = [r for r in rows if r["source"] == "835" and r["kind"] == "reversal"]
    own_n = min(ns) if ns else 10 ** 9
    earlier = [r for r in rows if r["source"] == "835" and r["kind"] in ("adjudication", "payment") and r["n"] < own_n]
    pat = sum(r["amount_cents"] or 0 for r in rows if "patient" in r["tags"] and r.get("counted", True) and r["kind"] == "patient_payment")
    tot = S._ema_line_totals(fx)
    parts = [f"Coordination of benefits: {grp} says another plan pays first ({_carcs(fx)}) {_refs(ns)}."]
    if rev and earlier:
        rn = _decide_rows(rows, [rev[-1]["n"], earlier[-1]["n"]])
        ns += rn
        parts.append(f"It had answered on {day(earlier[-1]['date'])} and reversed that answer on {day(rev[-1]['date'])} {_refs(rn)}.")
    if pat:
        parts.append(f"The patient has paid {usd(pat)} on this line.")
    if rev and tot["n"] and tot["balance"] <= 0:
        parts.append("EMA never posted the reversal, so it still shows the line settled.")
    parts.append("This is a task to find the plan that pays first, not an appeal." + _window(st.get("deadline"), today, st.get("legal_deadline"),
                                                                                             "cob", basis=st.get("deadline_basis")))
    return " ".join(parts), ns, "one_source", None


def _turnaround(conn: sqlite3.Connection, group: str | None) -> int:
    """The payer group's 95th-percentile answer time from the date of service (ema_checks.turnaround)."""
    try:
        from . import ema_checks as ec
        return ec.turnaround_days(conn, group, "svc_p95")
    except Exception:       # noqa: BLE001  (no EMA tables or meta on this copy)
        return S.response_days(conn, group)


def _duplicates(conn: sqlite3.Connection, f: dict) -> list[dict]:
    """Other open items for the same service (bill, date and code): the same service listed more than once."""
    if not f.get("patient_control_number") or not f.get("procedure_code") or not f.get("service_date") or not S._table(conn, "findings"):
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
    return sorted(out, key=lambda d: (d.get("payment_date") or "", d["finding_id"]))


def _siblings(conn: sqlite3.Connection, f: dict) -> list[dict]:
    """Other open items of the same visit (the bill's other codes on the date of service), to work together."""
    if not f.get("patient_control_number") or not f.get("service_date") or not S._table(conn, "findings"):
        return []
    b = S.bill_of(f["patient_control_number"])
    out = []
    for fid, sig, pcn, code, carc, cents, dl in conn.execute(
            """SELECT finding_id, signal, patient_control_number, procedure_code, carc, shortfall_cents, appeal_deadline FROM findings
               WHERE patient_control_number >= ? AND patient_control_number < ? AND service_date = ? AND finding_id <> ?
                 AND COALESCE(procedure_code, '') <> ? AND COALESCE(stale, 0) = 0 AND status IN ('open','in_progress','appealed')""",
            (b, b + "~", f["service_date"], f["finding_id"], f.get("procedure_code") or "")):
        if S.bill_of(pcn) == b:
            out.append({"finding_id": fid, "signal": sig, "code": code, "carc": carc, "cents": cents, "deadline": dl})
    return sorted(out, key=lambda d: (d.get("deadline") or "9999", d["finding_id"]))[:8]


def _sibling_words(sib: list[dict]) -> str:
    """The visit's other open items in words, repeats of one code folded: '17003 x3 (denied: too many units or too often,
    $270.00, due Jun 1, 2026)'."""
    try:
        from .insights import SIGNAL_LABELS
    except Exception:       # noqa: BLE001
        SIGNAL_LABELS = {}
    by: dict[tuple, list] = {}
    for s_ in sib:
        by.setdefault((s_["code"] or "the claim", s_["signal"]), []).append(s_)
    out = []
    for (code, sig), group in by.items():
        label = (SIGNAL_LABELS.get(sig) or ("",))[0] or str(sig or "").replace("_", " ").lower()
        label = label[:1].lower() + label[1:]
        cents = sum(g["cents"] or 0 for g in group)
        due = min((g["deadline"] for g in group if g.get("deadline")), default=None)
        out.append(f"{code}" + (f" x{len(group)}" if len(group) > 1 else "") + f" ({label}, {usd(cents)}" + (f", due {day(due)}" if due else "") + ")")
    return "; ".join(out)


def item_verdict(conn: sqlite3.Connection, finding_id: str, *, ledger: Any = None, today: str | None = None) -> dict:
    """The item's state as plain sentences naming the decisive story rows, with the strength of the proof:
    {'state', 'sentence', 'rows', 'strength', 'rule', 'label', 'follow_up_on', 'state_basis', 'stored'}."""
    return explain(conn, finding_id, ledger=ledger, today=today)["verdict"]


# ----------------------------------------------------------------------------------------------------- the next step
def _mac_portal(f: dict) -> str:
    """The Medicare contractor's portal by name where the payer group says which contractor it is (CGS: myCGS; WPS: the WPS
    portal), else the contractor's portal."""
    grp = f.get("payer_group") or ""
    if re.search(r"^Medicare KY|CGS", grp):
        return "myCGS"
    if re.search(r"^Medicare IN|WPS", grp):
        return "the WPS portal"
    return "the contractor's portal"


def _fax_number(conn: sqlite3.Connection, f: dict) -> tuple[bool, str]:
    """Whether a confirmed fax number for the payer's appeals is on file (a send then goes there, preview first). Medicare's
    contractors take no faxed redeterminations (CGS's job aid), so a Medicare appeal is never faxed from here."""
    if f.get("payer_kind") == "medicare":
        return False, (f"Medicare's contractor takes redeterminations through {_mac_portal(f)} or by mail, not by fax: file it there.")
    try:
        from . import fax
        nb = fax.number_for(conn, f.get("payer_group"), f.get("payer_kind"), 1)
        if fax.shared_group(f.get("payer_group")):
            return False, fax.shared_why(f.get("payer_group"))
        if nb.get("number") and nb.get("auto_ok", 1):
            return True, f"Fax to {f.get('payer_group')} appeals at the confirmed number ending {nb['number'][-4:]}, preview first."
        if nb.get("number"):
            return False, f"{_poss(f.get('payer_group'))} appeal fax number was changed and nobody has confirmed it: confirm it on the Faxes page."
        return False, ""
    except Exception:       # noqa: BLE001  (no fax tables on this copy)
        return False, ""


# Where a first-level dispute goes, by payer group (docs/RESEARCH_DOMAIN.md: Anthem and Aetna disputes on Availity,
# Cigna's reconsideration on CignaforHCP or its secure fax, UnitedHealthcare's on UHCprovider.com, CGS's redeterminations
# on myCGS and never by fax). First match wins; the pattern is on the payer group's name.
APPEAL_CHANNELS = [
    (r"^Medicare KY|CGS", "medicare", "File a redetermination on myCGS (CGS takes no faxed redeterminations), attaching the packet's PDF."),
    (r"^Medicare IN|WPS", "medicare", "File a redetermination on the WPS portal (or by mail), attaching the packet's PDF; Medicare takes no "
                                     "faxed redeterminations."),
    (r"^Medicare|^Railroad", "medicare", "File a redetermination on the Medicare contractor's portal (or by mail), attaching the packet's PDF."),
    (r"^Anthem", None, "File it on Availity as a Claim Payment Dispute (Claims & Payments > Disputes), attaching the packet's PDF."),
    (r"^Humana", None, "File it on Availity (Dispute Claim), attaching the packet's PDF."),
    (r"^Aetna", None, "File it on Availity (Disputes & Appeals), attaching the packet's PDF."),
    (r"^UMR\b", None, "File it on umr.com, attaching the packet's PDF."),
    (r"^(UnitedHealthcare|UHC)", None, "File a claim reconsideration on UHCprovider.com, attaching the packet's PDF."),
    (r"^Cigna", None, "File it on CignaforHCP (Claims > Search Claims > Start a reconsideration), or fax it to the confirmed number."),
]


def _appeal_channel(f: dict) -> str:
    """Where the payer takes a first-level dispute, in one step (M15)."""
    grp, kind = f.get("payer_group") or "", f.get("payer_kind")
    for rx, k, words in APPEAL_CHANNELS:
        if re.search(rx, grp) and (k is None or k == kind):
            return words
    return (f"Send it the way {_poss(grp or 'the payer')} provider manual says (its portal first); add its appeal fax number on the Faxes "
            "page to fax it from here.")


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
    links = list(fx["ema"]["links"]) or ([fx["item"]["ema_link"]] if fx["item"].get("ema_link") else [])
    if not tpl or not links or "{claim}" not in tpl:
        return None
    n = str(links[0]).strip()
    # the template's placeholder is {claim} (ema_reports.claim_url_template); only an EMA claim number of 6 to 12 digits
    return tpl.replace("{claim}", n) if re.fullmatch(r"\d{6,12}", n) else None


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


def _global_days(code: Any) -> int | None:
    try:
        from .codes import GLOBAL_DAYS
        return GLOBAL_DAYS.get(str(code or ""))
    except Exception:       # noqa: BLE001
        return None


def _corrected_change(f: dict, fx: dict) -> str:
    """The exact change a corrected claim makes, by signal, reason and remark."""
    code = f.get("procedure_code") or "the line"
    sig = f.get("signal") or ""
    rarcs = {x.strip() for x in str(f.get("rarc") or "").split(",") if x.strip()}
    mods = str(f.get("modifiers") or "")
    pccn = _payer_claim_number(fx)
    tail_ = (f"; resubmit as a corrected claim, frequency 7, with the original claim number {pccn}" if pccn else
             "; resubmit as a corrected claim, frequency 7, with the payer's original claim number")
    same = _same_day(None, f)
    minor = bool(same) and all((_global_days(c) or 0) in (0, 10) for c in same)
    if sig == "EM_BUNDLED_NO_MODIFIER":
        return f"Add modifier 25 to {code} (when the note shows a separate problem){tail_}"
    if sig == "EM_BUNDLED_MOD57_MINOR" or (sig == "DENIED_MISSING_MODIFIER" and "57" in mods and minor):
        return (f"Change modifier 57 to 25 on {code} when the note shows a separate evaluation: the same-day procedures "
                f"({', '.join(same)}) have a 0- or 10-day global period, so 57 does not apply{tail_}; repeat every other line unchanged")
    if sig == "DENIED_MISSING_MODIFIER":
        return f"Add or correct the modifier on {code} that the remark names ({_rarc_words(f.get('rarc')) or 'see the remittance'}){tail_}"
    if sig == "DENIED_CODING":
        if str(f.get("carc")) == "5" or "M77" in rarcs:
            return f"Fix the place of service on the claim of {code} (compare it with a claim of the same code this payer paid){tail_}"
        if "79" in mods:
            return (f"Check modifier 79 on {code}: a repair or closure of the same wound in the post-op period takes 58, an unrelated one keeps 79, "
                    f"a complication of the same surgery is in its global period{tail_}")
        return f"Correct the code or modifier combination on {code} ({_reason_words(fx)}){tail_}"
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
    return f"Correct the claim as the reason and remark codes say ({_reason_words(fx)}){tail_}"


def _payer_paid_like(conn: sqlite3.Connection, f: dict) -> dict | None:
    """The same payer paid this code on the patient's other claims of the same bill (another date of a treatment course,
    another fraction): {'n', 'dates', 'paid'}, the evidence that the denial is the payer's error. None when it did not."""
    b = S.bill_of(f.get("patient_control_number"))
    code, svc0 = f.get("procedure_code"), f.get("service_date")
    if not code or not svc0 or not S._table(conn, "claims") or not S._table(conn, "lines"):
        return None
    grp = f.get("payer_group")
    rows = []
    if b:
        rows += conn.execute(
            """SELECT c.patient_control_number, c.claim_status_code, COALESCE(l.service_date, c.statement_from), l.paid_cents, c.payer_name,
                      c.claim_filing_indicator, 1
               FROM claims c JOIN transactions t ON t.transaction_key = c.transaction_key JOIN lines l ON l.claim_key = c.claim_key
               WHERE c.patient_control_number >= ? AND c.patient_control_number < ? AND l.procedure_code = ? AND COALESCE(t.superseded, 0) = 0
                 AND COALESCE(l.paid_cents, 0) > 0""", (b, b + "~", code)).fetchall()
    if "patient_id" in S._cols(conn, "claims") and f.get("claim_key"):
        pid = conn.execute("SELECT patient_id FROM claims WHERE claim_key = ?", (f["claim_key"],)).fetchone()
        if pid and pid[0]:
            # the patient's other claims of a course (each fraction or visit its own claim), within 120 days either side
            rows += conn.execute(
                """SELECT c.patient_control_number, c.claim_status_code, COALESCE(l.service_date, c.statement_from), l.paid_cents, c.payer_name,
                          c.claim_filing_indicator, 0
                   FROM lines l JOIN claims c ON c.claim_key = l.claim_key JOIN transactions t ON t.transaction_key = c.transaction_key
                   WHERE l.procedure_code = ? AND c.patient_id = ? AND COALESCE(t.superseded, 0) = 0 AND COALESCE(l.paid_cents, 0) > 0
                     AND l.service_date BETWEEN ? AND ?""", (code, pid[0], S._add(svc0, -120), S._add(svc0, 120))).fetchall()
    hits = {}
    for pcn, st, svc, paid, payer, cfi, same_bill in rows:
        if (same_bill and S.bill_of(pcn) != b) or str(st) == "22" or svc == svc0:
            continue
        if grp and not S._same_payer(S.row_group({"payer": payer, "cfi": cfi})[0], grp):
            continue
        hits.setdefault(svc, paid)
    if not hits:
        return None
    dates = sorted(hits)
    return {"n": len(dates), "dates": dates[:4], "paid": hits[dates[0]]}


def _appeal_needs(conn: sqlite3.Connection, f: dict, fx: dict) -> tuple[list[str], str]:
    """What an appeal packet needs, and the argument, by signal, reason and code."""
    sig = f.get("signal") or ""
    code = f.get("procedure_code") or "the service"
    svc = day(f.get("service_date"))
    same = ", ".join(_same_day(None, f)) if _same_day(None, f) else ""
    carc = str(f.get("carc") or "")
    rarcs = {x.strip() for x in str(f.get("rarc") or "").split(",") if x.strip()}
    note = f"the visit note of {svc}"
    like = _payer_paid_like(conn, f) if sig in ("PROCEDURE_BUNDLED", "ADDON_BUNDLED", "DENIED_NONCOVERED", "DENIED_MEDICAL_NECESSITY") else None
    paid_like = (f" {f.get('payer_group') or 'The payer'} paid {code} for this patient on {like['n']} other "
                 f"{'date' if like['n'] == 1 else 'dates'} ({', '.join(day(d) for d in like['dates'])}" + (", ..." if like["n"] > 4 else "")
                 + ") with nothing different: ask it to pay this one the same way." if like else "")
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
        if code in RADIATION_CODES:
            return (["the treatment record of the course (each fraction and what was delivered)", "the physician's order for the course"],
                    f"{code} is a service separate from the radiation delivery it was bundled into, documented on its own in the treatment "
                    f"record.{paid_like}")
        return (["the operative or procedure note", "the NCCI edit table excerpt for the code pair (no edit, or modifier indicator 1)"],
                f"{code} is separately reportable: distinct lesion, site or session as documented; Mohs stages and the same surgeon's repairs are "
                f"separately reportable under CPT and NCCI.{paid_like}")
    if sig == "DENIED_MEDICAL_NECESSITY":
        reopen = " For Medicare, a wrong diagnosis code is a clerical error: ask for a reopening (within a year of the remittance) before a redetermination." \
            if f.get("payer_kind") == "medicare" else ""
        policy = "the coverage policy (the LCD)" if f.get("payer_kind") == "medicare" else "the payer's medical policy for the service"
        return ([note, "the pathology report", f"{policy} with the criteria met highlighted"],
                "The service meets the coverage criteria: the diagnosis on the claim and the record show the indication. Check the diagnosis "
                f"first: a wrong diagnosis code is fixed by a corrected claim (or a Medicare reopening), not an appeal.{reopen}{paid_like}")
    if sig == "DENIED_LEVEL_OF_SERVICE":
        return (["the procedure note", "the pathology report"], "The records document each stage or component of the procedure as billed.")
    if sig == "DENIED_FREQUENCY":
        return ([note, "the documentation of each unit, site or session"], "The units billed were rendered and documented.")
    if sig == "DENIED_AUTH":
        return (["the authorization number or the portal page showing it"], "The service was authorized: the authorization covers the date of service.")
    if sig == "DENIED_TIMELY_FILING":
        return (["the clearinghouse acceptance report (or 277CA) with the original submission date"], "The claim reached the payer in time.")
    if sig == "DENIED_PROVIDER":
        return (["the clinician's enrollment record with its effective date"], "The clinician was enrolled on the date of service.")
    return ([note], f"The service is payable as billed; the attached record supports it.{paid_like}")


def _timely_proof(conn: sqlite3.Connection, f: dict, fx: dict) -> dict:
    """For a timely-filing denial: when the claim first reached the payer and the first-filing limit (states.timely_proof)."""
    return S.timely_proof(f, fx)


def _route_for(conn: sqlite3.Connection, f: dict, fx: dict, st: dict) -> str:
    """The kind of work the item takes (states.route_of, the one place it is decided)."""
    return st.get("route") or S.route_of(fx, st, conn)


def _unposted(fx: dict) -> dict | None:
    """The item's own remittance that EMA never posted on the line (no EMA posting under its trace or check), when EMA
    holds the line: the 835 row. A payer's answer EMA never posted leaves EMA's A/R showing the claim as still pending."""
    if not fx["ema"]["lines"]:
        return None
    own = [x for x in S.own_rows(fx) if x.get("line_key") and x["status"] != "22" and not x.get("reversed_by")]
    if not own:
        return None
    x = max(own, key=lambda r: r["date"] or "")
    if str(x.get("method") or "").upper() == "NON" and (x["paid"] or 0) > 0:
        return None             # an informational notice of a payment that comes on another remittance
    traces = {p.get("trace_key") for p in fx["ema"]["pays"] if p.get("trace_key")}
    if x.get("tk") in traces or any(p.get("check") and x.get("trace") and S._same_check(p["check"], x["trace"]) for p in fx["ema"]["pays"]):
        return None
    as_of = max((ln.get("as_of") or "" for ln in fx["ema"]["lines"]), default="")
    if not as_of or (S._days(x["date"], as_of) or 0) < 14:
        return None             # EMA's report may not reach the posting yet
    return x


def _plb_unbooked(fx: dict) -> list[dict]:
    """Takebacks (PLB WO or 72) naming the item's claim that EMA shows no takeback for (states.plb_unbooked)."""
    return S.plb_unbooked(fx)


def _recovery_step(f: dict, rec: dict) -> str | None:
    """The payer's record for the item's pattern, as a step: ask to reprocess like the ones it paid, or stop and fix the cause."""
    if not rec or rec.get("rate") is None:
        return None
    grp = f.get("payer_group") or "the payer"
    short = f.get("category") in S.SHORT
    code = f.get("procedure_code") or "whole claims"
    what = (f"{code}, " + ("paid short" if f.get("category") == "underpayment" else "paid at a lower level")
            if short and not f.get("carc") else f"{code} with {f.get('carc') or f.get('signal')}")
    if rec["paid"] == 0 and rec["n"] >= 10 and short:
        return (f"{grp} has paid none of {rec['n']} earlier items like this ({what}): unless this one differs, it is unlikely to change "
                "the amount.")
    if rec["paid"] >= 2 and rec["rate"] >= 0.3:
        if f.get("payer_kind") == "medicare":
            return (f"{grp} later paid {rec['paid']} of {rec['n']} earlier items like this ({what}): the odds are good, so file it now, "
                    "and file the others still open the same way.")
        return (f"{grp} later paid {rec['paid']} of {rec['n']} earlier items like this ({what}): ask it to reprocess this one the same way, "
                "and list the others still open in the same request.")
    if rec["paid"] == 0 and rec["n"] >= 10:
        return (f"{grp} has paid none of {rec['n']} earlier items like this ({what}): unless this one differs, the money is unlikely to come; "
                "fix the cause at the front end, and the owner can decide the pattern in the policy table.")
    return None


CLOSE_RECOVERED = ("paid_later_ema", "paid_later_835", "rebilled_paid", "forwarded_paid", "second_payer_paid", "claim_paid_in_ema")
ACCEPT_REOPENS = ("person_let_go", "person_lost", "hidden_rule")


def _next(conn: sqlite3.Connection, fx: dict, story: dict, st: dict, verdict: dict, today: str | None) -> dict:
    f = fx["item"]
    t = S._today(today)
    o = fx.get("outcome") or {}
    sig = f.get("signal") or ""
    grp = f.get("payer_group") or "the payer"
    rule = st.get("rule") or ""
    route = _route_for(conn, f, fx, st)
    deadline, basis = st.get("deadline"), st.get("deadline_basis")
    proof = st.get("proof") or {}
    buttons: list[str] = []
    steps: list[str] = []
    needs: list[str] = []
    who = "Biller"
    action = ""
    expected, expected_basis = _expected(f, fx, story)
    clocks = _clocks(f, fx, st)
    url = _claim_url(conn, fx)
    appeal_block = st.get("appeal_block")
    bal = story.get("balance") or {}
    tot = S._ema_line_totals(fx)
    rec = S.recovery_of(conn, f) if f.get("category") in S.MONEY else None
    pattern = None
    if bal.get("credit") and not appeal_block:
        appeal_block = "credit"
    late = st.get("late_window") if rule == "open_late" else None
    # the binding deadline: the earliest clock that is still the item's own (a first denial of the same service counts)
    first = next((c for c in clocks if c["what"] == "first denial of this service"), None)
    if first and deadline and first["date"] < deadline and route in ("appeal", "call", "decide", "rate", "corrected") \
            and rule not in ("work_answered", "follow_up_due", "work_stale"):
        deadline, basis = first["date"], first["basis"]
    if route == "nothing":
        expected, expected_basis = 0, None
        state = st["work_state"]
        if state == "done":
            action = "Nothing to do: " + (proof.get("text") or st.get("state_basis") or "it is settled") + "."
            if f.get("status") == "open" and rule not in ("stale", "auto_closed", "person_paid"):
                if proof.get("rule") in CLOSE_RECOVERED and (proof.get("cents") or 0) > 0:
                    steps.append(f"Close it as Paid, recovered {usd(proof.get('cents'))} (the payment of {day(proof.get('date'))}): Log what "
                                 "happened > The payer paid.")
                else:
                    steps.append("Close it as Paid with $0.00 recovered: the money came before this item was listed (Log what happened > "
                                 "The payer paid, amount 0).")
                buttons.append("log_outcome")
            if proof.get("watch"):
                steps.append("Watch for a takeback: if the payer recoups the payment, dispute it with the remittance that paid it.")
            if proof.get("source") == "production" or proof.get("rule") in ("paid_later_ema", "rebilled_paid", "claim_paid_in_ema", "forwarded_paid"):
                era = _era_request(conn, fx, proof)
                if era:
                    steps.append(era)
                    buttons.append("request_era")
            if tot["n"] and tot["balance"] > 0 and rule != "stale":
                unj = [r for r in story.get("rows") or [] if "patient_unjoined" in r["tags"] and (r["amount_cents"] or 0) > 0]
                if unj:
                    steps.append(f"EMA still shows {usd(tot['balance'])} open on this line, but the patient paid {usd(sum(r['amount_cents'] for r in unj))} "
                                 f"for this date and code that EMA ties to no claim {_refs([r['n'] for r in unj])}: check those payments "
                                 "before billing the patient anything.")
                else:
                    steps.append(f"EMA still shows {usd(tot['balance'])} open on this line: if it is the patient's share (deductible, "
                                 "coinsurance or copay), put it on the patient's next statement; if it is the payer's, the line is not settled.")
                    buttons.append("mark_billed")
            if rule == "auto_closed" or f.get("auto_note"):
                buttons.append("reopen")
        elif state == "accepted":
            action = "Nothing to do: " + (st.get("state_basis") or "a loss someone accepted") + "."
            if rule in ACCEPT_REOPENS:
                buttons.append("reopen")
            elif rule == "policy":
                steps.append("To work these again, change the decision on the Write-off policy page.")
        else:
            closed = st.get("closed_on") or deadline
            cb = _short(st.get("closed_basis") or basis, 120)
            action = (f"Nothing can be recovered: the last window closed on {day(closed)}" + (f" ({cb})" if cb else "")
                      + ". It left the work list on its own and counts as lost to the deadline.")
            buttons.append("log_outcome")
        deadline, basis = None, None
    elif route == "wait":
        action, steps, who = _wait_step(f, fx, st)
        buttons += ["log_outcome"]
        deadline, basis = st.get("follow_up_on"), "the payer's usual answer time after the work was seen" if st.get("follow_up_on") else None
        if rule == "ema_follow_up":
            basis = "the follow-up date a person set in EMA"
        expected, expected_basis = (expected, expected_basis)
    elif route == "refund":
        action, steps, who, deadline, basis = _refund_step(f, fx, st, bal, deadline, basis)
        buttons += ["copy_instructions", "log_outcome"]
        expected, expected_basis = 0, "nothing to collect: money owed back"
    elif route == "post":
        action, steps, who, deadline, basis = _post_step(f, fx, st)
        buttons += ["mark_posted", "copy_instructions"]
        if rule == "credit_unposted_takeback":
            expected, expected_basis = 0, "nothing to collect: a takeback to post"
        else:
            expected, expected_basis = int(f.get("paid_cents") or f.get("shortfall_cents") or 0), "the 835's payment"
    elif route == "bill_patient":
        action, steps, who, owed, extra = _bill_patient_step(f, fx, st, tot)
        buttons += extra
        expected, expected_basis = owed, ("the patient's share on the 835" if owed else
                                          "a Medicaid member is not billed beyond the plan's copay")
        deadline, basis = None, "the next statement cycle"
    elif route == "bill_secondary":
        ev = S._ev(f)
        second = _second_name(fx)
        first_payer = ev.get("first_payer") or grp
        owed = int(f.get("shortfall_cents") or 0)
        share = ev.get("share") or "the patient's share"
        if ev.get("crossover"):
            action = (f"Ask {second} for its payment: {first_payer} paid on {day(f.get('payment_date'))} and forwarded the claim, and nothing from "
                      f"{second} is on file ({usd(owed)}, {share}).")
            steps.append(f"Check {_poss(second)} portal or call about the claim; if it has no record of it, bill it from EMA with {_poss(first_payer)} "
                         "remittance attached.")
        else:
            action = (f"Bill {second} for {usd(owed)} ({share}): {first_payer} paid on {day(f.get('payment_date'))} and left it, and {second} is "
                      "on file and was never billed.")
            steps.append(f"Send the claim to {second} from EMA with {_poss(first_payer)} remittance (its EOB) attached"
                         + (f", before {day(deadline)}" if deadline else "") + "; keep the acceptance report as proof of timely filing.")
        if ev.get("ema_wrote_off_share"):
            steps.append(f"EMA wrote the share off: reverse that write-off so the line shows {usd(owed)} open until {second} answers.")
        if f.get("payer_kind") == "medicaid" or ev.get("second_payer_kind") == "medicaid":
            steps.append("Medicaid pays last: never bill the patient for what Medicaid covers.")
        who = "Biller"
        buttons += ["copy_instructions", "mark_billed"]
        expected, expected_basis = owed, "the share the first payer left (its 835)"
    elif route == "contract":
        action = ("A contract or billing-policy question for the owner, not a claim to appeal: bring it to the payer's contract review with the "
                  "rates and volumes shown." if sig != "G2211_DENIED" else
                  "Decide whether to keep billing G2211 to this payer (from 2027 it becomes modifier B1).")
        who = "Owner"
        buttons += ["copy_instructions"]
        deadline, basis = None, "the agreement's renewal or notice date"
    elif route == "policy":
        action, steps, who, pattern = _policy_step(f)
        buttons += ["accept_pattern", "copy_instructions"]
        deadline, basis = None, "the owner's decision; the lines still inside their window can be worked once EMA posts them as denials"
        expected, expected_basis = int(S._ev(f).get("inside_window_cents") or 0), "the write-offs still inside their window (EMA's postings)"
    elif route == "fees":
        action = ("Ask the payer or its payment vendor for standard EFT with no fee, and for the fees withheld back." if sig == "FEE_SKIM" else
                  "Match the takeback to the payer's overpayment letter; if it was not owed, dispute it; either way post it in EMA as a "
                  "takeback on the claim its reference names, so EMA's balance is right.")
        who = "Billing lead"
        buttons += ["build_packet", "copy_instructions", "mark_sent"] if sig == "FEE_SKIM" else ["copy_instructions", "mark_posted", "mark_sent"]
    elif route == "call":
        action, steps, who = _call_step(conn, f, fx, st, t)
        buttons += ["copy_instructions", "log_outcome"]
        if rule == "person_billed_paid":
            buttons = ["log_outcome", "copy_instructions"]
        if rule in ("follow_up_due", "work_stale"):
            deadline, basis = (st.get("follow_up_on") if (st.get("follow_up_on") or "") >= t else t), "the follow-up is due"
        elif rule == "work_answered" and (st.get("answer_window") or {}).get("date"):
            aw = st["answer_window"]
            deadline, basis = aw["date"], aw["basis"]
    elif route == "eob_first":
        action = (f"Get the EOB from {_poss(grp)} portal (no 835 reached RemitWatch for this answer), then work the denial: "
                  + ("send a corrected claim." if f.get("actionable") == "correct_and_resubmit" else "appeal with the record."))
        steps += ["Download the EOB for the check that carried the denial (EMA shows the posting day).",
                  "Read its reason and remark codes; fix and resend, or appeal, as they say."]
        zero = next((p for p in sorted(fx["ema"]["pays"], key=lambda p: p["posted"] or "") if not p.get("in_835") and p.get("check_tail")), None)
        era = _era_request(conn, fx, {"check_tail": zero["check_tail"], "payer": zero.get("payer"), "rule": "eob_first"}) if zero else None
        buttons += ["copy_instructions", "mark_sent"]
        if era:
            steps.append(era)
            buttons.append("request_era")
    elif route == "cob":
        action, steps, who, expected = _cob_step(f, fx, story, expected)
        expected_basis = "the 835's allowed amount for the visit"
        buttons += ["copy_instructions", "log_outcome", "mark_billed"]
    elif route == "corrected":
        action = _corrected_change(f, fx) + "."
        medicare_fix = f.get("payer_kind") == "medicare" and sig not in ("REJECTED_PRE_PAYER", "NOT_SENT")
        if medicare_fix:
            from . import appeals
            nxt = appeals.next_step(dict(f, evidence=S._ev(f)))
            if nxt:
                action = nxt
        if late and late.get("process") == "corrected":
            action = f"{_late_words(st)} " + action
        who = "Coder, then the biller" if sig in ("DENIED_MISSING_INFO", "DENIED_CODING", "DENIED_MISSING_MODIFIER") else "Biller"
        buttons += ["copy_instructions", "mark_sent"]
        if medicare_fix:
            from . import appeals
            mroute = appeals._medicare_route(dict(f, evidence=S._ev(f)))
            if mroute == "new_claim":
                steps.append("Send it from EMA through the clearinghouse as a new claim and keep the acceptance report (proof of timely filing).")
            elif mroute == "reopening":
                steps.append(f"Ask for the reopening on {_mac_portal(f)} or by phone; write its reference number in Mark sent.")
            else:
                steps.append(f"File the redetermination on {_mac_portal(f)} or by mail, with the records the remark asks for; "
                             "Medicare takes no faxed redeterminations.")
        else:
            steps.append("Send it from EMA through the clearinghouse and keep the acceptance report (proof of timely filing).")
    elif route == "rate":
        chk = _short_check(conn, fx)
        action = (f"Ask {grp} to reprocess {f.get('procedure_code')} at its usual rate ({usd(f.get('expected_cents'))}); one request can list "
                  "every claim for the code.")
        if chk and chk["kind"] == "top_cut":
            steps.append(f"The argument: {chk['text']}.")
        else:
            steps.append("Check first that no multiple-procedure reduction (a higher-valued procedure the same day) or facility rate (a "
                         "surgery-center day) explains it.")
        who = "Biller"
        buttons += ["build_packet", "copy_instructions", "mark_sent"]
    elif route == "decide":
        action, steps, who, extra = _decide_step(conn, f, fx, st)
        buttons += extra
    else:
        needs, argument = _appeal_needs(conn, f, fx)
        cut = S.sentence_end(argument, semicolon=False)
        first_sentence = [argument[:cut], argument[cut + 2:]] if cut is not None else [argument]
        action = f"Appeal to {grp}: {first_sentence[0].rstrip('.')}."
        if f.get("payer_kind") == "medicare":
            action += " Medicare contractors take no faxed redeterminations: file it on the contractor's portal or by mail."
        if late and late.get("process") == "appeal":
            action = f"{_late_words(st)} Dispute it with {grp}: {first_sentence[0].rstrip('.')}."
        steps += [f"Build the appeal packet; it needs {', '.join(needs)}."]
        if len(first_sentence) > 1:
            steps.append("The argument, in full: " + argument)
        steps += [_appeal_how(f), "Read the letter, fill anything in brackets, then send it."]
        who = "Coder reads the note, then the biller sends it" if any("note" in n for n in needs) else "Biller"
        buttons += ["build_packet"]
        buttons += ["attach_note"] if _note_on_file(conn, f) else (["find_note"] if _modmed_configured() else [])
        buttons += ["upload_note"]
        ok, why = _fax_number(conn, f)
        if ok:
            buttons.append("fax_payer")
            steps.append(why)
        else:
            steps.append(_appeal_channel(f) + (f" {why}" if why and "Faxes page" in why else ""))
        buttons += ["copy_instructions", "mark_sent"]
    working = route in ("appeal", "corrected", "rate", "decide", "call", "cob") and st["work_state"] == "work_now"
    if working:
        # what else the item's line needs, whatever the route
        un = _unposted(fx)
        if un:
            steps.insert(0, f"First post {_poss(grp)} remittance of {day(un['date'])} in EMA (check ending {un.get('check_tail') or '?'}; paid "
                            f"{usd(un['paid'])}; {', '.join(f'{g}-{c} {usd(a)}' for g, c, a in un['adj']) or 'no reasons'}): EMA has not posted "
                            "it, so EMA's A/R still shows the claim as pending.")
            buttons.append("mark_posted")
        if o.get("outcome") in WRITE_OFFS and route in ("appeal", "corrected", "call", "decide") and f.get("category") in ("denial", "bundling") \
                and (o.get("written_off") or o.get("contractual_routine")):
            # a decision may end in a request to the payer (an authorization found, a reprocess): the same reversal, said as a condition
            steps.append(f"{'If' if route == 'decide' else 'When'} you send it, reverse EMA's write-off of "
                         f"{usd(o.get('written_off') or o.get('contractual_routine'))} ({', '.join(o.get('carcs') or []) or 'the denial'}) "
                         "so the line shows open until the payer answers.")
        reopen = _clerical_remark_step(f, clocks, st) if not late else None
        if reopen:
            steps.append(reopen)
        rs = _recovery_step(f, rec) if route in ("appeal", "corrected", "rate", "decide") and rule not in S.CHECK_RULES else None
        if rs and not (rec.get("paid") == 0 and route == "appeal" and _payer_paid_like(conn, f)):
            steps.append(rs)
        if deadline and rule not in ("follow_up_due", "work_stale") and not late:
            left = S._days(t, deadline)
            legal = st.get("legal_deadline")
            if left is not None and left < 0 and legal and legal >= t:
                lleft = S._days(t, legal) or 0
                go = "send it now" if route in ("appeal", "rate", "corrected") else "act now"
                when = "today is the last day" if lleft <= 0 else f"{lleft} day{'s' if lleft != 1 else ''} left"
                action = f"The safe date passed on {day(deadline)}, but {_poss(grp)} own limit is {day(legal)} ({when}): {go}. " + action
            elif left == 0:
                action = f"Today is the last day ({day(deadline)}): do it today. " + action
            elif left is not None and 0 < left <= 7:
                action = f"Only {left} day{'s' if left != 1 else ''} left (by {day(deadline)}): do it today. " + action
        if late:
            deadline, basis = late["date"], late["basis"]
    for p in _plb_unbooked(fx):
        if rule == "credit_unposted_takeback":
            break
        steps.append(f"{p['payer']} took back {usd(abs(p['cents']))} on {day(p['date'])} (provider-level adjustment {p['reason']} naming this "
                     "claim) and EMA shows no takeback on this line: post the takeback in EMA on this claim so its balance is right.")
        buttons.append("mark_posted")
    sib = _siblings(conn, f)
    if sib and st["work_state"] in ("work_now", "waiting"):
        steps.append("Also open on this visit: " + _sibling_words(sib) + ". Work them together.")
    if url:
        buttons.append("open_in_ema")
    # write-off decisions only through the owner's policy table or a person's Let go with a reason
    if (st["work_state"] == "work_now" and o.get("pattern") and o.get("outcome") in WRITE_OFFS and f.get("category") in ("denial", "bundling")
            and route in ("appeal", "corrected", "decide", "call")):
        buttons.append("accept_pattern")
        pattern = o["pattern"]
        steps.append(f"If the owner accepts these write-offs as policy, Accept this pattern ({_pattern_words(o['pattern'])}); otherwise work it. "
                     "A single item is let go only by a person, with a reason.")
    if st["work_state"] == "work_now" and "let_go" not in buttons and rule not in S.CHECK_RULES and route not in ("post", "policy"):
        buttons.append("let_go")
    last_day = max(d for d in (deadline, st.get("legal_deadline"), (late or {}).get("date")) if d) if (
        deadline or st.get("legal_deadline") or late) else None
    appeal_ok = route in ("appeal", "rate") and st["work_state"] == "work_now" and not appeal_block and not (last_day and last_day < t)
    instructions = _instructions(f, fx, action, steps, needs, deadline, basis)
    btn = []
    for b in buttons:
        if b in BUTTONS and b not in btn:
            btn.append(b)
    if rule in S.CHECK_RULES:
        btn = [b for b in btn if b not in ("build_packet", "fax_payer")]
    stored_ev = f.get("expected_value_cents")
    ranked = (min(int(stored_ev), expected) if stored_ev is not None and expected else
              (int(expected * rec["rate"]) if rec and rec.get("rate") is not None and expected else None))
    out = {"action": _clean(action), "who": who, "deadline": deadline, "basis": basis, "expected_cents": expected,
           "expected_basis": expected_basis, "buttons": btn, "instructions": instructions, "route": route,
           "steps": [_clean(s) for s in steps if s], "needs": needs, "clocks": clocks, "appeal_ok": appeal_ok,
           "appeal_block": appeal_block if not appeal_ok else None, "claim_url": url, "recovery": rec, "ranked_cents": ranked,
           "siblings": sib, "pattern": pattern}
    if f.get("recovery_basis") and rec is not None:
        out["recovery"] = dict(rec, basis=f["recovery_basis"])
    return out


def _pattern_words(pattern: str) -> str:
    return ", ".join(p for p in str(pattern or "").split("|") if p) or "this pattern"


def _appeal_how(f: dict) -> str:
    from . import appeals
    kind = f.get("payer_kind")
    if kind == "medicare":
        return ("This is a Medicare redetermination, within 120 days of the remittance; Medicare contractors take no faxed "
                "redeterminations.")
    return appeals._process(dict(f, evidence=S._ev(f)))


def _facts_line(f: dict, fx: dict) -> str:
    ev = S._ev(f)
    codes = ", ".join(sorted({c for _s, c in S._service_keys(f)})) or (f.get("procedure_code") or "")
    billed = ev.get("billed_cents") or f.get("charge_cents")
    pccn = _payer_claim_number(fx)
    facts = [f"date of service {day(f.get('service_date'))}", f"codes {codes}", f"billed {usd(billed)}"]
    if pccn:
        facts.append(f"the payer's claim number {pccn}")
    if f.get("carc"):
        facts.append(f"the denial codes {_reason_words(fx)}")
    return "Have ready: " + ", ".join(facts) + ", the member ID and the rendering NPI."


def _wait_step(f: dict, fx: dict, st: dict) -> tuple[str, list[str], str]:
    """What waiting means, by what the item waits on (M10): a carrier's payment, a payer's new answer after a reversal, a
    person's follow-up date in EMA, the answer to work sent; never 'work already done' where none was."""
    grp = f.get("payer_group") or "the payer"
    rule = st.get("rule") or ""
    follow = st.get("follow_up_on")
    work = st.get("work") or {}
    wkind = S.WORK_WORDS.get(work.get("kind") or "", "the work")
    verb = S.WORK_WHEN.get(work.get("kind") or "", "sent")
    when = day(work.get("date")) if work.get("date") and work.get("dated_by") != "first_report" else "on a date nobody recorded"
    who = "A/R follow-up biller"
    if not follow:
        return (f"Check {_poss(grp)} portal now: nothing says when its answer is due.", [_facts_line(f, fx)], who)
    if rule == "forwarded":
        fwd = S._forwarded(fx) or {}
        carrier = fwd.get("carrier") or "the carrier"
        return (f"Wait for {_poss(carrier)} payment: {grp} forwarded the claim to it on {day(fwd.get('date'))}. If nothing comes by {day(follow)}, "
                f"ask {carrier} whether it received the claim and when it will pay.",
                [f"On {day(follow)}, check {_poss(carrier)} portal (or call it) for the claim, with {_poss(grp)} remittance at hand.",
                 f"Check that TriZetto is enrolled for {_poss(carrier)} ERAs, so its answer reaches RemitWatch."], who)
    if rule == "archive_waiting":
        rev = max((r.get("date") or "" for r in (S._archive_hint(f) or {}).get("proof") or [] if r.get("date")), default="") or f.get("payment_date")
        return (f"Wait for {_poss(grp)} new answer: it reversed its answer on {day(rev)} and has not answered again. If nothing comes by "
                f"{day(follow)}, ask it when the claim will be reprocessed.", [_facts_line(f, fx)], who)
    if rule == "ema_follow_up":
        return (f"Wait: a person set an EMA follow-up date of {day(follow)} on this claim. Read the claim's notes in EMA for what was done; on that "
                "day, follow up as they say.", [], who)
    if f.get("signal") == "SECONDARY_NOT_BILLED" and work.get("kind") in ("billed", "sent", "secondary"):
        second = _second_name(fx)
        return (f"Wait for {_poss(second)} payment of the claim billed on {when}; follow up with {second} on {day(follow)} if nothing comes.",
                [f"On {day(follow)}, ask {second} whether it received the claim and when it will pay the "
                 f"{S._ev(f).get('share') or 'share'} ({usd(f.get('shortfall_cents'))})."], who)
    steps = [f"On {day(follow)}, check {_poss(grp)} portal or call about {wkind} ({verb} {when}), quoting what was done and when."]
    if st.get("deadline"):
        steps.append(f"If the payer has no record of it then, send it again in writing before {day(st['deadline'])}.")
    return (f"Wait for {_poss(grp)} answer to {wkind} ({verb} {when}); follow up on {day(follow)} if nothing comes.", steps, who)


def _refund_step(f: dict, fx: dict, st: dict, bal: dict, deadline: str | None, basis: str | None):
    """A credit to refund or let the payer recoup (L24): who paid, by the line's own payments; the deadline only where
    detection dated the refund (Medicare's 60 days); a possible overpayment the archive found (H2)."""
    grp = f.get("payer_group") or "the payer"
    rule = st.get("rule") or ""
    pays = [p for p in fx["ema"]["pays"] if (p["cents"] or 0) > 0]
    payers = sorted({p["payer"] for p in pays if p["payer"]})
    allowed = max((x.get("allowed") or 0 for x in S.standing_rows(fx["r835"]["rows"]) if x.get("line_key")), default=0)
    listing = "; ".join(f"{usd(p['cents'])} from {p['payer'] or 'insurance'} posted {day(p['posted'])}" for p in pays[:6])
    who_paid = (f"Who paid: {listing}" + (f", against an allowed amount of {usd(allowed)}" if allowed else "") + "." if listing else
                "Who paid: open the claim's payments in EMA (none is tied to this line here).")
    if rule == "possible_overpayment":
        hint = S._archive_hint(f) or {}
        over = st.get("credit_cents") or int(hint.get("possible_overpayment_cents") or 0)
        action = (f"Paid twice: {S._hint_text(hint) or st.get('state_basis')}. Do not ask for more. Confirm with {grp} which payment stands, then "
                  f"refund the extra {usd(over)} or let it recoup it.")
        steps = [who_paid, f"Call {grp} (or use its portal) to confirm which payment stands and whether it will recoup the extra or wants a refund check.",
                 "Post the refund or the recoupment in EMA so the line shows what the payer finally paid."]
    else:
        credit = st.get("credit_cents") or (-bal.get("open_cents", 0) if bal.get("open_cents", 0) < 0 else 0)
        action = (f"Overpaid by {usd(credit)}: decide whether to refund it or let {', '.join(payers) or grp} recoup it. Do not appeal or ask for "
                  "more on this line.")
        steps = [who_paid, "Call the payer (or use its portal) to confirm which plan is primary and whether it will recoup or wants a refund check.",
                 "Sign the refund (or record the recoupment) and post it in EMA so the credit clears."]
        tb = st.get("takeback")
        if tb and tb.get("cents"):
            steps.insert(0, f"First post {S._takeback_words(tb)} in EMA: that part of EMA's credit is a takeback EMA never posted, not money "
                            "owed back.")
    if f.get("signal") == "CREDIT_BALANCE" and f.get("appeal_deadline"):
        # only the refund date detection set (Medicare's 60 days from finding it), never an appeal window
        deadline, basis = f["appeal_deadline"], f.get("deadline_basis") or basis
    else:
        deadline = None
        basis = "Commercial plans usually ask for an identified overpayment back within 30 days; Medicare within 60 days of finding it"
    return action, steps, "Billing lead; owner decides", deadline, basis


def _post_step(f: dict, fx: dict, st: dict):
    """A payment or a takeback the 835 carries that EMA never posted."""
    grp = f.get("payer_group") or "the payer"
    if st.get("rule") == "credit_unposted_takeback":
        tb = st.get("takeback") or S.unposted_takebacks(fx)
        credit = -min(0, S._ema_line_totals(fx)["balance"]) or int(S._ev(f).get("credit_cents") or 0)
        action = (f"Post {S._takeback_words(tb)} in EMA on this claim: EMA shows a credit of {usd(credit)} only because it was never posted. "
                  "Nothing is owed back: do not refund it.")
        steps = ["Post the reversal as the 835 shows it: the payer's negative payment and the reversed adjustments, under that check.",
                 "Then post the payer's new answer on the same check if it has one, so the line shows what the payer finally paid.",
                 "Check the line afterwards: EMA's balance should match the 835 (the story's balance line)."]
        return action, steps, "Payment poster", None, "Post it now: EMA's credit is not money owed back"
    ev = S._ev(f)
    paid = f.get("paid_cents") or f.get("shortfall_cents")
    trace = ev.get("trace_tail") or next((x.get("check_tail") for x in S.own_rows(fx) if x.get("check_tail")), None)
    action = (f"Post the payment in EMA: {grp} paid {usd(paid)} on {day(f.get('payment_date'))}"
              + (f" (trace ending {trace})" if trace else "") + ", and EMA has not posted it.")
    steps = ["Find the deposit on the bank statement (or the payer's portal) for that date and amount.",
             "Post each claim line of the remittance in EMA under that trace: payment, contractual adjustment and patient share as the 835 shows.",
             "If EMA posted it under another number, correct the number; if it never arrived, ask the payer to reissue it."]
    return action, steps, "Payment poster", None, "Post it now: the deposit is in the bank and nobody else will find it"


def _bill_patient_step(f: dict, fx: dict, st: dict, tot: dict):
    """The patient's share after the payer reprocessed the line (H2): the share on the 835, never EMA's open balance when
    that still holds the contractual reduction (post the payer's answer first); a Medicaid member is never billed beyond
    the plan's copay."""
    grp = f.get("payer_group") or "the payer"
    proof = st.get("proof") or {}
    pr = int(proof.get("patient_cents") or 0)
    ema_open = tot["balance"] if tot["n"] else None
    row = next((x for x in fx["r835"]["rows"] if x["date"] == proof.get("date") and x["status"] != "22" and x.get("line_key")), None)
    co = sum(a for g, c, a in (row or {}).get("adj", []) if g == "CO")
    what = S._pr_words((row or {}).get("adj", [])) if row else "share"
    post = (f"Post {_poss(grp)} reprocessed answer of {day(proof.get('date'))} in EMA: contractual {usd(co)} and the patient's {what} {usd(pr)}, "
            f"so EMA shows {usd(pr)} open.")
    if f.get("payer_kind") == "medicaid":
        return (f"Do not bill the patient beyond the copay the state plan allows: post {_poss(grp)} answer of {day(proof.get('date'))} in EMA "
                "and write off the rest as Medicaid requires.", [post], "Patient accounts", 0, ["copy_instructions", "mark_posted"])
    owed = min(pr, ema_open) if ema_open is not None and 0 < ema_open < pr else pr
    steps, extra = [], ["mark_billed", "copy_instructions"]
    if ema_open is not None and ema_open > pr:
        steps.append(post)
        extra = ["mark_posted"] + extra
    steps.append(f"Put {usd(owed)} on the patient's next statement; the payer owes nothing more on this line.")
    return f"Bill the patient {usd(owed)}: {proof.get('text') or st.get('state_basis')}.", steps, "Patient accounts", owed, extra


def _policy_step(f: dict):
    """EMA books a payer's denials as contractual write-offs (C7), so they never reach a denial queue: the owner accepts the
    pattern as policy, or has EMA post the reason as non-contractual (M11). The pattern is the owner's policy key."""
    ev = S._ev(f)
    grp = f.get("payer_group") or "the payer"
    reason = str(ev.get("reason") or f.get("carc") or "")
    lines = ev.get("lines") if isinstance(ev.get("lines"), int) else None
    total = ev.get("written_off_cents")
    inside = ev.get("inside_window_cents")
    year = ev.get("year")
    bits = []
    if lines:
        bits.append(f"{lines} lines")
    if total:
        bits.append(f"{usd(total)}" + (f" in {year}" if year else ""))
    action = (f"EMA writes {_poss(grp)} {reason} denials off as contractual" + (f" ({', '.join(bits)})" if bits else "")
              + ", so they never reach a denial queue" + (f"; {usd(inside)} is still inside its window" if inside else "")
              + ". Owner: accept them as policy, or have EMA post this reason as non-contractual so those lines are worked.")
    steps = []
    if ev.get("next_step"):
        steps.append(str(ev["next_step"]))
    codes = [c for c, _v in ev.get("top_codes") or []]
    if codes:
        steps.append("The codes it touches most: " + ", ".join(codes[:6]) + ".")
    return action, steps, "Owner, with the billing lead", f"{grp}|||{reason}"


def _call_step(conn: sqlite3.Connection, f: dict, fx: dict, st: dict, today: str) -> tuple[str, list[str], str]:
    grp = f.get("payer_group") or "the payer"
    ev = S._ev(f)
    codes = ", ".join(sorted({c for _s, c in S._service_keys(f)})) or (f.get("procedure_code") or "")
    billed = ev.get("billed_cents") or f.get("charge_cents")
    facts = _facts_line(f, fx)
    rule = st.get("rule") or ""
    a = st.get("answered") or {}
    work = st.get("work") or {}
    wkind = S.WORK_WORDS.get(work.get("kind") or "", "the work")
    verb = S.WORK_WHEN.get(work.get("kind") or "", "sent")
    when = day(work.get("date")) if work.get("dated_by") not in ("first_report",) and work.get("date") else "on a date nobody recorded"
    fallback = _work_fallback(conn, f, fx, st)
    if st.get("work_state") == "cant_check":
        return _cant_check_step(f, fx, st, facts)
    if rule == "payment_never_arrived":
        paid = f.get("paid_cents") or f.get("shortfall_cents")
        return (f"Ask {grp} for the {usd(paid)} its informational remittance of {day(f.get('payment_date'))} reported paid: was the payment "
                "issued, on what date, for how much, under what EFT trace or check number?",
                [facts, "Issued: find it on the bank statement and post it in EMA under that trace. Not issued: ask them to send it.",
                 "Post nothing until the money arrives."], "A/R follow-up biller")
    if rule == "person_billed_paid":
        p = st.get("proof") or {}
        return (f"Mark it Paid: {p.get('text') or 'the second payer paid'}. Log what happened > The payer paid, with that amount.", [facts],
                "Biller")
    second = _second_name(fx) if f.get("signal") == "SECONDARY_NOT_BILLED" else None
    if second and rule in ("follow_up_due", "work_answered") and work.get("kind") in ("billed", "sent", "secondary"):
        share = S._ev(f).get("share") or "share"
        if rule == "work_answered" and a:
            return (f"{second} answered on {day(a.get('date'))} ({', '.join(a.get('codes') or []) or 'no payment'}): open its answer and work it.",
                    [f"Ask {second} why, if the answer is a denial; send what it asks for.", facts], "A/R follow-up biller")
        return (f"Ask {second} whether it received the claim billed on {when} and when it will pay the {share} ({usd(f.get('shortfall_cents'))}).",
                [facts, f"Not on file: send it again to {second} now, with the first payer's remittance attached."], "A/R follow-up biller")
    if rule == "work_answered" and a:
        if (a.get("paid") or 0) > 0:
            steps = [f"Open the payer's answer of {day(a.get('date'))}: it paid {usd(a['paid'])}.",
                     f"If that covers what this item lists ({usd(f.get('shortfall_cents'))}), mark it Paid with that amount; if not, ask the "
                     "payer for the rest (a reconsideration of the new answer).", facts]
            return (f"Read {_poss(grp)} answer of {day(a.get('date'))} to {wkind}: it paid {usd(a['paid'])}. Close the item if that settles it.",
                    steps, "Biller")
        codes_ = ", ".join(a.get("codes") or []) or "no payment"
        if a.get("inferred"):
            steps = [f"Check {_poss(grp)} portal for the decision on {wkind} (the claim was answered again on {day(a.get('date'))} with the same "
                     f"reason, {codes_}).", facts]
            if fallback:
                steps.append("If the payer upheld its denial: " + fallback)
            return (f"{grp} most likely upheld its denial: it answered the same claim again on {day(a.get('date'))} with the same reason "
                    f"({codes_}) after {wkind}. Confirm it, then take it to the next level.", steps, "A/R follow-up biller")
        steps = [f"Open the payer's answer of {day(a.get('date'))} ({codes_}).",
                 "Upheld after an appeal: take it to the next level (second-level appeal or reconsideration) with the same record. "
                 "Denied for something the resubmission still lacks: fix that and send it again.", facts]
        if fallback:
            steps.append("The case for it: " + fallback)
        return (f"{grp} answered {wkind} on {day(a.get('date'))} without paying ({codes_}): work that answer.", steps, "A/R follow-up biller")
    if rule in ("work_stale", "follow_up_due"):
        if work.get("kind") in ("follow_up", "note"):
            # the state's basis ends with the same instruction: said once
            said = re.sub(r"[:;] read the claim's notes in EMA, then follow up\.?$", "", (st.get("state_basis") or "").strip())
            return (f"Read the claim's notes in EMA, then follow up with {grp}: {_lower(said)}.",
                    [facts, "Paid or reprocessed: post it and close the item. Not on file: send it again now."], "A/R follow-up biller")
        follow = st.get("follow_up_on")
        steps = [f"Ask {grp} whether it received {wkind} ({verb} {when}) and what it decided.", facts,
                 "Paid or reprocessed: post it and close the item. Not on file: send it again now."]
        if fallback:
            steps.append("Denied again, or nothing on file: " + fallback)
        return (f"Follow up with {grp} on {wkind} ({verb} {when})" + (f": no answer by {day(follow)}." if follow else "."), steps,
                "A/R follow-up biller")
    if rule == "forwarded_late":
        fwd = S._forwarded(fx) or {}
        carrier = fwd.get("carrier") or "the carrier"
        steps = [f"Ask {carrier} whether it received the claim and when it will pay.", facts,
                 f"Check that TriZetto is enrolled for {_poss(carrier)} ERAs, so its answer reaches RemitWatch."]
        return (f"Ask {carrier} for its payment: {grp} forwarded the claim to it on {day(fwd.get('date'))}, and nothing has come by "
                f"{day(st.get('follow_up_on'))}.", steps, "A/R follow-up biller")
    if rule in ("stored_person",):
        return (f"Follow up with {grp} on the work a person marked: {_lower(st.get('state_basis') or '')}.",
                [facts, "Paid or reprocessed: post it and close the item. Denied again: work the new answer."], "A/R follow-up biller")
    if f.get("signal") == "DENIED_DUPLICATE_UNPAID":
        steps = [f"Ask {grp}: which claim and check paid this service the first time?", facts,
                 "If none was ever paid, ask the payer to reprocess this line."]
        return f"Call {grp} (or use its portal) to find the first payment this denial points to.", steps, "A/R follow-up biller"
    filing = st.get("deadline")
    steps = ["First check the clearinghouse (TriZetto) for the claim: was it accepted (999) and acknowledged by the payer (277CA), or "
             "rejected? A rejection there means it never reached the payer.",
             f"Ask {grp}: \"Do you have a claim for {codes}, billed {usd(billed)}, date of service {day(f.get('service_date'))}? "
             "When was it received, and what is its status?\"", facts,
             "Not on file: send it again now as a new claim and keep the clearinghouse acceptance report as proof of timely filing.",
             "Pending: ask what is missing and send it. Denied or paid: get the EOB and post or work it here."]
    return (f"Look the claim up on {_poss(grp)} portal or call: is it on file?" + (f" If not, it must reach the payer by {day(filing)}." if filing else ""),
            steps, "A/R follow-up biller")


def _cant_check_step(f: dict, fx: dict, st: dict, facts: str) -> tuple[str, list[str], str]:
    """What to do when no source RemitWatch reads can show the money: by why (M22: a surgery-center claim is never resent)."""
    grp = f.get("payer_group") or "the payer"
    rule = st.get("rule") or ""
    if rule == "cant_check_facility":
        return (f"Get the facility claim's EOB: the payer pays the surgery center's claims to the surgery center's own payee, so its remittances do "
                f"not reach RemitWatch. Look the claim up on {_poss(grp)} portal under the surgery center's account (or ask the surgery center's "
                "billing office), then post it in EMA and work the denial from it.", [facts], "A/R follow-up biller")
    if rule == "cant_check_carrier":
        fwd = S._forwarded(fx) or {}
        carrier = fwd.get("carrier") or "the carrier"
        return (f"Check whether {carrier} paid: {grp} forwarded the claim to it, and no source RemitWatch reads shows its money. Look it up on "
                f"{_poss(carrier)} portal (or in EMA's payments for the claim); if it never received the claim, send it there with {_poss(grp)} "
                "remittance.", [facts, f"Ask that TriZetto enroll for {_poss(carrier)} ERAs, so its answers reach RemitWatch."], "A/R follow-up biller")
    filing = st.get("deadline")
    return (f"Look the claim up on {_poss(grp)} portal or call: no source RemitWatch reads shows its answer."
            + (f" If it is not on file, it must reach the payer by {day(filing)}." if filing else ""),
            ["First check the clearinghouse (TriZetto): was it accepted (999) and acknowledged by the payer (277CA)?", facts,
             "Not on file: send it again now as a new claim and keep the acceptance report. Paid or denied: get the EOB and post or work it."],
            "A/R follow-up biller")


def _work_fallback(conn: sqlite3.Connection, f: dict, fx: dict, st: dict) -> str | None:
    """What to do about the item itself when the work already done failed: its own route's action, as if nobody had worked it.
    When the payer's newest answer gives another reason (a referral now missing, say), that reason's step."""
    a = st.get("answered") or {}
    new = [c.split("-", 1) for c in a.get("codes") or [] if "-" in c]
    if new and not any(c == str(f.get("carc") or "") for _g, c in new):
        g, c = new[0]
        f = dict(f, carc=c, rarc=None, carc_group=g)
        if c in AUTH_CARCS:
            act, steps, _who, _b = _decide_step(conn, f, fx, dict(st, rule="open"))
            return act + " " + " ".join(steps)
    base = dict(st, work_state="work_now", rule="open", answered=None, route=None, late_window=None)
    route = S.route_of(dict(fx, item=f), base, conn)
    if route == "decide":
        return _decide_step(conn, f, fx, base)[0]
    if route == "corrected":
        return _corrected_change(f, fx) + "."
    if route == "appeal":
        needs, argument = _appeal_needs(conn, f, fx)
        return f"appeal: {argument} It needs {', '.join(needs)}."
    if route == "rate":
        return f"ask {f.get('payer_group') or 'the payer'} to reprocess {f.get('procedure_code')} at its usual rate ({usd(f.get('expected_cents'))})."
    return None


def _cob_step(f: dict, fx: dict, story: dict, expected: int) -> tuple[str, list[str], str, int]:
    """A coordination-of-benefits task: find the plan that pays first, bill it, then send this payer the second claim."""
    grp = f.get("payer_group") or "the payer"
    names = [S.row_group(x)[0] for x in fx["r835"]["rows"]]
    for ln in fx["ema"]["lines"]:
        for k in ("primary_payer", "secondary_payer"):
            if ln.get(k):
                names.append(S._grp_kind(str(ln[k]))[0])
    others = sorted({n for n in names if n and n != "Unknown" and not S._same_payer(n, grp)})
    earlier = [x for x in S.own_rows(fx) if x.get("allowed")] or [x for x in fx["r835"]["rows"] if x.get("allowed") and x["status"] != "22"]
    allowed = max((x["allowed"] or 0 for x in earlier), default=0)
    steps = [f"Check the patient's other coverage for the date of service (Availity or the payers' portals), and what other coverage {grp} "
             "has on file and from when." + (f" Payers seen for this patient on this bill: {', '.join(others)}." if others else ""),
             "Another plan was active: bill it as primary with this remittance attached; if its filing limit has passed, ask for a "
             "timely-filing exception (the claim was filed on time with this payer). When it pays, send this payer the second claim with "
             "its EOB, and refund the patient anything above the first plan's patient share.",
             f"No other coverage: the front desk asks the patient to update their coordination of benefits with {grp}; then ask {grp} to "
             "reprocess the claim.",
             "In EMA, post this remittance (the reversal and the denial) so the line shows open until the plan that pays first is billed."]
    if allowed:
        expected = allowed      # what the plans allowed for the visit, not the usual rate (C13)
    return (f"Coordination of benefits: find the plan that pays first and bill it; {grp} says it is not the primary payer.", steps,
            "Biller; front desk for the patient's update", expected)


def _check_first_step(f: dict, fx: dict, st: dict) -> tuple[str, list[str], str]:
    """'Check first': the one thing to verify and where, then Close as paid or Appeal (orchestrator decision 4)."""
    grp = f.get("payer_group") or "the payer"
    hint = S._archive_hint(f) or {}
    if hint.get("reclass") == "secondary_paid":
        prior = hint.get("prior_payer_cents")
        return ("Check first: look up the primary payer's payment of this service" + (f" ({usd(prior)} settled, per the secondary's remittance)"
                if prior else "") + " on its portal or in EMA before appealing; the secondary's share is not an underpayment.",
                [S._hint_text(hint) + "."] if S._hint_text(hint) else [], "Biller")
    pay = next((r for r in hint.get("proof") or [] if r.get("kind") in ("payment", "restatement")), None) or {}
    who = _grp_of(pay.get("payer")) or grp
    what = (f"{_poss(who)} payment of {day(pay.get('date'))}" + (f" ({usd(pay.get('amount_cents'))})" if pay.get("amount_cents") else "")
            if pay else f"{_poss(grp)} earlier payment")
    return (f"Check first: look up {what} in EMA (the claim's payments) or on {_poss(who)} portal. If it pays this service, close it as Paid "
            "with $0.00 recovered (the money came before this denial); if not, appeal it.",
            [S._hint_text(hint) + "." if S._hint_text(hint) else st.get("state_basis") or "",
             "RemitWatch did not close it because the payment is not on file to prove it."], "Biller")


def _grp_of(payer: Any) -> str | None:
    return S._grp_kind(payer)[0] if payer else None


def _decide_step(conn: sqlite3.Connection, f: dict, fx: dict, st: dict) -> tuple[str, list[str], str, list[str]]:
    """A decision before any work: what to check, and the choices it leads to. Returns (action, steps, who, buttons)."""
    sig = f.get("signal") or ""
    carc = str(f.get("carc") or "")
    rarcs = {x.strip() for x in str(f.get("rarc") or "").split(",") if x.strip()}
    grp = f.get("payer_group") or "the payer"
    o = fx.get("outcome") or {}
    rule = st.get("rule") or ""
    hint = str(f.get("hint") or "")
    std = ["copy_instructions", "log_outcome", "let_go"]
    check = ["open_in_ema", "copy_instructions", "log_outcome"]
    deadline = st.get("deadline")
    if rule == "check_first":
        a, s_, w = _check_first_step(f, fx, st)
        return a, s_, w, check
    if rule == "settled_in_ema":
        se = st.get("settled") or {}
        return (f"Check first: EMA shows this line settled (insurance paid {usd(se.get('ins'))}, the patient paid {usd(se.get('pat'))}, "
                f"{usd(se.get('open'))} open). Find which payer paid in the claim's payments in EMA and whether it settles this service; if it "
                "does, close it as Paid with $0.00 recovered; if not, work the denial.", [], "Biller", check)
    if rule == "patient_paid_co":
        se = st.get("settled") or {}
        return (f"The patient paid {usd(se.get('pat'))} toward a denial {grp} put on the practice ({_carcs(fx)}): appeal it and refund the patient "
                "if the payer pays, or refund the patient if the denial stands (unless the patient agreed in writing beforehand, an ABN or a "
                "waiver).", ["The owner signs any refund to the patient; post it in EMA so the line shows it."],
                "Biller; the owner signs a refund", ["build_packet", "copy_instructions", "log_outcome", "let_go"])
    if rule == "decided_no_appeal":
        return (f"{st.get('state_basis')}. If that decision stands, Let go with that reason; if not, appeal"
                + (f" by {day(deadline)}." if deadline else "."), [], "Biller", std)
    if rule == "note_unread":
        base = dict(st, rule="open", route=None)
        nxt = S.route_of(fx, base, conn)
        what = {"appeal": "appeal it", "corrected": "send the corrected claim", "rate": "ask the payer to reprocess it",
                "call": "ask the payer where it stands", "cob": "find the plan that pays first"}.get(nxt, "work it")
        return (f"Read EMA's billing note on this claim first ({_lower(st.get('state_basis') or '')}): if it records an appeal, a call or a "
                "resubmission, Log what happened > The payer is working on it, with what the note says; if it records a decision not to appeal, "
                f"Let go with that reason; otherwise {what}.", [], "Biller", ["open_in_ema", "log_outcome", "let_go", "copy_instructions"])
    late = st.get("late_window") if rule == "open_late" else None
    if late and late.get("process") == "reopening":
        return (f"{_late_words(st)} A reopening fixes a clerical error only (a wrong code, modifier or diagnosis): if that is what went wrong, "
                f"ask the Medicare contractor for it on {_mac_portal(f)} or by phone, with the correction; if not, Let go with the "
                "reason \"window closed\".", ["Write the reopening's reference number in Mark sent."], "Coder, then the biller",
                ["copy_instructions", "mark_sent", "log_outcome", "let_go"])
    if o.get("outcome") == "lost_to_timely_filing":
        return ("Written off for late filing: appeal only with proof the claim reached the payer in time (the clearinghouse acceptance report or "
                "277CA). Without it, Let go with the reason \"lost to timely filing\" (provider liability: do not bill the patient).",
                [], "Biller", std)
    if hint.startswith(S.CHECK_HINTS):
        what = ("a payment on another claim" if hint.startswith("Possibly") else "an answer on another claim" if hint.startswith("Processed")
                else "fewer units paid than billed")
        return (f"Check first: {hint}. Open that claim and see whether it pays this service; if it does, mark this item Paid with that "
                "payment; if not, work the denial.", [f"RemitWatch found {what} that does not meet every test for closing it."], "Biller", check)
    if o.get("outcome") == "paid_before":
        pb = o.get("paid_before_first") or {}
        return (f"Check first: EMA shows this line paid {usd(o.get('paid_before'))} by {pb.get('payer') or 'insurance'} on {day(pb.get('date'))}, "
                "before this denial. Find out whether this denial is a duplicate or a second payer's answer before doing anything else.",
                ["Compare the payer that paid with the one that denied, and the claim numbers.",
                 "Same payer, same service: a duplicate; nothing owed. Another payer: check which is primary."], "Biller", check)
    if f.get("category") in S.SHORT:
        chk = _short_check(conn, fx)
        if chk and chk["kind"] == "reduced":
            return (f"Check first: {chk['text']}. If that explains the amount, let it go with that reason; if not, ask {grp} to reprocess "
                    f"{f.get('procedure_code')} at its usual rate ({usd(f.get('expected_cents'))}).",
                    ["Compare the payer's allowed amounts for the day's procedures on the remittance: the highest-valued one is paid in full, "
                     "the others at the reduced rate.", "A surgery-center day: compare with what the payer allows for this code at the facility."],
                    "Biller", std)
    if sig == "DENIED_MISSING_PRIMARY":
        prim = [x for x in fx["r835"]["rows"] if x["claim_key"] == f.get("claim_key") and (x["paid"] or 0) > 0 and x["code"] != f.get("procedure_code")]
        prim = prim or [x for x in S.standing_rows(fx["r835"]["rows"]) if (x["paid"] or 0) > 0 and x["code"] != f.get("procedure_code")]
        same = [d for d in _same_day_lines(conn, f) if (d["paid"] or 0) > 0]
        if prim or same:
            p = (f"{prim[0]['code']} was paid {usd(prim[0]['paid'])} on {day(prim[0]['date'])}" if prim else
                 f"{same[0]['code']} was paid {usd(same[0]['paid'])}")
            return (f"Ask {grp} to reprocess the add-on {f.get('procedure_code')} now: its main procedure was paid on the same claim ({p}), so "
                    "B15 does not apply.", ["File a reconsideration (or call) quoting the paid primary line, with the procedure note.",
                                            "Going forward, list primary codes before their add-ons."], "A/R follow-up biller", std)
    if carc == "B20" or "M86" in rarcs:
        return ("Not a benefit exclusion: B20 with M86 means the payer counts the visit as already paid inside a time frame, usually the global "
                "package of a recent procedure by the same clinician. Appeal only with a diagnosis unrelated to that procedure; otherwise let it go. "
                "Do not bill the patient (group PI or CO).", ["Check the patient's procedures in the 10 or 90 days before.",
                                                             "Read the note: unrelated problem, appeal with modifier 24; same wound, let it go."],
                "Coder", std)
    if carc == "B9":
        return ("The patient was in hospice: the plan is not liable while the member is enrolled; Original Medicare pays non-hospice care. "
                "Bill Original Medicare (with GW where it applies), or let it go if it already answered.", [], "Biller", std)
    if sig == "DENIED_PROVIDER":
        return ("Fix the clinician's enrollment with the payer (effective date back to the start date), then ask the payer to reprocess every "
                "claim it denied for it.", ["Credentialing: confirm participation and the effective date.",
                                            "Then one reprocessing request listing every affected claim."], "Credentialing coordinator", std)
    if carc in ("129",) and "N770" in rarcs:
        return ("Nothing to work: CO-129 with N770 says the payer processed the adjustment request on the original claim. Let it go with that "
                "note.", [], "Biller", std)
    if carc == "29" or sig == "DENIED_TIMELY_FILING":
        tp = _timely_proof(conn, f, fx)
        seen = []
        if tp["submitted"]:
            seen.append(f"EMA's claim-status export shows it first sent on {day(tp['submitted'])}")
        if tp["received"]:
            seen.append(f"the payer first received it on {day(tp['received'])}")
        lim = f" The first-filing limit was {day(tp['limit'])} ({_short(tp['label'])})." if tp["limit"] else ""
        if tp["in_time"]:
            return (f"A timely-filing denial, but {' and '.join(seen)}, inside the limit.{lim} Appeal with the clearinghouse acceptance report "
                    "(or 277CA) showing the first submission.", [], "Biller", ["build_packet"] + std)
        return ("A timely-filing denial: appeal only with proof it reached the payer in time (the clearinghouse acceptance report)." +
                (f" On file: {' and '.join(seen)}, after the limit.{lim} No earlier submission is on file." if seen else lim) +
                " Without that proof, Let go with the reason \"lost to timely filing\" (provider liability: do not bill the patient).", [],
                "Biller", std)
    if carc in AUTH_CARCS or sig == "DENIED_AUTH":
        what = {"243": "not authorized by the network or the primary care provider", "288": "a referral was absent",
                "197": "no authorization", "198": "the authorization was exceeded", "15": "the authorization number was missing or invalid",
                "62": "the authorization was missing or exceeded", "39": "authorization was denied"}.get(carc, "no authorization")
        steps = ["Look in EMA and the payer's portal for an authorization or referral covering the date of service.",
                 "Found: ask the payer to reprocess with its number (a corrected claim with the number, frequency 7, where the payer wants it).",
                 "Not found: ask for a retro-authorization (or a retro referral from the primary care provider's office, which submits it).",
                 "Neither: Let go with the reason, and tell the front desk which service needs it." +
                 (" A Medicaid member cannot be billed." if f.get("payer_kind") == "medicaid" else
                  " Do not bill the patient unless the payer's answer is final and the patient agreed in writing beforehand.")]
        words = f"{f['carc_group']}-{carc}" if f.get("carc_group") else _reason_words(fx)
        return (f"{grp} denied it because {what} ({words}): find the authorization or referral for {day(f.get('service_date'))}, or get one after "
                "the fact.", steps, "Referral coordinator, then the biller", std + ["mark_sent"])
    if sig == "DENIED_NONCOVERED":
        steps = ["Check the member's benefits for the service on the date (portal or call).",
                 "Covered after all, or another plan is primary: send it to the right payer, or appeal with the coverage rule.",
                 "Not covered: bill the patient only where allowed (group PR, or a signed advance notice); a CO denial and a Medicare Advantage "
                 "member without a pre-service determination cannot be billed; otherwise Let go with the reason."]
        if BENIGN_CODES.match(str(f.get("procedure_code") or "")):
            steps.insert(0, "Read the note against the coverage rule for removing benign lesions: it needs a documented reason (bleeding, "
                            "pain, itching, inflammation, rapid change, a suspicion of cancer, or a lesion that blocks vision or function) and a "
                            "diagnosis code that matches. If the note has it, dispute with the note; if not, let it go.")
        return ("Check benefits: send it to the right payer, bill the patient only where allowed, or let it go with the reason.", steps, "Biller", std)
    if sig == "CLAIM_DENIED_UNPAID":
        if carc in ("18", "B13"):
            return ("A whole-claim duplicate denial: find the claim it duplicates (the same bill's other submission) and how that one was "
                    "answered; nothing more is owed on this copy.", [], "Biller", std)
    if sig == "CODE_CHANGED":
        return ("Compare the note with both codes: if the code billed was right, appeal; if the payer's code was, let it go and change how it is "
                "billed.", [], "Coder", std)
    return (f"Read the reason ({_reason_words(fx)}): fix and resubmit if it names an error, appeal if the payer is wrong, let it go with the "
            "reason if the payer is right.", [], "Biller", std)


def _era_request(conn: sqlite3.Connection, fx: dict, proof: dict) -> str | None:
    """The text of a request for a missing ERA (payer, check date, amount): one request covers every claim on the check."""
    if not proof.get("check_tail"):
        return None
    pay = next((p for p in fx["ema"]["pays"] if p["check_tail"] == proof.get("check_tail") and not p["in_835"]), None)
    if pay is None:
        return None
    if pay.get("check") and (_check_in_835(conn, [(pay["check"], pay.get("payer") or "")]).get((pay["check"], pay.get("payer") or "")) or (False,))[0]:
        return None             # a number no 835 carries that stands for an 835 on file: there is no ERA to ask for
    total, claims = None, None
    if S._table(conn, "ema_checks") and pay.get("check"):
        r = conn.execute("SELECT ins_paid, links FROM ema_checks WHERE check_no=? AND (payer=? OR ?='')",
                         (pay["check"], pay.get("payer") or "", pay.get("payer") or "")).fetchone()
        if r:
            total, claims = r[0], r[1]
    who = proof.get("payer") or pay.get("payer") or "the payer"
    fwd = S._forwarded(fx) if proof.get("rule") == "forwarded_paid" else None
    text = (f"Ask {who} (through its portal, or TriZetto's ERA re-send) for the ERA of the check ending {proof['check_tail']}, posted "
            f"{day(pay.get('posted'))}" + (f", {usd(total)} in all for {claims} claim{'s' if claims != 1 else ''}" if total is not None and claims else
                                           (f", {usd(total)} in all" if total is not None else "")) + ", so the archive has it: one request covers every claim on that check.")
    if fwd:
        text += (f" The claim was forwarded to {fwd['carrier']}: check that TriZetto is enrolled for that carrier's ERAs, so its next payments "
                 "reach RemitWatch.")
    return text


def _expected(f: dict, fx: dict, story: dict | None = None) -> tuple[int, str | None]:
    """What working the item is worth, with the basis of the figure (orchestrator decision 5): its shortfall (a $0 shortfall
    is worth $0), else its expected amount; never more than the charge, never more than the payer's own allowed amount when
    its remittance shows one (the charge less its contractual reduction), and for a denial only EMA saw, never more than
    the denial took there (its write-off on the denial's posting day and what is still open)."""
    sc = f.get("shortfall_cents")
    exp = sc if sc is not None else (f.get("expected_cents") or 0)
    code = f.get("procedure_code") or "the code"
    basis = (f"RemitWatch's estimate: the payer's usual rate for {code} less what it paid" if (f.get("expected_cents") or 0) else
             "the amount the item lists")
    charge = f.get("charge_cents") or (S._ev(f).get("billed_cents") if f.get("signal") in S.CLAIM_SIGNALS else None)
    if charge and exp > charge:
        exp, basis = charge, "the charge"
    if f.get("category") in ("denial", "bundling"):
        for x in S.own_rows(fx):
            co45 = sum(a for g, c, a in x["adj"] if g == "CO" and c == "45")
            denied = sum(a for g, c, a in x["adj"] if not (g == "CO" and c in ("45", "253")) and g != "PR")
            if co45 and denied and x.get("charge") and (x["charge"] or 0) - co45 < exp:
                exp, basis = (x["charge"] or 0) - co45, "the 835's allowed amount (charge less CO-45)"
    if f.get("signal") == "EMA_DENIED" and fx["ema"]["lines"]:
        pay_day = f.get("payment_date")
        took = sum((z["contr"] or 0) + (z["nonc"] or 0) for z in fx["ema"]["reasons"]
                   if z["grp"] not in ("", "PR") and z.get("cls") not in ("routine", "patient") and (z["first"] == pay_day or z["last"] == pay_day))
        open_ = max(0, S._ema_line_totals(fx)["balance"])
        if took + open_ > 0 and took + open_ < exp:
            exp, basis = took + open_, "what the denial took in EMA (its write-off and what is still open)"
    return max(0, int(exp or 0)), basis


def _clerical_remark_step(f: dict, clocks: list[dict], st: dict) -> str | None:
    """A Medicare denial whose remark names a missing or invalid code (M51, M76 and the like, states.CLERICAL_RARCS) on a
    signal whose own step does not speak of a reopening: the step that compares the line with a paid one and names the
    reopening's date beside the redetermination's (C59)."""
    from .codes import MEDICARE_CLERICAL_SIGNALS, rarc_text
    if f.get("payer_kind") != "medicare" or f.get("signal") in MEDICARE_CLERICAL_SIGNALS or f.get("signal") == "DENIED_MEDICAL_NECESSITY":
        return None
    rarcs = [x.strip() for x in str(f.get("rarc") or "").split(",") if x.strip() in S.CLERICAL_RARCS]
    reopen = next((c for c in clocks if c["process"] == "reopening"), None)
    if not rarcs or not reopen:
        return None
    said = "; ".join(f"{r} ({rarc_text(r)})" if rarc_text(r) else r for r in rarcs)
    redet = st.get("legal_deadline") or st.get("deadline")
    return (f"The remark {said} points to a coding error: compare this line with a paid one of the same code (the code, NDC, units and "
            f"modifiers). A clerical error is fixed by a Medicare reopening until {day(reopen['date'])}, which takes no documents"
            + (f"; anything that needs records goes in a redetermination by {day(redet)}." if redet and redet < reopen["date"] else "."))


def _clocks(f: dict, fx: dict, st: dict) -> list[dict]:
    """Every clock the item runs on (states.windows, the same windows its state expires by), and the window counted from
    the first denial of the same service where the payer counts from the original determination (a safe, earlier date)."""
    from .codes import appeal_rule, filing_rule
    out = []
    for w in S.windows(f, st.get("deadline"), st.get("deadline_basis"), fx):
        what = {"the item's deadline": "the item's deadline", "the payer's own limit": "the payer's own limit",
                "the payer's dispute window": "appeal", "the corrected-claim limit": "corrected claim", "a Medicare reopening": "reopening",
                "the first-filing limit": "first filing"}.get(w["what"], w["what"])
        out.append({"what": what, "date": w["date"], "basis": w["basis"], "process": w["process"]})
    grp, kind = f.get("payer_group"), f.get("payer_kind")
    if f.get("payment_date") and f.get("signal") not in S.CLAIM_SIGNALS and f.get("category") in S.MONEY:
        a = appeal_rule(grp, kind)
        start = S.first_denial(fx, a)
        if start:
            out.append({"what": "first denial of this service", "date": S._add(start, a["days"]), "process": "appeal",
                        "basis": f"{_short(a['label'])}, counted from the first denial of this service ({day(start)}) to be safe"})
    if f.get("signal") in S.CLAIM_SIGNALS and f.get("service_date"):
        r = filing_rule(grp, kind)
        if r.get("earliest"):
            out.append({"what": "first filing (shortest common limit)", "date": S._add(f["service_date"], r["earliest"]), "process": "first_filing",
                        "basis": "the shortest common first-filing limit for this kind of payer; send it by then where possible"})
    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: c["date"] or ""):
        k = (c["what"], c["date"])
        if c["date"] and k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def _instructions(f: dict, fx: dict, action: str, steps: list[str], needs: list[str], deadline: str | None, basis: str | None) -> str:
    lines = [action]
    for i, s in enumerate([s for s in steps if s], 1):
        lines.append(f"{i}. {s}")
    if needs:
        lines.append("It needs: " + "; ".join(needs) + ".")
    if deadline:
        lines.append(f"By {day(deadline)}" + (f" ({_short(basis)})" if basis else "") + ".")
    return "\n".join(_clean(x) for x in lines)


def next_step(conn: sqlite3.Connection, finding_id: str, *, ledger: Any = None, today: str | None = None) -> dict:
    """What to do next, who, by when and why, what it is worth and on what basis, the buttons and the instructions:
    {'action', 'who', 'deadline', 'basis', 'expected_cents', 'expected_basis', 'buttons', 'instructions', 'route', 'steps',
    'needs', 'clocks', 'appeal_ok', 'appeal_block', 'claim_url', 'recovery', 'ranked_cents', 'siblings', 'pattern'}."""
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
