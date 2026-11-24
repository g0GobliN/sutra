"""A local OpenAI-shaped model that asks for one tool call, then answers.

The point is to exercise a host's real session loop — its tool execution, its
adapter hook, its request assembly — on a machine with no provider credential.
The model is fake; the host, the tool, the hook and the request the host finally
sends are real. Nothing here claims a model-quality measurement.

Run it beside a host:

python3 scripts/ab/mock_provider.py --port 8899 --log /tmp/mock.jsonl
    opencode run "run the command" -m mock/mock-1      # provider baseURL -> http://127.0.0.1:8899/v1

The server is unauthenticated and binds to loopback only: run it on a machine
you trust, and stop it when the host loop is over. Every request is summarised
into the log (one JSON object per line): call number, whether the client
streamed, and per message its role, byte size, whether it carries a `[sutra:`
marker, and how many tool calls it holds. That record is what proves the adapter
rewrote the result the host sent back. The default log lives under
``~/.sutra/mock-provider/`` and is created owner-only.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import pathlib
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_BYTES = 36_000
DEFAULT_LOG = str(pathlib.Path.home() / ".sutra" / "mock-provider" / "requests.jsonl")


def _summary(payload: dict) -> list[dict]:
    rows = []
    for message in payload.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        else:
            text = content or ""
        rows.append(
            {
                "role": message.get("role"),
                "bytes": len(text.encode()),
                "marker": "[sutra:" in text,
                "tool_calls": len(message.get("tool_calls") or []),
            }
        )
    return rows


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "mock-1",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _answer(content: str = "done") -> dict:
    return {
        "id": "chatcmpl-mock-final",
        "object": "chat.completion",
        "model": "mock-1",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _tool_call(tool: str, arguments: str) -> dict:
    return {
        "id": "chatcmpl-mock-call",
        "object": "chat.completion",
        "model": "mock-1",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_mock_1",
                            "type": "function",
                            "function": {"name": tool, "arguments": arguments},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _append_private(path: str, payload: dict) -> None:
    """Append one record, creating the file owner-only on first use."""

    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flag = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    descriptor = os.open(target, flag, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def _reply(handler, *, payload: dict, command: str, tool: str) -> None:
    """Answer one request: ask for the tool call, or answer once its result is in."""

    saw_result = any(m.get("role") == "tool" for m in payload.get("messages", []))
    stream = bool(payload.get("stream"))
    if saw_result:
        if stream:
            handler.sse([_chunk({"role": "assistant", "content": "done"}), _chunk({}, "stop")])
            return
        handler.json(200, _answer())
        return
    arguments = json.dumps({"command": command, "description": "print a large result"})
    if stream:
        handler.sse(
            [
                _chunk({"role": "assistant", "content": ""}),
                _chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_mock_1",
                                "type": "function",
                                "function": {"name": tool, "arguments": ""},
                            }
                        ]
                    }
                ),
                _chunk({"tool_calls": [{"index": 0, "function": {"arguments": arguments}}]}),
                _chunk({}, "tool_calls"),
            ]
        )
        return
    handler.json(200, _tool_call(tool, arguments))


LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1"}


def _loopback(peer: str, host_header: str) -> bool:
    """Whether both the connecting peer and the Host header are loopback.

    The peer check is the one that matters: a page in a local browser can send
    any ``Host`` it likes, but it still connects from a loopback address on this
    machine, so the server is for a host launched beside it, not a public service.
    """

    name = host_header.rsplit(":", 1)[0].strip("[]").lower()
    return peer in LOOPBACK_NAMES and name in LOOPBACK_NAMES


def handler_for(*, log_path: str | None, command: str, tool: str):
    """Build a request handler closing over the recorder settings."""

    calls = itertools.count(1)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the console quiet under a host
            pass

        def json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def sse(self, events: list[dict]) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()
            for event in events:
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def do_GET(self) -> None:  # noqa: N802 - http.server names
            if self.path.endswith("/models"):
                self.json(
                    200,
                    {
                        "object": "list",
                        "data": [{"id": "mock-1", "object": "model", "owned_by": "sutra"}],
                    },
                )
                return
            self.json(404, {"error": {"message": "not found"}})

        def _body(self) -> dict | None:
            """Read the request payload, answering 400 when it is not JSON."""

            try:
                length = int(self.headers.get("content-length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(raw or b"{}")
            except ValueError:
                self.json(400, {"error": {"message": "invalid JSON body"}})
                return None
            return payload if isinstance(payload, dict) else {}

        def do_POST(self) -> None:  # noqa: N802 - http.server names
            if not _loopback(self.client_address[0], self.headers.get("host", "")):
                self.json(403, {"error": {"message": "loopback only"}})
                return
            payload = self._body()
            if payload is None:
                return
            if log_path:
                _append_private(
                    log_path,
                    {
                        "call": next(calls),
                        "stream": bool(payload.get("stream")),
                        "messages": _summary(payload),
                        "tools": [
                            (tool_spec.get("function") or {}).get("name")
                            for tool_spec in payload.get("tools", [])
                        ],
                    },
                )
            _reply(self, payload=payload, command=command, tool=tool)

    return Handler


def print_command(total_bytes: int = DEFAULT_BYTES) -> str:
    """A shell command whose output is one repeated line of about ``total_bytes``."""

    line = "line of output "
    repetitions = max(1, total_bytes // len(line))
    return f"python3 -c \"print('{line}' * {repetitions})\""


def serve(
    port: int = 0,
    *,
    log_path: str | None = None,
    command: str | None = None,
    tool: str = "bash",
) -> ThreadingHTTPServer:
    """Start the mock on ``port`` (0 picks a free one) and return the server.

    The caller owns the socket: run ``server.serve_forever()`` in a thread, or
    call it directly. ``server.server_address[1]`` is the bound port. ``log_path``
    defaults to None, so importing the module or starting a server from code
    never writes request summaries into the user's home; the CLI passes an
    explicit path (its own default lives under ``~/.sutra/mock-provider``).
    """

    handler = handler_for(log_path=log_path, command=command or print_command(), tool=tool)
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8899, help="port to listen on")
    parser.add_argument("--log", default=DEFAULT_LOG, help="request log (JSON Lines)")
    parser.add_argument("--bytes", type=int, default=DEFAULT_BYTES, help="tool output size")
    parser.add_argument("--tool", default="bash", help="tool name to call")
    args = parser.parse_args(argv)
    server = serve(args.port, log_path=args.log, command=print_command(args.bytes), tool=args.tool)
    host, port = server.server_address[:2]
    print(f"mock provider listening on http://{host}:{port}/v1 (log: {args.log})", flush=True)
    print("local, unauthenticated provider: stop it when the host loop is over.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("mock provider stopped", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
