"""All-client doctor aggregation: every supported client gets a truthful row."""

import json
import shutil

import pytest

from conftest import plugin_module

cli = plugin_module("cli")


@pytest.fixture()
def empty_clients(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


def test_unfiltered_doctor_reports_all_four_when_none_are_installed(
        empty_clients, monkeypatch, capsys):
    import sutra_plugin.claude_code as claude_code
    import sutra_plugin.codex as codex
    import sutra_plugin.hermes as hermes
    import sutra_plugin.opencode as opencode

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(claude_code, "binary", lambda: None)
    monkeypatch.setattr(codex, "binary", lambda: None)
    monkeypatch.setattr(hermes, "binary", lambda: None)
    monkeypatch.setattr(opencode, "plugin_path",
                        lambda *_: empty_clients / "missing.js")

    assert cli.main(["doctor", "--json"]) == 0
    reports = json.loads(capsys.readouterr().out)
    assert [report["client"] for report in reports] == [
        "claude", "opencode", "hermes", "codex"]
    assert all(report["status"] == "not-installed" for report in reports)
    assert all(report["automatic"] is False for report in reports)


def test_one_adapter_exception_does_not_hide_the_next(
        empty_clients, monkeypatch, capsys):
    import sutra_plugin.claude_code as claude_code

    monkeypatch.setattr(claude_code, "binary", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_code, "doctor",
                        lambda: (_ for _ in ()).throw(RuntimeError("broken config")))
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    assert cli.main(["doctor", "--client", "claude", "--client", "opencode",
                     "--json"]) == 1
    reports = json.loads(capsys.readouterr().out)
    assert [report["client"] for report in reports] == ["claude", "opencode"]
    assert reports[0]["status"] == "fail"
    assert "broken config" in reports[0]["error"]
    assert reports[1]["status"] == "not-installed"


def test_explicit_absent_client_is_reported_not_silently_dropped(
        empty_clients, monkeypatch, capsys):
    import sutra_plugin.codex as codex

    monkeypatch.setattr(codex, "binary", lambda: None)
    assert cli.main(["doctor", "--client", "codex", "--json"]) == 0
    reports = json.loads(capsys.readouterr().out)
    assert len(reports) == 1
    assert reports[0]["client"] == "codex"
    assert reports[0]["status"] == "not-installed"
