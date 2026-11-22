"""
Tests for remitwatch.runlog: the scrubber every writer goes through, the run manifest and event log on both hosts (a
server's logs folder and a serverless state folder), killed runs, rotation and retention, the store's short copy, the
decision log of automatic changes to items (on the file store and the compact in-memory store), and the privacy test
the owner asked for: a full fixture cycle on each host with invented names, birth dates, member ids and record numbers
planted in the data, after which no byte of any log, manifest or bundle holds one of them. Everything is invented.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remitwatch import cloud, diagnose, pipeline, runlog  # noqa: E402
from remitwatch.alerts import ALERTS_DDL  # noqa: E402
from remitwatch.detect import Detector, ensure_schema  # noqa: E402
from remitwatch.state import DropboxState, LocalState  # noqa: E402
from remitwatch.store import Store  # noqa: E402
from tests import ema_fixtures as F  # noqa: E402
from tests.fakes import FakeDropbox  # noqa: E402
from tests.test_features import claim, clean_visits, svc  # noqa: E402
from tests.test_remitwatch import remit  # noqa: E402

DATA = "Trizetto Data"
# invented people planted in the data; none may reach a log, a manifest or a bundle
LAST, FIRST = "QUILLWORTHA", "ZYXBOURNE"            # an 835 patient
EMA_NAME = "Vexmoorstad, Thaddeusine"              # a patient of EMA's reports
UNI = "Ñandúvezka"                                 # a name with accents (an upload's file name)
MEMBER = "QZX9938271X"                             # an 835 member id
MRN = "MRNQ88213"                                  # an A/R record number
DOB = dt.date(1960, 2, 29)                         # a leap-day birth date
PLANTED = [LAST, FIRST, "Vexmoorstad", "Thaddeusine", UNI, MEMBER, MRN, "1960-02-29", "2/29/1960", "02/29/1960", "19600229",
           "29 Feb 1960"]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "SMTP_HOST", "REPORT_TO", "ALERT_WEBHOOK_URL",
              "TRIZETTO_SFTP_USER", "SRFAX_ACCESS_ID", "VERCEL", "VERCEL_ENV", "REMITWATCH_STATE_DIR", "REMITWATCH_EMA_REPORTS",
              "REMITWATCH_EMA_LEDGER", "ANTHROPIC_API_KEY", "REMITWATCH_COMMIT", "VERCEL_GIT_COMMIT_SHA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path / "scratch"))
    monkeypatch.setenv("DROPBOX_ROOT_PATH", f"/{DATA}")
    from remitwatch import ema_reports as er
    monkeypatch.setattr(er, "CLAIM_HOST_SUFFIX", F.HOST_SUFFIX)
    saved = set(runlog.SCRUB.names)
    runlog.set_clock(None)
    yield
    runlog.SCRUB.names = saved
    runlog.set_clock(None)
    tok = runlog._CURRENT.set(None)
    del tok


def named_claim(i: int, lines: str, last: str = LAST, first: str = FIRST, member: str = MEMBER, status: str = "1") -> str:
    return claim(i, lines, status=status).replace(f"NM1*QC*1*SAMPLE*PAT****MI*M{i:08d}", f"NM1*QC*1*{last}*{first}****MI*{member}")


def denial_files(folder, paid_later: bool = False) -> list[str]:
    """Clean visits for a baseline and one authorization denial for the planted patient; with paid_later, the same
    service paid on a later remittance."""
    os.makedirs(folder, exist_ok=True)
    claims = clean_visits(1, 25, "99214", "223.65", "127.80", cfi="16")
    claims.append(named_claim(300, svc("99214", "223.65", "0.00", None, "CAS*CO*197*223.65~")))
    p1 = os.path.join(folder, "r001.RMT")
    with open(p1, "wb") as fh:
        fh.write(remit("".join(claims), "100.00", seg_count=99, isa_ctrl="000000001", pay_date="20260415"))
    out = [p1]
    if paid_later:
        p2 = os.path.join(folder, "r002.RMT")
        with open(p2, "wb") as fh:
            fh.write(remit(named_claim(300, svc("99214", "223.65", "127.80", "127.80", "CAS*CO*45*95.85~")), "127.80", seg_count=99,
                           isa_ctrl="000000002", pay_date="20260501"))
        out.append(p2)
    return out


def compact_store() -> Store:
    """The serverless store, made the way cloud.load_raw makes a first one: a compact image held in memory."""
    blank = Store(":memory:", compact=True)
    image = blank.to_bytes()
    blank.close()
    store = Store.from_bytes(image)
    ensure_schema(store.conn)
    store.conn.executescript(ALERTS_DDL + cloud.CLOUD_DDL)
    return store


def file_store(tmp_path) -> Store:
    store = Store(str(tmp_path / "data" / "remitwatch.sqlite"))
    ensure_schema(store.conn)
    return store


@pytest.fixture(params=["file", "compact"])
def kind(request):
    return request.param


def make_store(kind: str, tmp_path) -> Store:
    return file_store(tmp_path) if kind == "file" else compact_store()


def all_log_bytes(*roots: str) -> dict[str, bytes]:
    """Every file under the roots, gzip unpacked: what a person reading the logs could see."""
    out = {}
    for root in roots:
        for dp, _d, fs in os.walk(root):
            for f in fs:
                p = os.path.join(dp, f)
                with open(p, "rb") as fh:
                    data = fh.read()
                if p.endswith(".gz"):
                    data = gzip.decompress(data)
                out[p] = data
    return out


def log_tables(conn: sqlite3.Connection) -> dict[str, bytes]:
    """The store's own logs: the short manifests, the decision log and the self-repair log, as text."""
    out = {}
    for t in ("run_log", "item_decisions", "repair_log"):
        try:
            rows = conn.execute(f"SELECT * FROM {t}").fetchall()
        except sqlite3.Error:
            continue
        out[t] = json.dumps([list(r) for r in rows], ensure_ascii=False, default=str).encode("utf-8")
    meta = conn.execute("SELECT value FROM meta WHERE key IN ('repair:state')").fetchall()
    out["meta:repair"] = json.dumps([r[0] for r in meta], ensure_ascii=False).encode()
    return out


