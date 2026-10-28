"""Run one of the bundled scripts in this process.

`sutra-cli.py` and `sutra-mcp-server.py` ship with the package and are the
single implementations the checkout and the wheel share; the console
commands are thin aliases for them. This module is the one place that decides
how a missing script is reported and how argv is handed over.
"""

from __future__ import annotations

import pathlib
import runpy
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent / "scripts"


def run_script(name: str, argv: list[str], *, missing: str) -> int:
    """Execute `scripts/name` with `argv`; return a process exit code."""
    script = SCRIPTS / name
    if not script.is_file():
        print(missing, file=sys.stderr)
        return 1
    sys.argv = [str(script), *argv]
    runpy.run_path(str(script), run_name="__main__")
