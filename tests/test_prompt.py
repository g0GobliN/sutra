"""The ``llm_request`` adapter: trim recoverable bytes, touch nothing else.

These tests exercise ``prompt.prepare`` directly and the middleware wrapper
``__init__.request_middleware`` that Hermes calls with the assembled request.
The middleware contract is asserted against what the host accepts: a dict with
``request`` under it replaces the payload, ``None`` means "send what you built".
"""

from __future__ import annotations

import copy
import json
import re

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


def prepare_input(items, **options):
    """``prepare`` for the Responses/Codex shape: the conversation is ``input``."""
    defaults = {"min_request_bytes": 1024, "min_message_bytes": 512}
    defaults.update(options)
    request = {
        "model": "m",
        "instructions": "You are a coding agent.",
        "input": items,
    }
    return prompt.prepare(request, **defaults)


def responses_conversation(output: str, *, tail_items: int = 6) -> list[dict]:
    """A Responses-shaped conversation whose only tool result is ``output``."""
    items = [
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "Fix the failing test."}]},
        {"type": "function_call", "call_id": "c1", "name": "read_file",
         "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": output},
    ]
    for index in range(tail_items):
        items.append({
            "type": "message", "role": "assistant" if index % 2 else "user",
            "content": [{"type": "output_text" if index % 2 else "input_text",
                         "text": f"continue {index}"}],
        })
    return items


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


# --- host-produced results ----------------------------------------------
#
# The frame carries every message the host assembled, not only results SUTRA
# compressed. The adapter may shorten those too, but only after it has kept the
# original itself; if the store declines, the message must go out unchanged.


def store_at(tmp_path, **changes):
    """A real store rooted in ``tmp_path``, with the production limits."""
    module = plugin_module("store")
    return module.Store(
        module.StoreConfig(directory=str(tmp_path / "originals"), **changes))


def host_body(size: int = 40_000) -> str:
    """A tool payload SUTRA never compressed: no marker and no id."""
    text = "traceback line that the model has already read\n" * 16
    while len(text) < size:
        text += text
    return text[:size]


def entry_ids(text: str) -> list[str]:
    return re.findall(r'id="(su_[0-9a-f]{32})"', text)


def test_a_host_result_is_retained_before_it_is_trimmed(tmp_path):
    handle = store_at(tmp_path)
    original = host_body()
    decision = prepare(conversation(original),
                       retain=prompt.host_retainer(store=handle))
    assert decision.changed
    trimmed = decision.request["messages"][3]["content"]
    assert "request copy" in trimmed
    assert original[:200] in trimmed, "the head of the result still reaches the model"
    ids = entry_ids(trimmed)
    assert len(ids) == 1
    lookup = handle.get(ids[0])
    assert lookup.status == "ok"
    assert handle.read_bytes(lookup.entry) == original.encode()


def test_the_retained_copy_is_reachable_through_the_public_tool(tmp_path):
    recovery = plugin_module("recovery")
    handle = store_at(tmp_path)
    original = host_body()
    decision = prepare(conversation(original),
                       retain=prompt.host_retainer(store=handle))
    entry_id = entry_ids(decision.request["messages"][3]["content"])[0]
    payload = json.loads(recovery.restore(
        {"id": entry_id, "mode": "bytes", "start": 0, "end": 64}, store=handle))
    assert payload["status"] in ("ok", "partial")
    assert payload["content"].encode() == original.encode()[:64]


def test_nothing_is_trimmed_when_the_original_cannot_be_kept(tmp_path):
    handle = store_at(tmp_path, enabled=False)
    decision = prepare(conversation(host_body()),
                       retain=prompt.host_retainer(store=handle))
    assert not decision.changed
    assert "retained" in decision.reason


def test_an_oversized_host_result_is_left_alone(tmp_path):
    handle = store_at(tmp_path, max_entry_bytes=1_024)
    decision = prepare(conversation(host_body()),
                       retain=prompt.host_retainer(store=handle))
    assert not decision.changed
    assert handle.usage()[0] == 0, "nothing was retained for a result it could not cut"


