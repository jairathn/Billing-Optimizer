"""Helpers for the sixth review, area: the EMA coverage check and the home screen's data (remitwatch.link.ema_coverage
and stored_ema_coverage, remitwatch.insights, /api/coverage, /api/brief and the Data tab). Live release 6dcc450 against
this tree. Invented data only: names, member numbers, claim ids, payer ids and amounts are made up. Run from the
repository root with python3 -B -m pytest -q -p no:cacheprovider tests/review6."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from remitwatch.store import Store  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

LIVE_COMMIT = "6dcc450"         # what the site runs before these four commits
PW = "correct-horse-battery-7"
DOC = "1555555551"              # an invented physician NPI
MARK = "@@JSON@@"


def utc_today() -> date:
    """The day as the dashboard's SQL sees it (date('now') is UTC)."""
    return datetime.now(timezone.utc).date()


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def sv(code: str, charge: str, paid: str, allowed: str | None = None, cas: str = "", dos: str = "20260401", units: int = 1) -> str:
    out = f"SVC*HC:{code}*{charge}*{paid}**{units}~DTM*472*{dos}~{cas}"
    return out + (f"AMT*B6*{allowed}~" if allowed else "")


def clm(pcn: str, status: str, lines: str, *, who: str = "SAMPLE*PAT****MI*M00000001", cfi: str = "12", plan: str = "PPO",
        dos: str = "20260401", charge: str = "100.00", paid: str = "0.00") -> str:
    """One claim loop with a physician as rendering provider."""
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*0.00*{cfi}*PC{pcn}*11~NM1*QC*1*{who}~NM1*82*1*DOCTOR*ALEX****XX*{DOC}~"
            f"REF*CE*{plan}~DTM*232*{dos}~{lines}")


def pcn(n: int, suffix: str = "C017") -> str:
    return f"CB{n:07d}{suffix}"


def add_file(folder, db: str, claims: list[str], pay_date: str, payer: str = "ANTHEM", payer_id: str = "ANTHM", plb: str = "") -> None:
    """One more remittance file in `folder`, ingested into the store at `db`."""
    n = len(list(folder.glob("*.RMT")))
    p = folder / f"r{n:04d}.RMT"
    p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=pay_date, plb=plb))
    s = Store(db)
    s.ingest_835_paths([str(p)])
    s.close()


def clean_visits(start: int, n: int, code: str, charge: str, allowed: str, dos: str, **kw) -> list[str]:
    co = f"{float(charge) - float(allowed):.2f}"
    return [clm(pcn(start + i), "1", sv(code, charge, allowed, allowed, f"CAS*CO*45*{co}~", dos=dos), dos=dos, charge=charge, paid=allowed, **kw)
            for i in range(n)]


def real_fingerprint(row: dict) -> str:
    """The identity remitwatch.ema_exports gives a row: every column but the file it came from, so the same row read
    from two files is stored once and a row whose content changed between pulls is stored again."""
    fp = sorted((k, str(v)) for k, v in row.items() if k not in ("source_sha256", "source_name", "row_index", "row_fingerprint"))
    return hashlib.sha256(json.dumps(fp).encode()).hexdigest()


def charge_row(source: str, i: int, d: str, claim_id: str | None, cpt: str = "99213", status: str | None = None, patient: str | None = None) -> dict:
    row = {"family": "charge_lines", "source_sha256": source, "source_name": source, "row_index": i, "service_date": d,
           "provider": "Doctor, Alex", "provider_role": "Doctor", "patient_id": patient, "claim_id": claim_id, "cpt": cpt, "units": 1.0,
           "charges_cents": 15000, "activity_type": "Charge", "claim_status": status, "location": "North Office"}
    row["row_fingerprint"] = real_fingerprint(row)
    return row


def client(monkeypatch, db: str):
    from fastapi.testclient import TestClient

    import remitwatch.api as api
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "PASSWORD", PW)
    monkeypatch.setattr(api, "USERNAME", "oda")
    monkeypatch.setattr(api, "OWNER_PASSWORD", "")
    monkeypatch.setattr(api, "SHOW_PHI", False)
    monkeypatch.delenv("REMITWATCH_MODE", raising=False)
    return TestClient(api.app), ("oda", PW)


# ---------------------------------------------------------------------------------------------- an invented practice
PEOPLE = [("QUIXOTEMBA", "ZEPHYRINE", "ZQ99887766"), ("VORTIGALLO", "PERSIMMON", "VG55443322"), ("MARZIPANOV", "ELDERBERRY", "MZ11223344")]


def person(k: int) -> str:
    last, first, member = PEOPLE[k % len(PEOPLE)]
    return f"{last}*{first}****MI*{member}"


