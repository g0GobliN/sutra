"""Read-only discovery of the agents SUTRA can integrate with.

`detect` must never configure anything: every path here is a read, and every
host probe is a subprocess bounded by a short timeout. A client is reported as
installed only when there is concrete evidence: an executable on PATH, or a
known configuration file. An empty directory with a familiar name is not
evidence.

The capability tier is deliberately separate from installation. Cursor, Codex
and Windsurf have no documented transparent result-replacement path today, so
they are reported as `mcp-only`; detecting their binary must never imply that
SUTRA can intercept their tool output.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

CLIENTS = ("hermes", "claude", "opencode", "cursor", "codex", "windsurf")

_AUTOMATIC = {
    "hermes": "native tool_execution middleware",
    "claude": "PostToolUse hook (updatedToolOutput) + MCP recovery",
    "opencode": "generated tool.execute.after plugin + sutra_restore",
}
_MCP_ONLY = {
    "cursor": "MCP server; built-in tool output cannot be replaced",
    "codex": "MCP server; documented hooks do not replace tool output",
    "windsurf": "MCP server; no documented output-replacement hook",
}
_VERSION_RE = re.compile(
    r"(?<![0-9A-Za-z_.-])v?(\d+\.\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?)")


def _home() -> pathlib.Path:
    # expanduser reads HOME at call time, which keeps tests and per-profile
    # invocations isolated without caching a developer's real home.
    return pathlib.Path(os.path.expanduser("~"))


def _config_home() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()


def _first_file(*paths: pathlib.Path) -> pathlib.Path | None:
    for path in paths:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _executable(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _probe_version(executable: str) -> tuple[str | None, str | None]:
    """Parse a bounded `--version` probe; never let a host command hang."""
    try:
        result = subprocess.run(
            [executable, "--version"], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=3)
    except subprocess.TimeoutExpired:
        return None, "version probe timed out"
    except OSError as exc:
        return None, f"version probe failed: {exc}"
    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return None, "version probe returned no output"
    first = next((line.strip() for line in output.splitlines() if line.strip()), "")
    match = _VERSION_RE.search(first)
    return (match.group(1) if match else first[:120], None)


def _hermes_config() -> pathlib.Path | None:
    bases = []
    override = os.environ.get("HERMES_HOME")
    if override:
        bases.append(pathlib.Path(override).expanduser())
    bases.extend((_home() / ".hermes", _config_home() / "hermes"))
    candidates = []
    for base in bases:
        candidates.extend((base / "config.yaml", base / "config.yml",
                           base / "config.json"))
    return _first_file(*candidates)


def _claude_config() -> pathlib.Path | None:
    try:
        from . import claude_code
        path = claude_code.settings_path()
    except Exception:
        path = _home() / ".claude" / "settings.json"
    return path if path.is_file() else None


def _opencode_config() -> pathlib.Path | None:
    try:
        from . import installer, opencode
        config = installer.opencode_config()
        if config.is_file():
            return config
        plugin = opencode.plugin_path(config)
        return plugin if plugin.is_file() else None
    except Exception:
        return None


def _cursor_config() -> pathlib.Path | None:
    return _first_file(_home() / ".cursor" / "mcp.json",
                       _config_home() / "cursor" / "mcp.json")


def _codex_config() -> pathlib.Path | None:
    return _first_file(_home() / ".codex" / "config.toml",
                       _config_home() / "codex" / "config.toml")


def _windsurf_config() -> pathlib.Path | None:
    home = _home()
    appdata = pathlib.Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    return _first_file(
        _config_home() / "devin" / "mcp_config.json",
        home / "Library" / "Application Support" / "Devin" / "mcp_config.json",
        appdata / "devin" / "mcp_config.json",
        home / ".codeium" / "windsurf" / "mcp_config.json",
    )


def _spec(name: str) -> tuple[tuple[str, ...], pathlib.Path | None, str, str]:
    if name == "hermes":
        return (("hermes",), _hermes_config(), "automatic",
                _AUTOMATIC["hermes"])
    if name == "claude":
        return (("claude",), _claude_config(), "automatic", _AUTOMATIC["claude"])
    if name == "opencode":
        return (("opencode",), _opencode_config(), "automatic",
                _AUTOMATIC["opencode"])
    if name == "cursor":
        return (("cursor", "cursor-agent"), _cursor_config(), "mcp-only",
                _MCP_ONLY["cursor"])
    if name == "codex":
        return (("codex",), _codex_config(), "mcp-only", _MCP_ONLY["codex"])
    if name == "windsurf":
        return (("windsurf",), _windsurf_config(), "mcp-only",
                _MCP_ONLY["windsurf"])
    raise ValueError(f"unknown client: {name}")


def _row(name: str) -> dict:
    names, config, tier, mechanism = _spec(name)
    executable = _executable(*names)
    version = version_error = None
    if executable:
        version, version_error = _probe_version(executable)
    installed = bool(executable or config)
    evidence: list[str] = []
    if executable:
        suffix = f" ({version})" if version else (
            f" (version unavailable: {version_error})" if version_error else "")
        evidence.append(f"executable {executable}{suffix}")
    if config:
        evidence.append(f"config {config}")
    return {
        "client": name,
        "installed": installed,
        "tier": tier if installed else "not installed",
        "automatic": bool(installed and tier == "automatic"),
        "mechanism": mechanism if installed else None,
        "executable": executable,
        "version": version,
        "version_error": version_error,
        "config": str(config) if config else None,
        "evidence": evidence,
    }


def detect(clients: list[str] | tuple[str, ...] | None = None) -> list[dict]:
    """Return one read-only row per requested client, in stable order."""
    selected = list(clients) if clients else list(CLIENTS)
    unknown = [name for name in selected if name not in CLIENTS]
    if unknown:
        raise ValueError(f"unknown client(s): {', '.join(unknown)}")
    wanted = set(selected)
    return [_row(name) for name in CLIENTS if name in wanted]


def format_detect(rows: list[dict]) -> str:
    lines = ["SUTRA client detection (read-only)"]
    for row in rows:
        evidence = "; ".join(row["evidence"]) or "no executable or config evidence"
        if row["installed"]:
            lines.append(
                f"{row['client']:<9} installed  tier={row['tier']}  "
                f"automatic={'yes' if row['automatic'] else 'no'}  "
                f"mechanism={row['mechanism']}  evidence={evidence}")
        else:
            lines.append(
                f"{row['client']:<9} not installed  tier=not installed  "
                f"mechanism=none  evidence={evidence}")
    return "\n".join(lines)
