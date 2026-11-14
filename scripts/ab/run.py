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
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

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
    return {"test_cmd": [sys.executable, "test_discount.py"]}


# --- Phase Four additions: representative repo tasks ----------------------
# Appended to TASKS below; pilot tasks 0-7 keep their indices so existing
# tests and scorers are unaffected.

def _big_text(workspace: pathlib.Path, rel: str, lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    # Fixtures must fall inside the sized window so control/treatment arms stay
    # comparable; pad undersized fixtures up to the minimum rather than aborting
    # a whole experiment over one small fixture.
    while len(text.encode("utf-8")) < MIN_PAYLOAD_BYTES:
        lines.append(f"pad-line {len(lines)}")
        text = "\n".join(lines) + "\n"
    _w(workspace, rel, text)


def _all_in_scorer(*keys: str):
    def score(workspace: pathlib.Path, final: str, evidence: dict) -> tuple[bool, str]:
        missing = [k for k in keys if str(evidence.get(k)) not in final]
        return (not missing, " + ".join(keys))
    return score


def _coding_scorer(workspace: pathlib.Path, final: str, evidence: dict) -> tuple[bool, str]:
    rc = subprocess.run(evidence["test_cmd"], cwd=workspace,
                        capture_output=True).returncode
    return (rc == 0, "test exits 0")


def setup_eslint(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"LINT-{rng.randint(10_000, 99_999)}"
    file = f"src/views/View-{rng.randint(100, 999)}.tsx"
    lines = [f"src/components/c{i}.tsx: line {i}, col {i % 100} - 'no-unused-vars' ({i % 3})"
             for i in range(500)]
    lines[250] = f"{file}: line 250, col 14 - '{token}' is defined but never used"
    _big_text(workspace, "eslint.out", lines)
    return {"planted_file": file, "planted_token": token,
            "planted": f"{file} {token}"}


def setup_mypy(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"T{rng.randint(10_000, 99_999)}"
    lines = [f"src/lib/m{i}.ts: line {i}, col {i % 120} - 'no-implicit-any'"
             for i in range(500)]
    lines[250] = f"src/lib/order.ts: line 250, col 9 - '{token}' implicitly has type 'any'"
    _big_text(workspace, "mypy.out", lines)
    return {"planted_token": token, "planted": token}


def setup_pytest_fail(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"test_{rng.randint(1000, 9999)}_retry"
    lines = [f"PASS tests/unit/m{i}.py::case_{i} - {i*0.001:.3f}s"
             for i in range(700)]
    lines[350] = f"FAIL tests/unit/{token}.py::case_350 - expected 200, got 500"
    _big_text(workspace, "pytest.out", lines)
    return {"planted": token}


def setup_cargo_fail(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"pkg_{rng.randint(1000, 9999)}"
    lines = [f"   Compiling crate-{i} v0.1.{i%10} (/src/crate-{i})" for i in range(600)]
    lines += [f"error[E0432]: unresolved import '{token}::missing'",
                  "   --> src/bin/main.rs:120:5",
                  "   |",
                  f"120 | use {token}::missing::do_thing;",
                  "       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ help: a typo in the crate name?"]
    _big_text(workspace, "cargo.out", lines)
    return {"planted": token}


def setup_go_fail(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"BadFile{rng.randint(100, 999)}.go"
    lines = [f"--- FAIL: TestCase{i} ({i*0.01:.2f}s)" for i in range(700)]
    lines.append(f"    profile_test.go:88: {token} not equal: want 7, got 3")
    _big_text(workspace, "go.out", lines)
    return {"planted_file": token, "planted_token": "want 7, got 3",
            "planted": f"{token} want 7, got 3"}


def setup_docker_fail(workspace: pathlib.Path, rng: random.Random) -> dict:
    stage = f"builder-{rng.randint(10, 99)}"
    token = f"DEP-{rng.randint(10_000, 99_999)}"
    lines = [f" => [stage {i} {i%5}] 0.{i%9}s" for i in range(900)]
    lines += [f" => ERROR [{stage}]  0.4s  RUN npm ci",
                  "------",
                  f" > [stage {stage}] RUN npm ci:",
                  f"#11 0.4s npm ERR! code {token}",
                  "#11 0.4s npm ERR! incompatible peer dependency"]
    _big_text(workspace, "docker.out", lines)
    return {"planted_file": stage, "planted_token": token, "planted": f"{stage} {token}"}


def setup_npm_conflict(workspace: pathlib.Path, rng: random.Random) -> dict:
    package = f"lib-{rng.randint(100, 999)}"
    version = f"{rng.randint(1, 9)}.{rng.randint(0, 9)}.{rng.randint(0, 99)}"
    lines = [f"npm info using npm@{rng.randint(6, 10)}.{rng.randint(0, 9)}.{rng.randint(0, 9)}"
             for i in range(700)]
    lines += ["npm ERR! code ERESOLVE",
              "npm ERR! Could not resolve dependency:",
              f"npm ERR! peer {package}@{version} from {package}-peer@{version}",
              f"npm ERR! Conflicting peer dependency {package}@{rng.randint(1, 9)}.0.0"]
    _big_text(workspace, "npm.err", lines)
    return {"planted_file": package, "planted_token": version,
            "planted": f"{package} {version}"}


def setup_git_diff(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"CONST_{rng.randint(1000, 9999)}"
    body = ("+if (retries > 2) log.warn('retrying the job after the backoff window')\\n"
            "-if (retries > 0) log.debug('retrying')\\n") * 300
    _big_text(workspace, "change.patch",
              ["diff --git a/src/job.ts b/src/job.ts",
               "--- a/src/job.ts",
               "+++ b/src/job.ts",
               "@@ -12,7 +12,7 @@",
               body,
               f"+export const {token} = true"])
    return {"planted_file": "src/job.ts", "planted_token": token,
            "planted": f"src/job.ts {token}"}


def setup_trace_deep(workspace: pathlib.Path, rng: random.Random) -> dict:
    fn = f"handle_{rng.randint(1000, 9999)}"
    lines = [f'  File "/app/m{i}.py", line {i}, in h{i}' for i in range(600)]
    lines[300] = f'  File "/app/{fn}.py", line 300, in {fn}'
    _big_text(workspace, "trace.txt",
              ["Traceback (most recent call last):"] + lines +
              [f"ValueError: aborting because {fn} did not resolve"])
    return {"planted_file": f"{fn}.py", "planted_token": fn,
            "planted": f"{fn}.py {fn}"}


def setup_passing_swamp(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"test_flaky_{rng.randint(1000, 9999)}"
    lines = [f"PASS tests/integration/i{i}.py::case_{i}" for i in range(800)]
    lines[400] = f"FAIL tests/integration/{token}.py::case_400 - timeout"
    _big_text(workspace, "swamp.out", lines)
    return {"planted": token}


def setup_config_hunt(workspace: pathlib.Path, rng: random.Random) -> dict:
    key = f"feature.flag.{rng.randint(1000, 9999)}"
    value = f"on-{rng.randint(100, 999)}"
    lines = [f"# config block {i}\nkey_{i} = value_{i}\n"            for i in range(700)]
    lines.insert(550, f"{key} = {value}")
    _big_text(workspace, "app.ini", lines)
    return {"planted_file": key, "planted_token": value, "planted": f"{key} {value}"}


def setup_trace_id(workspace: pathlib.Path, rng: random.Random) -> dict:
    trace = f"TR-{rng.randint(100_000_000, 999_999_999)}"
    lines = [f"[{i*11}ms] span s{i} op=op{i%7} status=ok" for i in range(800)]
    lines[400] = f"[{400*11}ms] span s400 op=trace status=ok traceId={trace}"
    _big_text(workspace, "trace.log", lines)
    return {"planted": trace}


def setup_deprecation(workspace: pathlib.Path, rng: random.Random) -> dict:
    package = f"deprecated-{rng.randint(100, 999)}"
    lines = [f"WARNING: package p{i} uses deprecated API (v{i%4})" for i in range(700)]
    lines += [f"WARNING: {package}@1.2.3 uses fs.statSync callback (deprecated since v20)",
              "WARNING: package z9 uses deprecated API"]
    _big_text(workspace, "deprecations.log", lines)
    return {"planted": package}


def setup_coverage(workspace: pathlib.Path, rng: random.Random) -> dict:
    file = f"src/lib/mod_{rng.randint(100, 999)}.ts"
    line = rng.randint(50, 300)
    lines = ["File      | % Stmts | % Branch | % Funcs | % Line | Uncovered Line #s",
             "----------|---------|----------|---------|--------|-------------------"]
    for i in range(350):
        lines.append(f"src/lib/m{i}.ts   | 100     | 100      | 100     | 100    | -")
    lines.append(f"{file}   | 88      | 76       | 90      | 85     | {line}, {line+1}")
    _big_text(workspace, "coverage.txt", lines)
    return {"planted_file": file, "planted_token": str(line),
            "planted": f"{file} {line}"}


def setup_flaky_test(workspace: pathlib.Path, rng: random.Random) -> dict:
    token = f"test_retry_{rng.randint(1000, 9999)}"
    lines = [f"PASS tests/unit/u{i}.py::case_{i}" for i in range(800)]
    lines += [f"FAIL tests/unit/{token}.py::case_0 - flaky",
              f"PASS tests/unit/{token}.py::case_0 - retry 1",
              f"FAIL tests/unit/{token}.py::case_0 - flaky",
              f"PASS tests/unit/{token}.py::case_0 - retry 2"]
    _big_text(workspace, "flaky.out", lines)
    return {"planted": token}


def setup_race_detector(workspace: pathlib.Path, rng: random.Random) -> dict:
    gr = f"goroutine {rng.randint(10000, 99999)}"
    file = f"internal/race/r{rng.randint(100, 999)}.go"
    lines = [f"WARNING: DATA RACE\nRead at 0x{rng.randint(0, 9999):x} by goroutine {rng.randint(1000,9999)}:"
             for i in range(450)]
    lines.append(f"Previous write at 0x{rng.randint(0, 9999):x} by {gr}")
    lines.append(f"  {file}:120: main")
    _big_text(workspace, "race.out", lines)
    return {"planted_file": gr, "planted_token": file, "planted": f"{gr} {file}"}


def setup_deadlock(workspace: pathlib.Path, rng: random.Random) -> dict:
    thread = f"Thread-{rng.randint(1000, 9999)}"
    resource = f"db-pool-{rng.randint(1, 20)}"
    lines = [f"[t{i}] lock acquired mutex-{i%10}" for i in range(800)]
    lines += ["DEADLOCK DETECTED",
              f"{thread} is waiting for {resource} held by Thread-0",
              f"Thread-0 is waiting for mutex-5 held by {thread}"]
    _big_text(workspace, "deadlock.log", lines)
    return {"planted_file": thread, "planted_token": resource,
            "planted": f"{thread} {resource}"}


def setup_oom_killer(workspace: pathlib.Path, rng: random.Random) -> dict:
    process = f"indexer-{rng.randint(100, 999)}"
    pid = rng.randint(10_000, 65_000)
    lines = [f"[mem] node-{i} heap {i*10} MB ok" for i in range(900)]
    lines += ["oom-killer: invoke_cgroup_oom",
              f"Killed process {pid} ({process}) total-vm:2400000kB anon-rss:900000kB"]
    _big_text(workspace, "oom.log", lines)
    return {"planted_file": process, "planted_token": str(pid),
            "planted": f"{process} {pid}"}


def setup_slow_query(workspace: pathlib.Path, rng: random.Random) -> dict:
    query = f"SELECT * FROM events_{rng.randint(1000, 9999)} WHERE ts > now()"
    duration = f"{rng.randint(5, 60)}.{rng.randint(0, 9)}s"
    lines = [f"[query] {i*7}ms SELECT * FROM log_{i} LIMIT 10" for i in range(650)]
    lines.append(f"[slow] {duration} {query}")
    _big_text(workspace, "slow.log", lines)
    return {"planted_file": query, "planted_token": duration,
            "planted": f"{query} {duration}"}


# Coding tasks: buggy source + failing test; the fix makes the test exit 0.

def setup_py_fix_factorial(workspace: pathlib.Path, rng: random.Random) -> dict:
    del rng
    _w(workspace, "factorial.py",
       "def factorial(n):\n"
       "    return n * factorial(n - 1)\n")
    _w(workspace, "test_factorial.py",
       "from factorial import factorial\n"
       "assert factorial(5) == 120\n"
       "assert factorial(0) == 1\n"
       "print('ok')\n")
    return {"test_cmd": [sys.executable, "test_factorial.py"],
            "fix_source": "factorial.py",
            "fix_content": "def factorial(n):\n"
                           "    return 1 if n <= 1 else n * factorial(n - 1)\n",
            "planted": "missing base case"}


def setup_py_fix_grades(workspace: pathlib.Path, rng: random.Random) -> dict:
    del rng
    _w(workspace, "grades.py",
       "def letter_grade(score):\n"
       "    return 'A' if score >= 90 else 'B' if score >= 80 else 'C' if score >= 70 else 'D'\n")
    _w(workspace, "test_grades.py",
       "from grades import letter_grade\n"
       "assert letter_grade(55) == 'F'\n"
       "assert letter_grade(85) == 'B'\n"
       "print('ok')\n")
    return {"test_cmd": [sys.executable, "test_grades.py"],
            "fix_source": "grades.py",
            "fix_content": "def letter_grade(score):\n"
                           "    return 'A' if score >= 90 else 'B' if score >= 80 else 'C' if score >= 70 else 'D' if score >= 60 else 'F'\n",
            "planted": "missing F grade"}


def setup_py_fix_pricing(workspace: pathlib.Path, rng: random.Random) -> dict:
    del rng
    _w(workspace, "pricing.py",
       "def total(items, tax):\n"
       "    return sum(items) + tax\n")
    _w(workspace, "test_pricing.py",
       "from pricing import total\n"
       "assert total([100, 200], 0.1) == 330\n"
       "print('ok')\n")
    return {"test_cmd": [sys.executable, "test_pricing.py"],
            "fix_source": "pricing.py",
            "fix_content": "def total(items, tax):\n"
                           "    return sum(items) * (1 + tax)\n",
            "planted": "tax added instead of multiplied"}


EXTRA_TASKS = [
    ("eslint", "The file eslint.out holds ESLint output for many components. "
     "Find the single unused-variable error: which file and which exact token "
     "is flagged? Reply with the file and the token.",
     setup_eslint, _all_in_scorer("planted_file", "planted_token")),
    ("mypy", "The file mypy.out holds type-checker output. Which exact token "
     "is flagged as implicitly typed? Reply with just that token.",
     setup_mypy, _all_in_scorer("planted_token")),
    ("pytest-fail", "The file pytest.out holds test output with exactly one "
     "failure. Which test failed? Reply with just the test file name.",
     setup_pytest_fail, _all_in_scorer("planted")),
    ("cargo-fail", "The file cargo.out holds a Rust build that fails to "
     "compile. Which crate token is the unresolved import? Reply with just that token.",
     setup_cargo_fail, _all_in_scorer("planted")),
    ("go-fail", "The file go.out holds Go test output. Which test file and "
     "which assertion failed? Reply with the file and the comparison.",
     setup_go_fail, _all_in_scorer("planted_file", "planted_token")),
    ("docker-fail", "The file docker.out holds a failed Docker build. Which "
     "build stage failed and which error code was reported? Reply with stage and code.",
     setup_docker_fail, _all_in_scorer("planted_file", "planted_token")),
    ("npm-conflict", "The file npm.err holds a failed npm install. Which "
     "package and version conflict? Reply with package and version.",
     setup_npm_conflict, _all_in_scorer("planted_file", "planted_token")),
    ("git-diff", "The file change.patch is a proposed diff. Which file does "
     "it modify and which exported constant does it add? Reply with file and name.",
     setup_git_diff, _all_in_scorer("planted_file", "planted_token")),
    ("trace-deep", "The file trace.txt holds a deep traceback. Which function "
     "and file raised the final error? Reply with function and file.",
     setup_trace_deep, _all_in_scorer("planted_token", "planted_file")),
    ("passing-swamp", "The file swamp.out holds 800 passing tests and one "
     "failure. Which integration test failed? Reply with just the test name.",
     setup_passing_swamp, _all_in_scorer("planted")),
    ("config-hunt", "The file app.ini is a large configuration. Which feature "
     "flag key is set and to what value? Reply with key and value.",
     setup_config_hunt, _all_in_scorer("planted_file", "planted_token")),
    ("trace-id", "The file trace.log holds a distributed trace. Which trace ID "
     "appears in the trace span? Reply with just the trace ID.",
     setup_trace_id, _all_in_scorer("planted")),
    ("deprecation", "The file deprecations.log holds deprecation warnings. "
     "Which package uses a deprecated fs.statSync callback? Reply with just the package.",
     setup_deprecation, _all_in_scorer("planted")),
    ("coverage", "The file coverage.txt holds a coverage report. Which source "
     "file has uncovered lines and what is the first uncovered line number? "
     "Reply with file and line number.",
     setup_coverage, _all_in_scorer("planted_file", "planted_token")),
    ("flaky-test", "The file flaky.out holds flaky test reruns. Which test "
     "fails and passes across retries? Reply with just the test name.",
     setup_flaky_test, _all_in_scorer("planted")),
    ("race-detector", "The file race.out holds a data-race report. Which "
     "goroutine and source file are implicated? Reply with goroutine and file.",
     setup_race_detector, _all_in_scorer("planted_file", "planted_token")),
    ("deadlock", "The file deadlock.log holds a deadlock report. Which thread "
     "and which resource are deadlocked? Reply with thread and resource.",
     setup_deadlock, _all_in_scorer("planted_file", "planted_token")),
    ("oom-killer", "The file oom.log holds an OOM-killer report. Which process "
     "was killed and what was its PID? Reply with process and PID.",
     setup_oom_killer, _all_in_scorer("planted_file", "planted_token")),
    ("slow-query", "The file slow.log holds query timings. Which query was "
     "slow and how long did it take? Reply with the query and duration.",
     setup_slow_query, _all_in_scorer("planted_file", "planted_token")),
    ("py-fix-factorial", "factorial.py crashes on any input and test_factorial.py "
     "fails. Fix factorial.py (smallest correct change) and verify by "
     "running test_factorial.py.",
     setup_py_fix_factorial, _coding_scorer),
    ("py-fix-grades", "grades.py is missing a grade band and test_grades.py "
     "fails. Fix grades.py (smallest correct change) and verify by "
     "running test_grades.py.",
     setup_py_fix_grades, _coding_scorer),
    ("py-fix-pricing", "pricing.py computes tax wrong and test_pricing.py "
     "fails. Fix pricing.py (smallest correct change) and verify by "
     "running test_pricing.py.",
     setup_py_fix_pricing, _coding_scorer),
]


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
     "fails. Fix discount.py (smallest correct change) and verify by "
     "running test_discount.py.",
     setup_coding, lambda ws, final, ev: (
         subprocess.run(ev["test_cmd"], cwd=ws, capture_output=True).returncode == 0,
         "test exits 0")),
]
TASKS.extend(EXTRA_TASKS)

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

# The host probe is a full CLI start. `hermes plugins list` measured 26.6 s on
# one run and 44.5 s on a later loaded one, so 120 s leaves over 2x headroom over
# the slowest observed while still bounding a hung CLI. A dry run never probes;
# `plugin_state()` accepts a tighter timeout for callers that need one.
HOST_PROBE_TIMEOUT_S = 120.0


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def plugin_state(timeout: float = HOST_PROBE_TIMEOUT_S) -> dict:
    """The operator-set arm, read from Hermes itself, within `timeout`."""
    try:
        out = subprocess.run(["hermes", "plugins", "list"], capture_output=True,
                             text=True, timeout=timeout).stdout
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


def session_tool_calls(session_id: str) -> int:
    """Best-effort count of tool-calling assistant messages for the session.

    Hermes records the served tool in ``tool_name`` on tool rows; a nonzero
    ``tool_name`` on an assistant row marks a tool-calling turn. Returns 0 when
    the store is unavailable so a missing provider never fails a run.
    """
    if not HERMES_STATE.exists() or not session_id:
        return 0
    try:
        db = sqlite3.connect(f"file:{HERMES_STATE}?mode=ro", uri=True)
        row = db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND role = 'assistant' "
            "AND tool_name IS NOT NULL AND tool_name != ''", (session_id,)).fetchone()
        db.close()
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0


def _count_tokens(text: str) -> int:
    """Best-effort token count (cl100k_base), falling back to a char heuristic
    when tiktoken is unavailable so the runner never hard-depends on it."""
    try:
        import tiktoken  # type: ignore[import-not-found]
        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return max(1, len(text) // 4)


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
    expected_enabled = args.arm == "on"
    if args.dry_run:
        # Mechanics only: a dry run must not depend on the operator's host (the
        # probe is a full CLI start), so it records an unknown state instead.
        state = {"sutra_installed": None, "sutra_enabled": None}
        print(f"arm={args.arm}: dry run, host state not probed")
    else:
        state = plugin_state(getattr(args, "probe_timeout", HOST_PROBE_TIMEOUT_S))
        print(f"arm={args.arm}: hermes reports sutra_installed="
              f"{state['sutra_installed']} sutra_enabled={state['sutra_enabled']}")
        if state["sutra_enabled"] is None:
            if state["sutra_installed"] is None:
                print("ABORT: could not read the live plugin state: the `hermes "
                      "plugins list` probe failed or timed out. Set the arm "
                      "explicitly and re-run.")
            else:
                print("ABORT: `hermes plugins list` does not report sutra as enabled "
                      "or disabled (not installed, or a status this runner does not "
                      "know). Install and enable it, then re-run.")
            return 2
        if state["sutra_enabled"] is not expected_enabled:
            print("ABORT: the live plugin state does not match --arm. Set it with "
                  "'hermes plugins disable/enable sutra' and restart the gateway "
                  "first; this runner records the state, it does not guess it.")
            return 2

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / f"results-{args.arm}-pass{args.pass_index}.jsonl"
    if results_path.exists() and results_path.stat().st_size > 0:
        # A stale file must never mix with a fresh run: records carry
        # started_at/plugin_state, but file-level freshness would be lost.
        print(f"ABORT: {results_path} already has records; a re-run would mix "
              f"arms/passes in one file. Use a fresh --out directory, or remove "
              f"the file if it is truly disposable.")
        return 2
    seed_base = args.seed
    failures = 0
    for index, (name, prompt, setup, scorer) in enumerate(TASKS):
        rng = random.Random(seed_base + index)
        workspace = out_dir / "workspace" / name
        workspace.mkdir(parents=True, exist_ok=True)
        evidence = setup(workspace, rng)
        size = fixture_bytes(workspace, name)
        if ("test_cmd" not in evidence
                and not MIN_PAYLOAD_BYTES <= size <= MAX_PAYLOAD_BYTES):
            print(f"{name}: fixture is {size} B, outside the sized window — "
                  f"fix the fixture before running, results would not be comparable")
            return 2
        record = {"arm": args.arm, "paired": args.paired, "task": name,
                  "seed": seed_base + index, "pass_index": args.pass_index,
                  "dry_run": args.dry_run, "plugin_state": state,
                  "fixture_bytes": size, "evidence": evidence,
                  "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        if args.dry_run:
            record.update(final_answer="", session_id=None, scored=False,
                          score_detail="dry-run: no session", usage=None,
                          tool_calls=None, runner_tokens=None,
                          sutra_evidence=None, duration_s=0.0)
        else:
            session = run_session(name, prompt, workspace, args.timeout)
            passed, detail = scorer(workspace, session["final_answer"], evidence)
            record.update(session, scored=passed, score_detail=detail,
                          usage=session_usage(session["session_id"]),
                          tool_calls=session_tool_calls(session["session_id"]),
                          runner_tokens={"input": _count_tokens(prompt),
                                         "output": _count_tokens(session["final_answer"])},
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
        # A per-run temp directory works on every OS; a literal "/tmp" is not a
        # valid path on Windows, where it resolves to the current drive root.
        workspace = pathlib.Path(tempfile.mkdtemp(prefix=f"sutra-ab-selfcheck-{name}-"))
        try:
            evidence = setup(workspace, rng)
            if "test_cmd" in evidence:  # the coding task: score the workspace itself
                bad, _ = scorer(workspace, "", evidence)
                fix_source = evidence.get("fix_source")
                fix_content = evidence.get("fix_content")
                if fix_source is not None and fix_content is not None:
                    (workspace / fix_source).write_text(fix_content)
                else:
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
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        print(f"{name}: scorer {'OK' if ok else 'BROKEN'}")
        if not ok:
            failures += 1
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", choices=("off", "on"), default="on")
    parser.add_argument("--paired", action="store_true",
                        help="paired experiment mode: run every task on both "
                             "arms (control off / treatment on) with identical "
                             "conditions and a shared seed")
    parser.add_argument("--out", default="results/ab")
    parser.add_argument("--seed", type=int, default=202_610_24,
                        help="base seed; both arms of one experiment must share it")
    parser.add_argument("--pass-index", type=int, default=1,
                        help="1 for the first pass, 2 for the reversed-order pass")
    parser.add_argument("--timeout", type=int, default=900,
                        help="per-task wall-clock budget in seconds")
    parser.add_argument("--probe-timeout", type=_positive_seconds,
                        default=HOST_PROBE_TIMEOUT_S,
                        help="seconds allowed for `hermes plugins list` "
                             f"(default: {HOST_PROBE_TIMEOUT_S:g})")
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