def assert_clean(files: dict[str, bytes], words=PLANTED) -> None:
    hits = diagnose.scan_bytes(files, list(words))
    assert not hits, f"planted words found: {hits[:10]}"


# =============================================================================================================== scrubber
@pytest.mark.parametrize("text, gone, kept", [
    ("ValueError: invalid literal for int() with base 10: 'SMITH, JOHN'", ["SMITH", "JOHN"], ["ValueError", "invalid literal"]),
    (f"bad row for patient {FIRST} {LAST} born 02/29/1960, member {MEMBER}, MRN {MRN}", [FIRST, LAST, "1960", MEMBER, MRN], ["bad row for"]),
    (f"{EMA_NAME} owes on 1960-02-29", ["Vexmoorstad", "Thaddeusine", "1960-02-29"], ["owes"]),
    ("DOB: 2/29/1960; MRN=48213; check 123456789 paid $1,234.56 on 16,165 lines", ["2/29/1960", "48213", "123456789"],
     ["$1,234.56", "16,165 lines"]),
    ("March 14, 1961 and 14 March 1961 and 19610314 and 1961-03-14", ["1961", "19610314"], []),
    ("called 502-555-0199 from jane.doe@example.com, see https://x.example/claim/AB12CDEF3G456C001",
     ["502-555-0199", "jane.doe", "AB12CDEF3G456C001", "x.example"], ["called"]),
    (f"EMA ERROR /Trizetto Data/uploads/{UNI} AR 2026.xlsx: bad", [UNI, "/Trizetto Data/uploads"], ["EMA ERROR", ".xlsx"]),
    ("ssn 123-45-6789 and member W123456789", ["123-45-6789", "W123456789"], []),
    ("first name: Zelphine; last name = Orbwick", ["Zelphine", "Orbwick"], []),
    ("KeyError: 'Brindlewood'", ["Brindlewood"], ["KeyError"]),          # one capitalized word: the quote rule alone
])
def test_free_text_loses_every_identifier(text, gone, kept):
    out = runlog.scrub_text(text)
    for g in gone:
        assert g not in out, (g, out)
    for k in kept:
        assert k in out, (k, out)


def test_ids_keys_codes_counts_and_money_stay():
    fid = "0123456789abcdef01234567"
    lk = "abcdef0123456789:1:0001:3:2"
    text = (f"run run-20261122T101700Z-ab12 item {fid} line {lk} CO-45 N822 G2211 99213 at $1,234.56, 14,215 files in 12.5 s "
            "sqlite3.OperationalError: no such column: work_state; UNIQUE constraint failed: findings.finding_id; KeyError: 'status'")
    out = runlog.scrub_text(text)
    for k in ("run-20261122T101700Z-ab12", fid, lk, "CO-45", "N822", "G2211", "$1,234.56", "14,215 files", "12.5 s",
              "no such column: work_state", "findings.finding_id", "'status'", "Anthem"[:0]):
        assert k in out, (k, out)


def test_the_codes_own_words_stay_and_unknown_names_go():
    out = runlog.scrub_text("Anthem Medicare Advantage denied 12 lines; Dropbox files/list_folder failed 503: Service Unavailable; "
                            "EMA Production: held (H6); John Smith; ERROR Zelphine")
    for k in ("Anthem Medicare Advantage", "Service Unavailable", "EMA Production: held", "ERROR"):
        assert k in out, (k, out)
    for g in ("John", "Smith", "Zelphine", "list_folder"):
        assert g not in out, (g, out)


def test_structured_values_keep_a_value_only_where_its_key_and_shape_agree():
    v = runlog.scrub({"at": "2026-11-22T10:17:00+00:00", "finding_id": "0123456789abcdef01234567", "code": "99213", "carc": "CO-45",
                      "payer_group": "Anthem Medicare Advantage", "newest_payment": "2026-10-09", "name": f"{FIRST} {LAST}",
                      "note": f"seen 1960-02-29 for {MEMBER}", "item": f"{LAST}", "n": 5, "cents": -1250, "ok": True, "none": None,
                      "nested": [{"dob": "02/29/1960"}, {"run_id": "run-20261122T101700Z-ab12"}], "raw": b"\x00\x01"})
    assert v["at"] == "2026-11-22T10:17:00+00:00" and v["finding_id"] == "0123456789abcdef01234567" and v["code"] == "99213"
    assert v["carc"] == "CO-45" and v["payer_group"] == "Anthem Medicare Advantage" and v["newest_payment"] == "2026-10-09"
    assert v["n"] == 5 and v["cents"] == -1250 and v["ok"] is True and v["none"] is None and v["raw"] == "<2 bytes>"
    assert v["nested"][1]["run_id"] == "run-20261122T101700Z-ab12"
    text = json.dumps(v)
    for g in (FIRST, LAST, "1960", MEMBER):
        assert g not in text, g


def test_names_the_store_holds_are_learned(tmp_path):
    store = file_store(tmp_path)
    store.ingest_835_paths(denial_files(tmp_path / "feed"))
    runlog.SCRUB.names.clear()
    assert runlog.SCRUB.learn(store.conn) >= 2
    out = runlog.scrub_text(f"could not match {LAST.title()} to a claim; Quillworth is not {FIRST.lower()}")
    assert LAST.title() not in out and FIRST.lower() not in out and "could not match" in out
    store.close()


