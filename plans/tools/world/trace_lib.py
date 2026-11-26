"""Hand-trace helper for the calibration of claim_audit.py (private folder only).

Prints, for a finding or a service group, every raw row that bears on it: the 835 processings of the whole bill (every
claim id of the bill), their lines, CAS and RARC, PLB entries naming the bill, the 837 sends, EMA weekly rows, and EMA's
books (aud_ema_lines_all, the Production postings and the A/R), plus the same patient's same service on other bills.

Privacy: no name, birth date, member id, claim id, payer claim number, check or trace number, address or exact date is
printed. Dates are printed as days from the date of service ("s+N"). Only the store's own keys (claim_key, line_key,
transaction_key), finding ids, codes, dollars, payer names and statuses appear.
"""
import datetime as dt
import json
import os
import re
import sqlite3
from collections import defaultdict

# Paths come from the environment (a private folder of your own, never a path in the repository):
#   TRACE_DIR     the private folder holding the world copy (TRACE_DB, default world.sqlite) and its posting ledger
#                 (TRACE_LEDGER, default ema_ledger.sqlite, else world_ema_ledger.sqlite, in TRACE_DIR)
#   TRACE_AUDIT   optional: an auditor output (audit_result.sqlite), attached as A for finding() and auditor_units()
BASE = os.environ.get("TRACE_DIR") or ""
BILL_RE = re.compile(r"^([A-Z]{2}\d{2}[A-Z0-9]{5,6})C\d{0,3}$")
AUD_RE = re.compile(r"^([A-Z]{2}\d{2}[A-Z0-9]{5})(C\d{0,3})?$")


def _ledger_path():
    p = os.environ.get("TRACE_LEDGER")
    if p:
        return p
    for nm in ("ema_ledger.sqlite", "world_ema_ledger.sqlite"):
        if os.path.exists(os.path.join(BASE, nm)):
            return os.path.join(BASE, nm)
    return os.path.join(BASE, "ema_ledger.sqlite")


def connect():
    if not BASE:
        raise SystemExit("set TRACE_DIR to the private folder holding the world copy")
    w = sqlite3.connect(f"file:{os.path.join(BASE, os.environ.get('TRACE_DB') or 'world.sqlite')}?mode=ro", uri=True)
    w.execute(f"attach 'file:{_ledger_path()}?mode=ro' as L")
    aud = os.environ.get("TRACE_AUDIT")
    if aud and os.path.exists(aud):
        w.execute(f"attach 'file:{aud}?mode=ro' as A")
    return w


def has_table(w, name, schema="main"):
    return w.execute(f"select 1 from {schema}.sqlite_master where type='table' and name=?", (name,)).fetchone() is not None


def iso(s):
    if s is None:
        return None
    s = str(s).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def rel(d, anchor):
    d = iso(d)
    if not d or not anchor:
        return "s?"
    n = (dt.date.fromisoformat(d) - dt.date.fromisoformat(anchor)).days
    return f"s{'+' if n >= 0 else ''}{n}"


def money(c):
    if c is None:
        return "-"
    return f"{c / 100:.2f}"


def sk(k):
    """Short form of a store key: first 6 characters of the file hash, then the claim (and line) sequence."""
    if not k:
        return "-"
    parts = str(k).split(":")
    if len(parts) >= 4:
        return parts[0][:6] + ":" + ":".join(parts[3:])
    return str(k)[:12]


def bill_of(pcn):
    s = (pcn or "").strip().upper()
    m = AUD_RE.match(s)
    if m:
        return m.group(1)
    return s


def sfx_of(pcn):
    s = (pcn or "").strip().upper()
    m = AUD_RE.match(s)
    if m:
        return m.group(2) or ""
    return ""


