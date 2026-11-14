"""/api/brief sorts open work by the kind of work it takes. A whole claim denied (CLAIM_DENIED_UNPAID) is sent to the front
desk whatever the reason the payer gave, although the detector already knows the reason and the action: a claim denied
for missing information is fixed and resent (correct_and_resubmit), a claim denied as bundled (CARC 97) is appealed on
the coding rules. On the archive (b8c3146, 13 November) 570 of the 818 front-desk items are whole-claim denials: 421 for
CARC 97 and 73 to fix and resend (494 items, $7,400), against 11 ($2,800) with an authorization, eligibility or coverage
reason. The test compares each whole-claim denial with the same denial on a line, so it does not depend on what the
kinds are called. Invented data only."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6cov_h import add_file, clean_visits, clm, pcn, sv, utc_today, ymd  # noqa: E402

from remitwatch import insights  # noqa: E402
from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402


def kind_of(tmp_path, name: str, status: str, cas: str, signal: str) -> tuple[str, str]:
    """The kind the brief gives one denial: a store with clean visits and that one denied claim."""
    folder = tmp_path / name
    folder.mkdir()
    db = str(folder / "s.sqlite")
    today = utc_today()
    dos, paid = ymd(today - timedelta(days=40)), ymd(today - timedelta(days=30))
    add_file(folder, db, clean_visits(100, 10, "99214", "223.65", "117.60", dos), paid)
    add_file(folder, db, [clm(pcn(1), status, sv("99214", "223.65", "0.00", cas=cas, dos=dos), dos=dos, charge="223.65")], paid)
    st = Store(db)
    Detector(st, run_id="r6").run()
    got = st.conn.execute("SELECT signal, actionable FROM findings WHERE patient_control_number=? AND category='denial'", (pcn(1),)).fetchall()
    assert [g[0] for g in got] == [signal], got
    kinds = [t["key"] for t in insights.brief(st.conn, today=today.isoformat())["work"]["types"] if t["n"]]
    st.close()
    assert len(kinds) == 1, kinds
    return kinds[0], got[0][1]


def test_a_whole_claim_denial_goes_to_the_kind_of_work_its_reason_needs(tmp_path):
    whole = {carc: kind_of(tmp_path, f"w{carc}", "4", f"CAS*CO*{carc}*223.65~", "CLAIM_DENIED_UNPAID") for carc in ("16", "97", "197")}
    line = {"16": kind_of(tmp_path, "l16", "1", "CAS*CO*16*223.65~", "DENIED_MISSING_INFO"),
            "151": kind_of(tmp_path, "l151", "1", "CAS*CO*151*223.65~", "DENIED_FREQUENCY"),
            "197": kind_of(tmp_path, "l197", "1", "CAS*CO*197*223.65~", "DENIED_AUTH")}
    # the detector's own actions for the whole claims: fix and resend, appeal, appeal
    assert [whole[c][1] for c in ("16", "97", "197")] == ["correct_and_resubmit", "appeal", "appeal"]
    assert whole["197"][0] == line["197"][0]                       # no authorization: the same kind on a claim as on a line
    # missing information: fix and resend, as on a line. Bundled (CARC 97): appealed on the coding rules, as a procedure
    # bundled on a line is (most whole claims denied this way on the archive are procedures, not visits)
    assert (whole["16"][0], whole["97"][0]) == (line["16"][0], "rule"), \
        (f"a whole claim denied for missing information is put under '{whole['16'][0]}' (a line: '{line['16'][0]}'); "
         f"one denied as bundled under '{whole['97'][0]}'")
