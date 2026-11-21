"""Run the request stage through the host's own middleware dispatch.

Every other Hermes test in this suite drives ``request_middleware`` directly, and
``tests/test_host_gate.py`` only asks the host to *admit* the plugin. Neither proves that the
host's dispatch actually calls the policy, applies its answer, or fires the observation hook.
This test does: it starts a Hermes plugin manager in a throwaway ``HERMES_HOME``, lets the host
discover and load the repository plugin through its normal loader, and then submits a request
through ``hermes_cli.middleware`` exactly as the agent loop does.

The host is a separate installation, so the test is opt-in and never hard-codes this machine:

    SUTRA_TEST_HERMES_DISPATCH=1 SUTRA_HERMES_SRC=/path/to/hermes-agent \\
        python3 -m pytest -q tests/test_hermes_dispatch.py

It runs in a subprocess because the host installs process-global registries and reads
``HERMES_HOME`` at import time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SOURCE = os.environ.get("SUTRA_HERMES_SRC", "")
_HOST = Path(_SOURCE).expanduser() if _SOURCE else None
_OPT_IN = os.environ.get("SUTRA_TEST_HERMES_DISPATCH") == "1"

pytestmark = pytest.mark.skipif(
    not _OPT_IN or _HOST is None or not (_HOST / "hermes_cli" / "plugins.py").is_file(),
    reason=(
        "set SUTRA_TEST_HERMES_DISPATCH=1 and SUTRA_HERMES_SRC to a Hermes checkout "
        "to run the dispatch test"
    ),
)

_SENTINEL = "SUTRA_DISPATCH "

DRIVER = textwrap.dedent(
    """
    import json, os, re, sys

    repo = os.environ["SUTRA_HOST_SRC"]
    sys.path.insert(0, repo)
    os.chdir(repo)

    import hermes_cli.middleware as middleware
    import hermes_cli.plugins as plugins

    manager = plugins.get_plugin_manager()
    manager.discover_and_load()
    row = manager._plugins["sutra"]
    report = {
        "enabled": bool(row.enabled),
        "error": row.error,
        "middleware": list(row.middleware_registered),
        "hooks": list(row.hooks_registered),
        "tools": list(row.tools_registered),
    }

    body = (
        'Traceback (most recent call last):\\n'
        '  File "src/app/main.py", line 7, in run\\n'
        "    raise RuntimeError('boom')\\n"
        'RuntimeError: boom\\n'
    ) * 700
    request = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "why did it fail?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "shell", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": body},
        ]
        + [{"role": "assistant", "content": "thinking"}, {"role": "user", "content": "continue"}] * 3,
    }
    before = len(json.dumps(request))
    first = middleware.apply_llm_request_middleware(
        request, session_id="s1", model="m", provider="p", api_mode="chat", api_call_count=1
    )
    report["changed"] = bool(first.changed)
    report["trace"] = list(first.trace)
    report["before"] = before
    report["after"] = len(json.dumps(first.payload))

    tool = [m for m in first.payload["messages"] if m.get("role") == "tool"][0]["content"]
    report["marker"] = "[sutra:" in tool
    ids = re.findall(r'id="(su_[0-9a-f]{32})"', tool)
    report["ids"] = ids
    if ids:
        stored = os.path.join(os.environ["SUTRA_STORE_DIR"], ids[0] + ".txt")
        report["stored_bytes"] = os.path.getsize(stored) if os.path.exists(stored) else None
        if os.path.exists(stored):
            with open(stored, "rb") as handle:
                report["restores_bytes"] = handle.read() == body.encode()
    second = middleware.apply_llm_request_middleware(
        first.payload, session_id="s1", model="m", provider="p", api_mode="chat", api_call_count=2
    )
    report["second_changed"] = bool(second.changed)

    manager.invoke_hook(
        "post_api_request",
        session_id="s1",
        usage={"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 40},
        api_call_count=1,
    )
    snapshot = row.module.metrics_snapshot()
    report["provider"] = dict(snapshot["provider"])
    report["prompt"] = dict(snapshot["prompt"])
    print("SUTRA_DISPATCH " + json.dumps(report))
    """
)


def _payload(stdout: str) -> dict:
    for line in stdout.splitlines():
        if line.startswith(_SENTINEL):
            return json.loads(line[len(_SENTINEL) :])
    raise AssertionError(f"driver printed no report:\n{stdout}")


def test_the_host_dispatch_shortens_a_request_and_fires_the_usage_hook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    os.symlink(ROOT, home / "plugins" / "sutra")
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - sutra\n", encoding="utf-8")
    store = tmp_path / "store"

    environment = {
        **os.environ,
        "HERMES_HOME": str(home),
        "SUTRA_HOST_SRC": str(_HOST),
        "SUTRA_STORE_DIR": str(store),
        "SUTRA_STATS_FILE": str(tmp_path / "stats.jsonl"),
        "SUTRA_PROMPT": "1",
        "SUTRA_MODE": "balanced",
    }
    completed = subprocess.run(
        [sys.executable, "-c", DRIVER],
        cwd=str(_HOST),
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )
    report = _payload(completed.stdout)

    assert report["error"] is None, report
    assert report["enabled"] is True
    assert report["middleware"] == ["tool_execution", "llm_request"]
    assert report["hooks"] == ["post_api_request"]
    assert report["tools"] == ["sutra_restore"]

    assert report["changed"] is True
    assert report["after"] < report["before"]
    assert report["trace"] and report["trace"][0]["source"] == "sutra"
    assert report["marker"] is True
    assert len(report["ids"]) == 1
    assert report["stored_bytes"] == len(
        (
            'Traceback (most recent call last):\n'
            '  File "src/app/main.py", line 7, in run\n'
            "    raise RuntimeError('boom')\n"
            "RuntimeError: boom\n"
        ).encode()
    ) * 700
    assert report["restores_bytes"] is True

    assert report["second_changed"] is False
    assert report["provider"]["requests"] == 1
    assert report["provider"]["cache_read_tokens"] == 40
    assert report["prompt"]["rewritten"] == 1
    assert report["prompt"]["by_mode"]["balanced"]["saved_bytes"] > 0
