"""Shared test setup: isolated Hermes home, and a loader for the plugin package.

The repository root *is* the plugin (``plugin.yaml`` + ``__init__.py``), so the
package is loaded by path with a package spec: that is what lets ``__init__``
import ``.engine``, ``.store`` and ``.recovery`` exactly as Hermes loads it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_MODULE_NAME = "sutra_plugin"

_SUTRA_ENV = (
    "SUTRA_ENABLED",
    "SUTRA_MAX_BYTES",
    "SUTRA_HEAD_BYTES",
    "SUTRA_TAIL_BYTES",
    "SUTRA_JSON_ITEMS",
    "SUTRA_STATS_FILE",
    "SUTRA_STATS_MAX_BYTES",
    "SUTRA_STORE",
    "SUTRA_STORE_DIR",
    "SUTRA_STORE_TTL_HOURS",
    "SUTRA_STORE_MAX_BYTES",
    "SUTRA_STORE_MAX_ENTRY_BYTES",
    "SUTRA_STORE_MAX_ENTRIES",
    "SUTRA_RECOVERY_MAX_BYTES",
    "SUTRA_RECOVERY_MAX_MATCHES",
)


def load_plugin():
    """Import the plugin as a package, once per interpreter."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(
        _MODULE_NAME, ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _alias_root_package_for_pytest() -> None:
    """Make the repository root's package importable under the name pytest derives.

    pytest >= 9 imports every ancestor package's ``__init__.py`` when setting
    up a test, and derives that import's module name from the directory names
    on disk. The repository root *is* the plugin package (the Hermes loader
    requires it), so a checkout whose directory name is not a valid module
    name — GitHub tarballs and zip archives extract to ``sutra-main`` — fails
    that import and errors every test in the suite. Registering the
    already-loaded plugin under the derived name makes the import resolve to
    the real module instead. In identifier-named checkouts this registers a
    harmless alias.
    """
    parts = []
    current = ROOT
    while (current / "__init__.py").is_file():
        parts.append(current.name)
        current = current.parent
    if not parts:
        return
    module = load_plugin()
    # Identifier-named checkouts: pytest resolves the root package to its
    # dotted directory name ("tooltrim"). Non-identifier checkouts: resolution
    # fails and pytest's fallback imports the file under the literal name
    # "__init__". Register both so either path finds the real module.
    sys.modules.setdefault(".".join(reversed(parts)), module)
    sys.modules.setdefault("__init__", module)


_alias_root_package_for_pytest()


@pytest.fixture(scope="session")
def sutra():
    return load_plugin()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.hermes, and never inherit ambient tuning."""
    for name in _SUTRA_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    yield


@pytest.fixture()
def hermes_home(tmp_path):
    path = tmp_path / "hermes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pytest_report_header(config):
    return f"SUTRA plugin under test: {ROOT}"
