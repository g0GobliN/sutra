"""Claude Code integration: the native hook plus the MCP recovery server.

Verified against the official Claude Code documentation (hooks, settings, MCP)
and Claude Code 2.1.268:

- User settings live at ``~/.claude/settings.json``, or
  ``$CLAUDE_CONFIG_DIR/settings.json`` when that variable is set.
- A ``PostToolUse`` command hook may return
  ``hookSpecificOutput.updatedToolOutput`` to replace the tool result for any
  tool (the older ``updatedMCPToolOutput`` was MCP-only).
- MCP servers are registered with ``claude mcp add --scope user``; the user
  config is ``$CLAUDE_CONFIG_DIR/.claude.json`` or ``~/.claude.json``.

The hook is the automatic path: Claude Code runs it without the user invoking
SUTRA. The MCP server is only needed for recovery (``sutra_restore``). Both are
installed together so a marker can be followed immediately.

This module owns the settings edit and the CLI calls; it never prints file
contents, and every write is backed up first.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

SERVER_NAME = "sutra"
HOOK_EVENT = "PostToolUse"
HOOK_TIMEOUT_SECONDS = 60
DOCTOR_CLUE = "sutra-doctor-clue-2f8c"
DOCTOR_LINES = 3000
ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


def binary() -> str | None:
    """The Claude Code CLI, or None when it is not installed."""
    return shutil.which("claude")


def config_dir() -> pathlib.Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path.home() / ".claude"


def settings_path() -> pathlib.Path:
    return config_dir() / "settings.json"


def hook_command(python: str | None = None) -> tuple[str, list[str]]:
    """The exact command Claude Code should run for the hook.

    ``python -m sutra.hook`` is used instead of a console-script path so the
    hook keeps working when the install directory changes or is not on PATH.
    """
    return (python or sys.executable, ["-m", "sutra.hook"])


def _handler(command: str, args: list[str]) -> dict:
    return {
        "type": "command",
        "command": command,
        "args": list(args),
        "timeout": HOOK_TIMEOUT_SECONDS,
    }


def _same_handler(handler: object, command: str, args: list[str]) -> bool:
    if not isinstance(handler, dict):
        return False
    return (handler.get("type") == "command"
            and handler.get("command") == command
            and list(handler.get("args") or []) == list(args))


def _handlers(document: dict):
    """Yield every hook handler object in the PostToolUse groups."""
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return
    groups = hooks.get(HOOK_EVENT)
    if not isinstance(groups, list):
        return
    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        for handler in handlers:
            yield handler


def _is_sutra_handler(handler: object) -> bool:
    """True for a handler this project owns, including an old interpreter.

    The installer uses ``<python> -m sutra.hook``. A user who followed the
    checkout instructions may instead have ``sutra-hook`` with no arguments.
    Both are SUTRA's; a different Python path is a reinstall, not a second
    integration.
    """
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return False
    command = handler.get("command")
    if not isinstance(command, str) or not command:
        return False
    args = list(handler.get("args") or [])
    if args == ["-m", "sutra.hook"]:
        return True
    name = pathlib.Path(command).name
    return not args and name in {"sutra-hook", "sutra-hook.exe", "sutra-hook.py"}


def find_hook(document: dict) -> tuple[str, list[str]] | None:
    """The first SUTRA PostToolUse handler, whatever Python installed it."""
    for handler in _handlers(document):
        if _is_sutra_handler(handler):
            return handler["command"], list(handler.get("args") or [])
    return None


def has_hook(document: dict, command: str, args: list[str]) -> bool:
    return any(_same_handler(item, command, args) for item in _handlers(document))


def add_hook(document: dict, command: str, args: list[str]) -> bool:
    """Add our PostToolUse handler. Returns True when the document changed.

    A malformed ``hooks`` shape is refused rather than overwritten: the file is
    the user's, and guessing at a broken structure risks losing other hooks.
    An existing SUTRA handler is replaced, so upgrading the venv updates the
    command instead of adding a duplicate.
    """
    existing = find_hook(document)
    if existing == (command, list(args)):
        return False
    if existing is not None:
        remove_sutra_hooks(document)
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError('the "hooks" setting is not an object')
    groups = hooks.setdefault(HOOK_EVENT, [])
    if not isinstance(groups, list):
        raise ValueError(f'"hooks.{HOOK_EVENT}" is not an array')
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError(f'"hooks.{HOOK_EVENT}" contains a non-object entry')
        handlers = group.setdefault("hooks", [])
        if not isinstance(handlers, list):
            raise ValueError(f'"hooks.{HOOK_EVENT}[].hooks" is not an array')
    # An omitted matcher means every tool; keep it in its own group so other
    # matchers and handlers are untouched.
    groups.append({"hooks": [_handler(command, args)]})
    return True


def _remove_matching(document: dict, matches) -> bool:
    """Remove matching handlers; prune only containers we emptied."""
    changed = False
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get(HOOK_EVENT)
    if not isinstance(groups, list):
        return False
    kept_groups = []
    for group in groups:
        if not isinstance(group, dict):
            kept_groups.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            kept_groups.append(group)
            continue
        kept = [item for item in handlers if not matches(item)]
        if len(kept) != len(handlers):
            changed = True
        if kept:
            if kept != handlers:
                group = {**group, "hooks": kept}
            kept_groups.append(group)
        # else: the group held only our handler; drop it with the handler.
    if not changed:
        return False
    if kept_groups:
        hooks[HOOK_EVENT] = kept_groups
    else:
        hooks.pop(HOOK_EVENT, None)
        if not hooks:
            document.pop("hooks", None)
    return True


def remove_hook(document: dict, command: str, args: list[str]) -> bool:
    """Remove exactly the handler built from this command and args."""
    return _remove_matching(
        document, lambda item: _same_handler(item, command, args))


def remove_sutra_hooks(document: dict) -> bool:
    """Remove every SUTRA-owned handler, regardless of interpreter path."""
    return _remove_matching(document, _is_sutra_handler)


def load_settings(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{path}'s root is not a JSON object")
    return document


def _write_json(path: pathlib.Path, document: dict) -> None:
    from .installer import _write  # local: installer imports this module lazily

    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        _write(path, text)
        return
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                     dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _run(args: list[str], timeout: int = 60):
    executable = binary()
    if not executable:
        return None
    return subprocess.run([executable, *args], capture_output=True, text=True,
                          timeout=timeout)


def _mcp_add_args(python: str) -> list[str]:
    return ["mcp", "add", "--scope", "user", "--transport", "stdio",
            SERVER_NAME, "--", python, "-m", "sutra.mcp"]


def _mcp_get() -> tuple[bool, str]:
    result = _run(["mcp", "get", SERVER_NAME], timeout=45)
    if result is None:
        return False, "the claude command is no longer on PATH"
    if result.returncode != 0:
        return False, "not configured"
    return True, result.stdout


def _mcp_matches(text: str, python: str) -> bool:
    """Check the command and args reported by ``claude mcp get``."""
    command = args = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Command:"):
            command = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Args:"):
            args = stripped.split(":", 1)[1].strip()
    return command == python and args == "-m sutra.mcp"


def _mcp_connected(text: str) -> bool:
    for line in text.splitlines():
        if line.strip().startswith("Status:"):
            return "Connected" in line and "Failed" not in line
    return False


def _doctor_payload(root: pathlib.Path) -> tuple[dict, str]:
    """A real ``PostToolUse`` event whose full text is only on disk.

    Claude Code hands a hook the first 30,000 characters of a large Bash
    result and puts the rest in ``persistedOutputPath``. The probe reproduces
    that shape, so a passing doctor proves the installed hook reads the full
    file, not just the preview.
    """
    project = root / "projects" / "sutra-doctor"
    session = project / "session"
    results = session / "tool-results"
    results.mkdir(parents=True, exist_ok=True)
    lines = [f"doctor line {index} padding" for index in range(DOCTOR_LINES)]
    lines[len(lines) // 2] = f"CLUE: {DOCTOR_CLUE}"
    full = "\n".join(lines)
    path = results / "result.txt"
    path.write_text(full, encoding="utf-8")
    transcript = project / "session.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = {
        "hook_event_name": HOOK_EVENT,
        "session_id": "sutra-doctor",
        "tool_name": "Bash",
        "transcript_path": str(transcript),
        "tool_response": {
            "stdout": full[:29_999], "stderr": "", "interrupted": False,
            "isImage": False, "noOutputExpected": False,
            "persistedOutputPath": str(path),
            "persistedOutputSize": len(full.encode()),
        },
    }
    return payload, full


def _probe_output(updated: object) -> str:
    if isinstance(updated, str):
        return updated
    if isinstance(updated, dict) and isinstance(updated.get("stdout"), str):
        return updated["stdout"]
    return ""


def _restore_probe(entry_id: str, store_dir: pathlib.Path) -> dict:
    """Restore through the same in-process seam the CLI and MCP use."""
    from . import core

    keys = ("SUTRA_ENABLED", "SUTRA_STORE", "SUTRA_STORE_DIR", "HERMES_HOME",
            "SUTRA_STATS_FILE")
    saved = {key: os.environ.get(key) for key in keys}
    os.environ.update({
        "SUTRA_ENABLED": "1", "SUTRA_STORE": "1",
        "SUTRA_STORE_DIR": str(store_dir),
        "HERMES_HOME": str(store_dir.parent.parent),
        "SUTRA_STATS_FILE": str(store_dir.parent / "doctor-stats.jsonl"),
    })
    try:
        raw = core.restore({"id": entry_id, "mode": "search",
                            "query": DOCTOR_CLUE, "context": 1})
        return json.loads(raw)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def doctor(*, python: str | None = None,
           hook: tuple[str, list[str]] | None = None) -> dict:
    """Prove the installed hook compresses and restores a real event.

    Configuration alone is not proof: this runs the same command Claude Code
    runs, with a synthetic payload shaped like the host's persisted-output
    event, then resolves the marker's id through the recovery seam. The probe
    uses a temporary store and never touches the user's entries or stats.
    """
    report: dict[str, object] = {
        "client": "claude",
        "settings": str(settings_path()),
        "hook": False,
        "mcp": "not configured",
        "probe": "not run",
        "ok": False,
    }
    try:
        document = load_settings(settings_path())
    except ValueError as exc:
        report["error"] = str(exc)
        return report
    if hook is None:
        found = find_hook(document)
        if found is None:
            report["probe"] = "hook is not installed in the user settings"
            return report
        command, args = found
        report["hook"] = True
    else:
        command, args = hook
        report["hook"] = has_hook(document, command, args)
    existing, detail = _mcp_get()
    if existing:
        report["mcp"] = "connected" if _mcp_connected(detail) else "configured, not connected"
    if report["hook"] is not True:
        report["probe"] = "the named hook is not installed in the user settings"
        return report

    with tempfile.TemporaryDirectory(prefix="sutra-doctor-") as temporary:
        root = pathlib.Path(temporary)
        store = root / "store"
        try:
            payload, full = _doctor_payload(root)
        except OSError as exc:
            report["probe"] = f"could not build the probe: {exc}"
            return report
        env = dict(os.environ)
        env.update({"SUTRA_ENABLED": "1", "SUTRA_STORE": "1",
                    "SUTRA_STORE_DIR": str(store),
                    "HERMES_HOME": str(root),
                    "SUTRA_STATS_FILE": str(root / "stats.jsonl")})
        try:
            result = subprocess.run(
                [command, *args], input=json.dumps(payload), capture_output=True,
                text=True, timeout=HOOK_TIMEOUT_SECONDS, env=env)
            response = json.loads(result.stdout or "{}")
            updated = response.get("hookSpecificOutput", {}).get("updatedToolOutput")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            report["probe"] = f"hook did not answer: {exc}"
            return report
        output = _probe_output(updated)
        match = ID_RE.search(output)
        if "[sutra:" not in output or match is None:
            report["probe"] = "hook left the synthetic large result unchanged"
            return report
        if DOCTOR_CLUE in output:
            report["probe"] = "hook did not omit the buried clue"
            return report
        try:
            restored = _restore_probe(match.group(1), store)
        except Exception as exc:  # a broken recovery path is a failed probe
            report["probe"] = f"restore raised: {exc}"
            return report
        status = restored.get("status") if isinstance(restored, dict) else None
        content = restored.get("content", "") if isinstance(restored, dict) else ""
        recovered = status == "ok" and isinstance(content, str) and DOCTOR_CLUE in content
        report.update({
            "original_bytes": len(full.encode()),
            "compressed_bytes": len(output.encode()),
            "id": match.group(1),
            "recovered": recovered,
            "probe": ("compressed and restored" if status == "ok"
                      else f"restore status: {status}"),
        })
        report["ok"] = (report["hook"] is True
                        and report["mcp"] == "connected"
                        and report["recovered"] is True)
    return report


def format_doctor(report: dict) -> str:
    if "error" in report:
        return f"claude: error: {report['error']}"
    probe = report.get("probe", "not run")
    if report.get("recovered"):
        probe += (f" ({report.get('original_bytes', 0):,} B -> "
                  f"{report.get('compressed_bytes', 0):,} B)")
    return (f"claude: hook={'yes' if report.get('hook') is True else 'no'} "
            f"mcp={report.get('mcp')} automatic={'yes' if report.get('ok') else 'no'} "
            f"probe={probe}")


def install(*, python: str | None = None, dry_run: bool = False) -> int:
    """Install the hook and MCP entry; verify both. Returns an exit code."""
    if not binary():
        print("sutra: claude is not installed; nothing to configure",
              file=sys.stderr)
        return 1
    python = python or sys.executable
    command, args = hook_command(python)
    path = settings_path()
    existed = path.exists()
    try:
        document = load_settings(path)
        changed = add_hook(document, command, args)
    except ValueError as exc:
        print(f"sutra: refusing to edit Claude Code settings: {exc}",
              file=sys.stderr)
        return 1

    if dry_run:
        # The Claude CLI creates ~/.claude.json even for `mcp get`, so a dry run
        # must not invoke it. Report the intended action instead.
        if changed:
            print(f"sutra: dry run, {path} would gain the PostToolUse hook "
                  f"({command} {' '.join(args)})")
        else:
            print(f"sutra: {path} already has the PostToolUse hook")
        print(f"sutra: dry run, would run if absent: claude {' '.join(_mcp_add_args(python))}")
        return 0

    existing, detail = _mcp_get()
    if existing and not _mcp_matches(detail, python):
        print("sutra: a different sutra MCP entry already exists in Claude Code; "
              "run `sutra uninstall --client claude` first", file=sys.stderr)
        return 1

    if changed:
        try:
            _write_json(path, document)
        except (OSError, ValueError) as exc:
            print(f"sutra: could not write {path}: {exc}", file=sys.stderr)
            return 1
        print(f"sutra: installed the PostToolUse hook in {path}")
    else:
        print(f"sutra: the PostToolUse hook is already installed in {path}")

    added_mcp = False
    if not existing:
        result = _run(_mcp_add_args(python), timeout=60)
        if result is None or result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip() if result else ""
            print(f"sutra: could not register the MCP server: {message or 'claude failed'}",
                  file=sys.stderr)
            _rollback_hook(path, document, command, args, existed=existed)
            return 1
        added_mcp = True
        print("sutra: registered the sutra MCP server (user scope)")

    connected, detail = _mcp_get()
    if connected and _mcp_connected(detail):
        print("sutra: Claude Code reports the MCP server connected")
    elif connected:
        print("sutra: the hook is installed, but Claude Code could not connect "
              "the MCP server; run `claude mcp get sutra` for the error", file=sys.stderr)
        if added_mcp:
            _run(["mcp", "remove", SERVER_NAME, "--scope", "user"], timeout=45)
        _rollback_hook(path, document, command, args, existed=existed)
        return 1

    report = doctor(python=python)
    print("sutra: " + format_doctor(report))
    if not report.get("ok"):
        print("sutra: the automatic integration is installed but the doctor probe "
              "failed; run `sutra doctor` after fixing the reported issue",
              file=sys.stderr)
        return 1
    return 0


def _rollback_hook(path: pathlib.Path, document: dict, command: str,
                    args: list[str], *, existed: bool) -> None:
    """Remove a hook written by a failed install, keeping the user's file."""
    try:
        remove_sutra_hooks(document)
        if existed:
            _write_json(path, document)
        else:
            path.unlink(missing_ok=True)
        print("sutra: rolled back the hook because the MCP recovery server "
              "could not be connected", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f"sutra: could not roll back {path}: {exc}; run `sutra uninstall "
              "--client claude`", file=sys.stderr)


