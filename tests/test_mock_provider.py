"""The mock provider asks for one tool call, then answers, and records both."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MOCK = load("sutra_mock_provider", ROOT / "scripts" / "ab" / "mock_provider.py")


@pytest.fixture()
def server(tmp_path):
    log = tmp_path / "requests.jsonl"
    httpd = MOCK.serve(0, log_path=str(log), command="echo 36000 bytes")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, log
    finally:
        httpd.shutdown()
        httpd.server_close()


def post(server, body: dict) -> str:
    host, port = server.server_address[:2]
    request = urllib.request.Request(
        f"http://{host}:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode()


def test_a_streaming_request_asks_for_one_tool_call(server):
    httpd, _ = server
    body = post(httpd, {"model": "mock-1", "stream": True, "messages": [{"role": "user", "content": "go"}]})
    assert '"finish_reason": "tool_calls"' in body
    assert '"name": "bash"' in body
    assert "data: [DONE]" in body


def test_a_request_carrying_a_tool_result_gets_the_final_answer(server):
    httpd, _ = server
    body = post(
        httpd,
        {
            "model": "mock-1",
            "stream": True,
            "messages": [
                {"role": "user", "content": "go"},
                {"role": "tool", "content": "x" * 500},
            ],
        },
    )
    assert '"content": "done"' in body
    assert '"finish_reason": "stop"' in body


def test_the_recorder_keeps_the_size_and_marker_of_each_message(server):
    httpd, log = server
    post(
        httpd,
        {
            "model": "mock-1",
            "messages": [
                {"role": "user", "content": "go"},
                {
                    "role": "tool",
                    "content": "head\n[sutra: partial · 17,569 bytes elided · id=su_x]\ntail",
                },
            ],
        },
    )
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    tool_row = rows[0]["messages"][-1]
    assert tool_row["role"] == "tool"
    assert tool_row["marker"] is True
    content = "head\n[sutra: partial · 17,569 bytes elided · id=su_x]\ntail"
    assert tool_row["bytes"] == len(content.encode())


def test_the_printed_command_produces_about_the_requested_size():
    command = MOCK.print_command(36_000)
    # Run the payload with this interpreter: the command is a host's shell string,
    # but the size it prints is what the assertion is about, on every platform.
    body = command.split('-c "', 1)[1].rsplit('"', 1)[0]
    printed = subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, check=True
    ).stdout
    assert 35_000 <= len(printed) <= 37_000


def test_a_malformed_body_is_answered_with_400_not_a_dropped_connection(server):
    httpd, log = server
    host, port = httpd.server_address[:2]
    request = urllib.request.Request(
        f"http://{host}:{port}/v1/chat/completions",
        data=b"{not json",
        headers={"content-type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=10)
    assert raised.value.code == 400
    assert json.loads(raised.value.read())["error"]["message"] == "invalid JSON body"
    assert not log.exists()
