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
