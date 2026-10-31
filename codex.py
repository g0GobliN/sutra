"""Codex CLI integration: PostToolUse feedback replacement plus MCP recovery.

Codex 0.166.0 supports lifecycle hooks. A ``PostToolUse`` command hook can
return ``continue: false`` with a ``stopReason``: Codex replaces the tool result
with that text, marks the hook run ``Stopped`` and continues the turn. SUTRA
uses that contract to put the compressed copy (and its recovery marker) in front
of the model. The MCP server stays registered for ``sutra_restore``; the hook
and MCP server share one store.

Codex rejects ``updatedMCPToolOutput`` explicitly, so the feedback contract is
the only supported replacement path. The installer writes a user-level
``hooks.json`` entry, using Codex's own ``codex mcp add``/``remove`` commands for
MCP, so the user's ``config.toml`` keeps its comments and other servers. Every
read is bounded; a malformed file or a different existing SUTRA entry is refused
rather than overwritten.

Codex requires a one-time trust review for a user hook. The installer cannot
record that trust without bypassing Codex's security model, so it prints the
``/hooks`` instruction and ``doctor`` reports ``limited`` until the hook is
trusted. No claim of automatic interception is made before that review.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib

CLIENT = "codex"
SERVER_NAME = "sutra"
HOOK_MARKER = "sutra-codex-hook.py"
HOOK_MATCHER = "Bash|exec_command"
HOOK_TIMEOUT_SECONDS = 60
DOCTOR_CLUE = "SUTRA-CODEX-DOCTOR-CLUE"
ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")
HOST_LIMIT = (
    "Codex replaces the model-visible result with PostToolUse feedback; "
    "the hook needs one-time trust in /hooks and Codex records the run as Stopped"
)


def binary() -> str | None:
    """The Codex CLI, or None when it is not installed."""
    return shutil.which("codex")


def config_home() -> pathlib.Path:
    override = os.environ.get("CODEX_HOME")
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path.home() / ".codex"


def config_path() -> pathlib.Path:
    return config_home() / "config.toml"


def hooks_path() -> pathlib.Path:
    return config_home() / "hooks.json"


def hook_script() -> pathlib.Path:
    return pathlib.Path(__file__).with_name("scripts") / HOOK_MARKER


def _hook_command(python: str) -> str:
    return f"{shlex.quote(python)} {shlex.quote(str(hook_script()))}"


def _run(args: list[str], timeout: int = 45):
    executable = binary()
    if executable is None:
        return None
    env = dict(os.environ)
    env["CODEX_HOME"] = str(config_home())
    try:
        return subprocess.run(
            [executable, *args], capture_output=True, text=True,
            timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def _list() -> tuple[list[dict] | None, str | None]:
    result = _run(["mcp", "list", "--json"])
    if result is None:
        return None, "codex did not run"
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "codex failed").strip()[-300:]
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        return None, f"codex returned invalid JSON: {exc}"
    if not isinstance(payload, list):
        return None, "codex returned a non-list MCP report"
    return [item for item in payload if isinstance(item, dict)], None


def _entry(entries: list[dict] | None) -> dict | None:
    if not entries:
        return None
    for entry in entries:
        if entry.get("name") == SERVER_NAME:
            return entry
    return None


def _matches(entry: dict, python: str) -> bool:
    transport = entry.get("transport")
    if not isinstance(transport, dict):
        return False
    return (
        transport.get("type") == "stdio"
        and transport.get("command") == python
        and list(transport.get("args") or []) == ["-m", "sutra.mcp"]
    )


# -- hooks.json --------------------------------------------------------------

def _load_hooks(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {"hooks": {}}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not a JSON object")
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{path}: 'hooks' is not an object")
    groups = hooks.get("PostToolUse", [])
    if not isinstance(groups, list):
        raise ValueError(f"{path}: 'hooks.PostToolUse' is not an array")
    return document


def _locate_hook(document: dict) -> tuple[int, int] | None:
    groups = document.get("hooks", {}).get("PostToolUse", [])
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        for handler_index, handler in enumerate(handlers):
            if (isinstance(handler, dict)
                    and HOOK_MARKER in str(handler.get("command", ""))):
                return group_index, handler_index
    return None


def _group(command: str) -> dict:
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [{
            "type": "command",
            "command": command,
            "timeout": 30,
            "statusMessage": "Compressing tool output with SUTRA",
        }],
    }


def _write_hooks(path: pathlib.Path, document: dict) -> pathlib.Path | None:
    from . import installer

    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return installer._write(path, text)
    temporary = path.with_name(path.name + ".sutra-tmp")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return None


def _installed_hook() -> dict:
    """Read-only hook state: command, indices and Codex's recorded trust."""
    state: dict = {"installed": False, "path": str(hooks_path()),
                   "command": None, "trusted": None}
    try:
        document = _load_hooks(hooks_path())
    except ValueError as exc:
        state["error"] = str(exc)
        return state
    located = _locate_hook(document)
    if located is None:
        return state
    group_index, handler_index = located
    handler = document["hooks"]["PostToolUse"][group_index]["hooks"][handler_index]
    state.update({
        "installed": True,
        "command": handler.get("command"),
        "group_index": group_index,
        "handler_index": handler_index,
    })
    trust_path = config_path()
    if not trust_path.is_file():
        return state
    try:
        config = tomllib.loads(trust_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return state
    hooks = config.get("hooks")
    table = hooks.get("state") if isinstance(hooks, dict) else None
    if not isinstance(table, dict):
        return state
    key = f"{hooks_path()}:post_tool_use:{group_index}:{handler_index}"
    state["trusted"] = key in table
    return state


def hook_installed() -> bool:
    """Read-only: is a SUTRA PostToolUse handler present in hooks.json?"""
    return bool(_installed_hook()["installed"])


def _install_hook(python: str, *, dry_run: bool) -> tuple[int, bool]:
    path = hooks_path()
    try:
        document = _load_hooks(path)
    except ValueError as exc:
        print(f"sutra: could not read Codex hooks: {exc}", file=sys.stderr)
        return 1, False
    command = _hook_command(python)
    located = _locate_hook(document)
    if located is not None:
        handler = document["hooks"]["PostToolUse"][located[0]]["hooks"][located[1]]
        if handler.get("command") == command and handler.get("type") == "command":
            print("sutra: Codex already has the SUTRA PostToolUse hook; nothing to do")
            return 0, False
        if dry_run:
            print(f"sutra: dry run, would update the SUTRA Codex hook in {path}")
            return 0, False
        handler["command"] = command
        handler.setdefault("type", "command")
        handler.setdefault("timeout", 30)
        backup = _write_hooks(path, document)
        print(f"sutra: updated the SUTRA PostToolUse hook in {path}")
        if backup:
            print(f"sutra: backup at {backup}")
        print("sutra: run /hooks in Codex and trust the SUTRA hook")
        return 0, True
    if dry_run:
        print(f"sutra: dry run, would add the SUTRA PostToolUse hook to {path}")
        return 0, False
    document.setdefault("hooks", {}).setdefault("PostToolUse", []).append(
        _group(command))
    backup = _write_hooks(path, document)
    print(f"sutra: installed the SUTRA PostToolUse hook in {path}")
    if backup:
        print(f"sutra: backup at {backup}")
    print("sutra: Codex requires one-time trust: run /hooks and trust the SUTRA hook")
    return 0, True


def _remove_hook(*, dry_run: bool) -> tuple[int, bool]:
    path = hooks_path()
    try:
        document = _load_hooks(path)
    except ValueError as exc:
        print(f"sutra: could not read Codex hooks: {exc}", file=sys.stderr)
        return 1, False
    located = _locate_hook(document)
    if located is None:
        print("sutra: Codex has no SUTRA PostToolUse hook; nothing to do")
        return 0, False
    if dry_run:
        print(f"sutra: dry run, would remove the SUTRA PostToolUse hook from {path}")
        return 0, False
    group_index, handler_index = located
    groups = document["hooks"]["PostToolUse"]
    del groups[group_index]["hooks"][handler_index]
    if not groups[group_index].get("hooks"):
        del groups[group_index]
    if not groups:
        document["hooks"].pop("PostToolUse", None)
    if document == {"hooks": {}}:
        path.unlink(missing_ok=True)
    else:
        backup = _write_hooks(path, document)
        if backup:
            print(f"sutra: backup at {backup}")
    print(f"sutra: removed the SUTRA PostToolUse hook from {path}")
    return 0, True


# -- MCP ---------------------------------------------------------------------

def _install_mcp(python: str, *, dry_run: bool) -> int:
    if config_home().is_dir():
        entries, error = _list()
    else:
        entries, error = [], None
    if entries is None:
        print(f"sutra: could not read Codex MCP config: {error}", file=sys.stderr)
        return 1
    entry = _entry(entries)
    if entry is not None:
        if _matches(entry, python):
            print("sutra: Codex already has the sutra MCP entry; nothing to do")
            return 0
        print("sutra: a different sutra MCP entry already exists in Codex; "
              "run `sutra uninstall --client codex` first", file=sys.stderr)
        return 1
    command = ["mcp", "add", SERVER_NAME, "--", python, "-m", "sutra.mcp"]
    if dry_run:
        print(f"sutra: dry run, would run: codex {' '.join(command)}")
        return 0
    config_home().mkdir(parents=True, exist_ok=True)
    result = _run(command, timeout=60)
    if result is None or result.returncode != 0:
        detail = ((result.stderr or result.stdout) if result else "codex did not run")
        print(f"sutra: could not register the Codex MCP server: "
              f"{(detail or '').strip()[-300:]}", file=sys.stderr)
        return 1
    entries, error = _list()
    if entries is None or not _matches(_entry(entries) or {}, python):
        print(f"sutra: Codex MCP registration could not be verified: "
              f"{error or 'entry missing'}", file=sys.stderr)
        _run(["mcp", "remove", SERVER_NAME], timeout=45)
        return 1
    print("sutra: registered the sutra MCP server with Codex")
    return 0


def _remove_mcp(*, dry_run: bool) -> int:
    if binary() is None:
        print("sutra: codex is not installed; nothing to uninstall")
        return 0
    if not config_home().is_dir():
        print("sutra: Codex has no sutra MCP entry; nothing to do")
        return 0
    entries, error = _list()
    if entries is None:
        print(f"sutra: could not read Codex MCP config: {error}", file=sys.stderr)
        return 1
    if _entry(entries) is None:
        print("sutra: Codex has no sutra MCP entry; nothing to do")
        return 0
    if dry_run:
        print("sutra: dry run, would remove the sutra MCP entry from Codex")
        return 0
    result = _run(["mcp", "remove", SERVER_NAME], timeout=45)
    if result is None or result.returncode != 0:
        detail = ((result.stderr or result.stdout) if result else "codex did not run")
        print(f"sutra: could not remove the Codex MCP entry: "
              f"{(detail or '').strip()[-300:]}", file=sys.stderr)
        return 1
    print("sutra: removed the sutra MCP entry from Codex")
    return 0


# -- public operations -------------------------------------------------------

def status() -> dict:
    """Read-only report; ``automatic`` requires an installed, trusted hook."""
    hook = _installed_hook()
    report = {
        "client": CLIENT,
        "binary": binary(),
        "config": str(config_path()),
        "hook": "installed" if hook["installed"] else "not installed",
        "hook_path": hook["path"],
        "trusted": hook.get("trusted"),
        "mcp": "not configured",
        "automatic": bool(hook["installed"] and hook.get("trusted") is True),
        "tier": "automatic" if hook["installed"] else "mcp-only",
        "host_limit": HOST_LIMIT,
    }
    if report["binary"] is None:
        return report
    if config_home().is_dir():
        entries, error = _list()
        if entries is None:
            report["mcp"] = f"error: {error}"
        else:
            entry = _entry(entries)
            if entry is not None:
                report["mcp"] = ("configured" if entry.get("enabled", True)
                                 else "configured, disabled")
    return report


def install(*, python: str | None = None, dry_run: bool = False) -> int:
    """Install the PostToolUse hook and the MCP recovery server."""
    if binary() is None:
        print("sutra: codex is not installed; nothing to configure", file=sys.stderr)
        return 1
    python = python or sys.executable
    code, hook_added = _install_hook(python, dry_run=dry_run)
    if code:
        return code
    code = _install_mcp(python, dry_run=dry_run)
    if code and hook_added:
        _remove_hook(dry_run=False)
    return code


def uninstall(*, dry_run: bool = False) -> int:
    """Remove only the hook and MCP entry SUTRA owns.

    The hook is removed even when the Codex binary has since disappeared; a
    leftover hooks.json entry must not outlive the integration.
    """
    code, _ = _remove_hook(dry_run=dry_run)
    return max(code, _remove_mcp(dry_run=dry_run))


def _probe(command: str, python: str) -> tuple[bool, str]:
    """Run the configured hook on a synthetic Bash event and restore its id."""
    text = "\n".join(
        [f"line {index} nominal" for index in range(3000)]
        + [f"{DOCTOR_CLUE}"] + [f"line {index} tail" for index in range(1000)])
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "sutra-doctor",
        "tool_name": "Bash",
        "tool_response": text,
    }
    with tempfile.TemporaryDirectory(prefix="sutra-codex-doctor-") as temporary:
        root = pathlib.Path(temporary)
        store = root / "store"
        env = dict(os.environ)
        env.update({"SUTRA_ENABLED": "1", "SUTRA_STORE": "1",
                    "SUTRA_STORE_DIR": str(store),
                    "HERMES_HOME": str(root),
                    "SUTRA_STATS_FILE": str(root / "stats.jsonl")})
        try:
            argv = shlex.split(command)
            if not argv:
                argv = [python, str(hook_script())]
            result = subprocess.run(
                argv, input=json.dumps(payload), capture_output=True, text=True,
                timeout=HOOK_TIMEOUT_SECONDS, env=env)
            response = json.loads(result.stdout or "{}")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return False, f"hook did not answer: {exc}"
        output = response.get("stopReason")
        if response.get("continue") is not False or not isinstance(output, str):
            return False, "hook left the synthetic large result unchanged"
        match = ID_RE.search(output)
        if "[sutra:" not in output or match is None:
            return False, "hook did not return a recovery marker"
        keys = ("SUTRA_ENABLED", "SUTRA_STORE", "SUTRA_STORE_DIR",
                "HERMES_HOME", "SUTRA_STATS_FILE")
        saved = {key: os.environ.get(key) for key in keys}
        os.environ.update({key: env[key] for key in keys})
        try:
            from . import core
            raw = core.restore({"id": match.group(1), "mode": "search",
                                "query": DOCTOR_CLUE, "context": 1})
            restored = json.loads(raw)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if restored.get("status") != "ok" or DOCTOR_CLUE not in restored.get("content", ""):
            return False, "the marker's id did not restore the original clue"
    return True, "hook compressed and restored the synthetic result"


