"""The ``sutra`` console command surface exposed by ``cli.main()``.

``install``, ``uninstall``, ``doctor``, ``detect`` and ``status`` run in
process. The client adapters they call are replaced with in-memory fakes, so
no real agent binary, user config directory or network call is involved.
``compress``/``restore``/``config`` go through scripts/sutra-cli.py and are
covered by tests/test_cli_adapter.py.
"""

import json
import sys
import types
from pathlib import Path

from conftest import plugin_module

cli = plugin_module("cli")

PACKAGE = "sutra_plugin"
CLIENTS = ("claude_code", "hermes", "codex", "opencode", "openclaw")


def _fake_client_module(name, *, binary=False):
    here = Path.home() / ".sutra-fake"
    return types.SimpleNamespace(
        binary=lambda: binary,
        settings_path=lambda: here / f"{name}-settings.json",
        config_path=lambda: here / f"{name}-config.json",
        plugin_dir=lambda: here / "plugins",
        plugin_path=lambda config_path=None: here / "plugins" / "sutra.js",
        default_home=lambda: here,
        configured=lambda: False,
        install=lambda dry_run=False, **_kwargs: 0,
        uninstall=lambda dry_run=False, **_kwargs: 0,
        doctor=lambda **_kwargs: {"client": name, "status": "not-installed"},
        status=lambda: {"client": name},
    )


