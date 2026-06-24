"""Entry point for `python -m mcp_server`."""
import sys
import os

# Ensure the project root is on the path so relative imports work
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from mcp_server.server import run

run()
