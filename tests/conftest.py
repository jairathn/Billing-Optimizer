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
