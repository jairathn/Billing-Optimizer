"""The failing tests of fx-837's needs from other fixers (plans/progress/fx-837.md, "Needs from others"). Invented data
only. Not collected by the suite (this file's name is not test_*.py): each fails on wip/fx-837 until the fixer named in
its docstring makes the change; then move it into that fixer's tests. Run from the repository root:
    python3 -m pytest -q plans/progress/fx-837_needs_tests.py
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from remitwatch.detect import Detector, WAITING_837_META  # noqa
from tests.test_837_sends import KINDS, _remits, _store, _visits  # noqa


@pytest.mark.parametrize("kind", KINDS)
def test_an_exports_charge_row_with_no_charge_given_is_priced_at_the_payers_rate(tmp_path, kind):
    """F3b need (live_items keeps None): EMA's charge export gives no charge on bill CB0000B91's 99214 row (the cell is
    blank, not $0.00). The charge is unknown, not nothing: the claim is chased at Anthem's rate for a 99214 ($117.60)."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000B91C017", charges_cents=None)
    ema(s.conn, "CB0000B91C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-09-16")
    s.conn.commit()
    Detector(s, run_id="t1").run()
    got = [dict(r) for r in s.query("SELECT signal, shortfall_cents FROM findings WHERE patient_control_number='CB0000B91C017'")]
    assert got == [{"signal": "NO_ANSWER", "shortfall_cents": 11760}], got
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_an_exports_claim_not_due_yet_is_on_the_waiting_view(tmp_path, kind):
    """F3b need (_note_waiting in run_ema_open_claims): bill CB0000B92, which EMA's weekly exports list as Submitted
    November 1, is not due before Anthem's answer days run out, so no list chases it yet. The waiting view (meta
    feed:waiting_837) shows it with its due day, as it shows the 837 pass's own claims."""
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    _remits(s, tmp_path, _visits(), "20261110")
    ema(s.conn, "CB0000B92C017", service_date="2026-10-30", bill_created="2026-11-01")
    ema(s.conn, "CB0000B92C017", family="claim_status", claim_status="Submitted", claim_submission_date="2026-11-01",
        service_date="2026-10-30")
    s.conn.commit()
    Detector(s, run_id="t1").run()
    w = json.loads(s.conn.execute("SELECT value FROM meta WHERE key=?", (WAITING_837_META,)).fetchone()[0] or "{}")
    print(w)
    assert [c.get("claim_id") or c.get("pcn") for c in w.get("claims") or []] == ["CB0000B92C017"], w
    s.close()


@pytest.mark.parametrize("kind", KINDS)
def test_the_dashboards_copy_shows_every_send_in_an_items_story(tmp_path, kind):
    """F6 need (cloud.SERVING_TABLES carries the findings' bills' 837 sends): bill CB0000T05's C017 went to Anthem September
    1 and its replacement C024 September 15; no payer answered. The story on the dashboard's copy shows both sends, as the
    store's does, with no note that the 837 files are kept on the server."""
    from remitwatch import cloud, story
    from remitwatch.store import Store
    from tests.test_837_sends import V, send
    from tests.test_fx_837 import clm837
    s = _store(tmp_path, kind)
    send(s, tmp_path, [clm837("CB0000T05C017", V)], "20260901")
    send(s, tmp_path, [clm837("CB0000T05C024", V, freq="7")], "20260915")
    _remits(s, tmp_path, _visits(), "20261110")
    Detector(s, run_id="t1").run()
    fid = s.query("SELECT finding_id FROM findings WHERE patient_control_number='CB0000T05C024'")[0]["finding_id"]
    copy = str(tmp_path / "serving.sqlite")
    cloud.build_serving(s.conn, copy)
    served = Store(copy)
    st = story.item_story(served.conn, fid)
    sends = [(r["date"], r["claim_id"]) for r in st["rows"] if r["source"] == "837"]
    assert sends == [("2026-09-01", "CB0000T05C017"), ("2026-09-15", "CB0000T05C024")], sends
    assert story.COPY_837_NOTE not in st["notes"]
    served.close()
    s.close()


