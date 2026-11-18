# Context intelligence

`goals.md` PART 8 asks for token-budgeted context selection: score what is worth
keeping, honour dependencies, never drop mandatory material silently, detect
near-duplicates, and report the choice. `context.py` implements that as a
self-contained library and `sutra context` exposes it. It reads JSON and writes
nothing.

This document states the model, maps it to the code, records the measured
accuracy and latency from `bench/context.py`, and lists what is *not* wired in.

## The selection problem

For items \(i = 1 \dots n\) with cost \(C_i\), value \(V_i\) and dependency set
\(D_i\):

\[
\max \sum_i x_i V_i
\quad\text{s.t.}\quad
\sum_i x_i C_i \le B,\quad
x_i \le x_j\ \ \forall j \in D_i,\quad
x_i = 1\ \ \forall i \in M
\]

where \(M\) is the mandatory set. The dependency constraint says an item may
only be selected when everything it depends on is selected too. This is the
precedence-constrained knapsack problem: weakly NP-hard, and not solved exactly
here when dependencies are present.

The value is the weighted sum from `goals.md`:

\[
V_i = w_r R_i + w_i I_i + w_d D_i + w_u U_i + w_t T_i
\]

`context.values()` normalises the inputs before weighting:

- \(R_i, I_i, U_i\) are supplied in \([0, 1]\) and validated.
- \(T_i\) uses the explicit `recency` field when given; otherwise it is the
  min–max normalisation of `last_updated` within the batch (all equal → 1.0,
  missing → 0.0).
- \(D_i\) is the share of items that transitively depend on \(i\), normalised
  by the largest such count: `dependency_values()` walks reverse edges once per
  item, so the count is exact for a DAG.
- Defaults are `Weights(0.35, 0.30, 0.15, 0.10, 0.10)`; callers can override
  them with `--weights`.

Token cost is whatever the caller reports. When `token_cost` is absent the cost
is an estimate of `utf-8 bytes // 4`, and the selection is marked
`estimated: true`; the CLI prints that note. Byte budgets (`--unit bytes`) are
exact.

## Algorithms

| Step | Algorithm | Complexity | Notes |
|---|---|---|---|
| Validation | hash map of ids, duplicate detection | \(O(n)\) | duplicate ids are an error |
| Cycle detection | iterative DFS with a stack path | \(O(n + E)\) | reports the cycle path |
| Dependency values | one reverse BFS per item | \(O(n(n + E))\) | exact DAG dependent counts |
| Mandatory closure | iterative DFS over dependencies | \(O(n + E)\) | kept before any budget decision |
| Near-duplicate groups | SimHash over 3-word shingles, 64 bits | \(O(S)\) per item | `DEFAULT_NEAR_DISTANCE = 16` |
| Exact selection | 0/1 knapsack dynamic program | \(O(nB)\) time, \(O(nB)\) memory | only when dependencies are already satisfied, `n ≤ 200`, `B ≤ 4096` |
| Bounded selection | dependency-closure greedy, by density and by value | \(O(n(n + E))\) per run | the better of two runs is kept |

The dynamic program is tie-inclusive: when including an item does not raise the
total value, it is kept if it fits. That makes a selection with zero-valued
items stable instead of returning an empty set, and it is why the exact path
reports `exact-knapsack` for a batch whose items all fit.

The greedy runs are bounded and deterministic: candidates are sorted by an
explicit key (density `V/C`, then value, then id), and the chosen set is the
one with the larger total value. No random restarts and no probability
cut-offs are used.

### Near-duplicates

`simhash()` hashes each 3-word shingle with BLAKE2b (8-byte digest), weights
each bit by the shingle sign, and keeps the 64-bit majority. `hamming()` counts
differing bits. Two items are grouped when the distance is at most
`near_distance` (default 16). Within a group the item with the highest value is
kept; a duplicate is dropped only when it is not mandatory and nothing depends
on it, and every drop is reported in `duplicates` as `(dropped, kept)`.

A second guard, taken from Caveman's engine
(`compressors/redundancy.go`, `redundantUnitContainment = 0.9` — see
[docs/ponytail-caveman.md](ponytail-caveman.md)), requires the representative
to carry at least 90% of the duplicate's vocabulary before anything is dropped.
A near-duplicate that happens to contain a distinct error, identifier or rare
token is therefore kept and still competes for the budget. The test
`test_near_duplicate_with_distinct_vocabulary_is_kept` forces one SimHash group
so the guard, not the bit distance, is what decides.

`minhash()` and `jaccard()` are provided for comparison and for callers that
want an overlap estimate. They are not used by the default grouping, and are
measured alongside SimHash in the benchmark.

### Failure behaviour

- A dependency cycle raises `CycleError` with the path, and the CLI exits 2.
- A missing optional dependency drops the dependent item with reason
  `missing dependency` instead of crashing.
- Mandatory items that do not fit return `feasible: false`, a `conflict`
  sentence naming the needed and available budget, and the mandatory items are
  still listed. The CLI exits 1.
