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

## What it does

A Hermes `tool_execution` middleware: the tool runs normally, then its result is passed through four deterministic rules before the model sees it.

| Rule | Applies to | Effect |
|---|---|---|
| ANSI strip | any result | removes colour/control escapes |
| Repeat collapse | runs of identical lines | keeps 2, replaces the rest with `[tooltrim: N more identical line(s) omitted]` |
| Head+tail elision | results over `TOOLTRIM_MAX_BYTES` | keeps the start and the end, inserts an explicit elision marker with the byte count |
| JSON array elision | a payload that is one long JSON array | **off by default** (`TOOLTRIM_JSON_ITEMS`); when enabled, keeps first/last items and reports the count |

Two payloads are **never** rewritten:

- **diffs and patches** (`diff --git`, `--- a/`, `+++ b/`, `@@`) — returned byte-identical
- **tracebacks** — the middle is elided but the error end is always preserved

Anything that is not a string or a dict, any parse error, and any internal exception returns the original object untouched. The plugin fails open by design.

## Safety contract

1. **It only removes.** No summarising, no rewriting, no invented content.
2. **It always says what it removed** — byte counts and item counts, in a marker the model can read.
3. **It tells the model how to recover** the elided part: narrow the query, use `grep`, `head`, or an offset.
4. **It never touches diffs**, and never drops the tail of a traceback.
5. **It is measurable**: every compression appends a record to a local JSONL file.

## Install

```bash
git clone https://github.com/<you>/tooltrim ~/.hermes/plugins/tooltrim
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

## Measuring the saving

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
```

Fifteen tests cover every rule, both never-touch payloads, the fail-open paths and the stats file. No Hermes runtime is needed to run them.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports with a reproducible result shape are the most useful contribution; a rule that removes more than it should is a security-grade bug, not a tuning issue.

## Licence

MIT.
