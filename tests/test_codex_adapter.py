"""Codex integration tests: hook installation, trust and MCP registration.

The fake runner pins the hooks.json edit and MCP commands. The optional live
test uses Codex 0.166.0's real `mcp add/list/remove` commands against a
throwaway CODEX_HOME; it never touches the developer's Codex config and does
not call a model.
"""

import json
import shlex
import shutil
import sys
import types

import pytest

from conftest import plugin_module

codex = plugin_module("codex")
cli = plugin_module("cli")


def _completed(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _transport(python="/venv/python"):
    return {
        "type": "stdio",
        "command": python,
        "args": ["-m", "sutra.mcp"],
        "env": None,
    }


def _entry(python="/venv/python", enabled=True):
    return {
        "name": "sutra",
        "enabled": enabled,
        "transport": _transport(python),
    }


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(codex, "binary", lambda: "/usr/bin/codex")
    return tmp_path


def _fake_list(monkeypatch, entries, on_add=None, on_remove=None):
    state = {"entries": list(entries), "adds": 0, "removes": 0}

    def fake_run(args, timeout=45):
        if args[:3] == ["mcp", "list", "--json"]:
            return _completed(stdout=json.dumps(state["entries"]))
        if args[:2] == ["mcp", "add"]:
            state["adds"] += 1
            state["entries"] = [_entry(args[4] if len(args) > 4 else "/venv/python")]
            if on_add is not None:
                on_add(state, args)
            return _completed()
        if args[:2] == ["mcp", "remove"]:
            state["removes"] += 1
            state["entries"] = []
            if on_remove is not None:
                on_remove(state, args)
            return _completed()
        raise AssertionError(args)

    monkeypatch.setattr(codex, "_run", fake_run)
    return state


def _hooks(document=None):
    return codex.hooks_path()


def _read_hooks():
    return json.loads(codex.hooks_path().read_text(encoding="utf-8"))


def _write_mcp(python=sys.executable):
    escaped = python.replace("\\", "\\\\")
    codex.config_path().write_text(
        f'[mcp_servers.sutra]\ncommand = "{escaped}"\n'
        'args = ["-m", "sutra.mcp"]\n', encoding="utf-8")


def test_install_writes_the_hook_and_registers_mcp(isolated, monkeypatch, capsys):
    state = _fake_list(monkeypatch, [])
    assert codex.install(python=sys.executable) == 0
    document = _read_hooks()
    group = document["hooks"]["PostToolUse"][0]
    assert group["matcher"] == codex.HOOK_MATCHER
    command = group["hooks"][0]["command"]
    assert "sutra-codex-hook.py" in command
    assert shlex.split(command)[0] == sys.executable
    assert state["adds"] == 1
    output = capsys.readouterr().out
    assert "installed the SUTRA PostToolUse hook" in output
    assert "/hooks" in output
    assert "registered the sutra MCP server" in output


def test_install_preserves_other_hooks_and_is_idempotent(isolated, monkeypatch, capsys):
    existing = {
        "description": "mine",
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}],
            "PostToolUse": [{
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "other-linter"}],
            }],
        },
    }
    _hooks().write_text(json.dumps(existing), encoding="utf-8")
    state = _fake_list(monkeypatch, [])
    assert codex.install(python=sys.executable) == 0
    document = _read_hooks()
    assert document["description"] == "mine"
    assert document["hooks"]["SessionStart"] == existing["hooks"]["SessionStart"]
    assert document["hooks"]["PostToolUse"][0] == existing["hooks"]["PostToolUse"][0]
    assert len(document["hooks"]["PostToolUse"]) == 2
    backups = list(_hooks().parent.glob("hooks.json.bak-*"))
    assert len(backups) == 1

    before = _hooks().read_bytes()
    assert codex.install(python=sys.executable) == 0
    assert _hooks().read_bytes() == before
    assert len(list(_hooks().parent.glob("hooks.json.bak-*"))) == 1

    assert codex.uninstall() == 0
    document = _read_hooks()
    assert document["description"] == "mine"
    assert document["hooks"]["SessionStart"] == existing["hooks"]["SessionStart"]
    assert document["hooks"]["PostToolUse"] == existing["hooks"]["PostToolUse"]
    assert state["removes"] == 1
    assert "removed the SUTRA PostToolUse hook" in capsys.readouterr().out


def test_uninstall_removes_an_empty_created_hooks_file(isolated, monkeypatch):
    _fake_list(monkeypatch, [])
    assert codex.install(python=sys.executable) == 0
    assert codex.hooks_path().is_file()
    assert codex.uninstall() == 0
    assert not codex.hooks_path().exists()


def test_malformed_hooks_json_is_refused(isolated, monkeypatch, capsys):
    _hooks().write_text("{not json", encoding="utf-8")
    state = _fake_list(monkeypatch, [])
    assert codex.install(python=sys.executable) == 1
    assert state["adds"] == 0
    assert _hooks().read_text(encoding="utf-8") == "{not json"
    assert "could not read Codex hooks" in capsys.readouterr().err


