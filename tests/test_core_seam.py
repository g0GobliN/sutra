"""The host-agnostic seam: one outcome, one process path, one restore call.

These are seam-level tests. Host translation is proved separately by
``test_claude_code_hook.py`` (hook protocol) and the existing Hermes/CLI/MCP
tests.
"""

import json
import os
import re
import subprocess
import sys

ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


def big():
    lines = [f"build line {i} nominal" for i in range(3000)]
    lines[1500] = "CLUE: seam-token-3M"
    return "\n".join(lines)


def test_outcome_reports_the_shared_contract_fields(sutra):
    outcome = sutra.process_text(big(), tool_name="terminal")
    assert outcome.changed is True
    assert outcome.marker is True
    assert outcome.id and ID_RE.fullmatch(f"id={outcome.id}")
    assert outcome.original_bytes > outcome.compressed_bytes
    assert "CLUE: seam-token-3M" not in outcome.output

    restored = json.loads(sutra.restore(
        {"id": outcome.id, "mode": "search", "query": "seam-token-3M", "context": 1}))
    assert restored["status"] == "ok"
    assert "seam-token-3M" in restored["content"]


def test_outcome_json_is_the_cli_mcp_contract(sutra):
    outcome = sutra.process_text(big(), tool_name="terminal")
    payload = sutra.outcome_json(outcome)
    assert set(payload) == {
        "changed", "output", "marker", "id", "original_bytes", "compressed_bytes"}
    assert payload["output"] == outcome.output
    assert payload["id"] == outcome.id
    assert payload["changed"] is True and payload["marker"] is True


def test_process_text_never_raises_and_returns_the_original(sutra, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("compressor down")

    monkeypatch.setattr(sutra.engine, "_compress_impl", boom)
    text = big()
    outcome = sutra.process_text(text)
    assert outcome.changed is False
    assert outcome.output == text
    assert outcome.original_bytes == outcome.compressed_bytes


def test_unencodable_text_is_never_replaced(sutra):
    """A lone surrogate must not become ``?`` or raise through the seam.

    JSON clients can send ``\\ud800``. Encoding that with ``errors="replace"``
    invented a different character and, for a small string, a fallback byte
    count raised ``UnicodeEncodeError`` out of ``process_text``.
    """
    for text in ("small\ud800 output", "head\ud800" + "x" * 30_000):
        outcome = sutra.process_text(text)
        assert outcome.output == text
        assert outcome.changed is False
        assert outcome.marker is False
        assert outcome.id is None
        assert outcome.original_bytes == outcome.compressed_bytes == 0

        direct, stats = sutra.compress(text)
        assert direct == text
        assert stats["changed"] is False
        assert stats["rule"] == "invalid-utf8-kept"


def test_process_result_bypasses_evidence_tools_by_name_and_namespace(sutra):
    payload = {"output": big()}
    for name in ("sutra_restore", "mcp__sutra__sutra_restore", "sutra_compress"):
        assert sutra.process_result(payload, tool_name=name) is payload, name
        assert sutra.should_bypass(name) is True


def test_process_text_honours_disabled_and_evidence_bypass(sutra, monkeypatch):
    text = big()
    monkeypatch.setenv("SUTRA_ENABLED", "0")
    outcome = sutra.process_text(text, tool_name="Bash")
    assert outcome.changed is False and outcome.output == text
    monkeypatch.delenv("SUTRA_ENABLED")
    for name in ("sutra_restore", "mcp__sutra__sutra_restore", "sutra_compress"):
        outcome = sutra.process_text(text, tool_name=name)
        assert outcome.changed is False and outcome.output == text, name


def test_process_result_preserves_identity_when_nothing_changes(sutra):
    small = "tiny output"
    assert sutra.process_result(small, tool_name="Bash") == small
    payload = {"output": "small", "exit_code": 1}
    assert sutra.process_result(payload, tool_name="Bash") is payload
    weird = ["a", "b"]
    assert sutra.process_result(weird, tool_name="Bash") is weird


def test_process_result_keeps_the_dict_shape_and_compresses_only_text(sutra):
    payload = {"stdout": big(), "stderr": "", "exit_code": 1, "ok": True}
    out = sutra.process_result(payload, tool_name="Bash")
    assert isinstance(out, dict)
    assert out["exit_code"] == 1 and out["ok"] is True and out["stderr"] == ""
    assert "[sutra:" in out["stdout"]
    assert "CLUE: seam-token-3M" not in out["stdout"]


def test_disabled_returns_the_same_object(sutra, monkeypatch):
    monkeypatch.setenv("SUTRA_ENABLED", "0")
    payload = {"output": big()}
    assert sutra.process_result(payload) is payload


def test_recovery_tool_is_never_recompressed_by_the_middleware(sutra):
    result = {"output": big()}
    assert sutra.tool_execution(
        next_call=lambda args: result, tool_name="sutra_restore", args={}) is result


def test_loader_is_shared_by_the_standalone_adapters():
    """The three transports must not carry three copies of the load path."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("sutra-cli.py", "sutra-mcp-server.py", "sutra-claude-code-hook.py"):
        source = open(os.path.join(root, "scripts", name), encoding="utf-8").read()
        assert "from _sutra import load_sutra" in source, name
        assert "importlib.util" not in source, name


def test_the_runner_handover_keeps_the_shared_loader_importable():
    """`sutra compress` reaches the script through runpy, not a direct run.

    Direct execution puts ``scripts/`` on ``sys.path``; ``runpy.run_path`` does
    not. The handover in ``runner.run_script`` is what keeps
    ``from _sutra import load_sutra`` working for the installed console
    commands, so run the real handover from a directory where only the package
    root is importable.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys\n"
        "sys.path.insert(0, '.')\n"  # the package root, never scripts/
        "import runner\n"
        "sys.exit(runner.run_script('sutra-cli.py', ['config'], missing='missing'))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=root,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["enabled"] is True