def _install_fakes(monkeypatch, present=()):
    """Replace every client adapter module with an in-memory fake.

    Function bodies inside cli.py re-import their adapters with
    ``from . import <name>``, which resolves through the package object's
    attributes (set by cli.py's own top-level import), so both the package
    attribute and ``sys.modules`` entry must be replaced. cli.py also binds
    ``openclaw`` as a module global from its top-level import, so that
    binding is replaced as well.
    """
    package = sys.modules[PACKAGE]
    fakes = {
        name: _fake_client_module(name, binary=name in present)
        for name in CLIENTS
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(package, name, fake, raising=False)
        monkeypatch.setitem(sys.modules, f"{PACKAGE}.{name}", fake)
    monkeypatch.setattr(cli, "openclaw", fakes["openclaw"])
    discovery_fake = types.SimpleNamespace(detect=lambda clients=None: [])
    monkeypatch.setattr(package, "discovery", discovery_fake, raising=False)
    monkeypatch.setitem(
        sys.modules, f"{PACKAGE}.discovery", discovery_fake,
    )


def test_version_prints_version_and_exits_zero(capsys):
    assert cli.main(["--version"]) == 0
    out = capsys.readouterr().out
    assert "sutra" in out and "0.6.1" in out


def test_help_without_args_prints_usage_and_exits_zero(capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "usage:" in out.lower() and "install" in out


def test_unknown_command_exits_two(capsys):
    assert cli.main(["frobnicate"]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_install_with_no_detected_client_exits_one_and_explains(
    monkeypatch, capsys,
):
    _install_fakes(monkeypatch)
    assert cli.main(["install", "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "no supported client detected" in err


def test_dry_run_summary_says_what_would_happen():
    assert cli._dry_run_label("claude=not configured") == "claude=would configure"
    assert cli._dry_run_label("opencode=hook-only") == "opencode=would add MCP"
    assert cli._dry_run_label("codex=configured") == "codex=configured"


def test_install_required_client_not_installed_fails(monkeypatch, capsys):
    _install_fakes(monkeypatch, present=("claude_code",))
    assert cli.main(["install", "--client", "codex"]) == 1
    assert "codex is not installed" in capsys.readouterr().err


def test_install_required_client_that_is_installed_succeeds(
    monkeypatch, capsys,
):
    _install_fakes(monkeypatch, present=("claude_code",))
    assert cli.main(["install", "--client", "claude", "--dry-run"]) == 0
    assert "dry-run summary" in capsys.readouterr().out


def test_install_opencode_dry_run_writes_nothing(monkeypatch, capsys):
    _install_fakes(monkeypatch)
    assert cli.main(["install", "--client", "opencode", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "no OpenCode config found" in out
    assert "dry run would skip the MCP recovery entry" in out
    assert not Path.home().joinpath(".config", "opencode", "opencode.json").exists()


def test_install_opencode_with_existing_config_dry_run_changes_nothing(
    monkeypatch, capsys, tmp_path,
):
    _install_fakes(monkeypatch)
    config = tmp_path / "opencode.json"
    config.write_text('{"foo": 1}', encoding="utf-8")
    assert cli.main(
        ["install", "--client", "opencode", "--config", str(config), "--dry-run"],
    ) == 0
    assert "dry run" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == '{"foo": 1}'


def test_install_all_dry_run_succeeds_without_real_clients(
    monkeypatch, capsys,
):
    _install_fakes(monkeypatch)
    assert cli.main(["install", "--all", "--dry-run"]) == 0
    assert "dry-run summary" in capsys.readouterr().out


def test_uninstall_opencode_dry_run_writes_nothing(monkeypatch):
    _install_fakes(monkeypatch)
    assert cli.main(["uninstall", "--client", "opencode", "--dry-run"]) == 0


def test_detect_json_is_read_only(monkeypatch, capsys):
    _install_fakes(monkeypatch)
    assert cli.main(["detect", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["read_only"] is True
    assert data["clients"] == []


def test_doctor_json_reports_not_installed_clients_without_failing(
    monkeypatch, capsys,
):
    _install_fakes(monkeypatch)
    assert cli.main(["doctor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert all(report["status"] == "not-installed" for report in data)


def test_status_json_returns_one_when_nothing_is_configured(
    monkeypatch, capsys,
):
    _install_fakes(monkeypatch)
    assert cli.main(["status", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert data[0]["mcp"] == "not configured"


def test_install_explains_a_detected_client_without_an_adapter(monkeypatch, capsys):
    _install_fakes(monkeypatch)
    assert cli.main(["install", "--client", "cursor"]) == 1
    output = capsys.readouterr()
    assert "no SUTRA installer" in output.err
    assert "MCP-only" in output.err
    assert "docs/automatic-install.md" in output.err


def test_uninstall_of_a_manual_client_is_an_honest_noop(monkeypatch, capsys):
    _install_fakes(monkeypatch)
    assert cli.main(["uninstall", "--client", "windsurf"]) == 0
    assert "nothing to uninstall" in capsys.readouterr().out


def test_doctor_explains_a_manual_client_without_failing(monkeypatch, capsys):
    _install_fakes(monkeypatch)
    assert cli.main(["doctor", "--client", "cursor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["status"] == "manual"
    assert data[0]["tier"] == "mcp-only"


def test_install_json_counts_skipped_clients_separately(monkeypatch, capsys):
    def operation(client, args, *, required):
        return 0

    monkeypatch.setattr(cli, "_install_one", operation)
    monkeypatch.setattr(cli, "_rows", lambda: [])
    args = types.SimpleNamespace(client=["hermes", "claude"], config=None,
                                 dry_run=False, all=False, json=True)
    assert cli._apply("install", args) == 0
    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["status"] == "partial"
    assert data["success"] == 0
    assert data["skipped"] == 2
    assert data["integrated_clients"] == ["hermes: skipped", "claude: skipped"]


def test_detect_does_not_offer_install_for_a_manual_only_client(monkeypatch, capsys):
    _install_fakes(monkeypatch)
    package = sys.modules[PACKAGE]
    monkeypatch.setattr(package, "discovery", types.SimpleNamespace(
        detect=lambda clients=None: [{"client": "cursor", "installed": True}],
        format_detect=lambda rows: "cursor detected"), raising=False)
    assert cli.main(["detect"]) == 0
    output = capsys.readouterr().out
    assert "MCP-only" in output
    assert "install --all" not in output


def test_detect_counts_only_installed_clients_as_detected(monkeypatch, capsys):
    _install_fakes(monkeypatch)
    package = sys.modules[PACKAGE]
    monkeypatch.setattr(package, "discovery", types.SimpleNamespace(
        detect=lambda clients=None: [
            {"client": "cursor", "installed": True},
            {"client": "codex", "installed": False},
        ],
        format_detect=lambda rows: "detection table"), raising=False)
    assert cli.main(["detect"]) == 0
    output = capsys.readouterr().out
    assert "Steps skipped: 1/2" in output
    assert "cursor: detected" in output
    assert "codex: not detected" in output


def test_status_does_not_call_an_unconfigured_client_a_failure(monkeypatch, capsys):
    _install_fakes(monkeypatch)
    assert cli.main(["status"]) == 1
    output = capsys.readouterr().out
    assert "opencode: not configured" in output
    assert "Steps failed" not in output
    assert "Action required" not in output


def test_doctor_with_no_integrations_does_not_claim_everything_is_healthy(
    monkeypatch, capsys,
):
    _install_fakes(monkeypatch)
    assert cli.main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "No automatic integration is installed" in output
    assert "All probed clients are healthy" not in output
    assert "Action required" not in output