def test_scrubbing_twice_changes_nothing():
    samples = [f"bad row for patient {FIRST} {LAST} born 02/29/1960", "KeyError: 'finding_id'", "EMA ERROR /a/b/c.xlsx: '<value>'",
               "paid $1,234.56 on 2026-03-02 check 123456789", f"{EMA_NAME} {UNI}", "name: <redacted>"]
    for s in samples:
        once = runlog.scrub_text(s)
        assert runlog.scrub_text(once) == once, (s, once)
    obj = {"detail": samples, "finding_id": "0123456789abcdef01234567", "at": "2026-11-22T10:00:00+00:00"}
    assert runlog.scrub(runlog.scrub(obj)) == runlog.scrub(obj)


def _raise_nested():
    try:
        int(f"{LAST}")
    except ValueError as exc:
        raise RuntimeError(f"row for {FIRST} {LAST} born 1960-02-29 could not be read") from exc


def test_an_error_keeps_its_stack_and_where_but_never_the_values():
    try:
        _raise_nested()
    except RuntimeError as exc:
        info = runlog.error_info(exc)
    assert info["type"] == "RuntimeError" and info["where"].startswith("tests/test_runlog.py:")
    assert any(f["function"] == "_raise_nested" and f["file"] == "tests/test_runlog.py" for f in info["stack"])
    assert info["causes"] and info["causes"][0]["type"] == "ValueError"
    assert_clean({"info": json.dumps(info, ensure_ascii=False).encode()})
    assert "/home/" not in json.dumps(info) and "Billing-Optimizer" not in json.dumps(info)


# ======================================================================================================= the run, server
def test_a_server_run_writes_events_as_it_goes_and_its_manifest_at_the_end(tmp_path):
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    rl = runlog.start_run("cycle", trigger="cron", db_path=db, host="server")
    logs = runlog.logs_dir_for(db)
    day = rl.manifest["started_at"][:10]
    events_file = os.path.join(logs, f"events-{day}.jsonl")
    assert os.path.exists(events_file) and os.path.exists(os.path.join(logs, "open", f"{rl.run_id}.json"))
    with rl.step("ingest", inputs={"files": 3}) as st:
        st.counts["remits"] = 3
    lines = open(events_file, encoding="utf-8").read().splitlines()     # written before the run ends: a killed run leaves them
    assert [json.loads(x)["event"] for x in lines] == ["run_start", "step_start", "step_end"]
    assert all(set(json.loads(x)) == set(runlog.EVENT_KEYS) for x in lines)
    line = rl.finish(out={"ingest": {"remits": 3}})
    assert line == f"RemitWatch run {rl.run_id}: ok ({rl.manifest['seconds']} s)" and rl.finish() == line
    m = json.loads(open(os.path.join(logs, f"runs-{day}.jsonl"), encoding="utf-8").read().splitlines()[-1])
    assert m["run_id"] == rl.run_id and m["outcome"] == "ok" and m["host_mode"] == "server" and m["trigger"] == "cron"
    step = m["steps"][0]
    assert step["step"] == "ingest" and step["ok"] and step["ms"] >= 0 and step["peak_mb"] and step["counts"] == {"remits": 3}
    assert m["code"]["detect_rules"] and m["versions"]["sqlite"] == sqlite3.sqlite_version
    assert not os.path.exists(os.path.join(logs, "open", f"{rl.run_id}.json"))
    idx = json.load(open(os.path.join(logs, "index.json")))
    assert idx["runs"][-1]["run_id"] == rl.run_id and idx["runs"][-1]["outcome"] == "ok"
    assert runlog.current() is None


@pytest.mark.parametrize("backend", ["local", "dropbox"])
def test_a_serverless_run_writes_one_file_per_run_and_prints_nothing(tmp_path, backend, capsys):
    state = LocalState(str(tmp_path / "state")) if backend == "local" else DropboxState(FakeDropbox(str(tmp_path / "dbx")), "/RemitWatch-state")
    rl = runlog.start_run("cycle", trigger="cron", state=state, host="serverless")
    assert state.list("logs") == []                      # an idle start costs no Dropbox call
    rl.log("  downloaded 3 files")
    with rl.step("detect"):
        pass
    assert [m["name"] for m in state.list("logs")] == [f"logs/open/{rl.run_id}.json"]
    line = rl.finish(out={"more": False})
    assert capsys.readouterr().out == ""                  # Vercel's own log gets the one line the caller prints, nothing else
    assert line.startswith(f"RemitWatch run {rl.run_id}: ok")
    names = sorted(m["name"] for m in state.list("logs"))
    day = rl.manifest["started_at"][:10]
    assert names == ["logs/index.json", f"logs/runs/{day}/{rl.run_id}.jsonl.gz"]
    manifest, events = runlog.read_run(runlog.StateSink(state), rl.run_id)
    assert manifest["run_id"] == rl.run_id and manifest["host_mode"] == "serverless" and "tmp" in manifest["memory"]
    assert [e["event"] for e in events] == ["run_start", "log", "step_start", "step_end"]
    assert events[1]["detail"]["text"] == "  downloaded 3 files"


def test_the_open_marker_is_rewritten_at_most_every_twenty_seconds_on_a_serverless_host(tmp_path, monkeypatch):
    state = LocalState(str(tmp_path / "state"))
    puts = []
    real = state.put
    monkeypatch.setattr(state, "put", lambda name, data, **kw: (puts.append(name), real(name, data, **kw))[1])
    rl = runlog.start_run("cycle", state=state, host="serverless")
    for s in ("load", "ingest", "detect", "save"):
        with rl.step(s):
            pass
    assert sum(1 for n in puts if n.startswith("logs/open/")) == 1
    rl.checkpoint("detect", force=True)
    assert sum(1 for n in puts if n.startswith("logs/open/")) == 2
    rl.finish()


