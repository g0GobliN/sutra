"""Tests for sutra's classifier, policies, adapter and metrics.

No Hermes runtime required; the plugin is loaded as a package by ``conftest``.
"""

import json
import os
import re
import stat

import pytest

ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


def big(lines=4000, line="2026-10-23T09:00:00Z INFO worker tick ok"):
    return "\n".join(f"{line} #{i}" for i in range(lines)) + "\n"


# --- baseline behaviour ----------------------------------------------------

def test_small_result_untouched(sutra):
    out, stats = sutra.compress("hello", max_bytes=1024)
    assert out == "hello"
    assert stats["changed"] is False
    assert stats["rule"] == "none"


def test_large_result_is_head_tail_trimmed(sutra):
    text = big()
    out, stats = sutra.compress(text, max_bytes=2000, head_bytes=600, tail_bytes=300)
    assert stats["changed"] is True
    assert "sutra:" in out
    assert out.startswith(text[:100])
    assert out.endswith(text[-100:])
    assert len(out.encode()) < len(text.encode())


def test_ansi_is_stripped(sutra):
    text = "\x1b[31mred\x1b[0m\n" * 3000
    out, _ = sutra.compress(text, max_bytes=2000, head_bytes=500, tail_bytes=200)
    assert "\x1b[" not in out


def test_identical_lines_are_collapsed(sutra):
    text = "same line\n" * 5000
    out, stats = sutra.compress(text, max_bytes=4096, head_bytes=400, tail_bytes=200)
    assert "identical line(s) omitted" in out
    assert stats["changed"] is True
    assert stats["rule"] in {"dedupe", "head-tail+dedupe"}


def test_diff_is_never_rewritten(sutra):
    text = "diff --git a/x b/x\n@@ -1 +1 @@\n" + "+line\n" * 5000
    out, stats = sutra.compress(text, max_bytes=1000)
    assert out == text
    assert stats["rule"] == "diff-kept"


def test_plain_unified_diff_without_git_prefix_is_never_rewritten(sutra):
    text = "--- old.txt\t2026-10-24\n+++ new.txt\t2026-10-24\n@@ -1,3 +1,3 @@\n" + "-a\n+b\n" * 4000
    out, stats = sutra.compress(text, max_bytes=1000)
    assert out == text
    assert stats["rule"] == "diff-kept"


def test_plain_diff_output_is_never_rewritten(sutra):
    """Regression: `diff a b` output matched none of the git markers."""
    text = "2c2\n< old line\n---\n> new line\n" + "".join(
        f"{i}c{i}\n< removed {i}\n---\n> added {i}\n" for i in range(500))
    assert len(text.encode()) > 1000
    out, stats = sutra.compress(text, max_bytes=1000)
    assert out == text
    assert stats["rule"] == "diff-kept"


def test_a_patch_fragment_protects_the_whole_payload(sutra):
    """Conservative on purpose: we would rather save nothing than cut a patch."""
    log = "\n".join(f"log line {i}" for i in range(4000)) + "\ndiff --git a/x b/x\n"
    out, stats = sutra.compress(log)
    assert out == log
    assert stats["rule"] == "diff-kept"


def test_text_that_merely_mentions_rows_is_still_compressed(sutra):
    text = "\n".join(f"> quoted {i}" for i in range(3000)) + "\n" + "\n".join(
        f"< quoted {i}" for i in range(3000))
    out, stats = sutra.compress(text, max_bytes=2000)
    assert stats["rule"] in {"head-tail", "head-tail+dedupe"}
    assert out != text


def test_traceback_keeps_the_error_end(sutra):
    text = "\n".join(f"frame {i}" for i in range(3000)) + \
        "\nTraceback (most recent call last):\nValueError: boom\n"
    out, stats = sutra.compress(text, max_bytes=2000, head_bytes=500, tail_bytes=500)
    assert "ValueError: boom" in out
    assert stats["rule"] == "traceback"


