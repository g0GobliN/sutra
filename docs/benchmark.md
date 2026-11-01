# Benchmark method

The benchmark answers three questions that are easy to confuse:

1. **Does the rewrite shrink the payload, and by how much?** Measured in bytes.
2. **Can the agent still reach the evidence it needs?** Measured in retrieval
   calls, response bytes, and whether a planted clue was actually returned.
3. **What does the work cost?** Measured as separated wall-clock phases and two
   different memory numbers, under a stated load policy.

It does not answer "how much money does this save" or "does the agent finish the
task". Those need a live model and a task harness; see *Not measured* below.

## Fixtures

All payloads are generated deterministically in `bench/benchmark.py`. Each one
carries a clue planted where the model would not see it after compression
(except the protected cases, where the clue must remain visible because the
payload is untouched).

| # | Payload | Shape | Clue |
|---|---|---|---|
| 1 | 12,000-line command output | plain text | line `6001` in the middle |
| 2 | JSON tool envelope | `{"output": …, "exit_code": 0}` | `line 1500 of a long command` inside the field |
| 3 | test run, 2,000 cases | repeated PASS lines, one FAIL | `FAIL src/module-1000.test.ts` |
| 4 | 3,000-frame traceback | frames + exception | `ValueError: the thing broke` at the end |
| 5 | 432 kB patch | unified diff | `+const line = 'added by the patch'` |
| 6 | 900-row SQL result | tab-separated rows | `user450@example.com` |
| 7 | 8,000-line build log | compiler lines, one error | `'node' undeclared` |
| 8 | 8,000-item JSON array | array of objects | `"id": 4000` |

The patch and the array are the two cases that must **not** change: a diff
because a rewritten patch can hide a code change, an array because reshaping it
breaks the payload's schema. Their reported 0.0% is a pass, not a miss. The
patch fixture is labelled `432 kB patch` after its measured 432,087 bytes (432.1
kB in the results tables); the archived 2026-10-25 artifacts still carry the
pre-rename `600 KB` label. The array preview that was evaluated and rejected, with the consumer failures that
rejected it, is recorded in [json-array-preview.md](json-array-preview.md).

## Method

- The real plugin is loaded from the repository root, exactly as Hermes loads
  it (package spec, `submodule_search_locations`).
- Compression runs through `sutra.compress`; recovery runs through the real
  middleware path (`compress_result`) against a real store in a fresh
  `HERMES_HOME`, then `recovery.restore`.
- Everything is single-process, offline, and deterministic except the timings.
- The byte and recovery measurements are deterministic and are checked against
  a fresh run by `verify_artifacts()` on every generation.

### Timing

Timing is split into the phases the architecture actually has, because they
answer different questions:

| Phase | What it times |
|---|---|
| `compress` | `sutra.compress` only — classification plus rewriting, no store |
| `store_put` | `Store.put` only — the retention write, a fresh id and two atomic files |
| `rewrite` | the full middleware path — classify, compress, retain, compress again so the marker can quote a real id |
| `restore` | the real `sutra_restore` search that reaches the buried clue |

Each phase gets one warm-up and five trials by default (`--repeats`), timed with
`time.perf_counter` (monotonic, high resolution). The artifact keeps **every raw
trial**, plus the median, min and max. The published number is the median:
best-of-N hides the spread that made an earlier 44.5 ms figure incomparable with
a 567.70 ms observation on the same host.

**Load policy.** A representative timing needs roughly one runnable task per
CPU. `measure_timing` is skipped when the 1-minute load average divided by the
CPU count exceeds `1.0`; `bench/timing.json` then records `"status":
"deferred"`, the measured load, and no numbers. The harness never stops other
work to make a machine idle. `--timing force` records a contended run and marks
`"representative": false`; `--timing off` skips timing entirely.

Every run records its conditions: timestamp (UTC), Python version and
implementation, platform, machine, CPU count, total RAM, 1-minute load, git SHA
and whether the tree was dirty.

### Memory

Two numbers, deliberately not merged:

- `peak_heap_kib` — tracemalloc's peak **Python heap** allocation during the
  call. The input string exists before measurement starts, so it is not
  included.
- `rss_delta_kib` — process resident-set growth across the same call, with the
  before/after values recorded. It includes allocator and page-cache effects and
  can be small or negative. It is not the payload size, and it is not total
  machine memory.