def test_a_failing_step_is_logged_once_with_its_stack_and_inputs(tmp_path):
    rl = runlog.start_run("cycle", db_path=str(tmp_path / "data" / "s.sqlite"), host="server")
    with rl.step("ema_waiting", inputs={"file": "abc123def4567890"}, contain=True) as st:
        raise ValueError(f"bad row for {FIRST} {LAST} born 1960-02-29")
    assert isinstance(st.failed, ValueError)
    runlog.caught(st.failed, "ema_waiting")               # the caller's except block logging it again changes nothing
    errs = rl.manifest["errors"]
    assert len(errs) == 1 and errs[0]["contained"] is True and errs[0]["type"] == "ValueError"
    assert errs[0]["stack"] and errs[0]["inputs"] == {"file": "abc123def4567890"} and errs[0]["step"] == "ema_waiting"
    try:
        with rl.step("detect"):
            raise KeyError("work_state")
    except KeyError as exc:
        caught = exc
    assert rl.manifest["errors"][-1]["contained"] is None             # raised to the caller
    runlog.caught(caught, "detect")
    assert rl.manifest["errors"][-1]["contained"] is True and len(rl.manifest["errors"]) == 2
    assert rl.finish() .split(": ")[1].startswith("partial")
    assert_clean({"m": json.dumps(rl.manifest, ensure_ascii=False).encode()})


def test_the_exception_that_ends_a_run_makes_it_failed(tmp_path):
    rl = runlog.start_run("cycle", db_path=str(tmp_path / "data" / "s.sqlite"), host="server")
    try:
        raise MemoryError("detection")
    except MemoryError as exc:
        rl.fail(exc, "detect")
    assert rl.finish().split(": ")[1].startswith("failed")


def test_caught_logs_to_the_run_in_progress_and_is_harmless_without_one(tmp_path):
    assert runlog.caught(ValueError("x"), "step") is None and runlog.event("s", "e") is None
    rl = runlog.start_run("cycle", db_path=str(tmp_path / "data" / "s.sqlite"), host="server")
    try:
        raise OSError(f"cannot open /uploads/{UNI}.pdf")
    except OSError as exc:
        runlog.caught(exc, "intake", inputs={"upload_id": "feedfacefeedface"})
    runlog.event("archive", "retired", counts={"items": 4})
    assert rl.manifest["errors"][0]["step"] == "intake" and rl.events[-1]["event"] == "retired"
    rl.finish()
    assert runlog.caught(ValueError("after"), "x") is None


def test_log_lines_become_events_with_a_level_and_the_cap_keeps_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(runlog, "MAX_EVENTS", 6)
    rl = runlog.start_run("cycle", db_path=str(tmp_path / "data" / "s.sqlite"), host="server")
    for i in range(10):
        rl.log(f"  downloaded {i} files")
    rl.log(f"  EMA ERROR {UNI} AR 2026.xlsx: invalid literal for int() with base 10: '{MEMBER}'")
    rl.log("  WARNING: slow")
    rl.finish()
    levels = [e["level"] for e in rl.events]
    assert len(rl.events) == 8 and levels[-2:] == ["error", "warning"] and rl.dropped == 5
    assert rl.manifest["events"] == {"kept": 8, "dropped": 5, "errors": 1, "warnings": 1}
    assert_clean({"e": json.dumps(rl.events, ensure_ascii=False).encode()})


def test_a_killed_run_is_found_and_recorded_by_the_next_run(tmp_path):
    db = str(tmp_path / "data" / "s.sqlite")
    sink = runlog.sink_for(db_path=db)
    gone_pid = 2 ** 22 + 12345                              # no process has this number here
    sink.write_json("open/run-20261122T090000Z-dead.json", {"run_id": "run-20261122T090000Z-dead", "kind": "cycle", "host_mode": "server",
                                                             "started_at": runlog._iso(), "last_step": "detect", "last_at": runlog._iso(),
                                                             "pid": gone_pid, "host": runlog._host_tag(), "peak_mb": 1400})
    sink.write_json("open/run-20261122T090100Z-live.json", {"run_id": "run-20261122T090100Z-live", "kind": "faxes", "host_mode": "server",
                                                             "started_at": runlog._iso(), "last_at": runlog._iso(), "pid": os.getpid(),
                                                             "host": runlog._host_tag()})
    rl = runlog.start_run("cycle", db_path=db, host="server")
    killed = rl.manifest["sections"]["killed_runs"]
    assert [k["run_id"] for k in killed] == ["run-20261122T090000Z-dead"] and killed[0]["last_step"] == "detect"
    assert any(e["event"] == "previous_run_killed" for e in rl.events)
    idx = runlog.read_index(sink)
    assert {r["run_id"]: r["outcome"] for r in idx}["run-20261122T090000Z-dead"] == "killed"
    assert os.path.exists(os.path.join(sink.root, "open", "run-20261122T090100Z-live.json"))
    rl.finish()


def test_a_serverless_marker_is_killed_only_once_it_is_older_than_any_run(tmp_path):
    state = LocalState(str(tmp_path / "state"))
    sink = runlog.StateSink(state)
    t0 = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)
    sink.write_json("open/run-20261122T100000Z-aaaa.json", {"run_id": "run-20261122T100000Z-aaaa", "host_mode": "serverless",
                                                             "started_at": runlog._iso(t0), "last_at": runlog._iso(t0), "last_step": "save"})
    assert runlog.find_killed_runs(sink, now=t0 + timedelta(seconds=300), host="serverless") == []
    got = runlog.find_killed_runs(sink, now=t0 + timedelta(seconds=runlog.SERVERLESS_STALE_SECONDS + 5), host="serverless")
    assert [k["run_id"] for k in got] == ["run-20261122T100000Z-aaaa"] and state.list("logs/open") == []


