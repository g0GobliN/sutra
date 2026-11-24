"""A local OpenAI-shaped model that asks for one tool call, then answers.

The point is to exercise a host's real session loop — its tool execution, its
adapter hook, its request assembly — on a machine with no provider credential.
The model is fake; the host, the tool, the hook and the request the host finally
sends are real. Nothing here claims a model-quality measurement.

Run it beside a host:

    python3 scripts/ab/mock_provider.py --port 8899 --log /tmp/mock.jsonl
    opencode run "run the command" -m mock/mock-1      # provider baseURL -> http://127.0.0.1:8899/v1

Every request is summarised into the log (one JSON object per line): call
number, whether the client streamed, and per message its role, byte size,
whether it carries a `[sutra:` marker, and how many tool calls it holds. That
record is what proves the adapter rewrote the result the host sent back.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_BYTES = 36_000
DEFAULT_LOG = "/tmp/sutra-mock-provider.jsonl"


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


def handler_for(*, log_path: str | None, command: str, tool: str):
    """Build a request handler closing over the recorder settings."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        calls = 0

        def log_message(self, *args):  # keep the console quiet under a host
            pass

        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _events(self, chunk: dict) -> None:
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")

        def _sse(self, events: list[dict]) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()
            for event in events:
                self._events(event)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def do_GET(self) -> None:  # noqa: N802 - http.server names
            if self.path.endswith("/models"):
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [{"id": "mock-1", "object": "model", "owned_by": "sutra"}],
                    },
                )
                return
            self._json(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802 - http.server names
            length = int(self.headers.get("content-length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            type(self).calls += 1
            if log_path:
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "call": type(self).calls,
                                "stream": bool(payload.get("stream")),
                                "messages": _summary(payload),
                                "tools": [
                                    (tool_spec.get("function") or {}).get("name")
                                    for tool_spec in payload.get("tools", [])
                                ],
                            }
                        )
                        + "\n"
                    )
            saw_result = any(m.get("role") == "tool" for m in payload.get("messages", []))
            arguments = json.dumps({"command": command, "description": "print a large result"})
            if not saw_result:
                if payload.get("stream"):
                    def chunk(delta: dict, finish: str | None = None) -> dict:
                        return {
                            "id": "chatcmpl-mock",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": "mock-1",
                            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                        }

                    self._sse(
                        [
                            chunk({"role": "assistant", "content": ""}),
                            chunk(
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
                            chunk({"tool_calls": [{"index": 0, "function": {"arguments": arguments}}]}),
                            chunk({}, "tool_calls"),
                        ]
                    )
                    return
                self._json(
                    200,
                    {
                        "id": "chatcmpl-mock-1",
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
                    },
                )
                return
            if payload.get("stream"):
                self._sse(
                    [
                        {
                            "id": "chatcmpl-mock",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": "mock-1",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant", "content": "done"},
                                    "finish_reason": None,
                                }
                            ],
                        },
                        {
                            "id": "chatcmpl-mock",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": "mock-1",
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        },
                    ]
                )
                return
            self._json(
                200,
                {
                    "id": "chatcmpl-mock-2",
                    "object": "chat.completion",
                    "model": "mock-1",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "done"},
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            )

    return Handler


def print_command(total_bytes: int = DEFAULT_BYTES) -> str:
    """A shell command whose output is one repeated line of about ``total_bytes``."""

    line = "line of output "
    repetitions = max(1, total_bytes // len(line))
    return f"python3 -c \"print('{line}' * {repetitions})\""


def serve(
    port: int = 0,
    *,
    log_path: str | None = DEFAULT_LOG,
    command: str | None = None,
    tool: str = "bash",
) -> ThreadingHTTPServer:
    """Start the mock on ``port`` (0 picks a free one) and return the server.

    The caller owns the socket: run ``server.serve_forever()`` in a thread, or
    call it directly. ``server.server_address[1]`` is the bound port.
    """

    handler = handler_for(
        log_path=log_path, command=command or print_command(), tool=tool
    )
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8899, help="port to listen on")
    parser.add_argument("--log", default=DEFAULT_LOG, help="request log (JSON Lines)")
    parser.add_argument("--bytes", type=int, default=DEFAULT_BYTES, help="tool output size")
    parser.add_argument("--tool", default="bash", help="tool name to call")
    args = parser.parse_args(argv)
    pathlib.Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    server = serve(
        args.port, log_path=args.log, command=print_command(args.bytes), tool=args.tool
    )
    host, port = server.server_address[:2]
    print(f"mock provider listening on http://{host}:{port}/v1 (log: {args.log})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
