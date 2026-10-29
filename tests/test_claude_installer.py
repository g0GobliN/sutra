"""Claude Code's automatic integration: hook settings and MCP registration.

The settings edit is exercised in a temporary ``CLAUDE_CONFIG_DIR``; the MCP
calls are a fake runner here, and the real CLI is covered by the live test at
the bottom (skipped when ``claude`` is absent). No user config is read.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

from conftest import plugin_module

claude_code = plugin_module("claude_code")
cli = plugin_module("cli")


def _completed(returncode=0, stdout="", stderr=""):
    import types
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    config = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(claude_code, "binary", lambda: "/usr/bin/claude")
    return config


def _settings(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _fake_runner(monkeypatch, handler):
    monkeypatch.setattr(claude_code, "_run", handler)


def test_add_hook_is_idempotent_and_preserves_other_hooks():
    document = {
        "theme": "dark",
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard"}]}],
            "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "lint"}]}],
        },
    }
    command, args = "/venv/python", ["-m", "sutra.hook"]
    assert claude_code.add_hook(document, command, args) is True
    assert claude_code.add_hook(document, command, args) is False
    assert document["theme"] == "dark"
    handlers = [item for group in document["hooks"]["PostToolUse"] for item in group["hooks"]]
    assert {"type": "command", "command": "lint"} in handlers
    assert {"type": "command", "command": command, "args": args, "timeout": 60} in handlers
    assert len(document["hooks"]["PostToolUse"]) == 2


def test_remove_hook_removes_only_ours_and_prunes_empty_containers():
    document = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Edit", "hooks": [{"type": "command", "command": "lint"}]},
                {"hooks": [{"type": "command", "command": "/venv/python",
                            "args": ["-m", "sutra.hook"], "timeout": 60}]},
            ]
        }
    }
    assert claude_code.remove_hook(document, "/venv/python", ["-m", "sutra.hook"]) is True
    assert document == {"hooks": {"PostToolUse": [
        {"matcher": "Edit", "hooks": [{"type": "command", "command": "lint"}]}]}}
    assert claude_code.remove_hook(document, "/venv/python", ["-m", "sutra.hook"]) is False


def test_remove_hook_drops_the_whole_hooks_key_when_it_was_only_ours():
    document = {"hooks": {"PostToolUse": [{"hooks": [
        {"type": "command", "command": "/venv/python", "args": ["-m", "sutra.hook"]}]}]}}
    assert claude_code.remove_hook(document, "/venv/python", ["-m", "sutra.hook"]) is True
    assert document == {}


def test_a_hook_from_another_interpreter_is_replaced_and_removed():
    document = {}
    assert claude_code.add_hook(document, "/old/venv/python", ["-m", "sutra.hook"]) is True
    assert claude_code.find_hook(document) == ("/old/venv/python", ["-m", "sutra.hook"])

    assert claude_code.add_hook(document, "/new/venv/python", ["-m", "sutra.hook"]) is True
    handlers = [item for group in document["hooks"]["PostToolUse"] for item in group["hooks"]]
    assert handlers == [{"type": "command", "command": "/new/venv/python",
                         "args": ["-m", "sutra.hook"], "timeout": 60}]
    assert claude_code.remove_sutra_hooks(document) is True
    assert document == {}


def test_a_malformed_hooks_shape_is_refused_not_overwritten():
    document = {"hooks": {"PostToolUse": "not-an-array"}}
    with pytest.raises(ValueError, match="not an array"):
        claude_code.add_hook(document, "/venv/python", ["-m", "sutra.hook"])
    assert document == {"hooks": {"PostToolUse": "not-an-array"}}


def test_install_writes_the_hook_and_registers_mcp(isolated, monkeypatch, capsys):
    path = claude_code.settings_path()
    _settings(path, {"theme": "dark"})
    configured = {"value": False}
    calls = []

    def fake_run(args, timeout=60):
        calls.append(args)
        if args[:2] == ["mcp", "get"]:
            if configured["value"]:
                return _completed(stdout="Command: /venv/python\nArgs: -m sutra.mcp\n"
                                          "Status: \u2714 Connected\n")
            return _completed(returncode=1, stdout='No MCP server named "sutra".\n')
        if args[:2] == ["mcp", "add"]:
            configured["value"] = True
            return _completed(stdout="Added stdio MCP server sutra\n")
        raise AssertionError(args)

    _fake_runner(monkeypatch, fake_run)
    monkeypatch.setattr(claude_code, "doctor", lambda **_: {"ok": True})
    assert claude_code.install(python="/venv/python") == 0
    assert claude_code.install(python="/venv/python") == 0

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["theme"] == "dark"
    handlers = [item for group in document["hooks"]["PostToolUse"] for item in group["hooks"]]
    assert handlers == [{"type": "command", "command": "/venv/python",
                         "args": ["-m", "sutra.hook"], "timeout": 60}]
    adds = [call for call in calls if call[:2] == ["mcp", "add"]]
    assert len(adds) == 1, "a second install must not add a duplicate MCP server"
    assert "connected" in capsys.readouterr().out


def test_install_refuses_a_different_existing_mcp_entry(isolated, monkeypatch, capsys):
    path = claude_code.settings_path()
    _settings(path, {})

    def fake_run(args, timeout=60):
        if args[:2] == ["mcp", "get"]:
            return _completed(stdout="Command: /other/python\nArgs: -m sutra.mcp\n"
                                      "Status: \u2714 Connected\n")
        raise AssertionError(args)

    _fake_runner(monkeypatch, fake_run)
    assert claude_code.install(python="/venv/python") == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {}
    assert "different sutra MCP entry" in capsys.readouterr().err


def test_a_failed_mcp_registration_rolls_back_a_new_hook(isolated, monkeypatch, capsys):
    path = claude_code.settings_path()
    _settings(path, {"theme": "dark"})

    def fake_run(args, timeout=60):
        if args[:2] == ["mcp", "get"]:
            return _completed(returncode=1, stdout="not found\n")
        if args[:2] == ["mcp", "add"]:
            return _completed(returncode=1, stderr="cannot start sutra.mcp\n")
        raise AssertionError(args)

    _fake_runner(monkeypatch, fake_run)
    assert claude_code.install(python="/venv/python") == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"theme": "dark"}
    assert list(path.parent.glob("settings.json.bak-*"))
    assert "rolled back" in capsys.readouterr().err


def test_a_failed_mcp_registration_removes_a_settings_file_it_created(
        isolated, monkeypatch, capsys):
    def fake_run(args, timeout=60):
        if args[:2] == ["mcp", "get"]:
            return _completed(returncode=1, stdout="not found\n")
        if args[:2] == ["mcp", "add"]:
            return _completed(returncode=1, stderr="boom\n")
        raise AssertionError(args)

    _fake_runner(monkeypatch, fake_run)
    assert claude_code.install(python="/venv/python") == 1
    assert not claude_code.settings_path().exists()
    assert "rolled back" in capsys.readouterr().err


def test_dry_run_writes_nothing_and_runs_no_claude(isolated, monkeypatch, capsys):
    def fake_run(args, timeout=60):
        if args[:2] == ["mcp", "get"]:
            return _completed(returncode=1, stdout="not found\n")
        raise AssertionError(f"dry run must not call {args}")

    _fake_runner(monkeypatch, fake_run)
    assert cli.main(["install", "--client", "claude", "--dry-run"]) == 0
    assert not claude_code.settings_path().exists()
    output = capsys.readouterr().out
    assert "dry run" in output and "PostToolUse" in output and "claude mcp add" in output


def test_uninstall_removes_only_the_hook_and_mcp(isolated, monkeypatch, capsys):
    path = claude_code.settings_path()
    _settings(path, {"theme": "dark"})
    removed = {"value": False}

    def fake_run(args, timeout=60):
        if args[:2] == ["mcp", "get"]:
            return _completed(stdout="Command: /venv/python\nArgs: -m sutra.mcp\n"
                                      "Status: \u2714 Connected\n")
        if args[:2] == ["mcp", "remove"]:
            removed["value"] = True
            return _completed(stdout="Removed\n")
        raise AssertionError(args)

    _fake_runner(monkeypatch, fake_run)
    monkeypatch.setattr(claude_code, "doctor", lambda **_: {"ok": True})
    assert claude_code.install(python="/venv/python") == 0
    assert claude_code.uninstall(python="/venv/python") == 0
    assert removed["value"]
    assert json.loads(path.read_text(encoding="utf-8")) == {"theme": "dark"}


def test_status_reports_hook_and_mcp_without_writing(isolated, monkeypatch, capsys):
    path = claude_code.settings_path()
    _settings(path, {})

    def fake_run(args, timeout=60):
        if args[:2] == ["mcp", "get"]:
            return _completed(returncode=1, stdout="not found\n")
        raise AssertionError(args)

    _fake_runner(monkeypatch, fake_run)
    assert cli.main(["status", "--json"]) == 1
    rows = json.loads(capsys.readouterr().out)
    claude = next(row for row in rows if row["client"] == "claude")
    assert claude["hook"] is False
    assert claude["mcp"] == "not configured"
    assert claude["automatic"] is False
    assert path.exists()


ROOT = Path(__file__).resolve().parents[1]


def test_doctor_proves_the_real_hook_compresses_and_restores(isolated, monkeypatch):
    """The doctor runs the installed hook, not a reimplementation of it."""
    command, args = sys.executable, [str(ROOT / "scripts" / "sutra-claude-code-hook.py")]
    document = {}
    assert claude_code.add_hook(document, command, args) is True
    _settings(claude_code.settings_path(), document)

    def fake_run(arguments, timeout=60):
        if arguments[:2] == ["mcp", "get"]:
            return _completed(stdout="Command: /venv/python\nArgs: -m sutra.mcp\n"
                                      "Status: \u2714 Connected\n")
        raise AssertionError(arguments)

    _fake_runner(monkeypatch, fake_run)
    report = claude_code.doctor(hook=(command, args))
    assert report["ok"] is True, report
    assert report["hook"] is True
    assert report["mcp"] == "connected"
    assert report["recovered"] is True
    assert report["original_bytes"] > 24_576
    assert report["compressed_bytes"] < report["original_bytes"]
    assert "compressed and restored" in claude_code.format_doctor(report)


def test_doctor_reports_a_missing_hook_without_probing(isolated, monkeypatch):
    _settings(claude_code.settings_path(), {})
    _fake_runner(monkeypatch, lambda arguments, timeout=60: _completed(
        returncode=1, stdout="not found\n"))
    report = claude_code.doctor()
    assert report["ok"] is False
    assert report["hook"] is False
    assert "not installed" in report["probe"]


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_live_claude_rolls_back_when_the_server_cannot_connect(isolated, monkeypatch, capsys):
    """The real CLI is used, but ``/bin/echo`` cannot be an MCP server.

    A written hook with no working recovery path is not a partial success: the
    install must remove both the MCP entry it added and the hook, and say so.
    """
    monkeypatch.setattr(claude_code, "binary", lambda: shutil.which("claude"))
    assert claude_code.install(python="/bin/echo") == 1
    assert not claude_code.settings_path().exists()
    existing, _ = claude_code._mcp_get()
    assert existing is False
    assert "rolled back" in capsys.readouterr().err
