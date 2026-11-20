"""The ``llm_request`` adapter: trim recoverable bytes, touch nothing else.

These tests exercise ``prompt.prepare`` directly and the middleware wrapper
``__init__.request_middleware`` that Hermes calls with the assembled request.
The middleware contract is asserted against what the host accepts: a dict with
``request`` under it replaces the payload, ``None`` means "send what you built".
"""

from __future__ import annotations

import copy

import pytest

from conftest import plugin_module

prompt = plugin_module("prompt")

MARKER = ('[sutra: partial · 43,000 bytes elided (output) · '
          'retrieve: sutra_restore(id="deadbeef", mode="bytes")]')


def body(size: int, *, marker: str = MARKER) -> str:
    """A tool payload of about ``size`` bytes that carries a marker."""
    filler = ("line of tool output that the model has already read\n" * 64)
    text = filler
    while len(text) < size:
        text += filler
    return f"{marker}\n{text}"


def conversation(tool_payload: str, *, tail_messages: int = 6) -> list[dict]:
    """A request whose oldest tool result is ``tool_payload``."""
    messages = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Fix the failing test."},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": tool_payload},
    ]
    for index in range(tail_messages):
        messages.append({"role": "assistant", "content": f"step {index}"})
        messages.append({"role": "user", "content": f"continue {index}"})
    return messages


def prepare(messages, **options):
    defaults = {"min_request_bytes": 1024, "min_message_bytes": 512}
    defaults.update(options)
    return prompt.prepare({"model": "m", "messages": messages}, **defaults)


def _narrow(callback, kwargs: dict) -> dict:
    """Only the kwargs the callback's signature declares reach it."""
    import inspect

    parameters = inspect.signature(callback).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return kwargs
    return {name: value for name, value in kwargs.items() if name in parameters}


def run_request_chain(callbacks, request, **context):
    """Mirror ``hermes_cli.middleware._apply_request_chain``.

    Each callback gets its own deep copy of ``request`` and
    ``original_request``; a returned dict with a dict under ``request``
    replaces the payload, and a raised exception is contained by the host. The
    chain returns the payload the host would finally send.
    """
    payload = copy.deepcopy(request)
    original = copy.deepcopy(request)
    for callback in callbacks:
        view = copy.deepcopy(payload)
        kwargs = dict(context, request=view, original_request=copy.deepcopy(original))
        try:
            result = callback(**_narrow(callback, kwargs))
        except Exception:  # the host reports and skips a failing callback
            continue
        if isinstance(result, dict) and isinstance(result.get("request"), dict):
            payload = result["request"]
    return payload


# --- eligibility ---------------------------------------------------------


def test_a_marked_old_tool_result_is_trimmed():
    decision = prepare(conversation(body(20_000)))
    assert decision.changed
    assert decision.elided == 1
    assert decision.messages == [3]
    trimmed = decision.request["messages"][3]["content"]
    assert MARKER in trimmed, "the recovery marker must survive the trim"
    assert "request copy" in trimmed
    assert len(trimmed.encode()) < 20_000
    assert decision.saved_bytes > 0


def test_the_stored_conversation_is_not_modified():
    messages = conversation(body(20_000))
    before = copy.deepcopy(messages)
    decision = prepare(messages)
    assert decision.changed
    assert messages[3]["content"] == before[3]["content"]


def test_a_result_without_a_marker_is_left_alone():
    decision = prepare(conversation("x" * 40_000))
    assert not decision.changed
    assert decision.reason == "no tool result in this request carries a recoverable marker"


def test_a_marker_without_an_id_is_not_recoverable():
    bare = "[sutra: something was elided]"
    decision = prepare(conversation(body(20_000, marker=bare)))
    assert not decision.changed


def test_the_recent_messages_are_never_touched():
    messages = conversation(body(20_000), tail_messages=1)
    messages.insert(3, {"role": "tool", "tool_call_id": "c2",
                        "content": body(20_000)})
    # keep_last spans everything except the two oldest tool results.
    decision = prepare(messages, keep_last=3)
    assert decision.changed
    assert decision.messages == [3], "only the message outside the window"
    untouched = decision.request["messages"][4]["content"]
    assert "request copy" not in untouched and MARKER in untouched


