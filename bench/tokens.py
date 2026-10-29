#!/usr/bin/env python3
"""Count o200k tokens on the same eight fixtures as bench/benchmark.py.

Run from the repository root:

    python3 -m pip install -r bench/requirements.txt
    python3 bench/tokens.py

This is a tokenizer measurement of the text the model would read, not a live
model call. It writes ``bench/tokens.json`` and prints the table. The plugin
and the test suite do not need tiktoken; this is a benchmark-only dependency.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

try:
    import tiktoken
except ImportError:  # pragma: no cover - exercised only without the extra
    raise SystemExit(
        "bench/tokens.py needs tiktoken: "
        "python3 -m pip install -r bench/requirements.txt")

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENCODING = "o200k_base"

spec = importlib.util.spec_from_file_location(
    "bench_benchmark", ROOT / "bench" / "benchmark.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def main() -> int:
    encoder = tiktoken.get_encoding(ENCODING)
    rows = []
    for case in bench.build_payloads():
        out, stats = bench.sutra.compress(case["text"])
        before = len(encoder.encode(case["text"]))
        after = len(encoder.encode(out))
        rows.append({
            "case": case["case"],
            "rule": stats["rule"],
            "before_tokens": before,
            "after_tokens": after,
            "saved_tokens": before - after,
            "saved_pct": round(100 * (before - after) / before, 1) if before else 0.0,
        })
    total_before = sum(row["before_tokens"] for row in rows)
    total_after = sum(row["after_tokens"] for row in rows)
    payload = {
        "environment": bench.environment(),
        "tiktoken": tiktoken.__version__,
        "encoding": ENCODING,
        "cases": rows,
        "total": {
            "before_tokens": total_before,
            "after_tokens": total_after,
            "saved_pct": round(100 * (total_before - total_after) / total_before, 1),
        },
    }
    out_path = ROOT / "bench" / "tokens.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("| payload | rule | before | after | saved |")
    print("|---|---|---|---|---|")
    for row in rows:
        print(f'| {row["case"]} | `{row["rule"]}` | {row["before_tokens"]:,} | '
              f'{row["after_tokens"]:,} | {row["saved_pct"]}% |')
    print(f'\ntotal: {total_before:,} → {total_after:,} tokens '
          f'({payload["total"]["saved_pct"]}% saved), {ENCODING}')
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
