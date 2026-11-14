"""`sutra update` reports PyPI state and only upgrades when asked.

Every network call is replaced, so the suite stays offline and deterministic.
"""

import json
import pathlib
import types
from urllib.error import URLError

import pytest

from conftest import plugin_module

cli = plugin_module("cli")
update = plugin_module("update")


def _stub(monkeypatch, *, current="0.6.1", latest="0.7.0", source=False,
          upgrade=None):
    monkeypatch.setattr(update, "installed_version", lambda: current)
    monkeypatch.setattr(update, "latest_version", lambda: latest)
    monkeypatch.setattr(update, "is_source_checkout", lambda: source)
    if upgrade is not None:
        monkeypatch.setattr(update, "upgrade", upgrade)


def _fake_pip_report(monkeypatch, payload, returncode=0):
    def fake_run(command, **_kwargs):
        report = pathlib.Path(command[command.index("--report") + 1])
        report.write_text(json.dumps(payload), encoding="utf-8")
        return types.SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(update.subprocess, "run", fake_run)


def test_the_check_uses_pips_resolver(monkeypatch):
    _fake_pip_report(monkeypatch, {
        "install": [{"metadata": {"name": "sutra-agent", "version": "0.6.2"}}],
    })
    assert update.latest_version() == "0.6.2"


def test_the_check_falls_back_to_pypi_when_pip_cannot_resolve(monkeypatch):
    _fake_pip_report(monkeypatch, {}, returncode=1)
    monkeypatch.setattr(update, "_pypi_latest", lambda timeout=6.0: "0.7.0")
    assert update.latest_version() == "0.7.0"


def test_an_empty_pip_report_means_the_installed_version_is_current(monkeypatch):
    _fake_pip_report(monkeypatch, {"install": []})
    monkeypatch.setattr(update, "installed_version", lambda: "0.6.1")
    assert update.latest_version() == "0.6.1"


def test_release_comparison():
    assert update.is_newer("0.7.0", "0.6.9")
    assert update.is_newer("1.0.0", "0.99.99")
    assert not update.is_newer("0.6.1", "0.6.1")
    assert not update.is_newer("0.6.0", "0.6.1")


def _install_metadata(monkeypatch, payload):
    class _Distribution:
        def read_text(self, name):
            assert name == "direct_url.json"
            return json.dumps(payload)

    monkeypatch.setattr(update.metadata, "distribution", lambda name: _Distribution())


def test_an_editable_checkout_is_a_source_checkout(monkeypatch):
    _install_metadata(monkeypatch, {
        "url": "file:///repo", "dir_info": {"editable": True},
    })
    assert update.is_source_checkout()


def test_a_wheel_install_is_not_a_source_checkout(monkeypatch):
    _install_metadata(monkeypatch, {
        "url": "file:///repo/dist/sutra_agent-0.6.1-py3-none-any.whl",
        "archive_info": {"hash": "sha256=deadbeef"},
    })
    assert not update.is_source_checkout()


def test_a_pypi_install_is_not_a_source_checkout(monkeypatch):
    _install_metadata(monkeypatch, {
        "url": "https://files.pythonhosted.org/packages/sutra_agent-0.6.1-py3-none-any.whl",
        "archive_info": {},
    })
    assert not update.is_source_checkout()


def test_update_reports_an_available_release(monkeypatch, capsys):
    _stub(monkeypatch)
    assert cli._update([]) == 0
    out = capsys.readouterr()
    assert "0.7.0" in out.out
    assert "0.6.1" in out.out
    assert "--apply" in out.out


def test_update_is_quiet_when_current(monkeypatch, capsys):
    _stub(monkeypatch, latest="0.6.1")
    assert cli._update([]) == 0
    assert "latest release" in capsys.readouterr().out


def test_update_reports_an_unreleased_build(monkeypatch, capsys):
    _stub(monkeypatch, current="0.6.1", latest="0.6.0")
    assert cli._update([]) == 0
    out = capsys.readouterr().out
    assert "newer than the latest published release" in out
    assert "0.6.0" in out


def test_update_handles_an_unreachable_pypi(monkeypatch, capsys):
    _stub(monkeypatch)

    def offline():
        raise URLError("network is unreachable")

    monkeypatch.setattr(update, "latest_version", offline)
    assert cli._update([]) == 2
    assert "could not check for updates" in capsys.readouterr().err