def test_a_marked_result_never_touches_the_store():
    calls: list[str] = []

    def retain(text: str) -> str | None:
        calls.append(text)
        return None

    decision = prepare(conversation(body(20_000)), retain=retain)
    assert decision.changed
    assert calls == [], "an already-recoverable result needs no new copy"


def test_a_host_trim_is_idempotent(tmp_path):
    handle = store_at(tmp_path)
    first = prepare(conversation(host_body()),
                    retain=prompt.host_retainer(store=handle))
    assert first.changed
    second = prompt.prepare(first.request, keep_last=6, min_message_bytes=512,
                            min_request_bytes=0,
                            retain=prompt.host_retainer(store=handle))
    assert not second.changed, "a trimmed copy is not shortened again"


def test_the_same_payload_is_retained_once(tmp_path):
    handle = store_at(tmp_path)
    payload = host_body()
    messages = conversation(payload)
    messages.insert(4, {"role": "tool", "tool_call_id": "c2", "content": payload})
    decision = prepare(messages, max_messages=2,
                       retain=prompt.host_retainer(store=handle))
    assert decision.elided == 2
    assert handle.usage()[0] == 1, "identical bytes share one retained copy"


def test_system_user_and_assistant_text_stays_byte_identical(tmp_path):
    handle = store_at(tmp_path)
    messages = conversation(host_body())
    messages[0]["content"] = "system words " * 3_000
    messages[1]["content"] = "user words " * 3_000
    messages[2]["content"] = "assistant words " * 3_000
    decision = prepare(messages, retain=prompt.host_retainer(store=handle))
    assert decision.changed
    for index in (0, 1, 2):
        assert decision.request["messages"][index]["content"] == messages[index]["content"]


def test_the_middleware_retains_a_host_result(tmp_path, monkeypatch):
    import sutra_plugin

    cfg = _config_with(prompt_enabled=True, mode="balanced",
                       prompt_store_host=True,
                       prompt_min_request_bytes=1_024,
                       prompt_min_message_bytes=512,
                       store_dir=str(tmp_path / "originals"))
    monkeypatch.setattr(sutra_plugin._config, "resolve", lambda: cfg)
    request = {"model": "m", "messages": conversation(host_body())}
    result = sutra_plugin.request_middleware(request=request, session_id="s1")
    assert isinstance(result, dict)
    assert "request copy" in result["request"]["messages"][3]["content"]
    handle = plugin_module("store").store_from_env()
    assert handle.usage()[0] == 1
    assert request["messages"][3]["content"] == conversation(host_body())[3]["content"]


def test_the_middleware_leaves_host_results_alone_when_host_storage_is_off(
        tmp_path, monkeypatch):
    import sutra_plugin

    cfg = _config_with(prompt_enabled=True, mode="balanced",
                       prompt_store_host=False,
                       prompt_min_request_bytes=1_024,
                       prompt_min_message_bytes=512,
                       store_dir=str(tmp_path / "originals"))
    monkeypatch.setattr(sutra_plugin._config, "resolve", lambda: cfg)
    request = {"model": "m", "messages": conversation(host_body())}
    assert sutra_plugin.request_middleware(request=request, session_id="s1") is None


# --- repeated results -------------------------------------------------------

def repeated_conversation(payload: str, *, tail_messages: int = 3) -> list[dict]:
    """A conversation in which the same tool payload arrives twice.

    Three trailing turns put both copies inside the ``keep_last`` window.
    """
    messages = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Fix the failing test."},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": payload},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c2", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": payload},
    ]
    for index in range(tail_messages):
        messages.append({"role": "assistant", "content": f"step {index}"})
        messages.append({"role": "user", "content": f"continue {index}"})
    return messages


