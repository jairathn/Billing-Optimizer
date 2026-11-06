"""
Vercel serverless entry point: exposes the RemitWatch FastAPI app.

Vercel has no persistent disk, so this only works when REMITWATCH_DB points at a
store that exists in the deployment (for example a read-only copy uploaded with the
build). For the hands-off weekly job and a store that grows, run the container in
deploy/ instead (see docs/OPERATIONS.md).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from remitwatch.api import app  # noqa: E402,F401