def test_the_server_reads_again_an_837_read_before_the_patient_loop_was_read(tmp_path, monkeypatch):
    """F6 need (hosts read the 837 files again once): the server's cycle read bill CB0000T06's 837 before
    x12_837.READ_VERSION 2 (its claim has no patient, SBR01 or other payer on file: read_version 0). The file is still
    where the cycle read it. The next cycle reads it again (x12_837.files_to_reread, reread_837_bytes): the claim gets
    its keyed patient and the file is no longer listed to read again."""
    from remitwatch import pipeline, x12_837
    from remitwatch.store import Store
    from tests.test_837_sends import V
    from tests.test_fx_837 import clm837
    for k in [k for k in os.environ if k.startswith(("DROPBOX_", "TRIZETTO_", "SMTP_", "SRFAX_", "MODMED_"))]:
        monkeypatch.delenv(k, raising=False)
    drop = tmp_path / "drop"
    drop.mkdir()
    st = (f"ST*837*0001*005010X222A1~BHT*0019*00*1*20260915*1200*CH~HL*1**20*1~NM1*85*2*TEST DERMATOLOGY*****XX*1777777772~"
          + clm837("CB0000T06C017", V) + "SE*99*0001~")
    (drop / "c0001.837").write_bytes((f"ISA*00*          *00*          *ZZ*999999999      *ZZ*TRIZETTO       *260915*1200*^*00501*000000001*0*P*:~"
                                      f"GS*HC*999999999*TRIZETTO*20260915*1200*1*X*005010X222A1~" + st + "GE*1*1~IEA*1*000000001~").encode())
    db = str(tmp_path / "s.sqlite")
    pipeline.run_cycle(db, sync=False, local=str(drop), email=False, report=False, log=lambda *a: None)
    s = Store(db)
    assert s.conn.execute("SELECT COUNT(*) FROM claims_837").fetchone()[0] == 1
    s.conn.execute("UPDATE claims_837 SET patient_key=NULL, person_key=NULL, payer_resp=NULL, filing_indicator=NULL, read_version=0")
    s.conn.commit()
    assert x12_837.files_to_reread(s.conn)
    s.close()
    pipeline.run_cycle(db, sync=False, local=str(drop), email=False, report=False, log=lambda *a: None)
    s = Store(db)
    row = s.conn.execute("SELECT patient_key, payer_resp, read_version FROM claims_837").fetchone()
    assert row[0] is not None and row[1] == "P" and row[2] == x12_837.READ_VERSION, row
    assert not x12_837.files_to_reread(s.conn)
    s.close()


def test_the_story_names_an_837_send_in_plain_words():
    """F5 need (features.js XP_SOURCE): the story's rows from an 837 claim file carry source '837'; the drawer labels each
    source in words (EMA posting, EMA A/R...), and has none for '837', so it shows the bare code."""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__import__("remitwatch").__file__)))
    with open(os.path.join(root, "remitwatch", "static", "features.js"), encoding="utf-8") as fh:
        js = fh.read()
    m = re.search(r"const XP_SOURCE = \{([^}]*)\}", js)
    assert m and re.search(r"'837'\s*:", m.group(1)), m.group(1) if m else "no XP_SOURCE"


@pytest.mark.parametrize("kind", KINDS)
def test_an_older_exports_bill_stays_given_way_once_the_newer_bill_of_the_visit_is_answered(tmp_path, kind):
    """F3b need (run_ema_open_claims' newest_for, the exports' side of fx-837-r2-6): one patient's visit of September 14
    (99214) is on two bills EMA's weekly exports list: CB0000E81 (Submitted September 15, no answer) and the bill made
    again, CB0000E82 (Submitted September 25). Anthem denies E82 (CO-16, October 30). The visit's answer is on the newest
    bill: E81, the bill the practice replaced, is not chased as never answered beside E82's denial (the newer bill's
    answer takes it out of the candidates, so today the older one comes back)."""
    from tests.test_837_sends import answer
    from tests.test_workable import ema
    s = _store(tmp_path, kind)
    for cid, day in (("CB0000E81C017", "2026-09-15"), ("CB0000E82C017", "2026-09-25")):
        ema(s.conn, cid, bill_created=day, patient_id="P0000E81")
        ema(s.conn, cid, family="claim_status", claim_status="Submitted", claim_submission_date=day, patient_id="P0000E81")
    s.conn.commit()
    _remits(s, tmp_path, [answer("CB0000E82C017", status="1", paid="0.00")], "20261030")
    _remits(s, tmp_path, _visits(), "20261110")
    Detector(s, run_id="t1").run()
    rows = [dict(r) for r in s.query("SELECT patient_control_number, signal FROM findings WHERE COALESCE(stale,0)=0 "
                                     "AND patient_control_number LIKE 'CB0000E8%'")]
    print(rows)
    assert any(r["patient_control_number"] == "CB0000E82C017" for r in rows), "control: E82's denial is listed"
    assert not any(r["patient_control_number"] == "CB0000E81C017" and r["signal"] == "NO_ANSWER" for r in rows), rows
    s.close()
