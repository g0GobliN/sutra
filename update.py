"""`sutra update` — compare the installed release with the latest on PyPI.

The CLI owns the wording; this module owns the facts: which version is
installed, which version is published, and how to upgrade this interpreter's
environment. Nothing here runs on import, so a plain ``sutra install`` never
touches the network.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from importlib import metadata
from urllib.request import urlopen

PACKAGE = "sutra-agent"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
TIMEOUT_SECONDS = 6.0
PIP_TIMEOUT_SECONDS = 120.0


def installed_version() -> str:
    """The version installed in the running interpreter."""
    return metadata.version(PACKAGE)


def _pip_latest(timeout: float = PIP_TIMEOUT_SECONDS) -> str | None:
    """The version pip's resolver would install.

    Returns an empty string when pip resolves nothing newer than what is
    installed, and None when pip could not answer at all. The upgrade
    subprocess uses this same resolver, so a private index, ``PIP_FIND_LINKS``
    or a constraints file feeds both the check and ``--apply``. The report goes
    to a file: pip renders its own progress through a console that cannot
    encode its metadata on a legacy Windows code page, so asking for the
    report on stdout fails there.
    """
    with tempfile.TemporaryDirectory(prefix="sutra-update-") as temporary:
        report = pathlib.Path(temporary) / "report.json"
        command = [sys.executable, "-m", "pip", "install", "--dry-run",
                   "--upgrade", "--quiet", "--report", str(report), PACKAGE]
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or not report.is_file():
            return None  # pip could not answer; the caller may fall back to PyPI
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    for item in payload.get("install") or []:
        metadata_block = item.get("metadata") or {}
        name = str(metadata_block.get("name", "")).lower().replace("_", "-")
        if name == PACKAGE:
            version = metadata_block.get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
    # pip resolved nothing to install, so what is installed is the newest this
    # environment can get. The empty string says so without being a failure.
    return ""


def _pypi_latest(timeout: float = TIMEOUT_SECONDS) -> str:
    """The newest release published on PyPI."""
    with urlopen(PYPI_URL, timeout=timeout) as response:
        payload = json.load(response)
    version = (payload.get("info") or {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"PyPI returned no version for {PACKAGE}")
    return version.strip()


def latest_version(timeout: float = TIMEOUT_SECONDS) -> str:
    """The newest version available to this environment.

    pip's resolver answers first, so an enterprise index or a local wheel
    directory is reported accurately. PyPI is the fallback when pip cannot
    resolve (no pip, an offline resolver error, an unreadable report).
    """
    resolved = _pip_latest()
    if resolved is not None:
        if resolved:
            return resolved
        try:
            return installed_version()
        except metadata.PackageNotFoundError:
            return _pypi_latest(timeout)
    return _pypi_latest(timeout)


def _release(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"\d+", version)[:3])
    return parts or (0,)


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a later release than ``current``."""
    return _release(candidate) > _release(current)


def is_source_checkout() -> bool:
    """True when pip installed this distribution from a local checkout.

    An editable (or otherwise file-sourced) install cannot be upgraded with
    pip without discarding the checkout, so ``sutra update --apply`` refuses.
    """
    try:
        distribution = metadata.distribution(PACKAGE)
    except metadata.PackageNotFoundError:
        return False
    raw = distribution.read_text("direct_url.json")
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    # PEP 610: a directory install (editable or plain) records ``dir_info``;
    # a wheel or sdist install records ``archive_info``. Only the former is a
    # checkout the user has to update with git.
    return isinstance(data.get("dir_info"), dict)


class UpdateError(Exception):
    """An update failed before pip could install anything."""


def _console_launchers() -> list[pathlib.Path]:
    """This package's console-script launchers in the environment, on Windows."""
    if os.name != "nt":
        return []
    scripts = pathlib.Path(sys.prefix) / "Scripts"
    names = ["sutra", "sutra-hook", "sutra-mcp"]
    try:
        entry_points = metadata.distribution(PACKAGE).entry_points
    except metadata.PackageNotFoundError:
        entry_points = ()
    declared = sorted({entry_point.name for entry_point in entry_points
                       if entry_point.group == "console_scripts"})
    candidates = declared or names
    return [launcher for launcher in (scripts / f"{name}.exe" for name in candidates)
            if launcher.is_file()]


