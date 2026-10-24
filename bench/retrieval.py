#!/usr/bin/env python3
"""Measure retrieval cost at the default per-entry cap.

Run from the repository root:

    python3 bench/retrieval.py

Writes ``bench/retrieval.json``. The point is the worst realistic restore, not
an average: a 32 MiB entry (the default ``SUTRA_STORE_MAX_ENTRY_BYTES``), every
mode, a search that has to scan the whole entry, and a field inside a 32 MiB
JSON document. Everything is generated and local; nothing touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "sutra_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
sutra = importlib.util.module_from_spec(spec)
sys.modules["sutra_plugin"] = sutra
spec.loader.exec_module(sutra)

CAP = 32 * 1024 * 1024
RUNS = 3
NEEDLE = "needle at the end"


def build_entry() -> str:
    """A 32 MB, 400,000-line log with a clue in its last line."""
    lines = [f"{i:08d} " + "x" * 70 for i in range(400_000)]
    lines[-1] = NEEDLE + " " + "x" * 62
    return "\n".join(lines) + "\n"


def measure(sutra, store, cases: "list[dict]") -> "list[dict]":
    rows = []
    for case in cases:
        sutra.recovery.restore(case["args"], store=store)  # warm-up
        times = []
        last = None
        for _ in range(RUNS):
            started = time.perf_counter()
            last = sutra.recovery.restore(case["args"], store=store)
            times.append((time.perf_counter() - started) * 1000)
        payload = json.loads(last)
        rows.append({
            "operation": case["operation"],
            "median_ms": round(statistics.median(times), 1),
            "worst_ms": round(max(times), 1),
            "status": payload.get("status"),
            "content_bytes": len(payload.get("content", "").encode("utf-8")),
            "matches_found": payload.get("matches_found"),
            "returned_bytes": payload.get("returned_bytes"),
            "complete": payload.get("complete"),
        })
    return rows


def main() -> int:
    previous_home = os.environ.get("HERMES_HOME")
    home = tempfile.mkdtemp(prefix="sutra-retrieval-home-")
    store_dir = tempfile.mkdtemp(prefix="sutra-retrieval-store-")
    os.environ["HERMES_HOME"] = home
    try:
        store = sutra.store.Store(sutra.store.StoreConfig(
            directory=store_dir, max_entry_bytes=CAP,
            max_bytes=2 * CAP, max_entries=4, ttl_hours=168))
        text = build_entry()
        entry = store.put(text, tool_name="bench")
        assert entry is not None, "32 MB entry was refused by the store"
        document = json.dumps({"output": text})
        json_entry = store.put(document, tool_name="bench")
        assert json_entry is not None, "32 MB JSON entry was refused by the store"

        cases = [
            {"operation": "full, 32 MB entry",
             "args": {"id": entry.id, "mode": "full"}},
            {"operation": "bytes page, 32 MB entry",
             "args": {"id": entry.id, "mode": "bytes", "start": 0, "end": 65_536}},
            {"operation": "lines page, 32 MB entry",
             "args": {"id": entry.id, "mode": "lines", "start": 1, "end": 101}},
            {"operation": "search, token absent, 32 MB",
             "args": {"id": entry.id, "mode": "search", "query": "no-such-token"}},
            {"operation": "search, token in last line, 32 MB",
             "args": {"id": entry.id, "mode": "search", "query": NEEDLE}},
            {"operation": "full field, 32 MB JSON",
             "args": {"id": json_entry.id, "mode": "full", "field": "/output"}},
            {"operation": "bytes field page, 32 MB JSON",
             "args": {"id": json_entry.id, "mode": "bytes", "field": "/output",
                      "start": 0, "end": 65_536}},
            {"operation": "search absent, 32 MB JSON field",
             "args": {"id": json_entry.id, "mode": "search", "field": "/output",
                      "query": "no-such-token"}},
            {"operation": "search last line, 32 MB JSON field",
             "args": {"id": json_entry.id, "mode": "search", "field": "/output",
                      "query": NEEDLE}},
        ]
        rows = measure(sutra, store, cases)
        result = {
            "measured_on": "the machine that wrote this file",
            "python": platform.python_version(),
            "entry_cap_bytes": CAP,
            "runs_per_case": RUNS,
            "plain_entry_bytes": len(text.encode("utf-8")),
            "json_entry_bytes": len(document.encode("utf-8")),
            "cases": rows,
        }
        out = ROOT / "bench" / "retrieval.json"
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

        print("| operation | median | worst | status | content |")
        print("|---|---|---|---|---|")
        for row in rows:
            print(f'| {row["operation"]} | {row["median_ms"]} ms | '
                  f'{row["worst_ms"]} ms | `{row["status"]}` | '
                  f'{row["content_bytes"]} B |')
        print(f"\nwrote {out}")
        return 0
    finally:
        if previous_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous_home
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(store_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