class Tracer:
    def __init__(self, out=print):
        self.w = connect()
        self.out = out
        self._sig = {}
        self.focus_lines = set()
        self.gov_lines = set()

    # ---------------------------------------------------------------------------------------------- 835
    def claims_of_bill(self, bill):
        rows = self.w.execute(
            "select claim_key, transaction_key, claim_seq, patient_control_number, claim_status_code, charge_cents, "
            "paid_cents, patient_responsibility_cents, claim_filing_indicator, facility_type_code, claim_frequency_code, "
            "statement_from, payer_name, payer_id, payment_date, crossover_carrier_name, rendering_provider_npi, "
            "payer_claim_control_number, patient_last, patient_first "
            "from claims where patient_control_number >= ? and patient_control_number < ?", (bill, bill + "~")).fetchall()
        return [r for r in rows if bill_of(r[3]) == bill]

    def tx_info(self, tk):
        r = self.w.execute("select payment_date, trace_number, superseded, payment_amount_cents, payer_name from transactions "
                           "where transaction_key=?", (tk,)).fetchone()
        return r

    def lines_of(self, ck):
        return self.w.execute(
            "select line_key, line_seq, procedure_qualifier, procedure_code, modifiers, charge_cents, paid_cents, paid_units,"
            " original_procedure_code, original_modifiers, original_units, service_date, service_period_start, allowed_cents,"
            " rendering_provider_npi from lines where claim_key=? order by line_seq", (ck,)).fetchall()

    def cas_of_line(self, lk):
        return self.w.execute("select group_code, reason_code, amount_cents from adjustments where line_key=? order by cas_index, id",
                              (lk,)).fetchall()

    def cas_of_claim(self, ck):
        return self.w.execute("select group_code, reason_code, amount_cents from adjustments where claim_key=? and "
                              "(line_key is null or line_key='') order by cas_index, id", (ck,)).fetchall()

    def rarc_of_line(self, lk):
        return [r[0] for r in self.w.execute("select code from remarks where line_key=?", (lk,))]

    def rarc_of_claim(self, ck):
        return [r[0] for r in self.w.execute("select code from remarks where claim_key=? and (line_key is null or line_key='')", (ck,))]

    # ---------------------------------------------------------------------------------------------- printing
    def p(self, *a):
        self.out(" ".join(str(x) for x in a))

    def fmt_cas(self, cas):
        return ",".join(f"{g}-{r}:{money(a)}" for g, r, a in cas) if cas else ""

    def svc_trace(self, bill, code, comp, dos, show_other_lines=True, focus_fids=()):
        """Everything about one service group, dates relative to its date of service."""
        anchor = dos
        claims = self.claims_of_bill(bill)
        claims.sort(key=lambda c: (iso(c[14]) or "", c[1] or "", c[2] or 0))
        # suffix order of claim ids
        pcns = sorted({c[3] for c in claims})
        c837 = self.w.execute(
            "select claim_837_key, bht_created, patient_control_number, total_charge_cents, frequency_code, payer_name, payer_id,"
            " service_date, place_of_service from claims_837 where patient_control_number >= ? and patient_control_number < ?",
            (bill, bill + "~")).fetchall()
        c837 = [c for c in c837 if bill_of(c[2]) == bill]
        ema = self.w.execute(
            "select family, source_name, activity_type, claim_status, claim_submission_date, posted_date, transaction_date,"
            " charges_cents, payments_cents, insurance_payments_cents, insurance_adjustments_cents, contractual_adjustments_cents,"
            " noncontractual_adjustments_cents, denial_amount_cents, units, payer, cpt, modifiers, service_date, claim_id,"
            " carc, claim_action, coverage_type, denial_date, bill_created from ema_rows where claim_id >= ? and claim_id < ?",
            (bill, bill + "~")).fetchall()
        ema = [e for e in ema if bill_of(e[19]) == bill]
        # the day each weekly export was made (its rows tell the claim's state as of that day)
        made = {}
        if has_table(self.w, "ema_sources"):
            made = {r[0]: iso(r[1]) for r in self.w.execute("select source_name, created_at from ema_sources")}
        srcs = {r[0]: r[1] for r in self.w.execute("select rowid, source_name from ema_rows where claim_id >= ? and "
                                                     "claim_id < ?", (bill, bill + "~"))} if made else {}
        exp_days = sorted({made.get(v) for v in srcs.values() if made.get(v)})
        if exp_days:
            self.p(f" EMA weekly exports holding this bill, made: {', '.join(rel(d, anchor) for d in exp_days)}")
        allp = sorted({c[3] for c in claims} | {c[2] for c in c837} | {e[19] for e in ema})
        idx = {p: i + 1 for i, p in enumerate(allp)}
        self.p(f"== service {code}/{comp} units: claim ids of bill={len(allp)}; 835 processings={len(claims)}; "
               f"837 sends={len(c837)}; EMA rows={len(ema)}")
        trace_seen = {}
        names = set()
        for c in claims:
            (ck, tk, seq, pcn, st, chg, paid, prr, prod, pos, freq, stmt, pname, pid, pdate, xover, npi, pccn, plast,
             pfirst) = c
            names.add(((plast or "").upper(), (pfirst or "").upper()))
            t = self.tx_info(tk)
            chk = (t[1], iso(t[0])) if t else None
            same = trace_seen.get(chk)
            trace_seen.setdefault(chk, ck)
            lines = self.lines_of(ck)
            tgt = [L for L in lines if self.line_code(L) == code and (iso(L[11]) or iso(L[12]) or iso(stmt)) == dos]
            others = [L for L in lines if L not in tgt]
            hdr = (f" 835 {rel(pdate, anchor)} #{idx.get(pcn, '?')} {str(pname)[:22]} st={st} f={freq} {prod} "
                   f"chg={money(chg)} paid={money(paid)} pr={money(prr)}"
                   f"{' XOVER' if (xover or '').strip() else ''}{' SUPERSEDED' if t and t[2] else ''}"
                   f"{' samechk:' + sk(same) if same else ''} ck={sk(ck)} npi..{str(npi or '')[-3:]}")
            self.p(hdr)
            ccas = self.cas_of_claim(ck)
            crarc = self.rarc_of_claim(ck)
            if ccas or crarc:
                self.p(f"     claim CAS {self.fmt_cas(ccas)} RARC {','.join(crarc)}")
            for L in tgt:
                mark = "  *"
                if L[0] in self.focus_lines:
                    mark = "  >ITEM"
                if L[0] in self.gov_lines:
                    mark += "<GOV"
                self.print_line(L, mark)
            if show_other_lines and others:
                parts = []
                for L in others[:10]:
                    cas = self.cas_of_line(L[0])
                    codes = sorted({f"{g}-{r}" for g, r, a in cas if r not in ("45", "253", "144", "237") and a})
                    md = self.mods(L[4])
                    d = iso(L[11]) or iso(L[12]) or iso(stmt)
                    parts.append(f"{self.line_code(L)}{('-' + md) if md else ''}{'' if d == dos else '@' + rel(d, anchor)}"
                                 f"{(' u' + str(int(float(L[7])))) if L[7] and float(L[7]) > 1 else ''} {money(L[6])}/{money(L[5])}"
                                 f"{(' ' + ','.join(codes)) if codes else ''}")
                self.p("     others: " + "; ".join(parts) + (" ..." if len(others) > 10 else ""))
        # PLB entries naming the bill
        plb = self.w.execute("select transaction_key, reason_code, reference, amount_cents, payer_name, payment_date from plb "
                             "where upper(reference) like ?", ("%" + bill + "%",)).fetchall()
        for tk, rc, ref, amt, pname, pdate in plb:
            m = re.search(r"([A-Z]{2}\d{2}[A-Z0-9]{5,6})(C\d{1,3})", (ref or "").upper())
            idn = idx.get(m.group(0), "?") if m else "?"
            self.p(f" PLB {rel(pdate, anchor)} {str(pname)[:22]} {rc} amt={money(amt)} names #{idn} tk={sk(tk + ':x:x:x') if tk else '-'}")
        # 837 sends
        for c in sorted(c837, key=lambda c: (iso(c[1]) or "")):
            (k, bht, pcn, tot, freq, pname, pid, sdate, pos) = c
            ls = self.w.execute("select procedure_code, modifiers, units, charge_cents, service_date from lines_837 where "
                                "claim_837_key=? order by line_seq", (k,)).fetchall()
            tl = [l for l in ls if (l[0] or "").upper() == code]
            desc = "; ".join(f"{l[0]}{('-' + self.mods(l[1])) if l[1] else ''} u{l[2]} {money(l[3])}"
                             f"{'' if (iso(l[4]) or iso(sdate)) == dos else '@' + rel(iso(l[4]) or iso(sdate), anchor)}" for l in tl)
            self.p(f" 837 {rel(bht, anchor)} #{idx.get(pcn, '?')} f={freq} {str(pname)[:22]} total={money(tot)} "
                   f"lines={len(ls)} this-code: {desc or 'NONE'}")
        # EMA weekly rows
        ema_sorted = sorted(ema, key=lambda e: (e[0], iso(e[5]) or iso(e[6]) or "", e[1] or ""))
        cs = [e for e in ema_sorted if e[0] == "claim_status"]
        keep = set()
        if cs:
            keep.add(id(cs[-1]))
            keep.add(id(cs[0]))
        for e in ema_sorted:
            if e[0] != "claim_status" and (e[16] or "").upper() == code:
                keep.add(id(e))
        ema_sorted = [e for e in ema_sorted if id(e) in keep]
        for e in ema_sorted:
            (fam, src, act, cst, subm, posted, tdate, chg, pay, inspay, insadj, contr, nonc, den, units, payer, cpt, mods, sdate,
             cid, carc, action, cov, ddate, bcreated) = e
            if (cpt or "").upper() != code and fam != "claim_status":
                continue
            if fam == "claim_status" and (cpt or "").upper() not in (code, ""):
                continue
            bits = [f"{n}={money(v)}" for n, v in (("chg", chg), ("pay", pay), ("inspay", inspay), ("insadj", insadj),
                                                     ("contr", contr), ("noncontr", nonc), ("denial", den)) if v]
            self.p(f" EMA {fam[:12]} #{idx.get(cid, '?')} {act or ''} st={cst} subm={rel(subm, anchor) if subm else '-'} "
                   f"post={rel(posted or tdate, anchor) if (posted or tdate) else '-'} {cpt}{('-' + mods) if mods else ''}"
                   f"{'' if iso(sdate) == dos else '@' + rel(sdate, anchor)} u{units} {' '.join(bits)} {carc or ''} "
                   f"{('action=' + action) if action else ''} {str(payer)[:20]}")
        self.ema_books(bill, code, dos, anchor)
        self.other_bills(bill, code, comp, dos, names, anchor)

    @staticmethod
    def mods(m):
        if not m:
            return ""
        m = str(m)
        if m.startswith("["):
            try:
                return ",".join(json.loads(m))
            except ValueError:
                return m
        return m

    def line_code(self, L):
        code = (L[3] or "").upper().strip()
        ocode = (L[8] or "").upper().strip()
        c = ocode or code
        m = re.match(r"^([A-Z0-9]{5})", c)
        return m.group(1) if m else c

    def print_line(self, L, mark):
        (lk, seq, qual, code, mods, chg, paid, punits, ocode, omods, ounits, sdate, pstart, allowed, npi) = L
        cas = self.cas_of_line(lk)
        rarc = self.rarc_of_line(lk)
        oc = f" orig={ocode}{('-' + self.mods(omods)) if omods else ''} u_orig={ounits}" if ocode else ""
        self.p(f"{mark} L{sk(lk)} {qual}:{code}{('-' + self.mods(mods)) if mods else ''}{oc} u={punits} chg={money(chg)} "
               f"paid={money(paid)} alw={money(allowed)} CAS[{self.fmt_cas(cas)}] RARC[{','.join(rarc)}]")

    # ---------------------------------------------------------------------------------------------- EMA books
    def ema_links_of_bill(self, bill):
        """EMA claims (links) tied to a bill by the link map or the A/R's claim links (keys only)."""
        out = set()
        if has_table(self.w, "ema_link_map"):
            out |= {r[0] for r in self.w.execute("select link from ema_link_map where bill=?", (bill,)) if r[0] is not None}
        if has_table(self.w, "ema_claim_links"):
            out |= {r[0] for r in self.w.execute("select link from ema_claim_links where bill=? or (claim_id >= ? and "
                                                 "claim_id < ?)", (bill, bill, bill + "~")) if r[0] is not None}
        return sorted(out)

    def postings(self, link, dos, code, anchor):
        ps = self.w.execute(
            "select p.posted, p.grp, p.code, ks.name, ss.name, acs.name, pys.name, p.ins_paid, p.pat_paid, p.contractual,"
            " p.noncontractual, p.pat_adj, p.allowed, p.charges, p.flags, p.check_no, p.units"
            " from L.postings p left join L.names ks on ks.id=p.kind left join L.names ss on ss.id=p.status"
            " left join L.names acs on acs.id=p.action left join L.names pys on pys.id=p.payer"
            " where p.link=? and p.svc=? and p.cpt=? order by p.posted", (link, dos, code)).fetchall()
        for (posted, grp, rc, kind, st2, act, payer, ip, pp, co, nc, pa, al, ch, fl, chk, un) in ps:
            bits = [f"{nm}={money(v)}" for nm, v in (("chg", ch), ("ins_paid", ip), ("pat_paid", pp), ("contr", co),
                                                     ("noncontr", nc), ("pat_adj", pa), ("allowed", al)) if v]
            self.p(f"   post {rel(posted, anchor)} {grp or ''}{('-' + rc) if rc else ''}{(' kind=' + kind) if kind else ''} "
                   f"[{st2}]{(' action=' + act) if act else ''} {str(payer)[:18]} {' '.join(bits)}"
                   f"{(' u=' + str(un)) if un else ''}{' chk' if chk else ''}")
        return len(ps)

    def ema_books_ledger(self, bill, code, dos, anchor):
        """EMA's books for a service from the link map and the posting ledger (a world without aud_ema_lines_all)."""
        links = self.ema_links_of_bill(bill)
        if not links:
            self.p(" BOOKS: no EMA claim tied to this bill by the link map or the A/R")
        for link in links:
            n = self.postings(link, dos, code, anchor)
            if not n:
                codes = [r[0] for r in self.w.execute("select distinct cpt from L.postings where link=? and svc=?",
                                                     (link, dos))]
                self.p(f" BOOKS: an EMA claim of the bill, no line for this code that day (codes that day: {codes})")
            if has_table(self.w, "ema_ar"):
                for a in self.w.execute("select balance_type, balance_cents, denial_cents, claim_status, claim_action, "
                                        "bucket, party, follow_up, note_kind from ema_ar where link=? and svc=? and cpt=?",
                                        (link, dos, code)):
                    self.p(f"   A/R: type={a[0]} bal={money(a[1])} denial={money(a[2])} status={a[3]} action={a[4]} "
                           f"bucket={a[5]} party={a[6]} note={a[8]}")

    def send_day_twins(self, bill, anchor):
        """EMA claims no link ties to any bill whose charges were first posted the day of the bill's first 837 send or
        the day before, with exactly its lines (day, code, charge): the bill's send-day twins, with their postings."""
        sends = self.w.execute("select claim_837_key, substr(bht_created,1,10), service_date from claims_837 where "
                               "patient_control_number >= ? and patient_control_number < ? and coalesce(frequency_code,'1')"
                               "<>'8' order by bht_created", (bill, bill + "~")).fetchall()
        sends = [x for x in sends if bill_of(self.w.execute("select patient_control_number from claims_837 where "
                                                            "claim_837_key=?", (x[0],)).fetchone()[0]) == bill]
        if not sends:
            self.p(" TWINS: no 837 send for this bill")
            return []
        k, day, sdate = sends[0]
        agg = defaultdict(int)
        for code, chg, ld in self.w.execute("select procedure_code, charge_cents, service_date from lines_837 where "
                                            "claim_837_key=?", (k,)):
            agg[(iso(ld) or iso(sdate), (code or "").upper())] += chg or 0
        want = sorted((d, c, ch) for (d, c), ch in agg.items() if ch > 0)
        lo = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
        cands = defaultdict(set)
        for d, c, ch in want:
            for (lk,) in self.w.execute("select link from L.postings where svc=? and cpt=? and charges>0 group by link "
                                        "having sum(charges)=? and min(posted) between ? and ?", (d, c, ch, lo, day)):
                cands[lk].add((d, c, ch))
        tied = set()
        if has_table(self.w, "ema_link_map"):
            tied = {r[0] for r in self.w.execute("select link from ema_link_map where bill is not null")}
        out = []
        for lk, got in cands.items():
            if lk in tied or sorted(got) != want:
                continue
            allc = self.w.execute("select count(distinct svc||cpt) from L.postings where link=? and charges>0",
                                  (lk,)).fetchone()[0]
            if allc != len(want):
                continue
            out.append(lk)
        self.p(f" TWINS: first send {rel(day, anchor)}, {len(want)} line(s); EMA claims of the same lines first charged "
               f"that day or the day before, tied to no bill: {len(out)}")
        for i, lk in enumerate(out[:4]):
            self.p(f"  twin #{i + 1}:")
            for d, c, ch in want:
                self.postings(lk, d, c, anchor)
        return out

    def ema_books(self, bill, code, dos, anchor):
        if not has_table(self.w, "aud_ema_lines_all"):
            return self.ema_books_ledger(bill, code, dos, anchor)
        rows = self.w.execute(
            "select link, charges, ins_charges, ins_paid, pat_paid, contractual, noncontractual, pat_adj, open, patient_joined,"
            " patient_ambiguous, status, status_class, primary_payer_group, first_posted, last_posted, a835_line, a835_visit,"
            " a835_bill, s837_line, s837_bill, first_837_sent, ar_rows, ar_balance, ar_denial, ar_aging_hi, product, bill_how"
            " from aud_ema_lines_all where bill=? and svc=? and cpt=?", (bill, dos, code)).fetchall()
        if not rows:
            # the EMA line may carry a code with a modifier or another day; show near matches by bill only
            near = self.w.execute("select count(*), group_concat(distinct cpt) from aud_ema_lines_all where bill=?", (bill,)).fetchone()
            self.p(f" BOOKS: no EMA claim line for this bill/day/code (EMA lines on the bill: {near[0]}, codes {near[1]})")
        for r in rows:
            (link, chg, inschg, inspaid, patpaid, contr, nonc, patadj, open_, pj, pamb, st, stc, ppg, fp, lp, a8l, a8v, a8b, s7l, s7b,
             f837, arr, arb, ard, arh, prod, how) = r
            self.p(f" BOOKS: chg={money(chg)} ins_paid={money(inspaid)} pat_paid={money(patpaid)} contr={money(contr)} "
                   f"noncontr={money(nonc)} pat_adj={money(patadj)} OPEN={money(open_)} [{st}] payer={ppg} "
                   f"835={a8l} 837={s7l} AR={arr or 0}/{money(arb)}/den {money(ard)}{' PAT_AMBIG' if pamb else ''}")
            ps = self.w.execute(
                "select p.posted, p.grp, p.code, ks.name, ss.name, acs.name, pys.name, p.ins_paid, p.pat_paid, p.contractual,"
                " p.noncontractual, p.pat_adj, p.allowed, p.charges, p.flags, p.check_no, p.units"
                " from L.postings p left join L.names ks on ks.id=p.kind left join L.names ss on ss.id=p.status"
                " left join L.names acs on acs.id=p.action left join L.names pys on pys.id=p.payer"
                " where p.link=? and p.svc=? and p.cpt=? order by p.posted", (link, dos, code)).fetchall()
            for (posted, grp, rc, kind, st2, act, payer, ip, pp, co, nc, pa, al, ch, fl, chk, un) in ps:
                on835 = None
                if chk:
                    on835 = self.w.execute("select max(in_835) from ema_line_payments where link=? and svc=? and cpt=? and check_no=?",
                                           (link, dos, code, chk)).fetchone()[0]
                bits = []
                for nm, v in (("chg", ch), ("ins_paid", ip), ("pat_paid", pp), ("contr", co), ("noncontr", nc), ("pat_adj", pa),
                              ("allowed", al)):
                    if v:
                        bits.append(f"{nm}={money(v)}")
                self.p(f"   post {rel(posted, anchor)} {grp or ''}{('-' + rc) if rc else ''}{(' kind=' + kind) if kind else ''} "
                       f"[{st2}]{(' action=' + act) if act else ''} {str(payer)[:18]} {' '.join(bits)}{(' u=' + str(un)) if un else ''}"
                       f"{' chk' + ('(835)' if on835 else '(not835)' if on835 is not None else '') if chk else ''}")
            ar = self.w.execute("select balance_type, balance_cents, denial_cents, claim_status, claim_action, bucket, party,"
                                " follow_up, note_kind from ema_ar where link=? and svc=? and cpt=?", (link, dos, code)).fetchall()
            for a in ar:
                self.p(f"   A/R: type={a[0]} bal={money(a[1])} denial={money(a[2])} status={a[3]} action={a[4]} bucket={a[5]} "
                       f"party={a[6]} note={a[8]}")

    # ---------------------------------------------------------------------------------------------- other bills
    def other_bills(self, bill, code, comp, dos, names, anchor):
        if not names:
            return
        rows = self.w.execute(
            "select l.line_key, l.claim_key, c.patient_control_number, c.patient_last, c.patient_first, c.payer_name, "
            "c.claim_status_code, c.payment_date, l.paid_cents, l.charge_cents, l.modifiers, l.allowed_cents "
            "from lines l join claims c on c.claim_key=l.claim_key where (l.procedure_code=? or l.original_procedure_code=?) "
            "and (l.service_date=? or l.service_date=?)", (code, code, dos, dos.replace("-", ""))).fetchall()
        found = 0
        for (lk, ck, pcn, last, first, pname, st, pdate, paid, chg, mods, allowed) in rows:
            if bill_of(pcn) == bill:
                continue
            if ((last or "").upper(), (first or "").upper()) not in names:
                continue
            found += 1
            cas = self.cas_of_line(lk)
            self.p(f" OTHER-BILL same patient/code/day: {rel(pdate, anchor)} {str(pname)[:22]} st={st} mods={self.mods(mods)} "
                   f"chg={money(chg)} paid={money(paid)} alw={money(allowed)} CAS[{self.fmt_cas(cas)}] L{sk(lk)}")

    # ---------------------------------------------------------------------------------------------- findings
    def finding(self, fid, show_services=True, max_svcs=4):
        f = self.w.execute(
            "select signal, category, status, line_key, claim_key, procedure_code, modifiers, service_date, payment_date, charge_cents,"
            " paid_cents, expected_cents, shortfall_cents, carc, evidence, first_seen, recovered_cents, written_off_cents,"
            " payer_group, payer_name, appeal_deadline, allowed_cents, provider_tier, rendering_provider_npi from findings where finding_id=?",
            (fid,)).fetchone()
        v = self.w.execute("select verdict, sub, item_cents, truth_cents, recovered_cents, units_claimed, units_allocated,"
                           " unit_states, keeper, in_scope, svc_keys, note, kind from A.verdicts where finding_id=?", (fid,)).fetchone()
        (sig, cat, status, lk, ck, code, mods, sdate, pdate, chg, paid, exp, short, carc, ev, first, rec, wo, pg, pname, dl,
         allowed, tier, npi) = f
        anchor = iso(sdate)
        try:
            evd = json.loads(ev) if ev else {}
        except ValueError:
            evd = {}
        self.p("=" * 110)
        self.p(f"FINDING {fid} {sig} [{cat}] status={status} payer={pg} code={code}{('-' + mods) if mods else ''} "
               f"chg={money(chg)} paid={money(paid)} allowed={money(allowed)} expected={money(exp)} shortfall={money(short)} "
               f"carc={carc} recovered={money(rec)} wo={money(wo)} answer={rel(pdate, anchor)} first_seen={rel(first, anchor)} "
               f"deadline={rel(dl, anchor) if dl else '-'} tier={tier} npi..{str(npi or '')[-3:]} L{sk(lk)} ck={sk(ck)}")
        safe = {}
        for k in ("basis", "unpaid", "codes", "units_unpaid", "codes_paid_on_the_bill", "days_waiting", "ema_status",
                  "billed_cents", "sent_basis", "recovery_counted_elsewhere", "claim_status", "usual_share", "usual_n", "rank",
                  "pct_of_usual", "level_adjustment_cents", "usual_rate_cents", "reason_code", "same_day_codes", "global_days",
                  "partly_paid", "ema_claim_status", "claim_charge_cents"):
            if k in evd:
                val = evd[k]
                if k == "unpaid" and isinstance(val, list):
                    val = [[u[0], rel(u[1], anchor)] if isinstance(u, list) and len(u) > 1 else u for u in val]
                if k == "partly_paid" and isinstance(val, dict):
                    val = {a: b for a, b in val.items() if a not in ("trace", "claim", "payment_date")}
                safe[k] = val
        for k in ("paid_later", "maybe_paid_later", "settled_elsewhere", "adjudicated_elsewhere", "ema_shows_paid", "secondary_answer"):
            if isinstance(evd.get(k), dict):
                d = evd[k]
                safe[k] = {a: (rel(b, anchor) if a in ("payment_date", "date") else b) for a, b in d.items()
                           if a not in ("trace", "claim", "pcn", "payer_claim_control_number", "claim_id")}
        if "lines" in evd and isinstance(evd["lines"], list):
            safe["lines"] = [[x[0], rel(x[1], anchor)] + list(x[2:3]) if isinstance(x, list) and len(x) > 1 else x
                             for x in evd["lines"]][:12]
        if "sent" in evd:
            safe["sent"] = rel(evd["sent"], anchor)
        self.p("  evidence:", json.dumps(safe, sort_keys=True)[:900])
        evs = self.w.execute("select at, kind, from_status, to_status, amount_cents, actor from finding_events where finding_id=? "
                             "order by at", (fid,)).fetchall()
        for e in evs:
            self.p(f"  event {rel(e[0], anchor)} {e[1]} {e[2]}->{e[3]} amt={money(e[4])} actor={e[5]}")
        if v:
            (verdict, sub, ic, tc, rc, kc, kg, states, keeper, insc, svk, note, kind) = v
            self.p(f"  AUDITOR: {verdict} [{sub}] kind={kind} item={money(ic)} truth={money(tc)} recovered={money(rc)} units claimed="
                   f"{kc} got={kg} states={states} keeper={keeper} in_scope={insc} note={note}")
            keys = json.loads(svk) if svk else []
            self.focus_lines = {lk} if lk else set()
            for key in keys[:max_svcs]:
                b, c, cp, d = key.split("|")
                self.gov_lines = set()
                self.auditor_units(b, c, cp, d, d)
                if show_services:
                    self.svc_trace(b, c, cp, d)
            return keys
        return []

    def auditor_units(self, bill, code, comp, dos, anchor):
        us = self.w.execute("select units_billed, unit_from, unit_to, state, sub, carc, fam, gov_line_key, gov_date, "
                            "owed_cents_per_unit, amount_basis, window_end, inside_window, in_scope, scope_basis, open_items, "
                            "closed_items, left_on_table from A.units where bill=? and code=? and comp=? and dos=?",
                            (bill, code, comp, dos)).fetchall()
        for u in us:
            if u[7]:
                self.gov_lines.add(u[7])
            self.p(f"  AUD-UNIT {code}/{comp} U={u[0]} units {u[1]}-{u[2]}: {u[3]} [{u[4]}] carc={u[5]} fam={u[6]} gov={u[7]} "
                   f"gov_at={rel(u[8], anchor) if u[8] else '-'} owed/unit={money(u[9])} basis={u[10]} window_end="
                   f"{rel(u[11], anchor) if u[11] else '-'} inside={u[12]} scope={u[13]}/{u[14]} open=[{u[15]}] closed=[{u[16]}] LOT={u[17]}")


