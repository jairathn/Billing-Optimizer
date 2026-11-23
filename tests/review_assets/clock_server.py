"""The dashboard on a local server whose clock (the one the practice's day is read from, runtime._utc_now) stands at a
fixed moment, with a fake fax line, for the page check in tests/test_hotfix2c.py. Invented data only.

    python clock_server.py <port> <ISO time with its zone>      (REMITWATCH_DB and the login come from the environment)
"""
import os
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from remitwatch import fax, runtime  # noqa: E402
from tests.fake_fax import FakeLine  # noqa: E402

port, when = int(sys.argv[1]), datetime.fromisoformat(sys.argv[2])
runtime._utc_now = lambda: when
line = FakeLine()
fax.provider = lambda env=None: line
fax.configured = lambda env=None: True

import uvicorn  # noqa: E402

uvicorn.run("remitwatch.api:app", host="127.0.0.1", port=port, log_level="warning")
