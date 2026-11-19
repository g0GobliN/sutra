"""The release gate must read the changelog the way the changelog is written.

`scripts/check_version.py` refuses a tag whose version has no `## <version>`
heading, and `--notes` prints that section for the release run. Both behaviors
depend on the heading regex, and the changelog has used several heading styles
(`## 0.6.3 - 2026-11-14`, `## 0.4.2 (2026-11-01)`, `## v0.2.0`). These tests
pin the accepted styles and the failure messages without touching the real
files.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_version.py"

CHANGELOG = """# Changelog

## Unreleased

### Added

- A thing.

## 0.6.3 - 2026-11-14

### Fixed

- The compressed output no longer loses the traceback end.

## v0.2.0

- Old style.

## 0.4.2 (2026-11-01)

- Parenthesised date.
"""


def _module() -> object:
    spec = importlib.util.spec_from_file_location("check_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.mark.parametrize("version", ["0.6.3", "0.2.0", "0.4.2"])
def test_every_heading_style_in_the_changelog_is_recognised(version: str) -> None:
    assert _module().changelog_heading(CHANGELOG, version) is not None


def test_a_version_without_a_heading_is_rejected() -> None:
    assert _module().changelog_heading(CHANGELOG, "9.9.9") is None


def test_the_section_stops_at_the_next_heading() -> None:
    section = _module().changelog_section(CHANGELOG, "0.6.3")
    assert "loses the traceback end" in section
    assert "Old style" not in section


def test_the_section_starts_below_the_heading_line() -> None:
    section = _module().changelog_section(CHANGELOG, "0.6.3")
    assert section.startswith("### Fixed")
    assert "2026-11-14" not in section


def test_the_section_ends_at_the_last_heading() -> None:
    assert _module().changelog_section(CHANGELOG, "0.4.2") == "- Parenthesised date."


def _release_tree(tmp_path: pathlib.Path, version: str, changelog: str) -> object:
    (tmp_path / "plugin.yaml").write_text(f'version: "{version}"\n', encoding="utf-8")
    (tmp_path / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    loaded = _module()
    loaded.ROOT = tmp_path
    return loaded


def test_a_consistent_release_passes_and_prints_its_notes(
        tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = _release_tree(tmp_path, "1.2.3", CHANGELOG.replace("0.6.3", "1.2.3"))
    assert script.main(["v1.2.3", "--notes"]) == 0
    assert "loses the traceback end" in capsys.readouterr().out


def test_a_missing_changelog_heading_names_the_problem(
        tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = _release_tree(tmp_path, "1.2.3", CHANGELOG)
    assert script.main(["v1.2.3"]) == 1
    assert "CHANGELOG.md has no '## 1.2.3' heading" in capsys.readouterr().err


def test_a_version_mismatch_names_both_files(
        tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = _release_tree(tmp_path, "1.2.3", CHANGELOG.replace("0.6.3", "1.2.3"))
    (tmp_path / "plugin.yaml").write_text('version: "1.2.2"\n', encoding="utf-8")
    assert script.main(["v1.2.3"]) == 1
    assert "plugin.yaml says 1.2.2" in capsys.readouterr().err
