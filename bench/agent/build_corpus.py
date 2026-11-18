"""Write the reproducible Hermes task corpus used by run_abc.py.

Every task is one or more shell commands whose output is the payload the model
sees. Nothing here is model-generated, and no task is tuned to favour SUTRA:
the corpus includes payloads where compression is expected to help (logs,
prose, structured JSON) and payloads where it cannot (a patch, a large array of
numbers).

Each scenario carries ``expect``: ``helpful``, ``neutral`` or ``no-benefit``.
That is a prediction to check, not a result.
"""

from __future__ import annotations

import argparse
import json
import pathlib

ROOT = "/home/goblin01/github_projects/tooltrim-goals"
PY = "/home/goblin01/github_projects/tooltrim/.venv/bin/python"

SCENARIOS = [
    ("01-repo-exploration", "repository exploration", [
        f"find {ROOT} -type f -not -path '*/.git/*' -printf '%10s %p\\n' | sort -n | tail -600"],
     "helpful"),
    ("02-code-modification", "code modification", [
        "python3 -c \"import pathlib;p=pathlib.Path('/tmp/sutra-corpus/big.py');"
        "p.parent.mkdir(exist_ok=True);lines=[f'def f{i}(x):' + chr(10) + f'    return x + {i}' for i in range(900)];"
        "p.write_text(chr(10).join(lines));print(p.read_text())\""], "helpful"),
    ("03-debugging", "debugging", [
        "python3 " + str(pathlib.Path(__file__).resolve().parent / "payloads" / "fake_traceback.py")],
     "helpful"),
    ("04-multi-file", "multi-file implementation", [
        "python3 -c \"import pathlib;d=pathlib.Path('/tmp/sutra-corpus/pkg');d.mkdir(parents=True,exist_ok=True);"
        "[(d/f'part{i}.py').write_text(chr(10).join(f'def f{j}(x):' + chr(10) + f'    return x * {j}' for j in range(400))) for i in range(3)];"
        "print(chr(10).join(str(p)+':'+p.read_text() for p in sorted(d.glob('*.py'))))\""], "helpful"),
    ("05-test-execution", "test execution and repair", [
        f"{PY} -m pytest {ROOT}/tests --collect-only -q"], "helpful"),
    ("06-long-context", "long-context reasoning", [
        "seq 1 12000"], "helpful"),
    ("07-repeated-operations", "repeated tool operations", [
        f"find {ROOT} -type f -not -path '*/.git/*' -printf '%10s %p\\n' | sort -n | tail -600",
        f"find {ROOT} -type f -not -path '*/.git/*' -printf '%10s %p\\n' | sort -n | tail -600",
        f"ls {ROOT} | head -40"], "neutral"),
    ("08-documentation", "documentation", [
        f"cat {ROOT}/README.md {ROOT}/docs/accounting.md"], "helpful"),
    ("09-structured-json", "structured JSON output", [
        "python3 -c \"import json;print(json.dumps([{'id':i,'path':'src/file_%d.py'%i,"
        "'ok':True,'note':'unchanged'} for i in range(300)],indent=1))\""], "helpful"),
    ("10-strict-preservation", "strict preservation requirements", [
        "python3 -c \"[print('keep /etc/passwd, https://example.com/x and 3 tests;"
        " do not delete 42 rows') for _ in range(600)]\""], "neutral"),
    ("11-repeated-context", "repeated context", [
        "python3 -c \"[print('It is important to note that worker %d needs to check"
        " the queue again.   ' % i) for i in range(3000)]\""], "helpful"),
    ("12-multi-tool-calls", "multiple tool calls", [
        f"git -C {ROOT} log --stat -30",
        f"git -C {ROOT} status --short",
        f"ls -la {ROOT} | head -20",
        "python3 --version"], "neutral"),
    ("13-diff-control", "diff must stay byte-identical", [
        f"git -C {ROOT} log -p -2"], "no-benefit"),
    ("14-json-array-control", "large numeric array", [
        "python3 -c \"import json;print(json.dumps(list(range(20000))))\""], "no-benefit"),
    ("15-build-log", "build and lint output", [
        "python3 -c \"[print('[%3d%%] Building CXX object src/very/long/path/file_%d.cpp.o' % (i%100,i))"
        " for i in range(600)]\""], "helpful"),
]

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parent / "corpus")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, category, commands, expect in SCENARIOS:
        payload = {
            "id": name,
            "category": category,
            "expect": expect,
            "commands": commands,
            "final": f"Inspected {category}.",
        }
        (args.out / f"{name}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(SCENARIOS)} scenarios to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
