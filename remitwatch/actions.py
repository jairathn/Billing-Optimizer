"""
What people record on findings and alerts: one function per action, applied to whichever copy of the store is at hand.

In server mode the API calls these on the store directly. In serverless mode each action is also written to a journal
in the state folder (remitwatch.cloud) so that it survives and reaches the next detection run.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

STATUSES = ("open", "in_progress", "appealed", "resolved", "lost", "dismissed")

# Faxes sent and received (remitwatch.fax), each payer's confirmed fax numbers, and what each note review taught (the
# suggested templates): written through the journal like statuses, so they last on a serverless host too.
FAX_DDL = """
CREATE TABLE IF NOT EXISTS faxes (
    fax_key TEXT PRIMARY KEY, direction TEXT, finding_id TEXT, packet_id TEXT, payer_group TEXT, number TEXT, purpose TEXT,
    level INTEGER, provider TEXT, provider_id TEXT, status TEXT, pages INTEGER, error TEXT, created_at TEXT, updated_at TEXT,
    sent_at TEXT, doc TEXT
);
CREATE INDEX IF NOT EXISTS ix_fax_finding ON faxes(finding_id);
CREATE TABLE IF NOT EXISTS payer_fax (
    payer_group TEXT, purpose TEXT, number TEXT, confirmed_by TEXT, confirmed_at TEXT, previous TEXT, changed_at TEXT,
    auto_ok INTEGER, checked_by TEXT, checked_at TEXT, history TEXT, PRIMARY KEY (payer_group, purpose)
);
CREATE TABLE IF NOT EXISTS note_lessons (
    lesson_key TEXT PRIMARY KEY, created_at TEXT, finding_id TEXT, payer_group TEXT, payer_kind TEXT, signal TEXT, code TEXT,
    npi TEXT, provider TEXT, recommendation TEXT, supports TEXT, amount_cents INTEGER, gaps TEXT, tips TEXT
);
"""
# payer_fax, beyond the number and who saved it: the number it replaced and when (previous, changed_at), whether a person
# has confirmed a changed number for the sends nobody presses a button for (auto_ok, checked_by, checked_at; retries and
# follow-ups wait until then), and every save and confirmation (history)
PAYER_FAX_ADDED = (("previous", "TEXT"), ("changed_at", "TEXT"), ("auto_ok", "INTEGER"), ("checked_by", "TEXT"), ("checked_at", "TEXT"),
                   ("history", "TEXT"))
FAX_COLS = ("direction", "finding_id", "packet_id", "payer_group", "number", "purpose", "level", "provider", "provider_id", "status",
            "pages", "error", "created_at", "updated_at", "sent_at", "doc")
LESSON_COLS = ("created_at", "finding_id", "payer_group", "payer_kind", "signal", "code", "npi", "provider", "recommendation",
               "supports", "amount_cents", "gaps", "tips")


def ensure_fax_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(FAX_DDL)
    have = {r[1] for r in conn.execute("PRAGMA table_info(payer_fax)")}
    for col, typ in PAYER_FAX_ADDED:
        if col not in have:             # a store made before these columns
            conn.execute(f"ALTER TABLE payer_fax ADD COLUMN {col} {typ}")


def heir_of(conn: sqlite3.Connection, finding_id):
    """The item a record written for `finding_id` belongs to now: the item itself while it exists; once detection gave its
    money a new id (detect.carry_to_heirs keeps the pair), its heir. A copy without these tables keeps the id as written."""
    if not finding_id:
        return finding_id
    try:
        if conn.execute("SELECT 1 FROM findings WHERE finding_id=?", (finding_id,)).fetchone():
            return finding_id
        r = conn.execute("SELECT new_id FROM finding_heirs WHERE old_id=?", (finding_id,)).fetchone()
    except sqlite3.OperationalError:            # no findings or finding_heirs table in this copy
        return finding_id
    return r[0] if r else finding_id


def upsert_fax(conn: sqlite3.Connection, rec: dict) -> None:
    """apply_fax for a store whose tables exist: it runs inside a transaction a caller holds (making the tables would end it).
    A fax recorded for an item whose money has a new id since is filed under the heir (heir_of)."""
    if rec.get("finding_id"):
        rec = dict(rec, finding_id=heir_of(conn, rec["finding_id"]))
    key = rec["fax_key"]
    vals = {k: (json.dumps(rec[k], default=str) if k == "doc" and not isinstance(rec[k], (str, type(None))) else rec[k])
            for k in FAX_COLS if k in rec}
    if conn.execute("SELECT 1 FROM faxes WHERE fax_key=?", (key,)).fetchone():
        if vals:
            conn.execute(f"UPDATE faxes SET {', '.join(f'{k}=?' for k in vals)} WHERE fax_key=?", (*vals.values(), key))
    else:
        conn.execute(f"INSERT INTO faxes (fax_key, {', '.join(vals)}) VALUES (?{', ?' * len(vals)})", (key, *vals.values()))


def apply_fax(conn: sqlite3.Connection, rec: dict) -> None:
    """Add a fax, or bring one up to date: only the fields given are changed (a status check sends only the status)."""
    ensure_fax_tables(conn)
    upsert_fax(conn, rec)


def apply_fax_new(conn: sqlite3.Connection, rec: dict) -> None:
    """Add a fax only where this copy has none under its key: a serverless send whose record was never written, found from
    its marker (remitwatch.api._adopt_marker). A copy that has the fax already keeps it as it is."""
    ensure_fax_tables(conn)
    if not conn.execute("SELECT 1 FROM faxes WHERE fax_key=?", (rec["fax_key"],)).fetchone():
        upsert_fax(conn, rec)


def _history(raw: str | None, entry: dict) -> str:
    try:
        hist = json.loads(raw or "[]")
    except ValueError:
        hist = []
    hist = [h for h in (hist if isinstance(hist, list) else []) if isinstance(h, dict)]
    if entry not in hist:
        hist.append(entry)
    return json.dumps(sorted(hist, key=lambda h: str(h.get("at") or ""))[-40:])


def apply_payer_fax(conn: sqlite3.Connection, a: dict) -> None:
    """A payer's fax number for one purpose ('appeal', 'level_2'), confirmed by a person; an empty number removes it (the
    row stays, with no number, so the removal is dated like any change). The newest save wins whatever order the saves
    arrive in: an instance of a serverless host can apply an older save after its own. A number that replaces another is
    not used by a send nobody pressed a button for (a retry, a follow-up) until a person confirms it (apply_payer_fax_check).
    Every save is kept in the row's history: the number, who, when."""
    ensure_fax_tables(conn)
    pg, purpose = a["payer_group"], a.get("purpose") or "appeal"
    num, who, at = a.get("number") or "", a.get("actor") or "", a.get("at") or _now()
    r = conn.execute("SELECT number, confirmed_at, previous, changed_at, auto_ok, history FROM payer_fax WHERE payer_group=? AND purpose=?",
                     (pg, purpose)).fetchone()
    hist = _history(r[5] if r else None, {"number": num, "by": who, "at": at})
    cur = (r[0] or "") if r else ""
    if r and ((r[1] or "") > at or ((r[1] or "") == at and cur == num)):
        # an older save than the one in place (or this very save, applied again): it changes nothing but the history
        conn.execute("UPDATE payer_fax SET history=? WHERE payer_group=? AND purpose=?", (hist, pg, purpose))
        return
    if r and cur == num:
        # the same number saved again: who saved it last, nothing else
        conn.execute("UPDATE payer_fax SET confirmed_by=?, confirmed_at=?, history=? WHERE payer_group=? AND purpose=?", (who, at, hist, pg, purpose))
        return
    before = cur or ((r[2] or "") if r else "")         # the number in use before this save (a removal keeps it as previous)
    changed = bool(before) and num != before
    if changed or not num:
        previous, changed_at = before, at
    else:                                                 # the first number, or the one removed saved again
        previous, changed_at = "", (r[3] if r else None)
    auto_ok = 0 if (changed and num) else 1
    conn.execute("""INSERT OR REPLACE INTO payer_fax (payer_group, purpose, number, confirmed_by, confirmed_at, previous, changed_at, auto_ok,
                    checked_by, checked_at, history) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 (pg, purpose, num, who, at, previous, changed_at, auto_ok, None if auto_ok == 0 else who, None if auto_ok == 0 else at, hist))


def apply_payer_fax_check(conn: sqlite3.Connection, a: dict) -> None:
    """A person confirms a payer's changed fax number, as shown to them: from then on retries and follow-ups use it. A
    confirmation of a number that is no longer the one saved changes nothing."""
    ensure_fax_tables(conn)
    pg, purpose = a["payer_group"], a.get("purpose") or "appeal"
    num, who, at = a.get("number") or "", a.get("actor") or "", a.get("at") or _now()
    r = conn.execute("SELECT number, confirmed_at, history FROM payer_fax WHERE payer_group=? AND purpose=?", (pg, purpose)).fetchone()
    if not r or not num or (r[0] or "") != num or (r[1] or "") > at:
        return
    conn.execute("UPDATE payer_fax SET auto_ok=1, checked_by=?, checked_at=?, history=? WHERE payer_group=? AND purpose=?",
                 (who, at, _history(r[2], {"number": num, "by": who, "at": at, "confirmed": True}), pg, purpose))


def apply_event(conn: sqlite3.Connection, a: dict) -> bool:
    """A line in an item's history that changes no status: a follow-up faxed, the payer's answer received
    (remitwatch.fax). False when the item is not in this copy, so the journal keeps the entry and tries it again. A line for
    an item whose money has a new id since goes into the heir's history (heir_of)."""
    fid = heir_of(conn, a["finding_id"])
    r = conn.execute("SELECT status FROM findings WHERE finding_id=?", (fid,)).fetchone()
    if not r:
        return False
    at, kind, note = a.get("at") or _now(), a.get("event") or "fax", a.get("note") or ""
    if not conn.execute("SELECT 1 FROM finding_events WHERE finding_id=? AND at=? AND kind=? AND COALESCE(note,'')=?",
                        (fid, at, kind, note)).fetchone():
        conn.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note) VALUES (?,?,?,?,?,?,?,?)",
                     (fid, at, kind, r[0], r[0], a.get("amount_cents"), a.get("actor") or "", note))
    return True


