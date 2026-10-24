"""Offline tests for the E2E harness's scripted-model turn machine.

``scripts/e2e/fake_model.py`` is the executable form of the integration
contract (compress -> marker -> tool_search -> bridge sutra_restore -> ok).
These tests walk its ``decide()`` state machine over synthetic request bodies,
so a harness regression fails here in CI without a Hermes installation.
"""

import importlib.util
import json
import pathlib
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HARNESS = ROOT / "scripts" / "e2e" / "fake_model.py"

CLUE = "CLUE: the magic word is umbrella-7f3a"
ENTRY_ID = "su_" + "0123456789abcdef" * 2
MARKER = (f'[sutra: partial · 20,548 bytes elided (large output) · field="/output" · '
          f"id={ENTRY_ID} · original 38,925 B retained 168h · "
          f'retrieve: sutra_restore(id="{ENTRY_ID}", field="/output", mode="bytes", '
          f"start=12253, end=32801)]")


@pytest.fixture()
def harness(tmp_path):
    spec = importlib.util.spec_from_file_location("sutra_fake_model", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.state.update({"verdict": None, "pending": {}, "seq": 0})
    cfg = types.SimpleNamespace(
        out_dir=str(tmp_path), clue=CLUE, query="umbrella-7f3a",
        command=f'{{ seq 1 4000; echo "{CLUE}"; seq 4002 8000; }}')
    return module, cfg


def terminal_tool():
    return [{"type": "function", "function": {
        "name": "terminal",
        "parameters": {"type": "object",
                       "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}}]


def tool_exchange(call_name: str, call_args: dict, call_id: str, result: str) -> list:
    return [
        {"role": "user", "content": "run"},
        {"role": "assistant", "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": call_name, "arguments": json.dumps(call_args)}}]},
        {"role": "tool", "tool_call_id": call_id, "content": result},
    ]


def test_fresh_conversation_issues_the_payload_command(harness):
    module, cfg = harness
    out = module.decide(cfg, {"messages": [{"role": "user", "content": "go"}],
                              "tools": terminal_tool()})
    assert out["tool_call"][1] == "terminal"
    assert out["tool_call"][2]["command"] == cfg.command
    assert cfg.clue in out["tool_call"][2]["command"]


def test_marker_result_triggers_a_tool_search(harness):
    module, cfg = harness
    messages = tool_exchange("terminal", {"command": "x"}, "call_x",
                             f'{{"output": "head\\n{MARKER}\\ntail"}}')
    out = module.decide(cfg, {"messages": messages, "tools": terminal_tool()})
    assert out["tool_call"][1] == "tool_search"
    assert "queries" in out["tool_call"][2]


def test_search_hit_issues_the_bridge_call_with_the_marker_id(harness):
    module, cfg = harness
    module.decide(cfg, {"messages": tool_exchange(
        "terminal", {"command": "x"}, "call_x", f'head\n{MARKER}\ntail'),
        "tools": terminal_tool()})
    search_result = json.dumps({"results": [{"name": "sutra_restore",
                                             "description": "retrieve omitted output"}]})
    messages = tool_exchange("terminal", {"command": "x"}, "call_x",
                             f"head\n{MARKER}\ntail") + tool_exchange(
        "tool_search", {"queries": ["restore"]}, "call_y", search_result)
    out = module.decide(cfg, {"messages": messages, "tools": terminal_tool()})
    name, args = out["tool_call"][1], out["tool_call"][2]
    assert name == "tool_call"
    entry = args["calls"][0]
    assert entry["name"] == "sutra_restore"
    assert entry["arguments"]["id"] == ENTRY_ID
    assert entry["arguments"]["mode"] == "search"


def test_bridge_result_judges_ok(harness):
    module, cfg = harness
    module.decide(cfg, {"messages": tool_exchange(
        "terminal", {"command": "x"}, "call_x", f"head\n{MARKER}\ntail"),
        "tools": terminal_tool()})
    search_result = json.dumps({"results": [{"name": "sutra_restore"}]})
    messages = tool_exchange("terminal", {"command": "x"}, "call_x",
                             f"head\n{MARKER}\ntail") + tool_exchange(
        "tool_search", {"queries": ["restore"]}, "call_y", search_result)
    module.decide(cfg, {"messages": messages, "tools": terminal_tool()})
    restore = json.dumps({"status": "ok", "content_note": "untrusted",
                          "content": f"--- lines 1500-1501 ---\n{CLUE}\n"})
    messages += tool_exchange("tool_call", {"calls": [{"name": "sutra_restore",
                                                       "arguments": {"id": ENTRY_ID}}]},
                              "call_z", restore)
    out = module.decide(cfg, {"messages": messages, "tools": terminal_tool()})
    assert out["content"].startswith("E2E-SUTRA-OK")
    assert module.state["verdict"] == {
        "ok": True, "marker_seen": True, "clue_visible_in_compressed": False,
        "clue_recovered": True, "restore_status": "ok", "search_found_tool": True}


def test_missing_marker_fails_the_loop(harness):
    module, cfg = harness
    big = "\n".join(f"line {i}" for i in range(4000))
    out = module.decide(cfg, {"messages": tool_exchange(
        "terminal", {"command": "x"}, "call_x", big), "tools": terminal_tool()})
    assert out["content"] == "E2E-SUTRA-FAIL: no SUTRA marker on an oversized result"
    assert module.state["verdict"]["ok"] is False


def test_search_miss_fails_the_loop(harness):
    module, cfg = harness
    module.decide(cfg, {"messages": tool_exchange(
        "terminal", {"command": "x"}, "call_x", f"head\n{MARKER}\ntail"),
        "tools": terminal_tool()})
    messages = tool_exchange("terminal", {"command": "x"}, "call_x",
                             f"head\n{MARKER}\ntail") + tool_exchange(
        "tool_search", {"queries": ["restore"]}, "call_y", '{"results": []}')
    out = module.decide(cfg, {"messages": messages, "tools": terminal_tool()})
    assert out["content"] == "E2E-SUTRA-FAIL: tool_search did not list sutra_restore"


def test_marker_without_id_fails_closed(harness):
    module, cfg = harness
    not_retained = ('[sutra: partial · 20,548 bytes elided (large output) · '
                    "original not retained · re-run the tool with a narrower "
                    'query (grep/head/offset) to see the middle]')
    out = module.decide(cfg, {"messages": tool_exchange(
        "terminal", {"command": "x"}, "call_x", f"head\n{not_retained}\ntail"),
        "tools": terminal_tool()})
    assert out["content"].startswith("E2E-SUTRA-FAIL")
    assert "not retained" in module.state["verdict"]["reason"]
