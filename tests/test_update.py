"""`sutra update` reports PyPI state and only upgrades when asked.

Every network call is replaced, so the suite stays offline and deterministic.
"""

import json
from urllib.error import URLError

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
    assert "could not check PyPI" in capsys.readouterr().err


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
