"""Codex PostToolUse hook: replacement, recovery and fail-open behaviour.

The script is invoked exactly as Codex invokes a command hook: one JSON event on
stdin and one JSON object on stdout. This is hook-level proof. The real-host
proof (Codex 0.166.0 with a scripted Responses provider) is recorded in
docs/automatic-install.md; the second model request contained only the hook's
replacement text, not the original tool output.
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
HOOK = ROOT / "scripts" / "sutra-codex-hook.py"
ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


def big():
    lines = [f"build line {i} nominal" for i in range(3000)]
    lines[1500] = "CLUE: codex-hook-token"
    return "\n".join(lines)


def codex_truncated():
    """Codex's pre-hook shape: ~10 KB with its truncation header."""
    lines = [f"line {i}" for i in range(1400)]
    lines[700] = "CLUE: codex-truncated-token"
    body = "\n".join(lines)
    return ("Chunk ID: 012345\nWall time: 0.0000 seconds\n"
            "Process exited with code 0\nOriginal token count: 27224\nOutput:\n"
            "Warning: truncated output (original token count: 27224)\n"
            "Total output lines: 20000\n\n" + body)


def run_hook(payload=None, *, raw=None, env=None, timeout=60):
    data = raw if raw is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK)], input=data, capture_output=True, text=True,
        timeout=timeout, cwd=str(ROOT), env=env)


def run_hook_bytes(raw: bytes, env=None, timeout=60):
    return subprocess.run(
        [sys.executable, str(HOOK)], input=raw, capture_output=True,
        timeout=timeout, cwd=str(ROOT), env=env)


def test_a_large_bash_result_is_replaced_and_recoverable_through_mcp(sutra):
    result = run_hook({
        "hook_event_name": "PostToolUse", "session_id": "codex-1",
        "tool_name": "Bash", "tool_response": big(),
    })
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["continue"] is False
    assert "decision" not in response
    output = response["stopReason"]
    assert "[sutra:" in output
    assert "CLUE: codex-hook-token" not in output
    assert 'retrieve: sutra_restore(id="' in output
    match = ID_RE.search(output)
    assert match, output[:500]

    session = McpSession()
    try:
        session.request("initialize", request_id=1)
        restored = tool_text(session.call_tool(
            "sutra_restore",
            {"id": match.group(1), "mode": "search",
             "query": "codex-hook-token", "context": 1}))
        assert restored["status"] == "ok"
        assert "codex-hook-token" in restored["content"]
    finally:
        assert session.close() == 0


@pytest.mark.parametrize("response", [
    "tiny output",
    {"stdout": big()},          # Codex sends a string for Bash; never guess a shape
    ["a", "b"],
    None,
    42,
])
def test_small_and_non_string_results_are_untouched(response):
    result = run_hook({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                       "tool_response": response})
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


def test_codex_truncated_payload_is_eligible_under_the_codex_limits(sutra):
    text = codex_truncated()
    assert len(text.encode()) < 24_576, "fixture must reproduce Codex's pre-hook size"
    result = run_hook({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                       "tool_response": text})
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["continue"] is False
    output = response["stopReason"]
    assert "[sutra:" in output
    assert "codex-truncated-token" not in output
    assert len(output.encode()) < len(text.encode())
    assert ID_RE.search(output)


def test_a_non_shell_tool_is_never_rewritten():
    result = run_hook({"hook_event_name": "PostToolUse",
                       "tool_name": "apply_patch", "tool_response": big()})
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


@pytest.mark.parametrize("name", [
    "sutra_restore", "mcp__sutra__sutra_restore", "sutra_compress"])
def test_evidence_tools_are_never_recompressed(name):
    result = run_hook({"hook_event_name": "PostToolUse", "tool_name": name,
                       "tool_response": big()})
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


def test_malformed_input_fails_open():
    malformed = run_hook(raw="{oops")
    assert malformed.returncode == 0
    assert malformed.stdout.strip() == "{}"

    invalid_utf8 = b'{"tool_name":"Bash","tool_response":"' + b"x" * 30_000 + b'\xff"}'
    result = run_hook_bytes(invalid_utf8)
    assert result.returncode == 0
    assert result.stdout.strip() == b"{}"

    deep = '{"tool_response":' + "[" * 100_000 + "]" * 100_000 + "}"
    recursion = run_hook(raw=deep)
    assert recursion.returncode == 0
    assert recursion.stdout.strip() == "{}"


def test_a_second_pass_over_compressed_output_is_a_noop(sutra):
    first = run_hook({"tool_name": "Bash", "tool_response": big()})
    compressed = json.loads(first.stdout)["stopReason"]
    second = run_hook({"tool_name": "Bash", "tool_response": compressed})
    assert second.returncode == 0
    assert second.stdout.strip() == "{}"


def test_storage_failure_compresses_without_a_false_recovery_id(tmp_path):
    not_a_dir = tmp_path / "store-is-a-file"
    not_a_dir.write_text("occupied", encoding="utf-8")
    env = dict(os.environ, SUTRA_STORE_DIR=str(not_a_dir))
    result = run_hook({"tool_name": "Bash", "tool_response": big()}, env=env)
    assert result.returncode == 0
    output = json.loads(result.stdout)["stopReason"]
    assert "[sutra:" in output
    assert "original not retained" in output
    assert not ID_RE.search(output)
