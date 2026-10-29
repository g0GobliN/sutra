"""Load the SUTRA package for the standalone adapters.

The repository root is the plugin package, so a script in ``scripts/`` cannot
use a normal import. One loader, used by the CLI, the MCP server and the Claude
Code hook, keeps the path/loader logic in one place.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_NAME = "sutra_adapter_runtime"  # unique: never collide with the test loader


def load_sutra():
    """Import the package once per process, or return the loaded module."""
    if _NAME in sys.modules:
        return sys.modules[_NAME]
    spec = importlib.util.spec_from_file_location(
        _NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = module
    spec.loader.exec_module(module)
    return module
