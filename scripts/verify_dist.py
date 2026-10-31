#!/usr/bin/env python3
"""Validate a built sdist and wheel before either is uploaded.

`twine check` proves the metadata renders. This proves the *contents*: every
runtime module, the bundled adapter scripts, the plugin manifest, the generated
OpenCode plugin and the licence are present; no test suite, cache, development
script or local absolute path came along; and, with ``--check-index``, the
version is not already on PyPI (an upload of an existing version always fails,
and a failure after a partial upload is worse than one before it).

Usage:
    python scripts/verify_dist.py dist
    python scripts/verify_dist.py dist --check-index
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAME = "sutra-agent"
IMPORT_NAME = "sutra"

RUNTIME_MODULES = frozenset({
    "__init__", "claude_code", "cli", "codex", "config", "core", "discovery",
    "engine", "hermes", "hook", "installer", "jsonc", "mcp", "metrics",
    "opencode", "recovery", "runner", "store", "utf8",
})
RUNTIME_SCRIPTS = frozenset({
    "_sutra.py", "sutra-cli.py", "sutra-mcp-server.py", "sutra-claude-code-hook.py",
    "sutra-codex-hook.py",
})
# `scripts/check_version.py` is release tooling. The wheel excludes it (it ships
# only what an install needs); the sdist includes it explicitly through
# `MANIFEST.in`, so its presence does not depend on the setuptools version.
DEV_SCRIPTS = frozenset({"check_version.py"})
PACKAGE_DATA = frozenset({"plugin.yaml", "opencode_plugin.js"})

# A path fragment that means a development artifact reached the archive.
FORBIDDEN = (
    "tests/", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv",
    "/.git/", "probes/", "bench/", "spikes/", "docs/", ".cloud-attachments",
    ".freebuff", ".pyc", ".DS_Store",
)
TEXT_SUFFIXES = (".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".js")


def _fail(problems: list[str], message: str) -> None:
    problems.append(message)


def _version_from_wheel(names: list[str], reader) -> str | None:
    target = f"{IMPORT_NAME}/__init__.py"
    if target not in names:
        return None
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)',
                      reader(target).decode("utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def _scan_text(names: list[str], reader, problems: list[str], where: str) -> None:
    """Refuse an archive that embedded this checkout's absolute paths."""
    markers = {str(ROOT), str(pathlib.Path.home())}
    for name in names:
        if not name.endswith(TEXT_SUFFIXES):
            continue
        try:
            text = reader(name).decode("utf-8")
        except (UnicodeDecodeError, KeyError):
            continue
        for marker in markers:
            if marker and marker in text:
                _fail(problems, f"{where}: {name} embeds the local path {marker!r}")


def _check_forbidden(names: list[str], problems: list[str], where: str) -> None:
    for name in names:
        for fragment in FORBIDDEN:
            if fragment in name:
                _fail(problems, f"{where}: development path {name!r} "
                                f"matched {fragment!r}")


def check_wheel(wheel: pathlib.Path, problems: list[str]) -> str | None:
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        reader = archive.read

        version = _version_from_wheel(names, reader)
        if version is None:
            _fail(problems, "wheel: sutra/__init__.py is missing or has no __version__")
            return None
        distinfo = f"sutra_agent-{version}.dist-info"
        tops = {name.split("/")[0] for name in names}
        if tops != {IMPORT_NAME, distinfo}:
            _fail(problems, f"wheel: unexpected top-level entries {sorted(tops)}")

        packaged = {n[len(IMPORT_NAME) + 1:] for n in names if n.startswith(IMPORT_NAME + "/")}
        modules = {p for p in packaged if p.endswith(".py") and "/" not in p}
        scripts = {p.split("/", 1)[1] for p in packaged if p.startswith("scripts/")}
        extras = packaged - modules - {f"scripts/{s}" for s in scripts}
        # Bundled client adapters are data, not importable modules.
        extras -= PACKAGE_DATA
        if modules != {f"{m}.py" for m in RUNTIME_MODULES}:
            _fail(problems, f"wheel: runtime modules differ: "
                            f"missing {sorted({f'{m}.py' for m in RUNTIME_MODULES} - modules)}, "
                            f"unexpected {sorted(modules - {f'{m}.py' for m in RUNTIME_MODULES})}")
        if scripts != RUNTIME_SCRIPTS:
            _fail(problems, f"wheel: bundled scripts differ: "
                            f"missing {sorted(RUNTIME_SCRIPTS - scripts)}, "
                            f"unexpected {sorted(scripts - RUNTIME_SCRIPTS)}")
        if extras:
            _fail(problems, f"wheel: unexpected package files {sorted(extras)}")

        required = {f"{distinfo}/{n}" for n in
                    ("METADATA", "WHEEL", "RECORD", "entry_points.txt")}
        missing = sorted(required - set(names))
        if missing:
            _fail(problems, f"wheel: missing metadata files {missing}")

        metadata = reader(f"{distinfo}/METADATA").decode("utf-8", "replace")
        for expected in (f"Name: {NAME}", f"Version: {version}",
                         "Requires-Python: >=3.11", "License-Expression: MIT",
                         "Description-Content-Type: text/markdown"):
            if expected not in metadata:
                _fail(problems, f"wheel: METADATA lacks {expected!r}")
        if re.search(r"^Requires-Dist:", metadata, re.MULTILINE):
            _fail(problems, "wheel: the distribution must stay dependency-free")

        entry_points = reader(f"{distinfo}/entry_points.txt").decode("utf-8", "replace")
        for line in ("sutra = sutra.cli:main", "sutra-mcp = sutra.mcp:main",
                     "sutra-hook = sutra.hook:main"):
            if line not in entry_points:
                _fail(problems, f"wheel: entry point {line!r} is missing")

        _check_forbidden(names, problems, "wheel")
        _scan_text([n for n in names if n.endswith(TEXT_SUFFIXES)], reader, problems, "wheel")
        return version


