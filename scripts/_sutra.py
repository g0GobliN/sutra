"""Load the SUTRA package for the standalone adapters.

The repository root is the plugin package, so a script in ``scripts/`` cannot
use a normal import. One loader, used by the CLI, the MCP server and the Claude
Code hook, keeps the path/loader logic in one place.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_NAME = "sutra_adapter_runtime"  # unique: never collide with the test loader


def load_sutra():
    """Import the package once per process, or return the loaded module."""
    if "sutra" in sys.modules:
        return sys.modules["sutra"]
    # Ensure the repo root (package root) is in sys.path for editable installs
    # where the package lives at the repo root (not in a subdirectory).
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import sutra
    return sutra
