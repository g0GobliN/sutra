"""Codex CLI integration: MCP registration, never a false automatic claim.

Codex 0.166.0's documented ``PostToolUse`` hook can block or add context, but
it cannot replace a tool result. ``updatedMCPToolOutput`` is parsed and then
rejected as unsupported. SUTRA therefore configures Codex's official MCP
server for explicit ``sutra_compress`` and ``sutra_restore`` calls, and reports
the host limitation instead of pretending that registration intercepts output.

The installer uses Codex's own ``codex mcp add``/``remove`` commands, so the
user's ``config.toml`` keeps its comments and other servers. Every read is
bounded, and a different existing ``sutra`` entry is refused rather than
overwritten.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

CLIENT = "codex"
SERVER_NAME = "sutra"
HOST_LIMIT = (
    "Codex 0.166.0 PostToolUse cannot replace tool output; "
    "MCP registration is manual compression/recovery only"
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


def status() -> dict:
    """Read-only report; ``automatic`` is always false for Codex."""
    report = {
        "client": CLIENT,
        "binary": binary(),
        "config": str(config_path()),
        "mcp": "not configured",
        "automatic": False,
        "tier": "mcp-only",
        "host_limit": HOST_LIMIT,
    }
    if report["binary"] is None or not config_home().is_dir():
        return report
    entries, error = _list()
    if entries is None:
        report["mcp"] = f"error: {error}"
        return report
    entry = _entry(entries)
    if entry is not None:
        report["mcp"] = "configured" if entry.get("enabled", True) else "configured, disabled"
    return report


def install(*, python: str | None = None, dry_run: bool = False) -> int:
    """Register the MCP server with Codex's official CLI."""
    if binary() is None:
        print("sutra: codex is not installed; nothing to configure", file=sys.stderr)
        return 1
    python = python or sys.executable
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
            print("sutra: Codex remains MCP-only; no automatic output replacement")
            return 0
        print("sutra: a different sutra MCP entry already exists in Codex; "
              "run `sutra uninstall --client codex` first", file=sys.stderr)
        return 1
    command = ["mcp", "add", SERVER_NAME, "--", python, "-m", "sutra.mcp"]
    if dry_run:
        print(f"sutra: dry run, would run: codex {' '.join(command)}")
        print("sutra: Codex is MCP-only; this does not make output replacement automatic")
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
    print("sutra: Codex is MCP-only; no automatic output replacement is available")
    return 0


def uninstall(*, dry_run: bool = False) -> int:
    """Remove only the Codex MCP entry SUTRA owns."""
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


def doctor(*, python: str | None = None) -> dict:
    """Report the real Codex state; never claim automatic interception."""
    report = {
        "client": CLIENT,
        "binary": binary(),
        "config": str(config_path()),
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
    if not config_home().is_dir():
        report["probe"] = "Codex has no config directory; register the MCP server first"
        return report
    entries, error = _list()
    if entries is None:
        report["probe"] = f"could not read MCP config: {error}"
        return report
    entry = _entry(entries)
    if entry is None:
        report["probe"] = "the sutra MCP server is not registered with Codex"
        return report
    if not _matches(entry, python or sys.executable):
        report["mcp"] = "different command"
        report["probe"] = "the registered sutra MCP command does not match this install"
        return report
    if entry.get("enabled", True) is not True:
        report["mcp"] = "configured, disabled"
        report["probe"] = "the sutra MCP server is disabled in Codex"
        return report
    report.update({
        "mcp": "configured",
        "ok": True,
        "status": "limited",
        "probe": "MCP recovery is configured; automatic output replacement is unavailable",
    })
    return report


def format_doctor(report: dict) -> str:
    if "error" in report:
        return f"codex: error: {report['error']}"
    return (f"codex: mcp={report.get('mcp')} automatic=no "
            f"status={report.get('status')} probe={report.get('probe')}")
