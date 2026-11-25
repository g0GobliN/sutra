"""The automatic OpenClaw adapter: generated plugin, safe install, real hook.

The plugin is JavaScript, so its protocol behaviour is exercised by a Node
script against the real Python CLI (no OpenClaw process). OpenClaw itself
loads the same generated file; the host-level proof lives in the optional
scripted-provider run documented in docs/automatic-install.md.
"""

import json
import os
import pathlib
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

def test_the_host_named_config_file_decides_where_the_plugin_goes(tmp_path, monkeypatch):
    """OPENCLAW_CONFIG_PATH names the file; its tree is what SUTRA writes into."""
    monkeypatch.delenv("SUTRA_OPENCLAW_CONFIG", raising=False)
    tree = tmp_path / "oclaw"
    tree.mkdir()
    named = tree / "custom.json"
    named.write_text('{"theme": "dark"}\n', encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(named))

    tree_out, file_out, plugin_out = openclaw.config_layout()
    assert file_out == named
    assert tree_out == tree
    assert plugin_out == tree / "plugins" / "sutra.js"

    assert openclaw.install(python="/usr/bin/python3", home=tmp_path / "home") == 0
    assert plugin_out.is_file()
    # The entry lands in the file the host reads, not in a sibling default name.
    written = json.loads(named.read_text(encoding="utf-8"))
    assert written["theme"] == "dark", "the host's own keys must survive"
    assert str(plugin_out) in written["plugins"]["load"]["paths"]
    assert written["plugins"]["allow"] == ["sutra"]
    assert not (tree / "openclaw.json").exists()


def test_an_explicit_config_directory_wins_over_the_environment(tmp_path, monkeypatch):
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(elsewhere / "custom.json"))
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(elsewhere))

    assert openclaw.config_layout(chosen) == (
        chosen, chosen / "openclaw.json", chosen / "plugins" / "sutra.js")


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX permission bits are what this asserts")
def test_a_rollback_keeps_the_config_file_mode_and_the_backup(tmp_path, monkeypatch, capsys):
    """Restoring the text must not quietly re-mode a file the user owns."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    config = config_dir / "openclaw.json"
    config.write_text('{"theme": "dark"}\n', encoding="utf-8")
    config.chmod(0o644)
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    plugin = openclaw.plugin_path(config_dir)
    plugin.parent.mkdir(parents=True)
    previous = openclaw.render(python="/usr/bin/python3", home=tmp_path / "home")
    plugin.write_text(previous + "\n// older build\n", encoding="utf-8")
    original = plugin.read_text(encoding="utf-8")

    def refuse_save(_file, _value):
        raise OSError("the config became read-only")
    monkeypatch.setattr(openclaw, "_save_config", refuse_save)

    assert openclaw.install(python="/usr/bin/python3", home=tmp_path / "home",
                            config_path=config_dir) == 1
    capsys.readouterr()
    assert stat.S_IMODE(config.stat().st_mode) == 0o644
    assert plugin.read_text(encoding="utf-8") == original
    (backup,) = list(plugin.parent.glob("sutra.js.bak-*"))
    assert backup.read_text(encoding="utf-8") == original, (
        "the backup describes the file that survived, not the build that was discarded")


@pytest.mark.skipif(sys.platform == "win32",
                    reason="file modes do not make a file unreadable on Windows")
def test_register_refuses_an_unreadable_config_instead_of_replacing_it(tmp_path, monkeypatch):
    """An unreadable config is not an empty one: writing would destroy it."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    config = config_dir / "openclaw.json"
    config.write_text('{"theme": "dark"}\n', encoding="utf-8")
    config.chmod(0o000)
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    try:
        with pytest.raises(OSError):
            openclaw.register(config_dir)
    finally:
        config.chmod(0o600)
    assert config.read_text(encoding="utf-8") == '{"theme": "dark"}\n'


def test_uninstall_without_the_plugin_reports_a_config_it_cannot_clean(tmp_path, monkeypatch, capsys):
    """The no-file branch reports, and never repairs by rewriting what it cannot parse."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    config = config_dir / "openclaw.json"
    config.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))

    assert openclaw.uninstall(config_path=config_dir) == 1
    assert "could not clean" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == "{not json"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="file modes do not make a file unreadable on Windows")
def test_install_refuses_an_unreadable_config_before_writing_anything(tmp_path, monkeypatch, capsys):
    """Text SUTRA never saw cannot be restored, so the install must not start."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    config = config_dir / "openclaw.json"
    config.write_text('{"theme": "dark"}\n', encoding="utf-8")
    config.chmod(0o000)
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    try:
        assert openclaw.install(python="/usr/bin/python3", home=tmp_path / "home",
                                config_path=config_dir) == 1
    finally:
        config.chmod(0o600)
    assert "cannot read" in capsys.readouterr().err
    assert not openclaw.plugin_path(config_dir).exists()
    assert config.read_text(encoding="utf-8") == '{"theme": "dark"}\n'


