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
