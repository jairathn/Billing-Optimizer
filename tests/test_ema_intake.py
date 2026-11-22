"""
EMA's A/R and Production reports wired into the cycle on both hosts: recognition before the weekly exports (Step 0), the
once-per-host clean-up of report rows an older build read as exports, the report files the Dropbox position passed, the
server cycle with its posting ledger, the serverless cycle (one Production file per run, the attempt written before the
read, the save order, the repair of a run that died between two saves), the upload hand-off through the intake folder
(remitwatch.intake) and the de-duplication guarantees (amendments 11.2). Every row is invented (tests/ema_fixtures.py);
a local folder stands in for Dropbox.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import os
import sqlite3
import time

import pytest

from remitwatch import cloud, dropbox_sync, ema_exports, ema_ledger as el, ema_reports as er, intake, pipeline
from remitwatch.state import DropboxState
from remitwatch.store import Store
from tests import ema_fixtures as F
from tests.fakes import FakeDropbox

D = dt.date
DATA = "Trizetto Data"
P = F.PAYERS


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    for k in ("VERCEL", "REMITWATCH_EMA_REPORTS", "REMITWATCH_EMA_CLAIM_URL", "REMITWATCH_EMA_PAYER_ALIASES", "REMITWATCH_EMA_LEDGER",
              "REMITWATCH_EMA_REPORT_KEEP", "REMITWATCH_EMA_RETAIN_DAYS", "DROPBOX_APP_KEY", "DROPBOX_APP_SECRET",
              "DROPBOX_REFRESH_TOKEN", "REMITWATCH_STATE_DIR", "SMTP_HOST", "REPORT_TO", "ALERT_WEBHOOK_URL", "TRIZETTO_SFTP_USER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    monkeypatch.setenv("DROPBOX_ROOT_PATH", f"/{DATA}")


class Log(list):
    def __call__(self, *a):
        self.append(" ".join(str(x) for x in a))


class Dbx(FakeDropbox):
    """The fake Dropbox with the real client's newer calls: a read timeout on downloads, a move inside Dropbox, and
    download() to a file for a server's mirror. It counts the bytes each streamed read handed over."""

    def __init__(self, root: str):
        super().__init__(root)
        self.timeouts: list = []
        self.streamed: list[int] = []

    def download_chunks(self, path_or_id, chunk=4 << 20, timeout=None):
        self.timeouts.append(timeout)
        data, _ = FakeDropbox.download_bytes(self, path_or_id)
        n = 0
        try:
            for i in range(0, len(data), chunk):
                n += len(data[i:i + chunk])
                yield data[i:i + chunk]
        finally:
            self.streamed.append(n)

    def download_bytes(self, path_or_id, timeout=None):
        return FakeDropbox.download_bytes(self, path_or_id)

    def move(self, from_path, to_path):
        src, dst = self._local(from_path), self._local(to_path)
        if not os.path.isfile(src):
            raise dropbox_sync.DropboxNotFound(from_path)
        if os.path.exists(dst):
            raise dropbox_sync.DropboxConflict("to/conflict/file/")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(src, dst)
        return self._entry(dst)

    def download(self, path_or_id, dest):
        data, meta = self.download_bytes(path_or_id)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        return meta


# ------------------------------------------------------------------------------------------------------------- data
def ar_rows(n: int = 6, *, svc: D = D(2026, 10, 5), aging: D = D(2026, 11, 10), first: int = 1) -> list[dict]:
    return [F.ar_row(claim=F.claim_id(i), link=F.link_no(i), svc=svc, aging=aging, current=100.0 + i)
            for i in range(first, first + n)]


def prod_rows(year: int = 2026, n: int = 6, *, month: int = 3, extra_paid: float = 0.0) -> list[dict]:
    """Postings for n claim lines of one service year: a charge two days after the visit, then the payer's payment and
    contractual adjustment three weeks later, three insurers in turn."""
    payers = [P["bcbs"], P["medicare"], P["commercial"]]
    out = []
    for i in range(1, n + 1):
        svc = D(year, month, 1) + dt.timedelta(days=i)
        link = F.link_no(year * 100 + i)
        payer = payers[i % 3]
        out.append(F.production_row(svc=svc, posted=svc + dt.timedelta(days=2), link=link, charges=150.0, payer=payer, primary=payer,
                                    status="Payment Pending"))
        out.append(F.production_row(svc=svc, posted=svc + dt.timedelta(days=21), link=link, ins_paid=90.0 + extra_paid,
                                    contractual=60.0 - extra_paid, check=F.check_no(year * 100 + i), reason="CO-45", payer=payer,
                                    primary=payer))
    return out


def write(path, data: bytes) -> str:
    return F.write(str(path), data)


