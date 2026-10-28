"""The `sutra-mcp` console command: SUTRA's stdio MCP server.

The protocol implementation stays in scripts/sutra-mcp-server.py, which ships
with the package, so a checkout and an installed wheel run the same server.
"""

from __future__ import annotations

import pathlib
import runpy
import sys


def main() -> int:
    script = pathlib.Path(__file__).resolve().parent / "scripts" / "sutra-mcp-server.py"
    if not script.is_file():
        print("sutra-mcp: the server script is missing from this installation", file=sys.stderr)
        return 1
    sys.argv = [str(script), *sys.argv[1:]]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
