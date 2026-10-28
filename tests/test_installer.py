"""The installer edits a client's config without owning it.

OpenCode's config may be JSONC: a real one carries comments and other servers.
These tests pin the two properties that matter: the `sutra` entry is added or
removed, and every other byte of the file survives.
"""

import json
import pathlib
import types

import pytest

from conftest import plugin_module

installer = plugin_module("installer")
cli = plugin_module("cli")


JSON_CONFIG = """{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "caveman": {
      "type": "local",
      "command": ["/usr/local/bin/caveman-mcp"],
      "enabled": true
    }
  }
}
"""

JSONC_CONFIG = """{
  // OpenCode reads JSONC; this comment must survive an install.
  "$schema": "https://opencode.ai/config.json",
  "provider": {"openai": {"options": {"baseURL": "http://127.0.0.1:8787/v1"}}},
  "mcp": {
    /* another server, with a comment of its own */
    "github": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
      "enabled": true,
      "environment": {"GITHUB_PERSONAL_ACCESS_TOKEN": "{env:GITHUB_TOKEN}"}
    },
    "filesystem": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/home"],
      "enabled": true
    }
  }
}
"""


def test_install_adds_one_entry_and_keeps_the_rest_parseable(tmp_path):
    path = tmp_path / "opencode.json"
    path.write_text(JSON_CONFIG, encoding="utf-8")
    updated, changed = installer.with_entry(JSON_CONFIG, "/usr/bin/python3")
    assert changed
    path.write_text(updated, encoding="utf-8")

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["mcp"]["sutra"] == {
        "type": "local",
        "command": ["/usr/bin/python3", "-m", "sutra.mcp"],
        "enabled": True,
    }
    assert document["mcp"]["caveman"]["command"] == ["/usr/local/bin/caveman-mcp"]
    assert document["$schema"] == "https://opencode.ai/config.json"


def test_install_preserves_jsonc_comments_and_neighbours():
    updated, changed = installer.with_entry(JSONC_CONFIG, "/usr/bin/python3")
    assert changed
    assert "// OpenCode reads JSONC; this comment must survive an install." in updated
    assert "/* another server, with a comment of its own */" in updated
    assert '"GITHUB_PERSONAL_ACCESS_TOKEN": "{env:GITHUB_TOKEN}"' in updated
    assert '"sutra": {' in updated
    # the new entry is inside "mcp", not appended after the document
    assert updated.index('"sutra"') < updated.rindex("}")


def test_installing_twice_changes_nothing():
    once, changed = installer.with_entry(JSON_CONFIG, "/usr/bin/python3")
    assert changed
    twice, changed_again = installer.with_entry(once, "/usr/bin/python3")
    assert not changed_again
    assert twice == once


def test_install_creates_the_mcp_block_when_absent():
    source = '{\n  "theme": "dark"\n}\n'
    updated, changed = installer.with_entry(source, "/usr/bin/python3")
    assert changed
    document = json.loads(updated)
    assert document["theme"] == "dark"
    assert document["mcp"]["sutra"]["command"][-1] == "sutra.mcp"


def test_a_different_existing_entry_is_refused_not_overwritten():
    source = json.dumps({"mcp": {"sutra": {"type": "local",
                                          "command": ["other"], "enabled": True}}})
    with pytest.raises(ValueError, match="uninstall"):
        installer.with_entry(source, "/usr/bin/python3")


def test_uninstall_removes_only_sutra_and_leaves_valid_json():
    installed, _ = installer.with_entry(JSON_CONFIG, "/usr/bin/python3")
    updated, changed = installer.without_entry(installed)
    assert changed
    document = json.loads(updated)
    assert "sutra" not in document["mcp"]
    assert "caveman" in document["mcp"]


def test_uninstall_keeps_a_jsonc_comment_intact():
    installed, _ = installer.with_entry(JSONC_CONFIG, "/usr/bin/python3")
    updated, changed = installer.without_entry(installed)
    assert changed
    assert "// OpenCode reads JSONC; this comment must survive an install." in updated
    assert '"sutra"' not in updated
    assert '"github"' in updated and '"filesystem"' in updated


def test_uninstall_when_absent_is_a_no_op():
    _, changed = installer.without_entry(JSON_CONFIG)
    assert not changed


