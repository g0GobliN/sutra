"""Provider usage is observed, never controlled.

The host hands the ``post_api_request`` hook the usage summary of the response
it already received. These tests pin the accounting rules: every bucket the
provider reported is summed, a summary that did not arrive is counted as
unreported rather than as zero tokens, and a summary without cache buckets is
not a cache hit. Nothing here may raise, because a missing metric must never
disturb a completed call.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _provider(sutra):
    return sutra.metrics_snapshot()["provider"]


def _delta(before, after, key):
    return after.get(key, 0) - before.get(key, 0)


def test_reported_buckets_are_summed(sutra):
    before = _provider(sutra)
    sutra.api_usage(usage={
        "input_tokens": 1_200,
        "output_tokens": 80,
        "cache_read_tokens": 900,
        "cache_write_tokens": 300,
    })
    after = _provider(sutra)

    assert _delta(before, after, "requests") == 1
    assert _delta(before, after, "reported") == 1
    assert _delta(before, after, "cached_requests") == 1
    assert _delta(before, after, "input_tokens") == 1_200
    assert _delta(before, after, "output_tokens") == 80
    assert _delta(before, after, "cache_read_tokens") == 900
    assert _delta(before, after, "cache_write_tokens") == 300


def test_a_summary_without_cache_buckets_is_not_a_cached_request(sutra):
    """A Chat Completions summary carries only prompt/completion tokens."""
    before = _provider(sutra)
    sutra.api_usage(usage={"prompt_tokens": 500, "total_tokens": 540})
    after = _provider(sutra)

    assert _delta(before, after, "requests") == 1
    assert _delta(before, after, "reported") == 1
    assert _delta(before, after, "cached_requests") == 0
    assert _delta(before, after, "cache_read_tokens") == 0


def test_a_missing_usage_block_counts_as_unreported(sutra):
    """Not saying and saying zero are different facts."""
    before = _provider(sutra)
    sutra.api_usage()
    sutra.api_usage(usage=None)
    after = _provider(sutra)

    assert _delta(before, after, "requests") == 2
    assert _delta(before, after, "unreported") == 2
    assert _delta(before, after, "reported") == 0


def test_unusable_values_are_not_counted_as_tokens(sutra):
    before = _provider(sutra)
    sutra.api_usage(usage={
        "input_tokens": "1,200",
        "output_tokens": True,
        "cache_read_tokens": -5,
        "cache_write_tokens": 12.0,
    })
    after = _provider(sutra)

    assert _delta(before, after, "reported") == 1
    assert _delta(before, after, "input_tokens") == 0
    assert _delta(before, after, "output_tokens") == 0
    assert _delta(before, after, "cache_read_tokens") == 0
    assert _delta(before, after, "cache_write_tokens") == 12


def test_the_hook_accepts_the_full_host_payload(sutra):
    """The host passes many named fields; only ``usage`` matters here."""
    before = _provider(sutra)
    assert sutra.api_usage(
        task_id="t1", turn_id="u1", api_request_id="r1", session_id="s1",
        platform="cli", model="m", provider="p", base_url="https://x",
        api_mode="anthropic_messages", api_call_count=3, api_duration=1.5,
        started_at="", ended_at="", first_chunk_at="", finish_reason="stop",
        message_count=12, response_model="m", response={"id": "resp"},
        context_length=9_000, assistant_message={"role": "assistant"},
        assistant_content_chars=42, assistant_tool_call_count=1,
        moa_references=[], usage={"input_tokens": 10, "cache_read_tokens": 90},
    ) is None
    after = _provider(sutra)
    assert _delta(before, after, "cache_read_tokens") == 90


def test_the_hook_never_raises_and_returns_none(sutra):
    """A malformed payload is a missing metric, not a failed turn."""
    assert sutra.api_usage(usage="not a dict") is None
    assert sutra.api_usage(usage={"input_tokens": object()}) is None
    assert sutra.api_usage(response={"private": "payload"}) is None


def test_register_declares_the_hook_it_registers(sutra):
    """The manifest gate and the runtime registration must agree."""
    class Ctx:
        def __init__(self):
            self.hooks = []

        def register_middleware(self, kind, callback):
            pass

        def register_tool(self, **kwargs):
            pass

        def register_hook(self, name, callback):
            self.hooks.append((name, callback))

    ctx = Ctx()
    sutra.register(ctx)
    assert ctx.hooks == [("post_api_request", sutra.api_usage)]

    manifest = open(os.path.join(ROOT, "plugin.yaml"), encoding="utf-8").read()
    for name, _ in ctx.hooks:
        assert f"- {name}" in manifest


def test_a_host_without_the_hook_api_still_registers(sutra):
    """An older host must keep compression, recovery and the request stage."""
    class Ctx:
        def __init__(self):
            self.middleware = []
            self.tools = []

        def register_middleware(self, kind, callback):
            self.middleware.append(kind)

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

    ctx = Ctx()
    sutra.register(ctx)
    assert ctx.middleware == ["tool_execution", "llm_request"]
    assert [tool["name"] for tool in ctx.tools] == ["sutra_restore"]
