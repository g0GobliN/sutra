# Benchmark method

The benchmark answers two questions that are easy to confuse:

1. **Does the rewrite shrink the payload, and by how much?** Measured in bytes.
2. **Can the agent still reach the evidence it needs?** Measured in retrieval
   calls, response bytes, and whether a planted clue was actually returned.

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
| 5 | 600 KB patch | unified diff | `+const line = 'added by the patch'` |
| 6 | 900-row SQL result | tab-separated rows | `user450@example.com` |
| 7 | 8,000-line build log | compiler lines, one error | `'node' undeclared` |
| 8 | 8,000-item JSON array | array of objects | `"id": 4000` |

The patch and the array are the two cases that must **not** change: a diff
because a rewritten patch can hide a code change, an array because reshaping it
breaks the payload's schema.

## Method

- The real plugin is loaded from the repository root, exactly as Hermes loads
  it (package spec, `submodule_search_locations`).
- Compression runs through `sutra.compress`; recovery runs through the real
  middleware path (`compress_result`) against a real store in a fresh
  `HERMES_HOME`, then `recovery.restore`.
- Everything is single-process, offline, and deterministic except the timings.
- Timings are best-of-three after a warm-up, on the machine that produced
  `bench/timing.json`; they are indicative, not a portable performance claim.
- Peak allocation uses `tracemalloc` around one rewrite and excludes the input
  string, which was allocated before measurement started. It is *additional*
  heap during processing, not total process memory.

## Results

`bench/results.json`, `bench/recovery.json`, `bench/timing.json`, and the two
charts are written by one command:

```bash
python3 bench/benchmark.py
```

The tables are reproduced in the README. Summary at the time of writing:

- Compression: **1,626,501 → 742,727 bytes (54.3%)** across all eight fixtures;
  60–97% on the compressible ones; **0%** on the patch and the array by design.
- Recovery: every planted clue was retrieved. Seven of eight cases needed at
  most one call; the two untouched protected payloads needed none. The median
  retrieval response was **~710 bytes**; the worst was **8.9 kB** (a single-line
  JSON payload, where the search returns a clipped window).
- Overhead: **0.5–105 ms** per result on the development machine, including
  retention; 0.5 ms for a protected patch (classification only).
  Retaining an original roughly doubles the cost of a rewrite because the
  marker needs a real id, which means a second pass. Timings vary by machine
  and load; they are not a portability claim.

## Worst-case retrieval

The eight fixtures above retrieve small, ordinary results. Retrieval cost at the
hard cap is measured separately, because that is where an execution-time claim
could be wrong:

```bash
python3 bench/retrieval.py    # writes bench/retrieval.json
```

It stores a 32 MiB plain entry and a 32 MiB JSON document, then times every
mode and a search that has to scan the whole entry. The table is reproduced in
[docs/recovery.md](recovery.md#execution-time-envelope); the worst measured case
is a search over the 32 MiB JSON field at **355 ms** (median 329 ms) on the
development machine. These are wall-clock timings, not a portability claim.

## Not measured

| Quantity | Why not | What would be needed |
|---|---|---|
| Input/output/cache tokens | no model call is made | a live provider and session-level usage records |
| Provider cost | follows from tokens | the same, plus pricing |
| Task completion / correctness | no task harness | a set of repository tasks, an agent loop, and a scoring rubric |
| Regression rate | same | repeated runs per condition, with and without the plugin |
| End-to-end latency | only the plugin's own work is timed | full turn timing from the host |

**No token number in this repository is derived from a byte count.** A byte
removed from a tool result is *usually* a token not re-sent on the next turn,
but prompt caching, tool-result truncation by the host, and the model's own
choices all change the arithmetic. The honest comparison is a before/after on a
real workload: same task, plugin disabled vs enabled, compare `input_tokens` in
the session row.

The benchmark deliberately does not use `tiktoken` or any tokenizer to produce
an "estimated tokens" column: a tokenizer estimate for one model is not a
measurement of another model's bill, and putting the two in the same table
invites exactly the confusion this project is trying to avoid.

## Release gate

A release must satisfy all of these, checked by `tests/test_benchmark.py`:

1. **No protected payload is rewritten.** The patch fixture stays byte-identical
   and reports `diff-kept`.
2. **No shape changes.** The JSON array fixture stays an array and reports
   `json-kept`; JSON documents re-parse.
3. **Every planted clue is recoverable** through the real store and tool.
4. **No fixture grows** after compression.
5. **Retrieval is bounded**: one call returns a response under the cap for every
   fixture, and `bytes_to_clue` is recorded so a regression in retrieval cost is
   visible.

The following are quality indicators, reported but not gated on a fixed number:

- bytes saved per fixture class;
- retrieval response size;
- processing overhead;
- peak additional allocation.

Any future numeric target (for example, "at least 40% saved and no more than
one extra call per task") must be set from a measured baseline on real
trajectories, not chosen to make the table look good.

## Limitations

- Fixtures are synthetic. They are representative in shape, not in distribution
  of real tool output.
- The clue test proves *retrievability*, not that an agent will think to ask.
  That is the end-to-end question the missing task harness would answer.
- Timings include filesystem effects and vary by machine and by page cache.
- `peak_kib` covers one representative rewrite, not the worst case across shapes.
