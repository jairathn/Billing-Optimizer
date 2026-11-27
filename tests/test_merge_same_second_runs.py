"""On Vercel the Data tab's "Accept" route refuses at once an acceptance whose run the dashboard's copy has moved past (the
checks ran again after the page was drawn), as the server does; a copy that is behind the page journals it for the next
run to fold. Run ids carry their start to the second, and two runs can start in the same second (a short run, then a
press): such a later run must count as having moved past the page. Before the fix the route compared the seconds with
'>', so a press started in the same second as the page's run was taken as the same moment and the acceptance was
journaled for a run the owner had not seen (tests/test_hook_review.py's acceptance test failed whenever the two runs
shared a second). Invented run ids only."""
from __future__ import annotations

import pytest

from remitwatch import api_ops


@pytest.mark.parametrize("latest, page, overtaken", [
    ("run-20261127T012230Z-bbbb", "run-20261127T012230Z-aaaa", True),     # another run, the same second: moved past
    ("run-20261127T012231Z-bbbb", "run-20261127T012230Z-aaaa", True),     # a later second
    ("run-20261127T012229Z-bbbb", "run-20261127T012230Z-aaaa", False),    # the copy is behind the page: journaled
    ("run-20261127T012230Z-aaaa", "run-20261127T012230Z-aaaa", False),    # the run the page showed
    ("run-20261231T235959Z-bbbb", "run-20270101T000000Z-aaaa", False),    # year end: the copy is the older run
    (None, "run-20261127T012230Z-aaaa", False),                           # no checks on the copy yet
    ("not a run id", "run-20261127T012230Z-aaaa", False),
])
def test_a_run_that_started_in_the_same_second_has_moved_past_the_page(latest, page, overtaken):
    assert api_ops.checks_overtook(latest, page) is overtaken