def test_install_refuses_an_unusable_config_before_writing_anything(tmp_path, monkeypatch, capsys):
    """A shape the entry cannot be added to is a refusal, not a rollback."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    config = config_dir / "openclaw.json"
    original = '{"plugins": {"load": {"paths": "not-a-list"}}}\n'
    config.write_text(original, encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))

    assert openclaw.install(python="/usr/bin/python3", home=tmp_path / "home",
                            config_path=config_dir) == 1
    assert "cannot name the plugin" in capsys.readouterr().err
    assert not openclaw.plugin_path(config_dir).exists()
    assert config.read_text(encoding="utf-8") == original


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


def test_a_failed_save_restores_the_previous_plugin_and_keeps_its_backup(tmp_path, monkeypatch):
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    (config_dir / "openclaw.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    plugin = openclaw.plugin_path(config_dir)

    # The config is valid, so the install gets as far as writing the plugin;
    # only the config save fails, which is what the rollback must survive.
    def refuse_save(_file, _value):
        raise OSError("the config became read-only")
    monkeypatch.setattr(openclaw, "_save_config", refuse_save)
    plugin.parent.mkdir(parents=True)
    previous = openclaw.render(python="/usr/bin/python3",
                               home=tmp_path / "home").replace(
        "SUTRA_MODE", "SUTRA_MODE", 1)
    plugin.write_text(previous + "\n// older build\n", encoding="utf-8")

    assert openclaw.install(python="/usr/bin/python3", home=tmp_path / "home",
                            config_path=config_dir) == 1
    assert plugin.read_text(encoding="utf-8") == previous + "\n// older build\n"
    backups = list(plugin.parent.glob("sutra.js.bak-*"))
    assert backups, "the rollback must leave the backup it restored from"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="a read-only directory is how this failure is injected; Windows ignores the mode")
def test_uninstall_keeps_the_file_when_the_config_cannot_be_cleaned(tmp_path, monkeypatch):
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    plugin = openclaw.plugin_path(config_dir)
    plugin.parent.mkdir(parents=True)
    plugin.write_text(openclaw.render(python="/usr/bin/python3",
                                      home=tmp_path / "home"), encoding="utf-8")
    (config_dir / "openclaw.json").write_text(
        json.dumps({"plugins": {"load": {"paths": [str(plugin)]},
                                "allow": ["sutra"]}}) + "\n", encoding="utf-8")
    # A config directory nobody may write leaves the entries in place, so the
    # file must stay: deleting it would point the host at a missing path.
    config_dir.chmod(0o500)
    try:
        assert openclaw.uninstall(config_path=config_dir) == 1
        assert plugin.exists()
    finally:
        config_dir.chmod(0o700)


def test_the_host_named_file_wins_over_a_named_directory(tmp_path, monkeypatch):
    """A directory variable says where the plugin goes; the host says which file it reads."""
    plugin_tree = tmp_path / "plugins-tree"
    plugin_tree.mkdir()
    host = tmp_path / "host"
    host.mkdir()
    named = host / "custom.json"
    named.write_text('{"theme": "dark"}\n', encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(plugin_tree))
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(named))

    tree, file, plugin = openclaw.config_layout()
    assert (tree, file, plugin) == (plugin_tree, named, plugin_tree / "plugins" / "sutra.js")

    assert openclaw.install(python="/usr/bin/python3", home=tmp_path / "home") == 0
    written = json.loads(named.read_text(encoding="utf-8"))
    assert str(plugin) in written["plugins"]["load"]["paths"]
    assert not (plugin_tree / "openclaw.json").exists(), (
        "the entry belongs in the file the host reads, not in one derived beside it")


def test_a_config_another_process_created_is_left_alone(tmp_path, monkeypatch, capsys):
    """O_EXCL, not text comparison: a file this run did not create is untouchable."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    config = config_dir / "openclaw.json"
    foreign = '{"theme": "light"}\n'

    def lose_the_race(file, _value):
        file.write_text(foreign, encoding="utf-8")
        raise FileExistsError(17, "File exists", str(file))
    monkeypatch.setattr(openclaw, "_create_config", lose_the_race)

    assert openclaw.install(python=sys.executable, home=tmp_path / "home",
                            config_path=config_dir) == 1
    err = capsys.readouterr().err
    assert config.read_text(encoding="utf-8") == foreign, (
        "a config this run did not create must survive the rollback")
    assert not openclaw.plugin_path(config_dir).exists()
    assert "another process" in err


