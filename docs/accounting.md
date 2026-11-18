# Token and cost accounting

`cost.py` produces the accounting set goals.md PART 14 asks for. It never
invents a bill: prices come from the caller, usage comes from the provider or
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
those tokens.

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
- `cached_input_tokens > billed_input_tokens` is an error, not a clamp.
- Negative or non-finite counts, times and costs are errors.
- `percent_saved` is `None` when the baseline has no tokens; it is never `0%`.
- A token reduction is only called comparable when the tokenizer is named;
  otherwise `reduction_basis` says "do not compare across models".
- `estimated_baseline` records whether the counterfactual was measured or
  estimated. The CLI never sets it on its own.

## What is still not available

There is no provider call in this module, so the provider's own usage metadata
must be supplied by the host (`--billed-input`, `--cached`, `--output`,
`--provider-ms`). The project's measured, model-free numbers live in
[benchmark.md](benchmark.md) and
[phase2-hermes-cost-report.md](phase2-hermes-cost-report.md); the frozen live
A/B protocols in [ab-benchmark.md](ab-benchmark.md) have not been run.
