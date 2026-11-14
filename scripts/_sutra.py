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
    try:
        import sutra
        return sutra
    except Exception:
        # Fallback: load the package __init__ directly by file path when the
        # normal import fails (e.g. under pytest's isolated interpreter).
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "sutra", ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)])
        if spec is None or spec.loader is None:  # pragma: no cover
            raise
        module = importlib.util.module_from_spec(spec)
        sys.modules["sutra"] = module
        spec.loader.exec_module(module)
        return module
