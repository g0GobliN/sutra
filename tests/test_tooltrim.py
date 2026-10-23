"""Tests for tooltrim's compression rules. No Hermes runtime required."""

import json
import os
import sys

import pytest

# The repository root IS the plugin (plugin.yaml + __init__.py), so load the
# module by path instead of relying on a package layout.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import importlib.util

spec = importlib.util.spec_from_file_location("tooltrim_plugin", os.path.join(ROOT, "__init__.py"))
tooltrim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tooltrim)


def big(lines=4000, line="2026-10-23T09:00:00Z INFO worker tick ok"):
    return "\n".join(f"{line} #{i}" for i in range(lines))


def test_small_result_untouched():
    text = "hello"
    out, stats = tooltrim.compress(text, max_bytes=1024)
    assert out == text
    assert stats["changed"] is False


def test_large_result_is_head_tail_trimmed():
    text = big()
    out, stats = tooltrim.compress(text, max_bytes=2000, head_bytes=600, tail_bytes=300)
    assert stats["changed"] is True
    assert "tooltrim:" in out
    assert out.startswith(text[:100])
    assert out.endswith(text[-100:])
    assert len(out.encode()) < len(text.encode())


def test_ansi_is_stripped():
    text = "\x1b[31mred\x1b[0m\n" * 3000
    out, _ = tooltrim.compress(text, max_bytes=2000, head_bytes=500, tail_bytes=200)
    assert "\x1b[" not in out


def test_identical_lines_are_collapsed():
    text = "same line\n" * 5000
    out, stats = tooltrim.compress(text, max_bytes=1000, head_bytes=400, tail_bytes=200)
    assert "more identical line(s) omitted" in out
    assert stats["changed"] is True


def test_diff_is_never_rewritten():
    text = "diff --git a/x b/x\n" + "@@ -1 +1 @@\n" + "+line\n" * 5000
    out, stats = tooltrim.compress(text, max_bytes=1000)
    assert out == text
    assert stats["rule"] == "diff-kept"


def test_traceback_keeps_the_error_end():
    text = "\n".join(f"frame {i}" for i in range(3000)) + "\nTraceback (most recent call last):\nValueError: boom\n"
    out, stats = tooltrim.compress(text, max_bytes=2000, head_bytes=500, tail_bytes=500)
    assert "ValueError: boom" in out
    assert stats["rule"] == "traceback"


def test_json_array_elides_the_middle():
    data = [{"id": i, "msg": "x" * 50} for i in range(400)]
    text = json.dumps(data)
    out, stats = tooltrim.compress(text, max_bytes=2000, json_items=40)
    parsed = json.loads(out)
    assert stats["rule"] == "json-array"
    assert parsed["tooltrim"]["original_items"] == 400
    assert parsed["items"][0]["id"] == 0
    assert parsed["items"][-1]["id"] == 399


def test_dict_result_compresses_text_fields_only():
    payload = {"output": big(), "exit_code": 0, "ok": True}
    out = tooltrim.compress_result(payload)
    assert out["exit_code"] == 0 and out["ok"] is True
    assert "tooltrim:" in out["output"]


def test_disabled_returns_result_untouched(monkeypatch):
    monkeypatch.setenv("TOOLTRIM_ENABLED", "0")
    payload = {"output": big()}
    assert tooltrim.compress_result(payload) is payload


def test_stats_are_recorded(tmp_path, monkeypatch):
    stats_file = tmp_path / "stats.jsonl"
    monkeypatch.setenv("TOOLTRIM_STATS_FILE", str(stats_file))
    tooltrim.compress_result({"output": big()})
    line = json.loads(stats_file.read_text().splitlines()[0])
    assert line["original_bytes"] > line["compressed_bytes"]
    assert line["saved_bytes"] > 0


def test_middleware_shape_calls_next_once():
    calls = []

    def next_call(args):
        calls.append(args)
        return {"output": big()}

    out = tooltrim.tool_execution(next_call=next_call, tool_name="terminal", args={"cmd": "x"})
    assert calls == [{"cmd": "x"}]
    assert "tooltrim:" in out["output"]


@pytest.mark.parametrize("weird", [None, 42, ["a"], object()])
def test_unknown_result_shapes_pass_through(weird):
    assert tooltrim.compress_result(weird) is weird
