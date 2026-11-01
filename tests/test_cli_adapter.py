"""The CLI adapter: the shared tool contract over a real subprocess.

Every test spawns ``scripts/sutra-cli.py`` with the test's isolated
HERMES_HOME, so store writes land in the per-test directory exactly as the
Hermes plugin's would. Exit codes and JSON shapes are the contract under
test; no mocking anywhere.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "sutra-cli.py"

# The shared fixture used by the cross-adapter equivalence test: big enough
# to elide (over 24,576 B), clue buried mid-output, deterministic bytes.
SHARED_LINES = [f"log line {i} filler for the shared adapter contract" for i in range(1000)]
SHARED_LINES[500] = "CLUE: adapter-equivalence-token-7Q"
SHARED_PAYLOAD = "\n".join(SHARED_LINES) + "\n"


def run_cli(args, stdin_text=None, timeout=60, env=None):
    result = subprocess.run(
        [sys.executable, str(CLI), *args],
        input=None if stdin_text is None else stdin_text.encode("utf-8"),
        capture_output=True, timeout=timeout, cwd=str(ROOT), env=env)
    # Bytes preserve the fixture exactly on Windows; text-mode pipes would
    # translate LF to CRLF and make a contract comparison platform-dependent.
    return subprocess.CompletedProcess(
        result.args, result.returncode,
        result.stdout.decode("utf-8"), result.stderr.decode("utf-8"))


def run_cli_bytes(args, stdin_bytes=b"", timeout=60, env=None):
    return subprocess.run(
        [sys.executable, str(CLI), *args], input=stdin_bytes, capture_output=True,
        timeout=timeout, cwd=str(ROOT), env=env)


def test_compress_round_trip_restores_the_buried_clue():
    result = run_cli(["compress", "--tool", "bash"], stdin_text=SHARED_PAYLOAD)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    assert payload["marker"] is True
    assert payload["id"]
    assert "adapter-equivalence-token-7Q" not in payload["output"], "clue must be elided"

    restored = run_cli(["restore", "--id", payload["id"], "--mode", "search",
                        "--query", "adapter-equivalence-token-7Q", "--context", "1"])
    assert restored.returncode == 0, restored.stderr
    response = json.loads(restored.stdout)
    assert response["status"] == "ok"
    assert "adapter-equivalence-token-7Q" in response["content"]


def test_search_flags_expose_both_case_modes():
    """`--case-sensitive` used to be a no-op because it was the default; there
    was no way to ask the CLI for case-insensitive search at all."""
    compressed = run_cli(["compress"], stdin_text=SHARED_PAYLOAD)
    body = json.loads(compressed.stdout)
    assert body["id"]

    def search(*extra):
        result = run_cli(["restore", "--id", body["id"], "--mode", "search",
                          "--query", "clue: adapter-equivalence-token-7q", *extra])
        return result, json.loads(result.stdout)

    default, default_body = search()
    explicit, explicit_body = search("--case-sensitive")
    folded, folded_body = search("--ignore-case")

    assert default.returncode == 0, default.stderr
    assert explicit.returncode == 0, explicit.stderr
    assert folded.returncode == 0, folded.stderr
    assert default_body["matches_found"] == 0
    assert explicit_body["matches_found"] == 0
    assert folded_body["matches_found"] == 1
    assert "CLUE: adapter-equivalence-token-7Q" in folded_body["content"]
    assert folded_body["case_sensitive"] is False

    exact = run_cli(["restore", "--id", body["id"], "--mode", "search",
                     "--query", "CLUE: adapter-equivalence-token-7Q",
                     "--case-sensitive"])
    exact_body = json.loads(exact.stdout)
    assert exact.returncode == 0, exact.stderr
    assert exact_body["matches_found"] == 1
    assert exact_body["case_sensitive"] is True

    both = run_cli(["restore", "--id", body["id"], "--mode", "search",
                    "--query", "clue", "--case-sensitive", "--ignore-case"])
    assert both.returncode == 2
    assert "not allowed with argument" in both.stderr


def test_small_input_is_unchanged_with_exit_zero():
    result = run_cli(["compress"], stdin_text="tiny output")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert payload["marker"] is False
    assert payload["id"] is None


def test_explicit_recovery_statuses_are_results_not_failures():
    result = run_cli(["restore", "--id", "su_" + "ab" * 16])
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "not_found"


def test_missing_id_is_a_usage_error():
    result = run_cli(["restore"])
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]


def test_config_reports_resolved_values_without_secrets():
    result = run_cli(["config"])
    assert result.returncode == 0
    config = json.loads(result.stdout)
    assert config["max_bytes"] == 24576
    assert "key" not in json.dumps(config).lower()
    assert "token" not in json.dumps(config).lower()


def test_oversized_input_is_refused_cleanly():
    huge = "x" * (65 * 1024 * 1024)
    result = run_cli(["compress"], stdin_text=huge, timeout=180)
    assert result.returncode == 1
    assert "exceeds" in json.loads(result.stdout)["error"]


def test_diff_payload_comes_back_untouched():
    patch = "diff --git a/x b/x\n@@ -1 +1 @@\n" + "+line\n" * 5000
    result = run_cli(["compress"], stdin_text=patch)
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert payload["output"] == patch


def test_json_array_of_small_objects_is_returned_byte_identical():
    """An array preview would pass the top-level-type check but break item
    consumers; the CLI must return the array untouched instead."""
    data = [{"id": i, "tag": "x"} for i in range(8000)]
    text = json.dumps(data)
    result = run_cli(["compress"], stdin_text=text)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["changed"] is False
    assert payload["id"] is None
    assert payload["output"] == text
    assert len(json.loads(payload["output"])) == len(data)
    assert all(isinstance(item, dict) and set(item) == {"id", "tag"}
               for item in json.loads(payload["output"]))


def test_byte_restore_rejects_a_range_that_splits_a_character():
    """The CLI exposes the same byte-range contract as the library: a range
    that cuts through UTF-8 is an explicit error, not a quietly short page."""
    payload = "€" * 9000
    compressed = run_cli(["compress"], stdin_text=payload)
    body = json.loads(compressed.stdout)
    assert body["id"]
    result = run_cli(["restore", "--id", body["id"], "--mode", "bytes",
                      "--start", "0", "--end", "2"])
    response = json.loads(result.stdout)
    assert response["status"] == "error"
    assert "splits a UTF-8 character" in response["error"]


def test_byte_restore_preserves_a_literal_replacement_character():
    """A range covering one valid U+FFFD must return that character, not strip
    it while claiming the response is complete."""
    payload = "\ufffd" + "a" * 30000
    compressed = run_cli(["compress"], stdin_text=payload)
    assert compressed.returncode == 0, compressed.stderr
    body = json.loads(compressed.stdout)
    assert body["id"]
    result = run_cli(["restore", "--id", body["id"], "--mode", "bytes",
                      "--start", "0", "--end", "3"])
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["status"] == "ok"
    assert response["content"] == "\ufffd"
    assert response["returned_bytes"] == 3
    assert response["complete"] is True


def test_json_array_of_long_strings_keeps_every_element():
    """Safe array compression rewrites string values in place: length, element
    types and keys stay, and the original is recoverable."""
    data = ["\n".join(f"line {j}" for j in range(800)) for _ in range(4)]
    text = json.dumps(data)
    result = run_cli(["compress"], stdin_text=text)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    parsed = json.loads(payload["output"])
    assert isinstance(parsed, list)
    assert len(parsed) == len(data)
    assert all(isinstance(item, str) for item in parsed)
    assert "[sutra:" in parsed[0]

    restored = run_cli(["restore", "--id", payload["id"], "--mode", "full",
                        "--max-bytes", "65536"])
    assert restored.returncode == 0, restored.stderr
    response = json.loads(restored.stdout)
    assert response["status"] == "ok"
    assert response["content"] == text
    assert response["sha256_verified"] is True


def test_missing_file_is_a_clean_error():
    result = run_cli(["compress", "--file", "/nonexistent/path"])
    assert result.returncode == 1
    assert "could not read input" in json.loads(result.stdout)["error"]


def test_non_integer_range_argument_is_a_usage_error():
    result = run_cli(["restore", "--id", "su_" + "ab" * 16, "--start", "ten"])
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]


def test_empty_input_is_an_unchanged_empty_result():
    result = run_cli(["compress"], stdin_text="")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["changed"] is False and payload["output"] == ""


def test_invalid_utf8_on_stdin_is_rejected_not_replaced():
    result = run_cli_bytes(["compress"], b"ok\xff\xfe\n")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "not valid UTF-8" in payload["error"]
    assert b"\xef\xbf\xbd" not in result.stdout


def test_invalid_utf8_file_is_rejected_not_replaced(tmp_path):
    path = tmp_path / "tool-output.txt"
    path.write_bytes(b"ok\xff\xfe\n")
    result = run_cli_bytes(["compress", "--file", str(path)])
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "not valid UTF-8" in payload["error"]
    assert b"\xef\xbf\xbd" not in result.stdout


def test_non_ascii_output_survives_an_ascii_stdout():
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    text = "日本語のログ行\n" * 4000
    result = run_cli(["compress"], stdin_text=text, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.isascii()
    payload = json.loads(result.stdout)
    assert payload["changed"] is True
    assert "日本語" in payload["output"]
