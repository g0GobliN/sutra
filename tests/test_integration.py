"""Contract tests: the Hermes middleware chain, plugin registration, profile scoping.

The chain below mirrors ``hermes_cli.middleware._run_execution_chain`` (v0.22.2):
the callback receives ``tool_name``/``args`` plus ``next_call``, and it is the
callback's *return value* that becomes the tool result. ``next_call`` is
single-use per frame. Keeping this local means the plugin's assumptions are
tested without importing the Hermes runtime.
"""

import json
import os
import re

import pytest

from .test_sutra import big

ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


def run_tool_chain(callbacks, terminal, *, tool_name="terminal", args=None, **context):
    def call_at(index, payload):
        if index >= len(callbacks):
            return terminal(payload)
        callback = callbacks[index]
        used = {"count": 0}

        def next_call(next_payload=None):
            if used["count"]:
                raise RuntimeError("next_call() called more than once")
            used["count"] += 1
            return call_at(index + 1, payload if next_payload is None else next_payload)

        call_kwargs = dict(context, tool_name=tool_name, args=payload,
                           original_args=args, next_call=next_call)
        return callback(**call_kwargs)

    return call_at(0, args)


class FakeCtx:
    def __init__(self):
        self.middleware = []
        self.tools = []

    def register_middleware(self, kind, callback):
        self.middleware.append((kind, callback))

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


def test_register_wires_middleware_and_recovery_tool(sutra):
    ctx = FakeCtx()
    sutra.register(ctx)
    assert [kind for kind, _ in ctx.middleware] == ["tool_execution"]
    assert len(ctx.tools) == 1
    tool = ctx.tools[0]
    assert tool["name"] == "sutra_restore"
    assert tool["name"] == tool["schema"]["name"]
    assert tool["toolset"] == "sutra"
    assert tool["schema"]["parameters"]["required"] == ["id"]
    assert "id" in tool["schema"]["parameters"]["properties"]
    assert callable(tool["handler"])


def test_registration_matches_the_manifest(sutra):
    """plugin.yaml must declare exactly what register() provides."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifest = open(os.path.join(root, "plugin.yaml"), encoding="utf-8").read()
    ctx = FakeCtx()
    sutra.register(ctx)
    assert "tool_execution" in manifest
    for tool in ctx.tools:
        assert f"- {tool['name']}" in manifest
    version = re.search(r'^version:\s*"([^"]+)"', manifest, re.M).group(1)
    assert version == sutra.__version__


def test_middleware_compresses_inside_a_real_chain(sutra):
    seen = {}

    def terminal(args):
        seen["args"] = args
        return {"output": big(), "exit_code": 1}

    def rewrite_args(**kwargs):
        rewritten = dict(kwargs["args"], injected=True)
        return kwargs["next_call"](rewritten)

    result = run_tool_chain([rewrite_args, sutra.tool_execution], terminal,
                            args={"cmd": "make"}, session_id="s-1")
    assert seen["args"] == {"cmd": "make", "injected": True}
    assert isinstance(result, dict)
    assert result["exit_code"] == 1
    assert "sutra:" in result["output"]


def test_middleware_calls_next_exactly_once(sutra):
    def terminal(args):
        return {"output": big()}

    result = run_tool_chain([sutra.tool_execution], terminal, args={})
    assert "sutra:" in result["output"]


def test_downstream_errors_are_not_swallowed(sutra):
    def terminal(args):
        raise RuntimeError("tool exploded")

    with pytest.raises(RuntimeError, match="tool exploded"):
        run_tool_chain([sutra.tool_execution], terminal, args={})


def test_profiles_do_not_share_the_store(sutra, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-a"))
    stored = sutra.compress_result({"output": big()})["output"]
    entry_id = ID_RE.search(stored).group(1)

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-b"))
    assert json.loads(sutra.recovery.restore({"id": entry_id}))["status"] == "not_found"

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-a"))
    again = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "lines", "start": 1, "end": 1}))
    assert again["status"] == "ok"


def test_stats_live_under_the_active_home(sutra, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    assert sutra.stats_path() == str(tmp_path / "home" / "cache" / "sutra-stats.jsonl")


def test_store_dir_can_be_moved(sutra, tmp_path, monkeypatch):
    target = tmp_path / "elsewhere"
    monkeypatch.setenv("SUTRA_STORE_DIR", str(target))
    sutra.compress_result({"output": big()})
    assert target.is_dir()


def test_end_to_end_journey(sutra):
    """Compress a failing test log, then recover the buried failure by id."""
    log = "\n".join(
        f"{'PASS' if i % 500 else 'FAIL'} src/module_{i}.test.ts > case {i}"
        for i in range(6000))
    result = sutra.compress_result({"output": log, "exit_code": 1}, tool_name="terminal")
    visible = result["output"]
    assert "FAIL src/module_0.test.ts" in visible
    assert "FAIL src/module_3000.test.ts" not in visible, "the middle should be elided"
    entry_id = ID_RE.search(visible).group(1)

    found = json.loads(sutra.recovery.restore(
        {"id": entry_id, "mode": "search", "query": "FAIL src/module_3000", "context": 2}))
    assert found["status"] == "ok"
    assert "FAIL src/module_3000.test.ts" in found["content"]
    # Exit code is machine-readable state: it must survive untouched.
    assert result["exit_code"] == 1
