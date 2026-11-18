#!/usr/bin/env python3
"""Compare deterministic compression with the opt-in search layer.

Run from the repository root, with the benchmark-only tokenizer installed:

    python3 -m pip install -r bench/requirements.txt
    python3 bench/maximum.py

Three modes are measured on the eight fixtures from ``bench/benchmark.py`` plus
one prose fixture (verbose tool description) that formatting strategies cannot
help, so the wording pass is measured on its own kind of input:

``off``       the shipped deterministic pass (default)
``balanced``  the same pass, then one bounded formatting pass
``aggressive`` the same pass, then the configured pass count and budget

Each mode reports o200k tokens for the text the model would read, wall-clock
milliseconds for the whole call, and whether every protected span outside the
engine's own elision survived. ``search-only`` additionally measures the
optimizer without the engine, so the formatting and wording gain is visible on payloads
the deterministic pass leaves untouched.

This is a tokenizer and wall-clock measurement, not a live model call: no
provider cost is claimed. It writes ``bench/maximum.json`` and prints one table
per mode plus a summary.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import statistics
import time

try:
    import tiktoken
except ImportError:  # pragma: no cover - exercised only without the extra
    raise SystemExit(
        "bench/maximum.py needs tiktoken: "
        "python3 -m pip install -r bench/requirements.txt")

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENCODING = "o200k_base"
MODES = ("off", "balanced", "aggressive")
MAXIMUM_ENV = {"SUTRA_SEARCH": "aggressive", "SUTRA_SEARCH_PASSES": "4",
               "SUTRA_SEARCH_BUDGET_MS": "50", "SUTRA_LANGUAGE": "1"}
BALANCED_ENV = {"SUTRA_SEARCH": "balanced"}


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load("bench_benchmark", ROOT / "bench" / "benchmark.py")
_pkg = bench.sutra
for _name in ("config", "engine", "language", "preserve", "optimize"):
    importlib.import_module(f"{_pkg.__name__}.{_name}")
config = getattr(_pkg, "config")
engine = getattr(_pkg, "engine")
preserve = getattr(_pkg, "preserve")
optimize = getattr(_pkg, "optimize")
language = getattr(_pkg, "language")


_SEARCH_KEYS = ("SUTRA_SEARCH", "SUTRA_SEARCH_PASSES", "SUTRA_SEARCH_BUDGET_MS",
                "SUTRA_LANGUAGE")


def _resolved(**overrides):
    """Resolve the real configuration with only the search variables overridden."""
    saved = {key: os.environ.get(key) for key in _SEARCH_KEYS}
    try:
        for key in _SEARCH_KEYS:
            os.environ.pop(key, None)
        os.environ.update(overrides)
        return config.resolve()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _timed(call, repeats: int = 3):
    best = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = call()
        elapsed = (time.perf_counter() - started) * 1000.0
        best = elapsed if best is None else min(best, elapsed)
    return result, best


def main() -> int:
    encoder = tiktoken.get_encoding(ENCODING)
    cases = list(bench.build_payloads())
    cases.append({
        "case": "prose tool description",
        "text": ("Please carefully inspect the repository and provide a detailed "
                 "explanation of all the changes required in order to improve "
                 "performance. It is important to note that you should not delete "
                 "files, and please keep /etc/hosts and https://example.com/docs "
                 "intact for 3 retries. ") * 40,
    })
    off_cfg = _resolved()
    balanced_cfg = _resolved(**BALANCED_ENV)
    aggressive_cfg = _resolved(**MAXIMUM_ENV)

    rows: dict[str, list[dict]] = {mode: [] for mode in MODES}
    search_only: list[dict] = []
    for case in cases:
        text = case["text"]
        before_tokens = len(encoder.encode(text))
        before_bytes = len(text.encode("utf-8"))
        for mode, cfg in (("off", off_cfg), ("balanced", balanced_cfg),
                          ("aggressive", aggressive_cfg)):
            (out, stats), elapsed = _timed(lambda cfg=cfg: engine.compress(text, cfg=cfg))
            after_tokens = len(encoder.encode(out))
            ok, reasons = preserve.preservation_ok(text, out)
            rows[mode].append({
                "case": case["case"],
                "rule": stats.rule,
                "search_applied": stats.search_applied,
                "search_saved_bytes": stats.search_saved_bytes,
                "before_bytes": before_bytes,
                "after_bytes": len(out.encode("utf-8")),
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "saved_tokens": before_tokens - after_tokens,
                "saved_pct": (round(100 * (before_tokens - after_tokens) / before_tokens, 1)
                              if before_tokens else 0.0),
                "preserved": ok,
                "preservation_reasons": reasons[:3],
                "elapsed_ms": round(elapsed, 3),
            })
        strategy_list = list(optimize.default_strategies()) + [
            optimize.Strategy("language", language.compress_text)]
        (report, _elapsed) = _timed(
            lambda: optimize.optimize(text, strategies=strategy_list))
        search_only.append({
            "case": case["case"],
            "applied": "+".join(report.applied),
            "saved_bytes": report.saved_bytes,
            "saved_pct": (round(100 * report.saved_bytes / before_bytes, 2)
                          if before_bytes else 0.0),
            "elapsed_ms": round(report.elapsed_ms, 3),
        })

    summary = []
    for mode in MODES:
        mode_rows = rows[mode]
        before = sum(row["before_tokens"] for row in mode_rows)
        after = sum(row["after_tokens"] for row in mode_rows)
        elapsed = [row["elapsed_ms"] for row in mode_rows]
        summary.append({
            "mode": mode,
            "before_tokens": before,
            "after_tokens": after,
            "saved_tokens": before - after,
            "saved_pct": round(100 * (before - after) / before, 1) if before else 0.0,
            "median_ms": round(statistics.median(elapsed), 3),
            "max_ms": round(max(elapsed), 3),
            "search_only_saved_bytes": sum(row["search_saved_bytes"] for row in mode_rows),
        })

    payload = {
        "environment": bench.environment(),
        "tiktoken": tiktoken.__version__,
        "encoding": ENCODING,
        "config": {
            "off": {"search_mode": off_cfg.search_mode,
                    "max_bytes": off_cfg.max_bytes,
                    "head_bytes": off_cfg.head_bytes,
                    "tail_bytes": off_cfg.tail_bytes},
            "balanced": {"search_mode": balanced_cfg.search_mode,
                         "search_passes": balanced_cfg.search_passes,
                         "search_budget_ms": balanced_cfg.search_budget_ms},
            "aggressive": {"search_mode": aggressive_cfg.search_mode,
                        "search_passes": aggressive_cfg.search_passes,
                        "search_budget_ms": aggressive_cfg.search_budget_ms},
        },
        "cases": rows,
        "search_only": search_only,
        "summary": summary,
        "cost": "unavailable: tokenizer and wall-clock measurement only, "
                "no provider call and no authoritative usage or price",
    }
    out_path = ROOT / "bench" / "maximum.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    for mode in MODES:
        print(f"\n== {mode} ==")
        print(f"{'case':<22}{'rule':<18}{'tokens':>9}{'saved':>8}{'%':>7}{'ms':>8}  search")
        for row in rows[mode]:
            print(f"{row['case']:<22}{row['rule']:<18}{row['after_tokens']:>9}"
                  f"{row['saved_tokens']:>8}{row['saved_pct']:>7}{row['elapsed_ms']:>8.2f}"
                  f"  {row['search_applied']}")
    print("\n== summary ==")
    for row in summary:
        print(f"{row['mode']:<10} before={row['before_tokens']:>6} "
              f"after={row['after_tokens']:>6} saved={row['saved_tokens']:>5} "
              f"({row['saved_pct']:>4}%) median={row['median_ms']:>6.2f}ms "
              f"max={row['max_ms']:>6.2f}ms")
    print("\n== search-only (optimizer without the engine) ==")
    for row in search_only:
        print(f"{row['case']:<22}{row['saved_bytes']:>6} bytes "
              f"({row['saved_pct']:>5}%) {row['elapsed_ms']:>6.2f}ms {row['applied']}")
    print(f"\nwrote {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
