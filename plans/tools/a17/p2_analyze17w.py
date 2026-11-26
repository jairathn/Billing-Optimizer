"""Score a detection run's worklist with the independent auditor (claim_audit_a17.py, rules A17; AUDITOR=claim_audit_a16
scores the same run with A16) and compare it with a pass-1 output when there is one.

The auditor's truth is computed from the raw 835 / 837 / EMA weekly tables only (findings play no part in it), so the
truth on p2/world_p2.sqlite is the truth of pass 1 (checked: the unit tables must be equal row for row).

Views of push 2's items:
  S (status view): the auditor as it is: every item whose status is open, in_progress or appealed is listed.
  W (worklist view): what push 2's biller list "Work now" shows (desk.js default state 'todo' = api._state_sql):
     stale = 0 and (work_state 'work_now', or 'waiting' with follow_up_on <= TODAY). Items push 2 keeps open but off
     that list (done, accepted, expired, cant_check, waiting not due) are judged as not listed: their owed units become
     money left on the table unless a person decided them.

Writes (private, mode 600, in p2/): out_p2/ (the auditor's own outputs for view S), p2_items.tsv (one row per push-2 item:
finding id, signal, status, state, verdicts S and W, pass-1 verdict), p2_lot.tsv (left-on-table units, both views and
pass 1), p2_eval.json (every aggregate). Prints aggregates only.

Usage: AUDIT_DIR=PRIVATE_DIR [AUDITOR=claim_audit_a16] python3 -I -B p2_analyze17w.py WORLD_COPY TAG
  AUDIT_DIR holds p2/ema_ledger.sqlite (the world's posting ledger) and, optionally, a16/pass1/audit_result.sqlite.
  Outputs go to AUDIT_DIR/<rules>/p2/ (private, mode 600). AUDIT_AS_OF and AUDIT_TODAY override the two dates.
"""
import copy
import json
import re
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict

import importlib  # noqa: E402

A = os.environ.get("AUDIT_DIR") or sys.exit("set AUDIT_DIR to the private audit folder (world copies, pass-1 results)")
P2 = os.path.join(A, "p2")
DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(P2, "world.sqlite")
LEDGER = os.environ.get("AUDIT_LEDGER") or os.path.join(P2, "ema_ledger.sqlite")
TAG = sys.argv[2] if len(sys.argv) > 2 else "p2"
AUDITOR = os.environ.get("AUDITOR") or "claim_audit_a17"
PASS1 = os.path.join(A, "a16", "pass1", "audit_result.sqlite")
AS_OF = os.environ.get("AUDIT_AS_OF") or "2026-11-23"      # the auditor's as-of date (pass 1's)
TODAY = os.environ.get("AUDIT_TODAY") or "2026-11-24"      # the day the detection run was dated
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # plans/tools: the auditors
ca = importlib.import_module(AUDITOR)
OUTDIR = os.path.join(A, ca.RULES_VERSION.lower(), "p2")
OUT = os.path.join(OUTDIR, "out_" + TAG)

OPEN_ST = ("open", "in_progress", "appealed")


def ro(path):
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


def pass1():
    if not os.path.exists(PASS1):
        return {}, []       # no pass-1 output in this folder: its columns stay empty
    c = ro(PASS1)
    v = {}
    for r in c.execute("select finding_id, signal, category, status, kind, verdict, sub, item_cents, truth_cents, "
                       "recovered_cents, units_claimed, units_allocated, in_scope, svc_keys, keeper from verdicts"):
        v[r[0]] = dict(zip(("fid", "signal", "category", "status", "kind", "verdict", "sub", "item", "truth", "recovered",
                            "k_claim", "k_got", "in_scope", "svc_keys", "keeper"), r))
    units = []
    for r in c.execute("select bill, code, comp, dos, units_billed, unit_from, unit_to, state, sub, carc, fam, "
                       "owed_cents_per_unit, inside_window, in_scope, open_items, closed_items, left_on_table from units"):
        units.append(r)
    c.close()
    return v, units


