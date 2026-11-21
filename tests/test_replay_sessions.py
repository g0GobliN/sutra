"""Tests for `bench/replay_sessions.py`.

The benchmark reads a host session store, so the tests build the smallest
database that has the same shape: one table, the columns the queries touch, and
messages large enough to qualify for the request stage.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bench" / "replay_sessions.py"

FILLER = "traceback line that repeats until the result is worth shortening\n"


@pytest.fixture(scope="module")
def sessions():
    spec = importlib.util.spec_from_file_location("replay_sessions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def filler(size: int) -> str:
    return (FILLER * (size // len(FILLER) + 1))[:size]


def build(path, *, results=2, size=12_000, tail=4):
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            compacted INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    rows = [("system", "You are a coding agent.")]
    rows.append(("user", "the build fails, find out why"))
    for index in range(results):
        rows.append(("tool", filler(size)))
        rows.append(("assistant", f"result {index} looks like a build failure"))
    for index in range(tail):
        rows.append(("assistant" if index % 2 else "user", f"tail message {index}"))
    connection.executemany(
        "INSERT INTO messages (session_id, role, content) VALUES ('s1', ?, ?)", rows)
    connection.commit()
    connection.close()


def run(module, capsys, argv):
    code = module.main(argv)
    return code, capsys.readouterr().out


def test_a_real_session_is_shortened_and_recoverable(sessions, tmp_path, capsys):
    database = tmp_path / "state.db"
    build(database)
    code, output = run(sessions, capsys, ["--db", str(database), "--limit", "5", "--min-messages", "3"])
    assert code == 0
    assert "balanced" in output
    assert "saved" in output
    assert "recovered=yes" in output and "untouched=yes" in output
    assert "traceback line" not in output


def test_a_short_session_is_skipped(sessions, tmp_path, capsys):
    database = tmp_path / "state.db"
    build(database, results=0, tail=1)
    code, output = run(sessions, capsys, ["--db", str(database), "--min-messages", "5"])
    assert code == 0
    assert "no session with enough" in output
    assert "balanced" not in output


def test_the_json_report_carries_rows_and_summary(sessions, tmp_path, capsys):
    database = tmp_path / "state.db"
    build(database)
    code, output = run(sessions, capsys, ["--db", str(database), "--min-messages", "3", "--json"])
    assert code == 0
    report = json.loads(output)
    assert set(report) == {"tool_results", "summary", "rows"}
    assert report["summary"][0]["mode"] == "balanced"
    assert report["rows"][0]["session"] == "s1"
    assert report["rows"][0]["recovered"] is True
    assert report["tool_results"]["over_8192"] == 2


def test_a_missing_database_is_an_error(sessions, tmp_path, capsys):
    code = sessions.main(["--db", str(tmp_path / "absent.db")])
    assert code == 1
    assert "no database" in capsys.readouterr().err


def test_only_the_configured_modes_run(sessions, tmp_path, capsys):
    database = tmp_path / "state.db"
    build(database)
    code, output = run(sessions, capsys, ["--db", str(database), "--min-messages", "3",
                                "--modes", "balanced"])
    assert code == 0
    assert "balanced" in output
    assert "aggressive" not in output
    assert "beast" not in output


@pytest.mark.parametrize("mode", ["balanced", "aggressive", "beast"])
def test_every_mode_keeps_the_untouched_identity(sessions, tmp_path, capsys, mode):
    database = tmp_path / "state.db"
    build(database)
    code, output = run(sessions, capsys, ["--db", str(database), "--min-messages", "3",
                                "--modes", mode])
    assert code == 0
    assert "untouched=yes" in output
