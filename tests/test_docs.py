"""The docs hold to the code, and the repository holds to the public-repo rules.

The docs (README.md, ARCHITECTURE.md, docs/*.md) tell people which settings each host reads, which commands to type, how
big an upload may be and how to restore a backup. Each test here reads a claim from the docs and checks it against the
code, so a doc that goes stale fails here instead of misleading whoever runs the server. The data is invented.

The public-repo rules (the repository is public): no patient data, no personal names, no practice name or initials, no
TriZetto Site ID or tax ID, no live site address. The words themselves cannot be listed here without publishing them, so
the checks are by shape (a tax ID's, a live site's address, a host under ModMed's domains) and, for the words, against a
list kept outside the repository: REMITWATCH_PRIVATE_WORDS names a file with one word or phrase per line, and the test
says only which line of that list was found where.
"""
from __future__ import annotations

import ast
import contextlib
import gzip
import io
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ["README.md", "ARCHITECTURE.md"] + sorted(f"docs/{p.name}" for p in (ROOT / "docs").glob("*.md"))
RUNBOOK, VERCEL, OPERATIONS = "docs/SERVER_RUNBOOK.md", "docs/VERCEL_SETUP.md", "docs/OPERATIONS.md"
# the setting names the app reads that matter to whoever sets up a host
PREFIXES = ("REMITWATCH_", "TRIZETTO_", "SRFAX_", "MODMED_", "DROPBOX_", "SMTP_", "PRACTICE_", "APPEAL_", "ALERT_",
            "REPORT_", "ANTHROPIC_", "CRON_", "VERCEL", "SITE_")


def doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def flat(name: str) -> str:
    """A doc's text with its line breaks and indents taken out, for checking a phrase that a line break may split."""
    return " ".join(doc(name).split())


