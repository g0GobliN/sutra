"""The `sutra-mcp` console command: SUTRA's stdio MCP server.

The protocol implementation stays in `scripts/sutra-mcp-server.py`, which
ships with the package, so a checkout and an installed wheel run the same
server. `runner` owns the handover, as it does for the `sutra` command.
"""

from __future__ import annotations

import sys

from . import runner


def main() -> int:
    return runner.run_script(
        "sutra-mcp-server.py", sys.argv[1:],
        missing="sutra-mcp: the server script is missing from this installation")


if __name__ == "__main__":
    raise SystemExit(main())
