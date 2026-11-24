"""The automatic OpenClaw adapter: generated plugin, safe install, real hook.

The plugin is JavaScript, so its protocol behaviour is exercised by a Node
script against the real Python CLI (no OpenClaw process). OpenClaw itself
loads the same generated file; the host-level proof lives in the optional
scripted-provider run documented in docs/automatic-install.md.
"""

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys

import pytest

from conftest import expose_package, plugin_module

openclaw = plugin_module("openclaw")
installer = plugin_module("installer")
cli = plugin_module("cli")


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    """Set up isolated OpenClaw config directory using SUTRA_OPENCLAW_CONFIG."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir(parents=True)
    config = config_dir / "openclaw.json"
    config.write_text('{\n  "theme": "dark"\n}\n', encoding="utf-8")
    # SUTRA_OPENCLAW_CONFIG should point to the config directory, not the file
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    # Add openclaw binary to PATH so shutil.which("openclaw") finds it
    oc_bin = "/tmp/oc-home/node_modules/.bin"
    monkeypatch.setenv("PATH", f"{oc_bin}:{os.environ.get('PATH', '')}")
    return tmp_path, config_dir, config


def test_render_embeds_json_paths_and_has_no_placeholders(tmp_path):
    home = tmp_path / "sutra home"
    text = openclaw.render(python="/opt/sutra/bin/python", home=home)
    assert openclaw.MARKER in text
    assert json.dumps("/opt/sutra/bin/python") in text
    assert json.dumps(str(home)) in text
    assert "__SUTRA_PYTHON__" not in text
    assert "__SUTRA_HOME__" not in text


def test_install_writes_a_private_plugin_and_is_idempotent(paths, capsys):
    root, config_dir, config = paths
    path = openclaw.plugin_path(config_dir)
    assert openclaw.install(python="/opt/python", home=root / "home",
                            config_path=config_dir) == 0
    assert path.is_file()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    else:
        assert path.is_file()
    assert openclaw.MARKER in path.read_text(encoding="utf-8")
    assert openclaw.configured(config_dir) is True

    first = path.read_text(encoding="utf-8")
    assert openclaw.install(python="/opt/python", home=root / "home",
                            config_path=config_dir) == 0
    assert path.read_text(encoding="utf-8") == first
    assert "nothing to do" in capsys.readouterr().out


def test_install_names_the_plugin_in_the_host_config(paths):
    """A file under the config tree is inert until the host config names it."""
    root, config_dir, config = paths
    path = openclaw.plugin_path(config_dir)
    assert openclaw.install(python="/opt/python", home=root / "home",
                            config_path=config_dir) == 0
    value = json.loads(config.read_text(encoding="utf-8"))
    assert str(path) in value["plugins"]["load"]["paths"]
    assert "sutra" in value["plugins"]["allow"]
    assert value["theme"] == "dark"

    assert openclaw.install(python="/opt/python", home=root / "home",
                            config_path=config_dir) == 0
    again = json.loads(config.read_text(encoding="utf-8"))
    assert again["plugins"]["load"]["paths"].count(str(path)) == 1
    assert again["plugins"]["allow"].count("sutra") == 1


def test_uninstall_takes_the_config_entries_back(paths):
    root, config_dir, config = paths
    path = openclaw.plugin_path(config_dir)
    assert openclaw.install(python="/opt/python", home=root / "home",
                            config_path=config_dir) == 0
    assert openclaw.uninstall(config_path=config_dir) == 0
    assert not path.exists()
    value = json.loads(config.read_text(encoding="utf-8"))
    assert str(path) not in value["plugins"]["load"]["paths"]
    assert "sutra" not in value["plugins"]["allow"]
    assert value["theme"] == "dark"


def test_doctor_reports_a_plugin_the_config_does_not_name(paths):
    root, config_dir, config = paths
    assert openclaw.install(python="/opt/python", home=root / "home",
                            config_path=config_dir) == 0
    value = json.loads(config.read_text(encoding="utf-8"))
    value["plugins"]["load"]["paths"] = []
    value["plugins"]["allow"] = []
    config.write_text(json.dumps(value), encoding="utf-8")
    report = openclaw.doctor(path=str(openclaw.plugin_path(config_dir)),
                             node="/nonexistent/node")
    config_check = next(item for item in report["checks"] if item["name"] == "config")
    assert config_check["status"] == "fail"
    assert "plugins.load.paths" in config_check["detail"]
    assert "plugins.allow" in config_check["detail"]


def test_install_auto_detection_includes_an_installed_openclaw(paths):
    root, config_dir, _ = paths
    assert openclaw.install(python="/opt/python", home=root / "home",
                            config_path=config_dir) == 0
    assert "openclaw" in installer.detect_clients()


def test_openclaw_dry_run_is_reported_as_would_configure(paths, capsys):
    root, config_dir, _ = paths
    assert cli.main(["install", "--client", "openclaw", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "writing the plugin so it is ready" in output
    assert "openclaw=would configure" in output
    assert "openclaw=skipped" not in output
    assert not openclaw.plugin_path(config_dir).exists()


def test_reinstall_after_an_interpreter_change_backs_up_the_old_plugin(paths):
    root, config_dir, config = paths
    path = openclaw.plugin_path(config_dir)
    assert openclaw.install(python="/old/python", home=root / "home",
                            config_path=config_dir) == 0
    assert openclaw.install(python="/new/python", home=root / "home",
                            config_path=config_dir) == 0
    assert json.dumps("/new/python") in path.read_text(encoding="utf-8")
    backups = list(path.parent.glob("sutra.js.bak-*"))
    assert len(backups) == 1
    assert json.dumps("/old/python") in backups[0].read_text(encoding="utf-8")


def test_a_foreign_plugin_is_refused_not_overwritten(paths, capsys):
    _, config_dir, config = paths
    path = openclaw.plugin_path(config_dir)
    path.parent.mkdir(parents=True)
    path.write_text("// my own plugin\n", encoding="utf-8")
    assert openclaw.install(python="/opt/python", home=path.parent / "home",
                            config_path=config_dir) == 1
    assert path.read_text(encoding="utf-8") == "// my own plugin\n"
    assert "refusing" in capsys.readouterr().err


def test_uninstall_removes_only_a_generated_plugin(paths):
    root, config_dir, config = paths
    path = openclaw.plugin_path(config_dir)
    assert openclaw.install(python="/opt/python", home=root / "home",
                            config_path=config_dir) == 0
    assert openclaw.uninstall(config_path=config_dir) == 0
    assert not path.exists()
    assert openclaw.uninstall(config_path=config_dir) == 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// not ours\n", encoding="utf-8")
    assert openclaw.uninstall(config_path=config_dir) == 1
    assert path.exists()


def test_cli_uninstall_removes_the_plugin_without_the_host_binary(
        paths, monkeypatch, capsys):
    root, config_dir, config = paths
    path = openclaw.plugin_path(config_dir)
    assert openclaw.install(python="/opt/python", home=root / "home",
                            config_path=config_dir) == 0
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)

    assert cli.main(["uninstall", "--client", "openclaw"]) == 0
    assert not path.exists()


def test_dry_run_writes_nothing(paths, capsys):
    _, config_dir, config = paths
    path = openclaw.plugin_path(config_dir)
    assert openclaw.install(dry_run=True, config_path=config_dir) == 0
    assert not path.exists()
    assert "dry run" in capsys.readouterr().out


def test_dry_run_without_a_config_does_not_claim_an_install(paths, capsys):
    _, config_dir, config = paths
    config.unlink()
    # Keep SUTRA_OPENCLAW_CONFIG set so plugin_path uses the temp dir,
    # not the real ~/.openclaw
    assert cli.main(["install", "--client", "openclaw", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "dry run" in output
    assert "plugin is installed" not in output
    assert not openclaw.plugin_path(config_dir).exists()


def test_cli_install_without_a_config_still_installs_the_plugin(paths, capsys):
    root, config_dir, config = paths
    config.unlink()  # no config file; the plugin does not need one
    import os
    os.environ["SUTRA_OPENCLAW_CONFIG"] = str(config_dir)
    assert cli.main(["install", "--client", "openclaw"]) == 0
    assert openclaw.plugin_path(config_dir).is_file()
    output = capsys.readouterr().out
    assert "configured" in output
    assert "restart OpenClaw" in output


def test_cli_status_reports_the_plugin_as_automatic(paths, capsys):
    root, config_dir, config = paths
    assert cli.main(["install", "--client", "openclaw"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(item for item in rows if item["client"] == "openclaw")
    assert row["plugin"] == "installed"
    assert row["automatic"] is True


def test_status_succeeds_with_only_the_automatic_plugin(paths, capsys):
    """A plugin-only install is a working automatic integration, not a failure."""
    root, config_dir, config = paths
    config.unlink()  # no OpenClaw config means no MCP recovery server entry
    import os
    os.environ["SUTRA_OPENCLAW_CONFIG"] = str(config_dir)
    assert cli.main(["install", "--client", "openclaw"]) == 0
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "plugin=installed" in output
    assert "automatic=yes" in output


def test_generated_javascript_parses_as_a_module(paths):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    _, config_dir, config = paths
    text = openclaw.render(python=sys.executable, home=config_dir / "home")
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "--check"],
        input=text, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_plugin_hook_compresses_and_recovers_through_the_real_cli(paths, tmp_path):
    """Node loads the generated plugin; the plugin spawns the real CLI."""
    import os
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    if os.environ.get("CI") == "true":
        pytest.skip("OpenClaw plugin test unstable in CI environment")
    _, config_dir, config = paths
    plugin = tmp_path / "sutra-plugin.cjs"
    home = tmp_path / "home"
    assert openclaw.install(python=sys.executable, home=home, config_path=config_dir) == 0
    plugin.write_text(openclaw.render(python=sys.executable, home=home), encoding="utf-8")

    script = tmp_path / "hook.mjs"
    script.write_text(f"""