def test_a_recent_tool_result_is_not_trimmed():
    messages = conversation(body(20_000), tail_messages=0)
    decision = prepare(messages, keep_last=4)
    assert not decision.changed
    assert decision.reason == "the request is short enough that every message is recent"


def test_a_request_under_the_floor_is_left_alone():
    decision = prepare(conversation(body(20_000)), min_request_bytes=10**9)
    assert not decision.changed
    assert decision.reason.endswith("under the floor")


def test_a_small_tool_result_is_left_alone():
    decision = prepare(conversation(body(2_000)), min_message_bytes=8_192)
    assert not decision.changed


def test_only_tool_messages_are_rewritten():
    messages = conversation(body(20_000))
    messages[1]["content"] = "user words " * 5_000
    decision = prepare(messages)
    assert decision.changed
    assert decision.messages == [3]
    assert "user words" in decision.request["messages"][1]["content"]


# --- shape handling ------------------------------------------------------


def test_block_content_is_trimmed_in_place():
    messages = conversation([{"type": "text", "text": body(20_000)}])
    decision = prepare(messages)
    assert decision.changed
    blocks = decision.request["messages"][3]["content"]
    assert isinstance(blocks, list) and len(blocks) == 1
    assert "request copy" in blocks[0]["text"]


def test_other_block_types_survive_untouched():
    image = {"type": "image", "source": {"data": "AAAA"}}
    messages = conversation([image, {"type": "text", "text": body(20_000)}])
    decision = prepare(messages)
    assert decision.changed
    blocks = decision.request["messages"][3]["content"]
    assert blocks[0] == image


@pytest.mark.parametrize("request_,reason", [
    ("not a request", "the request is not a mapping"),
    ({"model": "m"}, "the request has no message list this adapter models"),
    ({"messages": ["nope"]}, "a message is not a mapping"),
])
def test_an_unmodelled_shape_is_refused_with_a_reason(request_, reason):
    decision = prompt.prepare(request_, min_request_bytes=0)
    assert not decision.changed
    assert decision.request is None
    assert decision.reason == reason


def test_a_responses_style_payload_is_untouched():
    """``input`` is a different wire shape; guessing at it is not worth it."""
    request_ = {"model": "m", "input": [{"type": "function_call_output",
                                         "call_id": "x", "output": body(20_000)}]}
    decision = prompt.prepare(request_)
    assert not decision.changed


def test_the_trim_is_idempotent():
    first = prepare(conversation(body(20_000)))
    second = prompt.prepare(first.request,
                            keep_last=6, min_message_bytes=512,
                            min_request_bytes=0)
    assert not second.changed, "a trimmed payload is not trimmed again"


def test_at_most_max_messages_are_trimmed():
    messages = conversation(body(20_000), tail_messages=6)
    for index in range(4):
        messages.insert(3, {"role": "tool", "tool_call_id": f"c{index + 2}",
                            "content": body(20_000)})
    decision = prepare(messages, max_messages=2)
    assert decision.changed
    assert decision.elided == 2


# --- the middleware wrapper ---------------------------------------------


def _config_with(**changes):
    """A frozen ``Config`` with the given fields replaced."""
    import dataclasses

    import sutra_plugin

    return dataclasses.replace(sutra_plugin.configuration(), **changes)


def test_the_middleware_returns_the_host_payload(monkeypatch):
    import sutra_plugin

    cfg = _config_with(prompt_enabled=True, mode="balanced",
                       prompt_min_request_bytes=1_024,
                       prompt_min_message_bytes=512)
    monkeypatch.setattr(sutra_plugin._config, "resolve", lambda: cfg)

    request = {"model": "m", "messages": conversation(body(20_000))}
    result = sutra_plugin.request_middleware(request=request, session_id="s1")
    assert isinstance(result, dict)
    assert result["source"] == "sutra"
    assert "trimmed" in result["reason"]
    assert result["request"] is not request
    assert len(result["request"]["messages"]) == len(request["messages"])


