import json, os, subprocess, sys, time, urllib.request
from tests.test_hotfix2_pages import _store, _port, PW, OWNER, REPO

def test_probe(tmp_path, monkeypatch):
    site = _store(tmp_path, monkeypatch)
    port = _port()
    env = dict(os.environ)
    for k in [k for k in env if k.startswith(("SRFAX_", "DROPBOX_", "TRIZETTO_", "ANTHROPIC_", "MODMED_", "SMTP_"))] + ["REMITWATCH_MODE", "REMITWATCH_DB", "REMITWATCH_STATE_DIR", "VERCEL", "VERCEL_ENV"]:
        env.pop(k, None)
    env.update(site, REMITWATCH_PASSWORD=PW, REMITWATCH_USER="oda", REMITWATCH_OWNER_PASSWORD=OWNER, PYTHONDONTWRITEBYTECODE="1")
    srv = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "remitwatch.api:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1); break
            except OSError:
                time.sleep(0.1)
        run = subprocess.run(["node", os.environ["PROBE_JS"], str(port), "oda", PW], capture_output=True, text=True, timeout=120)
        print("PROBE", run.stdout[-4000:], run.stderr[-1500:])
    finally:
        srv.terminate(); srv.wait(timeout=10)
