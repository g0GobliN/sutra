#!/usr/bin/env python3
"""Fail unless a release tag, the declared versions and the changelog agree.

Usage:
    python scripts/check_version.py v0.6.3
    python scripts/check_version.py v0.6.3 --notes

The tag is the authority: `plugin.yaml` and `__init__.py` must declare the same
version, and `CHANGELOG.md` must already have a heading for it. `--notes`
prints that section so a release run can record the notes it is publishing;
docs/release.md describes the convention the heading has to follow.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def declared_version(text: str, pattern: str, source: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise SystemExit(f"could not read the version from {source}")
    return match.group(1)


def changelog_heading(text: str, version: str) -> re.Match[str] | None:
    """Return the `## <version>` heading, tolerating a `v` prefix and a date."""
    return re.search(rf"^##\s+v?{re.escape(version)}(?=$|\s)", text, re.MULTILINE)


def changelog_section(text: str, version: str) -> str:
    """Return the body under the version heading, up to the next heading."""
    match = changelog_heading(text, version)
    if match is None:
        return ""
    newline = text.find("\n", match.end())
    if newline == -1:
        return ""
    following = re.compile(r"(?m)^##\s").search(text, newline)
    end = following.start() if following else len(text)
    return text[newline:end].strip("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "tag", nargs="?", default=os.environ.get("GITHUB_REF_NAME", ""),
        help="release tag, e.g. v0.6.3; defaults to GITHUB_REF_NAME")
    parser.add_argument(
        "--notes", action="store_true",
        help="print the changelog section for this version instead of a summary line")
    arguments = parser.parse_args(argv)
    if not arguments.tag:
        raise SystemExit("pass a tag, e.g. v0.2.0")
    expected = arguments.tag[1:] if arguments.tag.startswith("v") else arguments.tag
    manifest = declared_version(
        (ROOT / "plugin.yaml").read_text(encoding="utf-8"),
        r'^version:\s*["\']?([^"\'\s]+)', "plugin.yaml")
    package = declared_version(
        (ROOT / "__init__.py").read_text(encoding="utf-8"),
        r'^__version__\s*=\s*["\']([^"\']+)', "__init__.py")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    problems = []
    if expected != manifest:
        problems.append(f"plugin.yaml says {manifest}")
    if expected != package:
        problems.append(f"__init__.py says {package}")
    if changelog_heading(changelog, expected) is None:
        problems.append(f"CHANGELOG.md has no '## {expected}' heading")
    if problems:
        print(f"tag {arguments.tag}: expected {expected}, but " + "; ".join(problems),
              file=sys.stderr)
        return 1
    if arguments.notes:
        print(changelog_section(changelog, expected))
    else:
        print(f"tag {arguments.tag} matches plugin.yaml {manifest}, "
              f"__init__.py {package} and the changelog heading")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