def doctor(*, python: str | None = None) -> dict:
    """Report the real Codex state; trust and a live probe are required."""
    hook = _installed_hook()
    report = {
        "client": CLIENT,
        "binary": binary(),
        "config": str(config_path()),
        "hook": bool(hook["installed"]),
        "trusted": hook.get("trusted"),
        "mcp": "not configured",
        "automatic": False,
        "tier": "mcp-only",
        "host_limit": HOST_LIMIT,
        "ok": False,
        "status": "fail",
        "probe": "codex is not installed",
    }
    if report["binary"] is None:
        report["status"] = "not-installed"
        return report
    if hook.get("error"):
        report["probe"] = f"could not read hooks.json: {hook['error']}"
        return report
    if not hook["installed"]:
        report["probe"] = "the SUTRA PostToolUse hook is not installed"
        return report
    entries, error = _list()
    if entries is None:
        report["probe"] = f"could not read MCP config: {error}"
        return report
    entry = _entry(entries)
    if entry is not None:
        report["mcp"] = ("configured" if entry.get("enabled", True)
                         else "configured, disabled")
    passed, detail = _probe(str(hook.get("command") or ""),
                            python or sys.executable)
    if not passed:
        report["probe"] = detail
        return report
    if entry is None:
        report["probe"] = "the hook works, but sutra_restore is not registered"
        return report
    if not _matches(entry, python or sys.executable):
        report["mcp"] = "different command"
        report["probe"] = "the registered sutra MCP command does not match this install"
        return report
    if entry.get("enabled", True) is not True:
        report["mcp"] = "configured, disabled"
        report["probe"] = "the sutra MCP server is disabled in Codex"
        return report
    report["automatic"] = bool(hook.get("trusted") is True)
    if report["automatic"]:
        report.update({"ok": True, "status": "ok",
                       "tier": "automatic",
                       "probe": "hook compressed and restored; hook is trusted"})
    else:
        report.update({"ok": True, "status": "limited", "tier": "hook-untrusted",
                       "probe": "hook compressed and restored; run /hooks in "
                                "Codex to trust it before automatic use"})
    return report


def format_doctor(report: dict) -> str:
    if "error" in report:
        return f"codex: error: {report['error']}"
    trusted = report.get("trusted")
    trusted_text = "unknown" if trusted is None else ("yes" if trusted else "no")
    return (f"codex: hook={'yes' if report.get('hook') else 'no'} "
            f"trusted={trusted_text} mcp={report.get('mcp')} "
            f"automatic={'yes' if report.get('automatic') else 'no'} "
            f"status={report.get('status')} probe={report.get('probe')}")
