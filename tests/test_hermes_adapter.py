"""Hermes integration: copy the native plugin, enable it through the host.

The fake runner checks the exact host commands and rollback behavior without
touching a real Hermes profile. The optional live test installs into a
throwaway HERMES_HOME when the Hermes Agent CLI is available; it does not
contact a model or a gateway.
"""

import json
import shutil
import types

import pytest

from conftest import plugin_module

hermes = plugin_module("hermes")
cli = plugin_module("cli")


def _completed(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(hermes, "binary", lambda: "/usr/bin/hermes")
    return tmp_path


def _fake_runner(monkeypatch, *, validate=0, enable=0, show="Status: enabled\n"):
    calls = []

    def fake_run(args, timeout=90):
        calls.append(args)
        if args[:2] == ["plugins", "validate"]:
            return _completed(returncode=validate, stdout="validated\n" if not validate else "",
                              stderr="bad plugin\n" if validate else "")
        if args[:2] == ["plugins", "enable"]:
            return _completed(returncode=enable, stdout="enabled\n" if not enable else "",
                              stderr="cannot enable\n" if enable else "")
        if args[:2] == ["plugins", "disable"]:
            return _completed(stdout="disabled\n")
        if args[:2] == ["plugins", "show"]:
            return _completed(stdout=show)
        raise AssertionError(args)

    monkeypatch.setattr(hermes, "_run", fake_run)
    return calls


def test_install_copies_the_native_plugin_and_enables_it(isolated, monkeypatch, capsys):
    calls = _fake_runner(monkeypatch)
    target = hermes.plugin_dir()
    assert hermes.install() == 0
    assert hermes.is_ours(target)
    assert (target / "plugin.yaml").is_file()
    assert (target / "__init__.py").is_file()
    assert (target / "core.py").is_file()
    assert (target / hermes.MARKER).is_file()
    assert ["plugins", "validate", str(target)] in calls
    assert ["plugins", "enable", "sutra"] in calls
    output = capsys.readouterr().out
    assert "installed and enabled" in output
    assert "restart Hermes" in output
    assert hermes.status()["automatic"] is True


def test_a_foreign_plugin_directory_is_refused(isolated, monkeypatch, capsys):
    _fake_runner(monkeypatch)
    target = hermes.plugin_dir()
    target.mkdir(parents=True)
    (target / "plugin.yaml").write_text("name: other\n", encoding="utf-8")
    assert hermes.install() == 1
    assert (target / "plugin.yaml").read_text(encoding="utf-8") == "name: other\n"
    assert "not a SUTRA plugin" in capsys.readouterr().err


def test_validation_failure_restores_the_previous_plugin(isolated, monkeypatch, capsys):
    _fake_runner(monkeypatch, validate=1)
    target = hermes.plugin_dir()
    target.mkdir(parents=True)
    (target / "plugin.yaml").write_text("name: sutra\n", encoding="utf-8")
    (target / hermes.MARKER).write_text("old generation\n", encoding="utf-8")
    (target / "old.txt").write_text("old\n", encoding="utf-8")
    assert hermes.install() == 1
    assert (target / "old.txt").read_text(encoding="utf-8") == "old\n"
    assert "rejected" in capsys.readouterr().err


def test_uninstall_disables_and_removes_only_our_plugin(isolated, monkeypatch, capsys):
    calls = _fake_runner(monkeypatch)
    assert hermes.install() == 0
    capsys.readouterr()
    assert hermes.uninstall() == 0
    assert not hermes.plugin_dir().exists()
    assert ["plugins", "disable", "sutra"] in calls
    assert "removed" in capsys.readouterr().out

    foreign = hermes.plugin_dir()
    foreign.mkdir(parents=True)
    (foreign / "plugin.yaml").write_text("name: other\n", encoding="utf-8")
    assert hermes.uninstall() == 1
    assert foreign.exists()


def test_doctor_reports_missing_or_disabled_plugin(isolated, monkeypatch):
    _fake_runner(monkeypatch)
    report = hermes.doctor()
    assert report["ok"] is False
    assert "not installed" in report["probe"]

    assert hermes.install() == 0
    report = hermes.doctor()
    assert report["ok"] is True
    assert report["automatic"] is True


def test_doctor_reports_a_stale_plugin_when_the_executable_is_missing(
        isolated, monkeypatch, capsys):
    _fake_runner(monkeypatch)
    assert hermes.install() == 0
    monkeypatch.setattr(hermes, "binary", lambda: None)
    report = hermes.doctor()
    assert report["status"] == "fail"
    assert report["ok"] is False
    assert "executable is missing" in report["probe"]


def test_dry_run_writes_nothing(isolated, monkeypatch, capsys):
    calls = _fake_runner(monkeypatch)
    assert hermes.install(dry_run=True) == 0
    assert not hermes.plugin_dir().exists()
    assert calls == []
    assert "dry run" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("hermes") is None, reason="hermes CLI not installed")
def test_real_hermes_plugin_round_trip_uses_an_isolated_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    import importlib
    real = importlib.reload(hermes)
    assert real.install() == 0
    report = real.doctor()
    assert report["ok"] is True
    assert report["automatic"] is True
    assert real.status()["automatic"] is True
    assert real.uninstall() == 0
    assert not real.plugin_dir().exists()
    assert "removed" in capsys.readouterr().out


def test_cli_doctor_accepts_hermes(isolated, monkeypatch, capsys):
    _fake_runner(monkeypatch)
    assert cli.main(["doctor", "--client", "hermes", "--json"]) == 1
    reports = json.loads(capsys.readouterr().out)
    assert reports[0]["client"] == "hermes"
    assert reports[0]["automatic"] is False
