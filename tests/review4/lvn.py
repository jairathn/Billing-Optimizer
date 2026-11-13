"""Live (3f8855b) against release (36030eb): run one invented store through each, each in its own subprocess with that
tree first on sys.path. Invented data only: names, member numbers, claim ids, payer ids and amounts are made up."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
REL = os.environ.get("RW_REL", REPO)       # the release under test: this tree
LIVE_COMMIT = "3f8855b"                     # what the site ran before this release


def _extract_live() -> str | None:
    """The live release's code, extracted once with git archive; None where git does not have the commit (a shallow CI
    checkout): the tests that compare with it are then skipped."""
    import shutil
    import tempfile
    if os.environ.get("RW_LIVE_TREE"):
        return os.environ["RW_LIVE_TREE"]
    if not shutil.which("git") or subprocess.run(["git", "cat-file", "-e", f"{LIVE_COMMIT}^{{commit}}"], cwd=REPO, capture_output=True).returncode:
        return None
    d = tempfile.mkdtemp(prefix="rw-live-")
    if subprocess.run(f"git archive {LIVE_COMMIT} | tar -x -C '{d}'", shell=True, cwd=REPO, capture_output=True).returncode:
        return None
    os.environ["RW_LIVE_TREE"] = d
    return d


LIVE = _extract_live()
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.test_features import NPI, build_store, clean_visits  # noqa: E402,F401
from tests.test_remitwatch import remit  # noqa: E402
from remitwatch.store import Store  # noqa: E402

MARK = "@@JSON@@"


def run_tree(tree: str, db: str, body: str) -> object:
    """Run `body` against the store at `db` with `tree`'s remitwatch; the body sets `result` (JSON-able)."""
    if tree is None:
        import pytest
        pytest.skip(f"needs the live release's commit ({LIVE_COMMIT}) in git history")
    code = textwrap.dedent(f"""
        import sys, json, sqlite3
        sys.path.insert(0, {tree!r})
        from remitwatch.store import Store
        from remitwatch.detect import Detector
        from remitwatch.actions import apply_status
        import remitwatch.detect as d
        assert d.__file__.startswith({tree!r}), d.__file__
        s = Store({db!r})
        result = None
    """) + textwrap.dedent(body) + textwrap.dedent(f"""
        s.conn.commit()
        s.close()
        print({MARK!r} + json.dumps(result, default=str))
    """)
    out = subprocess.run([sys.executable, "-I", "-c", code], cwd=tree, capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"{tree} failed:\n{out.stdout[-3000:]}\n{out.stderr[-3000:]}")
    line = [x for x in out.stdout.splitlines() if x.startswith(MARK)][-1]
    return json.loads(line[len(MARK):])


def detect(tree: str, db: str, run_id: str = "run", now: str | None = None) -> list[dict]:
    body = f"""
        det = Detector(s, run_id={run_id!r})
        if {now!r}:
            det.now = {now!r}
        det.run()
        s.conn.row_factory = sqlite3.Row
        result = [dict(r) for r in s.conn.execute("SELECT * FROM findings")]
    """
    return run_tree(tree, db, body)


def act(tree: str, db: str, pcn: str, signal: str, status: str, notes: str | None = None, recovered: int | None = None,
        at: str | None = None, actor: str = "biller", line_seq: int | None = None) -> str:
    """Set a status the way the page does (apply_status) on the finding of this claim id and signal (and line number)."""
    extra = f" AND line_key LIKE '%:{line_seq}'" if line_seq else ""
    body = f"""
        r = s.conn.execute("SELECT finding_id FROM findings WHERE patient_control_number=? AND signal=? AND COALESCE(stale,0)=0{extra}",
                           ({pcn!r}, {signal!r})).fetchall()
        assert len(r) == 1, ({pcn!r}, {signal!r}, r)
        assert apply_status(s.conn, r[0][0], {status!r}, {notes!r}, {recovered!r}, actor={actor!r}, at={at!r})
        result = r[0][0]
    """
    return run_tree(tree, db, body)


