#!/usr/bin/env python3
"""Measure what the request-stage policy costs per call.

The policy is bounded on purpose — a fixed number of messages, a fixed head and
tail — so its cost should stay proportional to the request, not to the number of
turns. This script builds synthetic conversations, runs the real
``prompt.prepare`` against them, and reports bytes removed alongside wall-clock
time and peak allocation, per mode. It also runs the same policy from several
threads at once, which is the shape a host with concurrent sessions produces.

Run it from the repository root::

    python3 bench/request_stage.py
    python3 bench/request_stage.py --results 80 --bytes 40000 --store
    python3 bench/request_stage.py --json

Numbers from one machine are not a benchmark of another; the script exists so
the measurement is repeatable and the methodology is inspectable.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_MODULE = "sutra_plugin"


def load(name: str):
    """Import a plugin submodule the way the host imports the package."""
    if _MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _MODULE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{_MODULE}.{name}")


LINE = "  File \"/app/src/handlers/auth.py\", line 42, in handle_login\n"


def body(size: int) -> str:
    text = LINE * (size // len(LINE) + 1)
    return text[:size]


def conversation(results: int, size: int, *, markers: bool, tail: int = 6) -> dict:
    messages = [{"role": "system", "content": "You are a coding agent."}]
    for index in range(results):
        marker = f"[sutra: compressed · id=\"bench{index:04d}\"]" if markers else ""
        messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{index}", "type": "function",
                            "function": {"name": "bash", "arguments": "{}"}}],
        })
        messages.append({"role": "tool", "tool_call_id": f"c{index}",
                         "content": body(size) + marker})
    for index in range(tail):
        messages.append({"role": "assistant", "content": f"step {index}"})
        messages.append({"role": "user", "content": f"continue {index}"})
    return {"model": "bench", "messages": messages}


def request_bytes(request: dict) -> int:
    return len(json.dumps(request, ensure_ascii=False).encode("utf-8"))


def one(prompt, config, request: dict, mode: str, *, retain=None) -> dict:
    caps = config.PROMPT_CAPS_BY_MODE[mode]
    started = time.perf_counter()
    decision = prompt.prepare(
        request,
        keep_last=6,
        min_message_bytes=caps["min_message_bytes"],
        min_request_bytes=caps["min_request_bytes"],
        max_messages=caps["max_messages"],
        head_bytes=prompt.HEAD_BYTES if caps["preview"] else 0,
        tail_bytes=prompt.TAIL_BYTES if caps["preview"] else 0,
        retain=retain,
    )
    elapsed = (time.perf_counter() - started) * 1000.0
    return {"ms": elapsed, "before": decision.bytes_before,
            "after": decision.bytes_after, "elided": decision.elided}


def peak_bytes(prompt, config, request: dict, mode: str, *, retain=None) -> int:
    tracemalloc.start()
    try:
        one(prompt, config, request, mode, retain=retain)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=int, default=40,
                        help="tool results in the synthetic conversation")
    parser.add_argument("--bytes", type=int, default=12_000,
                        help="size of each tool result")
    parser.add_argument("--repeat", type=int, default=7,
                        help="timed calls per mode")
    parser.add_argument("--threads", type=int, default=0,
                        help="also run the policy from N threads at once")
    parser.add_argument("--store", action="store_true",
                        help="retain host-produced results (writes to a temporary store)")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args()

    prompt = load("prompt")
    config = load("config")
    store = None
    retain = None
    temporary = None
    if args.store:
        store_module = load("store")
        temporary = tempfile.TemporaryDirectory(prefix="sutra-bench-")
        store = store_module.Store(store_module.StoreConfig(directory=temporary.name))
        retain = prompt.host_retainer(store=store)
        markers = False
    else:
        markers = True

    request = conversation(args.results, args.bytes, markers=markers)
    rows = []
    for mode in ("balanced", "aggressive", "beast"):
        one(prompt, config, request, mode, retain=retain)  # warm up
        samples = [one(prompt, config, request, mode, retain=retain)
                   for _ in range(max(1, args.repeat))]
        times = sorted(sample["ms"] for sample in samples)
        rows.append({
            "mode": mode,
            "before": samples[-1]["before"],
            "after": samples[-1]["after"],
            "elided": samples[-1]["elided"],
            "median_ms": round(statistics.median(times), 2),
            "p95_ms": round(times[max(0, int(len(times) * 0.95) - 1)], 2),
            "peak_kib": round(peak_bytes(prompt, config, request, mode, retain=retain) / 1024),
        })

    concurrency = None
    if args.threads > 0:
        errors: list[str] = []
        started = time.perf_counter()

        def work(index: int) -> None:
            try:
                one(prompt, config,
                    conversation(max(2, args.results // 2), args.bytes,
                                 markers=markers),
                    "balanced", retain=retain)
            except Exception as exc:  # reported, never swallowed
                errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=work, args=(index,))
                   for index in range(args.threads)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        concurrency = {
            "threads": args.threads,
            "wall_ms": round((time.perf_counter() - started) * 1000, 2),
            "errors": errors,
        }

    report = {
        "results": args.results,
        "result_bytes": args.bytes,
        "request_bytes": request_bytes(request),
        "store": bool(args.store),
        "modes": rows,
        "concurrency": concurrency,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"request: {report['request_bytes']:,} bytes, "
              f"{args.results} results x {args.bytes:,} bytes, "
              f"{'store' if args.store else 'marked'}")
        print(f"{'mode':<11}{'after':>12}{'saved':>8}{'elided':>8}"
              f"{'median ms':>11}{'p95 ms':>9}{'peak KiB':>11}")
        for row in rows:
            saved = 100.0 * (row["before"] - row["after"]) / max(1, row["before"])
            print(f"{row['mode']:<11}{row['after']:>12,}{saved:>7.1f}%"
                  f"{row['elided']:>8}{row['median_ms']:>11}"
                  f"{row['p95_ms']:>9}{row['peak_kib']:>11,}")
        if concurrency:
            print(f"concurrency: {concurrency['threads']} threads in "
                  f"{concurrency['wall_ms']} ms, errors={len(concurrency['errors'])}")
            for error in concurrency["errors"]:
                print(f"  {error}")
    if temporary is not None:
        temporary.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