# Hide rules: a reason a payer is known not to pay (a visit with modifier 57 at a payer that never pays it) leaves the lists.
# Its open items go to Let go with the rule's note, and so do new ones after each analysis; an item a person has worked or
# reopened is left alone. Removing the rule reopens what it hid. A removed rule stays as a row with the date it was removed
# (removed_at), so that journal entries applied again later (a serverless instance loading a new copy, a cycle whose
# journal delete failed), in any order, change nothing: an add or a remove older than the rule's last change is skipped.
HIDE_DDL = """
CREATE TABLE IF NOT EXISTS hide_rules (
    payer_group TEXT, signal TEXT, actor TEXT, at TEXT, note TEXT, removed_at TEXT, PRIMARY KEY (payer_group, signal)
);
"""
HIDE_ACTOR = "hide rule"
# Never hidden wholesale for a payer: a claim no payer answered (or one rejected before reaching the payer) is not a
# reason the payer won't pay. It is money nobody has asked for yet, so each one is let go only by a person, one at a time.
NEVER_HIDDEN = frozenset({"NO_ANSWER", "REJECTED_PRE_PAYER"})
# What decided an item's status last, for SQL over `findings f`: a line in its history that changes no status (a payer's
# fax read, a follow-up faxed) does not count, so an item the payer wrote about is still the rule's to bring back.
DECIDING_KINDS = "('manual','hidden','unhidden','paid_on_later_remit','reopened')"
LAST_DECISION = (f"(SELECT e.kind FROM finding_events e WHERE e.finding_id=f.finding_id AND e.kind IN {DECIDING_KINDS} "
                 "ORDER BY e.at DESC, e.id DESC LIMIT 1)")


