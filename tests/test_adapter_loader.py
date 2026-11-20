"""The standalone adapters load the package they ship with.

A bare ``import sutra`` resolves through ``sys.path``, which on a machine with
an editable install or a second checkout can be a different copy of the code.
An adapter must run the code beside its own script, so the loader prefers the
package found next to ``scripts/``.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_loader_prefers_the_checkout_beside_the_script():
    code = ("import sys; sys.path.insert(0, 'scripts'); import _sutra; "
            "print(_sutra.load_sutra().__file__)")
    done = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    loaded = pathlib.Path(done.stdout.strip()).resolve()
    assert loaded == (ROOT / "__init__.py").resolve()


def test_loader_returns_one_module_per_process():
    code = ("import sys; sys.path.insert(0, 'scripts'); import _sutra; "
            "first = _sutra.load_sutra(); second = _sutra.load_sutra(); "
            "print(first is second)")
    done = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "True"
