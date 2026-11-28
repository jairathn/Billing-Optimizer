"""
One definition of each scoreboard figure on every page that shows it (brief rule 3, review d-2). Today's strip showed
"Lost to deadlines, last 30 days" from insights.missed (open items whose window closed) and "paid back in <year>" from
the Paid marks, while Results' scoreboard counts items that expired while in Work now and money that came back after
flagging: numbers that could not both be right. Today now shows this week's lost to deadlines and money back from the
scoreboard itself (/api/brief's score_week is results.scoreboard's current week), with the scoreboard's words. Both
hosts, invented data (the tests/test_ema_pages world).
"""
from __future__ import annotations

import pathlib

import pytest

from tests.conftest import _own_scratch_folder  # noqa: F401
from tests.test_ema_pages import _site, world  # noqa: F401

STATIC = pathlib.Path(__file__).resolve().parent.parent / "remitwatch" / "static"


@pytest.mark.parametrize("mode", ["server", "serverless"])
def test_todays_money_back_and_lost_are_the_scoreboards_own_figures(world, tmp_path, monkeypatch, mode):  # noqa: F811
    site = _site(world, tmp_path, monkeypatch, mode)
    b = site.c.get("/api/brief").json()
    sb = site.c.get("/api/results/scoreboard").json()
    assert sb["weeks"], sb
    cw = sb["weeks"][-1]
    assert b["score_week"] == {"week": cw["week"], "recovered_cents": cw["recovered_cents"], "lost_cents": cw["lost_cents"]}


def test_todays_strip_reads_no_second_count_of_lost_or_money_back():
    js = (STATIC / "today.js").read_text(encoding="utf-8")
    strip = js[js.index("function tdStripHtml("):js.index("function tdBindStrip(")]
    assert "b.missed" not in strip and "b.recovered" not in strip and "score_week" in strip
    assert "windows closed, unworked" in strip
    # Results says the same words under the same figure
    assert "'windows closed, unworked'" in (STATIC / "results.js").read_text(encoding="utf-8")
