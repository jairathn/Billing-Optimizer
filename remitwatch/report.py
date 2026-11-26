"""
Weekly summary: what is new, what is due, what to do first.

Writes a Markdown report to data/reports/ and optionally emails it (SMTP).
"""
from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from .store import Store


def money(c) -> str:
    return f"${(c or 0) / 100:,.0f}"


def build_report(store: Store, run_id: str | None = None, sync_stats: dict | None = None, ingest_stats: dict | None = None,
                 detail: bool = False) -> str:
    """Summary report. The emailed version (detail=False) carries payer names, counts and dollars only; the saved file adds claim-level rows."""
    q = store.query
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last_run = q("SELECT * FROM detection_runs ORDER BY started_at DESC LIMIT 1")
    run_id = run_id or (last_run[0]["run_id"] if last_run else None)
    lines = [f"# RemitWatch weekly report ({now})", ""]
    meta = q("SELECT COUNT(*) n, MAX(payment_date) last_payment FROM transactions WHERE superseded=0")[0]
    lines.append(f"Remits in store: {meta['n']:,} (latest payment date {meta['last_payment']}).")
    if sync_stats and sync_stats.get("skipped"):
        lines.append(f"Dropbox sync: {sync_stats['skipped']}.")
    elif sync_stats:
        lines.append(f"Dropbox sync: {sync_stats.get('downloaded', 0)} new files downloaded, {sync_stats.get('unchanged', 0)} unchanged, {sync_stats.get('errors', 0)} errors ({sync_stats.get('mode')} listing).")
    if ingest_stats:
        lines.append(f"Ingested: {ingest_stats.get('remits', 0)} remits, {ingest_stats.get('ema', 0)} EMA exports, {ingest_stats.get('claims_837', 0)} claim files.")
    lines.append("")
    try:
        alerts = q("SELECT severity, title FROM alerts WHERE status IN ('new','acknowledged') ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, cents_recent DESC LIMIT 25")
    except Exception:
        alerts = []
    lines.append("## Alerts")
    lines += [f"- [{a['severity']}] {a['title']}" for a in alerts] or ["No active alerts."]
    lines.append("")
    prog = q("""SELECT status, COUNT(*) n, SUM(shortfall_cents) cents, SUM(recovered_cents) rec FROM findings
                WHERE category IN ('downcoding','bundling','denial','underpayment') AND COALESCE(stale,0)=0 GROUP BY 1""")
    by = {r["status"]: r for r in prog}
    lines.append("## Scoreboard")
    lines.append("| status | findings | dollars |")
    lines.append("|---|---:|---:|")
    for st in ("open", "in_progress", "appealed", "resolved", "lost", "dismissed"):
        if st in by:
            lines.append(f"| {st} | {by[st]['n']:,} | {money(by[st]['rec'] if st == 'resolved' else by[st]['cents'])} |")
    lines.append("")
    # stale rows (worked findings the rules no longer produce, kept for their notes) are left out of every count here
    new = q("""SELECT category, signal, COUNT(*) n, SUM(shortfall_cents) cents FROM findings
               WHERE first_seen = (SELECT MAX(first_seen) FROM findings WHERE COALESCE(stale,0)=0) AND COALESCE(stale,0)=0
                 AND category IN ('downcoding','bundling','denial','underpayment','fees')
               GROUP BY 1,2 ORDER BY cents DESC""")
    lines.append("## New this run")
    if new:
        lines.append("| category | signal | findings | dollars |")
        lines.append("|---|---|---:|---:|")
        for r in new:
            lines.append(f"| {r['category']} | {r['signal']} | {r['n']} | {money(r['cents'])} |")
    else:
        lines.append("No new findings.")
    lines.append("")
    # the practice's day (runtime.practice_today): a window is open until midnight where the practice is
    from . import runtime
    today, horizon = runtime.practice_today(), runtime.practice_today(45)
    due = q("""SELECT signal, payer_group, patient_control_number, service_date, procedure_code, shortfall_cents, appeal_deadline
               FROM findings WHERE status='open' AND COALESCE(stale,0)=0 AND actionable IN ('appeal','correct_and_resubmit') AND appeal_deadline BETWEEN ? AND ?
               ORDER BY appeal_deadline, shortfall_cents DESC LIMIT 40""", (today, horizon))
    # a payer's routine lower rate: one row with the claims whose own windows close in the period
    from .detect import pattern_windows
    soon: dict[str, dict] = {}
    for fid, grp, code, d, n, cents in pattern_windows(store.conn):
        if d and today <= d <= horizon:
            r = soon.setdefault(fid, {"signal": "SECOND_RATE", "payer_group": grp, "patient_control_number": "", "service_date": "",
                                      "procedure_code": code, "shortfall_cents": 0, "appeal_deadline": d, "n": 0})
            r["shortfall_cents"] += cents
            r["n"] += n
            r["appeal_deadline"] = min(r["appeal_deadline"], d)
    for r in soon.values():
        r["patient_control_number"] = f"{r['n']} claim{'s' if r['n'] != 1 else ''} at the payer's routine lower rate"
    due_n = q("""SELECT COUNT(*) n FROM findings WHERE status='open' AND COALESCE(stale,0)=0 AND actionable IN ('appeal','correct_and_resubmit')
                  AND appeal_deadline BETWEEN ? AND ?""", (today, horizon))[0]["n"] + len(soon)
    due = sorted([dict(r) for r in due] + list(soon.values()), key=lambda r: (r["appeal_deadline"], -(r["shortfall_cents"] or 0)))
    lines.append("## Appeals due in the next 45 days")
    if due and not detail:
        lines.append(f"{due_n} findings are due; open the Appeals queue in the dashboard for the list.")
    elif due:
        lines.append("| deadline | payer | claim | DOS | code | signal | dollars |")
        lines.append("|---|---|---|---|---|---|---:|")
        for r in due:
            lines.append(f"| {r['appeal_deadline']} | {r['payer_group']} | {r['patient_control_number']} | {r['service_date']} | {r['procedure_code'] or ''} | {r['signal']} | {money(r['shortfall_cents'])} |")
    else:
        lines.append("Nothing due.")
    lines.append("")
    top = [dict(r) for r in q("""SELECT payer_group, COUNT(*) n, SUM(shortfall_cents) cents FROM findings
               WHERE status='open' AND COALESCE(stale,0)=0 AND category IN ('downcoding','bundling','denial','underpayment') AND appeal_deadline >= ?
                 AND signal != 'SECOND_RATE' GROUP BY 1""", (today,))]
    # a payer's routine lower rate: the claims whose own windows are still open
    by_payer = {r["payer_group"]: r for r in top}
    counted: set = set()
    for fid, grp, _code, d, _n, cents in pattern_windows(store.conn):
        if d and d >= today:
            r = by_payer.setdefault(grp, {"payer_group": grp, "n": 0, "cents": 0})
            r["cents"] = (r["cents"] or 0) + cents
            if fid not in counted:
                counted.add(fid)
                r["n"] += 1
    top = sorted(by_payer.values(), key=lambda r: -(r["cents"] or 0))[:10]
    lines.append("## Open, still appealable, by payer")
    lines.append("| payer | findings | dollars |")
    lines.append("|---|---:|---:|")
    for r in top:
        lines.append(f"| {r['payer_group']} | {r['n']} | {money(r['cents'])} |")
    lines.append("")
    fix = q("""SELECT signal, COUNT(*) n, SUM(shortfall_cents) cents FROM findings WHERE status='open' AND COALESCE(stale,0)=0 AND actionable='correct_and_resubmit' AND appeal_deadline >= ? GROUP BY 1 ORDER BY cents DESC""", (today,))
    lines.append("## Correctable billing errors (resubmit, no appeal needed)")
    for r in fix:
        lines.append(f"- {r['signal']}: {r['n']} lines, {money(r['cents'])}")
    lines.append("")
    lines += ema_section(store)
    info = q("SELECT title FROM findings WHERE category='info' AND COALESCE(stale,0)=0 ORDER BY signal, year")
    if info:
        lines.append("## Context")
        for r in info:
            lines.append(f"- {r['title']}")
    return "\n".join(lines)


