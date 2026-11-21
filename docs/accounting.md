# Token and cost accounting

`cost.py` produces the full accounting set. It never invents a bill: prices come from the caller, usage comes from the provider or
from a named tokenizer, and anything unavailable is `null` with a reason.

## The formula

```
C_total = Σ_calls (input_tokens × input_price / 1e6)
        + Σ_calls (output_tokens × output_price / 1e6)
        + C_other
```

`cached_input_tokens` are part of `input_tokens`, not extra: the provider
already counts them in the input figure, so adding them again would double
count. When a cached rate is supplied it replaces the input rate for exactly
those tokens. `cache_write_input_tokens` are also a subset of billed input,
billed at their own premium rate; with cache writes present and no
`cache_write_per_million`, `price()` returns `None` instead of guessing.

## What one report contains

| Group | Fields |
|---|---|
| Tokens | `original_input`, `billed_input`, `cached_input`, `output`, `original_total`, `optimized_total`, `saved`, `percent_saved`, `tokenizer`, `reduction_basis` |
| Calls | `models`, `tools` |
| Latency | `processing_ms`, `validation_ms`, `overhead_ms` (processing + validation), `provider_ms`, `total_ms` |
| Cost | `baseline`, `actual` (includes SUTRA's `extra_cost`), `net_savings`, `basis`, `estimated_baseline` |
| Quality | `useful_tasks`, `efficiency` = useful tasks / actual cost |

`original_input_tokens` is the counterfactual: the input as it would have been
sent without SUTRA. `billed_input_tokens` is what the provider charged for —
they differ when SUTRA shrank the input, and they also differ when the provider
applied its own prompt cache. The second is never attributed to SUTRA.

## Worked example

```console
$ sutra cost --original-input 100000 --billed-input 80000 --cached 0 \
    --output 1000 --model-calls 1 --tool-calls 3 \
    --processing-ms 10 --validation-ms 5 --input-price 2 --output-price 8 \
    --extra-cost 0.001 --useful-tasks 4 --tokenizer o200k
tokens: 101000 -> 81000 (saved 20000)
percent saved: 19.80198% (same tokenizer; comparable)
calls: 1 model, 3 tool
overhead: 15.0 ms (processing 10.0 + validation 5.0)
cost: explicit pricing
  baseline: 0.208000
  actual:   0.169000 (includes 0.001 overhead)
  net saved: 0.039000
efficiency: 23.668639053254 useful tasks per unit cost
```

The baseline is `100000 × 2/1e6 + 1000 × 8/1e6 = 0.208`; the optimized run is
`80000 × 2/1e6 + 1000 × 8/1e6 = 0.168`, plus the explicit `0.001` of SUTRA
overhead. Net savings are `0.039`, and efficiency is `4 / 0.169`.

## Rules the code enforces

- No price table and no default rate. `Pricing` must be constructed by the
  caller, and without it every cost field is `None` with
  `basis: "unavailable: no pricing supplied"`.
- `cached_input_tokens > billed_input_tokens`, or cached read plus cache write
  above billed input, is an error, not a clamp.
- Negative or non-finite counts, times and costs are errors.
- `percent_saved` is `None` when the baseline has no tokens; it is never `0%`.
- A token reduction is only called comparable when the tokenizer is named;
  otherwise `reduction_basis` says "do not compare across models".
- SUTRA itself ships no tokenizer: the package declares `dependencies = []`, so
  nothing in the plugin needs model weights or an encoder at runtime. The
  four-mode comparison in [modes.md](modes.md) is measured with `o200k_base`
  from the benchmark-only extra (`bench/requirements.txt`), records the tokenizer
  it used, and is labelled a tokenizer measurement rather than a provider's
  billed usage. A provider's own counters, when a payload carries them, always
  win over a local count.
- `estimated_baseline` records whether the counterfactual was measured or
  estimated. The CLI never sets it on its own.

## What is still not available

There is no provider call in this module, so the provider's own usage metadata
must be supplied by the host (`--billed-input`, `--cached`, `--output`,
`--provider-ms`) or read from a saved response body with
`--usage-file response.json --provider anthropic` (parsing in `providers.py`,
shapes in [provider-matrix.md](provider-matrix.md)). The project's measured, model-free numbers live in
[benchmark.md](benchmark.md) and
[phase2-hermes-cost-report.md](phase2-hermes-cost-report.md); the frozen live
A/B protocols in [ab-benchmark.md](ab-benchmark.md) have not been run.

## The savings log

Every rewritten tool result appends one record to `SUTRA_STATS_FILE`
(`$XDG_CACHE_HOME/sutra/sutra-stats.jsonl` by default, rotated once to `.1`).
The record holds the rule and tool that produced it, the before/after byte
counts and whether the result was partial or failed open; it never holds the
content that was removed.

`sutra stats` aggregates the current file and its rotated predecessor, so the
numbers are the same from any process:

```console
$ sutra stats
sutra stats: /home/me/.cache/sutra/sutra-stats.jsonl
  rewrites: 12
  bytes: 1,204,881 before → 200,114 after (1,004,767 saved, 83.4%)
  from 2026-11-19T10:04:11+0900 to 2026-11-21T09:52:03+0900
  by rule:
    traceback: 8 rewrite(s), 940,220 bytes saved
    head-tail: 4 rewrite(s), 64,547 bytes saved
Counters for restores, request-stage rewrites and observed provider usage live
in the host process (`sutra.metrics_snapshot()`); this log holds rewritten tool
results only.
```

`--json` prints the same report as an object; `--file` reads a different log.
The log counts rewrites, not decisions: an unchanged result is counted only in
the host process, because a savings log that recorded every no-op would stop
being about savings. A line that does not parse is counted as `unreadable` and
the rest of the file is still reported; so is a line that parses as JSON but
carries a value where a byte count belongs, because counting it as zero would
report a saving the log does not contain.

