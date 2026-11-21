"""The request stage reports what it did, so a mode's depth can be measured.

The counters are process-scoped like the rest of ``metrics``, so every test
asserts a delta rather than an absolute value.
"""

from __future__ import annotations

import types

import pytest

from conftest import plugin_module

prompt = plugin_module("prompt")
metrics = plugin_module("metrics")


def _prompt_block() -> dict:
    return metrics.snapshot()["prompt"]


def _record(decision, mode: str) -> None:
    metrics.record_request_rewrite(decision, mode=mode)


def _marker_body(size: int) -> str:
    marker = ('[sutra: partial · 43,000 bytes elided (output) · '
              'retrieve: sutra_restore(id="deadbeef", mode="bytes")]')
    filler = "line of tool output that the model has already read\n" * 64
    text = marker + "\n"
    while len(text) < size:
        text += filler
    return text


def _request(size: int = 40_000) -> dict:
    return {
        "model": "m",
        "messages": [
            {"role": "tool", "tool_call_id": "c1", "content": _marker_body(size)},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "next"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "and then"},
            {"role": "assistant", "content": "carry on"},
            {"role": "user", "content": "last"},
        ],
    }


def _decision(request: dict) -> object:
    return prompt.prepare(request, keep_last=6, min_request_bytes=1024,
                          min_message_bytes=512)


def test_a_rewrite_is_counted_with_its_bytes():
    before = _prompt_block()
    decision = _decision(_request())
    assert decision.changed
    _record(decision, "aggressive")
    after = _prompt_block()

    assert after["requests"] - before["requests"] == 1
    assert after["rewritten"] - before["rewritten"] == 1
    assert after["unchanged"] == before["unchanged"]
    assert after["original_bytes"] - before["original_bytes"] == decision.bytes_before
    assert after["trimmed_bytes"] - before["trimmed_bytes"] == decision.bytes_after
    assert after["saved_bytes"] - before["saved_bytes"] == (
        decision.bytes_before - decision.bytes_after)


def test_an_unchanged_decision_counts_as_unchanged():
    before = _prompt_block()
    decision = types.SimpleNamespace(changed=False, bytes_before=4_000,
                                     bytes_after=4_000)
    _record(decision, "balanced")
    after = _prompt_block()

    assert after["requests"] - before["requests"] == 1
    assert after["unchanged"] - before["unchanged"] == 1
    assert after["saved_bytes"] == before["saved_bytes"]


def test_each_mode_keeps_its_own_row():
    before = _prompt_block()
    _record(_decision(_request()), "balanced")
    _record(_decision(_request()), "beast")
    after = _prompt_block()

    balanced = after["by_mode"]["balanced"]
    beast = after["by_mode"]["beast"]
    assert balanced["requests"] == before["by_mode"].get("balanced", {}).get("requests", 0) + 1
    assert beast["requests"] == before["by_mode"].get("beast", {}).get("requests", 0) + 1
    assert balanced["rewritten"] >= 1 and beast["rewritten"] >= 1
    assert balanced["saved_bytes"] > 0 and beast["saved_bytes"] > 0
    assert balanced is not beast


def test_the_counter_never_raises_on_a_foreign_object():
    _record(object(), "")
    _record(None, None)  # type: ignore[arg-type]
    block = _prompt_block()
    assert block["requests"] >= 2
    assert "unknown" in block["by_mode"]


def test_the_snapshot_does_not_share_the_mode_table():
    _record(_decision(_request()), "aggressive")
    block = _prompt_block()
    block["by_mode"]["aggressive"]["saved_bytes"] = -1
    assert _prompt_block()["by_mode"]["aggressive"]["saved_bytes"] > 0


def test_the_middleware_counts_the_request_it_rewrites(monkeypatch):
    sutra = plugin_module("__init__")
    config = plugin_module("config")
    monkeypatch.setenv("SUTRA_MODE", "aggressive")
    monkeypatch.setenv("SUTRA_PROMPT", "1")
    monkeypatch.setenv("SUTRA_PROMPT_STORE_HOST", "0")
    cfg = config.resolve()
    monkeypatch.setattr(sutra._config, "resolve", lambda: cfg)
    before = _prompt_block()

    result = sutra.request_middleware(request=_request())

    assert result is not None
    after = _prompt_block()
    assert after["by_mode"]["aggressive"]["requests"] - \
        before["by_mode"].get("aggressive", {}).get("requests", 0) == 1
    assert after["by_mode"]["aggressive"]["rewritten"] - \
        before["by_mode"].get("aggressive", {}).get("rewritten", 0) == 1


def test_the_middleware_counts_a_request_it_leaves_alone(monkeypatch):
    sutra = plugin_module("__init__")
    config = plugin_module("config")
    monkeypatch.setenv("SUTRA_MODE", "balanced")
    monkeypatch.setenv("SUTRA_PROMPT", "1")
    cfg = config.resolve()
    monkeypatch.setattr(sutra._config, "resolve", lambda: cfg)
    before = _prompt_block()

    result = sutra.request_middleware(request={"model": "m", "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]})

    assert result is None
    after = _prompt_block()
    assert after["requests"] - before["requests"] == 1
    assert after["unchanged"] - before["unchanged"] == 1


def test_a_disabled_stage_records_nothing(monkeypatch):
    sutra = plugin_module("__init__")
    monkeypatch.setenv("SUTRA_PROMPT", "0")
    before = _prompt_block()
    assert sutra.request_middleware(request=_request()) is None
    assert _prompt_block()["requests"] == before["requests"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
