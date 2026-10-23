# tooltrim

Deterministic compression of oversized tool output for [Hermes Agent](https://hermes-agent.nousresearch.com) — because agent bills are dominated by context re-sent every turn, not by the prose the model writes.

## Why this exists

Measured on a real seven-agent Hermes fleet over two days:

```
input tokens          16,330,829
output tokens          3,258,687
cache-read tokens    486,503,074     ← 96.8% of all tokens
```

Nearly every token was context being re-read on the next turn. Tool output is the part of that context that grows without limit: a `cat` of a 2 MB log, a 900-row SQL result, a test run's 6,000 lines. All of it is re-sent on every subsequent turn of the task.

Compressing the model's *replies* attacks the 3.2M. Compressing *tool output* attacks the 486M. This plugin does the second one.

![How tooltrim works: the tool runs, five ordered rules decide what happens to its result — untouched when small, byte-identical for diffs, decoded and re-encoded when it is a JSON document, head+tail with the error end kept for a traceback, and ANSI strip plus repeat collapse plus head+tail for everything else — then the model sees a smaller result, with every rewrite recorded in a stats file](docs/how-it-works.svg)

## What it does

A Hermes `tool_execution` middleware: the tool runs normally, then its result is passed through four deterministic rules before the model sees it.

| Rule | Applies to | Effect |
|---|---|---|
| ANSI strip | any result | removes colour/control escapes |
| Repeat collapse | runs of identical lines | keeps 2, replaces the rest with `[tooltrim: N more identical line(s) omitted]` |
| Head+tail elision | plain text results over `TOOLTRIM_MAX_BYTES` | keeps the start and the end, inserts an explicit elision marker with the byte count |
| JSON field elision | a payload that *is* a JSON document | decodes it, runs the same rules on its string fields, re-encodes — never byte-sliced |
| JSON array elision | a payload that is one long JSON array | **off by default** (`TOOLTRIM_JSON_ITEMS`); when enabled, keeps first/last items and reports the count |

Two payloads are **never** rewritten:

- **diffs and patches** (`diff --git`, `--- a/`, `+++ b/`, `@@`) — returned byte-identical
- **tracebacks** — the middle is elided but the error end is always preserved

A JSON document is never cut at a byte offset. Byte 12,288 of a serialized document can land inside an escape (`\n`, `\u00e9`), and the result stops parsing — `json.loads` raises `Invalid control character at line 1 column 12289`, which is a lost tool call, not a saved token. Documents are decoded, their string fields compressed and the document re-encoded; if a document has nothing worth eliding it is returned untouched, and if the re-encoded text somehow fails to parse, the original is returned.

Anything that is not a string or a dict, any parse error, and any internal exception returns the original object untouched. The plugin fails open by design.

## Safety contract

1. **It only removes.** No summarising, no rewriting, no invented content.
2. **It always says what it removed** — byte counts and item counts, in a marker the model can read.
3. **It tells the model how to recover** the elided part: narrow the query, use `grep`, `head`, or an offset.
4. **It never touches diffs**, and never drops the tail of a traceback.
5. **It never breaks a payload's own format.** JSON in, valid JSON out.
6. **It is measurable**: every compression appends a record to a local JSONL file.

## Install

```bash
git clone https://github.com/g0GobliN/tooltrim ~/.hermes/plugins/tooltrim
hermes plugins enable tooltrim
```

Confirm it loaded:

```bash
hermes plugins list | grep tooltrim     # → enabled
```

## Configuration

Environment variables, read on every call:

| Variable | Default | Meaning |
|---|---|---|
| `TOOLTRIM_ENABLED` | `1` | `0` disables the plugin entirely |
| `TOOLTRIM_MAX_BYTES` | `24576` | results at or below this are untouched |
| `TOOLTRIM_HEAD_BYTES` | `12288` | bytes kept from the start |
| `TOOLTRIM_TAIL_BYTES` | `6144` | bytes kept from the end |
| `TOOLTRIM_JSON_ITEMS` | `0` (off) | JSON arrays longer than this get elided; **off by default because exact data is a correctness risk** |
| `TOOLTRIM_STATS_FILE` | `~/.hermes/cache/tooltrim-stats.jsonl` | where savings are recorded |

## Measured, not claimed

![Horizontal bar chart: bytes before and after tooltrim on six real payload shapes. 12,000-line command output 60.9 kB → 18.6 kB (69.5%); JSON tool envelope 85.9 kB → 19.3 kB (77.6%); test run of 2,000 cases 166.6 kB → 18.6 kB (88.9%); 3,000-frame traceback 359.4 kB → 10.4 kB (97.1%); 600 KB patch 432.1 kB → unchanged (safety case); 900-row SQL result 46.6 kB → 18.6 kB (60.2%). Total 1.2 MB → 517.5 kB, 55.1% saved.](bench/savings.svg)

| payload | rule | before | after | saved |
|---|---|---|---|---|
| 12,000-line command output | `head-tail` | 60.9 kB | 18.6 kB | 69.5% |
| JSON tool envelope | `json-fields` | 85.9 kB | 19.3 kB | 77.6% |
| test run, 2,000 cases | `head-tail+dedupe` | 166.6 kB | 18.6 kB | 88.9% |
| 3,000-frame traceback | `traceback` | 359.4 kB | 10.4 kB | 97.1% |
| 600 KB patch | `diff-kept` | 432.1 kB | 432.1 kB | 0.0% |
| 900-row SQL result | `head-tail` | 46.6 kB | 18.6 kB | 60.2% |

Reproduce it — the payloads are generated, the compressor is the real plugin, nothing touches the network:

```bash
python3 bench/benchmark.py        # prints this table, writes bench/results.json and bench/savings.svg
```

The patch row is the one to read twice. A compressor that also shrinks your diffs is a compressor that can hide a code change, so that row saves nothing on purpose.

Savings on your own machine, from the plugin's own log:

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path.home() / ".hermes/cache/tooltrim-stats.jsonl"
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
before = sum(r["original_bytes"] for r in rows)
after = sum(r["compressed_bytes"] for r in rows)
print(f"{len(rows)} compressions, {before:,} → {after:,} bytes ({1 - after / before:.1%} saved)")
PY
```

The honest test is a before/after on your own workload: run the same task with the plugin disabled and enabled and compare `input_tokens` in the session row.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest -q
python3 bench/benchmark.py        # optional: refreshes the numbers in this README
```

Twenty-four tests cover every rule, both never-touch payloads, the fail-open paths, the stats file, and the JSON invariant (a document in must be a valid document out). No Hermes runtime is needed to run them.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports with a reproducible result shape are the most useful contribution; a rule that removes more than it should is a security-grade bug, not a tuning issue.

## Licence

MIT.
