"""Run the M-Pesa MCP server."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    """Run the MCP server from the project checkout."""

    from app.mcp.server import run

    # run() builds the server from the environment through create_mcp_server(), which
    # enforces app.startup.enforce_startup_safety before any provider is constructed.
    run()


if __name__ == "__main__":
    main()
