"""Stray remittance files must not move what RemitWatch believes: a copy of a remittance cut short never replaces the
complete copy, and a file on its own (provider-level adjustments only, or one file days after the rest stopped) never
makes the feed look current. All data invented."""
from __future__ import annotations

from remitwatch.store import Store, feed_through
from tests.test_features import build_store, claim, clean_visits, svc
from tests.test_remitwatch import remit
from tests.test_workable import charge

PLB_ONLY = "PLB*1777777772*20261231*WO:STRAY*71.59~"


def _file(tmp_path, name: str, body: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(body)
    return str(p)


def _two_claims(second_paid: str = "100.00") -> str:
    """Two claims of $100.00 paid each (the claim helper's own amount); the second's can be changed."""
    return (claim(901, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20261001"), dos="20261001")
            + claim(902, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20261001"), dos="20261001")
            .replace("*500.00*100.00*", f"*500.00*{second_paid}*"))


# --------------------------------------------------------------------------- a copy cut short
def test_a_copy_cut_short_never_replaces_the_complete_copy(tmp_path):
    """The same payment (trace number and date) delivered twice: complete and balanced, then a later file cut after its
    first claim. The complete copy stays; the cut one is set aside, whichever arrives first."""
    for order in ("complete_first", "cut_first"):
        d = tmp_path / order
        d.mkdir()
        s = Store(str(d / "s.sqlite"))
        whole = remit(_two_claims(), "200.00", isa_ctrl="000000777", pay_date="20261010")             # balances
        cut = remit(claim(901, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20261001"), dos="20261001"),
                    "200.00", isa_ctrl="000000777", pay_date="20261010").replace(b"*260904*", b"*261012*")   # a later file
        paths = [_file(d, "a.RMT", whole), _file(d, "b.RMT", cut)]
        s.ingest_835_paths(paths if order == "complete_first" else paths[::-1])
        s.mark_superseded()
        kept = s.conn.execute("SELECT claim_count, balanced FROM transactions WHERE superseded=0").fetchall()
        assert [tuple(r) for r in kept] == [(2, 1)], order
        assert s.conn.execute("""SELECT COUNT(*) FROM claims c JOIN transactions t ON t.transaction_key=c.transaction_key
                                 WHERE t.superseded=0""").fetchone()[0] == 2
        s.close()


def test_a_balanced_correction_still_replaces_the_earlier_copy(tmp_path):
    """Both copies balance: the later file is the payer's correction and wins, as before."""
    s = Store(str(tmp_path / "s.sqlite"))
    first = remit(_two_claims(), "200.00", isa_ctrl="000000778", pay_date="20261010")
    fixed = remit(_two_claims("82.40"), "182.40", isa_ctrl="000000778", pay_date="20261010").replace(b"*260904*", b"*261012*")
    s.ingest_835_paths([_file(tmp_path, "a.RMT", first), _file(tmp_path, "b.RMT", fixed)])
    s.mark_superseded()
    assert [tuple(r) for r in s.conn.execute("SELECT payment_amount_cents, balanced FROM transactions WHERE superseded=0")] == [(18240, 1)]
    s.close()


# --------------------------------------------------------------------------- how far the feed has arrived
def _feed(tmp_path) -> Store:
    """Remittances from two files a few days apart, the newest paid on October 3."""
    build_store(tmp_path, clean_visits(1, 20, "99214", "223.65", "117.60"), pay_date="20261001").close()
    return build_store(tmp_path, clean_visits(21, 20, "99214", "223.65", "117.60"), pay_date="20261003")


def test_a_stray_file_does_not_make_the_feed_current(tmp_path):
    s = _feed(tmp_path)
    assert feed_through(s.conn) == "2026-10-03"
    # a remittance with only a provider-level adjustment, weeks after the feed stopped
    s.ingest_835_paths([_file(tmp_path, "stray1.RMT", remit("", "-71.59", isa_ctrl="000000901", plb=PLB_ONLY, pay_date="20261130"))])
    assert feed_through(s.conn) == "2026-10-03"
    # one file that answers a claim, on its own
    one = claim(950, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20261101"), dos="20261101")
    s.ingest_835_paths([_file(tmp_path, "stray2.RMT", remit(one, "117.60", isa_ctrl="000000902", pay_date="20261130"))])
    assert feed_through(s.conn) == "2026-10-03"
    # a second file within the week backs it: the feed has resumed
    two = claim(951, svc("99214", "223.65", "117.60", "117.60", "CAS*CO*45*106.05~", dos="20261102"), dos="20261102")
    s.ingest_835_paths([_file(tmp_path, "back.RMT", remit(two, "117.60", isa_ctrl="000000903", pay_date="20261127"))])
    assert feed_through(s.conn) == "2026-11-30"
    s.close()


def test_one_file_alone_is_its_own_date(tmp_path):
    s = build_store(tmp_path, clean_visits(1, 5, "99214", "223.65", "117.60"), pay_date="20261003")
    assert feed_through(s.conn) == "2026-10-03"
    s.close()


def test_a_stray_file_does_not_make_every_newer_claim_look_unanswered(tmp_path):
    """The feed stopped on October 3. A claim sent October 20 cannot be judged unanswered, stray file or not; one sent in
    July still can."""
    from remitwatch.detect import Detector
    s = _feed(tmp_path)
    charge(s.conn, "x", "CB0000700C017", "2026-10-20", "2026-10-21")
    charge(s.conn, "y", "CB0000701C017", "2026-07-20", "2026-07-21")
    s.conn.commit()
    s.ingest_835_paths([_file(tmp_path, "stray.RMT", remit("", "-71.59", isa_ctrl="000000904", plb=PLB_ONLY, pay_date="20261215"))])
    Detector(s, run_id="t").run()
    assert [r[0] for r in s.conn.execute("SELECT patient_control_number FROM findings WHERE signal='NO_ANSWER'")] == ["CB0000701C017"]
    s.close()


def test_a_stray_file_does_not_lift_the_hold_on_appeals_over_old_data(tmp_path, monkeypatch):
    import remitwatch.api as api
    s = _feed(tmp_path)
    s.ingest_835_paths([_file(tmp_path, "stray.RMT", remit("", "-71.59", isa_ctrl="000000905", plb=PLB_ONLY, pay_date="20261215"))])
    s.close()
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "s.sqlite"))
    behind = api._data_behind_days()
    from datetime import date
    assert behind == (date.today() - date(2026, 10, 3)).days
