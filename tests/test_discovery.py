"""Read-only client discovery: evidence, capability tiers and no writes.

The probes are deterministic in these tests; version subprocesses are replaced
so the suite neither depends on the developer's installed hosts nor lets a host
binary mutate a temporary home.
"""

import json

import pytest

from conftest import plugin_module

discovery = plugin_module("discovery")
cli = plugin_module("cli")


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HERMES_HOME", str(home / ".hermes"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("SUTRA_OPENCODE_CONFIG", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(discovery.shutil, "which", lambda _name: None)
    monkeypatch.setattr(discovery, "_probe_version", lambda _path: ("9.9.9", None))
    return tmp_path, home, xdg


def test_empty_known_directories_are_not_evidence(isolated):
    root, home, xdg = isolated
    for relative in (".hermes", ".cursor", ".codex", ".claude"):
        (home / relative).mkdir()
    for relative in ("devin", "opencode", "hermes", "cursor", "codex"):
        (xdg / relative).mkdir()

    rows = discovery.detect()
    assert not any(row["installed"] for row in rows)
    assert all(row["tier"] == "not installed" for row in rows)
    assert all(row["automatic"] is False for row in rows)
    assert all(row["evidence"] == [] for row in rows)


def test_executable_is_concrete_evidence_with_a_parsed_version(isolated, monkeypatch):
    root, _, _ = isolated
    executable = root / "bin" / "claude"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(discovery, "_executable", lambda *names: str(executable))
    monkeypatch.setattr(discovery, "_probe_version",
                        lambda _path: ("2.1.316", None))

    rows = discovery.detect(["claude"])
    assert len(rows) == 1
    row = rows[0]
    assert row["client"] == "claude"
    assert row["installed"] is True
    assert row["tier"] == "automatic"
    assert row["automatic"] is True
    assert row["version"] == "2.1.316"
    assert row["version_error"] is None
    assert row["executable"] == str(executable)
    assert row["evidence"] == [f"executable {executable} (2.1.316)"]


def test_config_file_is_evidence_but_not_an_automatic_claim(isolated):
    root, home, _ = isolated
    mcp = home / ".cursor" / "mcp.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text("{}\n", encoding="utf-8")

    row = discovery.detect(["cursor"])[0]
    assert row["installed"] is True
    assert row["tier"] == "mcp-only"
    assert row["automatic"] is False
    assert row["mechanism"].startswith("MCP server")
    assert row["config"] == str(mcp)
    assert row["evidence"] == [f"config {mcp}"]


def test_hermes_config_yields_automatic_tier_without_a_binary(isolated):
    root, home, _ = isolated
    config = home / ".hermes" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("plugins: []\n", encoding="utf-8")

    row = discovery.detect(["hermes"])[0]
    assert row["installed"] is True
    assert row["tier"] == "automatic"
    assert row["automatic"] is True
    assert row["config"] == str(config)
    assert row["executable"] is None


def test_a_failed_version_probe_does_not_hide_an_installed_binary(isolated, monkeypatch):
    root, _, _ = isolated
    monkeypatch.setattr(discovery, "_executable", lambda *names: "/usr/bin/codex")
    monkeypatch.setattr(discovery, "_probe_version",
                        lambda _path: (None, "version probe timed out"))

    row = discovery.detect(["codex"])[0]
    assert row["installed"] is True
    assert row["tier"] == "mcp-only"
    assert row["version"] is None
    assert row["version_error"] == "version probe timed out"
    assert "version probe timed out" in row["evidence"][0]


def test_cli_json_is_a_read_only_envelope_with_client_filter(isolated, capsys):
    root, home, _ = isolated
    mcp = home / ".cursor" / "mcp.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text("{}\n", encoding="utf-8")

    assert cli.main(["detect", "--json", "--client", "cursor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert len(payload["clients"]) == 1
    assert payload["clients"][0]["client"] == "cursor"
    assert payload["clients"][0]["tier"] == "mcp-only"

    assert cli.main(["detect", "--client", "opencode"]) == 0
    output = capsys.readouterr().out
    assert "opencode" in output
    assert "not installed" in output
    assert "mechanism=none" in output


def test_detect_never_writes_or_mutates_configuration(isolated, capsys):
    root, home, xdg = isolated
    config = home / ".cursor" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"mcpServers": {"existing": {}}}\n', encoding="utf-8")
    opencode_config = xdg / "opencode" / "opencode.json"
    opencode_config.parent.mkdir(parents=True)
    opencode_config.write_text('{\n  "theme": "dark"\n}\n', encoding="utf-8")

    def snapshot():
        return {
            str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode)
            for path in sorted(root.rglob("*")) if path.is_file()
        }

    before = snapshot()
    assert discovery.detect()
    assert cli.main(["detect"]) == 0
    assert cli.main(["detect", "--json"]) == 0
    capsys.readouterr()
    after = snapshot()
    assert after == before
    assert not list(home.rglob("*.bak-*"))
    assert not list(xdg.rglob("*.bak-*"))