def test_install_stops_when_it_cannot_keep_the_config_it_replaces(paths, monkeypatch, capsys):
    """A rewrite loses formatting, so no copy means no rewrite."""
    root, config_dir, config = paths
    before = config.read_text(encoding="utf-8")

    def cannot_copy(_path):
        raise OSError("no space left on device")
    monkeypatch.setattr(openclaw, "_backup_config", cannot_copy)

    assert openclaw.install(python=sys.executable, home=root / "home",
                            config_path=config_dir) == 1

    assert config.read_text(encoding="utf-8") == before
    assert not openclaw.plugin_path(config_dir).exists()
    assert "no space left on device" in capsys.readouterr().err


def test_unregister_leaves_an_entry_it_cannot_trace_to_its_own_file(paths, capsys):
    """Another tree's install owns that entry; removing it disables that one."""
    _, config_dir, config = paths
    other = "/elsewhere/plugins/sutra.js"
    config.write_text(json.dumps(
        {"plugins": {"load": {"paths": [other]}, "allow": ["sutra"]}},
        indent=2) + "\n", encoding="utf-8")

    assert openclaw.unregister(config_path=config_dir,
                               path=openclaw.plugin_path(config_dir)) is False

    assert json.loads(config.read_text(encoding="utf-8")) == {
        "plugins": {"load": {"paths": [other]}, "allow": ["sutra"]}}


def test_uninstall_keeps_the_plugin_when_the_config_cannot_be_parsed(tmp_path, monkeypatch, capsys):
    """A config SUTRA cannot parse may still name the plugin, so the file stays."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    (config_dir / "openclaw.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))
    plugin = openclaw.plugin_path(config_dir)
    plugin.parent.mkdir(parents=True)
    plugin.write_text(openclaw.render(python="/usr/bin/python3",
                                      home=tmp_path / "home"), encoding="utf-8")

    assert openclaw.uninstall(config_path=config_dir) == 1
    assert "could not clean" in capsys.readouterr().out
    assert plugin.exists(), "the host may still point at this path"


def test_the_config_check_requires_the_file_it_was_given(tmp_path, monkeypatch):
    """Only the path asked about counts: the host loads that path or nothing."""
    config_dir = tmp_path / "openclaw"
    config_dir.mkdir()
    canonical = openclaw.plugin_path(config_dir)
    (config_dir / "openclaw.json").write_text(json.dumps(
        {"plugins": {"load": {"paths": [str(canonical)]}, "allow": ["sutra"]}}) + "\n",
        encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(config_dir))

    given = tmp_path / "somewhere" / "sutra.js"
    assert openclaw._config_check(given, config_dir)["status"] == "fail"
    assert openclaw._config_check(canonical, config_dir)["status"] == "pass"


def test_a_second_install_keeps_the_first_copy_of_the_config(paths):
    """The backup holds the host's own text; a later rewrite cannot bury it."""
    _, config_dir, config = paths
    assert openclaw.install(python=sys.executable) == 0
    backup = config_dir / "openclaw.json.sutra-bak"
    first = backup.read_text(encoding="utf-8")
    assert '"dark"' in first

    # The host edits its config and installs again: the copy still holds the
    # text from before SUTRA first touched the file, not the previous rewrite.
    config.write_text('{\n  "theme": "light"\n}\n', encoding="utf-8")
    openclaw.plugin_path(config_dir).unlink()
    assert openclaw.install(python=sys.executable) == 0
    assert backup.read_text(encoding="utf-8") == first


def test_install_says_so_when_the_named_file_is_not_the_one_it_uses(paths, tmp_path,
                                                                  monkeypatch, capsys):
    """An explicit directory wins, and the install warns where the host reads."""
    _, config_dir, _ = paths
    elsewhere = tmp_path / "elsewhere" / "openclaw.json"
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(elsewhere))

    assert openclaw.install(python=sys.executable, config_path=config_dir) == 0
    out = capsys.readouterr().out
    assert "OPENCLAW_CONFIG_PATH names" in out
    assert (config_dir / "openclaw.json").is_file()
    assert not elsewhere.exists()


