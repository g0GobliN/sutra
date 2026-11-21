"""The installed plugin copy must be compared with the package it came from.

Hermes runs whatever the plugin directory declares. A copy installed before the
request stage existed is enabled, validates, and executes only the stages it
declares, so `sutra doctor` called such a copy healthy. These tests pin the
comparison that makes the difference visible.
"""

from __future__ import annotations

import pathlib

from conftest import plugin_module

hermes = plugin_module("hermes")


def write_plugin(directory: pathlib.Path, *, middleware=("tool_execution",),
                 hooks=(), tools=("sutra_restore",), prompt="PROMPT V1",
                 init="def register(ctx):\n    return None\n",
                 manifest=True) -> pathlib.Path:
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["manifest_version: 1", "name: sutra", 'version: "0.6.3"']
    for key, values in (("provides_middleware", middleware),
                        ("provides_hooks", hooks),
                        ("provides_tools", tools)):
        lines.append(f"{key}:")
        lines.extend(f"  - {value}" for value in values)
    if manifest:
        (directory / "plugin.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (directory / "prompt.py").write_text(prompt, encoding="utf-8")
    (directory / "__init__.py").write_text(init, encoding="utf-8")
    return directory


def test_a_copy_that_declares_every_stage_is_current(tmp_path):
    origin = write_plugin(tmp_path / "origin", middleware=("tool_execution", "llm_request"),
                          hooks=("post_api_request",))
    installed = write_plugin(tmp_path / "installed", middleware=("tool_execution", "llm_request"),
                             hooks=("post_api_request",))
    report = hermes.deployment(installed, origin)
    assert report["current"] is True
    assert report["missing"] == {}
    assert report["changed_files"] == []
    assert report["reason"] == "the installed copy matches this package"


def test_a_copy_without_the_request_stage_is_stale(tmp_path):
    origin = write_plugin(tmp_path / "origin", middleware=("tool_execution", "llm_request"),
                          hooks=("post_api_request",))
    installed = write_plugin(tmp_path / "installed", middleware=("tool_execution",))
    report = hermes.deployment(installed, origin)
    assert report["current"] is False
    assert report["missing"]["provides_middleware"] == ["llm_request"]
    assert report["missing"]["provides_hooks"] == ["post_api_request"]
    assert "predates this package" in report["reason"]
    assert "llm_request" in report["reason"]


def test_a_copy_that_declares_an_extra_stage_is_not_hidden(tmp_path):
    origin = write_plugin(tmp_path / "origin")
    installed = write_plugin(tmp_path / "installed", hooks=("post_api_request",))
    report = hermes.deployment(installed, origin)
    assert report["current"] is True
    assert report["extra"]["provides_hooks"] == ["post_api_request"]


def test_changed_files_make_a_copy_stale(tmp_path):
    origin = write_plugin(tmp_path / "origin", middleware=("tool_execution", "llm_request"))
    installed = write_plugin(tmp_path / "installed", middleware=("tool_execution", "llm_request"),
                             prompt="PROMPT V0")
    report = hermes.deployment(installed, origin)
    assert report["current"] is False
    assert report["missing"] == {}
    assert report["changed_files"] == [{"file": "prompt.py", "state": "differs"}]
    assert "files differ" in report["reason"]


def test_a_file_the_installed_copy_lacks_is_named(tmp_path):
    origin = write_plugin(tmp_path / "origin", middleware=("tool_execution", "llm_request"))
    installed = write_plugin(tmp_path / "installed", middleware=("tool_execution", "llm_request"))
    (installed / "prompt.py").unlink()
    report = hermes.deployment(installed, origin)
    assert report["changed_files"] == [{"file": "prompt.py", "state": "missing"}]
    assert report["current"] is False


def test_a_missing_manifest_is_unknown_not_current(tmp_path):
    origin = write_plugin(tmp_path / "origin")
    installed = write_plugin(tmp_path / "installed", manifest=False)
    report = hermes.deployment(installed, origin)
    assert report["current"] is None
    assert "no readable plugin.yaml" in report["reason"]


def test_the_formatter_names_the_missing_stage(tmp_path):
    origin = write_plugin(tmp_path / "origin", middleware=("tool_execution", "llm_request"))
    installed = write_plugin(tmp_path / "installed")
    report = {"client": "hermes", "installed": True, "enabled": True, "ok": False,
              "status": "stale",
              "probe": hermes.deployment(installed, origin)["reason"],
              "copy": hermes.deployment(installed, origin)}
    line = hermes.format_doctor(report)
    assert "copy=stale" in line
    assert "missing_stages=llm_request" in line
    assert "status=stale" in line


def test_status_reports_the_copy_state(tmp_path, monkeypatch):
    origin = write_plugin(tmp_path / "origin", middleware=("tool_execution", "llm_request"))
    installed = write_plugin(tmp_path / "installed")
    monkeypatch.setattr(hermes, "plugin_dir", lambda: installed)
    monkeypatch.setattr(hermes, "source_dir", lambda: origin)
    monkeypatch.setattr(hermes, "binary", lambda: None)
    monkeypatch.setattr(hermes, "home", lambda: tmp_path)
    monkeypatch.setattr(hermes, "config_path", lambda: tmp_path / "config.yaml")
    report = hermes.status()
    assert report["current"] is False
    assert report["missing_stages"]["provides_middleware"] == ["llm_request"]
    assert report["automatic"] is False