import assert from "node:assert/strict"
import {{ createRequire }} from "node:module"
const require = createRequire(import.meta.url)
const plugin = require({json.dumps(str(plugin))})
const hooks = await plugin.Sutra({{}})
const lines = Array.from({{length: 3000}}, (_, i) => `line ${{i}} padding padding`)
lines[1500] = "CLUE: openclaw-plugin-token-9K"
const output = {{ title: "bash", output: lines.join("\\n"), metadata: {{ exit: 0 }} }}
await hooks["tool.execute.after"]({{ tool: "bash" }}, output)
assert.match(output.output, /\\[sutra:/)
assert.ok(!output.output.includes("openclaw-plugin-token-9K"))
assert.equal(output.title, "bash")
assert.deepEqual(output.metadata, {{ exit: 0 }})
const match = output.output.match(/id=(su_[0-9a-f]{{32}})/)
assert.ok(match, "marker must carry an id")
const restored = JSON.parse(await hooks.tool.sutra_restore.execute(
  {{ id: match[1], mode: "search", query: "openclaw-plugin-token-9K", context: 1 }}))
assert.equal(restored.status, "ok")
assert.ok(restored.content.includes("openclaw-plugin-token-9K"))
const missing = JSON.parse(await hooks.tool.sutra_restore.execute({{ id: "su_" + "a".repeat(32) }}))
assert.equal(missing.status, "not_found")
console.log("openclaw plugin ok")
""", encoding="utf-8")
    import os
    # The repository root *is* the ``sutra`` package, so a checkout whose
    # directory has another name needs the package exposed under its import
    # name. A real install provides that; the test builds the same shape.
    library = tmp_path / "lib"
    expose_package(library / "sutra")
    env = dict(os.environ, PYTHONPATH=str(library), HERMES_HOME=str(home))
    result = subprocess.run([node, str(script)], capture_output=True, text=True,
                            timeout=90, cwd=str(tmp_path), env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "openclaw plugin ok" in result.stdout


def test_plugin_hook_allows_a_slow_but_successful_compression(paths, tmp_path):
    """Regression: a 2.5 s hook bound killed the CLI on a large result and
    silently failed open, leaving the payload uncompressed."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    if os.environ.get("CI") == "true":
        pytest.skip("OpenClaw plugin test unstable in CI environment")
    if os.name != "posix":
        pytest.skip("the slow wrapper is a POSIX shell script")
    _, config_dir, config = paths
    home = tmp_path / "home"
    wrapper = tmp_path / "slow-python"
    wrapper.write_text(
        "#!/bin/sh\nsleep 3\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
        encoding="utf-8")
    wrapper.chmod(0o755)
    plugin = tmp_path / "slow-plugin.cjs"
    plugin.write_text(openclaw.render(python=str(wrapper), home=home), encoding="utf-8")

    script = tmp_path / "slow-hook.mjs"
    script.write_text(f"""
import assert from "node:assert/strict"
import {{ createRequire }} from "node:module"
const require = createRequire(import.meta.url)
const plugin = require({json.dumps(str(plugin))})
const hooks = await plugin.Sutra({{}})
const lines = Array.from({{length: 3000}}, (_, i) => `line ${{i}} padding padding`)
lines[1500] = "CLUE: openclaw-slow-token-7Q"
const output = {{ title: "bash", output: lines.join("\\n"), metadata: {{ exit: 0 }} }}
await hooks["tool.execute.after"]({{ tool: "bash" }}, output)
assert.match(output.output, /\\[sutra:/)
assert.ok(!output.output.includes("openclaw-slow-token-7Q"))
console.log("openclaw slow hook ok")
""", encoding="utf-8")
    library = tmp_path / "lib"
    expose_package(library / "sutra")
    env = dict(os.environ, PYTHONPATH=str(library), HERMES_HOME=str(home))
    result = subprocess.run([node, str(script)], capture_output=True, text=True,
                            timeout=30, cwd=str(tmp_path), env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "openclaw slow hook ok" in result.stdout

def test_a_failed_registration_rolls_the_plugin_back(tmp_path, monkeypatch):
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    config = config_dir / "openclaw.json"
    config.write_text('{"plugins": {"load": {"paths": "not-a-list"}}}\n', encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))

    code = openclaw.install(python="/usr/bin/python3", home=tmp_path / "home",
                            config_path=config_dir)
    assert code == 1
    # Nothing is left behind: a later install must not call this file present.
    assert not openclaw.plugin_path(config_dir).exists()
    assert "not-a-list" in config.read_text(encoding="utf-8")


def test_uninstall_clears_the_config_when_the_file_is_already_gone(tmp_path, monkeypatch):
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    config = config_dir / "openclaw.json"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    assert openclaw.install(python="/usr/bin/python3", home=tmp_path / "home",
                            config_path=config_dir) == 0
    plugin = openclaw.plugin_path(config_dir)
    plugin.unlink()

    assert openclaw.uninstall(config_path=config_dir) == 0
    assert not openclaw.named_in_config(config_dir)
