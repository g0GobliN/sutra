# Reference run: 2026-10-25

These are the benchmark artifacts that were present in the checkout when the
harness was hardened on 2026-10-29, copied byte-for-byte before any change.
They are kept so the published numbers in `docs/benchmark.md` and the historical
timing figures stay auditable, and so a future run can be compared against the
exact data it replaced.

| File | Produced by | Deterministic? |
|---|---|---|
| `results.json` | `bench/benchmark.py` (`measure()`) | yes — bytes and rule assignment |
| `recovery.json` | `bench/benchmark.py` (`measure_recovery()`) | yes — bytes and clue recovery |
| `timing.json` | `bench/benchmark.py` (`measure_overhead()`) | **no** — wall clock, min of 3 |
| `tokens.json` | `bench/tokens.py` (`tiktoken 0.14.0`, `o200k_base`) | yes for a fixed tokenizer version |
| `retrieval.json` | `bench/retrieval.py` | **no** — wall clock, median of 3 |
| `savings.svg`, `recovery.svg` | the same benchmark run | yes |

## What is known about the conditions

- File timestamps: 2026-10-25 (machine local time). The checkout they were
  found in was at commit `bee213c`; the commit that produced them is not
  recorded, and these artifacts did not capture their own environment.
- Machine: the development host described in `docs/benchmark.md` — 4 CPUs,
  19 GiB RAM, Linux. **Load average was not recorded.** The mission that
  prompted this work observed a later traceback timing of 567.70 ms on the same
  host at load average 29.7, against the 44.5 ms published from this run.
- Method: compression byte counts are deterministic; timings were best-of-three
  after a warm-up (`min`), which is why the published table is lower than a
  median under contention.

## Why this directory is not the "current" result

`bench/results.json`, `bench/recovery.json`, `bench/timing.json`,
`bench/tokens.json` and `bench/retrieval.json` are generated files (ignored by
git). A fresh run overwrites them. This directory is a fixed reference and is
never rewritten by the harness.

## Verification hashes

```
686a68fb2f52b8c7a3d611db7ac4284ef5600605df7e1cb9751d65ddf213a799  recovery.json
f72d9682b9b37193024cc1ab375945f56623044652f30afb25c710bef6ce5959  results.json
6623202a4abc34152baae5e395b6087031216bcd6fb055dab5421826f64b33c3  retrieval.json
64593e984a07339eda8b5dd2ba557d7a9922f3862ed6b881f826e9158083a04c  timing.json
b77266d02109ca995a7bf6aa6931b768fcd3c5de19af5cdac268693bd7f47ae1  tokens.json
```