def test_daily_files_are_packed_and_old_or_oversized_logs_dropped_with_a_note(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    today = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    for d in (1, 2, 200):
        day = (today - timedelta(days=d)).date().isoformat()
        (logs / f"events-{day}.jsonl").write_text('{"x":1}\n' * 2000)
        (logs / f"runs-{day}.jsonl").write_text('{"run_id":"r"}\n')
    sink = runlog.DirSink(str(logs))
    assert sink.rotate(today.date().isoformat()) == 6
    assert sorted(p.name for p in logs.iterdir() if p.name.endswith(".jsonl")) == []
    runlog.set_clock(today)
    rl = runlog.RunLog("cycle", host="server", sink=sink)
    res = runlog.prune_logs(sink, now=today, run=rl)
    assert res["old"] == 2 and res["over_budget"] == 0
    assert not any("2026-05" in p.name for p in logs.iterdir())
    assert runlog.prune_logs(sink, now=today) == {"skipped": True}       # once a day
    monkeypatch.setattr(runlog.DirSink, "budget", 1)
    res = runlog.prune_logs(sink, now=today, run=rl, force=True)
    assert res["over_budget"] == 4                                    # every day's file but today's (none here)
    assert [e for e in rl.events if e["event"] == "logs_pruned"][-1]["level"] == "warning"


def test_the_store_keeps_a_short_copy_and_learns_the_final_outcome_at_the_next_run(tmp_path, kind):
    store = make_store(kind, tmp_path)
    state = LocalState(str(tmp_path / "state"))
    rl = runlog.start_run("cycle", state=state, conn=store.conn, host="serverless")
    rl.save_to_store(store.conn)                                     # before the store is saved: the outcome so far
    rl.fail(RuntimeError("after the save"))
    rl.finish()                                                      # the final outcome reaches the state folder only
    row = store.conn.execute("SELECT outcome FROM run_log WHERE run_id=?", (rl.run_id,)).fetchone()
    assert row[0] == "ok"
    rl2 = runlog.start_run("cycle", state=state, conn=store.conn, host="serverless")
    assert store.conn.execute("SELECT outcome FROM run_log WHERE run_id=?", (rl.run_id,)).fetchone()[0] == "failed"
    rl2.finish(conn=store.conn)
    assert store.conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0] == 2
    summary = json.loads(store.conn.execute("SELECT summary FROM run_log WHERE run_id=?", (rl2.run_id,)).fetchone()[0])
    assert summary["run_id"] == rl2.run_id and len(json.dumps(summary)) <= 32000


def test_inputs_are_listed_by_hash_and_settings_by_name_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SRFAX_ACCESS_PWD", "s3cret-fax-passw0rd")
    monkeypatch.setenv("REMITWATCH_PASSWORD", "another-secret-value")
    store = file_store(tmp_path)
    rl = runlog.start_run("cycle", db_path=store.db_path, conn=store.conn, host="server")
    paths = denial_files(tmp_path / f"{UNI} feed")
    store.ingest_835_paths(paths)
    rl.finish(conn=store.conn)
    m = rl.manifest
    sha = hashlib.sha256(open(paths[0], "rb").read()).hexdigest()
    assert m["inputs"] == [{"sha256": sha[:12], "size": os.path.getsize(paths[0]), "kind": "835", "result": "ok", "origin": "folder"}]
    assert m["inputs_summary"] == {"835:ok": 1}
    assert "SRFAX_ACCESS_PWD" in m["settings"] and "REMITWATCH_PASSWORD" in m["settings"]
    text = json.dumps(m, ensure_ascii=False)
    assert "s3cret" not in text and "another-secret" not in text and UNI not in text and "r001" not in text
    store.close()


def test_feed_positions_are_fingerprints(tmp_path):
    store = file_store(tmp_path)
    store.conn.execute("INSERT INTO meta (key, value) VALUES ('sync:cursor', 'AAHcursor-opaque-value')")
    store.conn.commit()
    pos = runlog.positions(store.conn)
    assert pos["cursor"] == runlog.fingerprint("AAHcursor-opaque-value") and "opaque" not in json.dumps(pos)
    store.close()


# ========================================================================================================= decision log
def _ingest(store: Store, paths: list[str]) -> None:
    store.ingest_835_paths(paths)


def test_a_closure_by_a_later_payment_is_recorded_with_its_rule_and_evidence(tmp_path, kind):
    store = make_store(kind, tmp_path)
    feed = tmp_path / "feed"
    p1, p2 = denial_files(feed, paid_later=True)
    _ingest(store, [p1])
    Detector(store, "run-20261101T000000Z-aaaa").run()
    before = runlog.snapshot_items(store.conn)
    assert len(before) == 1
    _ingest(store, [p2])
    Detector(store, "run-20261102T000000Z-bbbb").run()
    res = runlog.record_decisions(store.conn, "run-20261102T000000Z-bbbb", before, rule_version="2.11-test")
    assert res["by_kind"] == {"closed": 1} and list(res["closed_by_rule"]) == ["paid_later:later_payment"]
    fid = next(iter(before.rows))
    got = runlog.decisions_for(store.conn, fid)["items"]
    assert len(got) == 1
    d = got[0]
    assert d["rule"] == "paid_later:later_payment" and d["rule_version"] == "2.11-test" and d["kind"] == "closed"
    assert d["change"]["status"] == ["open", "resolved"] and d["change"]["closed_automatically"] == [False, True]
    assert d["change"]["work_state"][1] == "done"
    src = {e["source"]: e for e in d["evidence"]}
    assert src["item"]["line_key"] and src["paid_later"]["amount_cents"] == 12780
    row = json.dumps(store.conn.execute("SELECT * FROM item_decisions").fetchall(), default=str)
    assert "TRACE" not in row and "CB0000300" not in row                    # no check number, no claim id
    assert_clean({"row": row.encode()})
    store.close()


