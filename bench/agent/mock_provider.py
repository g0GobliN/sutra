"""A deterministic, local OpenAI-compatible provider for agent A/B experiments.

The harness needs a model that answers the same way every time and that never
spends money. This server speaks just enough of the OpenAI chat-completions
protocol for a Hermes turn: it issues the scripted ``terminal`` calls from a
scenario file, one per request, and then returns the scenario's final answer.

Every request is appended to a JSONL log with the exact bytes and (when
``tiktoken`` is importable) o200k token counts of the messages it received, so a
benchmark can compare what the model *actually saw* under each SUTRA mode
rather than trusting the plugin's own counters.

Run it directly::

    python bench/agent/mock_provider.py --port 8787 --log /tmp/requests.jsonl \\
        --scenario /tmp/scenario.json

The scenario file is ``{"commands": ["..."], "final": "..."}``. Unknown paths
return 404, malformed bodies return 400, and the log is written before the
response so a crashed turn still leaves evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:  # tiktoken is optional; counters are labelled when it is missing.
    import tiktoken

    _ENCODER = tiktoken.get_encoding("o200k_base")
except Exception:  # pragma: no cover - environment without tiktoken
    _ENCODER = None

DEFAULT_SCENARIO: dict[str, Any] = {
    "commands": ["seq 1 4000 | sed 's/^/line /'"],
    "final": "The command produced numbered lines; I inspected them.",
}

_LOG_LOCK = threading.Lock()


def count_tokens(text: str) -> dict[str, Any]:
    """Token count for one string, with its provenance."""
    if _ENCODER is not None:
        return {"tokens": len(_ENCODER.encode(text)), "tokenizer": "o200k_base"}
    return {"tokens": len(text.encode("utf-8")) // 4, "tokenizer": "utf8//4-estimate"}


def message_text(message: Any) -> str:
    """The comparable text of one chat message, whatever its shape."""
    if not isinstance(message, dict):
        return json.dumps(message, ensure_ascii=False, sort_keys=True)
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        parts.append(json.dumps(content, ensure_ascii=False, sort_keys=True))
    for call in message.get("tool_calls") or []:
        parts.append(json.dumps(call, ensure_ascii=False, sort_keys=True))
    if message.get("role"):
        parts.append(str(message["role"]))
    return "\n".join(parts)


def tool_result_count(messages: list[Any]) -> int:
    """How many tool results the model has already seen in this conversation."""
    return sum(1 for message in messages if isinstance(message, dict) and message.get("role") == "tool")


class ServerState:
    """Scenario and log shared by every request thread."""

    def __init__(self, scenario: dict[str, Any], log_path: Path) -> None:
        self.scenario = scenario
        self.log_path = log_path
        self.sequence = 0

    def next_sequence(self) -> int:
        with _LOG_LOCK:
            self.sequence += 1
            return self.sequence

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with _LOG_LOCK:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)


def base_record(body: dict[str, Any], sequence: int) -> dict[str, Any]:
    """One log row describing what a request carried, whatever answers it."""
    messages = body.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    joined = "\n".join(message_text(message) for message in messages)
    prompt_tokens = count_tokens(joined)
    tool_messages = [message for message in messages
                     if isinstance(message, dict) and message.get("role") == "tool"]
    tool_bytes = [len(message_text(message).encode("utf-8")) for message in tool_messages]
    return {
        "seq": sequence,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "path": "/v1/chat/completions",
        "messages": len(messages),
        "roles": [str(message.get("role")) for message in messages if isinstance(message, dict)],
        "bytes": len(joined.encode("utf-8")),
        "tokens": prompt_tokens["tokens"],
        "tokenizer": prompt_tokens["tokenizer"],
        "tool_results": tool_result_count(messages),
        "tool_result_bytes": tool_bytes,
        "stream": bool(body.get("stream")),
        "model": body.get("model"),
    }


def chat_completion(state: ServerState, body: dict[str, Any], base_url: str) -> tuple[dict[str, Any], bool]:
    """Build the next assistant turn and log what this request carried."""
    messages = body.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    seen = tool_result_count(messages)
    commands = state.scenario.get("commands") or []
    final = str(state.scenario.get("final") or DEFAULT_SCENARIO["final"])
    record = base_record(body, state.next_sequence())
    state.append(record)
    sequence = record["seq"]

    if seen < len(commands):
        command = str(commands[seen])
        args = json.dumps({"command": command}, ensure_ascii=False)
        completion_text = args
        completion_tokens = count_tokens(completion_text)
        choice_message: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_mock_{sequence}",
                "type": "function",
                "function": {"name": "terminal", "arguments": args},
            }],
        }
        finish_reason = "tool_calls"
    else:
        choice_message = {"role": "assistant", "content": final}
        completion_tokens = count_tokens(final)
        finish_reason = "stop"

    usage = {
        "prompt_tokens": record["tokens"],
        "completion_tokens": completion_tokens["tokens"],
        "total_tokens": record["tokens"] + completion_tokens["tokens"],
    }
    payload = {
        "id": f"chatcmpl-mock-{sequence}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model") or "mock-model",
        "choices": [{
            "index": 0,
            "message": choice_message,
            "finish_reason": finish_reason,
        }],
        "usage": usage,
    }
    return payload, finish_reason == "tool_calls"


def _upstream_request(state: "ServerState", path: str, body: dict[str, Any] | None,
                      stream: bool) -> Any:
    """Send one request to the real provider and return the open response.

    The key is read from the environment named in ``state.upstream_key_env`` and
    never logged. Streaming requests ask for a usage chunk so the record can
    carry real billed tokens instead of a local estimate.
    """
    token = os.environ.get(state.upstream_key_env)
    if not token:
        raise RuntimeError(f"{state.upstream_key_env} is not set")
    data = None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if body is not None:
        if stream:
            options = body.get("stream_options")
            if not isinstance(options, dict):
                options = {}
            options["include_usage"] = True
            body["stream_options"] = options
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"{state.upstream.rstrip('/')}{path}", data=data,
                                     headers=headers, method="POST" if body else "GET")
    return urllib.request.urlopen(request, timeout=600)


class Handler(BaseHTTPRequestHandler):
    state: ServerState
    server_version = "sutra-mock/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter default access log
        return

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            if self.state.upstream:
                try:
                    with _upstream_request(self.state, "/models", None, False) as response:
                        self._send_json(json.loads(response.read()))
                except Exception as exc:  # report, never expose the key
                    self._send_json({"error": {"message": f"upstream failed: {exc}"}}, status=502)
                return
            self._send_json({"object": "list", "data": [
                {"id": "mock-model", "object": "model", "owned_by": "sutra-harness"},
            ]})
            return
        self._send_json({"error": {"message": "not found"}}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json({"error": {"message": "not found"}}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            self._send_json({"error": {"message": "malformed JSON body"}}, status=400)
            return
        if not isinstance(body, dict):
            self._send_json({"error": {"message": "body must be a JSON object"}}, status=400)
            return

        if self.state.upstream:
            self._relay(body)
            return

        payload, _ = chat_completion(self.state, body, f"http://{self.headers.get('Host', '127.0.0.1')}")
        stream = bool(body.get("stream"))
        if not stream:
            self._send_json(payload)
            return

        # Minimal SSE stream: the whole assistant turn in one delta, then the
        # final chunk, then usage when the client asked for it.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        chunk = {
            "id": payload["id"],
            "object": "chat.completion.chunk",
            "created": payload["created"],
            "model": payload["model"],
            "choices": [{"index": 0, "delta": payload["choices"][0]["message"], "finish_reason": None}],
        }
        finish = {
            "id": payload["id"],
            "object": "chat.completion.chunk",
            "created": payload["created"],
            "model": payload["model"],
            "choices": [{"index": 0, "delta": {}, "finish_reason": payload["choices"][0]["finish_reason"]}],
        }
        self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.write(f"data: {json.dumps(finish, ensure_ascii=False)}\n\n".encode("utf-8"))
        options = body.get("stream_options") or {}
        if isinstance(options, dict) and options.get("include_usage"):
            usage_chunk = {
                "id": payload["id"], "object": "chat.completion.chunk", "created": payload["created"],
                "model": payload["model"], "choices": [], "usage": payload["usage"],
            }
            self.wfile.write(f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


    def _relay(self, body: dict[str, Any]) -> None:
        """Forward one request to the real provider and record its usage."""
        stream = bool(body.get("stream"))
        record = base_record(body, self.state.next_sequence())
        record["upstream"] = self.state.upstream
        try:
            response = _upstream_request(self.state, "/chat/completions", body, stream)
        except Exception as exc:
            self._send_json({"error": {"message": f"upstream failed: {exc}"}}, status=502)
            record["error"] = str(exc)
            self.state.append(record)
            return
        with response:
            if not stream:
                data = response.read()
                try:
                    usage = json.loads(data).get("usage") or {}
                except ValueError:
                    usage = {}
                record["usage"] = usage
                record["tokens"] = usage.get("prompt_tokens") or record["tokens"]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.state.append(record)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            usage: dict[str, Any] = {}
            for line in response:
                self.wfile.write(line)
                self.wfile.flush()
                if line.startswith(b"data: ") and b"usage" in line:
                    try:
                        chunk = json.loads(line[6:])
                    except ValueError:
                        continue
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
            record["usage"] = usage
            record["tokens"] = usage.get("prompt_tokens") or record["tokens"]
            self.state.append(record)


def serve(port: int, scenario_path: Path | None, log_path: Path, host: str = "127.0.0.1",
          upstream: str | None = None) -> ThreadingHTTPServer:
    scenario = dict(DEFAULT_SCENARIO)
    if scenario_path is not None:
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        if not isinstance(scenario, dict):
            raise ValueError("scenario must be a JSON object")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    state = ServerState(scenario, log_path)
    state.upstream = upstream
    handler = type("MockHandler", (Handler,), {"state": state})
    server = ThreadingHTTPServer((host, port), handler)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic local OpenAI-compatible provider.")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--upstream", help="real provider base URL, e.g. https://openrouter.ai/api/v1")
    parser.add_argument("--upstream-key-env", default="OPENROUTER_API_KEY")
    args = parser.parse_args(argv)
    server = serve(args.port, args.scenario, args.log, args.host, args.upstream)
    if args.upstream:
        server.RequestHandlerClass.state.upstream_key_env = args.upstream_key_env
    print(f"mock provider on http://{args.host}:{args.port}/v1 (log: {args.log})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
