"""The forwarding proxy is the request boundary for hosts that expose only a base URL.

These tests drive a real socket pair — client → proxy → fake upstream — because
the properties that matter here are transport properties: the credential header
must arrive unchanged, the body must be JSON-valid on both sides, and the
response must come back byte for byte. A unit test on ``rewrite_body`` alone
would not catch a header or streaming mistake.
"""

from __future__ import annotations

import http.client
import http.server
import json
import pathlib
import re
import threading

import pytest

from conftest import plugin_module

proxy = plugin_module("proxy")
prompt = plugin_module("prompt")
config = plugin_module("config")
metrics = plugin_module("metrics")
store_module = plugin_module("store")

MARKER = 'id="deadbeef"'
SECRET = "sk-ant-test-value-1234"


def body(size: int = 12_000, *, marked: bool = True) -> str:
    line = "Traceback (most recent call last):\n  File \"src/app/main.py\", line 7, in run\n"
    text = (line * (size // len(line) + 1))[:size]
    return text + (f"\n[sutra: compressed · {MARKER}]\n" if marked else "\n")


def conversation(results: int = 2, *, marked: bool = True) -> dict:
    messages = [{"role": "system", "content": "You are a coding agent."},
                {"role": "user", "content": "why does the test fail?"}]
    for index in range(results):
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": [{"id": f"call{index}", "type": "function",
                                         "function": {"name": "bash", "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": f"call{index}", "content": body(marked=marked)})
        messages.append({"role": "assistant", "content": "the trace is above"})
    messages.append({"role": "user", "content": "fix it"})
    messages.append({"role": "assistant", "content": "working on it"})
    return {"model": "claude-test", "max_tokens": 64, "messages": messages}


class Upstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list = []
    #: The answer a test wants the upstream to give. Class attributes because
    #: the handler is instantiated by the standard library, per connection.
    answer_status = 200
    answer_type = "application/json"
    answer_payload = b'{"id": "msg_1", "type": "message"}'

    def log_message(self, *args):  # keep the test output quiet
        return

    def _respond(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length)
        self.__class__.seen.append({
            "path": self.path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body": data,
        })
        self._respond(self.answer_status, self.answer_payload, self.answer_type)

    def do_GET(self):  # noqa: N802
        self.__class__.seen.append({"path": self.path, "headers": {}, "body": b""})
        self._respond(200, b"model-list", "text/plain")

    def do_HEAD(self):  # noqa: N802
        self.__class__.seen.append({"path": self.path, "headers": {}, "body": b""})
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "10")
        self.end_headers()

    def do_OPTIONS(self):  # noqa: N802
        self.__class__.seen.append({"path": self.path, "headers": {}, "body": b""})
        self._respond(200, b"", "text/plain")


def configure(monkeypatch, tmp_path, *, mode="balanced"):
    """Resolve a real config from the environment the proxy would see."""
    monkeypatch.setenv("SUTRA_PROMPT", "1")
    monkeypatch.setenv("SUTRA_MODE", mode)
    monkeypatch.setenv("SUTRA_PROMPT_MIN_REQUEST_BYTES", "1024")
    monkeypatch.setenv("SUTRA_PROMPT_MIN_MESSAGE_BYTES", "512")
    monkeypatch.setenv("SUTRA_PROMPT_STORE_HOST", "1")
    monkeypatch.setenv("SUTRA_STORE_DIR", str(tmp_path / "originals"))
    return config.resolve()


@pytest.fixture()
def pair(tmp_path, monkeypatch):
    Upstream.seen = []
    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    cfg = configure(monkeypatch, tmp_path)
    handle = store_module.Store(store_module.StoreConfig(directory=str(tmp_path / "originals")))
    server = proxy.make_server(host="127.0.0.1", port=0,
                               upstream=f"http://127.0.0.1:{upstream.server_address[1]}",
                               cfg=cfg, retain=prompt.host_retainer(store=handle))
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        yield {"port": server.server_address[1], "upstream": upstream, "cfg": cfg,
               "handle": handle, "server": server}
    finally:
        server.shutdown()
        upstream.shutdown()
        server.server_close()
        upstream.server_close()