def fenced(text: str) -> list[str]:
    """The fenced code blocks of a markdown text, in order."""
    return re.findall(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", text, re.M | re.S)


# --------------------------------------------------------------------------- what the app reads
def _module_imports(path: Path) -> set[str]:
    """The remitwatch modules a module imports, anywhere in it (imports inside functions included)."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 1:                 # from . import x / from .x import y
                out |= {a.name for a in node.names} if not mod else {mod.split(".")[0]}
            elif mod == "remitwatch":
                out |= {a.name for a in node.names}
            elif mod.startswith("remitwatch."):
                out.add(mod.split(".")[1])
        elif isinstance(node, ast.Import):
            out |= {a.name.split(".")[1] for a in node.names if a.name.startswith("remitwatch.")}
    return out


def app_modules() -> set[str]:
    """Every remitwatch module the app reaches from its entry points (the web app and the command line). A module that is
    in the tree but not used yet is not counted until something imports it."""
    pkg = ROOT / "remitwatch"
    have = {p.stem for p in pkg.glob("*.py")}
    seen, todo = set(), ["api", "cli"]
    while todo:
        m = todo.pop()
        if m in seen or m not in have:
            continue
        seen.add(m)
        todo += sorted(_module_imports(pkg / f"{m}.py"))
    return seen


SETTING_READ = re.compile(r"""(?:environ(?:\.get)?\(|environ\[|getenv\(|\be\.get\(|\benv\.get\(|\benv\(|setdefault\()\s*["']([A-Z][A-Z0-9_]{2,})["']""")
SETTING_CONST = re.compile(r"""^[A-Z_]*SETTING\s*=\s*["']([A-Z][A-Z0-9_]+)["']""", re.M)


def settings_read() -> dict[str, str]:
    """The settings the app reads: {name: the module that reads it}."""
    out: dict[str, str] = {}
    files = [ROOT / "remitwatch" / f"{m}.py" for m in sorted(app_modules())] + [ROOT / "api" / "index.py"]
    for f in files:
        text = f.read_text(encoding="utf-8")
        for name in SETTING_READ.findall(text) + SETTING_CONST.findall(text):
            if name.startswith(PREFIXES):
                out.setdefault(name, f.relative_to(ROOT).as_posix())
    return out


def _named(name: str, text: str) -> bool:
    if re.search(rf"(?<![A-Z0-9_]){re.escape(name)}(?![A-Z0-9_])", text):
        return True
    return name.startswith("VERCEL") and "`VERCEL*`" in text      # the runbook rules out every Vercel setting at once


def test_the_app_reads_the_settings_this_test_looks_for():
    got = settings_read()
    # a few every host has: if these go missing the pattern above no longer finds what the code reads
    for name in ("REMITWATCH_PASSWORD", "REMITWATCH_FAX_SENDER", "TRIZETTO_SFTP_SERVERLESS", "SRFAX_ACCESS_ID", "MODMED_CLIENT_ID",
                 "REMITWATCH_ALLOW_OPEN", "REMITWATCH_NO_ANSWER_DAYS", "TRIZETTO_SFTP_PASSWORD_DAYS"):
        assert name in got, name


@pytest.mark.parametrize("page", [RUNBOOK, VERCEL])
def test_every_setting_the_app_reads_is_named_for_each_host(page):
    """The server's runbook and the Vercel page each account for every setting: to set, to leave out, or set by the host."""
    text = doc(page)
    missing = {name: where for name, where in settings_read().items() if not _named(name, text)}
    assert not missing, f"{page} does not name these settings the app reads: {missing}"


def _read_anywhere() -> str:
    parts = [p.read_text(encoding="utf-8") for p in (ROOT / "remitwatch").glob("*.py")]
    parts += [(ROOT / f).read_text(encoding="utf-8") for f in ("api/index.py", "docker-compose.yml", "Dockerfile", ".env.example",
                                                                "deploy/Caddyfile", "deploy/aws/configure.sh", "deploy/aws/remitwatch.yaml",
                                                                "deploy/auto-deploy.sh", "deploy/install-auto-deploy.sh")
              if (ROOT / f).exists()]
    return "\n".join(parts)


@pytest.mark.parametrize("page", [RUNBOOK, VERCEL, OPERATIONS, "README.md", "docs/AWS_SETUP.md", "docs/TRIZETTO_REQUEST.md"])
def test_the_settings_the_docs_name_exist(page):
    """A setting named in a doc is one something reads (the app, the image, Caddy, the settings script), so a renamed or
    invented setting cannot sit in the docs."""
    known = _read_anywhere()
    named = set(re.findall(r"`((?:REMITWATCH|TRIZETTO|SRFAX|MODMED|DROPBOX|SMTP|PRACTICE|APPEAL|ALERT)_[A-Z0-9_]+)", doc(page)))
    unknown = sorted(n for n in named if not re.search(rf"(?<![A-Z0-9_]){n}(?![A-Z0-9_])", known))
    assert not unknown, f"{page} names settings nothing reads: {unknown}"


# --------------------------------------------------------------------------- commands
def _cli_help(*args: str) -> str:
    from remitwatch import cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        cli.main([*args, "--help"])
    return out.getvalue()


def cli_commands() -> set[str]:
    text = _cli_help()
    m = re.search(r"\{([a-z0-9,-]+)\}", text)
    assert m, text
    return set(m.group(1).split(","))


def test_every_command_the_docs_give_exists_with_its_options():
    commands = cli_commands()
    bad = []
    for page in DOCS:
        for line in doc(page).splitlines():
            for m in re.finditer(r"remitwatch\.cli(?:\s+--db\s+\S+)?\s+([a-z0-9-]+)((?:\s+(?:\[?--[a-z-]+\]?|[^\s`'\"|;&]+))*)", line):
                cmd = m.group(1)
                if cmd not in commands:
                    bad.append(f"{page}: unknown command {cmd!r} in {line.strip()[:100]}")
                    continue
                flags = re.findall(r"--[a-z][a-z-]*", m.group(2))
                if flags:
                    known = set(re.findall(r"--[a-z][a-z-]*", _cli_help(cmd)))
                    bad += [f"{page}: {cmd} has no {f}" for f in flags if f not in known]
    assert not bad, "\n".join(bad)


def test_the_readme_lists_every_command():
    line = next(ln for ln in doc("README.md").splitlines() if ln.strip().startswith("cli.py"))
    listed = {w.strip() for w in line.split("cli.py", 1)[1].split("|")}
    assert listed == cli_commands(), sorted(listed ^ cli_commands())


# --------------------------------------------------------------------------- the README's layout
def _layout() -> str:
    text = doc("README.md")
    return fenced(text[text.index("## Layout"):])[0]


def test_the_readme_layout_names_every_module_the_app_uses():
    layout = _layout()
    missing = sorted(m for m in app_modules() if not re.search(rf"(?<![A-Za-z0-9_]){m}\.py\b", layout))
    assert not missing, f"README.md's layout leaves out {missing}"


def test_the_readme_layout_names_every_page_script_and_stand_in():
    layout = _layout()
    static_line = next(ln for ln in layout.splitlines() if ln.strip().startswith("static/"))
    names = re.findall(r"[a-z_]+\*?\.(?:js|html)", static_line)
    pats = [re.compile("^" + re.escape(n).replace(r"\*", ".*") + "$") for n in names]
    unlisted = sorted(p.name for p in (ROOT / "remitwatch" / "static").iterdir()
                      if p.suffix in (".js", ".html") and not any(r.match(p.name) for r in pats))
    assert not unlisted, f"README.md's static/ line leaves out {unlisted}"
    tests_line = next(ln for ln in layout.splitlines() if ln.strip().startswith("tests/"))
    fakes = sorted(p.name for p in (ROOT / "tests").glob("fake*.py"))
    assert all(f in tests_line for f in fakes), (fakes, tests_line)


def test_the_readme_says_how_many_schedules_vercel_runs():
    crons = json.loads(doc("vercel.json"))["crons"]
    words = {1: "one daily schedule", 2: "two daily schedules", 3: "three daily schedules"}
    assert words[len(crons)] in _layout()
    for c in crons:                       # and the Vercel page names each address and its time
        minute, hour = c["schedule"].split()[:2]
        assert c["path"] in doc(VERCEL) and f"{int(hour):02d}:{int(minute):02d} UTC" in doc(VERCEL), c


# --------------------------------------------------------------------------- limits
def test_the_upload_limits_in_the_docs_are_the_apps():
    from remitwatch import uploads
    mib = 1024 * 1024
    runbook = flat(RUNBOOK)
    assert f"{uploads.MAX_BYTES // mib} MiB per upload" in runbook
    assert f"{uploads.EXPORT_MAX_BYTES // mib} MiB per EMA export" in runbook
    assert f"{uploads.NOTE_MAX_BYTES // mib} MiB per visit note" in runbook
    caddy = int(re.search(r"@uploads \{\s*max_size (\d+)MiB", doc("deploy/Caddyfile").replace("request_body @uploads", "@uploads")).group(1))
    assert f"{caddy} MiB ({caddy * mib:,} bytes)" in runbook
    assert caddy * mib > uploads.MAX_BYTES        # Caddy lets the app give its own plain answer to a file too large
    ops = flat(OPERATIONS)                         # the person's page, in the app's own words ("at most 240 MB")
    assert f"{uploads.MAX_BYTES // mib} MB" in ops and f"{uploads.EXPORT_MAX_BYTES // mib} MB" in ops
    assert "250 M" not in runbook + ops


# --------------------------------------------------------------------------- the runbook's restore
def _restore_lines() -> tuple[str, str]:
    """The restore's two lines from the runbook: the shell line that puts the copy in place, and the Python check."""
    text = doc(RUNBOOK)
    block = next(b for b in fenced(text[text.index("To restore a copy"):]) if "gunzip" in b)
    shell = re.search(r"app sh -c '([^']+)'", block).group(1)
    check = re.search(r'app python -c "((?:[^"\\]|\\.)+)"', block).group(1).replace('\\"', '"')
    return shell, check


CHILD = r'''
import sqlite3, sys, time
c = sqlite3.connect(sys.argv[1], timeout=60)
c.execute("UPDATE work SET status='dismissed'")         # the mistake the restore is meant to undo
c.execute("DELETE FROM work WHERE id <= 5")
c.commit()
print("committed", flush=True)
time.sleep(600)                                         # still running when the app is stopped
'''


def _rows(db: str):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT COUNT(*), COALESCE(SUM(status='dismissed'), 0) FROM work").fetchone(), \
            c.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        c.close()


def test_the_runbooks_restore_brings_back_the_copy_even_with_a_run_killed_midway(tmp_path):
    """A run killed while the store was open leaves its last changes in remitwatch.sqlite-wal. The runbook's restore line,
    run as written, must give back exactly the copy, not the copy with that run's changes laid over it."""
    if not shutil.which("gunzip") or not shutil.which("sh"):
        pytest.skip("needs gunzip and sh")
    from remitwatch.cli import backup_store
    from remitwatch.store import Store
    data = tmp_path / "data"
    data.mkdir()
    db = str(data / "remitwatch.sqlite")
    s = Store(db)                                   # the app's own store: journal_mode=WAL
    s.conn.execute("CREATE TABLE IF NOT EXISTS work (id INTEGER PRIMARY KEY, status TEXT, note TEXT)")
    s.conn.executemany("INSERT INTO work (status, note) VALUES ('open', ?)", [("invented " * 20,) for _ in range(40)])
    s.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('pipeline:lock', '{\"pid\": 1}')")
    s.conn.commit()
    s.close()
    copy = backup_store(db, str(data / "backups"), keep=7)
    child = subprocess.Popen([sys.executable, "-c", CHILD, db], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "committed"
    finally:
        os.kill(child.pid, signal.SIGKILL)
        child.wait()
        child.stdout.close()
    assert os.path.exists(db + "-wal") and os.path.getsize(db + "-wal") > 0       # the dead run's changes, not in the file
    shell, check = _restore_lines()
    shell = shell.replace("cd /data", f"cd {data}").replace("<file>", os.path.basename(copy))
    subprocess.run(["sh", "-c", shell], check=True)
    assert _rows(db) == ((40, 0), "ok"), "the restore laid the killed run's changes over the copy"
    assert not os.path.exists(data / "restoring.sqlite")
    out = subprocess.run([sys.executable, "-c", check.replace("/data/remitwatch.sqlite", db)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "ok"
    c = sqlite3.connect(db)
    try:
        assert c.execute("SELECT COUNT(*) FROM meta WHERE key LIKE '%:lock'").fetchone()[0] == 0
    finally:
        c.close()


def test_the_restore_unpacks_before_it_removes_anything():
    """A mistyped copy name must change nothing: the old store and its -wal go only once the copy is unpacked."""
    shell, _ = _restore_lines()
    assert shell.index("gunzip") < shell.index("rm -f") < shell.index("mv ")
    assert re.search(r"rm -f \S*remitwatch\.sqlite-wal \S*remitwatch\.sqlite-shm", shell)
    assert "&&" in shell and ";" not in shell


# --------------------------------------------------------------------------- the server's commands
SERVER_PAGES = [RUNBOOK, "docs/AWS_SETUP.md", "docs/TRIZETTO_REQUEST.md", OPERATIONS]
NEEDS_ROOT = re.compile(r"^(docker\s+compose|docker\s|bash\s+/opt|bash\s+/usr|bash\s+deploy/|systemctl\s|git\s+(clone|pull|checkout)|"
                        r"chmod\s|tee\s|dnf\s|flock\s|growpart\s)")


def test_server_commands_in_the_docs_start_with_sudo():
    """The Session Manager user is not root, and Docker and .env are root's: a command shown for the server starts with
    sudo, or is typed in the root shell a block before opened (the restore)."""
    bad = []
    for page in SERVER_PAGES:
        root_shell = False
        for block in fenced(doc(page)):
            lines = [ln.strip() for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("#")]
            in_heredoc = False
            for ln in lines:
                if in_heredoc:
                    in_heredoc = ln != "UNIT"
                    continue
                if "<<'UNIT'" in ln:
                    in_heredoc = True
                if root_shell or ln.startswith(("sudo ", "cd ", "exit", "curl ", "python -m", "pip ", "uvicorn ")):
                    continue
                if NEEDS_ROOT.match(ln):
                    bad.append(f"{page}: {ln[:90]}")
            root_shell = (root_shell and not any(ln == "exit" for ln in lines)) or \
                any(re.match(r"sudo .*flock \S+ bash$", ln) or ln in ("sudo -i", "sudo bash") for ln in lines)
    assert not bad, "\n".join(bad)


def test_no_doc_restarts_the_app_to_apply_a_setting():
    """`docker compose restart` keeps the settings the container was made with: a change to .env needs `up -d`."""
    for page in DOCS:
        for block in fenced(doc(page)):
            assert not re.search(r"compose\s+restart", block), page
    runbook = flat(RUNBOOK)
    assert re.search(r"after changing \.env: the app is made again", runbook)
    assert "`docker compose restart` does not do it" in runbook


# --------------------------------------------------------------------------- faxes, TriZetto and the closed default
def test_the_docs_put_faxes_and_trizetto_on_one_site(monkeypatch):
    from remitwatch import fax, trizetto
    # the code: one sender, the server by default; a Vercel site reads TriZetto only when told to
    assert fax.sender({}) == "server" and fax.sender({"REMITWATCH_FAX_SENDER": "serverles"}) == "none"
    assert not fax.configured({"SRFAX_ACCESS_ID": "1", "SRFAX_ACCESS_PWD": "x", "VERCEL": "1"})
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")
    assert not trizetto.here({}) and trizetto.here({"TRIZETTO_SFTP_SERVERLESS": "1"})
    # the Vercel page: neither recommended there, the switch and the reminder pair named
    rows = {}
    for ln in doc(VERCEL).splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if ln.startswith("| `") and len(cells) >= 3:
            for name in re.findall(r"`([A-Z0-9_]+)`", cells[0]):
                rows[name] = (cells[1], cells[-1])
    for name in ("TRIZETTO_SFTP_USER", "TRIZETTO_SFTP_PASSWORD"):
        what, needed = rows[name]
        assert needed not in ("Yes", "Recommended") and "Site ID." not in what.replace("never the Site ID's own", "")
    for name in ("TRIZETTO_SFTP_SERVERLESS", "TRIZETTO_SFTP_PASSWORD_SET", "TRIZETTO_SFTP_PASSWORD_DAYS", "REMITWATCH_FAX_SENDER"):
        assert name in rows, name
    assert "server" in rows["REMITWATCH_FAX_SENDER"][0] and "default" in rows["REMITWATCH_FAX_SENDER"][0]


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n\s*\n|\n\s*[-*0-9]+[.)]?\s", text) if s.strip()]


def test_no_doc_sends_anyone_to_the_portal_for_remittances():
    """Remittances are asked for in writing and re-sent to RemitWatch's own login: a download in TriZetto's portal can
    send them to ModMed a second time."""
    bad = []
    for page in DOCS:
        for s in _sentences(doc(page)):
            if re.search(r"\bdownload(s|ed|ing)?\b[^.]*\b(from|in) (TriZetto's|the|its) portal", s) and \
                    not re.search(r"\b(never|not|Never|Nobody|nobody|Don't|don't|Do not|no one|No one)\b", s):
                bad.append(f"{page}: {s.strip()[:120]}")
    assert not bad, "\n".join(bad)


def test_no_doc_asks_for_a_second_fax_scheduler_on_the_server():
    """Every server cycle runs the fax step: the docs must not add a cron or an outside scheduler beside it."""
    import inspect

    from remitwatch import cli
    assert "_fax_run(" in inspect.getsource(cli.cmd_cycle)
    for page in (OPERATIONS, RUNBOOK):
        for s in _sentences(doc(page)):
            assert not ("fax-cycle" in s and re.search(r"from cron|crontab", s)), s
            if "outside scheduler" in s:
                assert re.search(r"\b(No|none|not|never)\b", s), s


def test_the_closed_default_is_what_the_docs_say(monkeypatch):
    from remitwatch import api
    monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    monkeypatch.setattr(api, "PASSWORD", "")
    assert api.password_problem() == "not set" and not api.open_allowed()
    monkeypatch.setattr(api, "PASSWORD", "short-one")          # a short one in use keeps working on a server, with a warning
    assert api.password_problem() is None and api.password_warning()
    monkeypatch.setenv("REMITWATCH_MODE", "serverless")        # and locks Vercel
    assert api.password_problem()
    assert "A shorter one already in use keeps working" in flat(RUNBOOK)
    assert "REMITWATCH_ALLOW_OPEN" in flat(RUNBOOK).split("Never on the server:", 1)[1][:200]
    assert "REMITWATCH_ALLOW_OPEN" in flat(VERCEL).split("Not on Vercel:", 1)[1][:200]


def test_the_trizetto_test_keeps_to_the_hold_as_the_request_says(monkeypatch):
    """docs/TRIZETTO_REQUEST.md, step 3: after a refusal nothing signs in with that password for a day, the Test button
    included, and a new password is tried at once."""
    from remitwatch import trizetto
    env = {"TRIZETTO_SFTP_USER": "INVENTED", "TRIZETTO_SFTP_PASSWORD": "not-a-real-one", "TRIZETTO_SFTP_HOST": "sftp.invalid"}
    monkeypatch.setenv("REMITWATCH_MODE", "server")
    conn = sqlite3.connect(":memory:")
    trizetto.remember_refusal(conn, trizetto.Settings.from_env(env), "refused", kind="password")

    def no_sign_in(*a, **kw):
        raise AssertionError("the test signed in while the refusal held")
    monkeypatch.setattr(trizetto, "Session", no_sign_in)
    out = trizetto.check(env=env, conn=conn)
    assert out.get("held") and any(s["detail"].startswith("Not tried now") for s in out["steps"])
    # a new password is not held
    assert trizetto.refusal(conn, trizetto.Settings.from_env(dict(env, TRIZETTO_SFTP_PASSWORD="a-new-one"))) is None
    text = flat("docs/TRIZETTO_REQUEST.md")
    assert "not the Test button either" in text and "A new password is tried at once" in text


# --------------------------------------------------------------------------- the public-repo rules
def tracked_files() -> list[Path]:
    try:
        names = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
        files = [ROOT / n for n in names if n]
    except (OSError, subprocess.CalledProcessError):        # a copy of the tree without git
        files = [p for p in ROOT.rglob("*") if p.is_file() and not {".git", "data", "__pycache__", "node_modules"} & set(p.parts)]
    skip = {".xlsx", ".png", ".jpg", ".gz", ".zip", ".pdf", ".sqlite", ".ico", ".woff", ".woff2", ".pyc"}
    return [p for p in files if p.suffix.lower() not in skip and p.exists()]


def _text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


PLACEHOLDER_HOSTS = re.compile(r"^(<[^>]+>|YOUR-HOST|localhost|127\.0\.0\.1|0\.0\.0\.0|testserver|.*\.example(\.com|\.org)?|example\.(com|org)|"
                               r"site\.example|.*\.invalid|\$\{?[a-z_]+\}?)$", re.I)
# hosts of ModMed's own, named in its public documentation; a host under these domains that is not one of them is
# likely a practice's own tenant
MODMED_PUBLIC = {"sso.ema.md", "fhirmp.mmi.prod.fhir.ema-api.com", "mmapi.ema-api.com", "stage.ema-api.com",
                 "portal.api.modmed.com", "www.modmed.com", "modmed.com"}


def test_no_live_site_address_is_in_the_repository():
    """The live sites' addresses are never written down: docs say <SITE_ADDRESS> or <the address>."""
    bad = []
    for p in tracked_files():
        for m in re.finditer(r"https?://([A-Za-z0-9.<>${}_-]+)(?::\d+)?/(setup\b|upload\b|api/(health|uploads|dropbox|cron|faxes|trizetto)|\.well-known/jwks)", _text(p)):
            host = m.group(1)
            if not PLACEHOLDER_HOSTS.match(host):
                bad.append(f"{p.relative_to(ROOT)}: {host}")
        for m in re.finditer(r"\b[a-z0-9-]+\.vercel\.app\b", _text(p), re.I):
            bad.append(f"{p.relative_to(ROOT)}: a vercel.app address")
    assert not bad, "\n".join(bad)


def test_no_public_ip_address_is_in_the_repository():
    """The live server is never written down by its address either: a bare public IPv4 address in a tracked document or
    settings file is found by its shape (the private, loopback, reserved and documentation ranges of RFC 5737 aside, and
    the section numbers of a standard such as the X12 TR3's). Only the file and line are reported, never the address."""
    import ipaddress
    ipv4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
    docs = ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
    bad = []
    for p in tracked_files():
        rel = str(p.relative_to(ROOT))
        if not (rel.endswith((".md", ".example", ".env", ".txt", ".yml", ".yaml", ".toml", ".json", ".sh")) or rel.startswith("docs/")):
            continue
        for i, line in enumerate(_text(p).split("\n"), 1):
            if "\u00a7" in line or "section" in line.lower() or "TR3" in line:
                continue
            for m in ipv4.finditer(line):
                try:
                    ip = ipaddress.IPv4Address(m.group(1))
                except ValueError:
                    continue
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved or ip.is_multicast \
                        or any(ip in ipaddress.ip_network(n) for n in docs):
                    continue
                bad.append(f"{rel}:{i}: a public IPv4 address")
    assert not bad, "\n".join(bad)


def test_no_ema_tenant_host_is_in_the_repository():
    bad = []
    for p in tracked_files():
        for host in set(re.findall(r"\b(?:[a-z0-9-]+\.)+(?:ema\.md|ema-api\.com|modmed\.com)\b", _text(p), re.I)):
            if host.lower() not in MODMED_PUBLIC:
                bad.append(f"{p.relative_to(ROOT)}: {host}")
    assert not bad, "\n".join(bad)


def _npi_check_digit_ok(n: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed("80840" + n[:-1])):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return (10 - total % 10) % 10 == int(n[-1])


def test_the_docs_hold_no_tax_id_or_npi():
    """A tax ID (two digits, a dash, seven digits) or a number that passes the NPI check digit has no place in the
    repository. Invented tax IDs in tests end in seven zeros."""
    bad = []
    for p in tracked_files():
        text = _text(p)
        for m in re.finditer(r"(?<![\d-])(\d{2}-\d{7})(?![\d-])", text):
            if not m.group(1).endswith("0000000"):
                bad.append(f"{p.relative_to(ROOT)}: a tax ID's shape")
        if p.suffix == ".md":
            for m in re.finditer(r"(?<![\d/:.])([12]\d{9})(?!\d)", text):
                if _npi_check_digit_ok(m.group(1)):
                    bad.append(f"{p.relative_to(ROOT)}: a number that passes the NPI check")
    assert not bad, "\n".join(bad)


def test_the_docs_do_not_name_the_default_login():
    """The default dashboard user name is the practice's initials (an owner decision is pending on changing it). The docs
    send people to /setup for it instead of writing it down."""
    m = re.search(r"""REMITWATCH_USER["'],\s*["']([^"']+)["']""", doc("remitwatch/api.py"))
    assert m, "api.py no longer has a default user name: drop this test"
    word = re.compile(rf"(?<![A-Za-z0-9]){re.escape(m.group(1))}(?![A-Za-z0-9])", re.I)
    bad = [page for page in DOCS if word.search(doc(page))]
    assert not bad, f"these docs write the default user name: {bad}"


def test_private_words_stay_out_of_the_repository():
    """With REMITWATCH_PRIVATE_WORDS naming a file kept outside the repository (one word or phrase per line: names, the
    Site ID, the tax ID, the live address), none of them may appear in a tracked file. Only the line number is reported."""
    path = os.environ.get("REMITWATCH_PRIVATE_WORDS")
    if not path:
        pytest.skip("REMITWATCH_PRIVATE_WORDS is not set (it names a file outside the repository)")
    words = [w.strip() for w in Path(path).read_text(encoding="utf-8").splitlines()]
    pats = [(i + 1, re.compile(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])")) for i, w in enumerate(words) if w and not w.startswith("#")]
    bad = []
    for p in tracked_files():
        if p.resolve() == Path(path).resolve():
            continue
        text = _text(p)
        bad += [f"{p.relative_to(ROOT)}: word on line {i} of the list" for i, pat in pats if pat.search(text)]
    assert not bad, "\n".join(bad)


def test_the_docs_use_no_em_dashes():
    """House style for what we write: plain sentences, no em dashes. The raw research notes (docs/RESEARCH_*.md) are left
    out: they quote their sources word for word, em dashes included."""
    for page in DOCS:
        if not page.startswith("docs/RESEARCH_"):
            assert "—" not in doc(page), page


# --------------------------------------------------------------------------- what the docs say the app does
def test_the_asc_refresh_command_in_the_docs_runs_with_the_scripts_options():
    """OPERATIONS.md's yearly refresh builds the ASC tables from CMS's own files: every option it gives is one
    scripts/build_ascfs.py takes, and the tables say they were checked against CMS's release the docs name."""
    ops = flat(OPERATIONS)
    cmd = re.search(r"`python scripts/build_ascfs\.py ([^`]+)`", ops).group(1)
    flags = set(re.findall(r"--[a-z][a-z0-9-]*", cmd))
    assert {"--cms", "--counties", "--dd1", "--ff", "--released"} <= flags
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_ascfs.py"), "--help"], capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    assert all(f in out.stdout for f in flags | {"--asca"}), sorted(f for f in flags if f not in out.stdout)
    meta = json.loads(doc("remitwatch/cms/asc_meta.json"))
    assert meta.get("checked_against_cms")
    year, month, day = meta["cms_release"].split("-")
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    said = f"{months[int(month) - 1]} {int(day)}, {year}"
    for page in (OPERATIONS, "ARCHITECTURE.md"):
        assert said in flat(page), (page, said)
    assert "from ASCA's workbook (CMS's own columns" not in flat("ARCHITECTURE.md")
    assert "agrees with ASCA's workbook to the cent" not in ops
    assert "CMS's own" in [ln for ln in _layout().splitlines() if ln.strip().startswith("ascrates.py")][0]


def test_the_note_matching_docs_follow_find_note():
    """A note goes into an appeal by itself only when two of the claim's identifiers agree; the member ID is not used."""
    from remitwatch import modmed
    rules = " ".join((modmed.__doc__ or "").split())
    assert "two of the claim's identifiers that agree" in rules and "The member ID is not used" in rules
    for page in (OPERATIONS, "ARCHITECTURE.md"):
        text = flat(page)
        assert "two of the claim's identifiers" in text, page
        assert re.search(r"member ID is not used|The member ID is not used", text), page
        assert "only ever a patient whose family name" not in text and "the right patient only" not in text, page


# the button and label words the docs quote, as the pages show them
PAGE_WORDS = ["It went out as billed: fax it", "Still unpaid in EMA: fax it anyway", "Fax them anyway", "Needs a person to match",
              "Send it to", "For a person", "Test the TriZetto connection", "Test the fax line", "Appeal everything appealable",
              "Appeal by fax", "Not tried now", "Tie a fax that came in to this item", "Before you send it"]


def test_the_words_the_docs_quote_are_on_the_pages():
    pages = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "remitwatch" / "static").iterdir() if p.suffix in (".js", ".html"))
    pages += doc("remitwatch/trizetto.py")              # "Not tried now" is the TriZetto test's own step text
    ops = flat(OPERATIONS)
    for w in PAGE_WORDS:
        assert w in ops or w in flat("docs/TRIZETTO_REQUEST.md"), f"the docs no longer quote {w!r}: drop it here"
        assert w in pages, f"the docs quote {w!r}, which no page shows"


def test_the_fax_docs_follow_the_fax_rules():
    import inspect

    from remitwatch import api, fax
    # a number decides where patient records go: saving or confirming one takes the owner's password where one is set
    for route in (api.fax_payer_save, api.fax_payer_confirm):
        assert inspect.signature(route).parameters["user"].default.dependency is api.require_owner
    ops = flat(OPERATIONS)
    numbers = ops[ops.index("**Payer fax numbers.**"):ops.index("**What happens next, by itself.**")]
    assert "owner's password" in numbers and "Confirm" in numbers and "history" in numbers
    # the follow-up calendar the docs give is the code's
    assert fax.WAIT_DAYS["medicare"] == 60 and fax.WAIT_DAYS["commercial"] == 30 and fax.REPEAT_DAYS == 7
    assert fax.MAX_REQUESTS == 12 and fax.MAX_ATTEMPTS == 3 and fax.PAID_WAIT_DAYS == 30 and api.MAX_READS == 3
    after = ops[ops.index("**What happens next, by itself.**"):]
    for words in ("60 days for Medicare and Medicare Advantage, 30 for commercial plans", "every week", "Twelve unanswered requests",
                  "three tries", "not paid within 30 days"):
        assert words in after, words
    assert "`REMITWATCH_FAX_SENDER` names the site that faxes" in ops
    # a payer's fax is tied by itself only on a number that names the claim, from a number known for that payer
    for page in (OPERATIONS, "ARCHITECTURE.md", "README.md"):
        for s in _sentences(doc(page)):
            assert not re.search(r"\bti(e|es|ed)\b[^.]*else by patient and date of service", s), (page, s)
    assert "only when it came from a number known for that payer" in ops


def test_the_unanswered_claims_docs_read_emas_words_as_the_code_does():
    """OPERATIONS.md names the status words the practice's EMA writes and says how each is read; detect.py decides."""
    from remitwatch import detect
    ops = flat(OPERATIONS)
    settled = ("Resolved/Paid", "Resolved/Other", "Inactive/Voided")
    for w in settled:
        assert f'"{w}"' in ops and detect.EMA_SETTLED.search(w), w
    assert '"Ready for Posting"' in ops and detect.EMA_NOT_SENT.search("Ready for Posting")
    for w in ("Payment Pending", "Invalid"):            # counted as sent: none of the patterns takes them
        assert f'"{w}"' in ops
        assert not any(p.search(w) for p in (detect.EMA_REJECTED, detect.EMA_DENIED, detect.EMA_SETTLED, detect.EMA_GAVE_UP,
                                             detect.EMA_NOT_SENT)), w
    if hasattr(detect, "EMA_PRINT_BATCH"):
        assert '"Batched for Printing"' in ops and detect.EMA_PRINT_BATCH.search("Batched for Printing")
    assert "to be checked against the practice's sample reports" not in ops
    assert "first-filing limit (see the filing window on the item)" in ops


def test_the_move_from_vercel_is_documented_as_it_works():
    from remitwatch import cli
    import inspect
    src = inspect.getsource(cli.cmd_import_cloud)
    assert "journal_left_behind" in src and "drop_waiting" in src
    runbook = flat(RUNBOOK)
    assert "--drop-waiting" in runbook and "journal_left_behind" in runbook and "journal entries still waiting" in runbook
    assert "journal entries still waiting" in inspect.getsource(cli.cmd_compare_cloud)


def test_the_checkpoint_keeps_the_owner_decisions_and_no_personal_remarks():
    text = flat("docs/CONTEXT_CHECKPOINT.md")
    assert "Git history still holds the TriZetto Site ID and the practice's tax ID" in text
    assert "That is the owner's decision" in text
    assert "annoyed" not in text


def test_the_readme_says_where_patient_data_goes():
    """README.md, "PHI": every place patient data leaves the store is named with the agreement that covers it."""
    import inspect

    from remitwatch import fax, uploads
    phi = flat("README.md").split("## PHI", 1)[1]
    # a payer's fax goes to Claude whole, as the PDF it is; the cover sheet names no patient
    assert '"application/pdf"' in inspect.getsource(fax.classify)
    assert "patient" not in inspect.signature(fax.cover_sheet).parameters
    assert "Every fax a payer sends back goes to Claude whole" in phi and "the cover sheet names no patient" in phi
    # uploads stay beside the store (on the server, /data/uploads), and the note review is scrubbed by default
    assert uploads.upload_dir("/data/remitwatch.sqlite") == "/data/uploads" or os.environ.get("REMITWATCH_UPLOAD_DIR")
    assert "`/data/uploads`" in phi and "`/data/backups`" in phi
    for words in ("SRFax (its own BAA", "Anthropic (the practice's BAA", "ModMed EMA", "`APPEAL_EMAIL_DOMAINS`", "`REMITWATCH_NOTE_SCRUB=1`",
                  "`REMITWATCH_NOTE_SEND_SCANS=1`", "never contain patient data"):
        assert words in phi, words