def test_a_repeated_host_result_becomes_a_pointer_to_the_first_copy(tmp_path):
    handle = store_at(tmp_path)
    original = host_body()
    decision = prepare(repeated_conversation(original),
                       retain=prompt.host_retainer(store=handle))
    assert decision.changed
    trimmed = decision.request["messages"][3]["content"]
    repeated = decision.request["messages"][5]["content"]
    assert "repeated result" in repeated
    ids = entry_ids(repeated)
    assert len(ids) == 1, "the pointer names exactly one recoverable copy"
    assert ids[0] in trimmed, "the id belongs to the copy still quoted in the request"
    assert len(repeated.encode()) < 512
    assert decision.elided == 2
    assert decision.bytes_after < decision.bytes_before
    lookup = handle.get(ids[0])
    assert handle.read_bytes(lookup.entry) == original.encode()


def test_a_repeated_marked_result_collapses_without_a_store(tmp_path):
    original = body(20_000)
    decision = prepare(repeated_conversation(original))
    assert decision.changed
    repeated = decision.request["messages"][5]["content"]
    assert "repeated result" in repeated
    assert 'id="deadbeef"' in repeated
    assert decision.bytes_after < decision.bytes_before


def test_a_repeat_without_a_recoverable_id_is_left_alone():
    request = {"model": "m", "messages": repeated_conversation(host_body())}
    decision = prompt.prepare(request, min_request_bytes=1_024,
                              min_message_bytes=512)
    assert decision.request is None
    assert "recoverable marker" in decision.reason


def test_a_repeat_inside_the_recent_window_is_never_rewritten(tmp_path):
    handle = store_at(tmp_path)
    original = host_body()
    messages = repeated_conversation(original, tail_messages=0)[:4]
    for index in range(6):
        messages.append({"role": "assistant", "content": f"step {index}"})
        messages.append({"role": "user", "content": f"continue {index}"})
    messages.extend(repeated_conversation(original, tail_messages=0)[4:6])
    decision = prepare(messages, retain=prompt.host_retainer(store=handle),
                       keep_last=3)
    assert decision.changed
    assert "request copy" in decision.request["messages"][3]["content"]
    assert decision.request["messages"][-1]["content"] == original
    assert decision.elided == 1


def test_identical_payloads_are_stored_once(tmp_path):
    handle = store_at(tmp_path)
    decision = prepare(repeated_conversation(host_body()),
                       retain=prompt.host_retainer(store=handle))
    assert decision.changed
    assert "repeated result" in decision.request["messages"][5]["content"]
    assert handle.usage()[0] == 1, "one digest, one entry"


def test_the_collapse_is_idempotent(tmp_path):
    handle = store_at(tmp_path)
    first = prepare(repeated_conversation(host_body()),
                    retain=prompt.host_retainer(store=handle))
    again = prepare(first.request["messages"],
                    retain=prompt.host_retainer(store=handle))
    assert again.request is None


# --------------------------------------------------------------------------
# --- request shapes ---
# Chat and Anthropic requests carry ``messages``; Responses and Codex requests
# carry ``input`` items, where a tool result is a ``function_call_output``.
# --------------------------------------------------------------------------


def test_a_responses_input_result_is_trimmed_and_restorable(tmp_path):
    handle = store_at(tmp_path)
    original = host_body(40_000)
    decision = prepare_input(
        responses_conversation(original), keep_last=2,
        retain=prompt.host_retainer(store=handle))
    assert decision.changed
    trimmed = decision.request["input"][2]["output"]
    assert trimmed.startswith("[sutra: request copy ·")
    ids = entry_ids(trimmed)
    assert len(ids) == 1
    lookup = handle.get(ids[0])
    assert handle.read_bytes(lookup.entry) == original.encode()


def test_the_responses_system_prompt_and_message_text_are_untouched(tmp_path):
    handle = store_at(tmp_path)
    items = responses_conversation(host_body(40_000))
    request = {"model": "m", "instructions": "SYSTEM " * 50,
               "system": "ALSO SYSTEM", "input": items}
    decision = prompt.prepare(
        request, keep_last=2, min_request_bytes=1024,
        min_message_bytes=512, retain=prompt.host_retainer(store=handle))
    assert decision.changed
    assert decision.request["instructions"] == "SYSTEM " * 50
    assert decision.request["system"] == "ALSO SYSTEM"
    assert decision.request["input"][0] == items[0]