def send(port: int, path: str, body_bytes: bytes, *, headers: dict | None = None,
         method: str = "POST"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        payload = dict({"Content-Type": "application/json",
                        "x-api-key": SECRET,
                        "anthropic-version": "2023-06-01"}, **(headers or {}))
        connection.request(method, path, body=body_bytes, headers=payload)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_a_messages_request_is_shortened_and_the_key_survives_the_hop(pair):
    original = json.dumps(conversation()).encode("utf-8")
    status, headers, payload = send(pair["port"], "/v1/messages", original)
    assert status == 200
    assert payload == b'{"id": "msg_1", "type": "message"}'
    assert headers.get("X-Sutra-Rewritten") == "1"

    forwarded = Upstream.seen[-1]
    assert forwarded["path"] == "/v1/messages"
    assert forwarded["headers"]["x-api-key"] == SECRET
    assert forwarded["headers"]["anthropic-version"] == "2023-06-01"
    assert len(forwarded["body"]) < len(original)
    sent = json.loads(forwarded["body"])
    assert sent["model"] == "claude-test"
    assert sent["messages"][0] == {"role": "system", "content": "You are a coding agent."}
    assert sent["messages"][1] == {"role": "user", "content": "why does the test fail?"}
    assert sent["messages"][-2] == {"role": "user", "content": "fix it"}
    assert sent["messages"][-1] == {"role": "assistant", "content": "working on it"}
    tool_texts = [m["content"] for m in sent["messages"] if m.get("role") == "tool"]
    assert len(tool_texts) == 2
    # Both results already carried a marker; exactly one was shortened here.
    assert sum("[sutra: request copy" in text for text in tool_texts) == 1


def test_a_host_produced_result_is_retained_and_its_id_restores_the_bytes(pair):
    original = json.dumps(conversation(marked=False)).encode("utf-8")
    send(pair["port"], "/v1/messages", original)
    forwarded = json.loads(Upstream.seen[-1]["body"])
    shortened = [m["content"] for m in forwarded["messages"]
                 if m.get("role") == "tool" and "[sutra: request copy" in m["content"]]
    assert len(shortened) == 1
    ids = re.findall(r'id="(su_[0-9a-f]{32})"', shortened[0])
    assert len(ids) == 1
    handle = pair["handle"]
    lookup = handle.get(ids[0])
    assert lookup.status == "ok"
    assert b"Traceback (most recent call last)" in handle.read_bytes(lookup.entry)


def test_a_non_messages_path_is_forwarded_untouched(pair):
    original = json.dumps(conversation()).encode("utf-8")
    status, headers, _ = send(pair["port"], "/v1/messages/count_tokens", original)
    assert status == 200
    assert "X-Sutra-Rewritten" not in headers
    assert Upstream.seen[-1]["body"] == original


def test_unparsable_json_is_forwarded_untouched(pair):
    original = b"{not json at all"
    status, headers, _ = send(pair["port"], "/v1/messages", original)
    assert status == 200
    assert "X-Sutra-Rewritten" not in headers
    assert Upstream.seen[-1]["body"] == original


def test_a_get_request_is_forwarded(pair):
    status, _, payload = send(pair["port"], "/v1/models", b"", method="GET")
    assert status == 200
    assert payload == b"model-list"


def test_off_mode_forwards_every_messages_body_untouched(tmp_path, pair):
    import dataclasses
    handler = pair["server"].RequestHandlerClass
    handler.config = dataclasses.replace(handler.config, mode="off")
    original = json.dumps(conversation()).encode("utf-8")
    status, headers, _ = send(pair["port"], "/v1/messages", original)
    assert status == 200
    assert "X-Sutra-Rewritten" not in headers
    assert Upstream.seen[-1]["body"] == original


def test_a_dead_upstream_reports_a_proxy_error_without_the_url(tmp_path, monkeypatch):
    Upstream.seen = []
    cfg = configure(monkeypatch, tmp_path)
    server = proxy.make_server(host="127.0.0.1", port=0,
                               upstream="http://127.0.0.1:9",
                               cfg=cfg, retain=prompt.host_retainer())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, payload = send(server.server_address[1], "/v1/messages",
                                  json.dumps(conversation()).encode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
    assert status == 502
    error = json.loads(payload)
    assert error["error"]["type"] == "sutra_proxy_error"
    assert "127.0.0.1:9" not in payload.decode("utf-8")


def test_a_rewrite_is_counted_for_its_mode(pair):
    before = metrics.snapshot()["prompt"]["by_mode"].get("balanced", {}).get("rewritten", 0)
    send(pair["port"], "/v1/messages", json.dumps(conversation()).encode("utf-8"))
    after = metrics.snapshot()["prompt"]["by_mode"]["balanced"]["rewritten"]
    assert after == before + 1


class StreamingUpstream(http.server.BaseHTTPRequestHandler):
    """An SSE upstream: chunked, flushed, and never length-delimited."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for index in range(4):
            event = f"event: content_block_delta\ndata: {{\"index\": {index}}}\n\n".encode()
            self.wfile.write(b"%x\r\n" % len(event) + event + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def test_a_streamed_response_arrives_unchanged(tmp_path, monkeypatch):
    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StreamingUpstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    cfg = configure(monkeypatch, tmp_path)
    server = proxy.make_server(host="127.0.0.1", port=0,
                               upstream=f"http://127.0.0.1:{upstream.server_address[1]}",
                               cfg=cfg, retain=prompt.host_retainer())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, headers, payload = send(server.server_address[1], "/v1/messages",
                                        json.dumps(conversation()).encode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        upstream.shutdown()
        upstream.server_close()
    assert status == 200
    assert headers.get("Content-Type") == "text/event-stream"
    assert headers.get("X-Sutra-Rewritten") == "1"
    events = [line for line in payload.split(b"\n") if line.startswith(b"event: ")]
    assert len(events) == 4
    assert payload.endswith(b"\n\n")


def test_a_head_request_gets_headers_and_no_body(pair):
    status, headers, payload = send(pair["port"], "/v1/models", b"", method="HEAD")
    assert status == 200
    assert payload == b""
    assert headers.get("Content-Length") == "10"


def test_an_options_request_is_forwarded(pair):
    status, _, payload = send(pair["port"], "/v1/messages", b"", method="OPTIONS")
    assert status == 200
    assert payload == b""
    assert Upstream.seen[-1]["path"] == "/v1/messages"


def test_the_cli_serves_a_request_in_its_own_process(tmp_path, monkeypatch):
    """The operator runs `sutra proxy`, not `make_server`; prove that path."""
    import os
    import subprocess
    import sys
    import time

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    read_fd, write_fd = os.pipe()
    driver = (
        "import importlib.util, pathlib, sys\n"
        "root = pathlib.Path(sys.argv[1])\n"
        "spec = importlib.util.spec_from_file_location('sutra_plugin', root / '__init__.py',\n"
        "    submodule_search_locations=[str(root)])\n"
        "pkg = importlib.util.module_from_spec(spec)\n"
        "sys.modules['sutra_plugin'] = pkg\n"
        "spec.loader.exec_module(pkg)\n"
        "import importlib\n"
        "proxy = importlib.import_module('sutra_plugin.proxy')\n"
        "sys.exit(proxy.run(sys.argv[2:]))\n"
    )
    env = dict(os.environ)
    env.update({"SUTRA_PROMPT": "1", "SUTRA_MODE": "balanced",
                "SUTRA_PROMPT_MIN_REQUEST_BYTES": "1024",
                "SUTRA_PROMPT_MIN_MESSAGE_BYTES": "512",
                "SUTRA_PROMPT_STORE_HOST": "1",
                "SUTRA_STORE_DIR": str(tmp_path / "originals")})
    process = subprocess.Popen(
        [sys.executable, "-c", driver, str(pathlib.Path(__file__).parents[1]),
         "--port", "0", "--upstream", f"http://127.0.0.1:{upstream.server_address[1]}"],
        stdout=write_fd, stderr=subprocess.DEVNULL, env=env)
    os.close(write_fd)
    try:
        port = None
        deadline = time.time() + 30
        line = b""
        while time.time() < deadline and port is None:
            chunk = os.read(read_fd, 1)
            if not chunk:
                break
            line += chunk
            if line.endswith(b"\n"):
                text = line.decode().strip()
                match = re.search(r"listening on http://[^:]+:(\d+)", text)
                if match:
                    port = int(match.group(1))
                line = b""
        assert port, "the CLI never announced its port"
        status, headers, _ = send(port, "/v1/messages", json.dumps(conversation()).encode("utf-8"))
    finally:
        os.close(read_fd)
        process.terminate()
        process.wait(timeout=10)
        upstream.shutdown()
        upstream.server_close()
    assert status == 200
    assert headers.get("X-Sutra-Rewritten") == "1"

def test_an_anthropic_tool_result_body_is_shortened(pair):
    """A Messages-API body carries its results inside user messages."""
    pytest.importorskip("json")
    body_text = (
        "pytest -q\n" + "=" * 60 + "\n"
        + "FAILED tests/test_app.py::test_login - AssertionError: 401 != 200\n" * 900
    )
    messages = [
        {"role": "user", "content": "find the failure"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": "pytest -q"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": body_text},
        ]},
    ]
    messages += [{"role": "user", "content": [{"type": "text", "text": f"next {i}"}]}
                 for i in range(6)]
    request = {"model": "claude-sonnet-4-5", "max_tokens": 1024, "messages": messages}
    original = json.dumps(request).encode("utf-8")

    status, headers, _ = send(pair["port"], "/v1/messages", original)

    assert status == 200
    assert headers.get("X-Sutra-Rewritten") == "1"
    forwarded = Upstream.seen[-1]["body"]
    assert len(forwarded) < len(original)
    block = json.loads(forwarded)["messages"][2]["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu_1"
    assert "request copy" in block["content"]


# --- the answer's usage is counted, never changed -------------------------
#
# Claude Code reports prompt-cache hits in the answer's ``usage`` object. The
# proxy is the only place SUTRA sees a Claude Code answer, so this is where the
# observation has to happen; the bytes the client receives stay the provider's.

def provider_counters() -> dict:
    return dict(metrics.snapshot()["provider"])


def delta(before: dict, after: dict, key: str) -> int:
    return after.get(key, 0) - before.get(key, 0)


def test_a_json_answer_reports_its_usage_and_arrives_unchanged(pair):
    answer = json.dumps({
        "id": "msg_1",
        "type": "message",
        "usage": {"input_tokens": 12, "output_tokens": 3,
                  "cache_read_input_tokens": 400,
                  "cache_creation_input_tokens": 25},
    }).encode("utf-8")
    before = provider_counters()
    Upstream.answer_payload = answer
    try:
        status, headers, payload = send(pair["port"], "/v1/messages",
                                        json.dumps(conversation()).encode("utf-8"))
    finally:
        Upstream.answer_payload = b'{"id": "msg_1", "type": "message"}'
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert payload == answer
    after = provider_counters()
    assert delta(before, after, "cache_read_tokens") == 400
    assert delta(before, after, "cache_write_tokens") == 25
    assert delta(before, after, "input_tokens") == 12
    assert delta(before, after, "requests") == 1
    assert delta(before, after, "reported") == 1
    assert delta(before, after, "unreported") == 0


def test_a_streamed_answer_reports_the_usage_of_its_first_event(pair):
    answer = (
        b"event: message_start\n"
        b'data: {"type": "message_start", "message": {"id": "msg_1", '
        b'"usage": {"input_tokens": 7, "cache_read_input_tokens": 900}}}\n\n'
        b"event: message_stop\n"
        b'data: {"type": "message_stop"}\n\n'
    )
    before = provider_counters()
    Upstream.answer_type = "text/event-stream"
    Upstream.answer_payload = answer
    try:
        status, headers, payload = send(pair["port"], "/v1/messages",
                                        json.dumps(conversation()).encode("utf-8"))
    finally:
        Upstream.answer_type = "application/json"
        Upstream.answer_payload = b'{"id": "msg_1", "type": "message"}'
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert payload == answer
    after = provider_counters()
    assert delta(before, after, "cache_read_tokens") == 900
    assert delta(before, after, "input_tokens") == 7


def test_an_answer_without_usage_is_counted_as_unreported(pair):
    before = provider_counters()
    send(pair["port"], "/v1/messages", json.dumps(conversation()).encode("utf-8"))
    after = provider_counters()
    assert delta(before, after, "requests") == 1
    assert delta(before, after, "reported") == 0
    assert delta(before, after, "unreported") == 1


def test_the_counters_never_change_what_the_client_receives(pair):
    answer = b'{"id": "msg_1", "type": "message", "usage": {"input_tokens": 1}}'
    Upstream.answer_payload = answer
    try:
        _, headers, payload = send(pair["port"], "/v1/messages",
                                   json.dumps(conversation()).encode("utf-8"))
    finally:
        Upstream.answer_payload = b'{"id": "msg_1", "type": "message"}'
    assert payload == answer
    assert headers.get("X-Sutra-Rewritten") == "1"
