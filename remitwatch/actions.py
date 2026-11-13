"""
What people record on findings and alerts: one function per action, applied to whichever copy of the store is at hand.

In server mode the API calls these on the store directly. In serverless mode each action is also written to a journal
in the state folder (remitwatch.cloud) so that it survives and reaches the next detection run.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

STATUSES = ("open", "in_progress", "appealed", "resolved", "lost", "dismissed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply_status(conn: sqlite3.Connection, finding_id: str, status: str, notes: str | None = None, recovered_cents: int | None = None,
                 actor: str = "", at: str | None = None) -> bool:
    """Set a finding's status and log the change. Returns False when the finding does not exist."""
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
                 (finding_id, now, "manual", r["status"], status, recovered if status == "resolved" else r["shortfall_cents"], actor, notes))
    return True


def apply_alert_ack(conn: sqlite3.Connection, alert_id: str, at: str | None = None) -> None:
    conn.execute("UPDATE alerts SET status='acknowledged', acknowledged_at=? WHERE alert_id=? AND status='new'", (at or _now(), alert_id))


def apply(conn: sqlite3.Connection, action: dict) -> bool:
    """Apply one journal entry. Unknown kinds are ignored so an older deployment can read a newer journal."""
    kind = action.get("kind")
    if kind == "status":
        return apply_status(conn, action["finding_id"], action["status"], action.get("notes"), action.get("recovered_cents"),
                            action.get("actor") or "", action.get("at"))
    if kind == "alert_ack":
        apply_alert_ack(conn, action["alert_id"], action.get("at"))
        return True
    return False
