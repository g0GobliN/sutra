"""The request stage as OpenCode needs it: one batch of results, JSON in and out.

OpenCode 1.19.6 exposes ``experimental.chat.messages.transform``, which hands a
plugin the assembled conversation before the provider sees it. Results do not
arrive as one request body there; they sit in ``part.state.output``. The plugin
therefore sends the large result strings to ``sutra shorten-results`` in a
single subprocess call and writes back only the strings it gets.

These tests pin that contract at the command seam: same policy, same store,
same marker, and a text that changes only when its original was stored.
"""

from __future__ import annotations

import io
import json
import re

from conftest import ROOT, plugin_module

cli = plugin_module("cli")
store_module = plugin_module("store")

NOTICE = "[sutra: request copy"
ID = re.compile(r'id="(su_[0-9a-f]{32})"')
TRACEBACK = (
    'Traceback (most recent call last):\n'
    '  File "src/app/main.py", line 7, in run\n'
    '    raise RuntimeError("boom")\n'
    'RuntimeError: boom\n'
)


def body(size: int = 40_000) -> str:
    return TRACEBACK * (size // len(TRACEBACK))


def call(monkeypatch, capsys, payload, **options):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["shorten-results", *options.pop("argv", ())]) == 0
    return json.loads(capsys.readouterr().out)


def environment(monkeypatch, tmp_path, **values):
    settings = {
        "SUTRA_PROMPT": "1",
        "SUTRA_MODE": "balanced",
        "SUTRA_PROMPT_PREVIEW": "1",
        "SUTRA_STORE_DIR": str(tmp_path / "originals"),
        "SUTRA_STATS_FILE": str(tmp_path / "stats.jsonl"),
    }
    settings.update({key: str(value) for key, value in values.items()})
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    return store_module.Store(store_module.StoreConfig(directory=settings["SUTRA_STORE_DIR"]))


def test_a_large_result_is_shortened_and_restorable(monkeypatch, capsys, tmp_path):
    handle = environment(monkeypatch, tmp_path)
    original = body()

    report = call(monkeypatch, capsys, {"texts": [original], "session_id": "s1"})

    assert report["changed"] == 1
    shortened = report["texts"][0]
    assert NOTICE in shortened
    assert len(shortened) < len(original)
    ids = ID.findall(shortened)
    assert len(ids) == 1
    assert handle.read_bytes(handle.get(ids[0]).entry) == original.encode()


def test_a_small_result_comes_back_unchanged(monkeypatch, capsys, tmp_path):
    environment(monkeypatch, tmp_path)

    report = call(monkeypatch, capsys, {"texts": ["a short result"]})

    assert report == {"texts": ["a short result"], "changed": 0}


def test_off_returns_the_same_texts(monkeypatch, capsys, tmp_path):
    environment(monkeypatch, tmp_path, SUTRA_MODE="off")

    report = call(monkeypatch, capsys, {"texts": [body()]})

    assert report["changed"] == 0
    assert report["texts"][0] == body()


def test_a_refused_retention_leaves_the_text_alone(monkeypatch, capsys, tmp_path):
    environment(monkeypatch, tmp_path, SUTRA_STORE_MAX_ENTRY_BYTES=1024)

    report = call(monkeypatch, capsys, {"texts": [body()]})

    assert report["changed"] == 0
    assert report["texts"][0] == body()


def test_the_batch_is_mapped_by_position(monkeypatch, capsys, tmp_path):
    environment(monkeypatch, tmp_path)
    long_text = body()

    report = call(monkeypatch, capsys, {"texts": ["small", long_text, 5]})

    assert report["changed"] == 1
    assert len(report["texts"]) == 3
    assert report["texts"][0] == "small"
    assert NOTICE in report["texts"][1]
    assert report["texts"][2] == 5


def test_the_plugin_declares_the_request_hook():
    source = (ROOT / "opencode_plugin.js").read_text(encoding="utf-8")

    assert '"experimental.chat.messages.transform"' in source
    assert '"tool.execute.after"' in source
    assert "shorten-results" in source
    # The hook must touch the result string and nothing else in the part.
    assert "targets[index].output = next" in source


def test_an_unreadable_payload_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))

    assert cli.main(["shorten-results"]) == 1
    assert "cannot read the request" in capsys.readouterr().err.lower()