def unit_rows(a):
    """The auditor's units table rows (as write_outputs builds them, without the item columns) for the equality check."""
    rows = []
    for key, sv in a.svcs.items():
        for i, u in enumerate(sv.units):
            rows.append((sv.bill, sv.code, sv.comp, sv.dos, sv.U, i + 1, u[0], u[1], u[2], u[3], u[6], 1 if u[9] else 0,
                         1 if sv.in_scope else 0))
    return rows


def expand_pass1_units(units):
    out = []
    for (bill, code, comp, dos, U, fr, to, st, sub, carc, fam, owed, inside, scope, oi, ci, lot) in units:
        for i in range(fr, to + 1):
            out.append((bill, code, comp, dos, U, i, st, sub, carc, fam, owed, inside, scope))
    return out


def p2_items():
    c = ro(DB)
    cols = [r[1] for r in c.execute("pragma table_info(findings)")]
    want = ["finding_id", "signal", "category", "status", "stale", "work_state", "follow_up_on", "state_basis", "source",
            "shortfall_cents", "expected_cents", "recovered_cents", "ema_outcome", "appeal_block", "written_off_cents",
            "written_off_source", "written_off_kind", "actionable", "payer_group", "first_seen", "auto_note", "hint",
            "line_key", "claim_key", "evidence"]
    sel = [x if x in cols else "NULL" for x in want]
    out = {}
    for r in c.execute(f"select {', '.join(sel)} from findings"):
        d = dict(zip(want, r))
        ev = d.pop("evidence")
        try:
            ev = json.loads(ev) if ev else {}
        except ValueError:
            ev = {}
        d["ev_keys"] = sorted(ev.keys()) if isinstance(ev, dict) else []
        d["paid_later"] = bool(isinstance(ev, dict) and ev.get("paid_later"))
        pl = ev.get("paid_later") if isinstance(ev, dict) else None
        d["paid_later_source"] = (pl.get("source") or pl.get("via") or "") if isinstance(pl, dict) else ""
        d["auto_note_kind"] = re.sub(r"[0-9$.,/#]+", "#", (d.get("auto_note") or "").split(":")[0])[:40]
        out[d["finding_id"]] = d
    meta = {}
    for k in ("ema:wiring", "ema:payer_policy", "ema:unanswered_unseen", "ema:policy_summary"):
        r = c.execute("select value from meta where key=?", (k,)).fetchone()
        meta[k] = r[0] if r else None
    runs = c.execute("select run_id, started_at, finished_at, version, lines, findings from detection_runs "
                     "order by finished_at desc limit 3").fetchall()
    c.close()
    return out, meta, runs


def basis_kind(b):
    """The rule that decided a push-2 state, from state_basis (its leading words, no values)."""
    if not b:
        return ""
    return b.split(":")[0].split(" (")[0][:60]


def listed_w(d):
    if (d.get("stale") or 0) == 1:
        return False
    ws = d.get("work_state")
    if ws is None:      # api._STATE_GUESS
        st = d.get("status")
        ws = "done" if st == "resolved" else "accepted" if st in ("lost", "dismissed") else "waiting" if st == "appealed" \
            else "work_now"
    if ws == "work_now":
        return True
    return ws == "waiting" and bool(d.get("follow_up_on")) and d["follow_up_on"] <= TODAY