def test_the_middleware_is_off_by_default(monkeypatch):
    import sutra_plugin

    cfg = _config_with(prompt_enabled=False, mode="balanced")
    monkeypatch.setattr(sutra_plugin._config, "resolve", lambda: cfg)
    assert sutra_plugin.request_middleware(
        request={"model": "m", "messages": conversation(body(20_000))}) is None


def test_off_mode_never_rewrites_a_request(monkeypatch):
    import sutra_plugin

    cfg = _config_with(prompt_enabled=True, mode="off")
    monkeypatch.setattr(sutra_plugin._config, "resolve", lambda: cfg)
    assert sutra_plugin.request_middleware(
        request={"model": "m", "messages": conversation(body(20_000))}) is None


def test_the_middleware_never_raises(monkeypatch):
    import sutra_plugin

    def explode():
        raise RuntimeError("config is broken")

    monkeypatch.setattr(sutra_plugin._config, "resolve", explode)
    assert sutra_plugin.request_middleware(request={"messages": []}) is None
    assert sutra_plugin.request_middleware(request=None) is None


def test_summarise_reports_the_saving_without_rewriting():
    report = prompt.summarise({"messages": conversation(body(20_000))},
                              min_request_bytes=1_024, min_message_bytes=512)
    assert report["changed"] is True
    assert report["elided"] == 1
    assert report["saved_bytes"] > 0
    assert report["messages"] == [3]


# --- the real chain ------------------------------------------------------


def test_the_request_chain_accepts_the_middleware_result(monkeypatch):
    import sutra_plugin

    cfg = _config_with(prompt_enabled=True, mode="balanced",
                       prompt_min_request_bytes=1_024,
                       prompt_min_message_bytes=512)
    monkeypatch.setattr(sutra_plugin._config, "resolve", lambda: cfg)
    request = {"model": "m", "messages": conversation(body(20_000))}
    sent = run_request_chain([sutra_plugin.request_middleware], request,
                             session_id="s1")
    assert MARKER in sent["messages"][3]["content"]
    assert "request copy" in sent["messages"][3]["content"]
    assert sent["messages"][0]["content"] == "You are a coding agent."
    assert len(sent["messages"]) == len(request["messages"])


def test_a_neighbouring_middleware_still_composes(monkeypatch):
    import sutra_plugin

    cfg = _config_with(prompt_enabled=True, mode="balanced",
                       prompt_min_request_bytes=1_024,
                       prompt_min_message_bytes=512)
    monkeypatch.setattr(sutra_plugin._config, "resolve", lambda: cfg)

    def tag(**kwargs):
        request_ = kwargs["request"]
        return {"request": {**request_, "temperature": 0.25}, "source": "tag"}

    def explode(**kwargs):
        raise RuntimeError("another plugin is broken")

    request = {"model": "m", "messages": conversation(body(20_000))}
    sent = run_request_chain([tag, explode, sutra_plugin.request_middleware],
                             request, session_id="s1")
    assert sent["temperature"] == 0.25, "the earlier rewrite survives"
    assert "request copy" in sent["messages"][3]["content"], "sutra still ran"


def test_the_chain_declines_to_change_an_off_request(monkeypatch):
    import sutra_plugin

    cfg = _config_with(prompt_enabled=False, mode="balanced")
    monkeypatch.setattr(sutra_plugin._config, "resolve", lambda: cfg)
    request = {"model": "m", "messages": conversation(body(20_000))}
    assert run_request_chain([sutra_plugin.request_middleware], request) == request