def rich_store(folder) -> str:
    """A store with the home screen's every part: a year of Medicare and Anthem office visits (payer rates against
    Medicare), denials of each kind of work for named invented patients, a routine lower rate (SECOND_RATE), a charge set
    below what a payer allows (CHARGE_BELOW_RATE) and a payment fee (FEE_SKIM). Not analysed yet."""
    db = str(folder / "s.sqlite")
    v25 = "20250602"
    add_file(folder, db, [clm(pcn(9000 + i), "1", sv("99214", "223.65", "110.00", "110.00", "CAS*CO*45*113.65~", dos=v25), dos=v25, cfi="MB",
                              charge="223.65", paid="110.00") for i in range(25)], "20250616", payer="KY MEDICARE", payer_id="MCRKY")
    for chunk in range(0, 210, 70):
        add_file(folder, db, clean_visits(10000 + chunk, 70, "99214", "223.65", "90.00", v25), "20250616")
    d, p = "20260901", "20261001"
    add_file(folder, db, clean_visits(20000, 12, "99214", "223.65", "95.00", d), p)
    add_file(folder, db, [
        clm(pcn(1), "1", sv("99214", "223.65", "0.00", cas="CAS*CO*197*223.65~", dos=d), dos=d, who=person(0)),            # front desk
        clm(pcn(2), "1", sv("99214", "223.65", "0.00", cas="CAS*CO*16*223.65~LQ*HE*N822~", dos=d), dos=d, who=person(1)),  # fix and resend
        clm(pcn(3), "1", sv("99214", "223.65", "0.00", cas="CAS*CO*50*223.65~", dos=d), dos=d, who=person(2)),             # visit note
        clm(pcn(4), "4", sv("99214", "223.65", "0.00", cas="CAS*CO*97*223.65~", dos=d), dos=d, who=person(0)),             # whole claim
    ], p, plb="PLB*999999999*20261231*CS:ZELIS TRANSACTION FEE*15.00~")
    # the payer's rate for 88305 is $90; eight lines charged $75 were paid the $75
    add_file(folder, db, [clm(pcn(30000 + i), "1", sv("88305", "150.00", "90.00", "90.00", "CAS*CO*45*60.00~", dos=d), dos=d, charge="150.00", paid="90.00")
                          for i in range(6)]
             + [clm(pcn(30100 + i), "1", sv("88305", "75.00", "75.00", "75.00", dos=d), dos=d, charge="75.00", paid="75.00", who=person(i)) for i in range(8)], p)
    # Aetna: usual $700 for 17311, then $400 on every visit since (a rate change)
    a = lambda n, allowed, dos: clm(pcn(n), "1", sv("17311", "1200.00", allowed, allowed, f"CAS*CO*45*{1200 - float(allowed):.2f}~", dos=dos),  # noqa: E731
                                    dos=dos, charge="1200.00", paid=allowed, who=person(n))
    add_file(folder, db, [a(40000 + i, "700.00", "20260105") for i in range(25)], "20260115", payer="AETNA", payer_id="AETNA")
    add_file(folder, db, [a(40100 + i, "400.00", "20260810") for i in range(6)], "20260820", payer="AETNA", payer_id="AETNA")
    return db


def identifiers(db: str) -> set[str]:
    """Every patient name, member number, claim id and payer claim number in the store, upper case."""
    import sqlite3
    c = sqlite3.connect(db)
    out = set()
    for row in c.execute("""SELECT patient_last, patient_first, patient_id, insured_last, insured_first, insured_id, patient_control_number,
                                   payer_claim_control_number FROM claims"""):
        out.update(str(v).upper() for v in row if v)
    c.close()
    return out


# ---------------------------------------------------------------------------------------------- the live release
def live_tree() -> str | None:
    """The live release's code, extracted once with git archive; None where git does not have the commit."""
    if os.environ.get("RW6_LIVE_TREE"):
        return os.environ["RW6_LIVE_TREE"]
    if not shutil.which("git") or subprocess.run(["git", "cat-file", "-e", f"{LIVE_COMMIT}^{{commit}}"], cwd=REPO, capture_output=True).returncode:
        return None
    d = tempfile.mkdtemp(prefix="rw6-live-")
    if subprocess.run(f"git archive {LIVE_COMMIT} | tar -x -C '{d}'", shell=True, cwd=REPO, capture_output=True).returncode:
        return None
    os.environ["RW6_LIVE_TREE"] = d
    return d


def run_tree(tree: str, db: str, body: str) -> object:
    """Run `body` against the store at `db` with `tree`'s remitwatch in its own process; the body sets `result`."""
    code = textwrap.dedent(f"""
        import sys, json, sqlite3
        sys.path.insert(0, {tree!r})
        from remitwatch.store import Store
        from remitwatch.detect import Detector, run_detection
        import remitwatch.detect as d
        assert d.__file__.startswith({tree!r}), d.__file__
        result = None
    """) + textwrap.dedent(body) + f"\nprint({MARK!r} + json.dumps(result, default=str))\n"
    out = subprocess.run([sys.executable, "-B", "-I", "-c", code], cwd=tree, capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"{tree} failed:\n{out.stdout[-3000:]}\n{out.stderr[-3000:]}")
    line = [x for x in out.stdout.splitlines() if x.startswith(MARK)][-1]
    return json.loads(line[len(MARK):])