def _launcher_is_locked(launchers: list[pathlib.Path]) -> bool:
    """Whether one of these launchers is the process running this command.

    pip's launcher sets ``sys.argv[0]`` to the script path *without* its
    ``.exe`` suffix, so compare both forms. A running executable cannot be
    renamed, deleted or overwritten on Windows.
    """
    running = pathlib.Path(sys.argv[0] if sys.argv else "").name.lower()
    return any(running in {launcher.name.lower(), launcher.stem.lower()}
               for launcher in launchers)


def _swap_staged(site: pathlib.Path, package: pathlib.Path,
                 metadata_dir: pathlib.Path) -> int:
    """Move a staged release over the installed one, restoring on failure."""
    installed = site / "sutra"
    backup = site / f".sutra-update-{os.getpid()}"
    old_metadata = [path for path in site.glob("sutra_agent-*.dist-info")
                    if path.is_dir()]
    try:
        backup.mkdir()
        if installed.exists():
            shutil.move(str(installed), str(backup / "sutra"))
        for directory in old_metadata:
            shutil.move(str(directory), str(backup / directory.name))
        shutil.move(str(package), str(installed))
        shutil.move(str(metadata_dir), str(site / metadata_dir.name))
    except OSError:
        if (backup / "sutra").exists():
            shutil.rmtree(installed, ignore_errors=True)
            shutil.move(str(backup / "sutra"), str(installed))
        new_metadata = site / metadata_dir.name
        if new_metadata.exists() and not (backup / new_metadata.name).exists():
            shutil.rmtree(new_metadata, ignore_errors=True)
        for directory in backup.glob("sutra_agent-*.dist-info"):
            if not (site / directory.name).exists():
                shutil.move(str(directory), str(site / directory.name))
        shutil.rmtree(backup, ignore_errors=True)
        return 1
    shutil.rmtree(backup, ignore_errors=True)
    return 0


def _upgrade_beside_running_launcher() -> int:
    """Install a newer release without touching the locked console scripts.

    Windows refuses to replace or rename the launcher that started this
    process, and pip replaces every console script of the distribution, so a
    normal ``pip install --upgrade`` fails there with WinError 32. pip still
    resolves and installs the release into a staging directory; the package
    and its metadata are then moved into place. The launcher itself is a
    generic stub for ``sutra.cli:main`` and keeps working unchanged.
    """
    version = _pip_latest()
    if version == "":
        return 0  # pip resolved nothing newer than what is installed
    if version is None:
        raise UpdateError("could not resolve a newer release")
    staging = pathlib.Path(tempfile.mkdtemp(prefix="sutra-update-"))
    try:
        command = [sys.executable, "-m", "pip", "install", "--quiet",
                   "--target", str(staging), "--no-deps", f"{PACKAGE}=={version}"]
        try:
            result = subprocess.run(command)
        except OSError as exc:
            raise UpdateError(f"pip could not start: {exc}") from exc
        if result.returncode != 0:
            return result.returncode
        package = staging / "sutra"
        metadata_dirs = sorted(staging.glob("sutra_agent-*.dist-info"))
        if not package.is_dir() or len(metadata_dirs) != 1:
            raise UpdateError("pip did not produce the expected files")
        try:
            requirements = (metadata_dirs[0] / "METADATA").read_text(encoding="utf-8")
        except OSError as exc:
            raise UpdateError("pip did not produce readable metadata") from exc
        if any(line.startswith("Requires-Dist:") and "extra ==" not in line
               for line in requirements.splitlines()):
            raise UpdateError(
                "this release needs new dependencies; install it with "
                f"`{sys.executable} -m pip install --upgrade {PACKAGE}`")
        if _swap_staged(pathlib.Path(sysconfig.get_path("purelib")),
                        package, metadata_dirs[0]) != 0:
            raise UpdateError("could not move the new release into place")
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def upgrade() -> int:
    """Upgrade this interpreter's environment; return pip's exit status."""
    if _launcher_is_locked(_console_launchers()):
        return _upgrade_beside_running_launcher()
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE])
    return result.returncode
