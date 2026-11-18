"""The distribution check must list exactly the modules this tree ships.

`scripts/verify_dist.py` compares a built wheel against `RUNTIME_MODULES`. That
list is written by hand, so a new root module can ship while the check still
passes the old set — or fail the build with "unexpected" entries, which is how
this test came to exist. Reading the script and the tree together keeps the two
in step without building a wheel.
"""

from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERIFY = ROOT / "scripts" / "verify_dist.py"


def _module() -> object:
    spec = importlib.util.spec_from_file_location("verify_dist", VERIFY)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_runtime_modules_match_the_root_modules() -> None:
    verify = _module()
    root_modules = {path.stem for path in ROOT.glob("*.py")}
    assert set(verify.RUNTIME_MODULES) == root_modules


def test_runtime_scripts_exist() -> None:
    verify = _module()
    for name in verify.RUNTIME_SCRIPTS:
        matches = list(ROOT.rglob(name))
        assert matches, f"{name} is listed as a runtime script but is missing"
