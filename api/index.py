"""
Vercel entry point: exposes the RemitWatch FastAPI app as the one function behind every address (see vercel.json).

On Vercel nothing written to disk survives and no process outlives a request, so the app runs in its serverless mode:
the store is kept in a private folder of the practice's Dropbox and each update is one time-boxed run of
/api/cron/cycle (remitwatch/cloud.py). Setup: docs/VERCEL_SETUP.md, and the live checklist at /setup.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from remitwatch.api import app  # noqa: E402,F401