def test_traceback_respects_max_bytes(sutra):
    """Regression: the traceback path used a hard-coded 2 KiB + 8 KiB budget."""
    text = "".join(f'  File "m{i}.py", line {i}, in f\n' for i in range(2000)) + \
        "Traceback (most recent call last):\nValueError: boom\n"
    out, stats = sutra.compress(text, max_bytes=2000)
    assert len(out.encode("utf-8")) <= 2000
    assert "ValueError: boom" in out
    assert stats["rule"] == "traceback"


def test_traceback_with_no_room_reports_no_change(sutra):
    """Regression: stats claimed a traceback rewrite that never happened."""
    text = "Traceback (most recent call last):\n" + "x" * 150
    out, stats = sutra.compress(text, max_bytes=50)
    assert out == text
    assert stats["changed"] is False
    assert stats["rule"] == "budget-too-small"


# --- text fidelity ---------------------------------------------------------

def test_crlf_and_final_newline_are_preserved(sutra):
    """Regression: splitlines() + '\\n'.join normalised every payload to LF."""
    text = "\r\n".join(f"line {i}" for i in range(3000)) + "\r\n"
    out, stats = sutra.compress(text, max_bytes=2000, head_bytes=600, tail_bytes=300)
    assert stats["changed"] is True
    assert "\r\n" in out
    assert out.endswith("\r\n")
    assert "\n" not in out.replace("\r\n", "")


def test_missing_final_newline_is_preserved(sutra):
    text = "\n".join(f"line {i}" for i in range(3000))
    out, _ = sutra.compress(text, max_bytes=2000, head_bytes=600, tail_bytes=300)
    assert not out.endswith("\n")
    assert out.endswith("line 2999")


def test_unicode_cut_does_not_produce_replacement_characters(sutra):
    text = "日本語テキスト行\n" * 4000
    out, _ = sutra.compress(text, max_bytes=2000, head_bytes=1001, tail_bytes=501)
    assert "\ufffd" not in out
    out.encode("utf-8").decode("utf-8")


def test_collapse_repeats_preserves_line_endings(sutra):
    text = "same\r\n" * 10
    out = sutra.collapse_repeats(text)
    assert "\r\n" in out
    assert out.count("same\r\n") == 2


# --- JSON ------------------------------------------------------------------

def test_json_document_is_never_byte_sliced(sutra):
    payload = json.dumps({"output": "\n".join(f"line {i}" for i in range(6000)),
                          "exit_code": 0})
    out, stats = sutra.compress(payload)
    parsed = json.loads(out)
    assert stats["rule"] == "json-fields"
    assert parsed["exit_code"] == 0
    assert "sutra:" in parsed["output"]
    assert len(out.encode()) < len(payload.encode())


def test_json_array_stays_an_array(sutra):
    """Regression: array elision returned an object, changing the payload's type."""
    data = [("\n".join(f"line {i}" for i in range(4000))) for _ in range(2)]
    text = json.dumps(data)
    # Two large fields share one budget, so each field gets about half of it.
    out, stats = sutra.compress(text, max_bytes=6_000, json_items=10)
    assert stats["rule"] == "json-fields"
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == len(data)
    assert "sutra:" in parsed[0]


def test_json_retired_array_option_is_ignored(sutra, monkeypatch):
    monkeypatch.setenv("SUTRA_JSON_ITEMS", "5")
    data = [{"id": i} for i in range(400)]
    out, stats = sutra.compress(json.dumps(data), max_bytes=1000)
    assert isinstance(json.loads(out), list)
    assert stats["rule"] in {"json-kept", "json-fields"}


def test_json_envelope_keeps_its_top_level_type_and_keys(sutra):
    payload = {"output": big(), "exit_code": 0, "ok": True}
    out = sutra.compress_result(payload)
    assert isinstance(out, dict)
    assert out["exit_code"] == 0 and out["ok"] is True
    assert set(out) == set(payload)
    assert "sutra:" in out["output"]