def ensure_hide_rules(conn: sqlite3.Connection) -> None:
    conn.executescript(HIDE_DDL)
    if "removed_at" not in {r[1] for r in conn.execute("PRAGMA table_info(hide_rules)")}:
        conn.execute("ALTER TABLE hide_rules ADD COLUMN removed_at TEXT")     # a store made before removed rules were kept


def rules_in_force(conn: sqlite3.Connection) -> str:
    """SQL that keeps the rules in force (not removed): every rule, in a store made before removed rules were kept."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(hide_rules)")}
    return "removed_at IS NULL" if "removed_at" in have else "1=1"


def _hide_matching(conn: sqlite3.Connection, payer_group: str, signal: str, note: str, at: str) -> int:
    """Move the open items of one rule to Let go, leaving out any a person has changed (their decision stands) and any
    whose status was decided after `at` (an entry applied again must not undo what came after it)."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "findings" not in have or signal in NEVER_HIDDEN:
        return 0
    touched, params = "", [payer_group, signal]
    if "finding_events" in have:
        touched = f"AND finding_id NOT IN (SELECT finding_id FROM finding_events WHERE kind='manual' OR (kind IN {DECIDING_KINDS} AND at > ?))"
        params.append(at)
    ids = [r[0] for r in conn.execute(f"""SELECT finding_id FROM findings WHERE payer_group=? AND signal=? AND status='open'
                                          AND COALESCE(stale,0)=0 {touched}""", tuple(params))]
    for fid in ids:
        conn.execute("UPDATE findings SET status='dismissed', status_changed_at=? WHERE finding_id=?", (at, fid))
        if "finding_events" in have:
            conn.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note) "
                         "VALUES (?,?,'hidden','open','dismissed',NULL,?,?)", (fid, at, HIDE_ACTOR, note))
    return len(ids)


