"""Tests for the opt-in search layer wired into engine.compress."""

from __future__ import annotations

import json

from conftest import load_plugin, plugin_module

load_plugin()
engine = plugin_module("engine")
config = plugin_module("config")
preserve = plugin_module("preserve")
optimize = plugin_module("optimize")

_BUDGET = {"SUTRA_ENABLED": "1", "SUTRA_MAX_BYTES": "2000",
           "SUTRA_HEAD_BYTES": "1200", "SUTRA_TAIL_BYTES": "600"}
PAYLOAD = "".join(f"line {i:03d}   \n" for i in range(200))


def _cfg(monkeypatch, **env):
    for key, value in {**_BUDGET, **env}.items():
        monkeypatch.setenv(key, value)
    return config.resolve()


def test_search_off_matches_the_default_mode(monkeypatch):
    """The default mode is off, so both runs produce the same bytes."""
    default_text, default_stats = engine.compress(PAYLOAD, cfg=_cfg(monkeypatch))
    monkeypatch.setenv("SUTRA_SEARCH", "off")
    off_text, off_stats = engine.compress(PAYLOAD, cfg=config.resolve())
    assert off_text == default_text
    assert off_stats.rule == default_stats.rule
    assert default_stats.changed
    assert off_stats.search_applied == ""
    assert off_stats.search_saved_bytes == 0


def test_balanced_shrinks_a_changed_result(monkeypatch):
    base_text, _ = engine.compress(PAYLOAD, cfg=_cfg(monkeypatch))
    text, stats = engine.compress(PAYLOAD, cfg=_cfg(monkeypatch, SUTRA_SEARCH="balanced"))
    assert stats.changed
    assert len(text) < len(base_text)
    assert "trailing-space" in stats.search_applied
    assert stats.search_saved_bytes == len(base_text) - len(text)
    assert stats.compressed_bytes == len(text.encode("utf-8"))
    assert len(text) <= 2000


def test_maximum_saves_at_least_balanced(monkeypatch):
    balanced_text, balanced = engine.compress(
        PAYLOAD, cfg=_cfg(monkeypatch, SUTRA_SEARCH="balanced"))
    max_text, maximum = engine.compress(
        PAYLOAD, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum",
                          SUTRA_SEARCH_PASSES="8"))
    assert len(max_text) <= len(balanced_text)
    assert maximum.search_saved_bytes >= balanced.search_saved_bytes


