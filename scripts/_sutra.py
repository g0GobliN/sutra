"""Load the SUTRA package for the standalone adapters.

The repository root is the plugin package, so a script in ``scripts/`` cannot
use a normal import. One loader, used by the CLI, the MCP server and the hooks,
keeps the path/loader logic in one place.

The package beside the script wins. A bare ``import sutra`` can resolve to a
different checkout — an editable install, a second worktree, an older release —
and an adapter must run the code it ships with rather than whatever happens to
be on ``sys.path``.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_from_root():
    """Import ``sutra`` from this checkout, or ``None`` when it has no package."""
    init = ROOT / "__init__.py"
    if not init.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "sutra", init, submodule_search_locations=[str(ROOT)])
    if spec is None or spec.loader is None:  # pragma: no cover
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sutra"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # A half-imported package must not linger: the fallback import below
        # would otherwise return a module whose submodules never loaded.
        for name in [n for n in sys.modules if n == "sutra" or n.startswith("sutra.")]:
            del sys.modules[name]
        return None
    return module


def load_sutra():
    """Import the package once per process, or return the loaded module."""
    if "sutra" in sys.modules:
        return sys.modules["sutra"]
    module = _load_from_root()
    if module is not None:
        return module
    # No package beside the script (an installed adapter, or a partial
    # checkout): fall back to whatever the interpreter's path resolves.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import sutra
    return sutra