def check_sdist(sdist: pathlib.Path, version: str | None, problems: list[str]) -> None:
    with tarfile.open(sdist) as archive:
        members = [m for m in archive.getmembers() if m.isfile()]
        names = [m.name for m in members]
        prefixes = {n.split("/")[0] for n in names}
        if len(prefixes) != 1:
            _fail(problems, f"sdist: expected one top-level directory, saw {sorted(prefixes)}")
            return
        prefix = prefixes.pop() + "/"
        relative = {n[len(prefix):] for n in names}

        if version and prefix.rstrip("/") != f"sutra_agent-{version}":
            _fail(problems, f"sdist: archive root {prefix!r} does not match version {version}")

        required = {"pyproject.toml", "README.md", "LICENSE", "MANIFEST.in",
                    "plugin.yaml"} | {f"{m}.py" for m in RUNTIME_MODULES}
        missing = sorted(required - relative)
        if missing:
            _fail(problems, f"sdist: missing {missing}")

        scripts = {p.split("/", 1)[1] for p in relative if p.startswith("scripts/")
                   and p.count("/") == 1}
        if scripts != RUNTIME_SCRIPTS | DEV_SCRIPTS:
            _fail(problems, f"sdist: scripts differ: missing "
                            f"{sorted((RUNTIME_SCRIPTS | DEV_SCRIPTS) - scripts)}, "
                            f"unexpected {sorted(scripts - (RUNTIME_SCRIPTS | DEV_SCRIPTS))}")

        _check_forbidden(sorted(relative), problems, "sdist")

        def read(name: str) -> bytes:
            handle = archive.extractfile(prefix + name)
            if handle is None:
                raise KeyError(name)
            return handle.read()

        _scan_text([n for n in relative if n.endswith(TEXT_SUFFIXES)], read, problems, "sdist")


def check_index(version: str, problems: list[str]) -> None:
    url = f"https://pypi.org/pypi/{NAME}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return  # the project does not exist yet: a first publication is fine
        _fail(problems, f"index: {url} answered HTTP {exc.code}")
        return
    except OSError as exc:
        _fail(problems, f"index: could not read {url}: {exc}")
        return
    if version in data.get("releases", {}):
        _fail(problems, f"index: {NAME} {version} is already on PyPI; "
                        f"a release must never reuse a version number")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the built sdist and wheel.")
    parser.add_argument("dist", type=pathlib.Path, help="directory holding the build")
    parser.add_argument("--version", default=None, help="expected version, e.g. 0.3.0")
    parser.add_argument("--check-index", action="store_true",
                        help="refuse a version that PyPI already serves")
    args = parser.parse_args(argv)

    wheels = sorted(args.dist.glob("*.whl"))
    sdists = sorted(args.dist.glob("*.tar.gz"))
    problems: list[str] = []
    if len(wheels) != 1:
        problems.append(f"{args.dist}: expected exactly one wheel, found {len(wheels)}")
    if len(sdists) != 1:
        problems.append(f"{args.dist}: expected exactly one sdist, found {len(sdists)}")
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1

    version = check_wheel(wheels[0], problems)
    check_sdist(sdists[0], version, problems)
    if args.version and version != args.version:
        problems.append(f"built version {version!r} does not match expected {args.version!r}")
    if version and args.check_index:
        check_index(version, problems)

    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1
    print(f"OK {wheels[0].name} and {sdists[0].name} are a {NAME} {version} release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
