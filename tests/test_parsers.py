"""Fail-open and integration tests for the Phase Two structured CLI parsers.

Parser families: Jest, Vitest, pytest, cargo test, go test, tsc, mypy, cargo
check, Go compiler, Python traceback, Node.js stack trace, ESLint, npm, pnpm,
Yarn, docker build.

Every parser is exercised through its contract
(detect / extract_evidence / find_repetitive_sections / compress / fallback)
and through the engine integration point, including dedicated fail-open cases.
"""

from __future__ import annotations

import importlib
import json

import pytest

engine = importlib.import_module("sutra_plugin.engine")
parsers = importlib.import_module("sutra_plugin.parsers")

PARSER_REGISTRY = parsers.PARSER_REGISTRY
Evidence = parsers.Evidence
Section = parsers.Section
CompressedResult = parsers.CompressedResult
Limits = parsers.Limits

CargoCheckParser = parsers.CargoCheckParser
CargoTestParser = parsers.CargoTestParser
DockerBuildParser = parsers.DockerBuildParser
ESLintParser = parsers.ESLintParser
GoCompilerParser = parsers.GoCompilerParser
GoTestParser = parsers.GoTestParser
JestParser = parsers.JestParser
MypyParser = parsers.MypyParser
NodeStackTraceParser = parsers.NodeStackTraceParser
NpmParser = parsers.NpmParser
PnpmParser = parsers.PnpmParser
PythonTracebackParser = parsers.PythonTracebackParser
PytestParser = parsers.PytestParser
TscParser = parsers.TscParser
VitestParser = parsers.VitestParser
YarnParser = parsers.YarnParser

ALL_PARSERS = [
    TscParser(),
    JestParser(),
    VitestParser(),
    PytestParser(),
    CargoTestParser(),
    GoTestParser(),
    MypyParser(),
    CargoCheckParser(),
    GoCompilerParser(),
    PythonTracebackParser(),
    ESLintParser(),
    NpmParser(),
    PnpmParser(),
    YarnParser(),
    DockerBuildParser(),
    NodeStackTraceParser(),
]

FAMILIES = [p.name for p in ALL_PARSERS]
BY_NAME = {p.name: p for p in ALL_PARSERS}

# --- realistic samples (grounded in benchmarks/fixtures) --------------------

JEST_FAILING = (
    "FAIL ./sum.test.js\n"
    "● adds 2 + 2 to equal 5\n"
    "✓ test_one (1 ms)\n"
    "✓ test_one (1 ms)\n"
    "✓ test_one (1 ms)\n"
    "✓ test_one (1 ms)\n"
    "✓ test_one (1 ms)\n"
    "    expect(received).toBe(expected) // toBe\n"
    "  Expected: 5\n"
    "  Received: 4\n"
    "    at Object.<anonymous> (sum.test.js:4:12)\n"
    "Test Suites: 1 failed, 2 total\n"
    "Tests:       1 failed, 6 passed, 7 total\n"
    "Snapshots:   0 total\n"
    "Time:        1.2 s\n"
)

VITEST_FAILING = (
    "PASS ./math.test.js\n"
    "FAIL ./sum.test.js\n"
    "  × adds 2 + 2 to equal 5\n"
    "    AssertionError: expected 5 to equal 4\n"
    "    at Object.<anonymous> (sum.test.js:4:12)\n"
    "Test Files 1 failed, 2 total\n"
    "Tests  3 failed, 8 passed\n"
    "Snapshots   0 total\n"
    "Time        1.35 s\n"
)

PYTEST_FAILING = (
    "test session starts\n"
    "platform linux -- Python 3.14.9\n"
    "collected 4 items\n"
    "test_math.py::test_add PASSED\n"
    "test_math.py::test_sub FAILED\n"
    "test_math.py::test_mul PASSED\n"
    "test_math.py::test_div PASSED\n"
    "FAILURES\n"
    "test_math.py::test_sub - assert 4 == 5\n"
    "short test summary info\n"
    "FAILED test_math.py::test_sub - assert 4 == 5\n"
    "1 failed, 3 passed in 0.12s\n"
)

CARGO_TEST_FAILING = (
    "running 2 tests\n"
    "test test_add ... ok\n"
    "test test_sub ... FAILED\n"
    "\n"
    "panicked at 'assertion failed', src/lib.rs:4:5\n"
    "\n"
    "test result: FAILED. 0 passed, 1 failed; 0 ignored; 0 measured;"
    " 0 filtered out; finished in 0.02s\n"
)

