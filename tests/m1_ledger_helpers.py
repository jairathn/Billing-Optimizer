"""Helpers for the merge review (m1-fx-ledger)'s tests (invented data only): a store of either kind fed remittance files one at a
time, the shape of tests/test_fx_ledger.World."""
from __future__ import annotations

import json

from remitwatch.detect import REVERSALS_NOT_ON_FILE_META, Detector
from remitwatch.store import Store
from tests.test_remitwatch import remit

OTHER = "ASH*RO****MI*W55500304"
BILL = "CB0000300"


class World:
    def __init__(self, tmp_path, kind: str):
        self.tmp, self.n = tmp_path, 0
        self.store = Store(str(tmp_path / "s.sqlite")) if kind == "file" else Store(":memory:", compact=True)

    def remit(self, claims: list[str], pay_date: str, plb: str = "", payer: str = "ANTHEM", payer_id: str = "ANTHM") -> None:
        self.n += 1
        p = self.tmp / f"r{self.n:04d}.RMT"
        p.write_bytes(remit("".join(claims), "100.00", payer=payer, payer_id=payer_id, seg_count=99, isa_ctrl=f"{self.n:09d}",
                            pay_date=pay_date, plb=plb))
        self.store.ingest_835_paths([str(p)])

    def run(self, run_id: str, today: str | None = None) -> Detector:
        det = Detector(self.store, run_id=run_id, today=today)
        det.run()
        return det

    def rows(self, where: str = "1=1") -> list[dict]:
        return [dict(r) for r in self.store.query(f"SELECT * FROM findings WHERE {where} ORDER BY finding_id")]

    def meta(self, key: str):
        row = self.store.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def not_on_file_cents(self) -> int:
        m = self.meta(REVERSALS_NOT_ON_FILE_META) or {}
        return (m.get("cents") or 0) + (m.get("older_cents") or 0)


def brief(rows: list[dict]) -> list[tuple]:
    return [(r["patient_control_number"], r["procedure_code"], r["signal"], r["status"], r.get("work_state"),
             "line" if r["line_key"] else "claim", r["shortfall_cents"], (r.get("title") or "")[:80]) for r in rows]


def left_on(det, pcn: str) -> int:
    return sum(det.standing.get(l["line_key"], 0) for l in det.lines if l["patient_control_number"] == pcn)
