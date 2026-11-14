"""Builders for the sixth review (detection and rule changes after 6dcc450). Invented data only: claim ids, names,
member numbers, payer claim numbers and amounts are made up. Run from the repository root."""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from remitwatch.store import Store  # noqa: E402
from tests.test_features import NPI  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

WHO = "SAMPLE*PAT****MI*Z99900101"


def sv(code: str, charge: str, paid: str, allowed: str | None = None, units: int | float = 1, cas: str = "", dos: str | None = "20260105",
       period: tuple[str, str] | None = None, rem: str = "", mods: str = "", orig_units: int | None = None) -> str:
    """One service line. `period` gives a DTM*150/151 service period instead of a DTM*472 date; `rem` adds LQ remarks;
    `orig_units` fills SVC07 (units billed)."""
    tail = f"**{orig_units}" if orig_units else ""          # SVC06 left empty, SVC07 = units billed
    out = f"SVC*HC:{code}{':' + mods if mods else ''}*{charge}*{paid}**{units}{tail}~"
    if period:
        out += f"DTM*150*{period[0]}~DTM*151*{period[1]}~"
    elif dos:
        out += f"DTM*472*{dos}~"
    out += cas + rem
    return out + (f"AMT*B6*{allowed}~" if allowed else "")


def clm(pcn: str, status: str, charge: str, paid: str, lines: str = "", cfi: str = "12", pos: str = "11", stmt: tuple | str | None = None,
        claim_extra: str = "", moa: str = "", who: str = WHO, payer_ctl: str | None = None, plan: str = "PPO") -> str:
    """One claim. `stmt` is the claim statement date (DTM*232) or (from, to); None leaves both out, as many professional
    remittances do. `claim_extra` goes right after CLP (claim-level CAS); `moa` is the MOA segment's remark list."""
    out = f"CLP*{pcn}*{status}*{charge}*{paid}*0.00*{cfi}*{payer_ctl or 'PC' + pcn[-8:]}*{pos}~{claim_extra}"
    out += f"NM1*QC*1*{who}~NM1*82*2*TEST PROVIDER*****XX*{NPI}~"
    if moa:
        out += f"MOA***{moa}~"
    out += f"REF*CE*{plan}~"
    if isinstance(stmt, tuple):
        out += f"DTM*232*{stmt[0]}~DTM*233*{stmt[1]}~"
    elif stmt:
        out += f"DTM*232*{stmt}~"
    return out + lines


def build_remits(tmp_path, batches: list[tuple[str, list[str]]], payer: str = "ANTHEM", payer_id: str = "ANTHM") -> Store:
    """One remittance file per (payment date, claims) batch, ingested in that order."""
    store = Store(str(tmp_path / "s.sqlite"))
    paths = []
    for n, (pay_date, claims) in enumerate(batches):
        p = tmp_path / f"r{n:04d}.RMT"
        p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{n + 1:09d}", pay_date=pay_date))
        paths.append(str(p))
    store.ingest_835_paths(paths)
    return store


def findings(store: Store, signal: str) -> list[dict]:
    cur = store.conn.execute("SELECT * FROM findings WHERE signal=? AND COALESCE(stale,0)=0", (signal,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
