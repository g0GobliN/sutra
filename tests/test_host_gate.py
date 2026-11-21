"""The host's own plugin gate, run against a staged copy of the runtime files.

A registered hook is not a working integration: the host has to load the plugin,
see the middleware the manifest promises, and agree that the registrations match.
Hermes ships that check as ``hermes plugins validate`` — the same gate it runs
before catalog admission, executed in an isolated capability probe.

The test is opt-in because it starts the CLI (seconds, not milliseconds) and only
means something on a machine with Hermes installed::

    SUTRA_TEST_HERMES_CLI=1 python3 -m pytest tests/test_host_gate.py

It stages the publishable runtime subset into a temporary directory first. The
repository also carries development-only files (release workflow, installer,
fixtures) that the gate's security scan reads as dangerous code paths; those are
not part of an installed plugin and are not what this test is about.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from conftest import ROOT

_OPT_IN = os.environ.get("SUTRA_TEST_HERMES_CLI") == "1"
_HERMES = shutil.which("hermes")

pytestmark = pytest.mark.skipif(
    not _OPT_IN or _HERMES is None,
    reason="set SUTRA_TEST_HERMES_CLI=1 with the Hermes CLI on PATH to run the host gate",
)

_RUNTIME_FILES = ("plugin.yaml", "opencode_plugin.js", "openclaw_plugin.js")


def _stage(destination):
    destination.mkdir(parents=True)
    for path in sorted(ROOT.glob("*.py")):
        shutil.copy2(path, destination / path.name)
    for name in _RUNTIME_FILES:
        shutil.copy2(ROOT / name, destination / name)
    return destination


def test_the_host_gate_accepts_every_declared_stage(tmp_path):
    plugin = _stage(tmp_path / "sutra")
    completed = subprocess.run(
        [_HERMES, "plugins", "validate", str(plugin)],
        capture_output=True, text=True, timeout=600,
    )
    output = completed.stdout + completed.stderr
    assert "declared middleware — matches registrations" in output, output
    assert "declared hooks — matches registrations" in output, output
    assert "Validation passed." in output, output
    assert completed.returncode == 0, output