def _meta(store: Store, key: str):
    try:
        r = store.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r and r[0] else None
    except (ValueError, TypeError, Exception):     # noqa: BLE001  (a store without the table, or an unreadable value)
        return None


def ema_section(store: Store) -> list[str]:
    """What EMA's A/R and Production reports add (counts and dollars only): the work nobody has touched, what EMA showed
    paid this week (items closed), claims that never left EMA, payments EMA never posted, automatic write-offs still
    appealable, and the A/R accounted for. Empty until the practice sends the reports."""
    wiring = _meta(store, "ema:wiring") or {}
    if not wiring.get("on"):
        return []
    q = store.query
    cols = {r["name"] for r in q("PRAGMA table_info(findings)")}
    if "ema_outcome" not in cols:
        return []
    out = ["## From EMA's reports"]
    unworked = _meta(store, "ema:unworked") or {}
    if unworked:
        out.append(f"- Untouched work inside its window: {unworked.get('n', 0):,} items, {money(unworked.get('cents'))} "
                   f"(EMA's postings through {unworked.get('data_through') or 'the newest data'})")
    from . import runtime
    # this week: closed since the start of the practice's day a week ago (a stored time, compared as the store writes them;
    # never SQL's datetime('now'), which is UTC and written with a space that a stored time passes all its day)
    week = q("""SELECT COUNT(*) n, COALESCE(SUM(recovered_cents),0) cents FROM findings WHERE status='resolved' AND COALESCE(stale,0)=0
                AND auto_note IS NOT NULL AND (auto_note LIKE 'EMA shows%' OR auto_note LIKE 'Rebilled to%')
                AND status_changed_at >= ?""", (runtime.practice_day_start(runtime.practice_today(-7)),))[0]
    out.append(f"- Closed this week because EMA shows them paid: {week['n']:,} items, {money(week['cents'])}")
    for sig, words in (("NOT_SENT", "Claims that never left EMA"), ("REMIT_NOT_POSTED", "Payments on an 835 that EMA never posted")):
        r = q("""SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings WHERE signal=? AND COALESCE(stale,0)=0
                 AND status IN ('open','in_progress')""", (sig,))[0]
        out.append(f"- {words}: {r['n']:,}, {money(r['cents'])}")
    auto = q("""SELECT COUNT(*) n, COALESCE(SUM(shortfall_cents),0) cents FROM findings WHERE ema_outcome='auto_written_off'
                AND COALESCE(stale,0)=0 AND status IN ('open','in_progress') AND appeal_deadline >= ?""",
             (runtime.practice_today(),))[0]           # the practice's day, never SQL's date('now') (UTC)
    out.append(f"- Written off automatically by EMA and still appealable: {auto['n']:,} items, {money(auto['cents'])}")
    acc = _meta(store, "ema:ar_accounted") or {}
    if acc.get("buckets"):
        labels = acc.get("labels") or {}
        total = acc.get("total") or {}
        out.append(f"- The insurance A/R of {acc.get('as_of') or 'the newest pull'}: {total.get('n', 0):,} rows, {money(total.get('cents'))}"
                   + ("" if acc.get("adds_up") else " (the buckets do not add up: tell the maintainer)"))
        for key, b in acc["buckets"].items():
            if b.get("n"):
                out.append(f"  - {labels.get(key, key)}: {b['n']:,}, {money(b.get('cents'))}")
    out.append("")
    return out


def save_report(text: str, out_dir: str = "data/reports") -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"remitwatch-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.md")
    with open(path, "w") as fh:
        fh.write(text)
    return path


def email_report(text: str, subject: str = "RemitWatch weekly report") -> bool:
    host = os.environ.get("SMTP_HOST")
    to = os.environ.get("REPORT_TO")
    if not host or not to:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "remitwatch@localhost"))
    msg["To"] = to
    msg.set_content(text)
    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as s:
        if os.environ.get("SMTP_STARTTLS", "1") == "1":
            s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
        s.send_message(msg)
    return True