def sha(path_or_bytes) -> str:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return hashlib.sha256(path_or_bytes).hexdigest()
    with open(path_or_bytes, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def catalog(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT file_id, status FROM ema_report_files")}


def ledger_rows(path: str) -> int:
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    finally:
        c.close()


def open_store(path) -> Store:
    return Store(str(path))


# ======================================================================================================== Step 0
def test_the_two_reports_are_recognised_before_the_weekly_exports():
    assert ema_exports.detect_family(F.AR_COLUMNS) == "ar_report"
    assert ema_exports.detect_family(F.PRODUCTION_COLUMNS) == "production"
    for fam, cols in F.WEEKLY_COLUMNS.items():
        assert ema_exports.detect_family(cols) == fam        # the three weekly exports read as before


def test_the_export_reader_refuses_a_report_before_it_reads_the_file_whole(tmp_path, monkeypatch):
    prod = write(tmp_path / "2026 Production.csv", F.production_csv(prod_rows()))
    ar = write(tmp_path / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    weekly = write(tmp_path / "Claim Status.xlsx", F.weekly_xlsx("claim_status", [["ZZ00T0001C001", "P1", "2026-03-02", "99213",
                                                                                    P["bcbs"], "Denied", None, None, 10, None, None, None]]))

    def whole(*a, **k):
        raise AssertionError("a report was read whole as an export")
    real = ema_exports._raw_rows
    monkeypatch.setattr(ema_exports, "_raw_rows", whole)
    for p in (prod, ar):
        with pytest.raises(ema_exports.IsReport):
            list(ema_exports.iter_rows(p))
    monkeypatch.setattr(ema_exports, "_raw_rows", real)
    assert ema_exports.read_export_detail(weekly)["family"] == "claim_status"
    # a folder: every workbook, and a CSV, zip or gzip only when it is a report
    write(tmp_path / "notes.csv", b"a,b,c\n1,2,3\n")
    write(tmp_path / "prod.zip", F.zipped("2025 Production.csv", F.production_csv(prod_rows(2025))))
    write(tmp_path / "other.zip", F.zipped("readme.txt", b"hello"))
    got = sorted(os.path.basename(p) for p in ema_exports.iter_export_paths(str(tmp_path)))
    assert got == ["2026 Production.csv", "AR 2026.xlsx", "Claim Status.xlsx", "prod.zip"]


def test_wanted_takes_report_files_and_keeps_the_backfill_archive_out(monkeypatch):
    w = lambda n: dropbox_sync.wanted({".tag": "file", "name": n})        # noqa: E731
    assert all(w(n) for n in ("2026 Production(Sheet1).csv", "AR 2026.xlsx", "prod.zip", "prod.csv.gz", "x.tsv", "a.RMT"))
    assert not w("Trizetto.zip") and not w("~$AR 2026.xlsx") and not w("notes.docx")
    monkeypatch.setenv("REMITWATCH_BACKFILL_ZIP", "Archive.zip")
    assert not w("archive.zip") and w("Trizetto.zip")


def test_the_router_reads_reports_first_and_records_what_it_cannot_read(tmp_path, monkeypatch):
    store = open_store(tmp_path / "s.sqlite")
    ar = write(tmp_path / "in" / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    prod = write(tmp_path / "in" / "2026 Production.csv", F.production_csv(prod_rows()))
    weekly = write(tmp_path / "in" / "Claim Status.xlsx", F.weekly_xlsx("claim_status", [["ZZ00T0001C001", "P1", "2026-03-02", "99213",
                                                                                          P["bcbs"], "Denied", None, None, 10, None, None, None]]))
    odd = write(tmp_path / "in" / "Budget.xlsx", _plain_workbook([["Month", "Total"], ["Jan", 5]]))
    log = Log()
    out = dropbox_sync.ingest_new_files(store, [ar, prod, weekly, odd], log=log, origins={ar: {"path": ar}, prod: {"path": prod}})
    cat = catalog(store.conn)
    assert cat[sha(ar)] == "current" and cat[sha(prod)] == "waiting"         # Production waits for the ledger
    assert out["ema"] == 2 and out["ema_changed"] is True                     # the A/R and the weekly export
    assert tuple(store.conn.execute("SELECT kind, status FROM files WHERE sha256=?", (sha(odd),)).fetchone()) == ("ema:unknown", "not_read")
    assert store.conn.execute("SELECT COUNT(*) FROM ema_rows WHERE source_sha256 IN (?, ?)", (sha(ar), sha(prod))).fetchone()[0] == 0
    assert json.loads(store.conn.execute("SELECT origin FROM ema_report_files WHERE file_id=?", (sha(ar),)).fetchone()[0]) == {"path": ar}
    # with the cycle's ledger the Production file is applied; handed over again, nothing changes
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        again = dropbox_sync.ingest_new_files(store, [prod, ar], log=log, ledger=lg)
        assert catalog(store.conn)[sha(prod)] == "current" and ledger_rows(lg.path) == 12
        assert [r["status"] for r in again["ema_reports"]] == ["current", "already"]
    finally:
        lg.close()
    # one of the weekly exports over 60 MB is refused before it is read, and noted
    monkeypatch.setattr(dropbox_sync, "MAX_EXPORT_BYTES", 1000)
    big = write(tmp_path / "in" / "Encounter & Charges.xlsx", F.weekly_xlsx("charge_lines", [["2026-03-02"] + [None] * 20] * 50))
    res = dropbox_sync.ingest_new_files(store, [big], log=log)
    assert res["failed"] == ["Encounter & Charges.xlsx (too large for this report)"]
    assert tuple(store.conn.execute("SELECT kind, status FROM files WHERE sha256=?", (sha(big),)).fetchone()) == ("ema:too_large", "not_read")


def _plain_workbook(rows: list[list]) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    out = io.BytesIO()
    wb.save(out)
    return F.stable_xlsx(out.getvalue())


def test_a_report_error_is_logged_by_its_kind_never_its_message(tmp_path, monkeypatch):
    store = open_store(tmp_path / "s.sqlite")
    ar = write(tmp_path / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))

    def boom(*a, **k):
        raise ValueError("could not convert string to float: 'Testpatient, Alpha'")
    monkeypatch.setattr(er, "ingest_file", boom)
    log = Log()
    out = dropbox_sync.ingest_new_files(store, [ar], log=log)
    assert out["failed"] == ["AR 2026.xlsx"] and out["errors"] == 1
    assert any("EMA report ERROR AR 2026.xlsx: ValueError" in line for line in log)
    assert not any("Testpatient" in line for line in log)


def test_report_reads_have_a_read_timeout_and_a_peek_lets_the_rest_go(monkeypatch):
    class Resp:
        status_code, text, headers = 200, "", {}
        closed = False

        def iter_content(self, n):
            for _ in range(100):
                yield b"x" * n

        def close(self):
            Resp.closed = True

    class Session:
        def __init__(self):
            self.kw = None

        def post(self, url, **kw):
            self.kw = kw
            return Resp()
    s = Session()
    c = dropbox_sync.DropboxClient("k", "s", "r", session=s)
    c._token, c._token_exp = "t", time.time() + 3600
    head = dropbox_sync.peek(c, "id:x", 1024)
    assert len(head) == 1024 and s.kw["timeout"] == (10, 30) and s.kw["stream"] is True and Resp.closed


# ==================================================================================================== migration
def _misread(store: Store, path: str, n: int = 5) -> str:
    """What an older build stored from an A/R report read as a Claim & Claim Status export: rows that name no claim,
    bill, bill item or patient, and a files row of that kind."""
    h = sha(path)
    rows = [{"family": "claim_status", "source_sha256": h, "source_name": os.path.basename(path), "row_index": i,
             "claim_status": "Payment Pending", "coverage_type": "Primary", "charges_cents": 12000 + i,
             "row_fingerprint": hashlib.sha256(f"{h}{i}".encode()).hexdigest()} for i in range(n)]
    store.ingest_ema_rows(rows)
    store.record_file(h, path, kind="ema:claim_status", status="ok", lines=n)
    return h


def test_the_misread_clean_up_runs_once_per_server_and_keeps_a_genuine_export(tmp_path):
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    store = open_store(db)
    ar = write(tmp_path / "mirror" / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    genuine = write(tmp_path / "mirror" / "Claim Status 9-1-26.xlsx", F.genuine_status_export())
    assert dropbox_sync.ingest_new_files(store, [genuine], log=lambda *a: None)["ema"] == 1
    gsha = sha(genuine)
    genuine_rows = store.conn.execute("SELECT COUNT(*) FROM ema_rows WHERE source_sha256=?", (gsha,)).fetchone()[0]
    assert genuine_rows == 23
    bad = _misread(store, ar)
    store.close()
    out = pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    assert out["ema_migrate"]["candidates"] == 1 and out["ema_migrate"]["purged"] == 1     # the genuine export is no candidate
    s = open_store(db)
    try:
        assert s.conn.execute("SELECT COUNT(*) FROM ema_rows WHERE source_sha256=?", (bad,)).fetchone()[0] == 0
        assert s.conn.execute("SELECT COUNT(*) FROM ema_rows WHERE source_sha256=?", (gsha,)).fetchone()[0] == genuine_rows
        assert catalog(s.conn)[bad] == "current"            # re-read from its file as the A/R report, in the same cycle
        assert s.conn.execute("SELECT 1 FROM meta WHERE key='ema:migrated_v1'").fetchone()
    finally:
        s.close()
    again = pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: None)
    assert "ema_migrate" not in again                       # once per host


# ======================================================================================================= server
@pytest.fixture
def server(tmp_path, monkeypatch):
    """A server's store and Dropbox mirror, with a fake Dropbox behind the sync."""
    root = tmp_path / "dbx"
    client = Dbx(str(root))
    (root / DATA).mkdir(parents=True)
    for k, v in (("DROPBOX_APP_KEY", "k"), ("DROPBOX_APP_SECRET", "s"), ("DROPBOX_REFRESH_TOKEN", "r")):
        monkeypatch.setenv(k, v)
    mirror = tmp_path / "data" / "dropbox"
    monkeypatch.setenv("REMITWATCH_DATA_DIR", str(mirror))
    monkeypatch.setattr(dropbox_sync, "DropboxClient", lambda *a, **k: client)
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    return root, client, mirror, db


def run(db, **kw):
    kw.setdefault("email", False)
    kw.setdefault("report", False)
    return pipeline.run_cycle(db, log=lambda *a: None, **kw)


def test_report_files_the_dropbox_position_passed_are_found_by_the_first_wired_cycle(server):
    """An older build moved the Dropbox position past the reports: a CSV was not even fetched, the A/R workbook was fetched
    into the mirror and left unread. The first cycle of this build lists the whole folder once and reads both."""
    root, client, mirror, db = server
    folder = root / DATA / "Trizetto" / "AR and Collections Reports from EMA"
    ar = write(folder / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    prod = write(folder / "2026 Production(Sheet1).csv", F.production_csv(prod_rows()))
    store = open_store(db)
    list(client.list_folder(f"/{DATA}", recursive=True))      # the older build's listing: its position is past both files
    store.conn.execute("INSERT INTO meta (key, value) VALUES ('sync:cursor', ?)", (client.last_cursor,))
    store.conn.commit()
    store.close()
    mirrored = mirror / DATA / "Trizetto" / "AR and Collections Reports from EMA" / "AR 2026.xlsx"
    write(mirrored, open(ar, "rb").read())                     # fetched then, never read
    out = run(db)
    assert out["sync"]["mode"] == "delta" and out["sync"]["downloaded"] == 0
    s = open_store(db)
    try:
        cat = catalog(s.conn)
        assert cat[sha(ar)] == "current" and cat[sha(prod)] == "current"
        assert ledger_rows(el.ledger_path(db)) == 12
        assert s.conn.execute("SELECT 1 FROM meta WHERE key='ema:refind_v1'").fetchone()
    finally:
        s.close()
    calls = dict(client.calls)
    again = run(db)
    assert client.calls.get("list_folder", 0) == calls.get("list_folder", 0)      # once: the next cycles list what changed
    assert again["ingest"] == {"remits": 0, "ema": 0, "claims_837": 0}


def test_the_server_cycle_applies_every_production_file_and_refreshes_before_detection(tmp_path):
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    local = tmp_path / "drop"
    p25 = write(local / "2025 Production.csv", F.production_csv(prod_rows(2025)))
    p26 = write(local / "2026 Production.csv", F.production_csv(prod_rows(2026)))
    ar = write(local / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    out = run(db, sync=False, local=str(local))
    assert out["ingest"]["ema"] == 3
    assert out["ema_refresh"]["part_a"] and out["ema_refresh"]["part_b"]
    lpath = el.ledger_path(db)
    assert ledger_rows(lpath) == 24 and oct(os.stat(lpath).st_mode & 0o777) == "0o600"
    s = open_store(db)
    try:
        cat = catalog(s.conn)
        assert cat[sha(p25)] == cat[sha(p26)] == cat[sha(ar)] == "current"
        assert s.conn.execute("SELECT value FROM meta WHERE key='ema:ledger_seq'").fetchone()[0] == "2"
        assert s.conn.execute("SELECT COUNT(*) FROM ema_lines").fetchone()[0] > 0
        assert s.conn.execute("SELECT 1 FROM meta WHERE key='ema:map_dirty'").fetchone() is None
    finally:
        s.close()
    # nothing new: no file is read again and the ledger is not touched
    again = run(db, sync=False, local=str(local))
    assert again["ingest"]["ema"] == 0 and "ema_refresh" not in again


def _remit_file(folder, n: int) -> str:
    from tests.test_features import claim, svc
    from tests.test_remitwatch import remit
    data = remit(claim(700 + n, svc("99214", "223.65", "110.00", "110.00", "CAS*CO*45*113.65~")), "110.00", seg_count=99,
                 isa_ctrl=f"{900 + n:09d}", pay_date="20261110")
    return write(os.path.join(folder, f"r{n}.RMT"), data)


def test_new_remittances_mark_the_map_for_the_next_detection(tmp_path):
    store = open_store(tmp_path / "s.sqlite")
    dropbox_sync.ingest_new_files(store, [_remit_file(tmp_path, 1)], log=lambda *a: None)
    assert store.conn.execute("SELECT 1 FROM meta WHERE key='ema:map_dirty'").fetchone() is None     # no EMA report yet
    ar = write(tmp_path / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    dropbox_sync.ingest_new_files(store, [ar], log=lambda *a: None)
    store.conn.execute("DELETE FROM meta WHERE key='ema:map_dirty'")
    store.conn.commit()
    out = dropbox_sync.ingest_new_files(store, [_remit_file(tmp_path, 2)], log=lambda *a: None)
    assert out["remits"] == 1
    assert store.conn.execute("SELECT value FROM meta WHERE key='ema:map_dirty'").fetchone()[0] == "1"


def test_cli_backs_up_the_ledger_and_runs_the_ema_reports_commands(tmp_path, capsys):
    from remitwatch import cli
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    local = tmp_path / "drop"
    p26 = write(local / "2026 Production.csv", F.production_csv(prod_rows(2026)))
    run(db, sync=False, local=str(local))
    dest = tmp_path / "backups"
    assert cli.main(["--db", db, "backup", "--dir", str(dest), "--keep", "1"]) == 0
    names = sorted(os.listdir(dest))
    assert len(names) == 2 and names[0].startswith("ema_ledger-") and names[1].startswith("remitwatch-")
    with gzip.open(dest / names[0]) as g:
        assert g.read(16) == b"SQLite format 3\x00"
    capsys.readouterr()
    assert cli.main(["--db", db, "ema-reports", "--status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["ledger"]["rows"] == 12 and [f["status"] for f in status["files"]] == ["current"]
    assert "origin" not in json.dumps(status["files"])
    # --reread: out of the ledger, read again from its kept copy by the next cycle
    fid = sha(p26)
    assert cli.main(["--db", db, "ema-reports", "--reread", fid]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "reread"
    assert ledger_rows(el.ledger_path(db)) == 0
    run(db, sync=False)
    s = open_store(db)
    try:
        assert catalog(s.conn)[fid] == "current" and ledger_rows(el.ledger_path(db)) == 12
    finally:
        s.close()
    assert cli.main(["--db", db, "ema-reports", "--decide", fid, "apply"]) == 1      # only a held report waits for a decision
    capsys.readouterr()
    assert cli.main(["--db", db, "ema-reports", "--summarize"]) == 0


# =================================================================================================== serverless
@pytest.fixture
def vercel(tmp_path, monkeypatch):
    """A serverless host: a fake Dropbox with the data folder and the state folder inside it."""
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    root = tmp_path / "dbx"
    client = Dbx(str(root))
    (root / DATA).mkdir(parents=True)
    state = DropboxState(client, "/RemitWatch-state")
    return root, client, state


def cycle(state, client, **kw):
    kw.setdefault("budget_s", 600)
    return cloud.run_cloud_cycle(state, client, email=False, log=lambda *a: None, **kw)


def state_store(state) -> sqlite3.Connection:
    store, existed = cloud.load_raw(state)
    assert existed
    return store.conn


def test_a_serverless_run_reads_the_ar_and_keeps_production_waiting_until_its_switch_allows(vercel, monkeypatch):
    root, client, state = vercel
    folder = root / DATA / "Reports"
    ar = write(folder / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    write(folder / "2026 Production.zip", F.zipped("2026 Production.csv", F.production_csv(prod_rows())))
    out = cycle(state, client)                                 # the serverless default reads the A/R only
    conn = state_store(state)
    cat = catalog(conn)
    member = sha(F.production_csv(prod_rows()))
    assert cat[sha(ar)] == "current" and cat[member] == "waiting"
    assert state.meta(el.STATE_NAME) is None and out["ingest"]["ema"] == 1
    origin = json.loads(conn.execute("SELECT origin FROM ema_report_files WHERE file_id=?", (member,)).fetchone()[0])
    assert origin["dropbox_id"].endswith("2026 Production.zip") and origin["member"] == "2026 Production.csv"
    # the switch allows Production: the waiting file is fetched by its Dropbox id and read, and detection waits a run
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    out = cycle(state, client)
    assert catalog(state_store(state))[member] == "current" and out["more"] is True and out["deferred"] == "detection"
    assert state.meta(el.STATE_NAME) is not None and out["ema_apply"]["files"] == 1


def test_one_production_file_per_run_the_ledger_saved_first_and_detection_in_the_next_run(vercel, monkeypatch):
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    folder = root / DATA / "Reports"
    z25 = F.production_csv(prod_rows(2025))
    z26 = F.production_csv(prod_rows(2026))
    write(folder / "2025 Production.zip", F.zipped("2025 Production.csv", z25))
    write(folder / "2026 Production.zip", F.zipped("2026 Production.csv", z26))
    saved: list[str] = []
    real_put, real_chunks = state.put, state.put_chunks

    def put(name, data, *a, **k):
        saved.append(name)
        return real_put(name, data, *a, **k)

    def put_chunks(name, chunks):
        saved.append(name)
        return real_chunks(name, chunks)
    monkeypatch.setattr(state, "put", put)
    monkeypatch.setattr(state, "put_chunks", put_chunks)
    first = cycle(state, client)
    assert first["more"] is True and first["deferred"] == "detection" and first["ema_apply"]["files"] == 1
    order = [n for n in saved if n in (el.STATE_NAME, cloud.RAW, cloud.SERVING, cloud.CURSOR)]
    assert order == [el.STATE_NAME, cloud.RAW, cloud.SERVING, cloud.CURSOR]      # the ledger, the store, the copy, the position
    assert saved.index(el.ATTEMPTS_NAME) < saved.index(el.STATE_NAME)            # the attempt before the read
    cat = catalog(state_store(state))
    assert sorted(cat[sha(z)] for z in (z25, z26)) == ["current", "waiting"]
    assert json.loads(state.get(el.ATTEMPTS_NAME) or b"{}") == {}                 # cleared once it was read
    scratch = os.path.join(os.environ["REMITWATCH_TMP"], "remitwatch")
    assert not [f for f in os.listdir(scratch) if f.startswith("ema_ledger")]     # no scratch ledger or staging file left
    second = cycle(state, client)                              # the second file, by its Dropbox id
    assert second["ema_apply"]["files"] == 1
    assert {catalog(state_store(state))[sha(z)] for z in (z25, z26)} == {"current"}
    h = el.LedgerHandle(state, tmp_dir=str(root.parent / "look"))
    try:
        assert h.open().conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 24
    finally:
        h.discard()
    third = cycle(state, client)
    assert "ema_apply" not in third and third["more"] is False
    rev = state.meta(cloud.RAW)["rev"]
    idle = cycle(state, client)
    assert idle["more"] is False and state.meta(cloud.RAW)["rev"] == rev and "ema_apply" not in idle


class Killed(BaseException):
    """The platform stopping a run (out of time or memory): nothing after it runs, nothing is saved."""


def test_the_attempt_is_written_before_the_read_and_a_file_three_runs_could_not_finish_is_held(vercel, monkeypatch):
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    data = F.production_csv(prod_rows())
    write(root / DATA / "Reports" / "2026 Production.zip", F.zipped("2026 Production.csv", data))
    cycle(state, client, full=False)                           # a first run reads it; then a new export arrives
    fid = sha(data)
    assert catalog(state_store(state))[fid] == "current"
    newer = F.production_csv(prod_rows(extra_paid=5.0))
    write(root / DATA / "Reports" / "2026 Production v2.zip", F.zipped("2026 Production v2.csv", newer))
    real = el.apply_production_file

    def killed(*a, **k):
        raise Killed()
    monkeypatch.setattr(el, "apply_production_file", killed)
    for n in (1, 2, 3):
        with pytest.raises(Killed):
            cycle(state, client)
        assert json.loads(state.get(el.ATTEMPTS_NAME))[sha(newer)]["n"] == n      # counted though the run was killed
    monkeypatch.setattr(el, "apply_production_file", real)
    out = cycle(state, client)
    conn = state_store(state)
    row = tuple(conn.execute("SELECT status, status_reason FROM ema_report_files WHERE file_id=?", (sha(newer),)).fetchone())
    assert row == ("held", el.ATTEMPT_HOLD_REASON) and "ema_apply" not in out


def test_a_run_that_died_after_saving_the_ledger_is_repaired_by_the_next(vercel, monkeypatch):
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    data = F.production_csv(prod_rows())
    write(root / DATA / "Reports" / "2026 Production.zip", F.zipped("2026 Production.csv", data))
    cycle(state, client)                                       # the first load: a store in the state folder
    newer = F.production_csv(prod_rows(extra_paid=5.0))
    write(root / DATA / "Reports" / "2026 Production v2.zip", F.zipped("2026 Production v2.csv", newer))
    real_save = cloud.save_raw

    def die(*a, **k):
        raise Killed()
    monkeypatch.setattr(cloud, "save_raw", die)
    with pytest.raises(Killed):
        cycle(state, client)
    monkeypatch.setattr(cloud, "save_raw", real_save)
    conn = state_store(state)                                  # the store as saved before: behind the ledger
    assert el.LedgerHandle(state).behind(conn)
    out = cycle(state, client)
    assert out["ema_repair"]["repaired"] is True
    conn = state_store(state)
    cat = catalog(conn)
    assert cat[sha(newer)] == "current" and cat[sha(data)] in ("replaced", "partly_replaced")
    assert not el.LedgerHandle(state).behind(conn)
    h = el.LedgerHandle(state, tmp_dir=str(root.parent / "look"))
    try:
        assert h.open().conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 12       # read once, never twice
    finally:
        h.discard()


def test_a_raw_production_csv_too_big_for_the_site_waits_and_its_zip_closes_it(vercel, monkeypatch):
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    monkeypatch.setattr(cloud, "MAX_FILE_BYTES", 2000)
    data = F.production_csv(prod_rows(n=12))
    assert len(data) > 2000
    big = write(root / DATA / "Reports" / "2026 Production(Sheet1).csv", data)
    out = cycle(state, client)
    assert out["ingest"]["errors"] == 0 and out["ingest"]["failed"] == [] and out["ingest"].get("ema_waiting") == 1
    assert client.streamed and max(client.streamed) <= er.PEEK_BYTES and client.timeouts[-1] == dropbox_sync.REPORT_TIMEOUT
    conn = state_store(state)
    entry = client._entry(big)
    pid = er.provisional_id(entry["content_hash"])
    assert tuple(conn.execute("SELECT status, status_reason FROM ema_report_files WHERE file_id=?", (pid,)).fetchone()) == \
        ("waiting", cloud.BIG_REPORT_REASON)
    assert cloud.has_new_files(state, client) is False
    write(root / DATA / "Reports" / "2026 Production(Sheet1).zip", F.zipped("2026 Production(Sheet1).csv", data))
    cycle(state, client)
    cat = catalog(state_store(state))
    assert cat[sha(data)] == "current" and cat[pid] == "superseded"


def test_the_first_wired_run_finds_the_reports_its_position_passed(vercel):
    root, client, state = vercel
    folder = root / DATA / "Trizetto" / "AR and Collections Reports from EMA"
    ar = write(folder / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
    list(client.list_folder(f"/{DATA}", recursive=True))       # an older build's position, past the report
    state.put(cloud.CURSOR, json.dumps({"cursor": client.last_cursor, "root": f"/{DATA}"}).encode())
    out = cycle(state, client)
    assert out["ingest"]["mode"].startswith("full (once") and catalog(state_store(state))[sha(ar)] == "current"
    again = cycle(state, client)
    assert again["ingest"]["mode"] == "delta"


# ====================================================================================================== intake
def _pair(folder, name: str, data: bytes, *, gz: bool = False, user: str = "staff", bad_sha: bool = False) -> str:
    uid = hashlib.md5(name.encode() + data[:64]).hexdigest()
    pair = f"{uid}__{name}" + (".gz" if gz else "")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, pair), "wb") as fh:
        fh.write(gzip.compress(data, mtime=0) if gz else data)
    sc = {"name": name, "origin": "upload", "user": user, "sha256": "0" * 64 if bad_sha else sha(data), "size": len(data),
          "gz": gz, "received_at": "2026-11-20T10:00:00+00:00", "report_hint": None}
    with open(os.path.join(folder, pair + ".json"), "w") as fh:
        json.dump(sc, fh)
    return pair


def test_an_upload_handed_to_the_server_is_read_moved_to_done_and_says_what_became_of_it(tmp_path):
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    ready = intake.ready_dir(os.path.dirname(db))
    ar = F.ar_xlsx(ar_rows())
    p_ar = _pair(ready, "AR 2026.xlsx", ar)
    p_bad = _pair(ready, "AR 2025.xlsx", F.ar_xlsx(ar_rows(svc=D(2025, 10, 5))), bad_sha=True)
    with open(os.path.join(ready, "feedface__half.csv"), "wb") as fh:      # still arriving: no sidecar yet
        fh.write(b"x")
    out = run(db, sync=False)
    assert out["intake"]["pairs"] == 2 and out["intake"]["read"] == 1 and out["intake"]["refused"] == 1
    done = intake.done_dir(os.path.dirname(db))
    res = json.load(open(os.path.join(done, p_ar + ".json")))
    assert res["result"]["status"] == "applied" and res["result"]["outcome"] == "current"
    assert res["result"]["file_id"] == sha(ar) and res["user"] == "staff"
    assert res["result"]["lines"][0].startswith("Read as the A/R report")
    bad = json.load(open(os.path.join(done, p_bad + ".json")))
    assert bad["result"]["status"] == "refused" and "changed on the way" in bad["result"]["lines"][0]
    assert sorted(os.listdir(ready)) == ["feedface__half.csv"]
    s = open_store(db)
    try:
        origin = json.loads(s.conn.execute("SELECT origin FROM ema_report_files WHERE file_id=?", (sha(ar),)).fetchone()[0])
        assert origin["upload_id"] and origin["user"] == "staff" and origin["path"] == os.path.join(done, p_ar)
        assert origin["pair"] == p_ar
        assert origin["content_hash"] == dropbox_sync.content_hash_bytes(ar)
        assert s.conn.execute("SELECT via FROM ema_report_files WHERE file_id=?", (sha(ar),)).fetchone()[0] == "upload"
    finally:
        s.close()


def test_no_log_line_names_an_upload_on_either_host(tmp_path, vercel, monkeypatch):
    """A person's file name can carry a patient's name: the readers' lines about an upload name its upload id instead."""
    named = "Doe Jane AR 2026.xlsx"                     # invented
    ar = F.ar_xlsx(ar_rows())
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    pair = _pair(intake.ready_dir(os.path.dirname(db)), named, ar)
    said: list[str] = []
    pipeline.run_cycle(db, sync=False, email=False, report=False, log=lambda *a: said.append(" ".join(map(str, a))))
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    pair2 = _pair(root / "RemitWatch-state" / "intake", "Doe Jane 2026 Production.csv", F.production_csv(prod_rows(2026)), gz=True)
    cloud.run_cloud_cycle(state, client, email=False, budget_s=600, log=lambda *a: said.append(" ".join(map(str, a))))
    assert said and not [s for s in said if "Doe" in s], [s for s in said if "Doe" in s]
    assert any(f"upload {intake.upload_id_of(pair)}" in s for s in said)
    assert any(f"upload {intake.upload_id_of(pair2)}" in s for s in said)


def test_the_how_to_boxes_kept_in_the_state_folder_reach_the_store_once(vercel):
    """The Upload page keeps the practice's "How we pull this in our EMA" boxes in the state folder on Vercel; the run
    copies them into the store (so a move to the server keeps them), and an unchanged copy saves nothing."""
    from remitwatch import uploads
    root, client, state = vercel
    cycle(state, client)
    doc = {"ar": {"text": "Reports, then A/R, all columns", "by": "staff", "at": "2026-11-20T10:00:00+00:00"}}
    state.put(uploads.HOWTO_STATE, json.dumps(doc).encode())
    rev = state.meta(cloud.RAW)["rev"]
    cycle(state, client)
    assert uploads.howto_read_conn(state_store(state)) == doc and state.meta(cloud.RAW)["rev"] != rev
    rev = state.meta(cloud.RAW)["rev"]
    cycle(state, client)
    assert state.meta(cloud.RAW)["rev"] == rev


def test_kick_asks_the_scheduler_for_a_cycle(tmp_path, monkeypatch):
    poked = []
    monkeypatch.setattr(pipeline.SCHEDULER, "is_alive", lambda: True)
    monkeypatch.setattr(pipeline.SCHEDULER, "poke", lambda trigger="webhook": poked.append(trigger))
    assert intake.kick(str(tmp_path)) is True and poked == ["upload"]
    monkeypatch.setattr(pipeline.SCHEDULER, "is_alive", lambda: False)
    monkeypatch.setattr(pipeline.SCHEDULER, "db_path", "")
    started = []
    monkeypatch.setattr(pipeline, "run_once_in_background", lambda db, trigger="button": started.append((db, trigger)))
    assert intake.kick(str(tmp_path)) is False and started == []         # no store there: no cycle that would make one
    db = tmp_path / "remitwatch.sqlite"
    open_store(db).close()
    assert intake.kick(str(tmp_path)) is True and started == [(str(db), "upload")]


def test_uploads_in_the_state_folder_are_read_before_dropbox_one_production_file_per_run(vercel, monkeypatch):
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    folder = root / "RemitWatch-state" / "intake"
    ar = F.ar_xlsx(ar_rows())
    p25, p26 = F.production_csv(prod_rows(2025)), F.production_csv(prod_rows(2026))
    pa = _pair(folder, "AR 2026.xlsx", ar)
    pb = _pair(folder, "2025 Production.csv", p25)
    pc = _pair(folder, "2026 Production.csv", p26)
    first = cycle(state, client)
    assert first["intake"]["read"] == 2 and first["intake"]["remaining"] == 1 and first["more"] is True
    names = {m["name"] for m in state.list("intake")}
    assert f"intake/done/{pa}.json" in names                 # the A/R, and one of the two Production files; the other waits
    waits = [p for p in (pb, pc) if f"intake/{p}.json" in names]
    assert len(waits) == 1 and f"intake/done/{({pb, pc} - set(waits)).pop()}.json" in names
    second = cycle(state, client)
    assert second["intake"]["read"] == 1 and f"intake/done/{waits[0]}.json" in {m["name"] for m in state.list("intake")}
    cat = catalog(state_store(state))
    assert cat[sha(ar)] == cat[sha(p25)] == cat[sha(p26)] == "current"
    done = json.loads(state.get(f"intake/done/{pc}.json"))
    assert done["result"]["status"] == "applied" and done["result"]["rows_applied"] == 12
    origin = json.loads(state_store(state).execute("SELECT origin FROM ema_report_files WHERE file_id=?", (sha(p26),)).fetchone()[0])
    assert origin["state_path"] == f"intake/done/{pc}"


def test_a_kept_upload_copy_goes_once_newer_files_replace_it_whole_on_vercel(vercel, monkeypatch):
    """REMITWATCH_EMA_REPORT_KEEP on Vercel as on the server: the kept copy of an uploaded Production file goes from
    intake/done/ once enough newer files cover its rows; its done sidecar and catalog row stay."""
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    monkeypatch.setenv("REMITWATCH_EMA_REPORT_KEEP", "1")
    folder = root / "RemitWatch-state" / "intake"
    old = F.production_csv(prod_rows(2026))
    pa = _pair(folder, "2026 Production.csv", old, gz=True)
    cycle(state, client)
    assert state.meta(f"intake/done/{pa}") is not None
    new = F.production_csv(prod_rows(2026, extra_paid=5.0))         # the same year exported again, a payment changed
    pb = _pair(folder, "2026 Production again.csv", new, gz=True)
    cycle(state, client)
    cat = catalog(state_store(state))
    assert cat[sha(new)] == "current" and cat[sha(old)] in ("replaced", "superseded")
    assert state.meta(f"intake/done/{pa}") is None and state.meta(f"intake/done/{pa}.json") is not None
    assert state.meta(f"intake/done/{pb}") is not None


def test_an_upload_read_by_a_run_that_died_before_its_saves_stays_in_intake_and_is_read_once(vercel, monkeypatch):
    """The pair moves to intake/done/ only after the store image is saved: a run that dies after saving the ledger leaves
    it in intake/, and the next run repairs the store and finds the file read ('already'), never twice."""
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    cycle(state, client)                                       # the first load: a store in the state folder
    data = F.production_csv(prod_rows())
    pair = _pair(root / "RemitWatch-state" / "intake", "2026 Production.csv", data, gz=True)
    real_save = cloud.save_raw

    def die(*a, **k):
        raise Killed()
    monkeypatch.setattr(cloud, "save_raw", die)
    with pytest.raises(Killed):
        cycle(state, client)
    monkeypatch.setattr(cloud, "save_raw", real_save)
    names = {m["name"] for m in state.list("intake")}
    assert f"intake/{pair}" in names and f"intake/{pair}.json" in names and f"intake/done/{pair}" not in names
    out = cycle(state, client)
    assert out["ema_repair"]["repaired"] is True and out["intake"]["read"] == 1
    names = {m["name"] for m in state.list("intake")}
    assert f"intake/{pair}" not in names and f"intake/done/{pair}.json" in names
    assert json.loads(state.get(f"intake/done/{pair}.json"))["result"]["status"] in ("already", "applied")
    conn = state_store(state)
    assert catalog(conn)[sha(data)] == "current"
    origin, kind = conn.execute("SELECT origin, source_kind FROM ema_report_files WHERE file_id=?", (sha(data),)).fetchone()
    assert json.loads(origin)["state_path"] == f"intake/done/{pair}" and kind == "upload"      # the kept copy is known again
    h = el.LedgerHandle(state, tmp_dir=str(root.parent / "look"))
    try:
        assert h.open().conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0] == 12       # read once, never twice
    finally:
        h.discard()


# ================================================================================================ de-duplication
def test_an_upload_and_the_same_file_in_dropbox_apply_once_whichever_comes_first(server, tmp_path):
    root, client, mirror, db = server
    ar = F.ar_xlsx(ar_rows())
    _pair(intake.ready_dir(os.path.dirname(db)), "AR 2026.xlsx", ar)         # uploaded first
    first = run(db)
    write(root / DATA / "Reports" / "AR 2026 (copy).xlsx", ar)               # then the same bytes in Dropbox
    second = run(db)
    s = open_store(db)
    try:
        assert catalog(s.conn) == {sha(ar): "current"}
        assert s.conn.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0] == 6
    finally:
        s.close()
    assert first["intake"]["read"] == 1 and second["ingest"]["ema"] == 0


def test_the_same_remittance_file_again_is_ignored(tmp_path):
    from tests.test_remitwatch import CLAIM_DOWNCODE, remit
    store = open_store(tmp_path / "s.sqlite")
    data = remit(CLAIM_DOWNCODE, "77.60", seg_count=29)
    a = write(tmp_path / "a" / "x.RMT", data)
    b = write(tmp_path / "b" / "y.RMT", data)
    assert dropbox_sync.ingest_new_files(store, [a], log=lambda *x: None)["remits"] == 1
    assert dropbox_sync.ingest_new_files(store, [b], log=lambda *x: None)["remits"] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_fixture_workbooks_are_the_same_bytes_every_time():
    rows = ar_rows()
    assert F.ar_xlsx(rows) == F.ar_xlsx(rows)
    time.sleep(1.1)                                         # across a second: openpyxl's own save time must not show
    assert F.ar_xlsx(rows) == F.ar_xlsx(list(rows)) and F.production_xlsx(prod_rows()) == F.production_xlsx(prod_rows())


# ================================================================================================== gzip and seam
def _ledger_totals(path: str) -> dict:
    """Insurance paid and charges by payer and posted month, to the cent (what two orders of the same files must agree on)."""
    c = sqlite3.connect(path)
    try:
        return {(r[0], r[1]): (r[2], r[3]) for r in c.execute(
            """SELECT n.name, substr(p.posted, 1, 7), SUM(COALESCE(p.ins_paid, 0)), SUM(COALESCE(p.charges, 0))
               FROM postings p LEFT JOIN names n ON n.id = p.payer GROUP BY 1, 2""")}
    finally:
        c.close()


def test_a_gzipped_upload_is_read_as_it_unpacks_under_its_own_name_on_a_server(tmp_path):
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    data = F.production_csv(prod_rows(2026))
    pair = _pair(intake.ready_dir(os.path.dirname(db)), "2026 Production(Sheet1).csv", data, gz=True)
    out = run(db, sync=False)
    assert out["intake"]["read"] == 1
    s = open_store(db)
    try:
        row = s.conn.execute("SELECT status, source_name, via, source_kind FROM ema_report_files WHERE file_id=?", (sha(data),)).fetchone()
        assert tuple(row) == ("current", "2026 Production(Sheet1).csv", "upload", "upload")
    finally:
        s.close()
    assert ledger_rows(el.ledger_path(db)) == 12
    assert json.load(open(os.path.join(intake.done_dir(os.path.dirname(db)), pair + ".json")))["result"]["status"] == "applied"
    # the same file arrives in the Dropbox mirror later: already read, nothing changes
    again = dropbox_sync.ingest_new_files(open_store(db), [write(tmp_path / "mirror" / "2026 Production(Sheet1).csv", data)],
                                          log=lambda *a: None, ledger=el.Ledger.open(el.ledger_path(db)))
    assert [r["status"] for r in again["ema_reports"]] == ["already"]


def test_a_gzipped_report_in_dropbox_and_one_uploaded_take_the_serverless_slot(vercel, monkeypatch):
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    d26 = F.production_csv(prod_rows(2026))
    write(root / DATA / "Reports" / "2026 Production.csv.gz", gzip.compress(d26, mtime=0))
    out = cycle(state, client)
    assert out["ema_apply"]["files"] == 1 and catalog(state_store(state))[sha(d26)] == "current"
    d25 = F.production_csv(prod_rows(2025))
    pair = _pair(root / "RemitWatch-state" / "intake", "2025 Production.csv", d25, gz=True)
    out = cycle(state, client)
    assert out["intake"]["read"] == 1 and out["ema_apply"]["files"] == 1
    assert catalog(state_store(state))[sha(d25)] == "current"
    assert json.loads(state.get(f"intake/done/{pair}.json"))["result"]["status"] == "applied"


def test_an_upload_first_then_the_same_raw_csv_too_big_for_the_site_is_already_read(vercel, monkeypatch):
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    monkeypatch.setattr(cloud, "MAX_FILE_BYTES", 2000)
    data = F.production_csv(prod_rows(n=12))
    _pair(root / "RemitWatch-state" / "intake", "2026 Production(Sheet1).csv", data, gz=True)
    cycle(state, client)
    write(root / DATA / "Reports" / "2026 Production(Sheet1).csv", data)        # then the same bytes land in Dropbox
    out = cycle(state, client)
    assert out["ingest"].get("ema_waiting") is None and out["ingest"]["errors"] == 0
    cat = catalog(state_store(state))
    assert cat == {sha(data): "current"} and cloud.has_new_files(state, client) is False


def test_the_same_export_saved_again_is_already_and_changes_nothing(tmp_path):
    """(a) Another save time, other line endings: other bytes, the same rows. It is 'already' and changes nothing."""
    store = open_store(tmp_path / "s.sqlite")
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        rows = prod_rows(2026)
        crlf = write(tmp_path / "a" / "2026 Production.csv", F.production_csv(rows, crlf=True))
        lf = write(tmp_path / "b" / "2026 Production.csv", F.production_csv(rows, crlf=False))
        assert sha(crlf) != sha(lf)
        first = er.ingest_file(store, crlf, via="mirror", ledger=lg, log=lambda *a: None)
        seq, totals = lg.seq(), _ledger_totals(lg.path)
        second = er.ingest_file(store, lf, via="mirror", ledger=lg, log=lambda *a: None)
        assert (first["status"], second["status"]) == ("current", "already") and second["duplicate_of"] == sha(crlf)
        assert lg.seq() == seq and _ledger_totals(lg.path) == totals and catalog(store.conn) == {sha(crlf): "current"}
        ar1 = write(tmp_path / "a" / "AR 2026.xlsx", F.ar_xlsx(ar_rows()))
        ar2 = write(tmp_path / "b" / "AR 2026.xlsx", F.ar_xlsx(ar_rows(), created=dt.datetime(2026, 11, 18, 9, 0)))
        assert sha(ar1) != sha(ar2)
        assert er.ingest_file(store, ar1, via="mirror", log=lambda *a: None)["status"] == "current"
        before = store.conn.execute("SELECT COUNT(*), SUM(exposure_cents) FROM ema_ar").fetchone()
        res = er.ingest_file(store, ar2, via="mirror", log=lambda *a: None)
        assert res["status"] == "already" and res["duplicate_of"] == sha(ar1)
        assert tuple(store.conn.execute("SELECT COUNT(*), SUM(exposure_cents) FROM ema_ar").fetchone()) == tuple(before)
        assert catalog(store.conn)[sha(ar1)] == "current" and sha(ar2) not in catalog(store.conn)
    finally:
        lg.close()
        store.close()


def test_a_whole_year_exported_again_replaces_its_box_and_nothing_counts_twice(tmp_path):
    """(b) The 2026 export again a month later: its box replaces the earlier one's, every posting once."""
    store = open_store(tmp_path / "s.sqlite")
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        early = write(tmp_path / "a" / "2026 Production.csv", F.production_csv(prod_rows(2026, n=6)))
        later_rows = prod_rows(2026, n=9)
        later = write(tmp_path / "b" / "2026 Production.csv", F.production_csv(later_rows))
        er.ingest_file(store, early, via="mirror", ledger=lg, log=lambda *a: None)
        res = er.ingest_file(store, later, via="mirror", ledger=lg, log=lambda *a: None)
        assert res["status"] == "current" and res["replaced"] == [(sha(early), "replaced")]
        assert ledger_rows(lg.path) == len(later_rows)
        fresh = el.Ledger.open(str(tmp_path / "fresh.sqlite"))
        other = open_store(tmp_path / "o.sqlite")
        other.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)",
                           (store.conn.execute("SELECT value FROM meta WHERE key='ema:key_salt'").fetchone()[0],))
        other.conn.commit()
        er.ingest_file(other, later, via="mirror", ledger=fresh, log=lambda *a: None)
        assert _ledger_totals(lg.path) == _ledger_totals(fresh.path)
        fresh.close()
        other.close()
    finally:
        lg.close()
        store.close()


def _year_and_slice(tmp_path) -> tuple[str, str]:
    """A service-year export, and a posted-date pull cut from it (the postings of its last weeks: posted scope)."""
    rows = []
    for i in range(1, 11):
        svc = D(2026, 1, 1) + dt.timedelta(days=i)
        link, payer = F.link_no(500 + i), [P["bcbs"], P["medicare"], P["commercial"]][i % 3]
        rows.append(F.production_row(svc=svc, posted=svc + dt.timedelta(days=2), link=link, charges=150.0, payer=payer, primary=payer))
        rows.append(F.production_row(svc=svc, posted=svc + dt.timedelta(days=60), link=link, ins_paid=80.0 + i, contractual=70.0 - i,
                                     check=F.check_no(500 + i), reason="CO-45", payer=payer, primary=payer))
    year = write(tmp_path / "files" / "2026 Production.csv", F.production_csv(rows))
    cut = D(2026, 2, 20)
    late = [r for r in rows if r["Posted Date"] >= cut]
    piece = write(tmp_path / "files" / "Production posted last weeks.csv", F.production_csv(late))
    return year, piece


@pytest.mark.parametrize("order", ["year_then_pull", "pull_then_year"])
def test_posted_date_pulls_and_a_year_file_give_the_same_totals_in_either_order(tmp_path, order):
    """(c) Weekly posted-date pulls after a year file, or a year file after them: the same totals as the year file alone."""
    year, piece = _year_and_slice(tmp_path)
    alone_store, alone = open_store(tmp_path / "alone.sqlite"), el.Ledger.open(str(tmp_path / "alone_ledger.sqlite"))
    salt = alone_store.conn.execute("SELECT value FROM meta WHERE key='ema:key_salt'").fetchone()
    er.ingest_file(alone_store, year, via="mirror", ledger=alone, log=lambda *a: None)
    store, lg = open_store(tmp_path / "s.sqlite"), el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    store.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ema:key_salt', ?)",
                       (alone_store.conn.execute("SELECT value FROM meta WHERE key='ema:key_salt'").fetchone()[0],))
    store.conn.commit()
    try:
        files = [year, piece] if order == "year_then_pull" else [piece, year]
        results = [er.ingest_file(store, f, via="mirror", ledger=lg, log=lambda *a: None) for f in files]
        assert all(r["status"] in ("current", "superseded") for r in results), [r["status"] for r in results]
        assert {r["scope"] for r in results} == {"service", "posted"}
        assert _ledger_totals(lg.path) == _ledger_totals(alone.path) and ledger_rows(lg.path) == ledger_rows(alone.path)
    finally:
        for x in (lg, alone, store, alone_store):
            x.close()
    assert salt is None or salt


def test_each_ar_snapshot_is_dated_the_newest_current_and_older_ones_kept_as_history(tmp_path):
    """(e) Two weekly A/R pulls: the newest is current, the older replaced and kept in the catalog with its date, and the
    item history spans both; the older one again changes nothing."""
    store = open_store(tmp_path / "s.sqlite")
    w1 = write(tmp_path / "a" / "AR 2026.xlsx", F.ar_xlsx(ar_rows(n=6, aging=D(2026, 11, 10))))
    w2_rows = ar_rows(n=5, aging=D(2026, 11, 17))           # one line is no longer listed
    w2 = write(tmp_path / "b" / "AR 2026.xlsx", F.ar_xlsx(w2_rows))
    assert er.ingest_file(store, w1, via="mirror", log=lambda *a: None)["as_of"] == "2026-11-10"
    res = er.ingest_file(store, w2, via="mirror", log=lambda *a: None)
    assert res["status"] == "current" and res["as_of"] == "2026-11-17" and res["replaced"] == [(sha(w1), "replaced")]
    rows = {r[0]: (r[1], r[2]) for r in store.conn.execute("SELECT file_id, status, as_of FROM ema_report_files")}
    assert rows == {sha(w1): ("replaced", "2026-11-10"), sha(w2): ("current", "2026-11-17")}
    items = store.conn.execute("SELECT COUNT(*), SUM(absent_since IS NOT NULL), MIN(first_seen), MAX(last_seen) FROM ema_ar_items").fetchone()
    assert tuple(items) == (6, 1, "2026-11-10", "2026-11-17")
    assert er.ingest_file(store, w1, via="mirror", log=lambda *a: None)["status"] == "already"
    assert store.conn.execute("SELECT COUNT(*) FROM ema_ar").fetchone()[0] == 5
    store.close()


def test_postings_and_ar_rows_from_any_source_go_through_one_seam(tmp_path):
    """Amendment 10: an API pull would hand postings for a stated range, and A/R rows with their date, to the same
    seam the file readers use; each applied batch records where it came from."""
    store = open_store(tmp_path / "s.sqlite")
    lg = el.Ledger.open(str(tmp_path / "ema_ledger.sqlite"))
    try:
        p = write(tmp_path / "x.csv", F.production_csv(prod_rows(2026)))
        rec = er.recognize(p, "x.csv")
        salt = er.key_salt(store.conn)
        postings = list(er.iter_production(p, rec, salt=salt, account=er.Account()))
        res = el.apply_postings(lg, "api:production:2026-03", {"scope": "posted", "posted_lo": "2026-03-01", "posted_hi": "2026-04-30",
                                                                "as_of": "2026-04-30"},
                                postings, source_kind="api", store=store, source_name="EMA API", via="api", log=lambda *a: None)
        assert res["status"] == "current" and res["scope"] == "posted" and res["source_kind"] == "api"
        f = lg.file("api:production:2026-03")
        assert (f["source_kind"], f["posted_lo"], f["posted_hi"], f["rows"]) == ("api", "2026-03-01", "2026-04-30", 12)
        again = el.apply_postings(lg, "api:production:2026-03b", None, postings, source_kind="api", store=store, log=lambda *a: None)
        assert again["status"] == "already" and again["duplicate_of"] == "api:production:2026-03"
        with pytest.raises(ValueError):
            el.apply_postings(lg, "x", None, postings, source_kind="email", store=store)
        a = write(tmp_path / "AR.xlsx", F.ar_xlsx(ar_rows()))
        arec = er.recognize(a, "AR.xlsx")
        ar = list(er.iter_ar(a, arec, salt=salt, account=er.Account()))
        res = er.apply_ar_snapshot(store.conn, "api:ar:2026-11-20", "2026-11-20", ar, source_kind="api", source_name="EMA API")
        assert res["status"] == "current" and res["as_of"] == "2026-11-20"
        assert tuple(store.conn.execute("SELECT source_kind, status FROM ema_report_files WHERE file_id='api:ar:2026-11-20'")
                     .fetchone()) == ("api", "current")
        assert store.conn.execute("SELECT COUNT(*) FROM files WHERE sha256='api:ar:2026-11-20'").fetchone()[0] == 0
        # the file readers record their own kind on what they apply
        fr = er.ingest_file(store, write(tmp_path / "y" / "2025 Production.csv", F.production_csv(prod_rows(2025))), via="mirror",
                            ledger=lg, log=lambda *a: None)
        assert fr["source_kind"] == "file" and lg.file(fr["file_id"])["source_kind"] == "file"
    finally:
        lg.close()
        store.close()


def test_a_file_held_because_it_would_not_fit_the_sites_temporary_space_is_no_failed_attempt(vercel, monkeypatch):
    root, client, state = vercel
    monkeypatch.setenv("REMITWATCH_EMA_REPORTS", "all")
    monkeypatch.setattr(el.Ledger, "_tmp_hold", lambda self, meta, stats, replaced: [("H8", "Too large for this site's temporary space.")])
    data = F.production_csv(prod_rows())
    write(root / DATA / "Reports" / "2026 Production.zip", F.zipped("2026 Production.csv", data))
    out = cycle(state, client)
    assert catalog(state_store(state))[sha(data)] == "held" and "ema_apply" not in out
    assert sha(data) not in json.loads(state.get(el.ATTEMPTS_NAME) or b"{}")
