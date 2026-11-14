# SUTRA eval — Phase 1 harness

Three-condition harness for measuring what SUTRA changes about tool-result
context: **A (control)**, **B (compression+recovery)**, **C (full optimization)**.

## CLI

```sh
# From the repo root:
python eval/run_harness.py run               # all 8 tasks, 3 reps per condition
python eval/run_harness.py run --task eval-001 --reps 4
python eval/run_harness.py compare           # latest run under eval/results
python eval/run_harness.py compare --run eval/results/run-20261113T...

# Equivalent, once the eval command is wired into the sutra CLI:
sutra eval run
sutra eval run --task eval-001
sutra eval compare
```

Options for `run`: `--task ID`, `--conditions ABC`, `--reps N (1-5)`, `--out DIR`, `--json`.

## Output

`eval/results/run-<timestamp>/` contains, per task and condition:

- `raw-<task>-<condition>.json` — per-rep results: steps, metrics (bytes, ms,
  clues, restore integrity, reuse), checks, pass/fail.
- `summary.json` — full run: per-task aggregates (mean/stdev/min/max/cv),
  variability, evidence levels, git commit, python version.
- `comparison.json` — pairwise comparisons B vs A / C vs A / C vs B with
  absolute and percentage differences per metric, plus next steps.

## Conditions

| Condition | Label | SUTRA_ENABLED | SUTRA_STORE | SUTRA_POLICY | SUTRA_REUSE |
|---|---|---|---|---|---|
| A | control | 0 | 0 | off | 0 |
| B | compression+recovery | 1 | 1 | off | 0 |
| C | full optimization | 1 | 1 | balanced | 1 |

## Task dataset

`eval/tasks/eval-001.py` … `eval-008.py` (Pixel's Phase 2 designs, made runnable
for Phase 1). Each task declares `evidence_level`; the harness computes
`evidence_achieved` from the run:

- **PRODUCTION VERIFIED** — every requested condition passed all reps
- **EXPERIMENTALLY DEMONSTRATED** — B and C passed all reps
- **TESTED** — at least one rep passed
- **IMPLEMENTED** — code path exists, not validated by this run

## Notes

- Each task run uses a fresh isolated `HERMES_HOME`, so nothing is written to
  `~/.hermes` and runs are independent.
- Condition A writes nothing to the store (raw output only).
- The reuse index is reset between reps; repeated identical outputs only hit
  under condition C.
- Existing source modules are not modified; the harness lives entirely under
  `eval/`. `sutra eval` is an additive branch in `cli.py`.
