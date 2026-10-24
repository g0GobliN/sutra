"""Pytest wrapper: run the node hook tests when node is available.

The plugin hook is JavaScript, so its unit tests live in
``test_plugin_hook.mjs`` and run under node; this wrapper folds them into the
Python suite and skips cleanly on machines without node.
"""

import os
import pathlib
import shutil
import subprocess

import pytest

HERE = pathlib.Path(__file__).resolve().parent


def test_plugin_hook_semantics():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SUTRA_")}  # ambient bridge vars must not leak in
    result = subprocess.run([node, str(HERE / "test_plugin_hook.mjs")],
                            capture_output=True, text=True, timeout=60, cwd=str(HERE),
                            env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8/8 OK" in result.stdout