def test_json_field_marker_names_the_field_and_offsets(sutra):
    payload = json.dumps({"output": "\n".join(f"line {i}" for i in range(6000))})
    stored = json.loads(sutra.compress_result(payload))["output"]
    marker = re.search(r"\[sutra: partial[^\]]*\]", stored).group(0)
    assert 'field="/output"' in marker
    assert 'mode="bytes"' in marker
    assert "start=" in marker and "end=" in marker


def test_json_document_with_only_small_strings_is_left_alone(sutra):
    payload = json.dumps({"rows": [{"id": i, "tag": "x"} for i in range(8000)]})
    out, stats = sutra.compress(payload)
    assert out == payload
    assert stats["changed"] is False
    assert stats["rule"] == "json-kept"


def test_json_with_escaped_unicode_stays_valid(sutra):
    payload = json.dumps({"body": ("café naïve 日本語\n" * 4000)}, ensure_ascii=True)
    out, _ = sutra.compress(payload)
    json.loads(out)
    assert len(out.encode()) < len(payload.encode())


def test_json_invariant_guard_returns_the_original(sutra, monkeypatch):
    payload = json.dumps({"output": "y" * 40000})
    broken = {"original_bytes": 40_000, "compressed_bytes": 10, "changed": True,
              "rule": "head-tail", "omitted_bytes": 39_990, "partial": True}
    monkeypatch.setattr(sutra.engine, "_compress_impl",
                        lambda text, **kw: ('{"broken": 1,}', dict(broken)))
    out, stats = sutra.compress(payload)
    assert out == payload
    assert stats["rule"] == "json-kept"


# --- configuration ---------------------------------------------------------

def test_explicit_zero_head_is_not_overridden_by_env(sutra, monkeypatch):
    """Regression: `x or env` ignored an explicit 0."""
    monkeypatch.setenv("SUTRA_HEAD_BYTES", "4096")
    text = "\n".join(f"row {i}" for i in range(4000))
    out, _ = sutra.compress(text, max_bytes=2000, head_bytes=0, tail_bytes=500)
    assert out.startswith("[sutra:")
    assert "row 0" not in out


def test_max_bytes_is_a_real_cap(sutra):
    """Regression: head+tail were applied without reference to max_bytes."""
    text = "\n".join(f"row {i} " + "x" * 20 for i in range(4000))
    out, _ = sutra.compress(text, max_bytes=2000, head_bytes=5000, tail_bytes=5000)
    assert len(out.encode("utf-8")) <= 2000


def test_budget_too_small_returns_the_original(sutra):
    text = "x" * 5000
    out, stats = sutra.compress(text, max_bytes=300)
    assert out == text
    assert stats["rule"] == "budget-too-small"
    assert stats["changed"] is False


def test_invalid_env_values_fall_back_to_defaults(sutra, monkeypatch):
    monkeypatch.setenv("SUTRA_MAX_BYTES", "not-a-number")
    cfg = sutra.config_from_env()
    assert cfg.max_bytes == sutra._DEFAULT_MAX_BYTES


# --- adapter, fail-open, metrics ------------------------------------------

def test_dict_result_compresses_text_fields_only(sutra):
    payload = {"output": big(), "exit_code": 0, "ok": True}
    out = sutra.compress_result(payload)
    assert out["exit_code"] == 0 and out["ok"] is True
    assert "sutra:" in out["output"]


def test_disabled_returns_result_untouched(sutra, monkeypatch):
    monkeypatch.setenv("SUTRA_ENABLED", "0")
    payload = {"output": big()}
    assert sutra.compress_result(payload) is payload


def test_middleware_shape_calls_next_once(sutra):
    calls = []

    def next_call(args):
        calls.append(args)
        return {"output": big()}

    out = sutra.tool_execution(next_call=next_call, tool_name="terminal", args={"cmd": "x"})
    assert calls == [{"cmd": "x"}]
    assert "sutra:" in out["output"]


