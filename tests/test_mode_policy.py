"""The four modes change how much the request stage does, measurably.

``config.PROMPT_CAPS_BY_MODE`` supplies the defaults per mode and an explicit
``SUTRA_PROMPT_*`` variable still wins. The last tests run the same conversation
through the middleware under each mode and compare the bytes that were left, so
a mode switch is a measurement rather than a label.
"""

from __future__ import annotations

import json
import re

import pytest

import sutra_plugin

config = sutra_plugin._config
_RESOLVE = config.resolve          # the real resolver, kept out of the patch
MODES = ("balanced", "aggressive", "beast")


def _resolve_with(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return _RESOLVE()


@pytest.mark.parametrize("mode", MODES)
def test_each_mode_supplies_its_own_caps(monkeypatch, mode):
    caps = config.PROMPT_CAPS_BY_MODE[mode]
    cfg = _resolve_with(monkeypatch, SUTRA_MODE=mode)
    assert cfg.prompt_max_messages == caps["max_messages"]
    assert cfg.prompt_min_request_bytes == caps["min_request_bytes"]
    assert cfg.prompt_min_message_bytes == caps["min_message_bytes"]
    assert cfg.prompt_preview is caps["preview"]


def test_off_keeps_the_balanced_caps(monkeypatch):
    caps = config.PROMPT_CAPS_BY_MODE["balanced"]
    cfg = _resolve_with(monkeypatch, SUTRA_MODE="off")
    assert cfg.prompt_max_messages == caps["max_messages"]
    assert cfg.prompt_min_request_bytes == caps["min_request_bytes"]


def test_an_explicit_variable_still_wins(monkeypatch):
    cfg = _resolve_with(monkeypatch, SUTRA_MODE="beast",
                        SUTRA_PROMPT_MAX_MESSAGES="3",
                        SUTRA_PROMPT_PREVIEW="1",
                        SUTRA_PROMPT_MIN_REQUEST_BYTES="4096")
    assert cfg.prompt_max_messages == 3
    assert cfg.prompt_preview is True
    assert cfg.prompt_min_request_bytes == 4_096


def _conversation(results: int = 40, size: int = 12_288) -> dict:
    """One request with many distinct, recoverable old results and a short tail."""
    messages = []
    for index in range(results):
        marker = f'[sutra: partial · tool=shell · id="old{index:04d}"]'
        filler = "output line\n" * (size // 12)
        messages.append({"role": "tool", "tool_call_id": f"c{index}",
                         "content": marker + "\n" + filler})
        messages.append({"role": "assistant", "content": f"read result {index}"})
    for index in range(3):
        messages.append({"role": "user", "content": f"question {index}"})
        messages.append({"role": "assistant", "content": f"answer {index}"})
    return {"model": "m", "messages": messages}


def _size(request: dict) -> int:
    return len(json.dumps(request["messages"], ensure_ascii=False).encode())


def _sent(monkeypatch, mode: str, request: dict) -> dict:
    cfg = _resolve_with(monkeypatch, SUTRA_MODE=mode, SUTRA_PROMPT="1",
                        SUTRA_PROMPT_STORE_HOST="0")
    monkeypatch.setattr(sutra_plugin._config, "resolve", lambda: cfg)
    result = sutra_plugin.request_middleware(request=request, session_id="s1")
    assert result is not None, mode
    return result["request"]


def _touched(sent: dict) -> list[str]:
    """The contents of old tool results the policy rewrote."""
    return [message["content"] for message in sent["messages"]
            if message.get("role") == "tool"
            and sutra_plugin._prompt.NOTICE_PREFIX in message["content"]]


def test_deeper_modes_remove_more_bytes_from_the_same_request(monkeypatch):
    saved = {}
    for mode in MODES:
        request = _conversation()
        before = _size(request)
        sent = _sent(monkeypatch, mode, request)
        saved[mode] = before - _size(sent)

    assert 0 < saved["balanced"] < saved["aggressive"] < saved["beast"]


def test_each_mode_spends_its_own_message_budget(monkeypatch):
    for mode in MODES:
        caps = config.PROMPT_CAPS_BY_MODE[mode]
        sent = _sent(monkeypatch, mode, _conversation())
        assert len(_touched(sent)) == caps["max_messages"]


def test_balanced_keeps_a_preview_and_beast_keeps_only_the_marker(monkeypatch):
    balanced = _touched(_sent(monkeypatch, "balanced", _conversation()))
    beast = _touched(_sent(monkeypatch, "beast", _conversation()))

    assert balanced and beast
    assert any("output line" in text for text in balanced)
    for text in beast:
        assert "output line" not in text
        assert re.search(r'id="old\d{4}"', text)
