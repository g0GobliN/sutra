"""The MCP adapter: the shared tool contract over the real stdio protocol.

Every test spawns ``scripts/sutra-mcp-server.py`` as a subprocess and speaks
newline-delimited JSON-RPC 2.0 through its actual stdin/stdout — the same
transport a real MCP client uses. No SDK, no mocking.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "sutra-mcp-server.py"


class McpSession:
    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            bufsize=1, cwd=str(ROOT))

    def send(self, payload: dict) -> None:
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def receive(self) -> dict:
        line = self.proc.stdout.readline()
        assert line, "server closed stdout unexpectedly"
        return json.loads(line)

    def request(self, method: str, params=None, request_id=None) -> dict:
        self.send({"jsonrpc": "2.0", "id": request_id if request_id is not None else method,
                   "method": method, "params": params or {}})
        while True:
            response = self.receive()
            if response.get("id") == (request_id if request_id is not None else method):
                return response

    def call_tool(self, name: str, arguments: dict, request_id="call") -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments},
                            request_id=request_id)

    def close(self) -> int:
        self.proc.stdin.close()
        rest = self.proc.stderr.read()
        assert not rest, f"unexpected stderr: {rest[:400]}"
        return self.proc.wait(timeout=30)


def tool_text(response: dict) -> dict:
    assert "result" in response, response
    content = response["result"]["content"]
    assert content[0]["type"] == "text"
    return json.loads(content[0]["text"])


PAYLOAD = "\n".join(
    ["mcp fixture line %d with stable padding" % i for i in range(1000)]
    [:500] + ["CLUE: mcp-equivalence-token-9Z"]
    + ["mcp fixture line %d with stable padding" % i for i in range(500, 1000)]) + "\n"


def test_initialize_lists_both_tools():
    session = McpSession()
    try:
        init = session.request("initialize", request_id=1)
        assert init["result"]["protocolVersion"] == "2024-11-05"
        assert init["result"]["serverInfo"]["name"] == "sutra"
        assert init["result"]["serverInfo"]["version"]
        tools = session.request("tools/list", request_id=2)["result"]["tools"]
        names = [tool["name"] for tool in tools]
        assert names == ["sutra_compress", "sutra_restore"]
        assert "output" in tools[0]["inputSchema"]["required"]
        assert tools[1]["inputSchema"]["required"] == ["id"]
    finally:
        assert session.close() == 0


def test_compress_then_restore_recovers_the_buried_clue():
    session = McpSession()
    try:
        session.request("initialize", request_id=1)
        compressed = tool_text(session.call_tool("sutra_compress", {"output": PAYLOAD}))
        assert compressed["changed"] is True
        assert compressed["marker"] is True
        assert compressed["id"]
        assert "mcp-equivalence-token-9Z" not in compressed["output"]

        restored = tool_text(session.call_tool(
            "sutra_restore",
            {"id": compressed["id"], "mode": "search",
             "query": "mcp-equivalence-token-9Z", "context": 1}))
        assert restored["status"] == "ok"
        assert "mcp-equivalence-token-9Z" in restored["content"]
        # An explicit recovery status is a successful execution, not isError.
        assert session.call_tool(
            "sutra_restore", {"id": compressed["id"]},
            request_id="r2")["result"]["isError"] is False
    finally:
        assert session.close() == 0


def test_bad_requests_are_isError_not_protocol_errors():
    session = McpSession()
    try:
        session.request("initialize", request_id=1)
        response = session.call_tool("sutra_compress", {"output": 42}, request_id="bad")
        assert response["result"]["isError"] is True
        assert "error" in tool_text(response)
        missing = session.call_tool("sutra_restore", {}, request_id="missing")
        assert missing["result"]["isError"] is True
        assert tool_text(missing)["status"] == "error"
    finally:
        assert session.close() == 0


def test_unknown_tool_and_method_are_protocol_errors():
    session = McpSession()
    try:
        session.request("initialize", request_id=1)
        unknown_tool = session.call_tool("nope", {}, request_id="u1")
        assert unknown_tool["error"]["code"] == -32602
        unknown_method = session.request("resources/list", request_id="u2")
        assert unknown_method["error"]["code"] == -32601
    finally:
        assert session.close() == 0


def test_parse_error_and_notifications():
    session = McpSession()
    try:
        session.proc.stdin.write("this is not json\n")
        session.proc.stdin.flush()
        response = session.receive()
        assert response["error"]["code"] == -32700
        session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})  # no response
        session.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                      "params": {"requestId": "x"}})
        pong = session.request("ping", request_id="p1")
        assert pong["result"] == {}
    finally:
        assert session.close() == 0


def test_eof_shuts_down_cleanly_after_work():
    session = McpSession()
    session.request("initialize", request_id=1)
    tool_text(session.call_tool("sutra_compress", {"output": "small"}))
    assert session.close() == 0
