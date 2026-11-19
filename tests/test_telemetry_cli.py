"""End-to-end telemetry failure tests through the real CLI.

These tests use the optional OpenTelemetry stack and a local HTTP server.  The
normal CI test jobs install the ``telemetry`` extra so the CLI takes the real
exporter path rather than the missing-dependency fallback.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip(
    "opentelemetry.sdk",
    reason="the telemetry extra is required for the real exporter path",
)

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = "".join(f"line {index:04d}\n" for index in range(5000))


def _environment(tmp_path: Path, endpoint: str) -> dict[str, str]:
    return {
        **os.environ,
        "SUTRA_ENABLED": "1",
        "SUTRA_TELEMETRY": "1",
        "SUTRA_TELEMETRY_ENDPOINT": endpoint,
        "SUTRA_TELEMETRY_SAMPLE_RATE": "1.0",
        "SUTRA_MODE": "off",
        "SUTRA_STORE": "1",
        "SUTRA_STORE_DIR": str(tmp_path / "store"),
        "SUTRA_STATS_FILE": str(tmp_path / "stats.jsonl"),
    }


def _run_compress(tmp_path: Path, endpoint: str, *, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sutra.cli", "compress"],
        input=PAYLOAD,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(ROOT),
        env=_environment(tmp_path, endpoint),
        timeout=timeout,
    )


def _assert_fails_open(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["changed"] is True
    assert payload["compressed_bytes"] < payload["original_bytes"]


class _HangingHandler(BaseHTTPRequestHandler):
    """Accept a request but never send a response, like a stalled collector."""

    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        time.sleep(20)


@pytest.fixture()
def hanging_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HangingHandler)
    server.daemon_threads = True
    server.block_on_close = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1/metrics"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_dead_endpoint_does_not_change_compression(tmp_path):
    endpoint = f"http://127.0.0.1:{_closed_port()}/v1/metrics"
    result = _run_compress(tmp_path, endpoint)
    _assert_fails_open(result)


def test_malformed_endpoint_does_not_change_compression(tmp_path):
    result = _run_compress(tmp_path, "not-a-url")
    _assert_fails_open(result)


def test_hanging_collector_does_not_block_or_change_compression(tmp_path, hanging_endpoint):
    result = _run_compress(tmp_path, hanging_endpoint, timeout=20)
    _assert_fails_open(result)
