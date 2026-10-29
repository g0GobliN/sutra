"""Hook-protocol tests for the Claude Code PostToolUse integration.

The script is invoked exactly as Claude Code invokes a shell hook: one JSON
event on stdin, one JSON object on stdout. No live Claude Code session is
started, so this is **hook-level** proof, not real-host proof. The host API
being exercised is the documented ``PostToolUse`` contract
(``hookSpecificOutput.updatedToolOutput``).

Recovery is exercised through the existing MCP server, the path a Claude Code
user actually has, not by calling the recovery function in-process.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_mcp_adapter import McpSession, tool_text

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "sutra-claude-code-hook.py"
ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


def big():
    lines = [f"build line {i} nominal" for i in range(3000)]
    lines[1500] = "CLUE: hook-token-5K"
    return "\n".join(lines)


def run_hook(payload=None, *, raw=None, env=None, timeout=60):
    data = raw if raw is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK)], input=data, capture_output=True, text=True,
        timeout=timeout, cwd=str(ROOT), env=env)


def updated(result):
    response = json.loads(result.stdout)
    return response["hookSpecificOutput"]["updatedToolOutput"]


def test_large_result_is_replaced_and_recoverable_through_mcp(sutra):
    result = run_hook({
        "hook_event_name": "PostToolUse", "session_id": "s-1",
        "tool_name": "Bash", "tool_response": big(),
    })
    assert result.returncode == 0, result.stderr
    output = updated(result)
    assert isinstance(output, str)
    assert "[sutra:" in output
    assert "CLUE: hook-token-5K" not in output
    assert 'retrieve: sutra_restore(id="' in output
    assert 'mode="bytes"' in output
    match = ID_RE.search(output)
    assert match, output[:500]

    session = McpSession()
    try:
        session.request("initialize", request_id=1)
        response = tool_text(session.call_tool(
            "sutra_restore",
            {"id": match.group(1), "mode": "search",
             "query": "hook-token-5K", "context": 1}))
        assert response["status"] == "ok"
        assert "hook-token-5K" in response["content"]
        assert response["content_note"]
    finally:
        assert session.close() == 0


def test_dict_tool_response_keeps_its_shape_and_recovers(sutra):
    result = run_hook({
        "tool_name": "Bash",
        "tool_response": {"stdout": big(), "stderr": "", "exit_code": 1,
                          "interrupted": False},
    })
    assert result.returncode == 0, result.stderr
    output = updated(result)
    assert isinstance(output, dict)
    assert output["exit_code"] == 1
    assert output["interrupted"] is False
    assert output["stderr"] == ""
    assert "[sutra:" in output["stdout"]
    assert "CLUE: hook-token-5K" not in output["stdout"]
    match = ID_RE.search(output["stdout"])
    assert match

    session = McpSession()
    try:
        session.request("initialize", request_id=1)
        response = tool_text(session.call_tool(
            "sutra_restore",
            {"id": match.group(1), "mode": "search", "query": "hook-token-5K"}))
        assert response["status"] == "ok"
        assert "hook-token-5K" in response["content"]
    finally:
        assert session.close() == 0


@pytest.mark.parametrize("tool_response", [
    "tiny output",
    "diff --git a/x b/x\n@@ -1 +1 @@\n" + "+line\n" * 3000,
    ["a", "b"],
    {"count": 1},
    None,
    42,
])
def test_small_protected_and_non_text_results_are_untouched(tool_response):
    result = run_hook({"tool_name": "Bash", "tool_response": tool_response})
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"
    assert "updatedToolOutput" not in result.stdout


def test_second_pass_over_already_compressed_output_is_a_noop(sutra):
    first = run_hook({"tool_name": "Bash", "tool_response": big()})
    compressed = updated(first)
    assert "[sutra:" in compressed

    second = run_hook({"tool_name": "Bash", "tool_response": compressed})
    assert second.returncode == 0
    assert second.stdout.strip() == "{}"


@pytest.mark.parametrize("name", [
    "sutra_restore", "mcp__sutra__sutra_restore", "sutra_compress"])
def test_evidence_tools_are_never_recompressed(name):
    result = run_hook({"tool_name": name, "tool_response": big()})
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


def test_malformed_and_unexpected_inputs_fail_open():
    malformed = run_hook(raw="{oops")
    assert malformed.returncode == 0
    assert malformed.stdout.strip() == "{}"

    # RecursionError from json.loads is an unexpected exception, not a shape
    # the hook should ever turn into a broken tool result.
    deep = '{"tool_response":' + "[" * 100_000 + "]" * 100_000 + "}"
    recursion = run_hook(raw=deep)
    assert recursion.returncode == 0
    assert recursion.stdout.strip() == "{}"

    missing = run_hook({"tool_name": "Bash"})
    assert missing.returncode == 0
    assert missing.stdout.strip() == "{}"


def test_storage_failure_compresses_without_a_false_recovery_id(tmp_path):
    not_a_dir = tmp_path / "store-is-a-file"
    not_a_dir.write_text("occupied", encoding="utf-8")
    env = dict(os.environ, SUTRA_STORE_DIR=str(not_a_dir))
    result = run_hook({"tool_name": "Bash", "tool_response": big()}, env=env)
    assert result.returncode == 0
    output = updated(result)
    assert "[sutra:" in output
    assert "original not retained" in output
    assert not ID_RE.search(output)