Earlier versions called this "peak allocation" without saying which of the two
it was; the current artifact names both.

### Tokens

Token counts are a separate, optional measurement in `bench/tokens.py`, using
`tiktoken==0.14.0` and `o200k_base` on the same payload text. They are a direct
tokenizer count, not a byte-to-token estimate and not a provider bill. If
`tiktoken` cannot be installed, report the count as unavailable rather than
deriving one. `bench/tokens.json` records the same environment block.

### Provenance and historical runs

`bench/*.json` are generated and ignored by git. Two fixed references are kept
so a change can be compared against the data it replaced:

| Directory | What it is |
|---|---|
| [`bench/reference/2026-10-25/`](../bench/reference/2026-10-25/README.md) | the artifacts found in the checkout before this harness was hardened; published numbers came from this run |
| [`bench/reference/2026-10-29-contended/`](../bench/reference/2026-10-29-contended/timing.json) | a forced timing run at load 19.04 on 4 CPUs, kept to show what contention does to these numbers |

## Results

One command writes `bench/results.json`, `bench/recovery.json`,
`bench/timing.json`, and both charts, then re-verifies them:

```bash
python3 bench/benchmark.py                  # defers timing on a loaded machine
python3 bench/benchmark.py --timing force   # records a contended run
python3 bench/benchmark.py --check          # re-verify without writing
python3 bench/benchmark.py --out /tmp/run   # isolated run
```

`bench/tokens.py` writes the token artifact:

```bash
python3 -m pip install -r bench/requirements.txt
python3 bench/tokens.py
```

### Bytes

| payload | rule | before | after | saved |
|---|---|---|---:|---:|
| 12,000-line command output | `head-tail` | 60.9 kB | 18.6 kB | 69.5% |
| JSON tool envelope | `json-fields` | 85.9 kB | 19.2 kB | 77.7% |
| test run, 2,000 cases | `head-tail` | 99.8 kB | 18.6 kB | 81.4% |
| 3,000-frame traceback | `traceback` | 359.4 kB | 10.3 kB | 97.1% |
| 432 kB patch | `diff-kept` | 432.1 kB | 432.1 kB | 0.0% |
| 900-row SQL result | `head-tail` | 46.6 kB | 18.5 kB | 60.2% |
| 8,000-line build log | `head-tail` | 334.9 kB | 18.6 kB | 94.5% |
| 8,000-item JSON array | `json-kept` | 206.9 kB | 206.9 kB | 0.0% |
| **total** | | **1,626,501 B** | **742,727 B** | **54.3%** |

### Recovery

| payload | visible | calls | response | total to the clue | clue recovered |
|---|---|---:|---:|---:|---|
| 12,000-line command output | 18.7 kB | 1 | 554 B | 19.2 kB | yes |
| JSON tool envelope | 19.3 kB | 1 | 8.9 kB | 28.2 kB | yes |
| test run, 2,000 cases | 18.6 kB | 1 | 711 B | 19.3 kB | yes |
| 3,000-frame traceback | 10.4 kB | 0 | — | 10.4 kB | yes (error end kept) |
| 432 kB patch | 432.1 kB | 0 | — | 432.1 kB | yes (untouched) |
| 900-row SQL result | 18.6 kB | 1 | 716 B | 19.3 kB | yes |
| 8,000-line build log | 18.6 kB | 1 | 712 B | 19.4 kB | yes |
| 8,000-item JSON array | 206.9 kB | 0 | — | 206.9 kB | yes (untouched) |

Five cases needed one call; three were already visible. The JSON envelope pays
the most (8.9 kB) because its whole payload is one 86 kB line and the search has
to return a clipped window around the match; everything with normal line breaks
costs about 700 bytes. The median response was 712 B.

### Cost

The current `bench/timing.json` was generated at **load 20.46 on 4 CPUs (5.12
per CPU)** and says:

```json
{"status": "deferred", "representative": false,
 "reason": "1-min load 20.46 on 4 CPUs is 5.12 per CPU (limit 1.00); a representative timing needs an idle machine"}
```

No representative timing is published from this run. The forced reference run
below is the same harness at load 19.04 (4.76 per CPU), five trials per phase,
median in ms. It is **not** a portability or performance claim; it is what the
machine did while other work was running, kept because hiding it would make the
next comparison dishonest.

