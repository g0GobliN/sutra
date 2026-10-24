"""Offline tests for the OpenCode spike bridge.

These run the real SUTRA package against a real HTTP bridge on loopback with
an isolated HERMES_HOME — the same code path the plugin exercises, without a
model. The runtime proof (README.md) covers the part a test cannot: OpenCode
itself handing the replaced output to the model.
"""

import importlib.util
import json
import os
import pathlib
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ID_RE = re.compile(r"su_[0-9a-f]{32}")


def _start_bridge(tmp_path):
    """Import the bridge module with an isolated home and serve on an ephemeral port."""
    os.environ["HERMES_HOME"] = str(tmp_path / "oc-home")
    os.environ["SUTRA_OC_BRIDGE_TOKEN"] = "test-token-" + str(abs(hash(tmp_path)) % 10**8)
    spec = importlib.util.spec_from_file_location(
        "sutra_oc_bridge", HERE / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["sutra_oc_bridge"] = module
    spec.loader.exec_module(module)
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture()
def bridge(tmp_path):
    server, url = _start_bridge(tmp_path)
    yield url
    server.shutdown()


def _post(url, path, body, token=None):
    """POST and return (status, body); expected 4xx responses are returned, not raised."""
    token = os.environ.get("SUTRA_OC_BRIDGE_TOKEN", "") if token is None else token
    request = urllib.request.Request(
        f"{url}{path}", data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "X-Sutra-Token": token},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_health_reports_version_and_never_storage_paths(bridge, tmp_path):
    with urllib.request.urlopen(f"{bridge}/health", timeout=10) as response:
        health = json.load(response)
    assert health["ok"] is True
    assert health["version"]
    assert "hermes_home" not in health
    assert str(tmp_path) not in json.dumps(health)


def test_post_without_or_with_wrong_token_is_rejected(bridge):
    status, body = _post(bridge, "/compress", {"tool": "bash", "output": "tiny"}, token="")
    assert status == 401
    status, body = _post(bridge, "/compress", {"tool": "bash", "output": "tiny"}, token="wrong")
    assert status == 401
    # And a valid token still works (the fixture's token is read by _post).
    status, _ = _post(bridge, "/compress", {"tool": "bash", "output": "tiny"})
    assert status == 200


def test_oversized_output_is_replaced_with_marker_and_retained(bridge):
    lines = [f"output line {i} padding padding" for i in range(1_500)]
    lines[750] = "CLUE: ZEBRA-7Q"
    big = "\n".join(lines) + "\n"
    assert len(big.encode()) > 24_576
    status, body = _post(bridge, "/compress", {"tool": "bash", "output": big})
    assert status == 200
    result = json.loads(body)
    assert result["changed"] is True
    assert result["marker"] is True
    assert ID_RE.fullmatch(result["id"])
    assert "CLUE: ZEBRA-7Q" not in result["output"], "the clue must be elided"
    assert len(result["output"].encode()) < 24_576, "the rewrite fits the budget"


def test_recovered_original_is_byte_exact(bridge):
    big = "line\r\n" * 6_000 + "日本語 tail"
    _, first = _post(bridge, "/compress", {"tool": "bash", "output": big})
    entry_id = json.loads(first)["id"]
    _, raw = _post(bridge, "/restore",
                   {"id": entry_id, "mode": "full", "max_bytes": 65_536})
    restored = json.loads(raw)
    assert restored["status"] == "ok"
    assert restored["content"] == big
    assert restored["sha256_verified"] is True


def test_search_recovery_returns_the_buried_clue(bridge):
    lines = [f"build log {i} compiling module src/parser_{i}.c" for i in range(1_500)]
    lines[750] = "CLUE: ZEBRA-7Q"
    big = "\n".join(lines) + "\n"
    assert len(big.encode()) > 24_576
    _, first = _post(bridge, "/compress", {"tool": "bash", "output": big})
    entry_id = json.loads(first)["id"]
    _, raw = _post(bridge, "/restore",
                   {"id": entry_id, "mode": "search", "query": "ZEBRA-7Q", "context": 1})
    restored = json.loads(raw)
    assert restored["status"] == "ok"
    assert "ZEBRA-7Q" in restored["content"]


def test_small_output_is_left_alone(bridge):
    _, body = _post(bridge, "/compress", {"tool": "bash", "output": "tiny"})
    assert json.loads(body)["changed"] is False


def test_diff_payload_is_never_rewritten(bridge):
    patch = "diff --git a/x b/x\n@@ -1 +1 @@\n" + "+line\n" * 5_000
    _, body = _post(bridge, "/compress", {"tool": "bash", "output": patch})
    result = json.loads(body)
    assert result["changed"] is False
    assert result["marker"] is False


def test_store_disabled_fails_open_with_no_id_promised(bridge, monkeypatch):
    monkeypatch.setenv("SUTRA_STORE", "0")
    # SUTRA resolves its config per call, so a disabled store means: compression
    # may still rewrite, but nothing is retained and no id may be promised.
    big = "x" * 40_000
    _, body = _post(bridge, "/compress", {"tool": "bash", "output": big})
    result = json.loads(body)
    if result["changed"]:
        assert result["marker"] is False or result["id"] is None
    _, raw = _post(bridge, "/restore", {"id": "su_" + "ab" * 16})
    assert json.loads(raw)["status"] == "unavailable"


def test_invalid_restore_arguments_error_without_content(bridge):
    _, raw = _post(bridge, "/restore", {"id": "not-an-id"})
    response = json.loads(raw)
    assert response["status"] == "error"
    assert "content" not in response


def test_oversized_body_is_rejected(bridge):
    status, body = _post(bridge, "/compress",
                         {"tool": "bash", "output": "x" * (3 * 1024 * 1024)})
    assert status == 413
    assert "too large" in body


def test_non_string_output_is_rejected_cleanly(bridge):
    status, body = _post(bridge, "/compress", {"tool": "bash", "output": {"a": 1}})
    assert status == 400
    assert json.loads(body)["error"]
