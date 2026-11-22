"""Settings shared by every test.

A site with no REMITWATCH_PASSWORD serves no data, on the server as on Vercel, unless REMITWATCH_ALLOW_OPEN=1 says it runs
on a developer's own machine. Most tests stand for that machine (an invented store and no password), so the run sets it,
for the servers some tests start in a subprocess too. A test of the closed default removes it (monkeypatch.delenv).
tests/test_cloud.py stands for the deployed Vercel site throughout, so its tests run without it.
"""
import os

import pytest

os.environ.setdefault("REMITWATCH_ALLOW_OPEN", "1")


@pytest.fixture(autouse=True)
def _a_deployed_site_is_closed_by_default(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] == "test_cloud":
        monkeypatch.delenv("REMITWATCH_ALLOW_OPEN", raising=False)