def test_a_responses_result_without_an_id_is_left_alone():
    decision = prepare_input(responses_conversation(host_body(40_000)),
                             keep_last=2)
    assert not decision.changed
    assert "recoverable marker" in decision.reason


def test_a_repeated_responses_result_collapses_onto_the_first_copy(tmp_path):
    handle = store_at(tmp_path)
    payload = host_body(10_000)
    items = responses_conversation(payload, tail_items=4)
    items.insert(2, {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "again"}]})
    items.insert(3, {"type": "function_call_output", "call_id": "c0",
                     "output": payload})
    decision = prepare_input(
        items, keep_last=4, retain=prompt.host_retainer(store=handle))
    assert decision.changed
    outputs = [item["output"] for item in decision.request["input"]
               if item.get("type") == "function_call_output"]
    assert outputs[0].startswith("[sutra: request copy ·")
    assert "repeated result" in outputs[1]
    assert entry_ids(outputs[0])[0] in outputs[1]


def test_messages_win_when_a_request_carries_both_shapes():
    request = {"model": "m",
               "messages": conversation(host_body(40_000)),
               "input": responses_conversation(host_body(40_000))}
    decision = prompt.prepare(request, keep_last=6, min_request_bytes=1024,
                              min_message_bytes=512)
    assert not decision.changed
    assert "recoverable marker" in decision.reason


# --- the Anthropic tool_result shape ---