def test_a_dry_run_writes_nothing(tmp_path, capsys):
    path = tmp_path / "opencode.jsonc"
    path.write_text(JSONC_CONFIG, encoding="utf-8")
    before = path.read_bytes()
    code = cli.main(["install", "--config", str(path), "--dry-run"])
    assert code == 0
    assert path.read_bytes() == before
    assert "dry run" in capsys.readouterr().out
    assert not list(tmp_path.glob("*.bak-*"))


def test_a_real_install_writes_a_backup_and_is_idempotent(tmp_path, capsys):
    path = tmp_path / "opencode.json"
    path.write_text(JSON_CONFIG, encoding="utf-8")
    assert cli.main(["install", "--config", str(path)]) == 0
    assert list(tmp_path.glob("opencode.json.bak-*"))
    assert cli.main(["install", "--config", str(path)]) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert len(list(tmp_path.glob("opencode.json.bak-*"))) == 1


COMMENTED_TAIL_CONFIG = """{
  "mcp": {
    "other": { "type": "local", "command": ["x"] } // keep this note
  }
}
"""

TRAILING_COMMA_CONFIG = """{
  "mcp": {
    "other": { "type": "local", "command": ["x"] },
  }
}
"""

PLAIN_TAIL_CONFIG = """{
  "mcp": {
    "other": { "type": "local", "command": ["x"] }
  }
}
"""


def test_a_comment_after_the_last_member_stays_with_that_member():
    updated, changed = installer.with_entry(COMMENTED_TAIL_CONFIG, "/usr/bin/python3")
    assert changed
    # The note belongs to `other`; it must not migrate onto the new entry's brace.
    assert ('"other": { "type": "local", "command": ["x"] }, // keep this note'
            in updated)
    assert "} // keep this note" not in updated
    restored, changed = installer.without_entry(updated)
    assert changed
    assert restored == COMMENTED_TAIL_CONFIG


def test_a_trailing_comma_is_preserved_only_where_it_existed():
    # The comma shape was JSONC before the install and stays JSONC after it:
    # the comma is carried past the new entry, not dropped and not duplicated.
    updated, changed = installer.with_entry(TRAILING_COMMA_CONFIG, "/usr/bin/python3")
    assert changed
    with pytest.raises(json.JSONDecodeError):
        json.loads(updated)
    restored, changed = installer.without_entry(updated)
    assert changed
    assert restored == TRAILING_COMMA_CONFIG

    # The plain shape was strict JSON before the install and stays strict after;
    # no comma is invented, and uninstall restores it byte for byte too.
    plain, changed = installer.with_entry(PLAIN_TAIL_CONFIG, "/usr/bin/python3")
    assert changed
    assert set(json.loads(plain)["mcp"]) == {"other", "sutra"}
    restored_plain, changed = installer.without_entry(plain)
    assert changed
    assert restored_plain == PLAIN_TAIL_CONFIG


def test_two_writes_in_one_second_do_not_overwrite_the_earlier_backup(tmp_path, monkeypatch):
    path = tmp_path / "opencode.json"
    path.write_text(JSON_CONFIG, encoding="utf-8")
    frozen = types.SimpleNamespace(strftime=lambda _format: "20260101T000000")
    monkeypatch.setattr(installer, "time", frozen)
    assert cli.main(["install", "--config", str(path)]) == 0
    assert cli.main(["uninstall", "--config", str(path)]) == 0
    backups = sorted(tmp_path.glob("opencode.json.bak-*"))
    assert len(backups) == 2
    assert backups[0].read_text(encoding="utf-8") == JSON_CONFIG
    assert '"sutra"' in backups[1].read_text(encoding="utf-8")


def test_config_discovery_prefers_jsonc_and_honours_the_override(tmp_path, monkeypatch):
    base = tmp_path / "opencode"
    base.mkdir()
    (base / "opencode.json").write_text("{}", encoding="utf-8")
    (base / "opencode.jsonc").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert installer.opencode_config().name == "opencode.jsonc"
    assert installer.opencode_config(str(tmp_path / "given.json")).name == "given.json"


def test_the_package_declares_its_console_entry_points():
    import tomllib
    root = pathlib.Path(__file__).resolve().parent.parent
    document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert document["project"]["scripts"] == {
        "sutra": "sutra.cli:main",
        "sutra-mcp": "sutra.mcp:main",
    }
    # PyPI's `sutra` is an unrelated 0.2.2 package (KWR-Water/sutra), so the
    # distribution cannot use that name; the import package and commands stay
    # `sutra`.
    assert document["project"]["name"] == "sutra-agent"