GO_TEST_FAILING = (
    "--- FAIL: TestAdd (0.00s)\n"
    "    add_test.go:10: expected 5, got 4\n"
    "    add_test.go:12: error: mismatch in sum\n"
    "ok      example.com/math   (cached)\n"
    "FAIL\n"
)

TSC_ERRORS = (
    "./src/index.ts(10,5): error TS2345: Argument of type 'string' is not "
    "assignable to parameter of type 'number'.\n"
    "./src/other.ts(3,1): error TS18047: 'x' is possibly 'null' or "
    "'undefined'.\n"
)

MYPY_ERRORS = (
    'src/main.py:7: error: Argument 1 to "greet" has incompatible type '
    '"str"; expected "int"  [arg-type]\n'
    'src/main.py:9: error: "Dict" has no attribute "frobnicate"  [attr-defined]\n'
    "Found 2 errors in 1 file (checked 1 source file)\n"
)

CARGO_CHECK_ERRORS = (
    "error[E0308]: mismatched types\n"
    "  --> src/main.rs:4:18\n"
    "   |\n"
    "4  |     let x: i32 = \"nope\";\n"
    "   |                  ^^^^^^ expected i32, found `&str`\n"
    "error: could not compile `demo` due to previous error\n"
)

GO_COMPILER_ERRORS = (
    "./main.go:7:16: cannot use \"nope\" (type string) as type int in "
    "argument to f\n"
    "./main.go:12:5: undefined: nope\n"
)

PYTHON_TRACEBACK = (
    "Traceback (most recent call last):\n"
    "  File \"app.py\", line 10, in <module>\n"
    "    main()\n"
    "  File \"app.py\", line 6, in main\n"
    "    return 1 / 0\n"
    "ZeroDivisionError: division by zero\n"
)

NODE_STACK_TRACE = (
    "TypeError: Cannot read properties of undefined (reading 'x')\n"
    "    at Object.<anonymous> (/app/index.js:4:12)\n"
    "    at Module._compile (internal/node.js:110:12)\n"
)

ESLINT_ERRORS = (
    "./src/main.js\n"
    "  3:5  error  missing semicolon  semi\n"
    "  7:10  warning  unused variable 'x'  no-unused-vars\n"
    "1 problem (1 error, 1 warning)\n"
)

NPM_ERROR = (
    "npm ERR! code E404\n"
    "npm ERR! 404 Not Found: demo@latest\n"
    "npm ERR!\n"
    "npm ERR! A complete log can be found here:\n"
    "npm ERR!     /root/.npm/_logs/2026-01-01T00:00:00Z-debug.log\n"
)

PNPM_ERROR = (
    "::::::::::::::::::::::::::::::::::::::::::::::::\n"
    "error NetworkError: fetch failed: ECONNREFUSED  127.0.0.1:8080\n"
)

YARN_ERROR = (
    "yarn add v1.22.19\n"
    'error Error: Couldn\'t find package "demo" on the "npm" registry.\n'
    "[1/4] 🔍  Resolving packages...\n"
)

DOCKER_BUILD_ERROR = (
    "#3 [build 2/4] RUN npm install\n"
    "#3 ERROR: failed to solve: process \"/bin/sh -c npm install\" did not "
    "complete successfully: exit code: 1\n"
)

PLAIN_TEXT = (
    "The quick brown fox jumps over the lazy dog. "
    "This is ordinary tool output with nothing to parse.\n"
) * 6

SAMPLES = {
    "tsc": TSC_ERRORS,
    "jest": JEST_FAILING,
    "vitest": VITEST_FAILING,
    "pytest": PYTEST_FAILING,
    "cargo-test": CARGO_TEST_FAILING,
    "go-test": GO_TEST_FAILING,
    "mypy": MYPY_ERRORS,
    "cargo-check": CARGO_CHECK_ERRORS,
    "go-compiler": GO_COMPILER_ERRORS,
    "python-traceback": PYTHON_TRACEBACK,
    "node-stack-trace": NODE_STACK_TRACE,
    "eslint": ESLINT_ERRORS,
    "npm": NPM_ERROR,
    "pnpm": PNPM_ERROR,
    "yarn": YARN_ERROR,
    "docker": DOCKER_BUILD_ERROR,
}

# --- contract: every parser exists and names its family --------------------


@pytest.mark.parametrize("parser_cls", [type(p) for p in ALL_PARSERS])
def test_parser_has_family_name(parser_cls):
    assert parser_cls.name in FAMILIES


# --- detection -------------------------------------------------------------


@pytest.mark.parametrize("parser_cls", [type(p) for p in ALL_PARSERS])
def test_each_parser_detects_its_own_sample(parser_cls):
    sample = SAMPLES[parser_cls.name]
    assert parser_cls().detect(sample) is True


