"""Eight deterministic Hermes workloads used by the cost replay.

Every payload is generated from fixed arithmetic, so two runs on the same
machine produce the same bytes and the same token counts. The sequences are
recorded agent behaviour, not live model decisions: they exist to measure what
SUTRA does to the tool-result context, not to predict how a model would act.
"""

from __future__ import annotations

from harness import Step, Workload


def _lines(prefix: str, count: int, *, width: int = 5) -> list[str]:
    return [f"{prefix} {index:0{width}d} " + "payload " * 6 for index in range(count)]


def _insert(lines: list[str], index: int, text: str) -> list[str]:
    lines[max(0, min(len(lines) - 1, index))] = text
    return lines


def _text(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def exploration() -> Workload:
    tree = _insert(_lines("src/module", 1_400), 700,
                   "src/cost/engine.py")
    grep = _insert(_lines("grep: src/cost/engine.py: match", 900), 450,
                   "grep: src/cost/engine.py:412: def compress(text, ref=None)")
    source = _insert(_lines("def worker():", 2_600), 1_300,
                     "        return evaluate(policy, mode='balanced')")
    targeted = [
        "src/cost/engine.py:412: def compress(text, ref=None):",
        "src/cost/engine.py:420:     if original <= limit: return text, stats",
        "src/cost/engine.py:433:     return rewrite, stats",
    ]
    return Workload(
        id="codebase-exploration",
        description="List a tree, run a broad grep, read a large file, then narrow to the region.",
        steps=(
            Step("list_files", "src", _text(tree), ("src/cost/engine.py",)),
            Step("grep", "worker", _text(grep), ("def compress(text, ref=None)",)),
            Step("read_file", "src/cost/engine.py",
                 _text(source), ("evaluate(policy, mode='balanced')",)),
            Step("grep", "def compress", _text(targeted),
                 ("return rewrite, stats",)),
        ),
    )


def multi_file_change() -> Workload:
    first = _insert(_lines("class Store:", 1_600), 800,
                    "    def put(self, text, session_id=None):")
    second = _insert(_lines("def resolve():", 1_300), 650,
                     "    return Config(policy_mode='balanced')")
    third = _insert(_lines("def restore(args):", 900), 450,
                    "    return _restore(args, store)")
    patch = (_text(["diff --git a/core.py b/core.py", "--- a/core.py", "+++ b/core.py",
                    "@@ -412,6 +412,7 @@ def process_text(text):",
                    "+    hit = reuse.lookup(text, session_id=session_id)"])
             + "".join("+context line %05d\n" % index for index in range(2_000)))
    tests = _insert(_lines("PASSED tests/unit", 1_800), 900,
                    "PASSED tests/test_reuse.py::test_exact_duplicate")
    return Workload(
        id="multi-file-change",
        description="Read three sources, inspect a large patch, then run the suite.",
        steps=(
            Step("read_file", "store.py",
                 _text(first), ("def put(self, text, session_id=None):",)),
            Step("read_file", "config.py",
                 _text(second), ("policy_mode='balanced'",)),
            Step("read_file", "recovery.py",
                 _text(third), ("return _restore(args, store)",)),
            Step("git_diff", "HEAD", patch, ("+    hit = reuse.lookup",), protected=True),
            Step("pytest", "tests", _text(tests),
                 ("test_exact_duplicate",)),
        ),
    )


def debugging() -> Workload:
    failure = _insert(_lines("tests/test_cost.py FAILED", 2_200), 1_100,
                      "E   AssertionError: policy mode was off")
    traceback_text = (
        "Traceback (most recent call last):\n"
        + "".join(f'  File "core.py", line {index}, in process_text\n' for index in range(1_500))
        + "ValueError: policy mode was off\n")
    section = _insert(_lines("def process_text(text):", 700), 350,
                      "    mode = policy.mode(cfg)")
    focused = [
        "tests/test_cost.py::test_policy_mode PASSED",
        "1 passed in 0.41s",
    ]
    return Workload(
        id="debug-failure",
        description="Run a failing suite, read the traceback, inspect the source, rerun narrowly.",
        steps=(
            Step("pytest", "-q", _text(failure),
                 ("AssertionError: policy mode was off",)),
            Step("read_file", "traceback", traceback_text,
                 ("ValueError: policy mode was off",)),
            Step("read_file", "core.py",
                 _text(section), ("mode = policy.mode(cfg)",)),
            Step("pytest", "tests/test_cost.py", _text(focused),
                 ("1 passed",)),
        ),
    )


def large_command_output() -> Workload:
    build = _insert(_lines("[build] compiling", 6_000), 3_000,
                    "[build] src/cost/policy.py: warning: unused import")
    tail = [
        "[build] src/cost/policy.py: warning: unused import",
        "[build] 41 warnings, 2 errors",
        "[build] error: cannot find symbol 'policy'",
        "[build] exit status 2",
    ]
    return Workload(
        id="large-command-output",
        description="Run a full build, then narrow to its tail after a failure.",
        steps=(
            Step("bash", "make -j4", _text(build),
                 ("warning: unused import",)),
            Step("bash", "make -j4 2>&1 | tail -40", _text(tail),
                 ("exit status 2",)),
        ),
    )


def repeated_file_inspection() -> Workload:
    source = _insert(_lines("async def handler(request):", 5_000), 2_500,
                     "    return await dispatch(request, retries=3)")
    listing = _insert(_lines("tests/test_case", 600), 300,
                      "tests/test_policy.py")
    return Workload(
        id="repeated-file-inspection",
        description="Read the same large source three times and the same listing twice.",
        steps=(
            Step("read_file", "server.py",
                 _text(source), ("retries=3",)),
            Step("read_file", "server.py", _text(source), ("retries=3",)),
            Step("list_files", "tests", _text(listing), ("tests/test_policy.py",)),
            Step("read_file", "server.py", _text(source), ("retries=3",)),
            Step("list_files", "tests", _text(listing), ("tests/test_policy.py",)),
        ),
    )


def test_execution() -> Workload:
    suite = _insert(_lines("PASSED tests/test_core.py::case", 3_000), 1_500,
                    "PASSED tests/test_core.py::case_eligibility")
    coverage = _insert(_lines("core.py 95%", 600), 300,
                       "policy.py 91%")
    return Workload(
        id="test-execution",
        description="Run the suite twice (the second run is unchanged) and a coverage report.",
        steps=(
            Step("pytest", "tests", _text(suite),
                 ("case_eligibility",)),
            Step("pytest", "tests", _text(suite),
                 ("case_eligibility",)),
            Step("coverage", "report", _text(coverage), ("policy.py 91%",)),
        ),
    )


def research_tool_calls() -> Workload:
    docs = _insert(_lines('{"field": "system_prompt_section",', 2_400), 1_200,
                   '  "note": "sections render once per new session",')
    search = _insert(_lines("result: plugin middleware -", 700), 350,
                     "result: system prompt section API - documented")
    same_page = _insert(_lines("Hermes plugin guide line", 3_000), 1_500,
                        "register_system_prompt_section is the documented API")
    return Workload(
        id="research-tool-calls",
        description="Fetch API documents, search, then fetch the same guide page again.",
        steps=(
            Step("fetch", "docs/plugin.json", _text(docs),
                 ("sections render once per new session",)),
            Step("search", "system prompt section", _text(search),
                 ("system prompt section API - documented",)),
            Step("fetch", "docs/plugin-guide", _text(same_page),
                 ("register_system_prompt_section",)),
            Step("fetch", "docs/plugin-guide", _text(same_page),
                 ("register_system_prompt_section",)),
        ),
    )


def log_analysis() -> Workload:
    log = _insert(_lines("2026-11-04T09:00:00Z INFO request", 12_000), 6_000,
                  "2026-11-04T09:31:12Z ERROR upstream timeout after 30s")
    errors = _insert(_lines("ERROR upstream timeout", 300), 150,
                     "ERROR upstream timeout after 30s (retry=2)")
    context = _insert(_lines("context: handler frame", 100), 50,
                      "context: retry policy has no jitter")
    return Workload(
        id="large-log-analysis",
        description="Scan a large server log, narrow to errors, read context, rescan.",
        steps=(
            Step("bash", "cat server.log", _text(log),
                 ("ERROR upstream timeout after 30s",)),
            Step("grep", "ERROR", _text(errors),
                 ("retry=2",)),
            Step("bash", "sed -n '5980,6080p' server.log", _text(context),
                 ("retry policy has no jitter",)),
            Step("grep", "ERROR", _text(errors), ("retry=2",)),
        ),
    )


def workloads() -> list[Workload]:
    """The eight workloads in a stable order."""
    return [
        exploration(),
        multi_file_change(),
        debugging(),
        large_command_output(),
        repeated_file_inspection(),
        test_execution(),
        research_tool_calls(),
        log_analysis(),
    ]


__all__ = ["workloads"]