def ema_by_person(T, bill, code, dos, anchor=None):
    """Find EMA's Production line for an unanswered service through the patient's other bills: 837 subscriber id ->
    835 member ids -> those claims' bills -> EMA person key -> EMA lines with that person, day and code."""
    anchor = anchor or dos
    w = T.w
    subs = {r[0] for r in w.execute("select subscriber_id from claims_837 where patient_control_number >= ? and "
                                    "patient_control_number < ?", (bill, bill + "~")) if r[0]}
    persons = set()
    for sub in subs:
        for (pcn,) in w.execute("select distinct patient_control_number from claims where patient_id=? or insured_id=?",
                                (sub, sub)):
            b2 = bill_of(pcn)
            for (p,) in w.execute("select distinct person from aud_ema_lines_all where bill=? and person is not null", (b2,)):
                persons.add(p)
    # the weekly EMA rows of this bill carry EMA's patient id: other bills of that patient id
    pids = {r[0] for r in w.execute("select distinct patient_id from ema_rows where claim_id >= ? and claim_id < ? and "
                                    "patient_id is not null", (bill, bill + "~"))}
    for pid in pids:
        for (cid,) in w.execute("select distinct claim_id from ema_rows where patient_id=? and claim_id is not null", (pid,)):
            for (p,) in w.execute("select distinct person from aud_ema_lines_all where bill=? and person is not null",
                                  (bill_of(cid),)):
                persons.add(p)
    T.p(f" BY-PERSON: subscriber ids={len(subs)} ema patient ids={len(pids)} persons found={len(persons)}")
    for p in persons:
        rows = w.execute("select link, charges, ins_paid, pat_paid, contractual, noncontractual, pat_adj, open, status, "
                         "primary_payer_group, a835_line, s837_line, ar_rows, ar_balance, ar_denial, bill from aud_ema_lines_all "
                         "where person=? and svc=? and cpt=?", (p, dos, code)).fetchall()
        for r in rows:
            T.p(f"   EMA line (same patient/day/code): chg={money(r[1])} ins_paid={money(r[2])} pat_paid={money(r[3])} "
                f"contr={money(r[4])} noncontr={money(r[5])} pat_adj={money(r[6])} OPEN={money(r[7])} [{r[8]}] payer={r[9]} "
                f"835={r[10]} 837={r[11]} AR={r[12] or 0}/{money(r[13])}/den {money(r[14])} same_bill={r[15] == bill}")
            ps = w.execute("select p.posted, p.grp, p.code, ss.name, pys.name, p.ins_paid, p.pat_paid, p.contractual, "
                           "p.noncontractual, p.pat_adj, p.charges, p.check_no from L.postings p left join L.names ss on ss.id=p.status "
                           "left join L.names pys on pys.id=p.payer where p.link=? and p.svc=? and p.cpt=? order by p.posted",
                           (r[0], dos, code)).fetchall()
            for (posted, grp, rc, st2, payer, ip, pp, co, nc, pa, ch, chk) in ps:
                on835 = None
                if chk:
                    on835 = w.execute("select max(in_835) from ema_line_payments where link=? and svc=? and cpt=? and check_no=?",
                                      (r[0], dos, code, chk)).fetchone()[0]
                bits = [f"{n}={money(v)}" for n, v in (("chg", ch), ("ins_paid", ip), ("pat_paid", pp), ("contr", co),
                                                     ("noncontr", nc), ("pat_adj", pa)) if v]
                T.p(f"     post {rel(posted, anchor)} {grp or ''}{('-' + rc) if rc else ''} [{st2}] {str(payer)[:18]} "
                    f"{' '.join(bits)}{' chk' + ('(835)' if on835 else '(not835)' if on835 is not None else '') if chk else ''}")
