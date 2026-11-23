"""The DeepSeek Harness target writes one bundle and lets the harness add it.

A harness capability is a package directory, not a settings key, so the adapter's job
is narrow: copy the bundle beside the harness home, fill in the interpreter that can
import sutra, and ask the harness CLI to attach it to the profile. These tests pin
that the copy happens, that a dry run writes nothing, and that the doctor tells a
missing bundle from an attached one.

The CLI is injected as an argv prefix so the tests run the same way on Windows, where
there is no shell script to put on PATH.
"""

import json
import pathlib
import sys

import pytest

from conftest import plugin_module

dsh = plugin_module("dsh")


@pytest.fixture()
def fake_cli(tmp_path):
    """An argv prefix that records what the adapter asked the harness to do."""
    record = tmp_path / "cli-calls.txt"
    recorder = tmp_path / "recorder.py"
    recorder.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(record)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return [sys.executable, str(recorder)], record


def test_the_bundle_is_written_and_attached_to_the_profile(tmp_path, fake_cli):
    home = tmp_path / "dsh"
    cli, record = fake_cli

    status = dsh.install(python=sys.executable, home_dir=home, name="desktop", cli=cli)

    assert status == 0
    target = home / "sutra-host"
    assert sorted(path.name for path in target.iterdir()) == sorted(dsh.BUNDLE_FILES)
    patch = (target / "cordis.patch.yml").read_text(encoding="utf-8")
    assert f"python: {sys.executable}" in patch
    assert "id: dsh-sutra-host" in patch
    assert record.read_text(encoding="utf-8") == f"plugin --profile desktop add {target}"


def test_a_dry_run_writes_nothing(tmp_path, fake_cli):
    home = tmp_path / "dsh"
    cli, record = fake_cli

    status = dsh.install(python=sys.executable, home_dir=home, name="desktop", dry_run=True, cli=cli)

    assert status == 0
    assert not (home / "sutra-host").exists()
    assert not record.exists()


def test_the_python_path_travels_into_the_bundle_config(tmp_path, fake_cli):
    home = tmp_path / "dsh"
    cli, _record = fake_cli

    dsh.install(python=sys.executable, python_path="/opt/sutra/lib", home_dir=home, cli=cli)

    patch = (home / "sutra-host" / "cordis.patch.yml").read_text(encoding="utf-8")
    assert "pythonPath: /opt/sutra/lib" in patch


def test_configured_reads_the_profile_manifest(tmp_path):
    home = tmp_path / "dsh"
    profile = home / "profiles" / "desktop"
    profile.mkdir(parents=True)
    (profile / "package.json").write_text(
        json.dumps({"dependencies": {"dsh-sutra-host": "link:../../sutra-host"}}),
        encoding="utf-8",
    )

    assert dsh.configured(home) is True
    assert dsh.configured(home, name="other") is False


def test_doctor_separates_an_unattached_copy_from_a_missing_one(tmp_path):
    home = tmp_path / "dsh"

    empty = dsh.doctor(home_dir=home)
    assert empty["files"] == []
    assert empty["status"] == "fail"

    target = home / "sutra-host"
    target.mkdir(parents=True)
    for filename in dsh.BUNDLE_FILES:
        (target / filename).write_text("placeholder\n", encoding="utf-8")
    copied = dsh.doctor(home_dir=home)
    assert copied["copy"] == "current"
    assert copied["attached"] is False
    assert copied["status"] == "partial"


def test_doctor_reports_what_the_stage_has_actually_saved(tmp_path):
    """A wired bundle is not a working stage.

    The preset defect produced a healthy doctor line while no model tool call
    ever reached the seam, so the doctor reports the seam's own log instead of
    implying that wiring means saving.
    """

    home = tmp_path / "dsh"
    target = home / "sutra-host"
    target.mkdir(parents=True)
    for filename in dsh.BUNDLE_FILES:
        (target / filename).write_text("placeholder\n", encoding="utf-8")

    empty = dsh.doctor(home_dir=home)
    assert empty["stage"] == "never"
    assert empty["rewrites"] == 0
    assert any("no tool result has been trimmed" in problem for problem in empty["problems"])

    log = home / "sutra" / "stats.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps({"at": "2026-11-23T09:39:30+0900", "saved_bytes": 0}) + "\n"
        + json.dumps({"at": "2026-11-23T09:40:00+0900", "saved_bytes": 45725}) + "\n"
        + "not json\n",
        encoding="utf-8",
    )

    saved = dsh.doctor(home_dir=home)
    assert saved["stage"] == "saved"
    assert saved["rewrites"] == 1
    assert saved["saved_bytes"] == 45725
    assert saved["last_saved"] == "2026-11-23T09:40:00+0900"


def test_the_bundle_declares_its_dependency_in_the_form_cordis_reads():
    """Cordis takes `inject` as a map of service names.

    An object such as `{ required: [...], optional: [...] }` is read as two
    services literally named "required" and "optional", so the plugin waits for
    them forever and its fiber never leaves the pending phase. The bundle must
    therefore declare only the services it needs, by name.
    """

    source = dsh.template("index.js").read_text(encoding="utf-8")

    assert "export const inject = ['tools']" in source
    assert "export const inject = {" not in source