def test_dry_run_writes_nothing_and_calls_no_mutation(isolated, monkeypatch, capsys):
    calls = []

    def list_only(args, timeout=45):
        calls.append(args)
        if args[:3] == ["mcp", "list", "--json"]:
            return _completed(stdout="[]")
        raise AssertionError(args)

    monkeypatch.setattr(codex, "_run", list_only)
    assert codex.install(python=sys.executable, dry_run=True) == 0
    assert not codex.hooks_path().exists()
    assert calls == [], "dry run must not invoke Codex at all"
    output = capsys.readouterr().out
    assert "would add the SUTRA PostToolUse hook" in output
    assert "would run: codex mcp add" in output


def test_a_path_with_spaces_survives_in_the_hook_command(tmp_path, monkeypatch):
    home = tmp_path / "codex home with spaces"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(codex, "binary", lambda: "/usr/bin/codex")
    _fake_list(monkeypatch, [])
    python = str(tmp_path / "python with spaces" / "python3")
    assert codex.install(python=python) == 0
    command = _read_hooks()["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert shlex.split(command) == [python, str(codex.hook_script())]


def test_refuses_a_different_existing_sutra_mcp_entry(isolated, monkeypatch, capsys):
    _fake_list(monkeypatch, [_entry(python="/other/python")])
    assert codex.install(python=sys.executable) == 1
    assert "different sutra MCP entry" in capsys.readouterr().err


def test_doctor_reports_missing_hook_as_a_failure(isolated, monkeypatch):
    _fake_list(monkeypatch, [_entry(python=sys.executable)])
    report = codex.doctor(python=sys.executable)
    assert report["ok"] is False
    assert report["status"] == "fail"
    assert "hook is not installed" in report["probe"]


def test_doctor_is_limited_until_the_hook_is_trusted(isolated, monkeypatch):
    _fake_list(monkeypatch, [_entry(python=sys.executable)])
    assert codex.install(python=sys.executable) == 0
    _write_mcp()
    report = codex.doctor(python=sys.executable)
    assert report["ok"] is True
    assert report["status"] == "limited"
    assert report["automatic"] is False
    assert report["trusted"] is None
    assert "run /hooks" in report["probe"]
    assert "automatic=no" in codex.format_doctor(report)


def test_doctor_is_ok_once_codex_records_trust(isolated, monkeypatch):
    _fake_list(monkeypatch, [_entry(python=sys.executable)])
    assert codex.install(python=sys.executable) == 0
    key = f"{codex.hooks_path()}:post_tool_use:0:0".replace("\\", "\\\\")
    codex.config_path().write_text(
        f'[mcp_servers.sutra]\ncommand = "{sys.executable.replace(chr(92), chr(92) * 2)}"\n'
        'args = ["-m", "sutra.mcp"]\n'
        f'[hooks.state."{key}"]\ntrusted_hash = "sha256:test"\n', encoding="utf-8")
    report = codex.doctor(python=sys.executable)
    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["automatic"] is True
    assert report["trusted"] is True
    assert "automatic=yes" in codex.format_doctor(report)


def test_status_never_claims_automatic_without_trust(isolated, monkeypatch):
    _fake_list(monkeypatch, [])
    assert codex.install(python=sys.executable) == 0
    report = codex.status()
    assert report["hook"] == "installed"
    assert report["trusted"] is None
    assert report["automatic"] is False
    # The tier names the host capability; automatic is the installed proof.
    assert report["tier"] == "automatic"


def test_status_and_doctor_never_run_codex(isolated, monkeypatch):
    _fake_list(monkeypatch, [])
    assert codex.install(python=sys.executable) == 0
    _write_mcp()
    monkeypatch.setattr(codex, "_run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("diagnosis must not invoke the Codex CLI")))
    report = codex.status()
    assert report["mcp"] == "configured"
    doctor = codex.doctor(python=sys.executable)
    assert doctor["status"] == "limited", doctor
    assert doctor["mcp"] == "configured"


def test_cli_status_prints_hook_and_trust(isolated, monkeypatch, capsys):
    _fake_list(monkeypatch, [])
    assert codex.install(python=sys.executable) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "codex:" in output
    assert "hook=installed" in output
    assert "trusted=unknown" in output
    assert "automatic=no" in output


@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed")
def test_real_codex_round_trip_uses_an_isolated_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    import importlib
    real = importlib.reload(codex)
    assert real.install(python=sys.executable) == 0
    assert real.hook_installed()
    assert real.status()["mcp"] == "configured"
    report = real.doctor(python=sys.executable)
    assert report["ok"] is True
    assert report["status"] == "limited"
    assert real.uninstall() == 0
    assert not real.hook_installed()
    assert real.status()["mcp"] == "not configured"
