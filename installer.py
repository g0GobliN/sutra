"""Apply a SUTRA MCP entry to a client's config file.

`jsonc.py` owns the text edits; this module owns the file: where the config
lives, the atomic write, the timestamped backup, and the two operations the
CLI calls. Every write names a backup first, and a dry run writes nothing.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys
import time

from . import jsonc

__all__ = ["install", "uninstall", "opencode_config"]


def opencode_config(explicit: str | None = None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser()
    override = os.environ.get("SUTRA_OPENCODE_CONFIG")
    if override:
        return pathlib.Path(override).expanduser()
    base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "opencode"
    for name in ("opencode.jsonc", "opencode.json"):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return base / "opencode.json"


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    """Atomic write with a timestamped backup beside the original.

    The stamp has one-second resolution, so a second write inside the same
    second would otherwise overwrite the first backup — and the first backup
    is the only copy of the bytes before the first edit.
    """
    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(path.name + ".bak-" + stamp)
    suffix = 1
    while backup.exists():
        suffix += 1
        backup = path.with_name(f"{path.name}.bak-{stamp}-{suffix}")
    shutil.copy2(path, backup)
    temp = path.with_name(path.name + ".sutra-tmp")
    temp.write_text(text, encoding="utf-8")
    shutil.copymode(path, temp)
    os.replace(temp, path)
    return backup


def _python() -> str:
    return sys.executable or "python3"


def install(path: pathlib.Path, *, dry_run: bool = False) -> int:
    """Register the MCP entry in `path`; return a process exit code."""
    return _apply(path, installing=True, dry_run=dry_run)


def uninstall(path: pathlib.Path, *, dry_run: bool = False) -> int:
    """Remove the MCP entry from `path`; return a process exit code."""
    return _apply(path, installing=False, dry_run=dry_run)


def _apply(path: pathlib.Path, *, installing: bool, dry_run: bool) -> int:
    if not path.is_file():
        if installing:
            print(f"sutra: {path} does not exist; open OpenCode once so it writes its "
                  f"config, or pass --config", file=sys.stderr)
            return 1
        print(f"sutra: {path} does not exist; nothing to uninstall")
        return 0
    text = path.read_text(encoding="utf-8")
    try:
        updated, changed = (jsonc.with_entry(text, _python()) if installing
                           else jsonc.without_entry(text))
    except ValueError as exc:
        print(f"sutra: {path} could not be edited safely: {exc}", file=sys.stderr)
        return 1
    if not changed:
        state = "has" if installing else "has no"
        print(f"sutra: {path} already {state} sutra MCP entry; nothing to do")
        return 0
    if dry_run:
        print(f"sutra: dry run, {path} would change (no file was written)")
        return 0
    backup = _write(path, updated)
    print(f"sutra: {'registered' if installing else 'removed'} the sutra MCP entry in {path}")
    print(f"sutra: backup at {backup}")
    if installing:
        print("sutra: restart OpenCode and confirm `sutra` appears in its MCP list")
    return 0
