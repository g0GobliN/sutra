#!/usr/bin/env python3
"""Measure the context-intelligence engine's accuracy and latency.

Run from the repository root:

    python3 bench/context.py

Three measurements, all deterministic and model-free:

1. **Near-duplicate accuracy.** A synthetic corpus of tool-like documents with
   known near-duplicate variants (one to three changed words, an appended line,
   or a reordered paragraph) and hard negatives (unrelated documents that share
   vocabulary). SimHash and MinHash are scored against the labels for several
   thresholds, so the shipped default is a measured choice rather than a guess.
2. **Selection latency.** Exact 0/1 knapsack (no dependencies) and
   dependency-closure greedy (a chain) at several instance sizes.
3. **Greedy optimality gap.** For small dependency forests, the greedy result is
   compared with an exhaustive optimum, so the cost of not solving the
   precedence-constrained knapsack exactly is visible.

It writes ``bench/context.json`` and prints one table per section.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import random
import statistics
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORDS = (
    "error test build module import file line return value config path "
    "output input warning failure traceback frame process memory cache token "
    "context request response server client socket timeout retry limit bytes "
    "parse render compile deploy branch commit patch diff result status code "
    "function class method object list dict string number boolean null index "
    "loop window buffer queue worker thread lock state event log message user "
    "schema table column query row record field type length offset range "
    "verify check assert expect actual error total count average median"
).split()


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bench = _load("bench_benchmark", ROOT / "bench" / "benchmark.py")
context = importlib.import_module(f"{bench.sutra.__name__}.context")


def _document(rng: random.Random, sentences: int) -> str:
    lines = []
    for index in range(sentences):
        words = [rng.choice(WORDS) for _ in range(rng.randint(6, 12))]
        lines.append(f"[{index:03d}] " + " ".join(words))
    return "\n".join(lines)


def _variant(rng: random.Random, text: str, kind: int) -> str:
    lines = text.split("\n")
    if kind == 0:  # change one word in one line
        target = rng.randrange(len(lines))
        words = lines[target].split()
        words[rng.randrange(len(words))] = rng.choice(WORDS)
        lines[target] = " ".join(words)
        return "\n".join(lines)
    if kind == 1:  # append one line
        return text + "\n[999] " + " ".join(
            rng.choice(WORDS) for _ in range(8))
    if kind == 2:  # reorder two lines
        first, second = rng.randrange(len(lines)), rng.randrange(len(lines))
        lines[first], lines[second] = lines[second], lines[first]
        return "\n".join(lines)
    # change two separated words
    for _ in range(2):
        target = rng.randrange(len(lines))
        words = lines[target].split()
        words[rng.randrange(len(words))] = rng.choice(WORDS)
        lines[target] = " ".join(words)
    return "\n".join(lines)


def _pairs(rng: random.Random, count: int) -> list[tuple[str, str, bool]]:
    documents = [_document(rng, rng.randint(20, 40)) for _ in range(count)]
    pairs: list[tuple[str, str, bool]] = []
    for index, text in enumerate(documents):
        pairs.append((text, _variant(rng, text, index % 4), True))
        other = documents[(index + 1) % len(documents)]
        pairs.append((text, other, False))
    # hard negatives: unrelated documents over the same vocabulary
    for _ in range(count):
        pairs.append((_document(rng, 30), _document(rng, 30), False))
    return pairs


def _score(pairs, classify):
    true_positive = false_positive = false_negative = true_negative = 0
    for left, right, duplicate in pairs:
        predicted = classify(left, right)
        if duplicate and predicted:
            true_positive += 1
        elif duplicate:
            false_negative += 1
        elif predicted:
            false_positive += 1
        else:
            true_negative += 1
    precision = (true_positive / (true_positive + false_positive)
                 if true_positive + false_positive else 0.0)
    recall = (true_positive / (true_positive + false_negative)
              if true_positive + false_negative else 0.0)
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
    }


def _near_duplicate_accuracy(seed: int = 20261118) -> list[dict]:
    rng = random.Random(seed)
    pairs = _pairs(rng, 20)
    rows = []
    for threshold in (4, 8, 12, 16, 20, 24, 28):
        rows.append({
            "method": "simhash",
            "threshold": threshold,
            "labels": len(pairs),
            **_score(pairs, lambda a, b, t=threshold: (
                context.hamming(context.simhash(a), context.simhash(b)) <= t)),
        })
    for threshold in (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9):
        rows.append({
            "method": "minhash",
            "threshold": threshold,
            "labels": len(pairs),
            **_score(pairs, lambda a, b, t=threshold: (
                context.jaccard(context.minhash(a), context.minhash(b)) >= t)),
        })
    return rows


def _timing(seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for size, kind in ((50, "exact"), (200, "exact"), (500, "chain"),
                       (1000, "chain")):
        items = []
        for index in range(size):
            dependency = ()
            if kind == "chain" and index:
                dependency = (f"item{index - 1:04d}",)
            items.append(context.Item(
                id=f"item{index:04d}", content="x" * rng.randint(20, 200),
                token_cost=rng.randint(1, 8), relevance=rng.random(),
                importance=rng.random(), dependency=dependency))
        budget = size * 6
        samples = []
        result = None
        for _ in range(5):
            started = time.perf_counter()
            result = context.select(items, budget)
            samples.append((time.perf_counter() - started) * 1000)
        rows.append({
            "items": size,
            "kind": kind,
            "budget": budget,
            "median_ms": round(statistics.median(samples), 2),
            "max_ms": round(max(samples), 2),
            "selected": len(result.selected),
            "algorithm": result.algorithm,
        })
    return rows


def _exhaustive_optimum(items, budget: int) -> float:
    best = 0.0
    count = len(items)
    score = context.values(items)
    for mask in range(1 << count):
        chosen = [items[index] for index in range(count) if mask & (1 << index)]
        ids = {item.id for item in chosen}
        if any(not all(dep in ids for dep in item.dependency) for item in chosen):
            continue
        if sum(item.token_cost or 0 for item in chosen) <= budget:
            best = max(best, sum(score[item.id] for item in chosen))
    return best


def _greedy_gap(seed: int = 11) -> dict:
    rng = random.Random(seed)
    gaps = []
    suboptimal = 0
    for _ in range(40):
        count = rng.randint(4, 10)
        items = []
        for index in range(count):
            dependency = ()
            if index and rng.random() < 0.5:
                dependency = (f"item{rng.randrange(index)}",)
            items.append(context.Item(
                id=f"item{index}", content="x" * rng.randint(1, 40),
                token_cost=rng.randint(1, 5), relevance=rng.random(),
                importance=rng.random(), dependency=dependency))
        budget = rng.randint(3, count * 4)
        result = context.select(items, budget)
        optimum = _exhaustive_optimum(items, budget)
        gap = optimum - result.value
        if gap > 1e-9:
            suboptimal += 1
        gaps.append(gap / optimum if optimum else 0.0)
    return {
        "instances": len(gaps),
        "suboptimal": suboptimal,
        "mean_gap_pct": round(100 * sum(gaps) / len(gaps), 4),
        "max_gap_pct": round(100 * max(gaps), 4),
    }


def main() -> int:
    accuracy = _near_duplicate_accuracy()
    timing = _timing()
    gap = _greedy_gap()
    report = {
        "note": ("model-free measurement on synthetic documents; SimHash and "
                 "MinHash are compared against known labels, latency is "
                 "wall-clock on this machine, no provider call is made"),
        "near_duplicates": accuracy,
        "selection": timing,
        "greedy_gap": gap,
    }
    out_path = ROOT / "bench" / "context.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"{'method':<9}{'thr':>5}{'precision':>10}{'recall':>8}{'f1':>8}"
          f"{'tp':>6}{'fp':>6}{'fn':>6}")
    for row in accuracy:
        print(f"{row['method']:<9}{row['threshold']:>5}{row['precision']:>10.3f}"
              f"{row['recall']:>8.3f}{row['f1']:>8.3f}{row['true_positive']:>6}"
              f"{row['false_positive']:>6}{row['false_negative']:>6}")
    print("\nselection latency")
    print(f"{'items':>6}{'kind':>7}{'median_ms':>11}{'max_ms':>9}"
          f"{'selected':>9}  algorithm")
    for row in timing:
        print(f"{row['items']:>6}{row['kind']:>7}{row['median_ms']:>11.2f}"
              f"{row['max_ms']:>9.2f}{row['selected']:>9}  {row['algorithm']}")
    print("\ngreedy optimality gap (small dependency instances)")
    print(f"  instances={gap['instances']} suboptimal={gap['suboptimal']} "
          f"mean_gap={gap['mean_gap_pct']}% max_gap={gap['max_gap_pct']}%")
    print(f"\nwrote {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