def uninstall(*, python: str | None = None, dry_run: bool = False) -> int:
    python = python or sys.executable
    command, args = hook_command(python)
    path = settings_path()
    try:
        document = load_settings(path)
        changed = remove_sutra_hooks(document)
    except ValueError as exc:
        print(f"sutra: refusing to edit Claude Code settings: {exc}",
              file=sys.stderr)
        return 1

    if dry_run:
        # Do not ask the Claude CLI: `mcp get` creates ~/.claude.json.
        if changed:
            print(f"sutra: dry run, {path} would lose the PostToolUse hook")
        print("sutra: dry run, would remove the sutra MCP server if registered")
        return 0

    existing, _ = _mcp_get()

    if changed:
        try:
            _write_json(path, document)
        except (OSError, ValueError) as exc:
            print(f"sutra: could not write {path}: {exc}", file=sys.stderr)
            return 1
        print(f"sutra: removed the PostToolUse hook from {path}")
    if existing:
        result = _run(["mcp", "remove", SERVER_NAME, "--scope", "user"], timeout=45)
        if result is None or result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip() if result else ""
            if "not found" not in message.lower():
                print(f"sutra: could not remove the MCP server: {message}", file=sys.stderr)
                return 1
        print("sutra: removed the sutra MCP server")
    return 0


def status() -> dict:
    """A read-only report; never edits a file."""
    path = settings_path()
    command, args = hook_command()
    report = {
        "client": "claude",
        "binary": binary(),
        "settings": str(path),
        "hook": False,
        "mcp": "not configured",
        "automatic": False,
    }
    try:
        report["hook"] = find_hook(load_settings(path)) is not None
        report["automatic"] = report["hook"] is True
    except ValueError as exc:
        report["hook"] = f"error: {exc}"
    existing, detail = _mcp_get()
    if existing:
        report["mcp"] = "connected" if _mcp_connected(detail) else "configured, not connected"
    return report
