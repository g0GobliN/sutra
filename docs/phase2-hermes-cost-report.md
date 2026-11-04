# Phase 2 Hermes cost report

**Status: model-free replay completed. Live-model behaviour and provider cost
remain unmeasured.**

This report records what SUTRA changes about the tool-result context of eight
representative Hermes workloads. It is not an invoice, not a provider-token
measurement and not a task-success score. No model was called and no provider
credit was spent.

## 1. Metadata

| Field | Value |
|---|---|
| Report date | 4 November 2026 |
| SUTRA version | 0.5.0 |
| Commit at run | `aaeb3fc66d4f` plus the 0.5.0 working tree measured here |
| Python | CPython 3.13.5 |
| Platform | Linux x86_64, kernel 6.12.113, glibc 2.41 |
| Tokenizer | `tiktoken 0.14.0`, `o200k_base` |
| Model / provider | none — model-free replay |
| Workloads | 8 deterministic recorded sequences |
| Tool-result turns | 31 |
| Machine-readable summary | `eval/results/phase2/hermes-cost-summary.json` |
| Reproduction | `python3 eval/replay.py` (needs `bench/requirements.txt` for tokens) |

## 2. Method

Each workload is a fixed sequence of tool calls and their exact outputs. The
harness replays every mode through the real shared core, store and recovery
seam under a temporary `HERMES_HOME`:

1. **Baseline** — `SUTRA_ENABLED=0`; the original result is visible.
2. **Output only** — existing compression and retention; policy off, reuse off.
3. **Balanced** — compression, exact-duplicate reuse and the balanced policy
   section.
4. **Aggressive** — the same pipeline with the aggressive policy section.

The tool sequence is identical in every mode. The policy's effect on what an
agent chooses to do is therefore **not** modelled; the replay measures the
policy section's prompt cost exactly and does not credit it with avoided tool
calls.

Marker ids are replaced with a deterministic sequence in the harness so two runs
are byte-for-byte reproducible. Production ids are random; real runs vary by a
few tokens per rewrite.

### Definitions

- **Tool-result context tokens**: sum over turns of every visible tool-result
  token so far. The system prompt and user text are excluded because they are
  identical across modes and are not what SUTRA controls.
- **Savings**: `(baseline tokens - optimized tokens) / baseline tokens × 100`.
- **Net tokens**: optimized tokens **plus** the policy section's tokens on every
  turn. A policy is a cost, so net savings are lower than gross savings.
- **Provider cost**: unavailable. There is no authoritative usage or price data
  in an offline replay, so none is reported.

## 3. Aggregate results

| Mode | Tool-result context tokens | Final context tokens | Visible bytes | Policy tokens / session | Net tokens | Savings | Net savings |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 3,529,723 | 1,117,653 | 5,294,539 | 0 | 3,529,723 | — | — |
| Output only | 296,780 | 111,496 | 533,765 | 0 | 296,780 | **91.59%** | **91.59%** |
| Balanced | 272,257 | 96,785 | 442,019 | 317 | 282,084 | **92.29%** | **92.01%** |
| Aggressive | 274,644 | 97,401 | 442,019 | 394 | 286,858 | **92.22%** | **91.87%** |

Byte reduction is **89.92%** for output-only and **91.65%** for both policy
modes. These are UTF-8 payload bytes on the replayed text, not provider
billing.

The final context is the state at the end of the session: 1,117,653 tokens in
the baseline, 111,496 with output compression and 96,785 with balanced reuse.

## 4. Per-workload results

The balanced column shows gross context tokens and the net figure after
charging the policy section on every turn.

| Workload | Steps | Baseline | Output only | Balanced gross | Balanced net (savings) |
|---|---:|---:|---:|---:|---:|
| codebase-exploration | 4 | 188,817 | 33,756 (82.1%) | 35,024 | 36,292 (80.8%) |
| multi-file-change | 5 | 262,646 | 74,283 (71.7%) | 75,868 | 77,453 (70.5%) |
| debug-failure | 4 | 217,532 | 31,430 (85.6%) | 32,698 | 33,966 (84.4%) |
| large-command-output | 2 | 168,047 | 7,259 (95.7%) | 7,893 | 8,527 (94.9%) |
| repeated-file-inspection | 5 | 856,113 | 51,285 (94.0%) | 29,542 | 31,127 (96.4%) |
| test-execution | 3 | 278,960 | 23,574 (91.5%) | 16,935 | 17,886 (93.6%) |
| research-tool-calls | 4 | 339,240 | 35,773 (89.5%) | 33,609 | 34,877 (89.7%) |
| large-log-analysis | 4 | 1,218,368 | 39,420 (96.8%) | 40,688 | 41,956 (96.6%) |

