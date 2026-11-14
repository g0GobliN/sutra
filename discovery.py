"""Read-only discovery of the agents SUTRA can integrate with.

`detect` must never configure anything: every path here is a read, and every
host probe is a subprocess bounded by a short timeout. A client is reported as
installed only when there is concrete evidence: an executable on PATH, or a
known configuration file. An empty directory with a familiar name is not
evidence.

The capability tier is deliberately separate from installation. Cursor and
Windsurf have no documented transparent result-replacement path today, so they
are reported as `mcp-only`. Codex gained a PostToolUse feedback contract that
replaces the model-visible result; it is reported as `automatic` only when the
SUTRA hook is actually installed, and `mcp-only` otherwise. Detecting a binary
never implies that SUTRA can intercept its output.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

CLIENTS = ("hermes", "claude", "opencode", "openclaw", "cursor", "codex",
           "windsurf")

_AUTOMATIC = {
    "hermes": "native tool_execution middleware",
    "claude": "PostToolUse hook (updatedToolOutput) + MCP recovery",
    "opencode": "generated tool.execute.after plugin + sutra_restore",
    "openclaw": "generated tool.execute.after plugin + sutra_restore",
}
_MCP_ONLY = {
    "cursor": "MCP server; built-in tool output cannot be replaced",
    "codex": "MCP server; SUTRA PostToolUse hook is not installed",
    "windsurf": "MCP server; no documented output-replacement hook",
}
_CODEX_HOOK = "PostToolUse feedback replacement + MCP recovery"
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
    output = "\n".join(part for part in (result.stdout, result.stderr)
                      if part and part.strip()).strip()
    if not output:
        if result.returncode:
            return None, f"version probe exited with status {result.returncode} and no output"
        return None, "version probe returned no output"
    first = next((line.strip() for line in output.splitlines() if line.strip()), "")
    detail = f": {first[:120]}" if first else ""
    if result.returncode:
        # A failed probe may print its runtime's version (for example, Node's
        # "Node.js v24.14.0" after a loader error). That is not the client's
        # version, so a nonzero exit never yields one.
        return None, f"version probe exited with status {result.returncode}{detail}"
    for line in output.splitlines():
        match = _VERSION_RE.search(line)
        if match:
            return match.group(1), None
    return None, f"version probe printed no version{detail}"


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


def _openclaw_config() -> pathlib.Path | None:
    """OpenClaw's config file, or the SUTRA plugin already written into it.

    The plugin can be installed before the OpenClaw host exists, so it is
    concrete evidence of the config tree in the same way OpenCode's generated
    plugin is. The config directory honors ``SUTRA_OPENCLAW_CONFIG`` and
    otherwise defaults to ``~/.openclaw``.
    """
    try:
        from . import openclaw
        config = openclaw.openclaw_config_dir() / "openclaw.json"
        if config.is_file():
            return config
        plugin = openclaw.plugin_path()
        return plugin if plugin.is_file() else None
    except Exception:
        return None


def _codex_hook() -> bool:
    """True when Codex's user hooks.json carries the SUTRA handler."""
    try:
        from . import codex
        return codex.hook_installed()
    except Exception:
        return False


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
    if name == "openclaw":
        return (("openclaw",), _openclaw_config(), "automatic",
                _AUTOMATIC["openclaw"])
    if name == "cursor":
        return (("cursor", "cursor-agent"), _cursor_config(), "mcp-only",
                _MCP_ONLY["cursor"])
    if name == "codex":
        if _codex_hook():
            return (("codex",), _codex_config(), "automatic", _CODEX_HOOK)
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