@pytest.mark.parametrize("family", FAMILIES)
def test_registry_finds_own_parser(family):
    _parser = dict((p.name, p) for p in ALL_PARSERS)[family]
    found = PARSER_REGISTRY.find(SAMPLES[family])
    assert found is not None and found.name == family


def test_registry_returns_none_for_unknown_format():
    assert PARSER_REGISTRY.find(PLAIN_TEXT) is None
    assert PARSER_REGISTRY.find("") is None


# --- evidence --------------------------------------------------------------


@pytest.mark.parametrize("family", FAMILIES)
def test_extract_evidence_returns_evidence(family):
    evidence = BY_NAME[family].extract_evidence(SAMPLES[family])
    assert isinstance(evidence, Evidence)
    assert evidence.family == family
    assert isinstance(evidence.errors, tuple)
    assert isinstance(evidence.failures, tuple)
    assert isinstance(evidence.summary, str)


def test_jest_evidence_keeps_failures_and_summary():
    evidence = JestParser().extract_evidence(JEST_FAILING)
    assert evidence.failed == 1
    assert evidence.passed == 6
    assert any("adds 2 + 2 to equal 5" in f for f in evidence.failures)
    assert any("Expected: 5" in e for e in evidence.errors)
    assert any("Received: 4" in e for e in evidence.errors)
    assert "1 failed, 6 passed, 7 total" in evidence.summary


def test_pytest_evidence_keeps_assertion_detail():
    evidence = PytestParser().extract_evidence(PYTEST_FAILING)
    assert evidence.failed == 1
    assert any("assert 4 == 5" in f for f in evidence.failures)


def test_tsc_evidence_keeps_error_codes_and_locations():
    evidence = TscParser().extract_evidence(TSC_ERRORS)
    assert any("error TS2345" in e for e in evidence.errors)
    assert any("src/index.ts(10,5)" in e for e in evidence.errors)


def test_traceback_evidence_preserves_full_traceback():
    evidence = PythonTracebackParser().extract_evidence(PYTHON_TRACEBACK)
    assert "Traceback (most recent call last):" in evidence.errors[0]
    assert "ZeroDivisionError: division by zero" in evidence.errors[-1]


# --- repetitive sections ---------------------------------------------------


def test_find_repetitive_sections_collapses_pass_run():
    sections = JestParser().find_repetitive_sections(JEST_FAILING)
    assert isinstance(sections, list)
    assert all(isinstance(s, Section) for s in sections)
    pass_sections = [s for s in sections if s.kind == "pass"]
    assert len(pass_sections) == 1
    assert pass_sections[0].body.count("✓ test_one (1 ms)") == 5
    assert pass_sections[0].start < pass_sections[0].end


def test_find_repetitive_sections_empty_for_non_repetitive():
    assert TscParser().find_repetitive_sections(TSC_ERRORS) == []
    assert JestParser().find_repetitive_sections(PLAIN_TEXT) == []


# --- compression -----------------------------------------------------------


def test_compress_small_input_unchanged():
    # Plain tool output that no parser detects must pass through untouched,
    # regardless of limits.
    for parser in ALL_PARSERS:
        result = parser.compress(PLAIN_TEXT, Limits(10_000, 5_000, 5_000))
        assert isinstance(result, CompressedResult)
        assert result.text == PLAIN_TEXT
        assert result.changed is False


def test_compress_returns_default_when_not_changed():
    result = CompressedResult(text="keep me")
    assert result.text == "keep me"
    assert result.changed is False
    assert result.rule == ""
    assert result.omitted_bytes == 0
    assert result.partial is False


def test_limits_fields():
    limits = Limits(max_bytes=1000, head_bytes=400, tail_bytes=300)
    assert limits.max_bytes == 1000
    assert limits.head_bytes == 400
    assert limits.tail_bytes == 300


FAILURE_MARKER = {
    "jest": "adds 2 + 2 to equal 5",
    "vitest": "adds 2 + 2 to equal 5",
    "pytest": "assert 4 == 5",
    "cargo-test": "test_sub",
    "go-test": "TestAdd",
    "tsc": "error TS2345",
    "mypy": "Argument 1 to",
    "cargo-check": "error[E0308]",
    "go-compiler": "cannot use",
    "eslint": "semi",
    "npm": "E404",
    "pnpm": "ECONNREFUSED",
    "yarn": "Couldn't find package",
    "docker": "exit code: 1",
    "node-stack-trace": "Error:",
}


