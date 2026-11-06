"""Doctor proof for the installed OpenClaw plugin, not just its file.

The positive case runs the real generated plugin through Node and the real
Python CLI against a temporary store. The negative case is a diff that must
come back byte-identical. Missing/foreign/broken installs must fail without
executing anything untrusted.
"""

import json
import os
import shutil
import sys

import pytest

from conftest import ROOT, plugin_module

openclaw = plugin_module("openclaw")
claude_code = plugin_module("claude_code")
cli = plugin_module("cli")


@pytest.fixture()
def installed(tmp_path, monkeypatch):
    """Set up isolated OpenClaw config directory using SUTRA_OPENCLAW_CONFIG."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir(parents=True)
    config = config_dir / "openclaw.json"
    config.write_text('{\n  "theme": "dark"\n}\n', encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    # Add openclaw binary to PATH so shutil.which("openclaw") finds it
    oc_bin = "/tmp/oc-home/node_modules/.bin"
    monkeypatch.setenv("PATH", f"{oc_bin}:{os.environ.get('PATH', '')}")
    home = tmp_path / "sutra-home"
    assert openclaw.install(python=sys.executable, home=home,
                            config_path=config_dir) == 0
    library = tmp_path / "lib"
    library.mkdir()
    (library / "sutra").symlink_to(ROOT, target_is_directory=True)
    monkeypatch.setenv("PYTHONPATH", str(library))
    return tmp_path, config_dir, home


def _checks(report):
    return {check["name"]: check for check in report["checks"]}


def test_doctor_passes_roundtrip_and_protected_output(installed):
    root, config_dir, home = installed
    plugin = openclaw.plugin_path(config_dir)
    before = plugin.read_bytes()
    report = openclaw.doctor()
    checks = _checks(report)

    assert report["ok"] is True, report
    expected = ("pass" if shutil.which("openclaw") and shutil.which("node")
                else "partial")
    assert report["status"] == expected
    assert checks["plugin"]["status"] == "pass"
    assert checks["host"]["status"] == ("pass" if shutil.which("openclaw") else "skip")
    assert checks["interpreter"]["status"] == "pass"
    assert checks["cli_roundtrip"]["status"] == "pass"
    assert "compressed and restored" in checks["cli_roundtrip"]["detail"]
    assert checks["protected_output"]["status"] == "pass"
    assert "byte-identical" in checks["protected_output"]["detail"]
    if shutil.which("node"):
        assert checks["plugin_hook"]["status"] == "pass"
        assert "OpenClaw contract" in checks["plugin_hook"]["detail"]
    else:
        assert checks["plugin_hook"]["status"] == "skip"
        assert "node" in checks["plugin_hook"]["detail"]
    assert plugin.read_bytes() == before
    assert not (home / "store").exists()


def test_doctor_reports_a_missing_plugin_without_running_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(tmp_path / "empty"))
    report = openclaw.doctor(path=tmp_path / "missing.js")
    checks = _checks(report)
    assert report["ok"] is False
    assert report["status"] == "fail"
    assert checks["plugin"]["status"] == "fail"
    assert all(checks[name]["status"] == "skip"
               for name in ("interpreter", "cli_roundtrip", "protected_output",
                            "plugin_hook", "host"))


def test_doctor_refuses_a_foreign_plugin(isolated_plugin, tmp_path):
    path = isolated_plugin
    path.write_text("// another plugin\n", encoding="utf-8")
    report = openclaw.doctor(path=path)
    checks = _checks(report)
    assert report["ok"] is False
    assert checks["plugin"]["status"] == "fail"
    assert "foreign" in report["probe"]
    assert checks["cli_roundtrip"]["status"] == "skip"


@pytest.fixture()
def isolated_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(tmp_path / "openclaw"))
    path = openclaw.plugin_path()
    path.parent.mkdir(parents=True)
    return path


def test_doctor_reports_a_missing_interpreter(tmp_path, monkeypatch):
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir(parents=True)
    config = config_dir / "openclaw.json"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    assert openclaw.install(python="/definitely/missing/sutra-python",
                            home=tmp_path / "home", config_path=config_dir) == 0
    report = openclaw.doctor()
    checks = _checks(report)
    assert report["ok"] is False
    assert report["status"] == "fail"
    assert checks["interpreter"]["status"] == "fail"
    assert "does not resolve" in checks["interpreter"]["detail"]
    assert checks["cli_roundtrip"]["status"] == "skip"
    assert checks["plugin_hook"]["status"] == "skip"


def test_node_absence_is_skipped_not_a_failure(installed):
    report = openclaw.doctor(node="")
    checks = _checks(report)
    assert report["ok"] is True
    assert report["status"] == "partial"
    assert checks["plugin_hook"]["status"] == "skip"
    assert "node" in checks["plugin_hook"]["detail"]
    if shutil.which("openclaw") is None:
        assert checks["host"]["status"] == "skip"


def test_cli_doctor_passes_openclaw_only(installed, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(claude_code, "binary", lambda: None)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-empty"))
    assert cli.main(["doctor", "--client", "openc claw", "--json"]) == 0
    reports = json.loads(capsys.readouterr().out)
    assert len(reports) == 1
    assert reports[0]["client"] == "openc claw"
    assert reports[0]["ok"] is True


def test_cli_doctor_returns_nonzero_for_a_real_failure(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir(parents=True)
    config = config_dir / "openclaw.json"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    assert openclaw.install(python="/missing/python",
                            home=tmp_path / "home", config_path=config_dir) == 0
    capsys.readouterr()  # discard the installer's success line
    monkeypatch.setattr(claude_code, "binary", lambda: None)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-empty"))
    assert cli.main(["doctor", "--client", "openc claw", "--json"]) == 1
    reports = json.loads(capsys.readouterr().out)
    assert reports[0]["status"] == "fail"
    assert reports[0]["ok"] is False