"""The installer must not leave a second plugin directory behind.

``hermes.py`` moves the current plugin aside before writing the new one. A backup beside
it (``<home>/plugins/sutra.bak-<stamp>``) is a plugin directory whose manifest carries the
same ``name: sutra``, so the host's next discovery can load the stale copy instead of the
one just installed — the installed code then looks current while the old code runs.
"""

from conftest import plugin_module

hermes = plugin_module("hermes")


def plugin_at(home):
    path = home / "plugins" / hermes.PLUGIN_NAME
    path.mkdir(parents=True)
    (path / "plugin.yaml").write_text("name: sutra\n", encoding="utf-8")
    (path / "marker.txt").write_text("old copy\n", encoding="utf-8")
    return path


def test_a_backup_is_kept_outside_the_plugin_scan(tmp_path):
    home = tmp_path / "hermes"
    path = plugin_at(home)

    backup = hermes._backup(path)

    assert not path.exists()
    assert backup.parent == home / "plugin-backups"
    assert (backup / "marker.txt").read_text(encoding="utf-8") == "old copy\n"
    assert list((home / "plugins").iterdir()) == []


def test_the_backup_restores_the_plugin_on_its_own_path(tmp_path):
    home = tmp_path / "hermes"
    path = plugin_at(home)
    backup = hermes._backup(path)
    replacement = path.parent / ".tmp"
    replacement.mkdir()
    (replacement / "plugin.yaml").write_text("name: sutra\nversion: new\n", encoding="utf-8")
    replacement.rename(path)

    hermes._restore(path, backup)

    assert (path / "marker.txt").read_text(encoding="utf-8") == "old copy\n"
    assert not backup.exists()
    assert [item.name for item in (home / "plugins").iterdir()] == ["sutra"]
