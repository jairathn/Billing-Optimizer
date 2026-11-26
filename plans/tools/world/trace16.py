"""Hand-trace helper for A16 (private folder only): trace_lib's Tracer with the A16 results attached as A (and the A15
results as A15). Same privacy rules as trace_lib: keys, codes, dollars, payer names, days relative to the service.

Usage: python3 -I trace16.py RESULT_DIR --fid FID [FID ...]
       python3 -I trace16.py RESULT_DIR --svc ROWNO (a row of a sample file, never a key on the command line)
       python3 -I trace16.py RESULT_DIR --person ROWNO   (EMA lines through the patient for a sampled service)"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "/tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/audit"
sys.path.insert(0, os.path.join(BASE, "scripts"))
import trace_lib  # noqa: E402


def make_tracer(result_dir, world=None):
    world = world or os.path.join(BASE, "world.sqlite")
    ledger = os.path.join(BASE, "world_ema_ledger.sqlite")

    def connect():
        w = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
        w.execute(f"attach 'file:{ledger}?mode=ro' as L")
        w.execute(f"attach 'file:{os.path.join(result_dir, 'audit_result.sqlite')}?mode=ro' as A")
        w.execute(f"attach 'file:{os.path.join(BASE, 'audit_result.sqlite')}?mode=ro' as A15")
        return w
    trace_lib.connect = connect
    return trace_lib.Tracer()


def ema_used(T, bill, code, comp, dos):
    """The EMA lines A16 matched to this service (its ema_links), with their postings and A/R rows, dates relative."""
    r = T.w.execute("select ema_how, ema_links, primary_fam from A.units where bill=? and code=? and comp=? and dos=? limit 1",
                    (bill, code, comp, dos)).fetchone()
    if not r:
        return
    how, links, pfam = r
    T.p(f"  A16 primary payer of the bill: {pfam or '-'}; EMA lines matched: {how or 'none'} ({len(links.split(',')) if links else 0})")
    for link in (links.split(",") if links else []):
        ps = T.w.execute(
            "select p.posted, p.grp, p.code, ss.name, acs.name, pys.name, p.ins_paid, p.pat_paid, p.contractual,"
            " p.noncontractual, p.pat_adj, p.allowed, p.charges, p.check_no, p.units"
            " from L.postings p left join L.names ss on ss.id=p.status left join L.names acs on acs.id=p.action"
            " left join L.names pys on pys.id=p.payer where p.link=? and p.svc=? and p.cpt=? order by p.posted",
            (int(link), dos, code)).fetchall()
        for (posted, grp, rc, st2, act, payer, ip, pp, co, nc, pa, al, ch, chk, un) in ps:
            on835 = None
            if chk:
                m = T.w.execute("select match from ema_checks where check_no=? and payer=?", (chk, payer)).fetchone()
                on835 = bool(m and m[0] in ("exact", "loose"))
            bits = [f"{nm}={trace_lib.money(v)}" for nm, v in (("chg", ch), ("ins_paid", ip), ("pat_paid", pp), ("contr", co),
                                                               ("noncontr", nc), ("pat_adj", pa)) if v]
            T.p(f"   ema {trace_lib.rel(posted, dos)} {grp or ''}{('-' + rc) if rc else ''} [{st2}]{(' action=' + act) if act else ''}"
                f" {str(payer)[:20]} {' '.join(bits)}{(' allowed=' + trace_lib.money(al)) if al else ''}"
                f"{' chk' + ('(835)' if on835 else '(not835)') if chk else ''}")
        for a in T.w.execute("select balance_cents, denial_cents, claim_status, note_kind, note_date, follow_up, coverage, party "
                             "from ema_ar where link=? and svc=? and cpt=?", (int(link), dos, code)).fetchall():
            T.p(f"   ema A/R: bal={trace_lib.money(a[0])} denial={trace_lib.money(a[1])} status={a[2]} note={a[3]} "
                f"note_at={trace_lib.rel(a[4], dos) if a[4] else '-'} follow_up={trace_lib.rel(a[5], dos) if a[5] else '-'} "
                f"cov={a[6]} party={a[7]}")


def a15_line(T, fid):
    r = T.w.execute("select verdict, sub, truth_cents, unit_states from A15.verdicts where finding_id=?", (fid,)).fetchone()
    if r:
        T.p(f"  A15 said: {r[0]} [{r[1]}] truth={trace_lib.money(r[2])} states={r[3]}")


def main():
    rd = sys.argv[1]
    T = make_tracer(rd)
    args = sys.argv[2:]
    if args and args[0] == "--fid":
        for fid in args[1:]:
            keys = T.finding(fid)
            a15_line(T, fid)
            for key in keys[:4]:
                b, c, cp, d = key.split("|")
                ema_used(T, b, c, cp, d)
    elif args and args[0] in ("--svc", "--person"):
        sample = os.path.join(rd, "..", "calib", "sample16.tsv") if len(args) < 3 else args[2]
        rows = [ln.rstrip("\n").split("\t") for ln in open(sample)]
        r = rows[int(args[1])]
        key = [x for x in r if x.count("|") == 3][0]
        b, c, cp, d = key.split("|")
        T.auditor_units(b, c, cp, d, d)
        r15 = T.w.execute("select unit_from, unit_to, state, sub, carc, owed_cents_per_unit, left_on_table from A15.units "
                          "where bill=? and code=? and comp=? and dos=?", (b, c, cp, d)).fetchall()
        for x in r15:
            T.p(f"  A15-UNIT units {x[0]}-{x[1]}: {x[2]} [{x[3]}] carc={x[4]} owed/unit={trace_lib.money(x[5])} LOT={x[6]}")
        T.svc_trace(b, c, cp, d)
        ema_used(T, b, c, cp, d)
        if args[0] == "--person":
            trace_lib.ema_by_person(T, b, c, d)


if __name__ == "__main__":
    main()