def test_the_default_seam_path_points_at_the_tree_that_installed_it(tmp_path, fake_cli):
    """A source checkout is not importable from the interpreter's own path.

    Without PYTHONPATH the seam runs whatever `sutra` happens to be installed in
    site-packages — often an older release that answers "unknown command" for
    `process-result`, which the plugin reads as "nothing to do". The default has
    to name the directory this package was imported from.
    """

    home = tmp_path / "dsh"
    cli, _ = fake_cli

    assert dsh.install(python=sys.executable, home_dir=home, cli=cli) == 0

    patch = (home / "sutra-host" / "cordis.patch.yml").read_text(encoding="utf-8")
    assert f"pythonPath: {pathlib.Path(dsh.__file__).resolve().parent.parent}" in patch


def test_the_bundle_reads_the_result_not_the_decision(tmp_path, fake_cli):
    """An accepted post-execute decision carries no content of its own.

    The waterfall hands the listener `(exec, result)` and a default
    `{kind: "accept"}` decision; the tool's text is on `result.content`. Reading
    `decision.content` therefore found nothing and every live tool call passed
    through untrimmed.
    """

    source = dsh.template("index.js").read_text(encoding="utf-8")

    assert "result?.content" in source
    assert "decision.content" not in source


def _profile_with_preset(tmp_path, preset="preset-test"):
    patch = tmp_path / "dsh" / "profiles" / "desktop" / "cordis.patch.yml"
    patch.parent.mkdir(parents=True)
    patch.write_text(
        "# Hand-written profile.\n"
        "- insert:\n"
        "    - id: agent-preset-registry\n"
        "      config:\n"
        f"        default: {preset}\n"
        "    - id: preset-a\n"
        "      name: '@deepseek-ai/dsh-agent-preset'\n"
        "      plugins:\n"
        "        - id: plugin-one\n"
        "          name: plugin-one\n",
        encoding="utf-8",
    )
    return patch


def test_the_preset_entry_is_inserted_once(tmp_path, fake_cli):
    """A profile-level entry never sees a model tool call; the preset does.

    `tools/post-execute` is scoped to the calling agent's own scope chain, so the
    bundle has to be composed *inside* the preset that builds the agent.
    """

    home = tmp_path / "dsh"
    cli, _ = fake_cli
    patch = _profile_with_preset(tmp_path)

    assert dsh.install(python=sys.executable, home_dir=home, cli=cli) == 0

    text = patch.read_text(encoding="utf-8")
    assert text.count(f"- id: {dsh.BUNDLE_NAME}") == 1
    entry = [line for line in text.splitlines() if line.strip() == f"- id: {dsh.BUNDLE_NAME}"]
    plugins = [line for line in text.splitlines() if line.strip() == "plugins:"][0]
    assert len(entry[0]) - len(entry[0].lstrip()) > len(plugins) - len(plugins.lstrip()), (
        "the entry must nest under plugins:"
    )

    assert dsh.wire_preset(sys.executable, "/tmp/lib", home_dir=home) == "present"
    assert patch.read_text(encoding="utf-8").count(f"- id: {dsh.BUNDLE_NAME}") == 1


def test_uninstall_takes_the_preset_entry_back(tmp_path, fake_cli):
    """Leaving the entry behind would load a bundle whose directory is gone."""

    home = tmp_path / "dsh"
    cli, _ = fake_cli
    patch = _profile_with_preset(tmp_path)

    assert dsh.install(python=sys.executable, home_dir=home, cli=cli) == 0
    assert dsh.uninstall(home_dir=home, cli=cli) == 0

    assert dsh.BUNDLE_NAME not in patch.read_text(encoding="utf-8")
    assert "plugin-one" in patch.read_text(encoding="utf-8")


def test_a_profile_without_a_preset_is_reported_not_guessed(tmp_path, fake_cli):
    """An empty profile has nothing to compose into; the install says so."""

    home = tmp_path / "dsh"
    cli, _ = fake_cli
    patch = home / "profiles" / "desktop" / "cordis.patch.yml"
    patch.parent.mkdir(parents=True)
    patch.write_text("- insert: []\n", encoding="utf-8")

    assert dsh.install(python=sys.executable, home_dir=home, cli=cli) == 0

    assert dsh.wire_preset(sys.executable, "/tmp/lib", home_dir=home) == "no-preset"
    assert patch.read_text(encoding="utf-8") == "- insert: []\n"


def test_the_doctor_line_names_what_the_stage_has_saved():
    """`wired` and `working` are different claims, and the line says which one holds."""

    saving = {
        "status": "pass",
        "profile": "desktop",
        "copy": "current",
        "attached": True,
        "preset": "wired",
        "stage": "saved",
        "rewrites": 3,
        "saved_bytes": 3_145_728,
        "last_saved": "2026-11-23T09:40:00+0900",
        "problems": [],
    }
    line = dsh.format_doctor(saving)
    assert line.startswith("dsh: status=pass profile=desktop copy=current")
    assert "preset=wired" in line
    assert "stage=saved rewrites=3 saved=3.0 MB last=2026-11-23T09:40:00+0900" in line

    idle = dsh.format_doctor({**saving, "stage": "never", "rewrites": 0})
    assert "stage=never" in idle
    assert "saved=" not in idle.split("·")[0]

    broken = dsh.format_doctor({"error": "boom"})
    assert broken == "dsh: error: boom"