def test_the_engine_id_shape_is_recognised():
    """The engine writes ``id=su_<hex>``; an id regex that only matched hex
    silently disabled the whole adapter, so this pins the real shape."""
    real = ('[sutra: partial · 68,095 bytes elided (large output) · '
            'id=su_4fa8e6a180853ce2d1a5e5c814fa6496 · original 86,490 B retained '
            '168h · retrieve: sutra_restore(id="su_4fa8e6a180853ce2d1a5e5c814fa6496", '
            'mode="bytes", start=12259, end=80354)]')
    payload = real + "\n" + ("src/sutra/engine.py:1: line of a long read\n" * 900)
    decision = prepare(conversation(payload))
    assert decision.changed, "a real engine marker must be recognised"
    trimmed = decision.request["messages"][3]["content"]
    assert 'id=su_4fa8e6a180853ce2d1a5e5c814fa6496' in trimmed


def test_a_payload_the_engine_never_touched_is_left_byte_identical():
    payload = "src/sutra/engine.py:1: line of a long read\n" * 2_000
    decision = prepare(conversation(payload))
    assert not decision.changed


# --------------------------------------------------------------------------
# Which older result goes first is a context-value decision, not an age order


def marked(size: int, recovery_id: str, *, about: str = "") -> str:
    """A tool payload that carries its own recovery marker with ``recovery_id``."""
    marker = (f'[sutra: partial · 9,000 bytes elided (output) · '
              f'retrieve: sutra_restore(id="{recovery_id}", mode="bytes")]')
    filler = ("line of tool output that the model has already read\n" * 64)
    text = f"{about}\n{marker}\n{filler}" if about else f"{marker}\n{filler}"
    while len(text) < size:
        text += filler
    return text


def two_results(first: str, second: str, *, question: str = "Summarize the run."):
    """A request with two older tool results and a fresh turn after them."""
    messages = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": question},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": first},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c2", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": second},
    ]
    for index in range(6):
        messages.append({"role": "assistant", "content": f"step {index}"})
        messages.append({"role": "user", "content": f"continue {index}"})
    return messages


def test_a_result_a_later_message_cites_is_mandatory():
    """A later message that quotes the id marks that result as live context."""
    first = marked(9_000, "aaaa1111")
    second = marked(9_000, "bbbb2222")
    messages = two_results(first, second)
    messages.insert(4, {"role": "assistant",
                        "content": 'The earlier read of id="aaaa1111" is still relevant.'})
    decision = prepare(messages)
    assert decision.changed
    assert 3 not in decision.messages  # cited by the assistant just below it
    assert 6 in decision.messages  # uncited, so it is the one that goes


def test_a_marker_inside_another_tool_result_is_not_a_citation():
    """Every compressed result carries a marker; that must not block trimming."""
    messages = two_results(marked(9_000, "aaaa1111"), marked(9_000, "bbbb2222"))
    decision = prepare(messages, max_messages=2)
    assert decision.changed
    assert decision.elided == 2


def test_the_result_that_answers_the_latest_question_survives_one_trim():
    """Value order, not age: the payload the question names is trimmed last."""
    unasked = marked(9_000, "aaaa1111", about="unrelated listing of a temporary directory")
    asked = marked(9_000, "bbbb2222", about="the retrieve_original helper in store.py")
    messages = two_results(
        unasked, asked, question="What does retrieve_original in store.py do?"
    )
    decision = prepare(messages, max_messages=1)
    assert decision.changed
    assert decision.elided == 1
    assert decision.messages == [3]


def test_the_reason_reports_the_value_of_what_was_trimmed():
    decision = prepare(two_results(marked(9_000, "aaaa1111"),
                                   marked(9_000, "bbbb2222")))
    assert "lowest context value" in decision.reason
    assert "(" in decision.reason and ")" in decision.reason


def test_every_cited_result_means_nothing_is_trimmed():
    """When the whole window is still referenced, the request goes out untouched."""
    messages = two_results(marked(9_000, "aaaa1111"), marked(9_000, "bbbb2222"))
    messages.insert(4, {"role": "assistant", "content": "restore id=aaaa1111 first"})
    messages.insert(7, {"role": "assistant", "content": "then restore id=bbbb2222"})
    decision = prepare(messages)
    assert not decision.changed
    assert "cited by a later message" in decision.reason
