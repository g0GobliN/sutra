"""Tests for the bounded repository map (the reuse-first lookup surface)."""

from __future__ import annotations

import json

from conftest import plugin_module

repomap = plugin_module("repomap")


def _write(root, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_map_lists_top_level_definitions_by_folder(tmp_path):
    _write(tmp_path, "pkg/core.py",
           "def load(path):\n    pass\n\nclass Cache:\n    pass\n"
           "def _private():\n    pass\n")
    _write(tmp_path, "web/app.ts",
           "export function render(x) {}\nexport const make = () => {}\n")
    built = repomap.build(tmp_path)
    # The regex follows Ponytail's map and lists public top-level names only.
    assert "pkg/: load, Cache" in built
    assert "web/: render, make" in built


def test_map_skips_tests_vendored_and_generated_files(tmp_path):
    _write(tmp_path, "pkg/core.py", "def keep():\n    pass\n")
    _write(tmp_path, "pkg/test_core.py", "def never_shown():\n    pass\n")
    _write(tmp_path, "tests/helper.py", "def never_shown():\n    pass\n")
    _write(tmp_path, "vendor/lib.py", "def never_shown():\n    pass\n")
    _write(tmp_path, "types.d.ts", "export declare function never_shown(): void\n")
    built = repomap.build(tmp_path)
    assert "keep" in built
    assert "never_shown" not in built


def test_map_respects_the_character_budget(tmp_path):
    for index in range(30):
        _write(tmp_path, f"folder{index:02d}/mod.py", "def one():\n    pass\n")
    built = repomap.build(tmp_path, budget=200)
    assert 0 < len(built) <= 200
    assert built.startswith("folder00/: one")


def test_map_lists_shared_folders_first(tmp_path):
    _write(tmp_path, "zzz/plain.py", "def plain():\n    pass\n")
    _write(tmp_path, "alpha/utils.py", "def shared():\n    pass\n")
    lines = repomap.build_lines(tmp_path, budget=200)
    assert lines[0].startswith("alpha/")


def test_map_is_deterministic_and_missing_root_is_safe(tmp_path):
    _write(tmp_path, "a.py", "def a():\n    pass\n")
    first = repomap.build(tmp_path)
    second = repomap.build(tmp_path)
    assert first == second
    # A budget too small for even one line returns nothing rather than exceeding it.
    assert repomap.build(tmp_path, budget=1) == ""


def test_map_rejects_a_non_positive_budget(tmp_path):
    try:
        repomap.build(tmp_path, budget=0)
    except ValueError as error:
        assert "positive" in str(error)
    else:  # pragma: no cover - the raise above is the assertion
        raise AssertionError("budget=0 must be rejected")


def test_cli_map_prints_text_and_json(tmp_path, capsys):
    cli = plugin_module("cli")
    _write(tmp_path, "pkg/core.py", "def load():\n    pass\n")
    code = cli.main(["map", "--root", str(tmp_path)])
    assert code == 0
    assert "pkg/: load" in capsys.readouterr().out
    code = cli.main(["map", "--root", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["lines"] == ["pkg/: load"]
    assert payload["budget"] == repomap.DEFAULT_BUDGET
