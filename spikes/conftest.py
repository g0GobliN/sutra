"""Make the legacy spike collect from non-identifier checkout paths.

The repository root is itself the plugin package. pytest >= 9 imports ancestor
package ``__init__.py`` files while setting up tests; in a worktree whose
directory name is not a Python identifier it falls back to the literal module
name ``__init__``, and the root's relative imports then fail. Register the
already-valid package under that name. Identifier-named checkouts need no
alias, so this only runs when the checkout directory name cannot be a module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if (ROOT / "__init__.py").is_file() and not ROOT.name.isidentifier():
    spec = importlib.util.spec_from_file_location(
        "__init__", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("__init__", module)
    if sys.modules["__init__"] is module:
        spec.loader.exec_module(module)