| payload | compress | store write | full rewrite | restore |
|---|---:|---:|---:|---:|
| 12,000-line command output | 27.5 | 1.0 | 41.0 | 5.1 |
| JSON tool envelope | 19.8 | 1.3 | 39.8 | 1.0 |
| test run, 2,000 cases | 11.4 | 2.1 | 31.5 | 3.8 |
| 3,000-frame traceback | 67.7 | 7.4 | 80.1 | n/a |
| 432 kB patch | 0.6 | 4.7 | 1.5 | n/a |
| 900-row SQL result | 6.6 | 0.8 | 17.7 | 1.3 |
| 8,000-line build log | 40.3 | 6.0 | 84.2 | 7.4 |
| 8,000-item JSON array | 174.7 | 2.0 | 154.9 | n/a |

The spread is the point. In that run the traceback's compression trials spanned
45.7–86.4 ms, the build log's full rewrite 78.1–103.5 ms, and the JSON array's
compression 133.9–234.2 ms. Retaining an original roughly doubles the cost of a
rewrite because the marker needs a real id, which means a second compression
pass; the store write itself is ~1 ms for ordinary payloads and ~5–7 ms for the
400 kB ones.

Memory for the same 359 kB traceback rewrite: **701.5 KiB** peak Python heap
(tracemalloc, input excluded), and **+52 KiB** process RSS across the call
(29,876 → 29,928 KiB). The two numbers measure different things; neither is the
payload size.

### Tokens

`bench/tokens.json` (tiktoken 0.14.0, `o200k_base`; the same payload text):

| payload | before | after | saved |
|---|---:|---:|---:|
| 12,000-line command output | 35,000 | 10,149 | 71.0% |
| JSON tool envelope | 26,012 | 5,670 | 78.2% |
| test run, 2,000 cases | 29,998 | 5,529 | 81.6% |
| 3,000-frame traceback | 126,015 | 3,680 | 97.1% |
| 432 kB patch | 120,039 | 120,039 | 0.0% |
| 900-row SQL result | 18,906 | 7,543 | 60.1% |
| 8,000-line build log | 127,005 | 6,905 | 94.6% |
| 8,000-item JSON array | 103,002 | 103,002 | 0.0% |
| **total** | **585,977** | **262,517** | **55.2%** |

These are tokenizer counts of the text, not a provider bill: prompt caching,
host truncation and the model's own choices all change what is actually re-sent.

## The 567.70 ms question

An earlier observation reported 567.70 ms for traceback compression-plus-storage
on this 4-core host at load average 29.7, against the 44.5 ms best-of-three
figure the earlier documentation published. The archived 2026-10-25
`timing.json` records 48.25 ms for that path. Both numbers are consistent with
the same code:

- The old method published the **minimum of three** trials, which is the fastest
  trial by construction.
- At load 19.04 the median full rewrite is 80.1 ms with trials up to 85 ms; at
  load ~20 a median full rewrite is about 1.6–2× the quiet-host minimum-of-three
  figure (80.1 vs 48.25 ms for the traceback), and a first call after a stall is
  far worse. A 567.70 ms sample is plausible contention, not a 12× regression.
- The 2026-10-25 artifact recorded neither load nor run conditions, so the two
  figures cannot be reconciled exactly. That is the defect this milestone fixes:
  a number without its conditions is not evidence.

What is confirmed: the deterministic byte and recovery results are unchanged
across the harness rewrite, and the memory measurement reproduces the old 701.5
KiB peak exactly once the `(current, peak)` tuple order is respected.

## Worst-case retrieval

The eight fixtures above retrieve small, ordinary results. Retrieval cost near
the default entry cap is measured separately, because that is where an
execution-time claim could be wrong:

```bash
python3 bench/retrieval.py    # writes bench/retrieval.json
```

