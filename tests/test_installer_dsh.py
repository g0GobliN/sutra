"""The DeepSeek Harness target writes one bundle and lets the harness add it.

A harness capability is a package directory, not a settings key, so the adapter's job
is narrow: copy the bundle beside the harness home, fill in the interpreter that can
import sutra, and ask the harness CLI to attach it to the profile. These tests pin
that the copy happens, that a dry run writes nothing, and that the doctor tells a
missing bundle from an attached one.
"""

import json

import pytest

from conftest import plugin_module

dsh = plugin_module("dsh")


@pytest.fixture()
def fake_cli(tmp_path, monkeypatch):
    """A `dsh` on PATH that records its arguments instead of touching a profile."""
    record = tmp_path / "cli-calls.txt"
    binary = tmp_path / "bin" / "dsh"
    binary.parent.mkdir()
    binary.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {record}\n',
        encoding="utf-8",
    )
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary.parent}:/usr/bin:/bin")
    return record


def test_the_bundle_is_written_and_attached_to_the_profile(tmp_path, fake_cli):
    home = tmp_path / "dsh"

    status = dsh.install(python="/usr/bin/python3", home_dir=home, name="desktop")

    assert status == 0
    target = home / "sutra-host"
    assert sorted(path.name for path in target.iterdir()) == sorted(dsh.BUNDLE_FILES)
    patch = (target / "cordis.patch.yml").read_text(encoding="utf-8")
    assert "python: /usr/bin/python3" in patch
    assert "id: dsh-sutra-host" in patch
    calls = fake_cli.read_text(encoding="utf-8").splitlines()
    assert calls == [f"plugin --profile desktop add {target}"]


def test_a_dry_run_writes_nothing(tmp_path, fake_cli):
    home = tmp_path / "dsh"

    status = dsh.install(python="/usr/bin/python3", home_dir=home, name="desktop", dry_run=True)

    assert status == 0
    assert not (home / "sutra-host").exists()
    assert not fake_cli.exists()


def test_the_python_path_travels_into_the_bundle_config(tmp_path, fake_cli):
    home = tmp_path / "dsh"

    dsh.install(python="/usr/bin/python3", python_path="/opt/sutra/lib", home_dir=home)

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
