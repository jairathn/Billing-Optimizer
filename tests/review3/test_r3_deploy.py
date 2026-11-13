"""What the first run of the new rules does to whole-claim denials people worked under the live release (3f8855b), where
every denied submission had its own finding. The first half runs that release's code in a subprocess (extracted with git
archive into a temporary folder; skipped where git does not have the commit, as in a shallow CI checkout); the second
half runs the current code. Invented data only."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r3helpers import add, base, clm, findings, show, sv  # noqa: E402

from remitwatch.detect import Detector  # noqa: E402

import pytest  # noqa: E402

OLD = os.environ.get("RW_OLD_TREE", "")      # a checkout of the live release (made below when git has it)
HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = "3f8855b"


@pytest.fixture(scope="module", autouse=True)
def _old_tree(tmp_path_factory):
    global OLD
    if OLD:
        return
    import shutil
    repo = os.path.dirname(os.path.dirname(HERE))
    if not shutil.which("git") or subprocess.run(["git", "cat-file", "-e", f"{LIVE}^{{commit}}"], cwd=repo, capture_output=True).returncode:
        pytest.skip(f"needs the live release's commit ({LIVE}) in git history")
    d = tmp_path_factory.mktemp("live")
    if subprocess.run(f"git archive {LIVE} | tar -x -C '{d}'", shell=True, cwd=repo, capture_output=True).returncode:
        pytest.skip("could not extract the live release")
    OLD = str(d)


def denied(pcn, carc, payer_ctl):
    return clm(pcn, "4", "223.65", "0.00", sv("99214", "223.65", "0.00", 1, f"CAS*CO*{carc}*223.65~"), payer_ctl=payer_ctl)


def old_run_and_work(db):
    code = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {OLD!r})
        from remitwatch.store import Store
        from remitwatch.detect import Detector
        from remitwatch.actions import apply_status
        import remitwatch.detect as d
        assert d.__file__.startswith({OLD!r}), d.__file__
        s = Store({db!r})
        Detector(s, run_id="old").run()
        ids = {{r[1]: r[0] for r in s.conn.execute("SELECT finding_id, patient_control_number FROM findings WHERE signal='CLAIM_DENIED_UNPAID'")}}
        print(sorted(ids))
        apply_status(s.conn, ids["CB0000300C017"], "appealed", "Appeal faxed to Anthem", actor="biller", at="2026-05-01T15:00:00+00:00")
        apply_status(s.conn, ids["CB0000300C024"], "in_progress", "Humana wants the referral; requested", actor="biller", at="2026-06-01T15:00:00+00:00")
        s.conn.commit()
        s.close()
    """)
    out = subprocess.run([sys.executable, "-c", code], cwd=OLD, capture_output=True, text=True)
    print(out.stdout, out.stderr[-2000:])
    assert out.returncode == 0


def test_after_deploy_both_worked_denials_of_one_bill_stay_in_the_work_lists(tmp_path):
    """Under the live release: Anthem denied C017 (CO-197, 2026-04-15) and the biller appealed it; Humana denied the rebill C024
    (CO-16, 2026-05-20) and the biller is working it. The first run of the new rules must keep both, live."""
    base(tmp_path).close()
    add(tmp_path, [denied("CB0000300C017", "197", "ANT300")], "20260415").close()
    add(tmp_path, [denied("CB0000300C024", "16", "HUM300")], "20260520", payer="HUMANA", payer_id="HUMNA").close()
    db = str(tmp_path / "s.sqlite")
    old_run_and_work(db)
    from remitwatch.store import Store
    store = Store(db)
    d = Detector(store, run_id="new")
    d.now = "2026-06-02T09:00:00+00:00"
    d.run()
    rows = findings(store, "signal='CLAIM_DENIED_UNPAID'")
    show(rows, ("patient_control_number", "payer_name", "status", "stale", "notes"))
    stale = [r for r in rows if r["stale"]]
    assert not stale, f"worked findings dropped out of every total and list: {[(r['patient_control_number'], r['status']) for r in stale]}"
    store.close()
