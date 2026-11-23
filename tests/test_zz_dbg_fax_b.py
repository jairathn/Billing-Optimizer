from tests.test_fax import *  # noqa
from tests.test_fax import site  # noqa


def test_dbg(site):
    import sqlite3
    c = sqlite3.connect(site.db)
    for r in c.execute("SELECT signal, status, work_state, appeal_block, appeal_deadline, actionable, category, stale, state_basis FROM findings"):
        print("ROW", r)
    assert 0