It stores a 32,000,001-byte plain entry and a 32,400,015-byte JSON document,
just under the 32 MiB default cap, then times every mode and a search that has
to scan the whole entry. The latest artifact was recorded at **load 22.36 on 4
CPUs (5.59 per CPU)** and is marked `"representative": false`: medians ran from
18.6 ms to 0.73 s and individual restores reached 2.2 s. The 2026-10-25 artifact
(load not captured) recorded 480.7 ms for the absent-token JSON-field search and
504.4 ms for the last-line search, against 732.8 ms and 541.2 ms in the
contended run. Treat a scan over a 32 MB entry as hundreds of milliseconds, with
multi-second outliers under contention; the fastest operations (a plain `bytes`
slice, a refused `full`) are tens of milliseconds. See
[docs/recovery.md](recovery.md#execution-time-envelope).

## Host-path latency (measured 2026-11-01)

Source checkout, Python 3.13 on Linux, isolated local store, default limits,
median of three runs. `core.process_text` is the shared seam; the hook figure
is a subprocess invocation of the installed Claude hook with the event JSON on
stdin. No model or provider was contacted.

| Input | Compressed | Saved | Core compress | Restore | Hook subprocess |
|---:|---:|---:|---:|---:|---:|
| 1 KB | 1 KB (unchanged, below threshold) | 0.0% | 0.26 ms | — | not run |
| 10 KB | 10 KB (unchanged, below threshold) | 0.0% | 0.69 ms | — | 188.1 ms |
| 100 KB | 893 B | 99.1% | 22.09 ms | 0.91 ms | 199.0 ms |
| 1 MB | 907 B | 99.9% | 170.31 ms | 8.82 ms | 412.2 ms |
| 5 MB | 912 B | 100.0% | 912.17 ms | 49.64 ms | 1,167.1 ms |

The 100 KB–5 MB fixtures use repeated log lines, so the reductions are
fixture-specific and larger than the mixed eight-fixture result above. The hook
number includes Python startup and JSON transport; the host pays that once per
eligible result, not per turn. The 10 KB hook payload is below the 24,576-byte
threshold and is returned unchanged, which is why it has no rewrite.

## Not measured

| Quantity | Why not | What would be needed |
|---|---|---|
| Live-model input/output/cache tokens | no model call is made | a live provider and session-level usage records |
| Provider cost | follows from live-model tokens | the same, plus pricing |
| Task completion / correctness | no task harness | a set of repository tasks, an agent loop, and a scoring rubric |
| Regression rate | same | repeated runs per condition, with and without the plugin |
| End-to-end latency | only the plugin's own work is timed | full turn timing from the host |

**No live-model token number in this repository is derived from a byte count.**
A byte removed from a tool result is *usually* a token not re-sent on the next
turn, but prompt caching, tool-result truncation by the host, and the model's
own choices all change the arithmetic. The honest comparison is a before/after
on a real workload: same task, plugin disabled vs enabled, compare
`input_tokens` in the session row.

## Release gate

A release must satisfy all of these:

1. **No protected payload is rewritten.** The patch fixture stays byte-identical
   and reports `diff-kept`.
2. **No shape changes.** The JSON array fixture stays an array and reports
   `json-kept`; JSON documents re-parse.
3. **Every planted clue is recoverable** through the real store and tool.
4. **No fixture grows** after compression.
5. **Retrieval is bounded**: one call returns content under the cap for every
   fixture, and `bytes_to_clue` is recorded so a regression in retrieval cost is
   visible.
6. **Generated artifacts agree with a fresh measurement** — `bench/benchmark.py
   --check` re-derives the byte results, cross-checks recovery rows and parses
   both SVG footers. `tests/test_benchmark.py` runs the generator and the
   verifier, and proves the verifier rejects a tampered chart.

Quality indicators, reported but not gated on a fixed number: bytes and o200k
tokens saved per fixture class; retrieval response size; processing overhead per
phase; peak heap and RSS delta. Any future numeric target (for example, "at least
40% saved and no more than one extra call per task") must be set from a measured
baseline on real trajectories, not chosen to make the table look good.

## Limitations

- Fixtures are synthetic. They are representative in shape, not in distribution
  of real tool output.
- Timings are wall clock on one host. The artifact records the conditions, and
  the harness refuses to call a loaded run representative, but a different CPU,
  filesystem or Python build will produce different numbers.
- The load policy uses the 1-minute average; a transiently busy machine can slip
  through, which is why raw trials and the spread are recorded too.
- The token counts use one encoding and version (`tiktoken 0.14.0`,
  `o200k_base`). Another tokenizer will produce different counts, and a
  tokenizer count is not a provider bill.
- The clue test proves *retrievability*, not that an agent will think to ask.
  That is the end-to-end question the missing task harness would answer.
- `rss_delta_kib` is a point-in-time sample around one call, not a high-water
  mark for the process.
