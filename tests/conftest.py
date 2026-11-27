"""Settings shared by every test.

A site with no REMITWATCH_PASSWORD serves no data, on the server as on Vercel, unless REMITWATCH_ALLOW_OPEN=1 says it runs
on a developer's own machine. Most tests stand for that machine (an invented store and no password), so the run sets it,
for the servers some tests start in a subprocess too. A test of the closed default removes it (monkeypatch.delenv), as
tests/test_cloud.py's `site` fixture does for the deployed Vercel site it stands for.
"""
import os

os.environ.setdefault("REMITWATCH_ALLOW_OPEN", "1")
# the bulk appeal paths are on in tests (production leaves them off until EMA payments are read); tests of the default
# turn it off themselves
os.environ.setdefault("REMITWATCH_BULK_APPEAL", "1")


import pytest  # noqa: E402  (after the settings above, which must hold before any test module imports the app)


@pytest.fixture(autouse=True)
def _own_scratch_folder(tmp_path_factory, monkeypatch):
    """Every test writes the serverless scratch files (runtime.tmp_dir) in a folder of its own, beside its tmp_path, never
    in the machine's shared /tmp/remitwatch: the suite's parallel shards and other checkouts' runs use that one too, and
    one run's serving copy could replace another's mid-test (tests/test_scratch_isolation.py). A test or fixture that sets
    REMITWATCH_TMP itself still decides: it does so after this."""
    monkeypatch.setenv("REMITWATCH_TMP", str(tmp_path_factory.mktemp("scratch")))