def _findings_table(conn: sqlite3.Connection, rows: list[dict]) -> None:
    ensure_schema(conn)
    conn.execute("DELETE FROM findings")
    for r in rows:
        cols = list(r)
        conn.execute(f"INSERT INTO findings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [r[c] for c in cols])
    conn.commit()


def _item(fid: str, **kw) -> dict:
    base = {"finding_id": fid, "signal": "DENIED_AUTH", "category": "denial", "status": "open", "work_state": "work_now",
            "payer_group": "Anthem / BCBS", "line_key": f"{fid[:16]}:1:0001:1:1", "claim_key": f"{fid[:16]}:1:0001:1",
            "shortfall_cents": 10000, "appeal_deadline": "2026-12-31", "follow_up_on": None, "stale": 0}
    base.update(kw)
    return base


def test_every_kind_of_automatic_change_is_classified(tmp_path, kind):
    store = make_store(kind, tmp_path)
    c = store.conn
    A, B, C, D, E, G, H, I, J, K, L, M = (f"{ch * 4}{n:020x}" for n, ch in enumerate("abcdefabcdef"))
    before_rows = [
        _item(A), _item(B), _item(C, signal="DENIED_CODING"), _item(D), _item(E, status="resolved", auto_note="Paid on a later remittance"),
        _item(G), _item(H, work_state="waiting", follow_up_on="2026-12-01"), _item(I), _item(J), _item(K, follow_up_on="2026-12-01",
                                                                                                    work_state="waiting"),
        _item(L), _item(M)]
    _findings_table(c, before_rows)
    c.execute("INSERT INTO detection_runs (run_id, started_at, finished_at, version) VALUES ('run-1', '2026-11-01T00:00:00+00:00', "
              "'2026-11-01T00:05:00+00:00', '2.11-x')")
    c.commit()
    before = runlog.snapshot_items(c)
    N = "f" * 4 + "0" * 20
    evidence_closed = json.dumps({"archive_rule": {"rule": "paid_before", "action": "close_with_proof", "strength": "proven",
                                                   "proof": [{"source": "835", "line_key": "1111111111111111:1:1:1:1", "amount_cents": 5000,
                                                              "claim": "CB0000099C017", "trace": "TRACE123456"}]}})
    after_rows = [
        _item(A, status="resolved", work_state="done", auto_note="closed by the archive", evidence=evidence_closed),   # closed
        # B gone, no carrier: no longer raised
        _item(N, signal="DENIED_MEDICAL_NECESSITY", line_key=f"{C[:16]}:1:0001:1:1"),     # C's line under a new signal
        _item(D, status="dismissed", work_state="accepted"),                               # hidden by a rule (event below)
        _item(E, status="open", auto_note=None),                                           # reopened: the payment no longer stands
        _item(G, work_state="waiting", follow_up_on="2026-12-15", state_basis="Appealed; the payer answers in 45 days"),
        _item(H, work_state="waiting", follow_up_on="2026-12-02"),                         # a day's drift: not a decision
        _item(I, appeal_deadline="2026-12-01"),                                            # deadline moved
        _item(J, shortfall_cents=10200),                                                   # $2: below the threshold
        _item(K, work_state="work_now", follow_up_on=None),                                # a person's status since the last run
        _item(L, shortfall_cents=25000),                                                   # $150: a value decision
        _item(M, evidence=json.dumps({"folded": [{"finding_id": B}]})),                    # (B would be folded into M; see below)
    ]
    _findings_table(c, after_rows)
    c.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor, note) VALUES (?,?,?,?,?,?,?)",
              (D, runlog._iso(), "hidden", "open", "dismissed", "hide rule", "not expected to pay"))
    c.execute("INSERT INTO finding_events (finding_id, at, kind, from_status, to_status, actor) VALUES (?,?,?,?,?,?)",
              (K, "2026-11-01T09:00:00+00:00", "manual", "in_progress", "in_progress", "a biller"))
    c.commit()
    res = runlog.record_decisions(c, "run-2", before, rules={G: "appealed_waiting"}, rule_version="2.11-x")
    kinds = {r[0]: (r[1], r[2]) for r in c.execute("SELECT finding_id, kind, rule FROM item_decisions")}
    assert kinds[A] == ("closed", "archive_rules:paid_before")
    assert kinds[B] == ("folded", "archive_rules:fold_into")
    assert kinds[C] == ("signal_changed", "detect:signal_changed") and kinds[N] == ("signal_changed", "detect:signal_changed")
    assert kinds[D] == ("hidden", "hide_rule:Anthem / BCBS|DENIED_AUTH")
    assert kinds[E] == ("reopened", "detect:payment_no_longer_stands")
    assert kinds[G] == ("state", "states:appealed_waiting")
    assert kinds[I] == ("deadline", "codes:deadline")
    assert kinds[L] == ("value", "detect:amount")
    assert H not in kinds and J not in kinds and K not in kinds and M not in kinds
    assert res["by_kind"]["after_person"] == 1
    ev = json.loads(c.execute("SELECT evidence FROM item_decisions WHERE finding_id=?", (A,)).fetchone()[0])
    ar = next(e for e in ev if e["source"] == "archive_rules")
    assert ar["rows"] == [{"source": "835", "line_key": "1111111111111111:1:1:1:1", "amount_cents": 5000}]   # no claim id, no trace
    chg = json.loads(c.execute("SELECT change FROM item_decisions WHERE finding_id=?", (C,)).fetchone()[0])
    assert chg == {"signal": ["DENIED_CODING", "DENIED_MEDICAL_NECESSITY"], "finding_id": [C, N]}
    st = runlog.decisions_for(c, G)["items"][0]
    assert {"source": "states", "basis": "Appealed; the payer answers in 45 days"} in st["evidence"]
    assert st["change"]["follow_up_on"] == [None, "2026-12-15"]
    store.close()


def test_a_raised_item_and_a_gone_item_are_recorded(tmp_path):
    store = file_store(tmp_path)
    c = store.conn
    _findings_table(c, [_item("a" * 24)])
    before = runlog.snapshot_items(c)
    _findings_table(c, [_item("b" * 24, signal="NO_ANSWER", line_key=None, claim_key=None)])
    res = runlog.record_decisions(c, "run-3", before)
    assert res["by_kind"] == {"raised": 1, "gone": 1} and res["raised_by_signal"] == {"NO_ANSWER": 1}
    rows = dict(c.execute("SELECT finding_id, rule FROM item_decisions").fetchall())
    assert rows == {"b" * 24: "detect:no_answer", "a" * 24: "detect:no_longer_raised"}
    store.close()


