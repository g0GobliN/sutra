"""Hook-protocol tests for the Claude Code PostToolUse integration.

The script is invoked exactly as Claude Code invokes a shell hook: one JSON
event on stdin, one JSON object on stdout. No live Claude Code session is
started, so this is **hook-level** proof, not real-host proof. The host API
being exercised is the documented ``PostToolUse`` contract
(``hookSpecificOutput.updatedToolOutput``).

Recovery is exercised through the existing MCP server, the path a Claude Code
user actually has, not by calling the recovery function in-process.
"""

import importlib
import io
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


def run_hook_bytes(raw: bytes, env=None, timeout=60):
    """Invoke the hook with bytes so an invalid UTF-8 envelope is possible."""
    return subprocess.run(
        [sys.executable, str(HOOK)], input=raw, capture_output=True,
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

    # The hook envelope is UTF-8 JSON. Replacing an invalid byte could turn a
    # corrupt event into a valid one and rewrite the wrong tool result.
    invalid_utf8 = b'{"tool_name":"Bash","tool_response":"' + b"x" * 30_000 + b'\xff"}'
    result = run_hook_bytes(invalid_utf8)
    assert result.returncode == 0
    assert result.stdout.strip() == b"{}"

    # A lone surrogate is not UTF-8 encodable. It must stay untouched rather
    # than become "?" or crash the hook.
    surrogate = ('{"tool_name":"Bash","tool_response":"head\\ud800'
                 + "x" * 30_000 + '"}').encode()
    result = run_hook_bytes(surrogate)
    assert result.returncode == 0
    assert result.stdout.strip() == b"{}"


def test_a_surrogate_in_one_field_does_not_block_a_compressible_field():
    payload = {
        "tool_name": "Bash",
        "tool_response": {"stdout": big(), "stderr": "warning\ud800"},
    }
    result = run_hook(payload)
    assert result.returncode == 0, result.stderr
    output = updated(result)
    assert "[sutra:" in output["stdout"]
    assert output["stderr"] == "warning\ud800"

    # RecursionError from json.loads is an unexpected exception, not a shape
    # the hook should ever turn into a broken tool result.
    deep = '{"tool_response":' + "[" * 100_000 + "]" * 100_000 + "}"
    recursion = run_hook(raw=deep)
    assert recursion.returncode == 0
    assert recursion.stdout.strip() == "{}"

    missing = run_hook({"tool_name": "Bash"})
    assert missing.returncode == 0
    assert missing.stdout.strip() == "{}"


def test_the_installed_console_command_runs_the_same_hook(sutra, monkeypatch, capsys):
    """`sutra-hook` is the installed user's path to this adapter.

    Without it the only way to wire Claude Code was an absolute path into a
    clone. The command must run the same bundled script, not a copy of it.
    """
    hook = importlib.import_module(f"{sutra.__name__}.hook")
    payload = json.dumps({"tool_name": "Bash", "tool_response": big()})
    monkeypatch.setattr(sys, "stdin",
                        io.TextIOWrapper(io.BytesIO(payload.encode("utf-8"))))
    monkeypatch.setattr(sys, "argv", sys.argv)

    with pytest.raises(SystemExit) as stopped:
        hook.main()

    assert stopped.value.code == 0
    response = json.loads(capsys.readouterr().out)
    output = response["hookSpecificOutput"]["updatedToolOutput"]
    assert "[sutra:" in output
    assert "CLUE: hook-token-5K" not in output


def persisted_payload(tmp_path, *, full=None, declared=None, tool="Bash",
                     link=False, transcript_root=None):
    """The event Claude Code sends when it persists a large Bash result.

    ``stdout`` is the host's 30,000-character preview; the complete text is in
    ``persistedOutputPath``. ``transcript_path`` is the JSONL sibling of the
    session directory that owns ``tool-results``.
    """
    full = big() if full is None else full
    root = transcript_root or (tmp_path / "projects" / "proj")
    session = root / "session"
    results = session / "tool-results"
    results.mkdir(parents=True, exist_ok=True)
    transcript = root / "session.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    path = results / "result.txt"
    path.write_text(full, encoding="utf-8")
    if link:
        target = path.with_suffix(".real")
        path.rename(target)
        path.symlink_to(target)
    return {
        "hook_event_name": "PostToolUse", "session_id": "sess-persisted",
        "tool_name": tool, "transcript_path": str(transcript),
        "tool_response": {
            "stdout": full[:29_999], "stderr": "", "interrupted": False,
            "isImage": False, "noOutputExpected": False,
            "persistedOutputPath": str(path),
            "persistedOutputSize": len(full.encode()) if declared is None else declared,
        },
    }


def test_a_persisted_result_is_compressed_from_the_full_file(sutra, tmp_path):
    payload = persisted_payload(tmp_path)
    result = run_hook(payload)
    assert result.returncode == 0, result.stderr
    output = updated(result)
    assert isinstance(output, dict)
    assert "persistedOutputPath" not in output
    assert "persistedOutputSize" not in output
    assert output["stdout"].startswith("build line 0 nominal")
    assert "[sutra:" in output["stdout"]
    assert "CLUE: hook-token-5K" not in output["stdout"]
    assert f"original {len(big().encode()):,} B" in output["stdout"]
    match = ID_RE.search(output["stdout"])
    assert match, output["stdout"][:500]

    session = McpSession()
    try:
        session.request("initialize", request_id=1)
        restored = tool_text(session.call_tool(
            "sutra_restore",
            {"id": match.group(1), "mode": "search", "query": "hook-token-5K"}))
        assert restored["status"] == "ok"
        assert "hook-token-5K" in restored["content"]
    finally:
        assert session.close() == 0


def test_a_persisted_result_that_disagrees_with_its_size_is_left_alone(tmp_path):
    payload = persisted_payload(tmp_path, declared=len(big().encode()) + 1)
    result = run_hook(payload)
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


def test_a_symlinked_persisted_result_is_never_followed(tmp_path):
    payload = persisted_payload(tmp_path, link=True)
    result = run_hook(payload)
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


def test_a_persisted_path_outside_the_transcript_tree_is_refused(tmp_path):
    outside = tmp_path / "elsewhere" / "tool-results"
    outside.mkdir(parents=True)
    (outside / "result.txt").write_text(big(), encoding="utf-8")
    payload = persisted_payload(tmp_path, transcript_root=tmp_path / "projects" / "other")
    payload["tool_response"]["persistedOutputPath"] = str(outside / "result.txt")
    result = run_hook(payload)
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


def test_persisted_metadata_from_a_non_bash_tool_is_not_read(tmp_path):
    payload = persisted_payload(tmp_path, tool="Read")
    result = run_hook(payload)
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


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
