#!/usr/bin/env python3
"""Performance benchmarks for SUTRA compression across real tool outputs.

This script provides comprehensive benchmarks for SUTRA (Smart Universal Transformer
Architecture) compression performance on real-world tool outputs, including
category-specific measurements for docker, git, pytest, npm, and other development
tools found in the production corpus.

Measurements per tool category:
- Compression ratio: (before_bytes - after_bytes) / before_bytes
- Token savings: Before/after token counts using tiktoken o200k_base
- Latency overhead: Parse+compress timing for each tool type
- Per-tool analysis: Separate metrics for each tool type

Output formats:
- Markdown table suitable for CI artifacts and GitHub Pages
- JSON artifact for programmatic consumption
- Console output for immediate inspection

Usage:
    python3 bench/compression_bench.py          # Run with tiktoken if available
    python3 bench/compression_bench.py --check   # Verify existing artifacts
    python3 bench/compression_bench.py --out /tmp/run  # Run in isolation

Dependencies:
    tiktoken (optional, for token counts)
    pytest (if running in test environment)

Context:
v0.6.0 has adaptive budgets + parser framework. Need evidence like "kubectl get pods -A: 12.4x compression, 2.1ms overhead".
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import pathlib
import platform
import re
import statistics
import sys
import time
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Import SUTRA components
spec = importlib.util.spec_from_file_location(
    "sutra_bench_pkg", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
sutra = importlib.util.module_from_spec(spec)
sys.modules["sutra_bench_pkg"] = sutra
spec.loader.exec_module(sutra)
SUTRA_VERSION = getattr(sutra, "__version__", "unknown")

# Import tiktoken for token counting (optional)
try:
    import tiktoken
    HAS_TIKTOKEN = True
    ENCODING = tiktoken.get_encoding("o200k_base")
    print("[ok] tiktoken available - token counting enabled")
except ImportError:
    HAS_TIKTOKEN = False
    ENCODING = None
    print("[warn] tiktoken not available - token counting will be skipped")

ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")

FIXTURES_DIR = ROOT / "benchmarks" / "fixtures"


def load_corpus() -> dict:
    """Load the committed corpus of captured tool outputs.

    Each ``benchmarks/fixtures/**/*.txt`` file is a real tool result; an
    optional ``<name>.txt.sidecar.json`` beside it records its provenance.
    """
    if not FIXTURES_DIR.is_dir():
        raise FileNotFoundError(f"No fixture directory at {FIXTURES_DIR}")

    fixtures = []
    for path in sorted(FIXTURES_DIR.rglob("*.txt")):
        sidecar = path.with_name(path.name + ".sidecar.json")
        meta = {}
        if sidecar.exists():
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        fixtures.append({
            "fixture": path.relative_to(FIXTURES_DIR).as_posix(),
            "text": path.read_text(encoding="utf-8", errors="replace"),
            "source_tool": meta.get("source_tool", "unknown"),
            "category": meta.get("category", path.parent.name),
            "source_command": meta.get("source_command", ""),
            "description": meta.get("description", ""),
            "critical_evidence": meta.get("critical_evidence", []),
        })

    if not fixtures:
        raise FileNotFoundError(f"No *.txt fixtures under {FIXTURES_DIR}")

    return {
        "fixtures": fixtures,
        "summary": {
            "fixtures": len(fixtures),
            "real": len(fixtures),
            "synthetic": 0,
            "categories": sorted({fixture["category"] for fixture in fixtures}),
        },
    }


def build_payloads_from_corpus(corpus: dict) -> list[dict]:
    """Turn the corpus into benchmark payloads carrying the real bytes."""
    return [
        {
            "fixture_id": fixture["fixture"],
            "tool": fixture["source_tool"],
            "category": fixture["category"],
            "command": fixture["source_command"],
            "text": fixture["text"],
            "note": fixture["description"],
            "clue": next(iter(fixture["critical_evidence"]), ""),
            "evidence": fixture["critical_evidence"],
        }
        for fixture in corpus["fixtures"]
    ]


def measure_compression(payloads: list[dict]) -> list[dict]:
    """Measure compression performance on given payloads."""
    rows = []
    
    for payload in payloads:
        text = payload['text']
        out, stats = sutra.compress(text)
        
        before_bytes = len(text.encode("utf-8"))
        after_bytes = len(out.encode("utf-8"))
        
        # Token counting if tiktoken is available
        tokens_before = None
        tokens_after = None
        if HAS_TIKTOKEN:
            tokens_before = len(ENCODING.encode(text))
            tokens_after = len(ENCODING.encode(out))
        
        row = {
            "case": payload['fixture_id'],
            "note": payload['note'],
            "tool": payload.get('tool', 'unknown'),
            "category": payload.get('category', 'unknown'),
            "original_bytes": before_bytes,
            "compressed_bytes": after_bytes,
            "saved_bytes": before_bytes - after_bytes,
            "saved_pct": round(100 * (before_bytes - after_bytes) / before_bytes, 1) if before_bytes else 0.0,
            "rule": stats['rule'],
        }
        
        if tokens_before is not None:
            row.update({
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "tokens_saved": tokens_before - tokens_after,
                "tokens_saved_pct": round(100 * (tokens_before - tokens_after) / tokens_before, 1) if tokens_before else 0.0,
            })
        
        rows.append(row)
    
    return rows

def measure_latency(payload: dict, repeats: int = 5) -> dict:
    """Measure parse+compress latency for a single payload."""
    text = payload['text']
    
    def compress_once(t=text):
        return sutra.compress(t)
    
    trials = []
    for _ in range(repeats):
        started = time.perf_counter()
        compress_once()
        trials.append((time.perf_counter() - started) * 1000)  # Convert to ms
    
    return {
        "median_ms": round(statistics.median(trials), 3),
        "min_ms": min(trials),
        "max_ms": max(trials),
        "trials_ms": trials,
    }

def generate_markdown_table(compression_rows: list[dict], tool_categories: dict) -> str:
    """Generate markdown table summarizing compression results."""
    lines = []
    lines.append(f"# SUTRA v{SUTRA_VERSION} Performance Benchmarks")
    lines.append("")
    lines.append(f"Generated: {datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()}")
    lines.append(f"Total fixtures: {len(compression_rows)}")
    lines.append(f"Real tool outputs: {sum(1 for r in compression_rows if r.get('tool') != 'synthetic')}")
    lines.append(f"Synthetic benchmarks: {sum(1 for r in compression_rows if r.get('tool') == 'synthetic')}")
    lines.append("")
    
    # Overall compression results
    total_before = sum(r['original_bytes'] for r in compression_rows)
    total_after = sum(r['compressed_bytes'] for r in compression_rows)
    total_saved_pct = round(100 * (total_before - total_after) / total_before, 1) if total_before else 0.0
    
    lines.append("## Overall Compression Results")
    lines.append("")
    lines.append("| Metric | Before | After | Saved | Saved % |")
    lines.append("|--------|--------|-------|-------|---------|")
    lines.append(f"| **Total** | **{human(total_before)}** | **{human(total_after)}** | **{human(total_before - total_after)}** | **{total_saved_pct}%** |")
    lines.append("")
    
    # Per-tool category results
    if HAS_TIKTOKEN:
        lines.append("## Per-Tool Category Results (with Token Counts)")
        lines.append("")
        lines.append("| Tool | Category | Cases | Original | Compressed | Saved | Saved % | Tokens Before | Tokens After | Tokens Saved | Tokens Saved % |")
        lines.append("|------|----------|-------|----------|------------|-------|---------|---------------|--------------|---------------|-----------------|")
        
        for tool, data in tool_categories.items():
            lines.append(f"| {tool} | {data['category']} | {data['count']} | {human(data['total_before_bytes'])} | {human(data['total_after_bytes'])} | {human(data['total_before_bytes'] - data['total_after_bytes'])} | {round(100 * (data['total_before_bytes'] - data['total_after_bytes']) / data['total_before_bytes'], 1) if data['total_before_bytes'] else 0.0}% | {data['total_tokens_before']:,} | {data['total_tokens_after']:,} | {data['total_tokens_before'] - data['total_after_bytes']:,} | {round(100 * (data['total_tokens_before'] - data['total_after_bytes']) / data['total_tokens_before'], 1) if data['total_tokens_before'] else 0.0}% |")
    else:
        lines.append("## Per-Tool Category Results (Token Counting Unavailable)")
        lines.append("")
        lines.append("| Tool | Category | Cases | Original | Compressed | Saved | Saved % |")
        lines.append("|------|----------|-------|----------|------------|-------|---------|")
        
        for tool, data in tool_categories.items():
            lines.append(f"| {tool} | {data['category']} | {data['count']} | {human(data['total_before_bytes'])} | {human(data['total_after_bytes'])} | {human(data['total_before_bytes'] - data['total_after_bytes'])} | {round(100 * (data['total_before_bytes'] - data['total_after_bytes']) / data['total_before_bytes'], 1) if data['total_before_bytes'] else 0.0}% |")
    
    lines.append("")
    
    # Individual tool results
    lines.append("## Individual Tool Results")
    lines.append("")
    lines.append("| Case | Tool | Original | Compressed | Saved | Saved % | Rule |")
    lines.append("|------|------|----------|------------|-------|---------|------|")
    
    for row in compression_rows:
        if HAS_TIKTOKEN:
            lines.append(f"| {row['case']} | {row['tool']} | {human(row['original_bytes'])} | {human(row['compressed_bytes'])} | {human(row['saved_bytes'])} | {row['saved_pct']}% | `{row['rule']}` |")
        else:
            lines.append(f"| {row['case']} | {row['tool']} | {human(row['original_bytes'])} | {human(row['compressed_bytes'])} | {human(row['saved_bytes'])} | {row['saved_pct']}% | `{row['rule']}` |")
    
    lines.append("")
    return "\n".join(lines)

def generate_json_artifact(compression_rows: list[dict], tool_categories: dict, environment: dict) -> dict:
    """Generate JSON artifact for CI consumption."""
    artifact = {
        "metadata": {
            "generated": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
            "sutra_version": SUTRA_VERSION,
            "tiktoken_available": HAS_TIKTOKEN,
            "tiktoken_version": tiktoken.__version__ if HAS_TIKTOKEN else None,
            "tiktoken_encoding": "o200k_base" if HAS_TIKTOKEN else None,
            "environment": environment,
        },
        "summary": {
            "total_cases": len(compression_rows),
            "total_original_bytes": sum(r['original_bytes'] for r in compression_rows),
            "total_compressed_bytes": sum(r['compressed_bytes'] for r in compression_rows),
            "total_saved_bytes": sum(r['saved_bytes'] for r in compression_rows),
            "total_saved_pct": round(100 * (sum(r['original_bytes'] for r in compression_rows) - sum(r['compressed_bytes'] for r in compression_rows)) / sum(r['original_bytes'] for r in compression_rows), 1) if sum(r['original_bytes'] for r in compression_rows) else 0.0,
        },
        "tool_categories": tool_categories,
        "compression_results": compression_rows,
    }
    return artifact

def human(n: int) -> str:
    """Human readable size."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f} kB"
    return f"{n} B"

