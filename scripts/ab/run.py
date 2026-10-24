#!/usr/bin/env python3
"""The frozen A/B task benchmark runner — see docs/ab-benchmark.md.

Two arms (SUTRA disabled / enabled), eight tasks with deterministic fixtures
and objective scorers, provider-reported usage read from Hermes' session
records. Every started session is recorded, pass or fail.

    scripts/ab/run.py --arm on --out results/on     # run all 8 tasks
    scripts/ab/run.py --selfcheck                   # validate scorers, no model
    scripts/ab/run.py --dry-run --arm on            # mechanics only, no sessions

The arm is state the operator sets (`hermes plugins disable/enable sutra` plus a
gateway restart); this runner verifies it and records it — it never infers the
arm from behavior. Scoring is programmatic: a planted token in the final answer,
or a test command exiting 0. No plausibility judgements anywhere.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sqlite3
import subprocess
import time

HERMES_STATE = pathlib.Path.home() / ".hermes/state.db"
SESSION_RE = re.compile(r"Session:\s+(\S+)")


# --- fixtures (deterministic; identical bytes for both arms) ----------------

def _w(workspace: pathlib.Path, rel: str, text: str) -> None:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def setup_log(workspace: pathlib.Path, rng: random.Random) -> dict:
    fail = f"src/module-{rng.randint(900, 1099)}.test.ts"
    lines = [f"\u2713 src/lib/module-{i}.test.ts > case {i} passes"
             for i in range(800)]
    lines[400] = f"FAIL {fail} > case 400 expected 4 to be 5"
    _w(workspace, "run.log", "\n".join(lines) + "\n")
    return {"planted": fail}


def setup_traceback(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"CTX-{rng.randint(10_000, 99_999)}"
    frames = "\n".join(f'  File "/app/m{i}.py", line {i}, in h{i}' for i in range(400))
    _w(workspace, "trace.txt",
       f"{frames}\nTraceback (most recent call last):\n{frames}\n"
       f"ValueError: aborting because {token} did not resolve\n")
    return {"planted_type": "ValueError", "planted_token": token}


def setup_assert(workspace: pathlib.Path, rng: random.Random) -> dict:
    var = f"expected_rows_{rng.randint(100, 999)}"
    lines = [f"ok case {i} passed — src/lib/module_{i}.test.ts::case_{i}"
             for i in range(600)]
    lines[300] = f"AssertionError: assert {var} == 42 where {var} = 41"
    _w(workspace, "pytest.out", "\n".join(lines) + "\n")
    return {"planted": var}


def setup_patch(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"flag_{rng.randint(1_000, 9_999)}"
    body = ("+const retries = backoff.next(3)\n-const retries = 0\n"
            "+if (retries > 2) log.warn('retrying the job after the backoff window')\n" * 300)
    _w(workspace, "change.patch",
       "diff --git a/src/job.ts b/src/job.ts\n--- a/src/job.ts\n+++ b/src/job.ts\n"
       f"@@ -12,7 +12,7 @@\n{body}+export const {token} = true\n")
    return {"planted_file": "src/job.ts", "planted_token": token}


def setup_json(workspace: pathlib.Path, rng: random.Random) -> dict:
    email = f"user{rng.randint(400, 460)}@example.com"
    records = [{"id": i, "email": f"user{i}@example.com", "status": "active"}
               for i in range(620)]
    records[450]["email"] = email
    _w(workspace, "response.json", json.dumps({"records": records}))
    return {"planted": email, "record_id": 450}


def setup_buried(workspace: pathlib.Path, rng: random.Random) -> dict:
    clue = f"the release key is {rng.randint(100_000, 999_999)}"
    lines = [f"log line {i} nominal" for i in range(1_400)]
    lines[700] = clue
    _w(workspace, "build.log", "\n".join(lines) + "\n")
    return {"planted": clue, "cat_hint": "build.log"}


def setup_boundary(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"OFFSET-{rng.randint(1_000, 9_999)}"
    lines = [f"line {i} filler" for i in range(1_700)]
    lines[1_550] = f"critical: retry budget is {token}"
    _w(workspace, "service.log", "\n".join(lines) + "\n")
    return {"planted": token}


def setup_coding(workspace: pathlib.Path, rng: random.Random) -> dict:
    del rng
    _w(workspace, "discount.py",
       "def final_price(price, percent_off):\n"
       "    return price - percent_off  # wrong: must be price * (100 - percent_off) / 100\n")
    _w(workspace, "test_discount.py",
       "from discount import final_price\n"
       "assert final_price(200, 10) == 180, final_price(200, 10)\n"
       "assert final_price(99, 50) == 49.5, final_price(99, 50)\n"
       "print('ok')\n")
    return {"test_cmd": ["python3", "test_discount.py"]}


TASKS = [
    ("log-failure", "The file run.log in this directory is a test-run log. "
     "Use the terminal to inspect it and tell me the exact name of the ONE "
     "failing test. Reply with just that name.",
     setup_log, lambda ws, final, ev: (ev["planted"] in final, "planted name")),
    ("traceback", "The file trace.txt holds a program crash. Use the terminal "
     "to read it. Which exception type was raised, and what is the exact "
     "context token in the error message? Reply with the type and the token.",
     setup_traceback, lambda ws, final, ev: (
         ev["planted_type"] in final and ev["planted_token"] in final, "type+token")),
    ("assertion", "The file pytest.out is test output with exactly one "
     "AssertionError. Use the terminal to find it. Which variable name does "
     "the assertion mention? Reply with just that variable.",
     setup_assert, lambda ws, final, ev: (ev["planted"] in final, "variable")),
    ("patch", "The file change.patch is a proposed diff. Use the terminal to "
     "read it and tell me which file it modifies and the exact exported "
     "constant name the patch adds. Reply with file and name.",
     setup_patch, lambda ws, final, ev: (
         ev["planted_file"] in final and ev["planted_token"] in final, "file+token")),
    ("json-record", "The file response.json holds a large JSON response. Use "
     "the terminal to inspect it. What is the email of the record with "
     "id=450? Reply with just that email.",
     setup_json, lambda ws, final, ev: (ev["planted"] in final, "email")),
    ("buried", "The file build.log is a build log. Somewhere in it is a "
     "sentence of the form 'the release key is N'. Use the terminal to find "
     "it. Reply with the full sentence.",
     setup_buried, lambda ws, final, ev: (ev["planted"] in final, "clue sentence")),
    ("boundary", "The file service.log is a service log. One line states the "
     "retry budget as 'critical: retry budget is OFFSET-N'. Use the terminal "
     "to find it. Reply with the exact budget token.",
     setup_boundary, lambda ws, final, ev: (ev["planted"] in final, "token")),
    ("coding", "In this directory, discount.py has a bug and test_discount.py "
     "fails. Fix discount.py (smallest correct change) and verify with: "
     "python3 test_discount.py",
     setup_coding, lambda ws, final, ev: (
         subprocess.run(ev["test_cmd"], cwd=ws, capture_output=True).returncode == 0,
         "test exits 0")),
]

# Sanity: every fixture must be big enough that SUTRA would rewrite it if it
# reached the model whole (SUTRA_MAX_BYTES 24,576), and small enough to survive
# Hermes' own 50,000-byte terminal cap.
MIN_PAYLOAD_BYTES, MAX_PAYLOAD_BYTES = 26_000, 49_000


def fixture_bytes(workspace: pathlib.Path, name: str) -> int:
    for path in workspace.rglob("*"):
        if path.is_file() and path.stat().st_size > 1_000:
            return path.stat().st_size
    return 0


# --- session execution ------------------------------------------------------

def plugin_state() -> dict:
    """The operator-set arm, read from Hermes itself."""
    try:
        out = subprocess.run(["hermes", "plugins", "list"], capture_output=True,
                             text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return {"sutra_installed": None, "sutra_enabled": None}
    row = next((line for line in out.splitlines()
                if line.strip().startswith("│ sutra")), "")
    cells = [c.strip() for c in row.split("│")]
    status = cells[2] if len(cells) > 2 else ""
    if status not in ("enabled", "disabled"):
        return {"sutra_installed": bool(row), "sutra_enabled": None}
    return {"sutra_installed": True, "sutra_enabled": status == "enabled"}


def session_usage(session_id: str) -> dict | None:
    """Provider-reported usage for the session: the total over every recorded
    row (that is what the provider billed) plus the main-task row on its own,
    so auxiliary calls (title generation) are visible rather than hidden."""
    if not HERMES_STATE.exists() or not session_id:
        return None
    try:
        db = sqlite3.connect(f"file:{HERMES_STATE}?mode=ro", uri=True)
        rows = db.execute(
            "SELECT task, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, api_call_count, estimated_cost_usd, "
            "actual_cost_usd, cost_status FROM session_model_usage "
            "WHERE session_id = ?", (session_id,)).fetchall()
        db.close()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    keys = ("task", "model", "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "api_call_count", "estimated_cost_usd",
            "actual_cost_usd", "cost_status")

    def total(selected):
        out = {"model": selected[0][1] if selected else None,
               "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
               "cache_write_tokens": 0, "api_call_count": 0,
               "estimated_cost_usd": 0.0, "actual_cost_usd": 0.0}
        for row in selected:
            for key in out:
                if key == "model":
                    continue
                if isinstance(out[key], float):
                    out[key] += float(row[keys.index(key)] or 0)
                else:
                    out[key] += int(row[keys.index(key)] or 0)
        return out

    main = [row for row in rows if not row[0]] or rows
    return {"total": total(rows), "main_task": total(main),
            "rows": [dict(zip(keys, row)) for row in rows]}


def _restore_status(content: str) -> str:
    """The status of one sutra_restore tool result; the Tool Search bridge may
    deliver it bare or wrapped in a batch array."""
    if content.lstrip().startswith("[hermes note:"):
        return "deduplicated-by-host"
    try:
        parsed = json.loads(content)
    except ValueError:
        return "unparseable"
    if isinstance(parsed, list):
        parsed = next((entry.get("result", entry) for entry in parsed
                       if isinstance(entry, dict)
                       and entry.get("name") in ("sutra_restore", "tool_call")),
                      None)
    if isinstance(parsed, dict):
        return str(parsed.get("status") or "no-status")
    return "unparseable"


def session_sutra_evidence(session_id: str) -> dict:
    """Markers seen by the model and sutra_restore calls, from the session store.

    The messages table records the serving tool in ``tool_name``; assistant
    ``content`` does not carry the tool call, so the tool rows are the source.
    """
    evidence = {"tool_results_with_marker": 0, "restore_calls": 0,
                "restore_statuses": []}
    if not HERMES_STATE.exists() or not session_id:
        return evidence
    try:
        db = sqlite3.connect(f"file:{HERMES_STATE}?mode=ro", uri=True)
        for tool_name, content in db.execute(
                "SELECT tool_name, content FROM messages "
                "WHERE session_id = ? AND role = 'tool'", (session_id,)):
            if not isinstance(content, str):
                continue
            if tool_name == "sutra_restore":
                evidence["restore_calls"] += 1
                evidence["restore_statuses"].append(_restore_status(content))
            elif "[sutra:" in content:
                evidence["tool_results_with_marker"] += 1
        db.close()
    except sqlite3.Error:
        pass
    return evidence


def run_session(task_name: str, prompt: str, workspace: pathlib.Path,
                timeout: int) -> dict:
    """One real `hermes chat` session in the workspace; returns its record."""
    started = time.time()
    try:
        proc = subprocess.run(
            ["hermes", "chat", "-q", prompt, "--oneshot", "-Q", "--yolo",
             "--max-turns", "12", "--in", str(workspace)],
            capture_output=True, text=True, timeout=timeout, cwd=str(workspace))
    except subprocess.TimeoutExpired as exc:
        # The protocol requires every started session to be recorded; a timeout
        # is a failed session with the partial output kept, never a crashed run.
        stdout = (exc.stdout or "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        match = SESSION_RE.search(stdout)
        return {"final_answer": stdout.strip()[-4_000:],
                "session_id": match.group(1) if match else None,
                "exit_code": None, "timed_out": True,
                "duration_s": round(time.time() - started, 1),
                "stderr_tail": "timed out after %ss" % timeout}
    duration = time.time() - started
    match = SESSION_RE.search(proc.stdout or "")
    return {"final_answer": (proc.stdout or "").strip()[-4_000:],
            "session_id": match.group(1) if match else None,
            "exit_code": proc.returncode, "timed_out": False,
            "duration_s": round(duration, 1),
            "stderr_tail": (proc.stderr or "")[-500:]}


# --- the runner -------------------------------------------------------------

def execute(args: argparse.Namespace) -> int:
    state = plugin_state()
    expected_enabled = args.arm == "on"
    print(f"arm={args.arm}: hermes reports sutra_installed="
          f"{state['sutra_installed']} sutra_enabled={state['sutra_enabled']}")
    if not args.dry_run and state["sutra_enabled"] is not expected_enabled:
        print("ABORT: the live plugin state does not match --arm. Set it with "
              "'hermes plugins disable/enable sutra' and restart the gateway "
              "first; this runner records the state, it does not guess it.")
        return 2

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / f"results-{args.arm}-pass{args.pass_index}.jsonl"
    seed_base = args.seed
    failures = 0
    for index, (name, prompt, setup, scorer) in enumerate(TASKS):
        rng = random.Random(seed_base + index)
        workspace = out_dir / "workspace" / name
        workspace.mkdir(parents=True, exist_ok=True)
        evidence = setup(workspace, rng)
        size = fixture_bytes(workspace, name)
        if not MIN_PAYLOAD_BYTES <= size <= MAX_PAYLOAD_BYTES and name != "coding":
            print(f"{name}: fixture is {size} B, outside the sized window — "
                  f"fix the fixture before running, results would not be comparable")
            return 2
        record = {"arm": args.arm, "task": name, "seed": seed_base + index,
                  "pass_index": args.pass_index, "dry_run": args.dry_run,
                  "plugin_state": state, "fixture_bytes": size, "evidence": evidence,
                  "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        if args.dry_run:
            record.update(final_answer="", session_id=None, scored=False,
                          score_detail="dry-run: no session", usage=None,
                          sutra_evidence=None, duration_s=0.0)
        else:
            session = run_session(name, prompt, workspace, args.timeout)
            passed, detail = scorer(workspace, session["final_answer"], evidence)
            record.update(session, scored=passed, score_detail=detail,
                          usage=session_usage(session["session_id"]),
                          sutra_evidence=session_sutra_evidence(session["session_id"]))
            if not passed:
                failures += 1
            print(f"{name}: {'PASS' if passed else 'FAIL'} "
                  f"({session['duration_s']}s, usage={'yes' if record['usage'] else 'unreported'})")
        with results_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    print(f"wrote {results_path}; {failures} task(s) failed" if not args.dry_run
          else f"wrote {results_path} (dry run)")
    return 0


def selfcheck() -> int:
    """Feed every scorer its own fixture's expected answer; all must pass, and a
    wrong answer must fail. Validates the frozen scoring without any model."""
    failures = 0
    for index, (name, _, setup, scorer) in enumerate(TASKS):
        rng = random.Random(9_000 + index)
        workspace = pathlib.Path("/tmp") / f"sutra-ab-selfcheck-{name}"
        workspace.mkdir(parents=True, exist_ok=True)
        evidence = setup(workspace, rng)
        if "test_cmd" in evidence:  # the coding task: score the workspace itself
            bad, _ = scorer(workspace, "", evidence)
            (workspace / "discount.py").write_text(
                "def final_price(price, percent_off):\n"
                "    return price * (100 - percent_off) / 100\n")
            good, _ = scorer(workspace, "", evidence)
        else:
            planted = evidence.get("planted") or (
                f'{evidence["planted_type"]} {evidence["planted_token"]}'
                if "planted_type" in evidence
                else f'{evidence["planted_file"]} {evidence["planted_token"]}')
            good, _ = scorer(workspace, f"the answer is {planted}", evidence)
            bad, _ = scorer(workspace, "I do not know", evidence)
        ok = good and not bad
        print(f"{name}: scorer {'OK' if ok else 'BROKEN'}")
        if not ok:
            failures += 1
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", choices=("off", "on"), default="on")
    parser.add_argument("--out", default="results/ab")
    parser.add_argument("--seed", type=int, default=202_610_24,
                        help="base seed; both arms of one experiment must share it")
    parser.add_argument("--pass-index", type=int, default=1,
                        help="1 for the first pass, 2 for the reversed-order pass")
    parser.add_argument("--timeout", type=int, default=900,
                        help="per-task wall-clock budget in seconds")
    parser.add_argument("--dry-run", action="store_true",
                        help="generate fixtures and records; run no sessions")
    parser.add_argument("--selfcheck", action="store_true",
                        help="validate the scorers and exit")
    args = parser.parse_args()
    if args.selfcheck:
        return selfcheck()
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