def test_the_entry_declares_the_host_contract_and_hides_the_wrapper(paths):
    """The entry is the plain contract OpenClaw reads, not an SDK wrapper.

    ``openclaw/plugin-sdk`` cannot be imported from a CommonJS file the loader
    calls ``require()`` on, so the generated entry carries ``id``,
    ``register(ctx)`` and ``contracts.tools`` itself, and the test wrapper is a
    non-enumerable property that never becomes part of that contract.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    _, config_dir, _config = paths
    plugin_file = config_dir / "entry.js"
    plugin_file.write_text(openclaw.render(python=sys.executable,
                                           home=config_dir / "home"), encoding="utf-8")
    script = (
        "const plugin = require(process.env.SUTRA_PLUGIN);"
        "process.stdout.write(JSON.stringify({keys: Object.keys(plugin),"
        " wrapper: typeof plugin.Sutra, id: plugin.id,"
        " register: typeof plugin.register,"
        " tools: plugin.contracts && plugin.contracts.tools}))"
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True,
                            timeout=30,
                            env=dict(os.environ, SUTRA_PLUGIN=str(plugin_file)))
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    assert seen["wrapper"] == "function"
    assert "Sutra" not in seen["keys"]
    assert seen["id"] == "sutra"
    assert seen["register"] == "function"
    assert seen["tools"] is True


def test_a_rewrite_keeps_a_copy_of_the_config_it_replaced(paths, capsys):
    """A whole-file rewrite is the only way to add an entry, so the text goes first."""
    _, config_dir, config = paths
    before = config.read_text(encoding="utf-8")

    backup = openclaw._save_config(config, {"theme": "light"})

    assert backup is not None
    assert backup.name == f"{config.name}.sutra-bak"
    assert backup.read_text(encoding="utf-8") == before
    assert json.loads(config.read_text(encoding="utf-8")) == {"theme": "light"}


def test_install_reports_the_config_it_replaced(paths, capsys):
    _, config_dir, _config = paths

    assert openclaw.install(python=sys.executable, home=paths[0] / "home",
                            config_path=config_dir) == 0

    out = capsys.readouterr().out
    assert ".sutra-bak" in out


def test_a_plugin_that_cannot_be_removed_stays_named_in_the_config(paths, monkeypatch, capsys):
    """The entries are the only thing that finds the file, so they go back."""
    root, config_dir, config = paths
    assert openclaw.install(python=sys.executable, home=root / "home",
                            config_path=config_dir) == 0
    plugin = openclaw.plugin_path(config_dir)
    capsys.readouterr()

    def refuse_to_delete(_self, *_args, **_kwargs):
        raise OSError("read-only file system")
    with monkeypatch.context() as patch:
        patch.setattr(pathlib.Path, "unlink", refuse_to_delete)
        assert openclaw.uninstall(config_path=config_dir) == 1

    assert plugin.is_file()
    # Read the value back, not the raw text: a Windows path is stored with
    # escaped backslashes, so the text never contains the path itself.
    named = json.loads(config.read_text(encoding="utf-8"))["plugins"]["load"]["paths"]
    assert str(plugin) in named
    assert "still named in the config" in capsys.readouterr().err


def test_doctor_finishes_when_the_protocol_probe_cannot_start(paths, monkeypatch, capsys):
    """A probe that cannot set itself up still leaves a complete report."""
    root, config_dir, _config = paths
    # `sys.executable`, not a POSIX path: doctor stops before the probe when the
    # embedded interpreter does not resolve, which is what every non-Linux
    # runner would see with a hard-coded `/usr/bin/python3`.
    assert openclaw.install(python=sys.executable, home=root / "home",
                            config_path=config_dir) == 0

    def cannot_start(*_args, **_kwargs):
        raise OSError("no temporary directory")
    monkeypatch.setattr(openclaw, "_doctor_runtime", cannot_start)

    report = openclaw.doctor(path=str(openclaw.plugin_path(config_dir)),
                             node="/nonexistent/node")

    runtime = next(item for item in report["checks"] if item["name"] == "runtime")
    assert runtime["status"] == "fail"
    assert "no temporary directory" in runtime["detail"]
    assert report["status"] == "fail"


def test_doctor_agrees_with_an_install_whose_tree_and_file_differ(tmp_path, monkeypatch):
    """Both sides read one resolver, so a split tree and file still agree."""
    tree = tmp_path / "tree"
    named = tmp_path / "host" / "openclaw.json"
    named.parent.mkdir()
    named.write_text('{"plugins": {"load": {"paths": []}, "allow": []}}\n', encoding="utf-8")
    monkeypatch.setenv("SUTRA_OPENCLAW_CONFIG", str(tree))
    monkeypatch.setenv("OPENCLAW_CONFIG_PATH", str(named))

    assert openclaw.install(python="/usr/bin/python3", home=tmp_path / "home") == 0

    assert json.loads(named.read_text(encoding="utf-8"))["plugins"]["load"]["paths"] == [
        str(tree / "plugins" / "sutra.js")]
    report = openclaw.doctor(node="/nonexistent/node")
    config_check = next(item for item in report["checks"] if item["name"] == "config")
    assert config_check["status"] == "pass"