def test_middleware_does_not_recompress_its_own_evidence(sutra):
    def next_call(args):
        return {"output": big()}

    result = {"output": big()}
    out = sutra.tool_execution(next_call=lambda args: result, tool_name="sutra_restore",
                            args={})
    assert out is result


def test_compress_result_never_raises(sutra, monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(sutra.engine, "_compress_impl", boom)
    payload = {"output": big()}
    assert sutra.compress_result(payload) is payload
    out, stats = sutra.compress(big())
    assert stats["rule"] == "fail-open"
    assert stats["fail_open"] is True
    assert out == big()


@pytest.mark.parametrize("weird", [None, 42, ["a"], object(), b"bytes"])
def test_unknown_result_shapes_pass_through(sutra, weird):
    assert sutra.compress_result(weird) is weird


def test_deeply_nested_json_does_not_raise(sutra):
    """Regression: json.loads raises RecursionError past the interpreter limit,
    which is not a ValueError and used to escape compress_result entirely."""
    deep = "[" * 100_000 + "]" * 100_000
    out = sutra.compress_result(deep)
    assert isinstance(out, str) and len(out) < len(deep)
    assert "sutra:" in out  # unparseable as JSON, so the text path handled it
    assert sutra.compress(deep)[1]["rule"] in {"head-tail", "head-tail+dedupe"}


def test_stats_are_recorded(sutra):
    sutra.compress_result({"output": big()})
    path = sutra.stats_path()
    line = json.loads(open(path, encoding="utf-8").read().splitlines()[0])
    assert line["original_bytes"] > line["compressed_bytes"]
    assert line["saved_bytes"] > 0
    assert line["stored"] is True
    assert ID_RE.match(f"id={line['id']}")
    assert line["partial"] is True
    assert line["duration_ms"] >= 0


def test_stats_relative_path_is_resolved(sutra, tmp_path, monkeypatch):
    """Regression: os.makedirs('') raised and the record was silently dropped."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUTRA_STATS_FILE", "stats.jsonl")
    sutra.compress_result({"output": big()})
    assert (tmp_path / "stats.jsonl").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_stats_file_is_private(sutra):
    sutra.compress_result({"output": big()})
    mode = stat.S_IMODE(os.stat(sutra.stats_path()).st_mode)
    assert mode == 0o600


def test_stats_file_rotates(sutra, monkeypatch):
    monkeypatch.setenv("SUTRA_STATS_MAX_BYTES", "100")
    sutra.compress_result({"output": big()})
    sutra.compress_result({"output": big(line="different line")})
    assert os.path.exists(sutra.stats_path() + ".1")


def test_json_document_stays_within_the_budget(sutra):
    """Regression: every string field was compressed to the full max_bytes, so a
    document with several large fields grew far past the budget — or, with a
    small budget, was not compressed at all because each field reserved a full
    marker's worth of it."""
    document = {f"field{i}": "\n".join(f"row {j}" for j in range(3_000))
                for i in range(4)}
    text = json.dumps(document)
    for budget in (2_000, 8_000, sutra._DEFAULT_MAX_BYTES):
        out, stats = sutra.compress(text, max_bytes=budget)
        assert stats["rule"] == "json-fields", (budget, stats["rule"])
        assert len(out.encode("utf-8")) <= budget, (budget, stats["rule"])
        parsed = json.loads(out)
        assert isinstance(parsed, dict) and set(parsed) == set(document)
        assert all("sutra:" in parsed[key] for key in document), budget


def test_json_fields_share_one_budget(sutra):
    document = {"a": "x\n" * 4_000, "b": "y\n" * 4_000}
    out, stats = sutra.compress(json.dumps(document), max_bytes=8_000)
    assert stats["rule"] == "json-fields"
    assert len(out.encode("utf-8")) <= 8_000
    parsed = json.loads(out)
    assert "sutra:" in parsed["a"] and "sutra:" in parsed["b"]


# --- behaviors verified against the live gateway (2026-10-24 audit) ---------

def test_nested_dict_text_fields_are_untouched(sutra):
    """Only top-level _TEXT_KEYS string fields are candidates: a dict result whose
    text sits one level down is returned unchanged, never rewritten."""
    big_nested = {"result": {"text": big()}}
    assert sutra.compress_result(big_nested) is big_nested


def test_non_text_keys_are_untouched(sutra):
    """A big string under a key outside _TEXT_KEYS is not rewritten."""
    payload = {"html": big()}
    assert sutra.compress_result(payload) is payload


def test_rewritten_output_is_stable_on_a_second_pass(sutra):
    """The pipeline is idempotent: a rewritten result fed through again is
    untouched, so the middleware can never compound its own marker."""
    out, _ = sutra.compress(big())
    out2, stats = sutra.compress(out)
    assert out2 == out
    assert stats["rule"] == "none"


def test_over_max_input_is_returned_untouched(sutra):
    """Above 64 MiB nothing is rewritten (too-large-kept): the store could not
    hold it and slicing it would cost more than returning it."""
    huge = "x" * (64 * 1024 * 1024 + 100)
    out, stats = sutra.compress(huge)
    assert out is huge
    assert stats["rule"] == "too-large-kept"
    assert stats["changed"] is False


def test_ansi_only_payload_is_not_rewritten(sutra):
    """A payload that is empty after ANSI stripping classifies as empty and is
    kept, not elided."""
    ansi = "\x1b[31m\x1b[0m" * 4000
    out, stats = sutra.compress(ansi, max_bytes=1000)
    assert out == ansi
    assert stats["rule"] == "empty-kept"


def test_small_result_with_ansi_is_untouched(sutra):
    """The size gate runs before ANSI stripping: a small coloured result is
    returned byte-identical, escapes included."""
    text = "\x1b[31mred\x1b[0m small"
    out, stats = sutra.compress(text, max_bytes=4096)
    assert out == text
    assert stats["rule"] == "none"


def test_duplicate_keys_survive_in_the_retained_original(sutra):
    """The visible copy re-encodes (duplicate keys collapse to the last value),
    but the retained original must be byte-exact: recovery returns the input,
    duplicate keys and all."""
    doc = '{"output": "' + "y" * 40000 + '", "a": 1, "a": 2}'
    out = sutra.compress_result(doc)
    parsed = json.loads(out)
    assert parsed["a"] == 2  # the visible copy collapses, as documented
    entry_id = ID_RE.search(out).group(1)
    restored = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "full", "max_bytes": 65536}))
    assert restored["status"] == "ok"
    assert restored["content"] == doc
    assert restored["sha256_verified"] is True


def test_crlf_patch_is_never_rewritten(sutra):
    """Patch detection works on CRLF payloads: a diff with \r\n terminators is
    byte-identical out."""
    text = "diff --git a/x b/x\r\n@@ -1 +1 @@\r\n" + "+line\r\n" * 3000
    out, stats = sutra.compress(text, max_bytes=1000)
    assert out == text
    assert stats["rule"] == "diff-kept"


def test_two_tracebacks_keep_the_last_error_end(sutra):
    """A payload containing two tracebacks is one traceback payload; the elision
    keeps the end, which is where the *last* error lives."""
    tb1 = "Traceback (most recent call last):\nValueError: first boom\n"
    tb2 = "Traceback (most recent call last):\nValueError: second boom\n"
    text = "\n".join(f"frame {i}" for i in range(2000)) + "\n" + tb1 + \
        "\n".join(f"frame {i}" for i in range(2000)) + "\n" + tb2
    out, stats = sutra.compress(text, max_bytes=2000)
    assert stats["rule"] == "traceback"
    assert "ValueError: second boom" in out