On workloads with exact repeats, reuse adds a second, independent reduction:

| Workload | Repeated calls | Output only | Balanced | Reuse hits | Improvement |
|---|---:|---:|---:|---:|---:|
| repeated-file-inspection | 3 | 51,285 | 29,542 | 3 | 42.4% |
| test-execution | 1 | 23,574 | 16,935 | 1 | 28.2% |
| research-tool-calls | 1 | 35,773 | 33,609 | 1 | 6.0% |

Across all workloads, reuse saved **24,523** tool-result context tokens before
the policy cost (5 hits). The balanced policy section costs 9,827 tokens over
the 31 turns, so the net gain over output-only is **14,696 tokens**, or 4.95%
of the baseline context. The aggressive section costs 12,214 tokens and its net
gain over output-only is 9,922 tokens (3.34% of baseline); offline it has no
behavioural advantage over balanced.

## 5. Quality

| Check | Baseline | Output / balanced / aggressive |
|---|---:|---:|
| Required clues visible | 31/31 | 8/31 |
| Required clues recoverable byte-exactly | 0 | 23/31 |
| Required clues lost | 0 | **0** |
| Byte-exact restores performed | 0 | 24 |
| Restoration failures | 0 | **0** |
| Protected diff unchanged | 1/1 | 1/1 |
| Workloads passing all criteria | 8/8 | **8/8** |

A clue counts as kept when it is either visible in the rewritten result or
present in the paged, byte-exact restoration of the retained original. The
replay pages every stored original with `sutra_restore(mode="bytes")` and
compares the concatenation to the original bytes. All 24 restores matched.

## 6. Latency

Wall-clock for the replay process, with the suite and other work running on the
same machine. This is a sanity check, not a latency benchmark.

| Mode | Compression time (31 calls) | Restoration time (24 restores) |
|---|---:|---:|
| Output only | 5,622 ms | 1,010 ms |
| Balanced | 9,419 ms | 1,209 ms |
| Aggressive | 5,631 ms | 607 ms |

The balanced run's higher figure is dominated by store writes and CPU
contention during this measurement; the aggressive run of the same pipeline
took 5.6 s. The compression work is tens to hundreds of milliseconds per
eligible result, and restoration is bounded by the response cap.

## 7. What is measured, simulated and unavailable

| Class | Items |
|---|---|
| **Measured** | Original and visible UTF-8 bytes; local tokenizer counts on the replayed text; policy section tokens; reuse hits; exact-restore equality; latency |
| **Simulated** | Context growth: every turn is modelled as re-sending every prior tool result; tool sequences are fixed, not model-chosen |
| **Estimated** | None. No provider input-token or invoice figure is derived from bytes |
| **Unavailable** | Provider spending; live task success; the policy's effect on tool calls, verification behaviour or answer quality; prompt-cache effects |

## 8. Limitations

- **No live model.** This says nothing about whether SUTRA reduces a real
  provider bill or improves/degrades a real task outcome. The frozen live A/B
  protocol in [ab-benchmark.md](ab-benchmark.md) has still not been run.
- **Synthetic, fixed workloads.** They are representative shapes, not a sample
  of real sessions. A session that never repeats a result and never produces a
  large payload gains nothing.
- **Policy behaviour is not tested here.** The section's prompt cost is exact;
  its benefit depends on a model following it, which requires live inference.
- **One machine, one run.** Latency is not a stable benchmark and varies with
  load and disk.
- **Reuse is per process session.** A restart, a changed byte or a pruned entry
  falls back to normal compression.

## 9. Reproduction

```bash
python3 -m venv .venv
.venv/bin/pip install pytest ruff -r bench/requirements.txt
.venv/bin/python eval/replay.py
.venv/bin/python -m pytest tests/test_eval_harness.py -q
```

The harness writes `eval/results/phase2/hermes-cost-summary.json` with every
per-workload and per-mode field used above. It calls no network service and no
model.
