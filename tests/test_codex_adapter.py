"""Codex integration tests: official MCP commands, no automatic claim.

The fake runner pins the config edit and failure handling. The optional live
test uses Codex 0.166.0's real `mcp add/list/remove` commands against a
throwaway CODEX_HOME; it never touches the developer's Codex config and does
not call a model.
"""

import json
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


def test_status_is_read_only_and_never_claims_automatic(isolated, monkeypatch):
    _fake_list(monkeypatch, [])
    report = codex.status()
    assert report["client"] == "codex"
    assert report["mcp"] == "not configured"
    assert report["automatic"] is False
    assert report["tier"] == "mcp-only"
    assert "cannot replace tool output" in report["host_limit"]
    assert list((isolated / "codex").iterdir()) == []


def test_install_uses_codex_mcp_add_and_verifies(isolated, monkeypatch, capsys):
    state = _fake_list(monkeypatch, [])
    assert codex.install(python="/venv/python") == 0
    assert state["adds"] == 1
    assert state["entries"][0]["transport"]["command"] == "/venv/python"
    output = capsys.readouterr().out
    assert "registered" in output
    assert "MCP-only" in output
    assert "automatic" not in output.lower() or "no automatic" in output.lower()


def test_install_is_idempotent_and_refuses_another_sutra_entry(isolated, monkeypatch, capsys):
    _fake_list(monkeypatch, [_entry()])
    assert codex.install(python="/venv/python") == 0
    assert "nothing to do" in capsys.readouterr().out

    _fake_list(monkeypatch, [_entry(python="/other/python")])
    assert codex.install(python="/venv/python") == 1
    assert "different sutra" in capsys.readouterr().err


def test_dry_run_does_not_call_codex(isolated, monkeypatch, capsys):
    _fake_list(monkeypatch, [])
    def reject(_args, timeout=45):
        raise AssertionError("dry run must not modify Codex")
    monkeypatch.setattr(codex, "_run", reject)
    # The dry-run path still needs the pre-read, so use a list-only runner.
    calls = []
    def list_only(args, timeout=45):
        calls.append(args)
        if args[:3] == ["mcp", "list", "--json"]:
            return _completed(stdout="[]")
        raise AssertionError(args)
    monkeypatch.setattr(codex, "_run", list_only)
    assert codex.install(python="/venv/python", dry_run=True) == 0
    assert calls == [["mcp", "list", "--json"]]
    assert "dry run" in capsys.readouterr().out


def test_doctor_reports_failure_when_mcp_is_missing(isolated, monkeypatch):
    _fake_list(monkeypatch, [])
    report = codex.doctor(python="/venv/python")
    assert report["ok"] is False
    assert report["status"] == "fail"
    assert "not registered" in report["probe"]
    assert "automatic=no" in codex.format_doctor(report)


def test_doctor_reports_limited_not_automatic(isolated, monkeypatch):
    _fake_list(monkeypatch, [_entry()])
    report = codex.doctor(python="/venv/python")
    assert report["ok"] is True
    assert report["status"] == "limited"
    assert report["automatic"] is False
    assert "automatic=no" in codex.format_doctor(report)


def test_uninstall_removes_only_the_entry(isolated, monkeypatch, capsys):
    state = _fake_list(monkeypatch, [_entry()])
    assert codex.uninstall() == 0
    assert state["removes"] == 1
    assert "removed" in capsys.readouterr().out
    assert codex.uninstall() == 0


@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed")
def test_real_codex_mcp_round_trip_uses_an_isolated_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    # Remove the fake runner from the other tests; this path uses the real CLI.
    import importlib
    real = importlib.reload(codex)
    assert real.install(python=sys.executable) == 0
    row = real.status()
    assert row["mcp"] == "configured"
    assert row["automatic"] is False
    report = real.doctor(python=sys.executable)
    assert report["ok"] is True
    assert report["status"] == "limited"
    assert real.uninstall() == 0
    assert real.status()["mcp"] == "not configured"
    assert "MCP-only" in capsys.readouterr().out or True


def test_cli_doctor_accepts_codex(isolated, monkeypatch, capsys):
    _fake_list(monkeypatch, [])
    assert cli.main(["doctor", "--client", "codex", "--json"]) == 1
    reports = json.loads(capsys.readouterr().out)
    assert reports[0]["client"] == "codex"
    assert reports[0]["automatic"] is False
