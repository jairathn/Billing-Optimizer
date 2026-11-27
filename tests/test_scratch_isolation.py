"""Every test writes the serverless scratch files (runtime.tmp_dir: the serving copy being built, a batch, a Production
file waiting) in a folder of its own. Without one, every pytest process on the machine shares /tmp/remitwatch: the suite's
parallel shards, or another checkout's run, can replace a serving copy another test is about to read, and a test of the
Data tab's acceptance or of a handed-on analysis fails now and then for no fault of the code (seen at the hook and
decisions merges, 2026-11-27). Production is not affected: the cycle builds its copy under the run's lock."""
from __future__ import annotations

import os
import tempfile


def test_a_tests_scratch_folder_is_its_own_not_the_machines_shared_one():
    from remitwatch import runtime
    shared = os.path.join(tempfile.gettempdir(), "remitwatch")
    mine = runtime.tmp_dir()
    assert os.path.realpath(mine) != os.path.realpath(shared), mine
    with open(os.path.join(mine, "left-by-the-first-test"), "w", encoding="utf-8") as fh:
        fh.write("x")


def test_the_next_test_finds_its_folder_empty():
    """Whatever ran before (the test above, another file's cycle, another process) left nothing in this test's folder."""
    from remitwatch import runtime
    assert os.listdir(runtime.tmp_dir()) == []
