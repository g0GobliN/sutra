# Contended timing reference: 2026-10-29

A deliberate `--timing force` run on the development host while it was busy,
kept because it documents something the published numbers cannot: what host
contention does to SUTRA's own timings.

| Field | Value |
|---|---|
| Timestamp (UTC) | 2026-10-29T03:37:12+00:00 |
| Host | 4 CPUs, 20,665,458,688 B RAM, Linux 6.12 (x86_64) |
| Python | 3.13.5 (CPython) |
| 1-minute load | 19.04 (4.76 per CPU) |
| Checkout | `bee213c` plus the harness change under test (dirty) |
| Method | 1 warm-up, 5 trials per phase, median reported, every trial kept |
| Representative | **no** — the load policy threshold is 1.0 per CPU |

The median traceback full rewrite here is 80.1 ms, with trials up to 85.0 ms;
the 2026-10-25 best-of-three artifact recorded 48.25 ms for the same path. That
gap is contention plus the difference between `min` and `median`, and it is the
context for the 567.70 ms observation discussed in `docs/benchmark.md`.

`bench/benchmark.py --check` cannot verify this file (it is not a generated
artifact of the current directory), so it is covered by its recorded hash:

```
2acfdf713e266d892aa18568b21887405aaec3ff31b02caff61f0137241c41ea  timing.json
```