def test_search_runs_even_when_the_engine_changed_nothing(monkeypatch):
    """A search mode owns the final result, not only results the engine touched."""
    off_text, off_stats = engine.compress("short   \n", cfg=_cfg(monkeypatch))
    assert off_text == "short   \n"
    assert off_stats.changed is False and off_stats.rule == "none"
    text, stats = engine.compress(
        "short   \n", cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum"))
    assert text == "short\n"
    assert stats.changed is True
    assert stats.rule == "search"
    assert stats.search_applied == "trailing-space"
    assert stats.search_saved_bytes == 3


def test_search_keeps_recovery_marker_lines(monkeypatch):
    text, stats = engine.compress(
        PAYLOAD + "tail line\n", cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum"))
    marker_lines = [line for line in text.splitlines()
                    if line.strip().startswith("[sutra:")]
    assert marker_lines, "expected a recovery marker in the compressed result"
    for line in marker_lines:
        assert line.rstrip() == line
    assert stats.search_applied


def test_search_keeps_protected_spans_in_the_kept_part(monkeypatch):
    """Trailing-space normalisation inside the kept head keeps paths and URLs."""
    payload = "head\n" + PAYLOAD.replace(
        "line 001   ", "see /etc/hosts and https://example.com/x   ")
    text, stats = engine.compress(payload, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum"))
    ok, reasons = preserve.preservation_ok(payload, text)
    assert not ok  # the engine legitimately elided the middle
    assert not [r for r in reasons if "url" in r or "path" in r]
    assert "https://example.com/x" in text
    assert "/etc/hosts" in text
    assert stats.search_applied


def test_search_keeps_json_parseable(monkeypatch):
    doc = {"items": [{"note": "line one   "} for _ in range(200)]}
    payload = json.dumps(doc, indent=2) + "\n\n\n\n\n"
    text, stats = engine.compress(payload, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum"))
    assert json.loads(text) is not None
    assert len(text) <= len(payload)


def test_language_is_off_unless_requested(monkeypatch):
    text, stats = engine.compress(PAYLOAD, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum"))
    assert "language" not in stats.search_applied


def test_language_shortens_prose_inside_a_compressed_result(monkeypatch):
    prose = ("Please carefully inspect the repository in order to find the "
             "failing test. ") * 20
    payload = PAYLOAD + prose
    base, _ = engine.compress(payload, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum"))
    text, stats = engine.compress(
        payload, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum", SUTRA_LANGUAGE="1"))
    assert "language" in stats.search_applied
    assert len(text) < len(base)
    assert "Please" not in text
    assert "in order to" not in text


def test_language_never_breaks_protected_spans(monkeypatch):
    prose = ("Please keep /etc/hosts and https://example.com/x in order to run "
             "3 tests; do not delete 42 rows. ")
    payload = PAYLOAD + prose * 30
    text, _ = engine.compress(
        payload, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum", SUTRA_LANGUAGE="1"))
    assert "/etc/hosts" in text
    assert "https://example.com/x" in text
    assert "3 tests" in text
    assert "not" in text
    assert "42" in text


def test_search_report_is_deterministic(monkeypatch):
    first, _ = engine.compress(PAYLOAD, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum"))
    second, _ = engine.compress(PAYLOAD, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum"))
    assert first == second


def test_search_strategy_names_are_real(monkeypatch):
    text, stats = engine.compress(PAYLOAD, cfg=_cfg(monkeypatch, SUTRA_SEARCH="maximum"))
    known = {s.name for s in optimize.default_strategies()}
    assert stats.search_applied
    assert set(stats.search_applied.split("+")) <= known


def test_safe_mode_is_its_own_conservative_mode(monkeypatch):
    """`safe` stays a distinct mode: formatting only, never the language pass."""
    resolved = _cfg(monkeypatch, SUTRA_SEARCH="safe")
    assert resolved.search_mode == "safe"
    prose = ("It is important to note that the build is very slow. " * 40)
    safe_text, safe = engine.compress(
        prose, cfg=_cfg(monkeypatch, SUTRA_SEARCH="safe", SUTRA_LANGUAGE="1"))
    balanced_text, balanced = engine.compress(
        prose, cfg=_cfg(monkeypatch, SUTRA_SEARCH="balanced", SUTRA_LANGUAGE="1"))
    assert "language" not in safe.search_applied.split("+")
    assert "language" in balanced.search_applied.split("+")
    assert len(balanced_text) <= len(safe_text)



def test_stats_and_the_log_name_the_mode_that_ran(monkeypatch):
    """A host can tell which mode produced a result from the record alone."""
    metrics = plugin_module("metrics")
    for mode in ("off", "safe", "balanced", "aggressive"):
        _, stats = engine.compress(PAYLOAD, cfg=_cfg(monkeypatch, SUTRA_SEARCH=mode))
        assert stats.search_mode == mode
        record = metrics._compression_record(dict(stats))
        assert record["search_mode"] == mode
        assert record["search_applied"] == stats.search_applied
        assert record["search_saved_bytes"] == stats.search_saved_bytes


def test_json_field_search_is_reported_on_the_document_record(monkeypatch):
    """A JSON result whose prose field was optimised must say so.

    The search layer runs on each decoded string field, because that is where
    prose is visible as prose. The document record used to drop those names and
    savings, so a host saw a smaller payload with an empty search report.
    """
    prose = "".join(
        "It is important to note that worker %d needs to check the queue.   \n" % i
        for i in range(200))
    payload = json.dumps({"stdout": prose, "exit_code": 0})
    balanced, stats = engine.compress(
        payload, cfg=_cfg(monkeypatch, SUTRA_SEARCH="balanced", SUTRA_LANGUAGE="1"))
    assert json.loads(balanced)["exit_code"] == 0
    assert "language" in stats.search_applied.split("+")
    assert stats.search_saved_bytes > 0

    off_text, off_stats = engine.compress(
        payload, cfg=_cfg(monkeypatch, SUTRA_SEARCH="off", SUTRA_LANGUAGE="1"))
    assert off_stats.search_applied == ""
    assert off_stats.search_saved_bytes == 0
    assert len(balanced) < len(off_text)