def verdict_table(a, verdicts, flag_open):
    """(verdict, open|closed) -> [items, cents] and in-scope; sub-kinds -> [items, cents] (all and in scope)."""
    vt, vt_s, sub, sub_s = Counter(), Counter(), Counter(), Counter()
    vc, vc_s, subc, subc_s = Counter(), Counter(), Counter(), Counter()
    for it in a.items:
        v = verdicts[it["fid"]]
        is_open = flag_open(it)
        key = (v["verdict"], "open" if is_open else "closed")
        cents = (it["amount"] or 0) if is_open else (it["recovered"] or 0)
        sc = any(sv.in_scope for sv, k in it["parts"])
        vt[key] += 1
        vc[key] += cents
        sk = (v["verdict"], "open" if is_open else "closed", v["sub"].split(";")[0] if v["sub"] else "")
        sub[sk] += 1
        subc[sk] += cents
        if sc:
            vt_s[key] += 1
            vc_s[key] += cents
            sub_s[sk] += 1
            subc_s[sk] += cents
    return ({"|".join(k): [vt[k], vc[k], vt_s[k], vc_s[k]] for k in sorted(vt)},
            {"|".join(k): [sub[k], subc[k], sub_s[k], subc_s[k]] for k in sorted(sub)})


def lot_table(lot):
    t = Counter()
    tc = Counter()
    svcs = defaultdict(set)
    for sv, i, u in lot:
        k = (u[0], u[1] if u[0] in ("NEVER_ANSWERED", "SHORT", "WORKED_WAITING", "TAKEN_BACK") else "",
             "in_scope" if sv.in_scope else "out_of_scope")
        t[k] += 1
        tc[k] += u[6]
        svcs[k].add(sv.key)
    return {"|".join(k): [len(svcs[k]), t[k], tc[k]] for k in sorted(t)}


def why_of(it):
    """Structural facts behind an item's verdict (no values): its own answer reversed or a copy, the unit's paid answer
    before or after the item's answer, on another bill, the unit subs."""
    out = []
    A0 = it.get("ans")
    p = it.get("proc")
    if A0 is not None and not A0.standing:
        out.append("own_answer_not_standing")
    elif p is not None and (p.cancelled_by is not None or p.copy_of is not None):
        out.append("own_claim_reversed_or_copy")
    d0 = p.date if p is not None else ""
    subs = set()
    for sv, k in it["parts"]:
        for u in sv.units:
            if u[0] in ("PAID", "SHORT"):
                if u[1] == "paid_on_other_bill":
                    subs.add("paid_other_bill")
                elif u[5] and d0 and u[5] < d0:
                    subs.add("paid_before_item")
                elif u[5] and d0 and u[5] == d0:
                    subs.add("paid_same_day")
                elif u[5] and d0:
                    subs.add("paid_after_item")
            elif u[0] in ("MOVED_TO_PATIENT", "SETTLED_SECONDARY", "VOIDED", "NOT_PAYABLE"):
                subs.add(u[0].lower() + ("_before" if u[5] and d0 and u[5] < d0 else "_after" if u[5] and d0 and u[5] > d0 else ""))
    return ",".join(out + sorted(subs))


