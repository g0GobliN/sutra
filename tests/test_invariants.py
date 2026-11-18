"""Invariants that must hold for every payload, not only the fixtures.

Property-style tests with a fixed seed instead of a new dependency: the corpus
is generated from the shapes SUTRA meets in practice (prose, logs, JSON, code
fences, unicode, protected spans) and every invariant is asserted over all of
it, so a failure names the generated case that broke it.
"""

from __future__ import annotations

import json
import random

import pytest

from conftest import plugin_module

config = plugin_module("config")
core = plugin_module("core")
cost = plugin_module("cost")
engine = plugin_module("engine")
optimize = plugin_module("optimize")
preserve = plugin_module("preserve")

SEED = 20261118

FRAGMENTS = (
    "Please carefully inspect the repository and provide a detailed explanation.",
    "It is important to note that you should not delete files.",
    "Keep /etc/hosts and https://example.com/docs intact for 3 retries.",
    "Run `pip install -r requirements.txt` before the next step.",
    "ERROR: failed to connect to the database after 5 attempts",
    "SUTRA_SEARCH=aggressive does not change the default output.",
    "GET /api/v1/users returned 200 in 12ms",
    "日本語のテキストはそのまま保持します",
    "emoji must survive: ✅ 🚀 🔒",
    "a\u00a0b\u2003c",  # unicode spaces
    "line with trailing spaces   ",
    "if value > 0 and value != 10: return value",
    "diff --git a/core.py b/core.py",
    "def compress(text: str) -> str:",
    "",
)


def _corpus() -> list[tuple[str, str]]:
    rng = random.Random(SEED)
    cases: list[tuple[str, str]] = []
    for index in range(24):
        lines = [rng.choice(FRAGMENTS) for _ in range(rng.randint(3, 30))]
        cases.append((f"mixed-{index}", "\n".join(lines)))
    cases.extend([
        ("json-array", json.dumps([{"index": n, "path": f"/tmp/file{n}.txt"}
                                   for n in range(20)])),
        ("json-object", json.dumps({"error": "timeout", "retries": 3,
                                    "url": "https://example.com/docs"})),
        ("code-fence", "before\n```python\nprint('hi')\n```\nafter"),
        ("blank-runs", "a\n\n\n\n\nb\n" * 40),
        ("crlf", "line one\r\nline two\r\n" * 30),
        ("long-line", "x" * 20000 + " /etc/hosts " + "y" * 20000),
        ("numbers", " ".join(str(n) for n in range(200))),
    ])
    return cases


def _aggressive_cfg(monkeypatch):
    monkeypatch.setenv("SUTRA_SEARCH", "aggressive")
    monkeypatch.setenv("SUTRA_SEARCH_PASSES", "4")
    monkeypatch.setenv("SUTRA_SEARCH_BUDGET_MS", "25")
    monkeypatch.setenv("SUTRA_LANGUAGE", "1")
    return config.resolve()


def test_identity_always_preserves_everything():
    for name, text in _corpus():
        ok, reasons = preserve.preservation_ok(text, text)
        assert ok, f"{name}: identity rejected: {reasons}"


def test_every_compressed_result_is_smaller_and_preserved(monkeypatch):
    """The search layer may not lose anything the deterministic pass kept.

    The deterministic pass itself may omit the middle of a huge payload — that
    is its documented, recoverable behaviour. The invariant being tested here is
    that the extra passes never lose another protected span on top of it.
    """
    plain = config.resolve()
    aggressive = _aggressive_cfg(monkeypatch)
    for name, text in _corpus():
        baseline, _ = engine.compress(text, cfg=plain)
        out, stats = engine.compress(text, cfg=aggressive)
        before = len(text.encode("utf-8"))
        after = len(out.encode("utf-8"))
        assert after <= before, f"{name}: compression grew the payload"
        ok, reasons = preserve.preservation_ok(baseline, out)
        assert ok, f"{name}: search lost a span: {reasons}"
        if text.lstrip().startswith(("[", "{")):
            json.loads(out)  # the JSON invariant holds after the search too
        if stats.changed:
            assert after < before, f"{name}: changed but not smaller"


def test_compression_is_deterministic(monkeypatch):
    cfg = _aggressive_cfg(monkeypatch)
    for name, text in _corpus()[:10]:
        first = engine.compress(text, cfg=cfg)
        second = engine.compress(text, cfg=cfg)
        assert first[0] == second[0], f"{name}: output differs between runs"
        assert first[1].rule == second[1].rule


def test_a_candidate_that_drops_a_path_is_rejected():
    text = "Keep /etc/hosts intact.\n" * 20
    destructive = optimize.Strategy(
        "drop-path", lambda value: value.replace("/etc/hosts", ""))
    report = optimize.optimize(text, strategies=[destructive])
    assert report.text == text
    assert tuple(report.applied) == ()
    assert report.evaluations[0].accepted is False


def test_a_raising_strategy_never_escapes_or_changes_the_text():
    def explode(_text: str) -> str:
        raise RuntimeError("strategy failure")

    text = "plain text that must stay\n" * 30
    report = optimize.optimize(text, strategies=[optimize.Strategy("boom", explode)])
    assert report.text == text
    assert report.evaluations[0].accepted is False
    assert "strategy failure" in (report.evaluations[0].reason or "")


def test_the_optimizer_is_deterministic():
    text = "a  \n\n\n\nb  \n" * 60
    first = optimize.optimize(text)
    second = optimize.optimize(text)
    assert first.text == second.text
    assert first.applied == second.applied


def test_disabled_configuration_does_not_modify_data(monkeypatch):
    text = "do not modify me\n" * 100
    result = {"content": text, "status": "ok"}
    monkeypatch.setenv("SUTRA_ENABLED", "0")
    cfg = config.resolve()
    outcome = core.process_text(text, cfg=cfg)
    assert outcome.output == text
    assert outcome.changed is False
    assert core.process_result(result) == result


def test_failed_transformation_returns_the_original_text():
    """A payload that cannot be measured is never rewritten."""
    surrogate = "bad \ud800 text"
    outcome = core.process_text(surrogate)
    assert outcome.output == surrogate
    assert outcome.changed is False


def test_cost_uses_only_explicit_numbers():
    baseline = cost.Usage(input_tokens=1_000_000, output_tokens=100_000)
    optimized = cost.Usage(input_tokens=400_000, output_tokens=100_000)
    pricing = cost.Pricing(input_per_million=3.0, output_per_million=15.0)
    report = cost.savings_report(baseline=baseline, optimized=optimized,
                                 pricing=pricing)
    assert report["saved_tokens"] == 600_000
    assert report["cost"]["saved_usd"] == pytest.approx(1.8)
    assert report["cost"]["basis"] == "explicit pricing"
    unpriced = cost.savings_report(baseline=baseline, optimized=optimized)
    assert unpriced["cost"]["saved_usd"] is None
    assert unpriced["cost"]["basis"] == "unavailable: no pricing supplied"
