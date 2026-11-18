"""Run the same Hermes task under baseline, balanced and aggressive SUTRA.

The harness owns both sides of the experiment: an isolated Hermes home that
loads the plugin from this checkout, and a deterministic local provider that
issues one shell call and then reports. Every condition restarts the Hermes
gateway, because a gateway keeps the environment it was started with and would
otherwise silently serve an earlier condition's configuration.

What is measured: the model-visible request (bytes and o200k tokens, with the
o200k tokenizer itself when tiktoken is importable) and SUTRA's own record
(bytes in, bytes out, rule, mode, applied strategies, savings). What is not
measured: billed provider usage — the mock provider is local and free. Live
provider numbers need a key, a spend authorization, and are reported as
unavailable otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

import isolated_home  # noqa: E402  (same directory, plain script import)

CONDITIONS = (
    # name, SUTRA_ENABLED, SUTRA_SEARCH, language, passes
    ("baseline", "0", "off", "0", "1"),
    ("compression-only", "1", "off", "0", "1"),
    ("balanced", "1", "balanced", "1", "4"),
    ("aggressive", "1", "aggressive", "1", "4"),
)
PROMPT = "Run the shell command and summarize what it printed."


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(url: str, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/models", timeout=1) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def git_revision(path: pathlib.Path) -> tuple[str, bool]:
    try:
        head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                                    capture_output=True, text=True, timeout=10).stdout.strip())
        return head, dirty
    except Exception:
        return "", False


def read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def stop_gateway(env: dict[str, str]) -> None:
    subprocess.run(["hermes", "gateway", "stop"], env=env, capture_output=True, timeout=90)


def run_condition(name: str, enabled: str, mode: str, language: str, passes: str, *,
                  home: pathlib.Path, scenario: pathlib.Path, work: pathlib.Path,
                  timeout: float) -> dict:
    port = free_port()
    log = work / f"{name}-requests.jsonl"
    stats = work / f"{name}-stats.jsonl"
    out = work / f"{name}-run.log"
    store = work / f"{name}-store"
    store.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "HERMES_HOME": str(home),
        "SUTRA_AB_KEY": "dummy",
        "SUTRA_ENABLED": enabled,
        "SUTRA_SEARCH": mode,
        "SUTRA_LANGUAGE": language,
        "SUTRA_SEARCH_PASSES": passes,
        "SUTRA_REUSE": "0",
        "SUTRA_STORE_DIR": str(store),
        "SUTRA_STATS_FILE": str(stats),
    }
    server = subprocess.Popen(
        [sys.executable, str(HERE.parent / "mock_provider.py"), "--port", str(port),
         "--scenario", str(scenario), "--log", str(log)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        if not wait_for_server(f"http://127.0.0.1:{port}/v1"):
            raise RuntimeError("mock provider did not start")
        # The isolated home's config points at this port for the run.
        isolated_home.prepare(home, REPO, f"http://127.0.0.1:{port}/v1", force=True)
        stop_gateway(env)
        started = time.monotonic()
        run = subprocess.run(
            ["hermes", "-z", PROMPT, "--provider", "custom", "-m", "mock-model", "--yolo"],
            env=env, capture_output=True, text=True, timeout=timeout)
        wall = time.monotonic() - started
        out.write_text(run.stdout + "\n--- stderr ---\n" + run.stderr, encoding="utf-8")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        stop_gateway(env)
    requests_seen = read_jsonl(log)
    records = read_jsonl(stats)
    last = requests_seen[-1] if requests_seen else {}
    return {
        "name": name,
        "mode": mode,
        "exit_code": run.returncode,
        "wall_seconds": round(wall, 2),
        "requests": len(requests_seen),
        "final_request": {key: last.get(key) for key in
                          ("bytes", "tokens", "tokenizer", "tool_result_bytes", "messages")},
        "max_request_tokens": max((r.get("tokens") or 0) for r in requests_seen) if requests_seen else 0,
        "sutra": [{
            "original_bytes": r.get("original_bytes"),
            "compressed_bytes": r.get("compressed_bytes"),
            "rule": r.get("rule"),
            "search_mode": r.get("search_mode"),
            "search_applied": r.get("search_applied"),
            "search_saved_bytes": r.get("search_saved_bytes"),
            "duration_ms": r.get("duration_ms"),
        } for r in records],
        "log": str(log),
        "run_log": str(out),
    }


def summarize(conditions: list[dict]) -> dict:
    base = next((c for c in conditions if c["name"] == "baseline"), None)
    rows = []
    for condition in conditions:
        final = condition["final_request"]
        row = {
            "name": condition["name"],
            "mode": condition["mode"],
            "final_request_bytes": final.get("bytes"),
            "final_request_tokens": final.get("tokens"),
            "max_request_tokens": condition["max_request_tokens"],
            "wall_seconds": condition["wall_seconds"],
            "search_applied": sorted({r["search_applied"] for r in condition["sutra"] if r.get("search_applied")}),
            "search_saved_bytes": sum(int(r.get("search_saved_bytes") or 0) for r in condition["sutra"]),
            "original_bytes": sum(int(r.get("original_bytes") or 0) for r in condition["sutra"]),
            "compressed_bytes": sum(int(r.get("compressed_bytes") or 0) for r in condition["sutra"]),
        }
        if base and base["final_request"].get("tokens") and row["final_request_tokens"]:
            row["tokens_vs_baseline_percent"] = round(
                100 * (1 - row["final_request_tokens"] / base["final_request"]["tokens"]), 2)
        else:
            row["tokens_vs_baseline_percent"] = None
        rows.append(row)
    return {"rows": rows}


def run_scenario(scenario: pathlib.Path, args, work_root: pathlib.Path) -> dict:
    data = json.loads(scenario.read_text(encoding="utf-8"))
    work = work_root / scenario.stem
    work.mkdir(parents=True, exist_ok=True)
    print(f"  {data.get('id', scenario.stem)} -> {len(CONDITIONS)} conditions", flush=True)
    conditions = []
    for name, enabled, mode, language, passes in CONDITIONS:
        conditions.append(run_condition(
            name, enabled, mode, language, passes, home=args.home,
            scenario=scenario, work=work, timeout=args.timeout))
    return {
        "id": data.get("id", scenario.stem),
        "category": data.get("category"),
        "expect": data.get("expect"),
        "scenario_sha256": hashlib.sha256(scenario.read_bytes()).hexdigest(),
        "conditions": conditions,
        "summary": summarize(conditions),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=pathlib.Path, default=pathlib.Path.home() / ".hermes-sutra-ab")
    parser.add_argument("--scenario", type=pathlib.Path)
    parser.add_argument("--corpus", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--work", type=pathlib.Path)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)
    if not args.scenario and not args.corpus:
        parser.error("one of --scenario or --corpus is required")
    scenarios = ([args.scenario] if args.scenario else
                 sorted(args.corpus.glob("*.json")))
    if not scenarios:
        parser.error(f"no scenarios found in {args.corpus}")

    work = args.work or pathlib.Path(tempfile.mkdtemp(prefix="sutra-abc-"))
    work.mkdir(parents=True, exist_ok=True)
    revision, dirty = git_revision(REPO)
    hermes_version = subprocess.run(["hermes", "--version"], capture_output=True,
                                    text=True, timeout=30).stdout.strip()
    results = []
    for scenario in scenarios:
        results.append(run_scenario(scenario.resolve(), args, work))
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hermes": hermes_version,
        "plugin_commit": revision,
        "plugin_dirty": dirty,
        "prompt": PROMPT,
        "scenarios": results,
        "notes": [
            "Token counts are an o200k_base measurement of the serialized request "
            "when tiktoken is importable, not billed provider usage.",
            "SUTRA_REUSE=0 in every condition so a repeated tool result cannot skip "
            "compression and hide a mode difference.",
            "Each condition restarts the Hermes gateway: it keeps the environment of "
            "its first start.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(f"\n{result['id']} ({result.get('category')}, expect {result.get('expect')})")
        for row in result["summary"]["rows"]:
            print("  {name:16s} tokens {tokens!s:>7s}  vs baseline {pct!s:>7s}%  wall {wall}s  search {applied}".format(
                name=row["name"], tokens=row["final_request_tokens"],
                pct=row["tokens_vs_baseline_percent"], wall=row["wall_seconds"],
                applied=",".join(row["search_applied"]) or "-"))
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
