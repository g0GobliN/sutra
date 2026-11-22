"""Install the automatic DeepSeek Harness adapter.

DeepSeek Harness has no console script and no settings file a third party may
edit: a host capability arrives as a *bundle* — a package directory with a
``dsh.bundle.patch`` entry — and the supported way to attach it is
``dsh plugin --profile <name> add <directory>``. This adapter therefore copies
one small JavaScript bundle to ``<DSH_HOME>/sutra-host`` and asks the harness
to add it, which is the same path a user follows by hand.

The bundle wires every stage this harness exposes:

* ``tools/post-execute`` trims an oversized tool result before the model reads
  it, through ``python -m sutra process-result`` (the same seam the other hosts
  use), so the Python core stays the only implementation of the policy;
* ``mcpManager.register`` exposes the recovery server, so the id the marker
  quotes can be exchanged for the exact bytes the store kept.

The request stage is deliberately absent: the harness hands plugins a model
route (``agent/request``) rather than the outbound message array, and inventing
one would be a claim the host does not support.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

try:
    from .ui import print_error, print_info, print_success, print_warning
except ImportError:  # allow `python -c "import dsh"` run from the package directory
    def print_error(message, /, *, tty=None):
        print(message, file=sys.stderr)

    def print_warning(message, /, *, tty=None):
        print(message)

    def print_info(message, /, *, tty=None):
        print(message)

    def print_success(message, /, *, tty=None):
        print(message)

BUNDLE_NAME = "dsh-sutra-host"
BUNDLE_FILES = ("package.json", "cordis.patch.yml", "index.js")
# The released templates are flat package data, beside the JS adapters.
TEMPLATES = {
    "package.json": "dsh_host_package.json",
    "cordis.patch.yml": "dsh_host_cordis.patch.yml",
    "index.js": "dsh_host_index.js",
}


def home(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    """Where the harness keeps its profiles and plugin packages."""
    if explicit:
        return pathlib.Path(explicit).expanduser()
    return pathlib.Path(os.environ.get("DSH_HOME") or "~/.dsh").expanduser()


def bundle_dir(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    return home(explicit) / "sutra-host"


def profile(explicit: str | None = None) -> str:
    return explicit or os.environ.get("DSH_PROFILE") or "desktop"


def binary() -> str | None:
    return shutil.which("dsh")


def configured(config_home: str | pathlib.Path | None = None, name: str | None = None) -> bool:
    """True when the bundle is copied and the profile already lists it."""
    manifest = home(config_home) / "profiles" / profile(name) / "package.json"
    if not manifest.is_file():
        return False
    try:
        package = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    dependencies = package.get("dependencies") or {}
    return BUNDLE_NAME in dependencies


def template(filename: str) -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / TEMPLATES[filename]


def _patch_text(python: str, python_path: str) -> str:
    return (
        "# Added by `sutra install --client dsh`: one bundle, every stage this\n"
        "# harness exposes. Remove the bundle to withdraw it.\n"
        "- insert:\n"
        f"    - id: {BUNDLE_NAME}\n"
        f"      name: {BUNDLE_NAME}\n"
        "      config:\n"
        f"        python: {python}\n"
        f"        pythonPath: {python_path}\n"
        "        mode: balanced\n"
    )


def install(*, dry_run: bool = False, python: str | None = None,
            python_path: str | None = None, name: str | None = None,
            home_dir: str | pathlib.Path | None = None) -> int:
    interpreter = python or sys.executable or "python3"
    path = python_path if python_path is not None else (os.environ.get("SUTRA_PYTHONPATH") or "")
    target = bundle_dir(home_dir)
    sources = {filename: template(filename) for filename in BUNDLE_FILES}
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        print_error(f"the DeepSeek Harness bundle template is missing: {', '.join(missing)}")
        return 1
    if shutil.which(interpreter) is None and not pathlib.Path(interpreter).exists():
        print_error(f"{interpreter} is not runnable; pass --python with an interpreter that can import sutra")
        return 1
    cli = binary()
    if cli is None and not dry_run:
        print_error("dsh is not on PATH; install DeepSeek Harness first, or pass --dry-run to inspect the bundle")
        return 1
    if dry_run:
        print_warning(f"dry run, would write {target} and run `dsh plugin --profile {profile(name)} add {target}`")
        return 0
    target.mkdir(parents=True, exist_ok=True)
    for filename in BUNDLE_FILES:
        if filename == "cordis.patch.yml":
            (target / filename).write_text(_patch_text(interpreter, path), encoding="utf-8")
        else:
            shutil.copyfile(sources[filename], target / filename)
    print_success(f"sutra: wrote the host bundle at {target}")
    command = [cli, "plugin", "--profile", profile(name), "add", str(target)]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        print_error(f"dsh plugin add failed: {exc}")
        return 1
    if finished.returncode != 0:
        detail = (finished.stderr or finished.stdout or "").strip().splitlines()[-1:]
        print_error(f"dsh plugin add failed ({finished.returncode}): {' '.join(detail)}")
        return 1
    print_success(f"sutra: attached the bundle to the {profile(name)} profile")
    print_warning("Restart DeepSeek Harness once so the bundle loads; `sutra doctor` reports dsh after that")
    return 0


def uninstall(*, dry_run: bool = False, name: str | None = None,
              home_dir: str | pathlib.Path | None = None) -> int:
    target = bundle_dir(home_dir)
    cli = binary()
    if dry_run or cli is None:
        print_warning(f"would run `dsh plugin --profile {profile(name)} remove {BUNDLE_NAME}` and delete {target}")
        return 0
    finished = subprocess.run(
        [cli, "plugin", "--profile", profile(name), "remove", BUNDLE_NAME],
        capture_output=True, text=True, timeout=600,
    )
    if finished.returncode != 0:
        print_warning("dsh plugin remove reported a problem; the profile may need one manual pass")
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    print_success("sutra: removed the DeepSeek Harness bundle")
    return 0


def doctor(*, name: str | None = None, home_dir: str | pathlib.Path | None = None) -> dict:
    target = bundle_dir(home_dir)
    files = [filename for filename in BUNDLE_FILES if (target / filename).is_file()]
    attached = configured(home_dir, name)
    cli = binary()
    problems = []
    if not files:
        problems.append("the host bundle is not copied")
    if not attached:
        problems.append("the profile does not list dsh-sutra-host")
    if cli is None:
        problems.append("dsh is not on PATH")
    status = "pass" if not problems else ("partial" if files else "fail")
    return {
        "status": status,
        "bundle": str(target),
        "files": files,
        "profile": profile(name),
        "attached": attached,
        "binary": cli or "",
        "problems": problems,
        "copy": "current" if len(files) == len(BUNDLE_FILES) else "stale",
    }