def copy_db(src: str, dst: str) -> str:
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    a.backup(b)
    a.close()
    b.close()
    return dst


def both(tmp_path, run_id: str = "run", now: str | None = None) -> tuple[list[dict], list[dict]]:
    """The same store (closed) analysed by the live release and by the release under review, on separate copies."""
    db = str(tmp_path / "s.sqlite")
    live, rel = copy_db(db, str(tmp_path / "live.sqlite")), copy_db(db, str(tmp_path / "rel.sqlite"))
    return detect(LIVE, live, run_id, now), detect(REL, rel, run_id, now)


def money(rows: list[dict], where=lambda r: True) -> tuple[int, int]:
    """Findings and dollars listed as still owed (open, in progress or appealed; not stale) in money categories."""
    sel = [r for r in rows if where(r) and not r.get("stale") and r["status"] in ("open", "in_progress", "appealed")
           and r["category"] in ("downcoding", "bundling", "denial", "underpayment")]
    return len(sel), sum(r.get("shortfall_cents") or 0 for r in sel)


def show(label: str, rows: list[dict], cols=("patient_control_number", "procedure_code", "signal", "status", "stale", "shortfall_cents",
                                            "recovered_cents", "notes", "hint")) -> None:
    print(f"--- {label}")
    for r in sorted(rows, key=lambda r: (r.get("category") or "", r.get("patient_control_number") or "", r.get("signal") or "")):
        if r.get("category") in ("info", "contract", "fees") and r.get("signal") not in ("TAKEBACK",):
            continue
        print({c: r.get(c) for c in cols})


# ---------------------------------------------------------------------------------------------------- 835 builders
WHO = "ELM*BO****MI*W55500302"
PROV = f"NM1*82*1*TESTER*DANA****XX*{NPI}~"         # a person: the rendering clinician


def sv(code, charge, paid, units=1, cas="", dos="20260401", allowed=None, mods="", orig="", orig_units=""):
    """One service line: SVC01 code, SVC02 charge, SVC03 paid, SVC05 units, SVC06 billed code, SVC07 billed units."""
    svc06 = f"HC:{orig}" if orig else ""
    tail = f"*{svc06}*{orig_units}" if (orig or orig_units) else ""
    out = f"SVC*HC:{code}{':' + mods if mods else ''}*{charge}*{paid}**{units}{tail}~DTM*472*{dos}~{cas}"
    return out + (f"AMT*B6*{allowed}~" if allowed else "")


def clm(pcn, status, charge, paid, lines="", who=WHO, payer_ctl="PC1", dos="20260401", claim_cas="", pr="0.00", cfi="12"):
    return (f"CLP*{pcn}*{status}*{charge}*{paid}*{pr}*{cfi}*{payer_ctl}*11~NM1*QC*1*{who}~{PROV}"
            f"DTM*232*{dos}~{claim_cas}{lines}")


def add(tmp_path, claims, pay_date, payer="ANTHEM", payer_id="ANTHM", plb=""):
    """One more remittance file from this payer, ingested into the test store."""
    store = Store(str(tmp_path / "s.sqlite"))
    n = len(list(tmp_path.glob("*.RMT")))
    p = tmp_path / f"r{n:04d}.RMT"
    p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{n + 1:09d}",
                        pay_date=pay_date, plb=plb))
    store.ingest_835_paths([str(p)])
    store.close()


def base(tmp_path, pay_date="20260301", payer="ANTHEM", payer_id="ANTHM", extra=()):
    """25 clean 99214 visits and 25 clean 99213 visits, 25 17000 and 25 11102, so the payer has usual rates and the codes
    count as paid once per patient and date."""
    claims = (clean_visits(1, 25, "99214", "223.65", "117.60") + clean_visits(101, 25, "99213", "156.66", "76.15")
              + clean_visits(201, 25, "17000", "150.00", "60.00") + clean_visits(301, 25, "11102", "180.00", "90.00") + list(extra))
    s = build_store(tmp_path, claims, pay_date=pay_date, payer=payer)
    s.close()
