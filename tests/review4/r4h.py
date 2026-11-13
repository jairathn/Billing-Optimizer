"""Helpers for the fourth review (latest round of fixes). Invented data only: names, member numbers, claim ids, payer
ids and amounts are made up. Run from the repository root."""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
# RW_LIVE=1 runs the same story on the live release's code (3f8855b, extracted with git archive into live_code/), with
# the release's test builders
if os.environ.get("RW_LIVE") and os.environ.get("RW_LIVE_TREE"):
    sys.path.insert(0, os.environ["RW_LIVE_TREE"])

from remitwatch.actions import apply_status  # noqa: E402,F401
from remitwatch.detect import Detector  # noqa: E402
from tests.review3.r3helpers import (WHO, add, base, by_key, clm, ev, findings, show, sv, visit_denied_line,  # noqa: E402,F401
                                     visit_paid, visit_reversed)
from tests.test_features import NPI, build_store, clean_visits  # noqa: E402,F401


def run(store, run_id, now=None):
    d = Detector(store, run_id=run_id)
    if now:
        d.now = now
    d.run()
    return d


def events(store, fid):
    return [tuple(r) for r in store.query("SELECT kind, from_status, to_status, at, note FROM finding_events WHERE finding_id=? ORDER BY id", (fid,))]


def rows(store, where="1=1"):
    out = []
    for r in store.query(f"SELECT * FROM findings WHERE {where}"):
        d = dict(r)
        for k in ("hint", "auto_note", "stale", "prev_status"):
            d.setdefault(k, None)
        if d.get("evidence"):
            d["evidence"] = json.loads(d["evidence"])
        out.append(d)
    return out


def brief(rs, cols=("patient_control_number", "procedure_code", "signal", "payer_name", "status", "stale", "shortfall_cents", "recovered_cents", "auto_note", "hint")):
    for r in rs:
        print({c: r.get(c) for c in cols})
