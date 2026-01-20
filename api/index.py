"""
Vercel serverless entry point for DermBill AI.
This file imports the FastAPI app from dermbill.
"""

import sys
from pathlib import Path

# Add the project root to the path so we can import dermbill
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Import the FastAPI app
from dermbill.api import app

# Vercel looks for 'app' by default for ASGI applications