def apply_hide_rule(conn: sqlite3.Connection, a: dict) -> None:
    """Add a rule and hide what it covers, or remove one and reopen what it hid (and nothing a person has changed since).
    An entry older than the rule's last change (added or removed) is skipped, so applying the journal again is harmless."""
    ensure_hide_rules(conn)
    pg, sig, at = a["payer_group"], a["signal"], a.get("at") or _now()
    row = conn.execute("SELECT at, removed_at FROM hide_rules WHERE payer_group=? AND signal=?", (pg, sig)).fetchone()
    if row and max(row[0] or "", row[1] or "") > at:
        return
    if a.get("op") == "remove":
        if row:
            conn.execute("UPDATE hide_rules SET removed_at=? WHERE payer_group=? AND signal=?", (at, pg, sig))
        else:           # a copy that never had the rule keeps the date too, so an older add applied after this one is skipped
            conn.execute("INSERT INTO hide_rules (payer_group, signal, actor, at, note, removed_at) VALUES (?,?,?,NULL,'',?)",
                         (pg, sig, a.get("actor") or "", at))
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "finding_events" not in have or "findings" not in have:
            return
        for (fid,) in conn.execute(f"""SELECT f.finding_id FROM findings f WHERE f.payer_group=? AND f.signal=? AND f.status='dismissed'
                                         AND {LAST_DECISION}='hidden'""", (pg, sig)).fetchall():
            conn.execute("UPDATE findings SET status='open', status_changed_at=? WHERE finding_id=?", (at, fid))
            conn.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note) "
                         "VALUES (?,?,'unhidden','dismissed','open',NULL,?,?)", (fid, at, a.get("actor") or "", "rule removed"))
        return
    note = (a.get("note") or "").strip()[:300] or f"Hidden: {pg} is not expected to pay these"
    conn.execute("INSERT OR REPLACE INTO hide_rules (payer_group, signal, actor, at, note, removed_at) VALUES (?,?,?,?,?,NULL)",
                 (pg, sig, a.get("actor") or "", at, note))
    _hide_matching(conn, pg, sig, note, at)