def test_update_refuses_to_upgrade_a_source_checkout(monkeypatch, capsys):
    called = []
    _stub(monkeypatch, source=True, upgrade=lambda: called.append(True) or 0)
    assert cli._update(["--apply"]) == 1
    out = capsys.readouterr()
    assert "git pull" in out.out
    assert called == []


def test_update_applies_an_available_release(monkeypatch, capsys):
    versions = iter(["0.6.1", "0.7.0"])
    upgraded = []
    monkeypatch.setattr(update, "installed_version", lambda: next(versions))
    monkeypatch.setattr(update, "latest_version", lambda: "0.7.0")
    monkeypatch.setattr(update, "is_source_checkout", lambda: False)
    monkeypatch.setattr(update, "upgrade", lambda: upgraded.append(True) or 0)

    assert cli._update(["--apply"]) == 0
    assert upgraded == [True]
    assert "sutra is now 0.7.0" in capsys.readouterr().out


def test_update_reports_a_failed_upgrade(monkeypatch, capsys):
    _stub(monkeypatch, upgrade=lambda: 1)
    assert cli._update(["--apply"]) == 1
    assert "unchanged" in capsys.readouterr().err


def test_apply_asks_pip_even_when_the_check_sees_no_newer_release(monkeypatch, capsys):
    """A private index or local wheel can be newer than the checked source."""
    versions = iter(["0.6.1", "0.6.2"])
    upgraded = []
    monkeypatch.setattr(update, "installed_version", lambda: next(versions))
    monkeypatch.setattr(update, "latest_version", lambda: "0.6.1")
    monkeypatch.setattr(update, "is_source_checkout", lambda: False)
    monkeypatch.setattr(update, "upgrade", lambda: upgraded.append(True) or 0)

    assert cli._update(["--apply"]) == 0
    assert upgraded == [True]
    assert "sutra is now 0.6.2" in capsys.readouterr().out


def test_apply_reports_when_pip_cannot_go_further(monkeypatch, capsys):
    _stub(monkeypatch, upgrade=lambda: 0)
    assert cli._update(["--apply"]) == 0
    assert "already the latest version pip can install" in capsys.readouterr().out


def test_a_locked_launcher_is_detected_from_the_running_script(monkeypatch, tmp_path):
    launcher = tmp_path / "Scripts" / "sutra.exe"
    monkeypatch.setattr(update.sys, "argv", [str(launcher.with_suffix(""))])
    assert update._launcher_is_locked([launcher]) is True
    monkeypatch.setattr(update.sys, "argv", [str(tmp_path / "sutra" / "cli.py")])
    assert update._launcher_is_locked([launcher]) is False


def test_a_locked_update_reports_a_resolution_failure(monkeypatch):
    monkeypatch.setattr(update, "_pip_latest", lambda: None)
    with pytest.raises(update.UpdateError):
        update._upgrade_beside_running_launcher()


def test_a_staged_release_replaces_the_package_and_metadata(tmp_path):
    site = tmp_path / "site-packages"
    (site / "sutra").mkdir(parents=True)
    (site / "sutra" / "old.py").write_text("old", encoding="utf-8")
    (site / "sutra_agent-0.6.1.dist-info").mkdir()
    staging = tmp_path / "staging"
    (staging / "sutra").mkdir(parents=True)
    (staging / "sutra" / "new.py").write_text("new", encoding="utf-8")
    (staging / "sutra_agent-0.6.2.dist-info").mkdir()

    assert update._swap_staged(
        site, staging / "sutra", staging / "sutra_agent-0.6.2.dist-info") == 0
    assert (site / "sutra" / "new.py").exists()
    assert not (site / "sutra" / "old.py").exists()
    assert not (site / "sutra_agent-0.6.1.dist-info").exists()
    assert (site / "sutra_agent-0.6.2.dist-info").exists()


def test_update_reports_an_update_error(monkeypatch, capsys):
    def fail():
        raise update.UpdateError("could not resolve a newer release")

    _stub(monkeypatch, upgrade=fail)
    assert cli._update(["--apply"]) == 1
    assert "could not resolve a newer release" in capsys.readouterr().err
