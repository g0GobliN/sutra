#!/usr/bin/env python3
"""Measure retrieval cost near the default per-entry cap.

Run from the repository root:

    python3 bench/retrieval.py

Writes ``bench/retrieval.json``. The point is the worst realistic restore, not
an average: a 32,000,001-byte entry just under the 32 MiB default
(``SUTRA_STORE_MAX_ENTRY_BYTES``), every mode, a search that has to scan the
whole entry, and a field inside a 32,400,015-byte JSON document. Everything is
generated and local; nothing touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# bench/benchmark.py owns the plugin load and the provenance block; reusing it
# keeps one module instance per process and one definition of "environment".
_spec = importlib.util.spec_from_file_location(
    "bench_benchmark", ROOT / "bench" / "benchmark.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)
sutra = bench.sutra

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
            "min_ms": round(min(times), 1),
            "worst_ms": round(max(times), 1),
            "trials_ms": [round(value, 1) for value in times],
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
        env = bench.environment()
        representative, load_note = bench.load_policy(env)
        result = {
            "environment": env,
            "representative": representative,
            "load_note": load_note,
            "entry_cap_bytes": CAP,
            "runs_per_case": RUNS,
            "plain_entry_bytes": len(text.encode("utf-8")),
            "json_entry_bytes": len(document.encode("utf-8")),
            "cases": rows,
        }
        out = ROOT / "bench" / "retrieval.json"
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

        print("| operation | median | min | worst | status | content |")
        print("|---|---|---|---|---|---|")
        for row in rows:
            print(f'| {row["operation"]} | {row["median_ms"]} ms | '
                  f'{row["min_ms"]} ms | {row["worst_ms"]} ms | '
                  f'`{row["status"]}` | {row["content_bytes"]} B |')
        print(f"\nwrote {out}")
        if not representative:
            print(f"NOT representative: {load_note}")
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
