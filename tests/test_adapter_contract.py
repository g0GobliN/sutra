"""One fixture, three surfaces: the engine, the CLI adapter, the MCP adapter.

The generic adapters must not change the core's behavior: with the per-call
recovery id normalized away (each surface retains its own original, so the
ids legitimately differ), the compressed output and every contract field are
byte-identical across surfaces, and each surface's own id round-trips to the
exact original. This is the equivalence the adapter contract promises
(docs/integrations.md).
"""

import json
import re
from pathlib import Path

# Tolerant of both layouts while PR #17 (de-packaged tests) is pending:
# before it merges tests/ is a package (relative import); after, plain.
try:
    from .test_cli_adapter import SHARED_PAYLOAD, run_cli  # type: ignore
    from .test_mcp_adapter import McpSession, tool_text  # type: ignore
except ImportError:
    from test_cli_adapter import SHARED_PAYLOAD, run_cli
    from test_mcp_adapter import McpSession, tool_text

ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"su_[0-9a-f]{32}")


def _engine_result(sutra):
    out = sutra.compress_result(SHARED_PAYLOAD, tool_name="equivalence")
    return {"changed": out != SHARED_PAYLOAD, "output": out,
            "marker": "[sutra:" in out,
            "id": (ID_RE.search(out).group(0) if ID_RE.search(out) else None)}


def _cli_result():
    result = run_cli(["compress", "--tool", "equivalence"], stdin_text=SHARED_PAYLOAD)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _mcp_result():
    session = McpSession()
    try:
        session.request("initialize", request_id=1)
        return tool_text(session.call_tool("sutra_compress",
                                           {"tool": "equivalence", "output": SHARED_PAYLOAD}))
    finally:
        session.close()


def test_engine_cli_and_mcp_produce_identical_compression(sutra):
    surfaces = {"engine": _engine_result(sutra), "cli": _cli_result(), "mcp": _mcp_result()}
    normalized = {
        name: {"changed": r["changed"], "marker": r["marker"],
               "output": ID_RE.sub("su_<id>", r["output"]),
               "original_bytes": len(SHARED_PAYLOAD.encode()),
               "compressed_bytes": len(r["output"].encode())}
        for name, r in surfaces.items()}
    engine = normalized["engine"]
    for name, payload in normalized.items():
        assert payload == engine, f"{name} diverges from the engine contract"
    assert engine["changed"] is True and engine["marker"] is True
    assert engine["compressed_bytes"] < engine["original_bytes"]


def test_each_surface_restores_the_exact_original(sutra):
    results = {"engine": _engine_result(sutra), "cli": _cli_result(), "mcp": _mcp_result()}
    for name, payload in results.items():
        assert payload["id"], name
        if name == "engine":
            response = json.loads(sutra.recovery.restore(
                {"id": payload["id"], "mode": "full", "max_bytes": 65536}))
        elif name == "cli":
            response = json.loads(run_cli(
                ["restore", "--id", payload["id"], "--mode", "full",
                 "--max-bytes", "65536"]).stdout)
        else:
            session = McpSession()
            try:
                session.request("initialize", request_id=1)
                response = tool_text(session.call_tool(
                    "sutra_restore",
                    {"id": payload["id"], "mode": "full", "max_bytes": 65536}))
            finally:
                session.close()
        assert response["status"] == "ok", (name, response.get("error"))
        assert response["content"] == SHARED_PAYLOAD
        assert response["sha256_verified"] is True