def test_modules_record_their_own_changes(tmp_path):
    store = file_store(tmp_path)
    n = runlog.record_changes(store.conn, "run-4", [
        {"finding_id": "c" * 24, "kind": "state", "rule": "archive:retired", "change": {"work_state": ["work_now", "expired"]},
         "evidence": [{"source": "archive", "basis": f"past every window; patient {FIRST} {LAST}"}]},
        {"finding_id": f"{LAST}", "kind": "state", "rule": "x"}], step="archive", rule_version="e1")
    assert n == 1
    d = runlog.decisions_for(store.conn, "c" * 24)["items"][0]
    assert d["rule"] == "archive:retired" and d["step"] == "archive" and d["change"] == {"work_state": ["work_now", "expired"]}
    assert_clean({"d": json.dumps(d).encode()})
    store.close()


def test_old_rows_leave_the_store(tmp_path):
    store = file_store(tmp_path)
    c = store.conn
    runlog.ensure_schema(c)
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)
    c.executemany("INSERT INTO item_decisions (finding_id, at, run_id, kind, rule) VALUES (?,?,?,?,?)",
                  [("d" * 24, runlog._iso(now - timedelta(days=500)), "r", "state", "x"),
                   ("e" * 24, runlog._iso(now - timedelta(days=100)), "r", "state", "x"),
                   ("e" * 24, runlog._iso(now - timedelta(days=10)), "r", "state", "x")])
    c.execute("INSERT INTO run_log (run_id, started_at) VALUES ('old', ?)", (runlog._iso(now - timedelta(days=300)),))
    c.commit()
    out = runlog.prune_store(c, now=now)
    assert out["old"] == 1 and out["orphans"] == 1 and out["run_log"] == 1
    assert c.execute("SELECT COUNT(*) FROM item_decisions").fetchone()[0] == 1
    store.close()


# ============================================================================== the privacy test: a full cycle on each host
def _report_files(folder) -> list[str]:
    """An A/R workbook and a Production CSV whose patients are the planted ones."""
    os.makedirs(folder, exist_ok=True)
    ar = F.ar_xlsx([F.ar_row(patient=EMA_NAME, mrn=MRN, claim=F.claim_id(7), link=F.link_no(7), denial=40.0, status="Denied",
                             svc=dt.date(2026, 11, 3)), F.ar_row(patient=0, svc=dt.date(2026, 11, 4))])
    prod = F.production_csv([F.production_row(patient=EMA_NAME, dob=DOB, link=F.link_no(7), charges=150.0, svc=dt.date(2026, 3, 2)),
                             F.production_row(patient=f"{LAST}, {FIRST}", dob=DOB, link=F.link_no(8), charges=90.0)])
    p_ar = os.path.join(folder, "AR 2026.xlsx")
    p_prod = os.path.join(folder, "2026 Production(Sheet1).csv")
    with open(p_ar, "wb") as fh:
        fh.write(ar)
    with open(p_prod, "wb") as fh:
        fh.write(prod)
    return [p_ar, p_prod]


def _wired_ema_step(out, key, fn, *a, **kw):
    """pipeline._ema_step as needs_H.md asks for it: the exception is logged (with its stack, scrubbed) before it is
    contained."""
    try:
        return fn(*a, **kw)
    except Exception as exc:        # noqa: BLE001
        runlog.caught(exc, key)
        out[key] = {"error": type(exc).__name__}
        return None


def _poisoned(*_a, log=print, **_kw):
    """A step that fails with the planted values in its log line and in its error message."""
    log(f"  EMA ERROR /uploads/{UNI}/{EMA_NAME}.xlsx: could not read '{MEMBER}' born {DOB.isoformat()}")
    raise ValueError(f"bad row for {FIRST} {LAST} born 02/29/1960, member {MEMBER}, MRN {MRN}")


def test_no_planted_identifier_reaches_any_log_byte_on_a_server(tmp_path, monkeypatch):
    db = str(tmp_path / "data" / "remitwatch.sqlite")
    feed = tmp_path / "feed"
    denial_files(feed)
    _report_files(feed)
    from remitwatch import intake
    ready = intake.ready_dir(os.path.dirname(db))
    os.makedirs(ready)
    up = F.ar_xlsx([F.ar_row(patient=EMA_NAME, mrn=MRN, svc=dt.date(2026, 11, 5))])
    pair = f"{'ab' * 16}__{UNI} AR.xlsx"
    with open(os.path.join(ready, pair), "wb") as fh:
        fh.write(up)
    with open(os.path.join(ready, pair + ".json"), "w") as fh:
        json.dump({"name": f"{UNI} {EMA_NAME} AR.xlsx", "origin": "upload", "user": "staff", "sha256": hashlib.sha256(up).hexdigest(),
                   "size": len(up), "gz": False, "received_at": "2026-11-20T10:00:00+00:00", "report_hint": None}, fh)
    monkeypatch.setattr(pipeline, "_ema_step", _wired_ema_step)
    monkeypatch.setattr(pipeline, "reread_waiting", _poisoned)
    rl = runlog.start_run("cycle", trigger="test", db_path=db, host="server")
    with rl.step("cycle"):
        out = pipeline.run_cycle(db, sync=False, local=str(feed), email=False, report=False, log=rl.log)
    with rl.step("broken", contain=True):
        raise RuntimeError(f"{EMA_NAME} {UNI} {MEMBER} {MRN} 1960-02-29")
    store = Store(db)
    try:
        assert store.conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] >= 1
        assert store.conn.execute("SELECT COUNT(*) FROM ema_report_files").fetchone()[0] >= 2     # the reports were read
        rl.finish(conn=store.conn, out=out)
        assert any(e["type"] == "ValueError" for e in rl.manifest["errors"])                  # the hook logged the contained error
        files = all_log_bytes(runlog.logs_dir_for(db))
        assert any(n.endswith(".jsonl") for n in files)
        files.update(log_tables(store.conn))
    finally:
        store.close()
    res = diagnose.build_bundle(db_path=db, days=2, known=PLANTED)
    files.update({f"bundle:{n}": b for n, b in diagnose.unpack(res["bytes"]).items()})
    files["bundle.zip"] = res["bytes"]
    assert_clean(files)