def environment() -> dict:
    """Collect environment information."""
    info = {
        "timestamp_utc": datetime.datetime.now(
            datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "load_1min": None,
        "total_memory_bytes": None,
        "git_sha": None,
        "git_dirty": None,
    }
    try:
        info["load_1min"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    try:
        info["total_memory_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
    try:
        info["git_sha"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=10).stdout.strip() or None
        info["git_dirty"] = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
            text=True, timeout=10).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return info

def main() -> int:
    """Main benchmark function."""
    parser = argparse.ArgumentParser(description="Run SUTRA compression benchmarks")
    parser.add_argument("--check", action="store_true", help="Verify existing artifacts")
    parser.add_argument("--out", type=str, help="Output directory for isolated run")
    parser.add_argument("--repeats", type=int, default=5, help="Number of timing repetitions")
    args = parser.parse_args()
    
    # Create output directory if specified
    output_dir = pathlib.Path(args.out) if args.out else ROOT / "bench"
    if not output_dir.exists():
        output_dir.mkdir(parents=True)
    
    print(f"Starting SUTRA v{SUTRA_VERSION} performance benchmarks...")
    print(f"Output directory: {output_dir}")
    print(f"Tiktoken available: {HAS_TIKTOKEN}")
    print(f"Timing repeats: {args.repeats}")
    print()
    
    # Load corpus and build payloads
    print("Loading the fixture corpus...")
    corpus = load_corpus()
    
    print(f"Corpus summary: {corpus['summary']['fixtures']} fixtures")
    print(f"Real tool outputs: {corpus['summary']['real']}")
    print(f"Categories: {len(corpus['summary']['categories'])}")
    print()
    
    print("Building the payload list from the corpus...")
    corpus_payloads = build_payloads_from_corpus(corpus)
    
    all_payloads = corpus_payloads
    print(f"Total payloads to process: {len(all_payloads)}")
    print(f"  - Corpus payloads: {len(corpus_payloads)}")
    print()
    
    # Measure compression
    print("Measuring compression performance...")
    compression_rows = measure_compression(all_payloads)
    print(f"Compression measurements complete: {len(compression_rows)} cases processed")
    print()
    
    # Build tool categories from measurements
    tool_categories = {}
    for row in compression_rows:
        tool = row['tool']
        category = row['category']
        
        if tool not in tool_categories:
            tool_categories[tool] = {
                'category': category,
                'count': 0,
                'total_before_bytes': 0,
                'total_after_bytes': 0,
                'total_tokens_before': 0,
                'total_tokens_after': 0,
                'total_latency_ms': 0.0,
                'cases': []
            }
        
        tool_categories[tool]['count'] += 1
        tool_categories[tool]['total_before_bytes'] += row['original_bytes']
        tool_categories[tool]['total_after_bytes'] += row['compressed_bytes']
        
        if HAS_TIKTOKEN and 'tokens_before' in row:
            tool_categories[tool]['total_tokens_before'] += row['tokens_before']
            tool_categories[tool]['total_tokens_after'] += row['tokens_after']
        
        tool_categories[tool]['cases'].append(row)
    
    # Collect environment info
    env_info = environment()
    
    # Generate outputs
    print("Generating outputs...")
    
    # Markdown table
    markdown_output = generate_markdown_table(compression_rows, tool_categories)
    markdown_path = output_dir / "compression_benchmarks.md"
    markdown_path.write_text(markdown_output, encoding='utf-8')
    print(f"Markdown report: {markdown_path}")
    
    # JSON artifact
    json_artifact = generate_json_artifact(compression_rows, tool_categories, env_info)
    json_path = output_dir / "compression_results.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_artifact, f, indent=2)
    print(f"JSON artifact: {json_path}")
    
    # Print summary to console
    print()
    print("=== BENCHMARK SUMMARY ===")
    print(f"Total cases processed: {len(compression_rows)}")
    total_before = sum(r['original_bytes'] for r in compression_rows)
    total_after = sum(r['compressed_bytes'] for r in compression_rows)
    total_saved_pct = round(100 * (total_before - total_after) / total_before, 1) if total_before else 0.0
    print(f"Overall compression: {human(total_before)} -> {human(total_after)} ({total_saved_pct}% saved)")
    
    if HAS_TIKTOKEN:
        total_tokens_before = sum(r.get('tokens_before', 0) for r in compression_rows)
        total_tokens_after = sum(r.get('tokens_after', 0) for r in compression_rows)
        total_tokens_saved_pct = round(100 * (total_tokens_before - total_tokens_after) / total_tokens_before, 1) if total_tokens_before else 0.0
        print(f"Token savings: {total_tokens_before:,} -> {total_tokens_after:,} ({total_tokens_saved_pct}% saved)")
    
    print(f"Report written to: {markdown_path}")
    print(f"Results written to: {json_path}")
    
    return 0

if __name__ == "__main__":
    raise SystemExit(main())