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
    # The scripts share one loader (`scripts/_sutra.py`). Running a file
    # directly puts its directory on sys.path; runpy does not, so the
    # installed console commands restore that here for the duration of the
    # run. Without it `sutra compress` cannot import the shared loader.
    sys.path.insert(0, str(SCRIPTS))
    try:
        runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.path.remove(str(SCRIPTS))
    return 0
