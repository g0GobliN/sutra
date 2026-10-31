"""The automatic OpenCode adapter: generated plugin, safe install, real hook.

The plugin is JavaScript, so its protocol behaviour is exercised by a Node
script against the real Python CLI (no OpenCode process). OpenCode itself
loads the same generated file; the host-level proof lives in the optional
scripted-provider run documented in docs/automatic-install.md.
"""

import json
import pathlib
import shutil
import stat
import subprocess
import sys

import pytest

from conftest import plugin_module

opencode = plugin_module("opencode")
cli = plugin_module("cli")


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    config = tmp_path / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text('{\n  "theme": "dark"\n}\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path, config


def test_render_embeds_json_paths_and_has_no_placeholders():
    text = opencode.render(python="/opt/sutra/bin/python", home="/tmp/sutra home")
    assert opencode.MARKER in text
    assert json.dumps("/opt/sutra/bin/python") in text
    assert json.dumps("/tmp/sutra home") in text
    assert "__SUTRA_PYTHON__" not in text
    assert "__SUTRA_HOME__" not in text


def test_install_writes_a_private_plugin_and_is_idempotent(paths, capsys):
    root, config = paths
    path = opencode.plugin_path(config)
    assert opencode.install(python="/opt/python", home=root / "home",
                            config_path=config) == 0
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert opencode.MARKER in path.read_text(encoding="utf-8")
    assert opencode.configured(config) is True

    first = path.read_text(encoding="utf-8")
    assert opencode.install(python="/opt/python", home=root / "home",
                            config_path=config) == 0
    assert path.read_text(encoding="utf-8") == first
    assert "nothing to do" in capsys.readouterr().out


def test_reinstall_after_an_interpreter_change_backs_up_the_old_plugin(paths):
    root, config = paths
    path = opencode.plugin_path(config)
    assert opencode.install(python="/old/python", home=root / "home",
                            config_path=config) == 0
    assert opencode.install(python="/new/python", home=root / "home",
                            config_path=config) == 0
    assert json.dumps("/new/python") in path.read_text(encoding="utf-8")
    backups = list(path.parent.glob("sutra.js.bak-*"))
    assert len(backups) == 1
    assert json.dumps("/old/python") in backups[0].read_text(encoding="utf-8")


def test_a_foreign_plugin_is_refused_not_overwritten(paths, capsys):
    _, config = paths
    path = opencode.plugin_path(config)
    path.parent.mkdir(parents=True)
    path.write_text("// my own plugin\n", encoding="utf-8")
    assert opencode.install(python="/opt/python", home=path.parent / "home",
                            config_path=config) == 1
    assert path.read_text(encoding="utf-8") == "// my own plugin\n"
    assert "refusing" in capsys.readouterr().err


def test_uninstall_removes_only_a_generated_plugin(paths):
    root, config = paths
    path = opencode.plugin_path(config)
    assert opencode.install(python="/opt/python", home=root / "home",
                            config_path=config) == 0
    assert opencode.uninstall(config_path=config) == 0
    assert not path.exists()
    assert opencode.uninstall(config_path=config) == 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// not ours\n", encoding="utf-8")
    assert opencode.uninstall(config_path=config) == 1
    assert path.exists()


def test_dry_run_writes_nothing(paths, capsys):
    _, config = paths
    path = opencode.plugin_path(config)
    assert opencode.install(dry_run=True, config_path=config) == 0
    assert not path.exists()
    assert "dry run" in capsys.readouterr().out


def test_cli_install_without_a_config_still_installs_the_plugin(paths, capsys):
    root, config = paths
    config.unlink()  # no config file; the plugin does not need one
    assert cli.main(["install", "--client", "opencode"]) == 0
    assert opencode.plugin_path(config).is_file()
    output = capsys.readouterr().out
    assert "automatic plugin" in output
    assert "MCP recovery entry skipped" in output


def test_cli_status_reports_the_plugin_as_automatic(paths, capsys):
    root, config = paths
    assert cli.main(["install", "--client", "opencode"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(item for item in rows if item["client"] == "opencode")
    assert row["plugin"] == "installed"
    assert row["automatic"] is True


def test_status_succeeds_with_only_the_automatic_plugin(paths, capsys):
    """A plugin-only install is a working automatic integration, not a failure."""
    root, config = paths
    config.unlink()  # no OpenCode config means no MCP recovery entry
    assert cli.main(["install", "--client", "opencode"]) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "plugin=installed" in output
    assert "automatic=yes" in output


def test_generated_javascript_parses_as_a_module(paths):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    _, config = paths
    text = opencode.render(python=sys.executable, home=config.parent / "home")
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "--check"],
        input=text, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_plugin_hook_compresses_and_recovers_through_the_real_cli(paths, tmp_path):
    """Node loads the generated plugin; the plugin spawns the real CLI."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    _, config = paths
    plugin = tmp_path / "sutra-plugin.mjs"
    home = tmp_path / "home"
    assert opencode.install(python=sys.executable, home=home, config_path=config) == 0
    plugin.write_text(opencode.render(python=sys.executable, home=home), encoding="utf-8")

    script = tmp_path / "hook.mjs"
    script.write_text(f"""
import assert from "node:assert/strict"
const plugin = await import({json.dumps(plugin.as_uri())})
const hooks = await plugin.Sutra({{}})
const lines = Array.from({{length: 3000}}, (_, i) => `line ${{i}} padding padding`)
lines[1500] = "CLUE: opencode-plugin-token-9K"
const output = {{ title: "bash", output: lines.join("\\n"), metadata: {{ exit: 0 }} }}
await hooks["tool.execute.after"]({{ tool: "bash" }}, output)
assert.match(output.output, /\\[sutra:/)
assert.ok(!output.output.includes("opencode-plugin-token-9K"))
assert.equal(output.title, "bash")
assert.deepEqual(output.metadata, {{ exit: 0 }})
const match = output.output.match(/id=(su_[0-9a-f]{{32}})/)
assert.ok(match, "marker must carry an id")
const restored = JSON.parse(await hooks.tool.sutra_restore.execute(
  {{ id: match[1], mode: "search", query: "opencode-plugin-token-9K", context: 1 }}))
assert.equal(restored.status, "ok")
assert.ok(restored.content.includes("opencode-plugin-token-9K"))
const missing = JSON.parse(await hooks.tool.sutra_restore.execute({{ id: "su_" + "a".repeat(32) }}))
assert.equal(missing.status, "not_found")
console.log("opencode plugin ok")
""", encoding="utf-8")
    import os
    # The repository root *is* the ``sutra`` package, so a checkout whose
    # directory has another name needs the package exposed under its import
    # name. A real install provides that; the test builds the same shape.
    library = tmp_path / "lib"
    library.mkdir()
    (library / "sutra").symlink_to(pathlib.Path(__file__).resolve().parents[1],
                                    target_is_directory=True)
    env = dict(os.environ, PYTHONPATH=str(library), HERMES_HOME=str(home))
    result = subprocess.run([node, str(script)], capture_output=True, text=True,
                            timeout=90, cwd=str(tmp_path), env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "opencode plugin ok" in result.stdout
