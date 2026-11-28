"""
DESIGN rule 7 (review d-7): the line a biller reads first, an item's verdict and its next step, is 20 words or fewer. The
whole sentence stays in `sentence` and `action` (and the step's text keeps every word in its fold); the headline is its
first sentence, cut at its first clause (a colon or a semicolon) when that sentence alone is longer. Measured over every
open item of the invented tests/test_ema_pages world.
"""
from __future__ import annotations

import re
import sqlite3

from tests.conftest import _own_scratch_folder  # noqa: F401
from tests.test_ema_pages import _site, world  # noqa: F401

LIMIT = 20


def _words(s: str) -> int:
    return len(re.findall(r"[^\s]+", s or ""))


def test_every_verdict_and_next_step_headline_is_twenty_words_or_fewer(world, tmp_path, monkeypatch):  # noqa: F811
    from remitwatch import story
    site = _site(world, tmp_path, monkeypatch, "server")
    conn = sqlite3.connect(site.db)
    conn.row_factory = sqlite3.Row
    try:
        ids = [r[0] for r in conn.execute("SELECT finding_id FROM findings WHERE status IN ('open','in_progress') ORDER BY finding_id")]
        assert ids
        long = []
        for fid in ids:
            out = story.explain(conn, fid)
            for part in ("verdict", "next_step"):
                head = (out.get(part) or {}).get("headline") or ""
                if _words(head) > LIMIT:
                    long.append((part, _words(head), head))
    finally:
        conn.close()
    assert not long, f"{len(long)} of {2 * len(ids)} headlines over {LIMIT} words: {sorted(long, key=lambda x: -x[1])[:8]}"
