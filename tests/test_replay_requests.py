"""``bench/replay_requests.py`` replays recorded bodies without leaking them.

The benchmark reads request bodies a host saved before submission, so its two
claims need to be checked: it must only act on bodies that carry a conversation,
and it must report a changed message only when the stored copy restores it.
These tests build their own recordings; no captured traffic is required.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bench" / "replay_requests.py"


@pytest.fixture(scope="module")
def replay():
    spec = importlib.util.spec_from_file_location("replay_requests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def body(size: int) -> str:
    """A tool payload without a SUTRA marker, so retention has to store it."""
    filler = "line of command output the model has already read\n" * 64
    text = filler
    while len(text) < size:
        text += filler
    return text[:size]


def recording(*, results: int = 2, size: int = 12_000) -> dict:
    messages = [{"role": "system", "content": "You are a coding agent."},
                {"role": "user", "content": "Why did the suite fail?"}]
    for index in range(results):
        messages.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": f"c{index}", "type": "function"}]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}",
                         "content": body(size)})
        messages.append({"role": "assistant", "content": f"Result {index} read."})
        messages.append({"role": "user", "content": f"Continue after {index}."})
    return {"request": {"body": {"model": "m", "messages": messages}}}


def write(directory: Path, name: str, document: dict) -> Path:
    path = directory / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_recorded_result_is_shortened_and_restorable(replay, tmp_path, capsys):
    write(tmp_path, "kept.json", recording())
    assert replay.main(["--source", str(tmp_path), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["rows"]

    beast = [row for row in rows if row["mode"] == "beast"]
    assert beast, "every mode should report a row"
    assert beast[0]["saved"] > 0
    assert beast[0]["elided"] > 0
    for row in rows:
        assert row["recoverable"] is True
        assert row["untouched"] is True
        assert row["after"] <= row["before"]


def test_a_body_without_a_conversation_is_skipped(replay, tmp_path, capsys):
    write(tmp_path, "empty.json", {"request": {"body": {"model": "m"}}})
    write(tmp_path, "torn.json", {"error": {"message": "429"}})
    assert replay.main(["--source", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == []


def test_a_single_file_is_accepted(replay, tmp_path, capsys):
    path = write(tmp_path, "one.json", recording())
    assert replay.main(["--source", str(path), "--modes", "balanced", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["rows"]
    assert {row["mode"] for row in rows} == {"balanced"}
    assert rows[0]["source"] == "one.json"


def test_the_report_prints_no_message_text(replay, tmp_path, capsys):
    write(tmp_path, "kept.json", recording())
    assert replay.main(["--source", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "line of command output" not in printed
    assert "Why did the suite fail?" not in printed
    assert "saved=" in printed