def test_no_planted_identifier_reaches_any_log_byte_on_a_serverless_host(tmp_path, monkeypatch):
    root = tmp_path / "dbx"
    client = FakeDropbox(str(root))
    state = DropboxState(client, "/RemitWatch-state")
    denial_files(root / DATA)
    _report_files(root / DATA)
    up = F.ar_xlsx([F.ar_row(patient=EMA_NAME, mrn=MRN, svc=dt.date(2026, 11, 5))])
    pair = f"{'cd' * 16}__{UNI} AR.xlsx"
    state.put(f"intake/{pair}", up)
    state.put(f"intake/{pair}.json", json.dumps({"name": f"{UNI} {EMA_NAME} AR.xlsx", "origin": "upload", "user": "staff",
                                                  "sha256": hashlib.sha256(up).hexdigest(), "size": len(up), "gz": False,
                                                  "received_at": "2026-11-20T10:00:00+00:00", "report_hint": None}).encode())

    def poisoned_repair(store, state, log=print):
        try:
            _poisoned(log=log)
        except ValueError as exc:
            runlog.caught(exc, "ema_repair")            # the hook in run_cloud_cycle's except block (needs_H.md)
            raise
    monkeypatch.setattr(cloud, "repair_ledger", poisoned_repair)
    outs = []
    for _ in range(6):
        rl = runlog.start_run("cycle", trigger="test", state=state, host="serverless")
        with rl.step("cycle"):
            out = cloud.run_cloud_cycle(state, client, budget_s=600, email=False, log=rl.log)
        outs.append(out)
        rl.finish(out=out)
        if not out.get("more"):
            break
    assert not outs[-1].get("more") and any("detect" in o for o in outs)
    store, _existed = cloud.load_raw(state)
    try:
        assert store.conn.execute("SELECT COUNT(*) FROM ema_report_files").fetchone()[0] >= 2
        files = all_log_bytes(str(root / "RemitWatch-state" / "logs"))
        assert any(n.endswith(".jsonl.gz") for n in files)
        files.update(log_tables(store.conn))
        res = diagnose.build_bundle(conn=store.conn, state=state, days=2, known=PLANTED)
    finally:
        store.close()
    files.update({f"bundle:{n}": b for n, b in diagnose.unpack(res["bytes"]).items()})
    assert_clean(files)


# ====================================================================================== nothing is swallowed silently
H_MODULES = ["runlog.py", "repair.py", "diagnose.py"]
PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remitwatch")
# the cycle files' silent except blocks, counted when runlog was built: the hook-in adds a runlog.caught to each step
# boundary's block, so these numbers may only go down (lower them when a block starts logging)
SILENT_CEILING = {"pipeline.py": 9, "cloud.py": 11}


def test_runlogs_own_modules_swallow_nothing():
    assert runlog.silent_excepts([os.path.join(PKG, m) for m in H_MODULES]) == []


def test_the_census_finds_a_silent_block_and_passes_one_that_logs(tmp_path):
    p = tmp_path / "m.py"
    p.write_text("def a():\n    try:\n        x()\n    except Exception:\n        pass\n\n"
                 "def b():\n    try:\n        x()\n    except Exception as exc:\n        caught(exc)\n\n"
                 "def c():\n    try:\n        x()\n    except (ValueError, Exception):\n        return None\n\n"
                 "def d():\n    try:\n        x()\n    except ProcessLookupError:\n        return False\n\n"
                 "def e():\n    try:\n        x()\n    except:\n        pass\n")
    got = runlog.silent_excepts([str(p)])
    assert [(g["line"], g["handler"]) for g in got] == [(4, "Exception"), (16, "(ValueError, Exception)"), (28, "(bare)")]


def test_the_cycle_files_silent_blocks_do_not_grow():
    for name, ceiling in SILENT_CEILING.items():
        found = runlog.silent_excepts([os.path.join(PKG, name)])
        assert len(found) <= ceiling, f"{name}: {len(found)} silent except blocks (ceiling {ceiling}): {found}"


# ================================================================================================================ panel
def test_the_panel_lists_runs_repairs_and_the_bundle(tmp_path):
    store = file_store(tmp_path)
    logs = runlog.logs_dir_for(store.db_path)
    rl = runlog.start_run("cycle", db_path=store.db_path, conn=store.conn, host="server")
    with rl.step("detect", contain=True):
        raise ValueError("boom")
    rl.finish(conn=store.conn)
    from remitwatch import repair
    reg = repair.Registry(conn=store.conn, sink=runlog.DirSink(logs))
    reg.failed("unreadable_file", "production:abcdef0123456789", None, reason="held: cut short", hold=True)
    reg.save()
    p = runlog.panel(store.conn, logs)
    assert p["title"] == "Logs and diagnostics" and p["last_run"]["run_id"] == rl.run_id and p["last_run"]["outcome"] == "partial"
    assert p["last_run"]["first_error"]["type"] == "ValueError"
    assert "1 step(s) failed" in p["sentence"] and "could not be repaired automatically" in p["sentence"]
    assert p["open_problems"][0]["target"] == "production:abcdef0123456789" and "Data tab" in p["open_problems"][0]["next_step"]
    assert p["bundle"]["route"] == "/api/ops/diagnostic-bundle" and "—" not in json.dumps(p)
    assert runlog.panel(None)["sentence"] == "No run has been logged yet."
    store.close()