def apply_hide_rules(conn: sqlite3.Connection) -> int:
    """After an analysis: every rule in force hides the new items it covers."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "hide_rules" not in have:
        return 0
    at = _now()
    return sum(_hide_matching(conn, pg, sig, note, at) for pg, sig, note in
               conn.execute(f"SELECT payer_group, signal, note FROM hide_rules WHERE {rules_in_force(conn)}").fetchall())


def apply_lesson(conn: sqlite3.Connection, rec: dict) -> None:
    ensure_fax_tables(conn)
    vals = {k: (json.dumps(rec.get(k)) if k in ("gaps", "tips") else rec.get(k)) for k in LESSON_COLS}
    conn.execute(f"INSERT OR IGNORE INTO note_lessons (lesson_key, {', '.join(vals)}) VALUES (?{', ?' * len(vals)})",
                 (rec["lesson_key"], *vals.values()))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply_status(conn: sqlite3.Connection, finding_id: str, status: str, notes: str | None = None, recovered_cents: int | None = None,
                 actor: str = "", at: str | None = None, seen: str | None = None) -> bool:
    """Set a finding's status and log the change. Returns False when the finding does not exist. `seen` is the status the
    person was looking at when they made the change (recorded in the journal on a serverless host): it is logged as the
    status they changed from, because the copy this is replayed on may have moved since."""
    if status not in STATUSES:
        raise ValueError("bad status")
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT status, shortfall_cents, appealed_at FROM findings WHERE finding_id=?", (finding_id,)).fetchone()
    if not r:
        return False
    now = at or _now()
    if at and conn.execute("SELECT 1 FROM finding_events WHERE finding_id=? AND at=? AND kind='manual' AND to_status=? AND COALESCE(actor,'')=?",
                           (finding_id, at, status, actor or "")).fetchone():
        return True     # a journal entry being replayed: it is already in this copy
    recovered = (recovered_cents if recovered_cents is not None else r["shortfall_cents"]) if status == "resolved" else None
    # A person's status replaces whatever the automatic resolution recorded: its note no longer describes the finding, and
    # the next analysis does not close it again on the same grounds (remitwatch.detect save()).
    conn.execute("""UPDATE findings SET status=?, notes=COALESCE(?, notes), status_changed_at=?, recovered_cents=?, recovered_date=?,
                    appealed_at=CASE WHEN ?='appealed' AND appealed_at IS NULL THEN ? ELSE appealed_at END, auto_note=NULL, prev_status=NULL
                    WHERE finding_id=?""",
                 (status, notes, now, recovered, now[:10] if status == "resolved" else None, status, now, finding_id))
    conn.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, amount_cents, actor, note) VALUES (?,?,?,?,?,?,?,?)",
                 (finding_id, now, "manual", seen or r["status"], status, recovered if status == "resolved" else r["shortfall_cents"], actor, notes))
    return True


def apply_alert_ack(conn: sqlite3.Connection, alert_id: str, at: str | None = None) -> None:
    conn.execute("UPDATE alerts SET status='acknowledged', acknowledged_at=? WHERE alert_id=? AND status='new'", (at or _now(), alert_id))


def apply(conn: sqlite3.Connection, action: dict) -> bool:
    """Apply one journal entry. Unknown kinds are ignored so an older deployment can read a newer journal."""
    kind = action.get("kind")
    if kind == "status":
        return apply_status(conn, action["finding_id"], action["status"], action.get("notes"), action.get("recovered_cents"),
                            action.get("actor") or "", action.get("at"), action.get("seen"))
    if kind == "alert_ack":
        apply_alert_ack(conn, action["alert_id"], action.get("at"))
        return True
    if kind == "fax":
        apply_fax(conn, action["record"])
        return True
    if kind == "fax_new":
        apply_fax_new(conn, action["record"])
        return True
    if kind == "payer_fax":
        apply_payer_fax(conn, action)
        return True
    if kind == "payer_fax_check":
        apply_payer_fax_check(conn, action)
        return True
    if kind == "lesson":
        apply_lesson(conn, action["record"])
        return True
    if kind == "event":
        return apply_event(conn, action)
    if kind == "hide_rule":
        apply_hide_rule(conn, action)
        return True
    if kind == "trizetto_hold":
        # the /setup test on a serverless host: a sign-in that worked ends the hold, a refusal starts one (remitwatch.trizetto)
        from . import trizetto
        if action.get("op") == "clear":
            # only a hold older than the test that passed: a refusal after it stands. Applied once, whether or not a hold was there
            row = conn.execute("SELECT value FROM meta WHERE key=?", (trizetto.REFUSED_META,)).fetchone() if \
                conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone() else None
            try:
                held_at = json.loads(row[0]).get("at") if row else None
            except (TypeError, ValueError):
                held_at = ""
            if row and (held_at or "") <= (action.get("at") or "~"):
                trizetto.forget_refusal(conn)
            return True
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        # the kind of refusal goes with it, so Today says "refused the password" only when TriZetto refused a password
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (trizetto.REFUSED_META, json.dumps({"who": action.get("who") or "", "at": action.get("at") or _now(),
                                                         "detail": str(action.get("detail") or "")[:300],
                                                         "kind": str(action.get("refused_kind") or "")})))
        return True
    return False