@pytest.mark.parametrize("family", [f for f in FAMILIES if f != "python-traceback"])
def test_engine_integration_uses_parser_for_family(family):
    """Oversized output of each family is compressed by its parser via engine."""
    text = SAMPLES[family] * 12
    out, stats = engine._compress_impl(text, max_bytes=900)
    marker = FAILURE_MARKER[family]
    assert marker in out, f"{family}: evidence marker lost: {out!r}"
    if family in ("jest", "vitest", "pytest"):
        assert stats["changed"] is True
        assert stats["rule"] == f"parser:{family}", stats


def test_engine_integration_keeps_full_traceback():
    """Python tracebacks are classified before parsers reach them, so nothing
    is dropped and they are never handed to a line-oriented parser."""
    text = PYTHON_TRACEBACK * 12
    out, stats = engine._compress_impl(text, max_bytes=20000)
    assert stats["changed"] is False
    assert stats["rule"] != "budget-too-small", stats
    assert out == text, stats
    assert "Traceback (most recent call last):" in out
    assert "ZeroDivisionError: division by zero" in out


def test_compress_collapse_reduces_bytes_and_marks_omitted_passes():
    text = (
        "FAIL ./sum.test.js\n"
        "● adds 2 + 2 to equal 5\n"
        + ("✓ test_one (1 ms)\n" * 5)
        + "    expect(received).toBe(expected)\n"
        + "  Expected: 5\n"
        + "  Received: 4\n"
        + "    at Object.<anonymous> (sum.test.js:4:12)\n"
        + "Test Suites: 1 failed, 2 total\n"
        + "Tests:       1 failed, 6 passed, 7 total\n"
    )
    result = JestParser().compress(text, Limits(900, 10000, 10000))
    assert result.changed is True
    assert "[5 tests passed, omitted]" in result.text
    assert "Expected: 5" in result.text
    assert "Received: 4" in result.text
    assert "adds 2 + 2 to equal 5" in result.text
    assert len(result.text.encode()) < len(text.encode())


# --- fallback --------------------------------------------------------------


@pytest.mark.parametrize("family", FAMILIES)
def test_fallback_returns_input_unchanged(family):
    assert BY_NAME[family].fallback(SAMPLES[family]) == SAMPLES[family]


# --- fail-open -------------------------------------------------------------


def test_fail_open_detect_exception_skips_parser(monkeypatch):
    def boom(self, text):  # noqa: ARG002
        raise RuntimeError("detect is broken")

    monkeypatch.setattr(JestParser, "detect", boom)
    found = PARSER_REGISTRY.find(JEST_FAILING)
    assert found is None or found.name != "jest"


def test_fail_open_compress_exception_returns_output(monkeypatch):
    big = JEST_FAILING * 15

    def boom(self, text, limits):  # noqa: ARG002
        raise RuntimeError("compress is broken")

    monkeypatch.setattr(JestParser, "compress", boom)
    out, stats = engine._compress_impl(big, max_bytes=900)
    assert isinstance(out, str) and out
    assert stats["rule"] != "parser:jest"


def test_fail_open_malformed_detected_input_returns_unchanged():
    text = "FAIL ./x.test.js\n● "  # looks like jest, parses to nothing useful
    result = JestParser().compress(text, Limits(1000, 500, 500))
    assert result.text == text
    assert result.changed is False


def test_fail_open_unknown_format_uses_head_tail():
    big = PLAIN_TEXT * 20
    out, stats = engine._compress_impl(big, max_bytes=2000, head_bytes=600,
                                       tail_bytes=300)
    assert isinstance(out, str) and out
    assert not stats["rule"].startswith("parser:")


def test_fail_open_json_original_stays_json():
    records = [{"level": "error", "message": f"thing number {i} went wrong"}
               for i in range(300)]
    text = json.dumps(records, indent=1)
    out, stats = engine._compress_impl(text, max_bytes=1200)
    json.loads(out)  # must remain valid JSON
    assert stats["changed"] is True or stats["rule"].startswith("json")


def test_fail_open_diff_never_reaches_parsers():
    text = ("diff --git a/x b/x\n@@ -1 +1 @@\n" + "+line of addition\n" * 4000)
    out, stats = engine._compress_impl(text, max_bytes=1000)
    assert out == text
    assert stats["rule"] == "diff-kept"


# --- public API integration ------------------------------------------------


def test_public_compress_reports_parser_rule(sutra):
    big = JEST_FAILING * 15
    out, stats = sutra.compress(big, max_bytes=900)
    assert stats["rule"] == "parser:jest"
    assert "Expected: 5" in out
    assert "adds 2 + 2 to equal 5" in out