def anthropic_conversation(output, *, tool_use_id="tu_1", extra_blocks=(), tail=6):
    """A Messages-API conversation: the result lives inside a user message."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "run the tests"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "running"},
                {"type": "tool_use", "id": tool_use_id, "name": "Bash",
                 "input": {"command": "pytest -q"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id,
                 "content": output},
                *extra_blocks,
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]
    for _ in range(tail):
        messages.append({"role": "user", "content": [{"type": "text", "text": "next"}]})
    return {"model": "claude", "messages": messages}


def prepare_anthropic(output, **options):
    options.setdefault("min_request_bytes", 1024)
    options.setdefault("min_message_bytes", 512)
    return prompt.prepare(anthropic_conversation(output, **options.pop("conversation", {})), **options)


def result_block(request):
    return request["messages"][2]["content"][0]


def test_a_tool_result_block_is_shortened_and_restorable(tmp_path):
    original = host_body(40_000)
    handle = store_at(tmp_path)
    request = anthropic_conversation(original)
    decision = prompt.prepare(
        request, min_request_bytes=1024, min_message_bytes=512,
        retain=prompt.host_retainer(store=handle),
    )
    assert decision.changed, decision.reason
    block = result_block(decision.request)
    assert block["tool_use_id"] == "tu_1"
    assert isinstance(block["content"], str)
    assert "request copy" in block["content"]
    ids = entry_ids(block["content"])
    assert len(ids) == 1
    lookup = handle.get(ids[0])
    assert handle.read_bytes(lookup.entry).decode() == original
    assert decision.bytes_after < decision.bytes_before


def test_a_tool_result_block_of_parts_keeps_its_other_keys(tmp_path):
    parts = [
        {"type": "text", "text": host_body(30_000), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "exit code 1"},
    ]
    request = anthropic_conversation(parts)
    request["messages"][2]["content"][0]["is_error"] = True
    decision = prompt.prepare(
        request, min_request_bytes=1024, min_message_bytes=512,
        retain=prompt.host_retainer(store=store_at(tmp_path)),
    )
    assert decision.changed, decision.reason
    block = result_block(decision.request)
    assert block["is_error"] is True
    first, second = block["content"]
    assert "request copy" in first["text"]
    assert first["cache_control"] == {"type": "ephemeral"}
    assert second["text"] == "exit code 1"


def test_a_repeated_tool_result_collapses_onto_the_first_copy(tmp_path):
    original = host_body(40_000)
    request = anthropic_conversation(original)
    request["messages"].insert(3, {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "tu_2", "content": original}],
    })
    decision = prompt.prepare(
        request, keep_last=3, min_request_bytes=1024, min_message_bytes=512,
        retain=prompt.host_retainer(store=store_at(tmp_path)),
    )
    assert decision.changed, decision.reason
    first, second = decision.request["messages"][2], decision.request["messages"][3]
    ids = entry_ids(first["content"][0]["content"])
    assert len(ids) == 1
    pointer = second["content"][0]["content"]
    assert "repeated result" in pointer
    assert ids[0] in pointer
    assert second["content"][0]["tool_use_id"] == "tu_2"


def test_two_results_in_one_message_are_never_collapsed(tmp_path):
    original = host_body(40_000)
    request = anthropic_conversation(original, extra_blocks=(
        {"type": "tool_result", "tool_use_id": "tu_2", "content": original},
    ))
    decision = prompt.prepare(
        request, keep_last=3, min_request_bytes=1024, min_message_bytes=512,
        retain=prompt.host_retainer(store=store_at(tmp_path)),
    )
    assert decision.changed, decision.reason
    blocks = decision.request["messages"][2]["content"]
    assert len(blocks) == 2
    assert not any("repeated result" in block["content"] for block in blocks)


def test_a_recent_tool_result_is_not_rewritten(tmp_path):
    request = anthropic_conversation(host_body(40_000))
    request["messages"] = request["messages"][-3:]
    request["messages"][-1] = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "tu_9", "content": host_body(40_000)}],
    }
    decision = prompt.prepare(
        request, keep_last=2, min_request_bytes=1024, min_message_bytes=512,
        retain=prompt.host_retainer(store=store_at(tmp_path)),
    )
    assert decision.changed is False
    assert decision.request is None


# --- dependencies ---------------------------------------------------------


def named_body(size: int, name: str) -> str:
    """A tool payload that quotes ``name`` the way a traceback would."""
    return body(size) + f'\n  File "{name}", line 42, in handle_login\n'


def test_a_result_a_later_message_refers_to_is_never_trimmed():
    named = named_body(20_000, "src/app/auth.py")
    other = body(20_000)
    messages = [
        {"role": "tool", "tool_call_id": "c1", "content": named},
        {"role": "tool", "tool_call_id": "c2", "content": other},
        {"role": "assistant", "content": "the trace points at src/app/auth.py"},
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "go on"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "and then"},
    ]
    decision = prepare(messages)

    assert decision.changed
    assert decision.elided == 1
    sent = decision.request["messages"]
    assert sent[0]["content"] == named
    assert sent[1]["content"] != other


def test_a_result_nothing_later_refers_to_is_still_trimmed():
    messages = [
        {"role": "tool", "tool_call_id": "c1", "content": named_body(20_000, "src/app/auth.py")},
        {"role": "tool", "tool_call_id": "c2", "content": body(20_000)},
        {"role": "assistant", "content": "both outputs are noisy"},
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "go on"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "and then"},
    ]
    decision = prepare(messages)

    assert decision.elided == 2


def test_an_ordinary_word_is_not_a_dependency():
    payload = body(20_000) + "\nerror: the login guard rejected the token\n"
    messages = [
        {"role": "tool", "tool_call_id": "c1", "content": payload},
        {"role": "tool", "tool_call_id": "c2", "content": body(20_000)},
        {"role": "assistant", "content": "that error is the cause"},
        {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "go on"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "and then"},
    ]
    decision = prepare(messages)

    assert decision.elided == 2


def test_dependency_protection_reads_the_responses_shape():
    named = named_body(20_000, "src/app/auth.py")
    items = [
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "why does login fail"}]},
        {"type": "function_call", "call_id": "c1", "name": "read_file",
         "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": named},
        {"type": "function_call", "call_id": "c2", "name": "read_file",
         "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c2", "output": body(20_000)},
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text",
                      "text": "the trace names src/app/auth.py"}]},
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "then"}]},
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "ok"}]},
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "go"}]},
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "done"}]},
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "next"}]},
    ]
    decision = prepare_input(items)

    assert decision.changed
    assert decision.elided == 1
    sent = decision.request["input"]
    assert sent[2]["output"] == named
    assert sent[4]["output"] != body(20_000)


def test_the_system_prompt_is_not_a_dependency():
    named = named_body(20_000, "src/app/auth.py")
    request = {
        "model": "m",
        "instructions": "You are a coding agent fixing src/app/auth.py.",
        "input": responses_conversation(named),
    }
    decision = prompt.prepare(request, keep_last=6, min_request_bytes=1024,
                              min_message_bytes=512)

    assert decision.changed
    assert decision.request["input"][2]["output"] != named


def test_a_later_tool_result_does_not_make_a_result_mandatory():
    named = named_body(20_000, "src/app/auth.py")
    echoing = body(20_000) + "\nsrc/app/auth.py:42: handle_login\n"
    messages = [
        {"role": "user", "content": "Fix the failing test."},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": named},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c2", "type": "function",
                         "function": {"name": "grep", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": echoing},
    ]
    for index in range(3):
        messages.append({"role": "assistant", "content": f"step {index}"})
        messages.append({"role": "user", "content": f"continue {index}"})

    decision = prepare(messages, keep_last=3)

    assert decision.elided == 2, "a grep hit that prints the path is not a dependency"
    assert decision.request["messages"][2]["content"] != named
    assert decision.request["messages"][4]["content"] != echoing


def test_a_collapsed_repeat_keeps_the_payload_container(tmp_path):
    handle = store_at(tmp_path)
    image = {"type": "image", "source": {"type": "base64", "data": "AAAA"}}
    blocks = [{"type": "text", "text": host_body()}, image]
    decision = prepare(repeated_conversation(blocks),
                       retain=prompt.host_retainer(store=handle))

    assert decision.changed
    repeated = decision.request["messages"][5]["content"]
    assert isinstance(repeated, list), "a block payload stays a block payload"
    texts = [block["text"] for block in repeated
             if isinstance(block, dict) and "text" in block]
    assert len(texts) == 1
    assert "repeated result" in texts[0]
    assert image in repeated, "a non-text block is not a marker's business"
    assert decision.elided == 2
    assert decision.bytes_after < decision.bytes_before


def test_concurrent_requests_share_one_bounded_cache(tmp_path):
    import threading

    handle = store_at(tmp_path)
    bodies = [host_body() + f"\npayload {index}\n" for index in range(12)]
    markers: list[str | None] = []
    errors: list[BaseException] = []

    def work(body: str) -> None:
        try:
            retain = prompt.host_retainer(store=handle)
            markers.append(retain(body))
        except BaseException as exc:  # a raise here fails the test below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(body,))
               for body in bodies * 2]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(markers) == len(threads)
    ids = [entry_ids(marker or "")[0] for marker in markers]
    assert len(set(ids)) == len(bodies), "one payload keeps one id under load"
    stored = {handle.read_bytes(handle.get(entry_id).entry).decode()
              for entry_id in ids}
    assert stored == set(bodies)
    assert len(prompt._RETAINED) <= prompt.MAX_RETAINED


def test_the_shared_cache_evicts_old_payloads(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt, "MAX_RETAINED", 3)
    retain = prompt.host_retainer(store=store_at(tmp_path))

    for index in range(8):
        assert entry_ids(retain(host_body() + f"\nunique {index}\n"))

    assert len(prompt._RETAINED) <= 3

# --- the preview keeps the lines that say what failed --------------------
#
# A trimmed result that loses the failing test's name or the `File "…", line N`
# pointer makes the agent read the file again, which costs more than the bytes
# the preview saved. Salient lines come from the middle, where neither the head
# nor the tail reaches.


def test_a_trimmed_result_keeps_the_lines_that_say_what_failed(tmp_path):
    noise = "".join(f"collected line {index}\n" for index in range(400))
    failure = (
        "FAILED tests/test_app.py::test_login - AssertionError: 401 != 200\n"
        '  File "src/app/auth.py", line 42, in handle_login\n'
    )
    text = noise + failure + noise
    retain = prompt.host_retainer(store=store_at(tmp_path))

    trimmed = prompt.shorten_result(text, retain=retain)

    assert trimmed is not None and len(trimmed) < len(text)
    assert "FAILED tests/test_app.py::test_login" in trimmed
    assert 'File "src/app/auth.py", line 42' in trimmed


def test_the_salient_lines_are_bounded(tmp_path):
    failures = "".join(f"FAILED tests/test_app.py::test_{index} - boom\n" for index in range(400))
    text = ("noise\n" * 2000) + failures + ("noise\n" * 2000)
    retain = prompt.host_retainer(store=store_at(tmp_path))

    trimmed = prompt.shorten_result(text, retain=retain)

    assert trimmed is not None
    kept = len(trimmed) - (prompt.HEAD_BYTES + prompt.TAIL_BYTES)
    assert kept < prompt.SALIENT_BYTES + 2_000


def test_a_repeated_body_is_collapsed_in_place(tmp_path):
    """A body that repeats itself keeps its shape instead of a head and a tail.

    The lines are walked once, in order: a run of same-shape lines becomes one
    line and a count, so the neighbour of the line that differs is still visible.
    """
    body = "\n".join(f'  File "/app/m{i}.py", line {i}, in h{i}' for i in range(400))
    body += '\n  File "/app/planted.py", line 400, in planted'
    messages = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Which frame was planted?"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": body},
    ] + [{"role": "user", "content": f"step {index}"} for index in range(8)]
    decision = prompt.prepare({"messages": messages}, keep_last=6,
                              min_request_bytes=1024, min_message_bytes=512,
                              retain=prompt.host_retainer(store=store_at(tmp_path)))

    assert decision.request is not None, decision.reason
    trimmed = decision.request["messages"][3]["content"]
    assert "collapsed in place" in trimmed, trimmed[:200]
    assert 'File "/app/m0.py"' in trimmed
    assert 'File "/app/planted.py"' in trimmed
    assert "shaped the same omitted in place" in trimmed
    assert len(trimmed) < len(body) // 2


@pytest.mark.parametrize("position", [5, 300, 590])
def test_a_frame_that_differs_survives_a_deep_traceback(tmp_path, position):
    """One interesting frame among hundreds of identical ones is what a preview should keep."""
    lines = [f'  File "/app/m{i}.py", line {i}, in h{i}' for i in range(600)]
    lines[position] = f'  File "/app/handle_3890.py", line {position}, in handle_3890'
    body = "\n".join(["Traceback (most recent call last):"] + lines +
                     ["ValueError: aborting because handle_3890 did not resolve"])
    conversation = [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Which function and file raised the final error?"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": body},
    ] + [{"role": "assistant" if index % 2 else "user", "content": f"step {index}"}
         for index in range(8)]
    decision = prompt.prepare({"messages": conversation}, keep_last=6,
                              min_request_bytes=1024, min_message_bytes=512,
                              retain=prompt.host_retainer(store=store_at(tmp_path)))

    assert decision.request is not None
    trimmed = decision.request["messages"][3]["content"]

    assert '"/app/handle_3890.py"' in trimmed
    # The preview keeps the anomaly, not the 599 identical frames around it.
    assert trimmed.count('File "/app/m') < 100  # 600 frames in the body, far fewer in the preview
    assert decision.bytes_after < decision.bytes_before


def test_a_trim_below_the_savings_floor_is_refused(tmp_path):
    """A host that pays for prompt tokens can refuse a small win.

    Removing a few kilobytes from a large request still moves the cached
    prefix, so the saving has to justify the cache miss it causes.
    """
    messages = conversation(body(60_000), tail_messages=6)
    messages.append({"role": "user", "content": "context without markers " * 20_000})
    retain = prompt.host_retainer(store=store_at(tmp_path))

    refused = prepare(messages, retain=retain, min_saving_percent=90)
    assert refused.request is None and refused.elided == 0
    assert "below the 90% floor" in refused.reason

    accepted = prepare(messages, retain=retain, min_saving_percent=1)
    assert accepted.changed is True
    assert accepted.elided == 1
    assert accepted.bytes_after < accepted.bytes_before
