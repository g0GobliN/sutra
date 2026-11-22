"""The seam a host calls when it owns the result but not the conversation.

A host that invokes a plugin per tool result (DeepSeek Harness does) has no
request to rewrite: the payload is the whole unit of work. These tests pin the
command a host shells out to, its JSON contract, and its refusals.
"""

import io
import json
import re
import sys

ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


def big():
    lines = [f"build line {i} nominal" for i in range(3000)]
    lines[1500] = "CLUE: hook-token-5K"
    return "\n".join(lines)


def run(payload, monkeypatch, argv=()):
    from sutra import cli

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    code = cli.main(["process-result", *argv])
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else None)


def test_a_tool_result_is_rewritten_and_marked(sutra, monkeypatch):
    code, report = run({"tool_name": "read_file", "texts": [big()]}, monkeypatch)

    assert code == 0
    assert report["changed"] is True
    output = report["texts"][0]
    assert "[sutra:" in output
    assert "CLUE: hook-token-5K" not in output
    match = ID_RE.search(output)
    assert match, output[:400]

    restored = json.loads(sutra.restore(
        {"id": match.group(1), "mode": "search", "query": "hook-token-5K"}))
    assert restored["status"] == "ok"
    assert "hook-token-5K" in restored["content"]


def test_non_text_entries_pass_through(monkeypatch):
    code, report = run({"tool_name": "read_file", "texts": [None, 4]}, monkeypatch)

    assert code == 0
    assert report == {"texts": [None, 4], "changed": False}


def test_an_evidence_tool_and_a_small_result_are_untouched(sutra, monkeypatch):
    code, report = run(
        {"tool_name": "sutra_restore", "texts": [big()]}, monkeypatch)
    assert code == 0
    assert report == {"texts": [big()], "changed": False}

    code, report = run({"tool_name": "read_file", "texts": ["tiny"]}, monkeypatch)
    assert code == 0
    assert report == {"texts": ["tiny"], "changed": False}


def test_a_payload_without_texts_is_refused(monkeypatch):
    code, _ = run({"tool_name": "read_file"}, monkeypatch)

    assert code == 1
