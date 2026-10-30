#!/usr/bin/env python3
"""SUTRA's MCP (Model Context Protocol) server adapter, stdio transport.

One adapter for every MCP client — Claude Code, Cursor, Codex-style agents,
OpenCode, and any other client that speaks MCP over stdio. Hermes users do
not need this; the plugin's native middleware and ``sutra_restore`` tool
cover that host.

Protocol: newline-delimited JSON-RPC 2.0 on stdin/stdout (the MCP stdio
transport). Implemented against the MCP 2024-11-05 revision using only the
standard library — no SDK dependency. Two tools, matching the CLI adapter's
contract exactly (docs/integrations.md):

    sutra_compress  {tool?: string, output: string}
        -> {changed, output, marker, id, original_bytes, compressed_bytes}
    sutra_restore   the sutra_restore protocol arguments verbatim
        -> the recovery JSON envelope (status: ok | not_found | expired |
           too_large | unavailable | error)

Semantics:
  - An explicit recovery status is a successful tool execution (isError false);
    "error" with a message is how the protocol reports a bad request.
  - Compression failures and oversized inputs return isError true with the
    reason in the text payload — never a silent success.
  - Deterministic: no timestamps or random values in tool results.
  - Storage isolation follows HERMES_HOME / SUTRA_STORE_DIR, exactly like the
    Hermes plugin; one server process serves one profile.
  - Lifecycle: EOF on stdin ends the server cleanly; no background threads,
    so shutdown cannot corrupt the store.

Run (a client normally spawns this itself):
    python3 scripts/sutra-mcp-server.py < messages.jsonl
"""

from __future__ import annotations

import json
import sys

from _sutra import load_sutra

PROTOCOL_VERSION = "2024-11-05"
MAX_LINE_BYTES = 70 * 1024 * 1024  # engine cap (64 MiB) plus envelope room

COMPRESS_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string",
                 "description": "Tool name recorded with the entry (optional)."},
        "output": {"type": "string",
                   "description": "The complete tool output to compress."},
    },
    "required": ["output"],
    "additionalProperties": False,
}


def _tool_definitions(sutra) -> list[dict]:
    restore_schema = dict(sutra.recovery.TOOL_SCHEMA["parameters"])
    return [
        {"name": "sutra_compress",
         "description": ("Deterministically compress an oversized tool output. "
                         "Returns the compressed text with a marker naming what "
                         "was omitted and how to retrieve it."),
         "inputSchema": COMPRESS_SCHEMA},
        {"name": "sutra_restore",
         "description": sutra.recovery.TOOL_SCHEMA["description"],
         "inputSchema": restore_schema},
    ]


def _result(request_id, content_text: str, isError: bool = False) -> str:
    # ASCII escapes keep the JSON-RPC wire valid for a lone surrogate in an
    # id or a tool result, and on a host whose stdout is not UTF-8.
    return json.dumps({
        "jsonrpc": "2.0", "id": request_id,
        "result": {"content": [{"type": "text", "text": content_text}],
                   "isError": isError},
    }, ensure_ascii=True)


def _error(request_id, code: int, message: str) -> str:
    return json.dumps({
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": code, "message": message},
    }, ensure_ascii=True)


def _compress(sutra, arguments: dict) -> str:
    text = arguments.get("output")
    if not isinstance(text, str):
        return json.dumps({"error": "output must be a string"})
    outcome = sutra.core.process_text(
        text, tool_name=str(arguments.get("tool") or "")[:64])
    return json.dumps(sutra.core.outcome_json(outcome), ensure_ascii=True)


def handle(sutra, line: str) -> str | None:
    """One request line -> one response line (None for notifications)."""
    try:
        message = json.loads(line)
    except ValueError:
        return _error(None, -32700, "parse error: not valid JSON")
    if not isinstance(message, dict):
        return _error(None, -32600, "invalid request: expected an object")
    request_id = message.get("id")
    method = message.get("method")
    if method is None:
        return _error(request_id, -32600, "invalid request: missing method")

    if method == "initialize":
        return json.dumps({
            "jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sutra", "version": sutra.__version__},
            }})
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notifications take no response
    if method == "ping":
        return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {}})
    if method == "tools/list":
        return json.dumps({"jsonrpc": "2.0", "id": request_id,
                           "result": {"tools": _tool_definitions(sutra)}})
    if method == "tools/call":
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, -32602, "invalid params: expected an object")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "invalid arguments: expected an object")
        if name == "sutra_compress":
            if not isinstance(arguments.get("output"), str):
                return _result(request_id, json.dumps(
                    {"error": "output must be a string"}), isError=True)
            return _result(request_id, _compress(sutra, arguments))
        if name == "sutra_restore":
            if not isinstance(arguments.get("id"), str) or not arguments.get("id"):
                return _result(request_id, json.dumps(
                    {"status": "error",
                     "error": "missing required argument: id"}), isError=True)
            return _result(request_id, sutra.core.restore(arguments))
        return _error(request_id, -32602, f"unknown tool: {name}")
    return _error(request_id, -32601, f"method not found: {method}")


def main() -> int:
    sutra = load_sutra()
    # Read bytes, not text: the text wrapper decodes ahead, so one invalid byte
    # used to raise before the first valid request was even handled. The
    # protocol is UTF-8; an invalid line is one request's error, not a dead
    # server.
    for raw in sys.stdin.buffer:
        if len(raw) > MAX_LINE_BYTES:
            print(_error(None, -32600,
                         f"request exceeds {MAX_LINE_BYTES:,} bytes"), flush=True)
            continue
        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            print(_error(None, -32700, "parse error: input is not valid UTF-8"),
                  flush=True)
            continue
        if not line:
            continue
        try:
            response = handle(sutra, line)
        except Exception as exc:  # one malformed message must never kill the loop
            response = _error(None, -32603, f"internal error: {type(exc).__name__}")
        if response is not None:
            print(response, flush=True)
    return 0  # EOF: clean shutdown


if __name__ == "__main__":
    raise SystemExit(main())