- Malformed JSON, a missing budget, or invalid field values exit 2 with the
  reason on stderr.

## Measured results

`python3 bench/context.py` (2026-11-18, this machine) writes
`bench/context.json`. The corpus is synthetic: 20 tool-like documents, one
near-duplicate variant of each (word change, appended line, reordered lines, two
word changes), 20 unrelated pairs and 20 hard negatives over the same
vocabulary. All numbers are deterministic; no model is called.

| Method | Threshold | Precision | Recall | F1 |
|---|---|---|---|---|
| SimHash | 4 | 1.000 | 0.550 | 0.710 |
| SimHash | 8 | 1.000 | 0.900 | 0.947 |
| SimHash | 12 | 1.000 | 1.000 | 1.000 |
| **SimHash** | **16 (default)** | **1.000** | **1.000** | **1.000** |
| SimHash | 20 | 1.000 | 1.000 | 1.000 |
| SimHash | 24 | 0.952 | 1.000 | 0.976 |
| SimHash | 28 | 0.870 | 1.000 | 0.930 |
| MinHash | 0.50–0.90 | 1.000 | 1.000 | 1.000 |

Unrelated documents sit around 30–32 differing bits, so the 12–20 bit plateau
is not a coincidence of this seed; 16 sits in the middle of it. The MinHash
sweep is perfect on this corpus but costs 32 signature comparisons per pair, so
it is offered for larger-scale pre-filtering rather than used by default.
These are synthetic documents: real tool output contains boilerplate that can
push unrelated pairs closer, which is why dropping is opt-in and never touches
an item with dependents.

| Items | Mode | Median ms | Max ms | Algorithm |
|---|---|---|---|---|
| 50 | exact | 7.45 | 8.90 | `exact-knapsack` |
| 200 | exact | 126.00 | 127.90 | `exact-knapsack` |
| 500 | dependency chain | 83.28 | 85.36 | `greedy-closure` |
| 1000 | dependency chain | 318.60 | 388.72 | `greedy-closure` |

The dependency-chain runs select every item because the budget exceeds the
total cost; the numbers measure the closure bookkeeping, not a tight budget.
Wall-clock depends on the machine and is not a provider cost.

On 40 small random dependency instances (4–10 items), the greedy result matched
the exhaustive optimum in all 40 (`mean gap 0.0%`, `max gap 0.0%`). That is a
small sample and not a proof: precedence-constrained knapsack is NP-hard, so a
budget-tight instance can make greedy suboptimal. The exact path is used
whenever dependencies do not cross the budget decision.

## What is wired in, and what is not

- **Wired:** `context.select()` / `Context.select()` as a library, and
  `sutra context` as a read-only CLI over JSON. Both are covered by
  `tests/test_context.py`.
- **Not wired:** the post-tool hook path. A tool-result hook receives one result
  and has no view of the conversation, so it cannot rank the other context
  items a provider will send. Feeding `select()` automatically needs a host
  that exposes the context list; no supported adapter does. Claiming otherwise
  would be wrong.
- The module does not call a provider, a tokenizer or a price sheet. Token
  counts without `token_cost` are estimates and are labelled as such.
- Near-duplicate grouping compares against the group's first id, so chained
  variants can split into more than one group. Dropping is opt-in and reported,
  which bounds the consequence.
- There is no persistence layer here: `Context` is in-memory. The local result
  store in `store.py` remains the only on-disk cache.

## Using it

```console
$ echo '{"budget": 3, "items": [
    {"id": "task", "content": "implement the retry budget", "token_cost": 1, "relevance": 1.0, "mandatory": true},
    {"id": "log", "content": "FAIL test_retry expected 3 got 4", "token_cost": 2, "relevance": 0.4},
    {"id": "old", "content": "earlier plan about sockets", "token_cost": 2, "relevance": 0.1}]}' \
  | sutra context --near-duplicates
selected 2 of 3 items, 3/3 tokens, value 0.490000
algorithm: exact-knapsack
  keep  log
  keep  task
  drop  old: not selected: budget or lower value
```

`--json` prints the full `Selection` object, including the algorithm, the
estimated flag, dropped reasons and near-duplicate pairs.

## Limits

- Values come from the caller. There is no learned relevance model; `goals.md`
  requires data before any ML addition, and none exists.
- The precedence-constrained case is greedy, with the measured small-instance
  gap above. Tight budgets over deep dependency graphs can lose value.
- Near-duplicate detection is lexical. Two outputs that differ only in a number
  can be grouped as near-duplicates; the caller must decide whether the older
  one carries information the newer lacks. Nothing is dropped unless
  `near_duplicates=True` and the item has no dependents.
- Exact selection is capped at 200 items and 4096 budget units; above that the
  bounded greedy path runs and the report says so.
- The benchmark corpus is synthetic and small. Real corpus accuracy is
  unmeasured.
