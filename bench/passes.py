#!/usr/bin/env python3
"""Measure what each additional optimization pass buys, and whether reruns help.

goals.md PART 16 asks two questions a mode table cannot answer:

1. Does maximum-effort search find more useful savings than one pass?
2. Does compressing again find anything the first run left behind?

This benchmark runs the optimizer with 1, 2, 3 and 4 passes on the fixtures from
``bench/benchmark.py`` plus one prose fixture, reports the marginal bytes and
o200k tokens each pass adds, and then feeds the result back through the same
optimizer to measure the second-run (idempotence) gain.

It is a tokenizer and wall-clock measurement: no provider call, no model, no
cost claim. It writes ``bench/passes.json``.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import statistics
import time

try:
    import tiktoken
except ImportError:  # pragma: no cover
    raise SystemExit(
        "bench/passes.py needs tiktoken: "
        "python3 -m pip install -r bench/requirements.txt")

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENCODING = "o200k_base"
PASS_COUNTS = (1, 2, 3, 4)


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load("bench_benchmark", ROOT / "bench" / "benchmark.py")
_pkg = bench.sutra
for _name in ("language", "optimize"):
    importlib.import_module(f"{_pkg.__name__}.{_name}")
optimize = getattr(_pkg, "optimize")
language = getattr(_pkg, "language")

STRATEGIES = tuple(optimize.default_strategies()) + (
    optimize.Strategy("language", language.compress_text),
)


def _prose() -> dict[str, str]:
    return {
        "case": "prose tool description",
        "text": ("Please carefully inspect the repository and provide a detailed "
                 "explanation of all the changes required in order to improve "
                 "performance. It is important to note that you should not delete "
                 "files, and please keep /etc/hosts and https://example.com/docs "
                 "intact for 3 retries. ") * 40,
    }


def _timed(call, repeats: int = 3) -> tuple[object, float]:
    best = None
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = call()
        elapsed = (time.perf_counter() - started) * 1000.0
        best = elapsed if best is None else min(best, elapsed)
    return result, float(best or 0.0)


def main() -> int:
    encoder = tiktoken.get_encoding(ENCODING)
    cases = list(bench.build_payloads()) + [_prose()]
    rows: list[dict[str, object]] = []

    for case in cases:
        text = case["text"]
        before_bytes = len(text.encode("utf-8"))
        before_tokens = len(encoder.encode(text))
        previous_bytes = 0
        for passes in PASS_COUNTS:
            report, elapsed = _timed(
                lambda passes=passes: optimize.optimize(
                    text, max_passes=passes, max_ms=250, strategies=STRATEGIES))
            second, second_elapsed = _timed(
                lambda report=report: optimize.optimize(
                    report.text, max_passes=passes, max_ms=250,
                    strategies=STRATEGIES))
            rows.append({
                "case": case["case"],
                "passes": passes,
                "applied": "+".join(report.applied),
                "saved_bytes": report.saved_bytes,
                "marginal_bytes": report.saved_bytes - previous_bytes,
                "saved_tokens": before_tokens - len(encoder.encode(report.text)),
                "saved_pct": (round(100 * report.saved_bytes / before_bytes, 2)
                              if before_bytes else 0.0),
                "second_run_saved_bytes": second.saved_bytes,
                "second_run_applied": "+".join(second.applied),
                "elapsed_ms": round(elapsed, 3),
                "second_run_elapsed_ms": round(second_elapsed, 3),
            })
            previous_bytes = max(previous_bytes, report.saved_bytes)

    by_passes = []
    for passes in PASS_COUNTS:
        group = [row for row in rows if row["passes"] == passes]
        by_passes.append({
            "passes": passes,
            "saved_bytes": sum(int(row["saved_bytes"]) for row in group),
            "marginal_bytes": sum(int(row["marginal_bytes"]) for row in group),
            "saved_tokens": sum(int(row["saved_tokens"]) for row in group),
            "second_run_saved_bytes": sum(int(row["second_run_saved_bytes"])
                                          for row in group),
            "median_ms": round(statistics.median(
                [float(row["elapsed_ms"]) for row in group]), 3),
            "max_ms": round(max(float(row["elapsed_ms"]) for row in group), 3),
        })

    payload = {
        "environment": bench.environment(),
        "tiktoken": tiktoken.__version__,
        "encoding": ENCODING,
        "passes": by_passes,
        "cases": rows,
        "cost": "unavailable: tokenizer and wall-clock measurement only",
    }
    out = ROOT / "bench" / "passes.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    print(f"{'passes':>6} {'saved B':>9} {'marginal B':>11} {'saved tok':>10} "
          f"{'rerun B':>8} {'median ms':>10}")
    for row in by_passes:
        print(f"{row['passes']:>6} {row['saved_bytes']:>9} "
              f"{row['marginal_bytes']:>11} {row['saved_tokens']:>10} "
              f"{row['second_run_saved_bytes']:>8} {row['median_ms']:>10}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
