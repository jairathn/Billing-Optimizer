"""
Items on the Work now list that were already done or listed twice (the auditor's DUPLICATE, ALREADY_DONE paid and
ALREADY_DONE voided items on the merged code, plans/push2_eval_2026-11-24.md; trace findings T-N2, T-N5, T-C4):
- a claim the practice voided after its denial stayed listed (T-N5 left out only the payer's answers to the void itself);
- one service the payer answered again for another reason (a corrected claim, a copy denied as a duplicate) was listed
  once per answer: rule 7 folded only repeats of one reason (T-N2), and a payer that said the service was not its to pay
  (CARC 109) stayed listed beside the payer that answered it since;
- a line answered under another code and paid nothing (CODE_CHANGED) was never closed by a later payment of the code the
  practice billed;
- a second payer's whole-charge CO-45 on a claim the first payer forwarded after denying the line was listed beside the
  first payer's denial;
- a duplicate of a claim processed as a crossover (N522) was not read as the payer's coordination answer, so its later
  processing as secondary did not settle it;
- an answer, its reversal and the payer's new answer on one remittance were two items.

Every claim, member number, name, amount and date here is invented (tests/test_remitwatch.remit, tests/test_features.svc).
Each test runs on the server's store file and on the compact in-memory store the serverless cycle builds and analyses.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch.detect import Detector  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests.test_features import NPI, claim, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

KINDS = ["file", "compact"]
DOS = "20260914"
ANTHEM = {"payer": "ANTHEM", "payer_id": "ANTHM"}
HUMANA = {"payer": "HUMANA", "payer_id": "HUMNA"}
CIGNA = {"payer": "CIGNA", "payer_id": "CIGNA"}
MEDICARE_KY = {"payer": "KY MEDICARE", "payer_id": "MCRKY"}
MEDICARE_IN = {"payer": "MEDICARE OF INDIANA", "payer_id": "MCRIN"}
MEDIGAP = {"payer": "MUTUAL OF OMAHA", "payer_id": "MOOMA"}


def _store(tmp_path, kind: str) -> Store:
    return Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)


def _add(store: Store, tmp_path, claims: list[str], pay_date: str, payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
    """Remittances holding these claims (20 to a file, in this order), read into the store the way the feeds read them."""
    paths = []
    for chunk in [claims[i:i + 20] for i in range(0, len(claims), 20)]:
        n = len(list(tmp_path.glob("*.RMT")))
        p = tmp_path / f"r{n:04d}.RMT"
        p.write_bytes(remit("".join(chunk), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=pay_date))
        paths.append(str(p))
    store.ingest_835_paths(paths)


def _clp(cid: str, status: str, lines: str, paid: str = "0.00", cfi: str = "12", charge: str = "223.65", freq: str = "",
         crossover: str = "") -> str:
    """One claim of the bill `cid` names (its last four characters are the submission), answered with claim status
    `status` (CLP02: 1 as primary, 19 as primary and forwarded, 2 as secondary, 4 denied, 22 a reversal); `freq` is its
    frequency code (CLP09: 8, the practice's void); `crossover` names the payer it was forwarded to (NM1*TT)."""
    sign = "-" if status == "22" else ""
    return (f"CLP*{cid}*{status}*{sign}{charge}*{sign}{paid}*0.00*{cfi}*PCN{cid[4:9]}{cid[-3:]}{status}*11{'*' + freq if freq else ''}~"
            "NM1*QC*1*SAMPLE*PAT****MI*M00000777~" + (f"NM1*TT*2*{crossover}*****PI*99999~" if crossover else "")
            + f"NM1*82*2*TEST PROVIDER*****XX*{NPI}~DTM*232*{DOS}~" + lines)


def _line(paid: str, cas: str, allowed: str | None = None, code: str = "99214", charge: str = "223.65", mods: str = "",
          sign: str = "", orig: str = "") -> str:
    out = svc(code, f"{sign}{charge}", f"{sign}{paid}" if float(paid) else paid, allowed, cas, dos=DOS, mods=mods, orig=orig)
    return out.replace("**1~", f"**{sign}1~", 1) if not orig else out


def _usual(cfi: str, allowed: str, code: str = "99214", charge: str = "223.65", start: int = 1) -> list[str]:
    """25 lines the payer paid at its usual rate for the code, a week before the services below: its baseline, so that
    each denial has a size (another date, so that no rule reads them as the same patient's service paid elsewhere)."""
    co = f"{float(charge) - float(allowed):.2f}"
    return [claim(start + i, svc(code, charge, allowed, allowed, f"CAS*CO*45*{co}~", dos="20260907"), cfi=cfi, dos="20260907")
            for i in range(25)]


def _items(s: Store) -> dict[str, list[dict]]:
    """The money items still listed (not stale), by claim id."""
    out: dict[str, list[dict]] = {}
    for r in s.query("SELECT * FROM findings WHERE category IN ('denial','bundling','downcoding') AND COALESCE(stale,0)=0"):
        out.setdefault(r["patient_control_number"], []).append(dict(r))
    return out


def _one(rows: dict, pcn: str) -> dict:
    got = rows.get(pcn) or []
    assert len(got) == 1, f"{pcn}: {[(r['signal'], r['procedure_code']) for r in got]}"
    return got[0]


def _start(s: Store, pcn: str, note: str | None = None) -> None:
    """A person marks the claim's item Started (in progress), as the panel's status action records it."""
    fid = s.conn.execute("SELECT finding_id FROM findings WHERE patient_control_number=?", (pcn,)).fetchone()[0]
    s.conn.execute("UPDATE findings SET status='in_progress', status_changed_at='2026-10-20T15:00:00+00:00' WHERE finding_id=?", (fid,))
    s.conn.execute("""INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor, note)
                      VALUES (?, '2026-10-20T15:00:00+00:00', 'manual', 'open', 'in_progress', 'biller', ?)""", (fid, note or "called the payer"))
    s.conn.commit()


def _ar(row: dict) -> dict:
    return json.loads(row["evidence"] or "{}").get("archive_rule") or {}


# --------------------------------------------------------------------------- T-N5: a claim the practice voided afterwards

AUTH = "CAS*CO*197*223.65~"


@pytest.mark.parametrize("kind", KINDS)
def test_a_claim_the_practice_voided_after_its_denial_is_done_not_on_the_work_list(tmp_path, kind):
    """Humana denies four visits on October 1 for authorization (CO-197). On November 5 it answers the practice's void
    (frequency 8) of three of those claims:
    - V01: voided and nothing since: the item is done ('voided', proven), off the Work now list, never closed as Paid
      (before, it stayed listed as a denial to appeal: the auditor's ALREADY_DONE voided items).
    - V02: never voided: still work now.
    - V03: voided, then billed again on a new claim (V03C018) that Humana denied on November 10: the void is no withdrawal
      of the service, and the item is never called voided.
    - V04: voided, but a biller had started the item: it stays listed (the person's status), the void named on it."""
    s = _store(tmp_path, kind)
    _add(s, tmp_path, _usual("16", "117.60") + [_clp(f"CB0000V0{i}C017", "1", _line("0.00", AUTH), cfi="16") for i in (1, 2, 3, 4)],
         "20261001", **HUMANA)
    Detector(s, run_id="t1").run()
    assert {pcn for pcn in _items(s)} >= {f"CB0000V0{i}C017" for i in (1, 2, 3, 4)}
    _start(s, "CB0000V04C017")
    void = "CAS*CO*115*223.65~"
    _add(s, tmp_path, [_clp(f"CB0000V0{i}C017", "1", _line("0.00", void), cfi="16", freq="8") for i in (1, 3, 4)], "20261105", **HUMANA)
    _add(s, tmp_path, [_clp("CB0000V03C018", "1", _line("0.00", "CAS*CO*16*223.65~LQ*HE*MA130~"), cfi="16")], "20261110", **HUMANA)
    Detector(s, run_id="t2").run()
    rows = _items(s)
    for pcn in sorted(rows):
        for r in rows[pcn]:
            print(pcn, r["signal"], r["status"], r["work_state"], "|", r["state_basis"])
    v1 = _one(rows, "CB0000V01C017")
    assert (v1["status"], v1["work_state"]) == ("open", "done"), (v1["status"], v1["work_state"], v1["state_basis"])
    assert _ar(v1)["reclass"] == "voided" and _ar(v1)["strength"] == "proven"
    assert v1["state_basis"].startswith("Voided by the practice"), v1["state_basis"]
    assert v1["hint"].startswith("The practice voided this claim: HUMANA answered its void of the claim (frequency 8) on 2026-11-05"), v1["hint"]
    assert _one(rows, "CB0000V02C017")["work_state"] == "work_now"
    assert all(_ar(r).get("reclass") != "voided" for r in rows.get("CB0000V03C017", [])), rows.get("CB0000V03C017")
    assert _one(rows, "CB0000V03C018")["work_state"] == "work_now"
    v4 = _one(rows, "CB0000V04C017")
    assert (v4["status"], v4["work_state"]) == ("in_progress", "work_now")
    assert _ar(v4)["reclass"] == "voided", "the void is not named on the worked item"
    s.close()


# --------------------------------------------------------------------------- T-N2: one service answered again, one item

def _f(rows: dict, pcn: str) -> dict:
    return _one(rows, pcn)


@pytest.mark.parametrize("kind", KINDS)
def test_one_service_the_payer_answered_again_for_another_reason_is_one_item(tmp_path, kind):
    """Anthem denies each service on October 1 and answers its resubmissions later (a new claim id of the same bill):
    - R01: missing information (CO-16, MA130), then the corrected claim denied for medical necessity (CO-50): one item, the
      newest answer's; the first listing is folded into it (before: two items, rule 7 folded only one reason's repeats).
    - R02: a biopsy bundled (PI-B10), then two resubmissions denied as duplicates (OA-18): one item, the bundling denial
      (a copy's denial is never the answer to work while the payer's own answer is listed).
    - R03: authorization (CO-197), a copy denied as a duplicate (CO-18), authorization again: one item, the newest CO-197.
    - R05: missing information, a biller started it, then denied for medical necessity: both stay listed (a person's work
      is never folded away), the newer answer named on the worked one.
    - R06: Medicare's Kentucky contractor says the visit is not in its jurisdiction (CO-109, N104); the Indiana contractor
      then denies it for missing information: one item, Indiana's (before: both listed).
    - R07: Anthem and Humana each deny the visit (CO-16): two payers, no CARC 109, both stay listed."""
    s = _store(tmp_path, kind)
    info = "CAS*CO*16*223.65~LQ*HE*MA130~"
    b10 = "CAS*PI*B10*150.00~"
    dup = "CAS*OA*18*150.00~"
    _add(s, tmp_path, _usual("12", "117.60") + [
        _clp("CB0000R01C017", "1", _line("0.00", info)),
        _clp("CB0000R02C017", "1", _line("0.00", b10, code="11102", charge="150.00"), charge="150.00"),
        _clp("CB0000R03C017", "1", _line("0.00", AUTH)),
        _clp("CB0000R05C017", "1", _line("0.00", info)),
        _clp("CB0000R07C017", "1", _line("0.00", info))], "20261001", **ANTHEM)
    _add(s, tmp_path, [_clp("CB0000R06C017", "1", _line("0.00", "CAS*CO*109*223.65~LQ*HE*N104~"), cfi="MB")], "20261001", **MEDICARE_KY)
    Detector(s, run_id="t1").run()
    _start(s, "CB0000R05C017")
    _add(s, tmp_path, [_clp("CB0000R01C018", "1", _line("0.00", "CAS*CO*50*223.65~")),
                       _clp("CB0000R02C018", "1", _line("0.00", dup, code="11102", charge="150.00"), charge="150.00"),
                       _clp("CB0000R03C018", "1", _line("0.00", "CAS*CO*18*223.65~")),
                       _clp("CB0000R05C018", "1", _line("0.00", "CAS*CO*50*223.65~"))], "20261020", **ANTHEM)
    _add(s, tmp_path, [_clp("CB0000R06C018", "1", _line("0.00", info), cfi="MB")], "20261020", **MEDICARE_IN)
    _add(s, tmp_path, [_clp("CB0000R07C018", "1", _line("0.00", info), cfi="16")], "20261020", **HUMANA)
    _add(s, tmp_path, [_clp("CB0000R02C019", "1", _line("0.00", dup, code="11102", charge="150.00"), charge="150.00"),
                       _clp("CB0000R03C019", "1", _line("0.00", AUTH))], "20261110", **ANTHEM)
    Detector(s, run_id="t2").run()
    rows = _items(s)
    for pcn in sorted(rows):
        for r in rows[pcn]:
            print(pcn, r["signal"], r["status"], r["work_state"], "|", r["hint"])
    listed = {pcn for pcn in rows if pcn[:9] in {f"CB0000R0{i}" for i in (1, 2, 3, 5, 6, 7)}}
    assert listed == {"CB0000R01C018", "CB0000R02C017", "CB0000R03C019", "CB0000R05C017", "CB0000R05C018", "CB0000R06C018",
                      "CB0000R07C017", "CB0000R07C018"}, sorted(listed)
    folded = {pcn: sorted(x["signal"] for x in json.loads(_f(rows, pcn)["evidence"]).get("folded") or [])
              for pcn in ("CB0000R01C018", "CB0000R02C017", "CB0000R03C019", "CB0000R06C018")}
    assert folded == {"CB0000R01C018": ["DENIED_MISSING_INFO"], "CB0000R02C017": ["DENIED_DUPLICATE_UNPAID", "DENIED_DUPLICATE_UNPAID"],
                      "CB0000R03C019": ["DENIED_AUTH", "DENIED_DUPLICATE_UNPAID"], "CB0000R06C018": ["DENIED_NONCOVERED"]}, folded
    worked = _f(rows, "CB0000R05C017")
    newer = _f(rows, "CB0000R05C018")
    assert worked["status"] == "in_progress" and _ar(worked)["reclass"] == "replaced" and _ar(worked)["new_item"] == newer["finding_id"]
    assert "a person worked it" in worked["hint"], worked["hint"]
    s.close()


# --------------------------------------------------------------------------- a code change paid later as the billed code

@pytest.mark.parametrize("kind", KINDS)
def test_a_line_answered_under_another_code_then_paid_as_the_billed_code_is_closed(tmp_path, kind):
    """Anthem answers 77600 as 77436 and pays nothing (CO-97, N19) on October 1: a code change (CODE_CHANGED), sized at
    Anthem's usual $300.00 for 77600. On November 10 Medicare, the second payer, processes K01 as secondary and pays 77600
    $250.00 after Anthem's answer: the billed code is paid, and the item closes on that payment (before, a code change was
    never checked for a later payment). K02 has no later payment: still open."""
    s = _store(tmp_path, kind)
    changed = _line("0.00", "CAS*CO*97*861.23~LQ*HE*N19~", code="77436", charge="861.23", orig="77600")
    _add(s, tmp_path, _usual("12", "300.00", code="77600", charge="861.23") + [
        _clp("CB0000K01C017", "1", changed, charge="861.23"), _clp("CB0000K02C017", "1", changed, charge="861.23")], "20261001", **ANTHEM)
    Detector(s, run_id="t1").run()
    rows = _items(s)
    assert {(pcn, _one(rows, pcn)["signal"]) for pcn in ("CB0000K01C017", "CB0000K02C017")} == {
        ("CB0000K01C017", "CODE_CHANGED"), ("CB0000K02C017", "CODE_CHANGED")}
    _add(s, tmp_path, [_clp("CB0000K01C018", "2", _line("250.00", "CAS*CO*45*549.16~CAS*PR*2*62.07~", "312.07", code="77600", charge="861.23"),
                            paid="250.00", cfi="MB", charge="861.23")], "20261110", **MEDICARE_KY)
    Detector(s, run_id="t2").run()
    rows = _items(s)
    k1, k2 = _one(rows, "CB0000K01C017"), _one(rows, "CB0000K02C017")
    print(k1["status"], k1["auto_note"], "|", k2["status"])
    assert (k1["status"], k1["work_state"]) == ("resolved", "done"), (k1["status"], k1["auto_note"])
    assert json.loads(k1["evidence"])["paid_later"]["paid_cents"] == 25000
    assert k2["status"] == "open"
    s.close()


# --------------------------------------------------------------------------- a second payer following the first's denial

@pytest.mark.parametrize("kind", KINDS)
def test_a_second_payers_whole_charge_co45_after_the_first_payer_denied_the_line_is_not_listed(tmp_path, kind):
    """Medicare processes G01 on October 1 and forwards it to the Medigap (claim status 19): it pays the destruction and
    bundles the visit (CO-97). The Medigap answers on October 8: it pays the coinsurance of the destruction and takes the
    visit's whole charge as CO-45, allowing nothing. Its CO-45 follows Medicare's denial, which is the item to work: no
    second item (before, a 'denial in all but name' was listed beside Medicare's, the auditor's duplicate). G02: the
    Medigap's CO-45 with no answer of Medicare's on file is listed as before. G03: the Medigap's answer came first into
    the feed and was listed, and a biller started it; Medicare's forwarded denial arrives later: the worked item stays
    listed, Medicare's answer named on it."""
    s = _store(tmp_path, kind)
    visit_bundled = _line("0.00", "CAS*CO*97*223.65~", code="99214", mods="25")
    destruction = _line("40.00", "CAS*CO*45*127.56~CAS*PR*2*10.00~", "50.00", code="17110", charge="177.56")
    co45 = _line("0.00", "CAS*CO*45*223.65~", code="99214", mods="25")
    share = _line("10.00", "CAS*OA*23*40.00~CAS*CO*45*127.56~", "10.00", code="17110", charge="177.56")
    # the Medigap has paid the visit's code before, for another patient (a payer with no rate for a code makes no denial
    # of it with a whole-charge CO-45: tests/test_whole_charge_co45.py)
    _add(s, tmp_path, [claim(901, svc("99214", "223.65", "20.00", "20.00", "CAS*CO*45*203.65~", dos="20260907"), cfi="15", dos="20260907")],
         "20260920", **MEDIGAP)
    _add(s, tmp_path, [_clp("CB0000G03C017", "1", co45 + share, paid="10.00", cfi="15", charge="401.21")], "20261008", **MEDIGAP)
    Detector(s, run_id="t0").run()
    _start(s, "CB0000G03C017")
    _add(s, tmp_path, [_clp(f"CB0000G0{i}C017", "19", visit_bundled + destruction, paid="40.00", cfi="MB", charge="401.21",
                            crossover="MUTUAL OF OMAHA") for i in (1, 3)], "20261001", **MEDICARE_KY)
    _add(s, tmp_path, [_clp("CB0000G01C017", "1", co45 + share, paid="10.00", cfi="15", charge="401.21"),
                       _clp("CB0000G02C017", "1", co45 + share, paid="10.00", cfi="15", charge="401.21")], "20261008", **MEDIGAP)
    Detector(s, run_id="t1").run()
    rows = _items(s)
    for pcn in sorted(rows):
        for r in rows[pcn]:
            print(pcn, r["signal"], r["payer_group"], r["status"], "|", r["hint"])
    g1 = {(r["signal"], r["payer_group"]) for r in rows.get("CB0000G01C017", [])}
    assert g1 == {("EM_BUNDLED_MOD25", "Medicare KY (CGS J15)")}, g1
    assert {(r["signal"], r["payer_group"]) for r in rows.get("CB0000G02C017", [])} == {("DENIED_OTHER", "Medicare Supplement carriers")}
    g3 = {r["signal"]: r for r in rows.get("CB0000G03C017", [])}
    assert set(g3) == {"EM_BUNDLED_MOD25", "DENIED_OTHER"}, set(g3)
    assert g3["DENIED_OTHER"]["status"] == "in_progress" and "Kept listed because a person worked it" in g3["DENIED_OTHER"]["hint"]
    s.close()


# --------------------------------------------------------------------------- N522: the duplicate of a crossover claim

@pytest.mark.parametrize("kind", KINDS)
def test_a_duplicate_of_a_crossover_claim_the_payer_then_processed_as_secondary_is_closed(tmp_path, kind):
    """Cigna denies N01's whole claim on October 1 as a duplicate of a claim processed, or to be processed, as a crossover
    claim (OA-18 with N522), and on November 10 processes it as secondary, the primary's answer applied (OA-23), paying
    $20.00: its coordination answer is settled by that processing, and the item closes with that answer as its proof (as
    a coordination denial does, T-C3). N02: OA-18 with no N522 is a duplicate's denial, not a coordination answer: the
    same processing leaves it listed as before. N03: as N01, but on November 20 Cigna takes that secondary payment back,
    under another payer id than it paid under (the ledger pairs reversals by payer id): the processing no longer stands,
    and the item stays listed (a closure on a payment taken back would be a wrong closure)."""
    s = _store(tmp_path, kind)
    n522 = "CAS*OA*18*223.65~LQ*HE*N522~"
    _add(s, tmp_path, _usual("13", "117.60") + [
        _clp("CB0000N01C017", "4", _line("0.00", n522), cfi="13"), _clp("CB0000N02C017", "4", _line("0.00", "CAS*OA*18*223.65~"), cfi="13"),
        _clp("CB0000N03C017", "4", _line("0.00", n522), cfi="13")], "20261001", **CIGNA)
    Detector(s, run_id="t1").run()
    assert {pcn for pcn, r in _items(s).items() if r[0]["status"] == "open"} >= {"CB0000N01C017", "CB0000N02C017", "CB0000N03C017"}
    second = _line("20.00", "CAS*OA*23*97.60~CAS*CO*45*106.05~", "20.00")
    _add(s, tmp_path, [_clp(f"CB0000N0{i}C017", "2", second, paid="20.00", cfi="13") for i in (1, 2, 3)], "20261110", **CIGNA)
    taken = _line("20.00", "CAS*OA*23*-97.60~CAS*CO*45*-106.05~", "20.00", sign="-")
    _add(s, tmp_path, [_clp("CB0000N03C017", "22", taken, paid="20.00", cfi="13")], "20261120", payer="CIGNA", payer_id="CIGN2")
    Detector(s, run_id="t2").run()
    rows = _items(s)
    n1, n2, n3 = _one(rows, "CB0000N01C017"), _one(rows, "CB0000N02C017"), _one(rows, "CB0000N03C017")
    print(n1["status"], n1["auto_note"], "|", n2["status"], "|", n3["status"], n3["auto_note"])
    assert (n1["status"], n1["recovered_cents"]) == ("resolved", 2000), (n1["status"], n1["auto_note"])
    assert json.loads(n1["evidence"])["settled_by"]["how"] == "processed as secondary"
    assert n2["status"] == "open"
    assert n3["status"] == "open", f"closed on a secondary payment taken back: {n3['auto_note']}"
    s.close()


# --------------------------------------------------------------------------- an answer, its reversal and the new answer

@pytest.mark.parametrize("kind", KINDS)
def test_an_answer_reversed_and_given_again_on_one_remittance_is_one_item(tmp_path, kind):
    """One remittance carries S01's answer (the visit bundled, CO-97), its reversal and the same answer again, in that
    order (the payer processed, reversed and processed the claim in one cycle): one item, on the newest answer, the first
    folded into it (before: two items for one service). S02 carries the answer alone: one item."""
    s = _store(tmp_path, kind)
    bundled = "CAS*CO*97*141.66~LQ*HE*N19~"
    first = _clp("CB0000S01C017", "1", _line("0.00", bundled, code="99214", charge="141.66", mods="57"), charge="141.66")
    rev = _clp("CB0000S01C017", "22", _line("0.00", "CAS*CO*97*-141.66~LQ*HE*N19~", code="99214", charge="141.66", mods="57", sign="-"),
               charge="141.66")
    _add(s, tmp_path, _usual("12", "117.60"), "20260920", **ANTHEM)
    _add(s, tmp_path, [first, rev, first, _clp("CB0000S02C017", "1", _line("0.00", bundled, code="99214", charge="141.66", mods="57"),
                                               charge="141.66")], "20261001", **ANTHEM)
    Detector(s, run_id="t1").run()
    rows = _items(s)
    for pcn in sorted(rows):
        for r in rows[pcn]:
            print(pcn, r["signal"], r["line_key"], r["status"])
    s1 = _one(rows, "CB0000S01C017")
    assert s1["line_key"].endswith(":3:1"), s1["line_key"]            # the remittance's third claim: the payer's new answer
    assert [x["rule"] for x in json.loads(s1["evidence"]).get("folded") or []] == ["same_day_answers"]
    _one(rows, "CB0000S02C017")
    s.close()
