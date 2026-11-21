"""Stability guarantees the provider-side cache depends on.

A provider caches the prefix it has already processed, so the one thing the
request stage must never do is rewrite the same conversation differently on the
next turn. Two properties support that claim and are checked here:

* a request already carrying the notice comes back unchanged, and
* retention stays inside the store budget when several sessions retain at once,
  so a store refusal can only ever leave a message byte-identical.
"""

from __future__ import annotations

import threading

from conftest import plugin_module

prompt = plugin_module("prompt")
store_module = plugin_module("store")


def store_at(tmp_path, **changes):
    return store_module.Store(
        store_module.StoreConfig(directory=str(tmp_path / "originals"), **changes))


def marked_body(size, marker="old0001", *, marked=True):
    line = "output line that repeats until the result is worth shortening\n"
    body = (line * (size // len(line) + 1))[:size]
    if not marked:
        return body
    return f'[sutra: compressed · id="{marker}"]\n' + body


def conversation(results=3, size=12_000, *, marked=True):
    messages = [{"role": "system", "content": "You are a coding agent."},
                {"role": "user", "content": "the build fails"}]
    for index in range(results):
        messages.append({"role": "assistant", "content": None,
                         "tool_calls": [{"id": f"call{index}", "type": "function",
                                         "function": {"name": "bash",
                                                      "arguments": "{}"}}]})
        messages.append({"role": "tool",
                         "content": marked_body(size, f"old{index:04d}", marked=marked)})
        messages.append({"role": "assistant", "content": f"failure number {index}"})
    for index in range(6):
        messages.append({"role": "user" if index % 2 else "assistant",
                         "content": f"tail message {index}"})
    return {"model": "m", "messages": messages}


def prepare(request, handle, *, keep_last=6):
    return prompt.prepare(request, keep_last=keep_last, min_message_bytes=1024,
                          min_request_bytes=2048, max_messages=8,
                          head_bytes=prompt.HEAD_BYTES, tail_bytes=prompt.TAIL_BYTES,
                          retain=prompt.host_retainer(store=handle))


def test_a_second_identical_request_is_left_alone(tmp_path):
    handle = store_at(tmp_path)
    first = prepare(conversation(), handle)
    assert first.changed and first.request is not None

    second = prepare(first.request, handle)
    assert second.changed is False
    assert second.request is None
    assert second.bytes_after == second.bytes_before
    assert second.elided == 0


def test_the_notice_appears_once_after_two_passes(tmp_path):
    handle = store_at(tmp_path)
    first = prepare(conversation(), handle)
    prepared = next(message for message in first.request["messages"]
                    if isinstance(message.get("content"), str)
                    and prompt.NOTICE_PREFIX in message["content"])
    assert prepared["content"].count(prompt.NOTICE_PREFIX) == 1

    second = prepare(first.request, handle)
    assert second.request is None


def test_concurrent_retention_stays_within_the_store_budget(tmp_path):
    handle = store_at(tmp_path, max_bytes=20_000, max_entry_bytes=8_000,
                      max_entries=8)
    failures: list[BaseException] = []

    def worker(worker_index):
        try:
            for index in range(10):
                handle.put(f"worker {worker_index} payload {index} " + "x" * 3_000,
                           tool_name="bash")
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            failures.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    entries, total = handle.usage()
    assert total <= 20_000
    assert entries <= 8


def test_a_refused_retention_leaves_the_message_untouched(tmp_path):
    handle = store_at(tmp_path, max_entry_bytes=1_024)
    request = conversation(results=1, marked=False)
    decision = prepare(request, handle)
    assert decision.changed is False
    assert decision.request is None
    assert handle.usage()[0] == 0
