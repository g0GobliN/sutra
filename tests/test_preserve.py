"""SPC: protected spans are detected and every loss is named, never silent."""

import time

from conftest import load_plugin, plugin_module

load_plugin()
preserve = plugin_module("preserve")


def _text():
    return (
        "Running checks\n"
        "```python\n"
        "def apply(value):\n"
        "    return value * 2\n"
        "```\n"
        "read /etc/sutra/config.toml and src/cost/engine.py\n"
        "see https://example.com/docs/limits?page=2\n"
        "$ pytest -q tests/test_engine.py\n"
        "ERROR: permission denied for user 1000 on port 8080\n"
        "Do not run sudo rm -rf /var/lib/data\n"
        "the final_price_quoted value went from 120 to 125\n"
        "Call OrderProcessor.handle_request now\n"
    )


def test_every_protected_kind_is_detected():
    kinds = preserve.counts(_text())
    for kind in ("code-fence", "url", "path", "command", "negation",
                 "security", "error", "number", "identifier"):
        assert kinds[kind] >= 1, (kind, kinds)
    assert kinds["path"] >= 2
    assert kinds["number"] >= 3


def test_identical_text_is_preserved():
    ok, reasons = preserve.preservation_ok(_text(), _text())
    assert ok and reasons == []


def test_a_dropped_path_is_reported_by_kind():
    original = _text()
    candidate = original.replace("read /etc/sutra/config.toml and ", "read ")
    ok, reasons = preserve.preservation_ok(original, candidate)
    assert not ok
    assert any(reason.startswith("path:") for reason in reasons), reasons
    assert any("/etc/sutra/config.toml" in reason for reason in reasons)


def test_a_dropped_error_term_is_reported():
    original = _text()
    candidate = original.replace("ERROR: permission denied", "OK now")
    ok, reasons = preserve.preservation_ok(original, candidate)
    assert not ok
    assert any(reason.startswith("error:") for reason in reasons), reasons


def test_a_swapped_path_with_equal_count_still_fails():
    original = _text()
    candidate = original.replace("/etc/sutra/config.toml", "/etc/other/x.toml")
    ok, reasons = preserve.preservation_ok(original, candidate)
    assert not ok
    assert any("/etc/sutra/config.toml" in reason for reason in reasons)


def test_a_repeated_span_dropped_once_is_reported():
    original = "/etc/hosts\n/etc/hosts\n/etc/hosts\n"
    candidate = "/etc/hosts\n/etc/hosts\n"
    ok, reasons = preserve.preservation_ok(original, candidate)
    assert not ok
    assert any("path:" in reason for reason in reasons), reasons


def test_reindenting_alone_is_preserved():
    original = "if ok:\n    run /etc/hosts\n"
    candidate = "if ok:\n  run /etc/hosts\n"
    ok, reasons = preserve.preservation_ok(original, candidate)
    assert ok, reasons


def test_a_dropped_number_count_is_reported():
    original = "errors: 3 at line 10, port 8080\n"
    candidate = "errors: 1 at line 10\n"
    ok, reasons = preserve.preservation_ok(original, candidate)
    assert not ok
    assert any(reason.startswith("number:") for reason in reasons), reasons


def test_an_empty_candidate_is_rejected():
    ok, reasons = preserve.preservation_ok("text", "")
    assert not ok and reasons == ["candidate is empty"]


def test_a_larger_candidate_is_rejected():
    ok, reasons = preserve.preservation_ok("short", "a much longer candidate")
    assert not ok
    assert "larger" in reasons[0]


def test_a_lost_code_fence_body_is_reported():
    original = "```\nkeep me\n```\n"
    candidate = "```\n```\n"
    ok, reasons = preserve.preservation_ok(original, candidate)
    assert not ok
    assert any(reason.startswith("code-fence:") for reason in reasons), reasons


def test_describe_summarises_counts():
    summary = preserve.describe(_text())
    assert summary["total"] > 10
    assert summary["by_kind"]["path"] >= 2
    assert summary["longest"] > 0


def test_non_text_input_never_raises():
    assert preserve.protected_spans(None) == ()
    assert preserve.protected_spans(b"bytes") == ()
    ok, reasons = preserve.preservation_ok(None, "x")
    assert not ok and reasons
    ok, reasons = preserve.preservation_ok("x", None)
    assert not ok and reasons


def test_one_megabyte_is_checked_quickly():
    block = _text()
    original = (block * 3000)[: 1_000_000]
    started = time.monotonic()
    ok, _ = preserve.preservation_ok(original, original)
    elapsed = time.monotonic() - started
    assert ok
    assert elapsed < 2.0, elapsed
