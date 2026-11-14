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

from .ui import (
    print_step,
    print_summary,
    format_error,
    print_error,
    print_success,
    print_warning,
    emit_json,
    _should_emit_json,
)

try:
    from . import jsonc
except ImportError:
    import jsonc

__all__ = ["detect_clients", "install", "opencode_config", "opencode_configured",
           "uninstall"]

# Clients with a verified configuration path. ``hermes`` is native middleware;
# ``claude`` is the native PostToolUse hook; ``opencode`` gets a generated
# plugin; ``openclaw`` gets the generated OpenClaw plugin; ``codex`` gets a
# PostToolUse feedback hook plus MCP recovery.
CLIENTS = ("hermes", "claude", "opencode", "openclaw", "codex")


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


def detect_clients() -> list[str]:
    """Clients present on this machine, in a stable order.

    Detection is intentionally narrow: a binary on PATH, or an existing
    OpenCode/OpenClaw config or SUTRA plugin. A client that is not installed
    is never configured on speculation.
    """
    found: list[str] = []
    try:
        from . import hermes
        if hermes.binary() or hermes.plugin_dir().exists() or hermes.config_path().exists():
            found.append("hermes")
    except Exception:  # a detection failure must not make install unusable
        pass
    try:
        from . import claude_code
        if claude_code.binary():
            found.append("claude")
    except Exception:  # a detection failure must not make install unusable
        pass
    if shutil.which("opencode") or opencode_config().is_file():
        found.append("opencode")
    try:
        from . import codex
        if codex.binary() or codex.config_path().exists():
            found.append("codex")
    except Exception:  # a detection failure must not make install unusable
        pass
    try:
        from . import openclaw
        if (shutil.which("openclaw") or openclaw.configured()
                or (openclaw.openclaw_config_dir() / "openclaw.json").is_file()):
            found.append("openclaw")
    except Exception:  # a detection failure must not make install unusable
        pass
    return found


def opencode_configured(path: pathlib.Path | None = None) -> bool:
    """True when the OpenCode config names a sutra MCP entry."""
    path = path or opencode_config()
    try:
        return jsonc.has_entry(path.read_text(encoding="utf-8"))
    except OSError:
        return False


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
    # newline="" preserves CRLF configs on Windows; text-mode translation here
    # would rewrite every unrelated line ending.
    with temp.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
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
            print_step(1, 6, "info", "Detecting clients", tty=_should_emit_json() is False)
            print_error(f"{path} does not exist; open OpenCode once so it writes its config, or pass --config", tty=_should_emit_json() is False)
            return 1
        print_warning(f"{path} does not exist; nothing to uninstall")
        return 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    try:
        updated, changed = (jsonc.with_entry(text, _python()) if installing
                           else jsonc.without_entry(text))
    except ValueError as exc:
        friendly = format_error(
            "permission-error",
            details=str(exc),
            step1="Run `sutra doctor` to diagnose the issue",
            step2="Check file permissions and ensure the config is not read-only",
        )
        print_error(friendly)
        return 1
    if not changed:
        state = "has" if installing else "has no"
        print_success(f"{path} already {state} sutra MCP entry; nothing to do")
        return 0
    if dry_run:
        print_warning(f"dry run, {path} would change (no file was written)")
        return 0
    _write(path, updated)
    # Step 5: Writing the change
    changed_verb = "Registered" if installing else "Removed"
    entry_state = "registered" if installing else "removed"
    print_step(5, 6, "success", f"{changed_verb} sutra MCP entry",
               tty=_should_emit_json() is False)

    # Emit JSON if in non-TTY mode
    if _should_emit_json():
        emit_json(
            step=5,
            total=6,
            status="success",
            message=f"{changed_verb} sutra MCP entry",
            integrated_clients=[{"client": "opencode", "status": entry_state}],
        )

    # Final summary
    print_summary(
        5,
        0,
        0,
        integrated_clients=[f"opencode MCP entry {entry_state} in {path}"],
        next_steps=([
            "Run `sutra doctor` to verify the installation",
            "Restart OpenCode to pick up the new MCP entry",
        ] if installing else [
            "Restart OpenCode so it drops the sutra MCP entry",
        ]),
        tty=_should_emit_json() is False,
        title=("SUTRA INSTALLATION REPORT" if installing
               else "SUTRA UNINSTALLATION REPORT"),
    )

    if installing:
        print_warning("Restart OpenCode to confirm `sutra` appears in its MCP list")
    return 0
