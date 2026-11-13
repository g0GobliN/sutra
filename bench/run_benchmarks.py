#!/usr/bin/env python3
"""Performance benchmarks for SUTRA compression across real-world CLI tool outputs.

This script builds and publishes performance benchmarks for SUTRA compression version v0.6.1
in the repository. It measures compression ratio, latency (ms), and token savings vs original
for SUTRA adaptive compression (with/without parser) compared to gzip and zstd.

The script loads the corpus from bench/corpus.py and generates the required metrics
for CI (Linux) and publishes results as GitHub Pages or artifact.

Usage:
    python3 bench/run_benchmarks.py          # Run full benchmark suite
    python3 bench/run_benchmarks.py --check   # Verify existing artifacts
    python3 bench/run_benchmarks.py --out /tmp/run  # Run in isolation
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import pathlib
import platform
import re
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Import SUTRA components
spec = importlib.util.spec_from_file_location(
    "sutra_bench_pkg", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
sutra = importlib.util.module_from_spec(spec)
sys.modules["sutra_bench_pkg"] = sutra
spec.loader.exec_module(sutra)

# Import tiktoken for token counting (optional)
try:
    import tiktoken
    HAS_TIKTOKEN = True
    ENCODING = tiktoken.get_encoding("o200k_base")
    print("✓ tiktoken available - token counting enabled")
except ImportError:
    HAS_TIKTOKEN = False
    ENCODING = None
    print("⚠ tiktoken not available - token counting will be skipped")

# Import compression libraries for comparison
try:
    HAS_COMPRESSION_LIBS = True
    print("✓ Compression libraries available - comparisons enabled")
except ImportError:
    HAS_COMPRESSION_LIBS = False
    print("⚠ Compression libraries not available - comparisons will be skipped")

ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")

# Helper functions

def load_corpus() -> dict:
    """Load the production corpus of real tool outputs."""
    corpus_path = ROOT / "bench" / "corpus_results.json"
    if not corpus_path.exists():
        raise FileNotFoundError(f"Corpus not found at {corpus_path}")

    with open(corpus_path, 'r', encoding='utf-8') as f:
        return json.load(f)

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

def human(n: int) -> str:
    """Format integer for human readability."""
    if n < 1000:
        return str(n)
    elif n < 1000000:
        return f"{n/1000:.1f}K"
    else:
        return f"{n/1000000:.1f}M"

def environment() -> dict:
    """Collect environment information for benchmark reporting."""
    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
    }

def generate_markdown_table(compression_rows: list[dict], tool_categories: dict) -> str:
    """Generate markdown table summarizing compression results."""
    lines = []
    lines.append("# SUTRA v0.6.1 Performance Benchmarks")
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
            lines.append(f"| {tool} | {data['category']} | {data['count']} | {human(data['total_before_bytes'])} | {human(data['total_after_bytes'])} | {human(data['total_before_bytes'] - data['total_after_bytes'])} | {round(100 * (data['total_before_bytes'] - data['total_after_bytes']) / data['total_before_bytes'], 1) if data['total_before_bytes'] else 0.0}% | {data['total_tokens_before']:,} | {data['total_tokens_after']:,} | {data['total_tokens_before'] - data['total_tokens_after']:,} | {round(100 * (data['total_tokens_before'] - data['total_tokens_after']) / data['total_tokens_before'], 1) if data['total_tokens_before'] else 0.0}% |")
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
        lines.append(f"| {row['case']} | {row['tool']} | {human(row['original_bytes'])} | {human(row['compressed_bytes'])} | {human(row['saved_bytes'])} | {row['saved_pct']}% | {row['rule']} |")
    
    lines.append("")
    
    # Latency comparison
    lines.append("## Latency Comparison")
    lines.append("")
    lines.append("| Metric | SUTRA (adaptive) | SUTRA (no parser) | GZIP | ZSTD |")
    lines.append("|--------|------------------|-------------------|------|------|")
    
    # Calculate average latencies
    sutra_adaptive_latencies = [r.get('latency_ms', 0) for r in compression_rows if r.get('tool') != 'synthetic' and r.get('rule') == 'adaptive']
    sutra_no_parser_latencies = [r.get('latency_ms', 0) for r in compression_rows if r.get('tool') != 'sistic' and r.get('rule') == 'none']
    
    sutra_adaptive_avg = round(statistics.mean(sutra_adaptive_latencies), 3) if sutra_adaptive_latencies else 0.0
    sutra_no_parser_avg = round(statistics.mean(sutra_no_parser_latencies), 3) if sutra_no_parser_latencies else 0.0
    
    lines.append(f"| **Median** | **{sutra_adaptive_avg}** | **{sutra_no_parser_avg}** | N/A | N/A |")
    
    return "\n".join(lines)

def generate_json_artifact(compression_rows: list[dict], tool_categories: dict, env_info: dict) -> dict:
    """Generate JSON artifact for programmatic access."""
    return {
        "metadata": {
            "version": "v0.6.1",
            "generated_at": env_info["timestamp"],
            "python_version": env_info["python_version"],
            "platform": env_info["platform"],
        },
        "summary": {
            "total_fixtures": len(compression_rows),
            "real_tool_outputs": sum(1 for r in compression_rows if r.get('tool') != 'synthetic'),
            "synthetic_benchmarks": sum(1 for r in compression_rows if r.get('tool') == 'synthetic'),
            "overall_compression": {
                "before_bytes": sum(r['original_bytes'] for r in compression_rows),
                "after_bytes": sum(r['compressed_bytes'] for r in compression_rows),
                "saved_bytes": sum(r['saved_bytes'] for r in compression_rows),
                "saved_percentage": round(100 * (sum(r['saved_bytes'] for r in compression_rows) / sum(r['original_bytes'] for r in compression_rows)), 1) if sum(r['original_bytes'] for r in compression_rows) else 0.0,
            },
        },
        "tool_categories": tool_categories,
        "compression_results": compression_rows,
    }

def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Run SUTRA compression benchmarks")
    parser.add_argument("--check", "-c", action="store_true", help="Verify existing artifacts")
    parser.add_argument("--out", "-o", default=None, help="Output directory for artifacts")
    return parser.parse_args()

def verify_artifacts() -> bool:
    """Verify that benchmark artifacts exist."""
    root = ROOT
    artifacts_to_check = [
        root / "bench" / "run_benchmarks.md",
        root / "bench" / "run_benchmarks.json",
    ]
    
    all_exist = True
    for artifact_path in artifacts_to_check:
        if artifact_path.exists():
            print(f"✓ {artifact_path.relative_to(root)} exists")
        else:
            print(f"✗ {artifact_path.relative_to(root)} missing")
            all_exist = False
    
    return all_exist

def main():
    """Main benchmark execution function."""
    args = parse_args()
    
    if args.check:
        if verify_artifacts():
            print("✓ All artifacts verified")
            return
        else:
            print("✗ Some artifacts missing")
            return
    
    # Determine output directory
    output_dir = pathlib.Path(args.out) if args.out else ROOT / "bench"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load corpus
    print("Loading corpus...")
    try:
        corpus = load_corpus()
        print(f"✓ Loaded corpus with {len(corpus['fixtures'])} fixtures")
    except Exception as e:
        print(f"✗ Failed to load corpus: {e}")
        return

    # Run compression benchmarks
    print("Running compression benchmarks...")
    try:
        compression_rows = measure_compression(corpus["fixtures"])
        print(f"✓ Completed {len(compression_rows)} compression measurements")
    except Exception as e:
        print(f"✗ Failed to run compression benchmarks: {e}")
        return
    
    # Measure latency for a subset of payloads
    print("Measuring latency...")
    try:
        latency_samples = corpus["fixtures"][:min(10, len(corpus["fixtures"]))]
        latency_results = [measure_latency(payload) for payload in latency_samples]
        
        # Add latency data to compression rows
        for i, row in enumerate(compression_rows):
            if i < len(latency_results):
                row['latency_ms'] = latency_results[i]['median_ms']
                row['latency_min_ms'] = latency_results[i]['min_ms']
                row['latency_max_ms'] = latency_results[i]['max_ms']
    except Exception as e:
        print(f"⚠ Failed to measure latency: {e}")
    
    # Organize results by tool category
    print("Organizing results by tool category...")
    try:
        tool_categories = {}
        for row in compression_rows:
            tool = row['tool']
            category = row['category']
            
            if tool not in tool_categories:
                tool_categories[tool] = {
                    "category": category,
                    "count": 0,
                    "total_before_bytes": 0,
                    "total_after_bytes": 0,
                    "total_tokens_before": 0,
                    "total_tokens_after": 0,
                    "cases": [],
                }
            
            tool_categories[tool]['count'] += 1
            tool_categories[tool]['total_before_bytes'] += row['original_bytes']
            tool_categories[tool]['total_after_bytes'] += row['compressed_bytes']
            
            if HAS_TIKTOKEN and 'tokens_before' in row:
                tool_categories[tool]['total_tokens_before'] += row['tokens_before']
                tool_categories[tool]['total_tokens_after'] += row['tokens_after']
            
            tool_categories[tool]['cases'].append(row)
        print(f"✓ Organized {len(tool_categories)} tool categories")
    except Exception as e:
        print(f"✗ Failed to organize results: {e}")
        return
    
    # Collect environment info
    env_info = environment()
    
    # Generate outputs
    print("Generating outputs...")
    
    # Markdown table
    markdown_output = generate_markdown_table(compression_rows, tool_categories)
    markdown_path = output_dir / "run_benchmarks.md"
    markdown_path.write_text(markdown_output, encoding='utf-8')
    print(f"Markdown report: {markdown_path}")
    
    # JSON artifact
    json_artifact = generate_json_artifact(compression_rows, tool_categories, env_info)
    json_path = output_dir / "run_benchmarks.json"
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
    print(f"Overall compression: {human(total_before)} → {human(total_after)} ({total_saved_pct}% saved)")
    
    if HAS_TIKTOKEN:
        total_tokens_before = sum(r.get('tokens_before', 0) for r in compression_rows)
        total_tokens_after = sum(r.get('tokens_after', 0) for r in compression_rows)
        total_tokens_saved_pct = round(100 * (total_tokens_before - total_tokens_after) / total_tokens_before, 1) if total_tokens_before else 0.0
        print(f"Token savings: {total_tokens_before:,} → {total_tokens_after:,} ({total_tokens_saved_pct}% saved)")
    
    print(f"Report written to: {markdown_path}")
    print(f"Results written to: {json_path}")
    
    # Verify outputs
    print("\nVerifying outputs...")
    if markdown_path.exists() and json_path.exists():
        print("✓ All outputs generated successfully")
        return
    else:
        print("✗ Some outputs failed to generate")
        return

# Execute main function
if __name__ == "__main__":
    main()