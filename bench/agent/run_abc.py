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
import urllib.error
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
                if response.status:
                    return True
        except urllib.error.HTTPError:
            # Any HTTP answer means the socket is up; an upstream error is not a
            # startup failure.
            return True
        except Exception:
            time.sleep(0.15)
    return False


def hermes_env_file(path: pathlib.Path | None = None) -> dict[str, str]:
    """Read simple KEY=VALUE lines from ~/.hermes/.env, without printing values."""
    env_path = path or pathlib.Path.home() / ".hermes" / ".env"
    values: dict[str, str] = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key.replace("_", "").isalnum():
            values.setdefault(key, value.strip().strip('"').strip("'"))
    return values


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
    # `stop` returns while the process is still draining; a fresh `hermes -z` in that
    # window fails with "Gateway draining: not_ready".
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = subprocess.run(["hermes", "gateway", "status"], env=env,
                                capture_output=True, text=True, timeout=30)
        if "not running" in status.stdout.lower():
            return
        time.sleep(0.5)


def run_condition(name: str, enabled: str, mode: str, language: str, passes: str, *,
                  home: pathlib.Path, scenario: pathlib.Path, work: pathlib.Path,
                  timeout: float, model: str = "mock-model",
                  upstream: str | None = None, prompt: str = PROMPT) -> dict:
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
    if upstream:
        # Only the key the upstream proxy needs; importing the whole .env would
        # switch on chat platforms in the isolated gateway (a telegram token in
        # the environment is a startup conflict).
        env.setdefault("OPENROUTER_API_KEY", hermes_env_file().get("OPENROUTER_API_KEY", ""))
    command = [sys.executable, str(HERE.parent / "mock_provider.py"), "--port", str(port),
               "--scenario", str(scenario), "--log", str(log)]
    if upstream:
        command.extend(["--upstream", upstream])
    server = subprocess.Popen(
        command, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        if not wait_for_server(f"http://127.0.0.1:{port}/v1"):
            raise RuntimeError("mock provider did not start")
        # The isolated home's config points at this port for the run.
        isolated_home.prepare(home, REPO, f"http://127.0.0.1:{port}/v1", force=True,
                              model=model)
        stop_gateway(env)
        started = time.monotonic()
        for attempt in range(4):
            run = subprocess.run(
                ["hermes", "-z", prompt, "--provider", "custom", "-m", model, "--yolo"],
                env=env, capture_output=True, text=True, timeout=timeout)
            if "not_ready" not in (run.stdout + run.stderr):
                break
            time.sleep(5)
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
        valid = condition.get("exit_code") == 0 and bool(final.get("tool_result_bytes"))
        row = {
            "name": condition["name"],
            "valid": valid,
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
        if not valid:
            row["tokens_vs_baseline_percent"] = None
        elif base and base["final_request"].get("tokens") and row["final_request_tokens"]:
            row["tokens_vs_baseline_percent"] = round(
                100 * (1 - row["final_request_tokens"] / base["final_request"]["tokens"]), 2)
        else:
            row["tokens_vs_baseline_percent"] = None
        rows.append(row)
    return {"rows": rows}


def run_scenario(scenario: pathlib.Path, args, work_root: pathlib.Path) -> dict:
    if args.conditions:
        wanted = {c.strip() for c in args.conditions.split(",") if c.strip()}
        selected = tuple(c for c in CONDITIONS if c[0] in wanted)
        if not selected:
            raise SystemExit(f"no known condition in {args.conditions!r}")
    else:
        selected = CONDITIONS
    data = json.loads(scenario.read_text(encoding="utf-8"))
    work = work_root / scenario.stem
    work.mkdir(parents=True, exist_ok=True)
    print(f"  {data.get('id', scenario.stem)} -> {len(selected)} conditions", flush=True)
    conditions = []
    for name, enabled, mode, language, passes in selected:
        attempts = 0
        while True:
            attempts += 1
            condition = run_condition(
                name, enabled, mode, language, passes, home=args.home,
                scenario=scenario, work=work, timeout=args.timeout,
                model=args.model, upstream=args.upstream,
                prompt=args.prompt or PROMPT)
            condition["attempts"] = attempts
            transport_failure = (condition["exit_code"] != 0
                                 and not condition["sutra"]
                                 and condition["requests"] > 1)
            if not transport_failure or attempts > args.retries:
                break
            print(f"    retrying {name} after a failed attempt", flush=True)
        conditions.append(condition)
    return {
        "id": data.get("id", scenario.stem),
        "category": data.get("category"),
        "expect": data.get("expect"),
        "scenario_sha256": hashlib.sha256(scenario.read_bytes()).hexdigest(),
        "conditions": conditions,
        "summary": summarize(conditions),
    }


def aggregates(results: list[dict]) -> dict:
    """Mean token change per condition over the scenarios where every run was valid."""
    names = [c[0] for c in CONDITIONS]
    complete = [r for r in results if all(row["valid"] for row in r["summary"]["rows"])]
    means: dict[str, float | None] = {}
    counts: dict[str, int] = {}
    for name in names:
        values = [row["tokens_vs_baseline_percent"] for r in complete
                  for row in r["summary"]["rows"] if row["name"] == name
                  and row["tokens_vs_baseline_percent"] is not None]
        means[name] = round(sum(values) / len(values), 2) if values else None
        counts[name] = len(values)
    return {"scenarios_complete": len(complete), "scenarios_total": len(results),
            "mean_percent_vs_baseline": means, "mean_over": counts}


def print_report(report: dict) -> None:
    for result in report["scenarios"]:
        print(f"\n{result['id']} ({result.get('category')}, expect {result.get('expect')})")
        for row in result["summary"]["rows"]:
            pct = row["tokens_vs_baseline_percent"]
            note = "" if row["valid"] else "  INVALID (run failed; excluded)"
            print("  {name:16s} tokens {tokens!s:>7s}  vs baseline {pct!s:>7s}%  wall {wall}s  search {applied}{note}".format(
                name=row["name"], tokens=row["final_request_tokens"],
                pct=pct, wall=row["wall_seconds"],
                applied=",".join(row["search_applied"]) or "-", note=note))
    agg = report.get("aggregates") or aggregates(report["scenarios"])
    print(f"\nvalid scenarios: {agg['scenarios_complete']}/{agg['scenarios_total']}")
    for name, mean in agg["mean_percent_vs_baseline"].items():
        print(f"  {name:16s} mean {mean}% over {agg['mean_over'][name]} scenarios")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", type=pathlib.Path, default=pathlib.Path.home() / ".hermes-sutra-ab")
    parser.add_argument("--scenario", type=pathlib.Path)
    parser.add_argument("--corpus", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--work", type=pathlib.Path)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--model", default="mock-model")
    parser.add_argument("--upstream", help="real provider base URL for live runs")
    parser.add_argument("--retries", type=int, default=1,
                        help="retries for a condition that failed before producing a result")
    parser.add_argument("--report", type=pathlib.Path,
                        help="print an existing report instead of running anything")
    parser.add_argument("--prompt", help="prompt sent to the agent (default: run the shell command)")
    parser.add_argument("--conditions", help="comma-separated subset of conditions to run")
    args = parser.parse_args(argv)
    if args.report:
        print_report(json.loads(args.report.read_text(encoding="utf-8")))
        return 0
    if not args.out:
        parser.error("--out is required unless --report is used")
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
        "prompt": args.prompt or PROMPT,
        "scenarios": results,
        "aggregates": aggregates(results),
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
    print_report(report)
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
