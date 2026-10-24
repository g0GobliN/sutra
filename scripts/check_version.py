#!/usr/bin/env python3
"""Fail unless a release tag, plugin.yaml, and __init__.py agree.

Usage: python scripts/check_version.py v0.2.0
"""

from __future__ import annotations

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


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_REF_NAME", "")
    if not tag:
        raise SystemExit("pass a tag, e.g. v0.2.0")
    expected = tag[1:] if tag.startswith("v") else tag
    manifest = declared_version(
        (ROOT / "plugin.yaml").read_text(encoding="utf-8"),
        r'^version:\s*["\']?([^"\'\s]+)', "plugin.yaml")
    package = declared_version(
        (ROOT / "__init__.py").read_text(encoding="utf-8"),
        r'^__version__\s*=\s*["\']([^"\']+)', "__init__.py")
    if expected != manifest or expected != package:
        print(f"tag {tag}: expected {expected}, plugin.yaml says {manifest}, "
              f"__init__.py says {package}", file=sys.stderr)
        return 1
    print(f"tag {tag} matches plugin.yaml {manifest} and __init__.py {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