def main():
    t0 = time.time()
    p1v, p1units = pass1()
    items2, meta, runs = p2_items()
    print("push 2 runs:", [(r[0], r[3], r[4], r[5]) for r in runs])
    con = ro(DB)
    lcon = ro(LEDGER)
    raw = ca.load(con, lcon)
    con.close()
    lcon.close()
    kw = {}
    if ca.RULES_VERSION >= "A17":
        # the payer window table (A17_WINDOWS: a path) and, for diagnostics only, rules switched off (A17_DISABLE)
        kw = {"windows": os.environ.get("A17_WINDOWS") or None,
              "disable": {x.strip() for x in (os.environ.get("A17_DISABLE") or "").split(",") if x.strip()}}
        print("A17 windows table:", "on" if kw["windows"] else "off (A16 windows)", "| rules off:",
              sorted(kw["disable"]) or "none", flush=True)
    a = ca.Audit(raw, AS_OF, log=lambda *x: None, **kw).run()
    print("auditor seconds", round(time.time() - t0, 1), flush=True)
    if True:
        path, spath, summary = ca.write_outputs(a, OUT)
        print("auditor outputs written (view S)")
    else:
        summary = ca.summarize(a)
    # 1. the truth is pass 1's truth
    mine = sorted(unit_rows(a))
    theirs = sorted(expand_pass1_units(p1units))
    same_truth = mine == theirs
    print("truth units equal to pass 1:", same_truth, len(mine), len(theirs))
    # 2. view S
    VS = copy.deepcopy(a.verdicts)
    LOT_S = list(a.lot)
    S_open = {it["fid"]: it["is_open"] for it in a.items}
    S_parts = {it["fid"]: [(sv.key, k) for sv, k in it["parts"]] for it in a.items}
    S_scope = {it["fid"]: any(sv.in_scope for sv, k in it["parts"]) for it in a.items}
    S_kind = {it["fid"]: it["kind"] for it in a.items}
    vS, subS = verdict_table(a, VS, lambda it: S_open[it["fid"]])
    WHY = {it["fid"]: why_of(it) for it in a.items}
    lotS = lot_table(LOT_S)
    # 3. view W: only items on push 2's Work now list count as listed
    listed = {}
    for it in a.items:
        d = items2.get(it["fid"]) or {}
        listed[it["fid"]] = bool(it["is_open"]) and listed_w(d)
    for it in a.items:
        it["is_open"] = listed[it["fid"]]
        it["todo"] = it["todo"] and listed[it["fid"]]
    a.judge()
    a.left_on_table()
    VW = copy.deepcopy(a.verdicts)
    LOT_W = list(a.lot)
    if True:
        oc = sqlite3.connect(os.path.join(OUT, "audit_result.sqlite"))
        oc.execute("drop table if exists verdicts_w")
        oc.execute("create table verdicts_w as select * from verdicts where 0")
        oc.execute("alter table verdicts_w add column listed INTEGER")
        cols = [r[1] for r in oc.execute("pragma table_info(verdicts)")]
        base = {r[0]: r for r in oc.execute("select * from verdicts")}
        rowsw = []
        for it in a.items:
            r = list(base[it["fid"]])
            v = VW[it["fid"]]
            r[cols.index("verdict")], r[cols.index("sub")] = v["verdict"], v["sub"]
            r[cols.index("truth_cents")], r[cols.index("units_allocated")] = v["truth"], v["k_got"]
            r[cols.index("keeper")] = v["keeper"]
            rowsw.append(tuple(r) + (1 if listed[it["fid"]] else 0,))
        oc.executemany(f"insert into verdicts_w values ({','.join('?' * (len(cols) + 1))})", rowsw)
        try:
            oc.execute("alter table units add column lot_w INTEGER default 0")
        except sqlite3.OperationalError:
            pass
        oc.execute("update units set lot_w=0")
        wl = defaultdict(list)
        for sv, i, u in LOT_W:
            wl[sv.key].append(i + 1)
        # units rows are runs of identical units: split a run that is only partly left on the table in view W
        for key, idxs in wl.items():
            rs = oc.execute("select rowid, unit_from, unit_to from units where bill=? and code=? and comp=? and dos=?",
                            key).fetchall()
            for rowid, fr, to in rs:
                inside = [i for i in idxs if fr <= i <= to]
                if not inside:
                    continue
                if len(inside) == to - fr + 1:
                    oc.execute("update units set lot_w=1 where rowid=?", (rowid,))
                else:
                    row = oc.execute("select * from units where rowid=?", (rowid,)).fetchone()
                    ucols = [r[1] for r in oc.execute("pragma table_info(units)")]
                    oc.execute("delete from units where rowid=?", (rowid,))
                    for i in range(fr, to + 1):
                        nr = list(row)
                        nr[ucols.index("unit_from")] = i
                        nr[ucols.index("unit_to")] = i
                        nr[ucols.index("lot_w")] = 1 if i in inside else 0
                        oc.execute(f"insert into units values ({','.join('?' * len(ucols))})", nr)
        oc.commit()
        oc.close()
    vW, subW = verdict_table(a, VW, lambda it: listed[it["fid"]])
    lotW = lot_table(LOT_W)
    # 4. per item rows
    p1fids = set(p1v)
    rows = []
    state_by_verdict = Counter()
    state_by_verdict_c = Counter()
    state_by_verdict_s = Counter()
    state_by_verdict_sc = Counter()
    for it in a.items:
        fid = it["fid"]
        d = items2.get(fid) or {}
        vs, vw = VS[fid], VW[fid]
        p1 = p1v.get(fid)
        ws = d.get("work_state") or ""
        if S_open[fid]:
            k = (vs["verdict"], ws or "none", "listed" if listed[fid] else "off")
            state_by_verdict[k] += 1
            state_by_verdict_c[k] += it["amount"] or 0
            if S_scope[fid]:
                state_by_verdict_s[k] += 1
                state_by_verdict_sc[k] += it["amount"] or 0
        rows.append((fid, it["sig"], it["kind"], it["status"], d.get("stale") or 0, ws, d.get("follow_up_on") or "",
                     basis_kind(d.get("state_basis")), d.get("source") or "", d.get("ema_outcome") or "",
                     1 if listed[fid] else 0, 1 if S_scope[fid] else 0, it["amount"] if it["amount"] is not None else "",
                     it["recovered"] if it["recovered"] is not None else "", vs["verdict"], vs["sub"], vw["verdict"],
                     vw["sub"], vs["keeper"], (p1 or {}).get("verdict", "NEW"), (p1 or {}).get("sub", ""),
                     (p1 or {}).get("status", ""), (p1 or {}).get("item", ""), d.get("appeal_block") or "",
                     json.dumps(["|".join(k) for k, n in S_parts[fid]]), d.get("auto_note_kind") or "",
                     d.get("paid_later_source") or "", d.get("payer_group") or "", WHY.get(fid, "")))
    hdr = ("finding_id", "signal", "kind", "status", "stale", "work_state", "follow_up_on", "state_basis_kind", "source",
           "ema_outcome", "listed_w", "in_scope", "item_cents", "recovered_cents", "verdict_s", "sub_s", "verdict_w",
           "sub_w", "keeper_s", "verdict_p1", "sub_p1", "status_p1", "item_cents_p1", "appeal_block", "svc_keys",
           "auto_note_kind", "paid_later_source", "payer_group", "why")
    tpath = os.path.join(OUTDIR, TAG + "_items.tsv")
    with open(tpath, "w") as fh:
        fh.write("\t".join(hdr) + "\n")
        for r in rows:
            fh.write("\t".join(str(x).replace("\t", " ") for x in r) + "\n")
    os.chmod(tpath, 0o600)
    # 5. left on the table, unit by unit, both views and pass 1
    p1lot = {}
    for (bill, code, comp, dos, U, fr, to, st, sub, carc, fam, owed, inside, scope, oi, ci, lot) in p1units:
        if lot:
            for i in range(fr, to + 1):
                p1lot[((bill, code, comp, dos), i)] = (st, sub, owed, scope, oi, ci)
    lotS_set = {(sv.key, i): (sv, u) for sv, i, u in LOT_S}
    lotW_set = {(sv.key, i): (sv, u) for sv, i, u in LOT_W}
    p1lot0 = {(k, i - 1): v for (k, i), v in p1lot.items()}       # 0-based like the auditor's own unit index
    open_on_S = defaultdict(list)
    for it in a.items:
        if S_open[it["fid"]]:
            for key, k in S_parts[it["fid"]]:
                open_on_S[key].append(it["fid"])
    lpath = os.path.join(OUTDIR, TAG + "_lot.tsv")
    keys = set(p1lot0) | set(lotS_set) | set(lotW_set)
    with open(lpath, "w") as fh:
        fh.write("bill\tcode\tcomp\tdos\tunit\tstate\tsub\tcarc\tfam\towed_cents\tin_scope\tlot_p1\tlot_s\tlot_w\t"
                 "p1_open_items\tp1_closed_items\tp2_open_items\tp2_states\n")
        for key in sorted(keys):
            sv = a.svcs.get(key[0])
            if sv is None:
                continue
            u = sv.units[key[1]] if key[1] < len(sv.units) else None
            if u is None:
                continue
            p1 = p1lot0.get(key)
            fids = open_on_S.get(key[0], [])
            states = sorted({(items2.get(f) or {}).get("work_state") or "none" for f in fids})
            fh.write("\t".join(str(x) for x in (*key[0], key[1] + 1, u[0], u[1], u[2], u[3], u[6],
                                                 1 if sv.in_scope else 0, 1 if p1 else 0, 1 if key in lotS_set else 0,
                                                 1 if key in lotW_set else 0, (p1 or ("", "", "", "", "", ""))[4],
                                                 (p1 or ("", "", "", "", "", ""))[5], ",".join(fids), ",".join(states)))
                     + "\n")
    os.chmod(lpath, 0o600)
    # 6. aggregates
    agg = {"same_truth_as_pass1": same_truth, "units_compared": len(mine), "verdicts_S": vS, "subs_S": subS,
           "verdicts_W": vW, "subs_W": subW, "lot_S": lotS, "lot_W": lotW,
           "state_by_verdict_S": {"|".join(k): [state_by_verdict[k], state_by_verdict_c[k], state_by_verdict_s[k],
                                                state_by_verdict_sc[k]] for k in sorted(state_by_verdict)},
           "summary_S": {k: summary[k] for k in ("verdicts", "verdict_subs", "verdicts_by_scope", "left_on_table",
                                                 "closed_wrongly_units")},
           "n_items_p2": len(items2), "n_items_audited": len(a.items), "meta_wiring": meta.get("ema:wiring"),
           "runs": [(r[0], r[3], r[4], r[5]) for r in runs]}
    # pass-1 left-on-table units: where are they in push 2
    tr = Counter()
    trc = Counter()
    for (key, idx1), (st, sub, owed, scope, oi, ci) in p1lot.items():
        k0 = (key, idx1 - 1)
        if not scope:
            continue
        if k0 in lotW_set:
            what = "still_left_W"
        elif k0 in lotS_set:
            what = "held_W_only_off_list"
        else:
            fids = open_on_S.get(key, [])
            if fids:
                what = "held_by_listed_item" if any(listed.get(f) for f in fids) else "held_off_list_decided"
            else:
                what = "not_left_now(decided_or_closed_wrongly)"
        tr[(st, what)] += 1
        trc[(st, what)] += owed
    agg["pass1_lot_in_scope_now"] = {"|".join(k): [tr[k], trc[k]] for k in sorted(tr)}
    # new left-on-table units in view W (in scope), by what holds them in view S
    nw = Counter()
    nwc = Counter()
    for k0, (sv, u) in lotW_set.items():
        if not sv.in_scope:
            continue
        if (k0[0], k0[1] + 1) in p1lot:
            continue
        fids = open_on_S.get(k0[0], [])
        if not fids:
            why = "no_item_in_push2"
        else:
            why = "off_list:" + ",".join(sorted({(items2.get(f) or {}).get("work_state") or "none" for f in fids
                                                if not listed.get(f)}))
        nw[(u[0], why)] += 1
        nwc[(u[0], why)] += u[6]
    agg["new_lot_W_in_scope"] = {"|".join(k): [nw[k], nwc[k]] for k in sorted(nw)}
    with open(os.path.join(OUTDIR, TAG + "_eval.json"), "w") as fh:
        json.dump(agg, fh, indent=1, sort_keys=True, default=str)
    os.chmod(os.path.join(OUTDIR, TAG + "_eval.json"), 0o600)
    print("done in", round(time.time() - t0, 1), "s")
    for name in ("verdicts_S", "verdicts_W", "lot_S", "lot_W", "pass1_lot_in_scope_now", "new_lot_W_in_scope"):
        print("\n==", name)
        for k, v in agg[name].items():
            print("  ", k, v)


if __name__ == "__main__":
    main()
