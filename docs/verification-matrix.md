# What this branch actually does, and how each claim is checked

The implementation report describes intent. This table is the evidence map: for
each capability, the code that implements it, the test that would fail if it
broke, and the measurement (if any) that shows its effect. A capability with no
measurement is marked as such instead of being presented as a result.

| Capability | Code | Test | Evidence |
| --- | --- | --- | --- |
| Deterministic, format-preserving compression | `engine._compress_impl` | `tests/test_sutra.py`, `tests/test_invariants.py` | 54.3% of bytes removed across the eight fixtures, 55.2% of o200k tokens |
| Original recoverable after compression | `store.py`, `recovery.py` | `tests/test_store.py`, `tests/test_recovery.py` | every compressed fixture restores byte-identically |
| Exact duplicate reuse | `reuse.py` | `tests/test_reuse.py` | SHA-256 keyed, no approximate matching |
| Protected-span validation | `preserve.py` | `tests/test_preserve.py` (14) | validator rejects any candidate that drops a protected span |
| Bounded search, four modes | `optimize.py`, `engine._apply_search` | `tests/test_search.py` (15), `tests/test_optimize.py` (24) | balanced 3,590 tokens vs off 4,572 on the prose payload; aggressive identical, 4x the time |
| Failure tracking and scoped rollback | `failure.py` | `tests/test_failure.py` (13) | a strategy that fails twice on one payload shape is skipped there, nowhere else |
| Context selection under a budget | `context.py`, `sutra context` | `tests/test_context.py` (29) | exact DP matches brute force on 50 instances; greedy matches exhaustive optimum on 40/40 |
| Near-duplicate detection | `context.select(near_duplicates=True)` | `tests/test_context.py` | SimHash F1 1.000 at thresholds 12-20 on 80 labelled pairs; a distinct-vocabulary item is kept |
| Redundancy classification | `redundancy.py`, `sutra redundancy` | `tests/test_redundancy.py` (18) | 15.54 µs median per classification; mandatory commands are never classified reusable |
| Cache economics | `cache.py`, `sutra cache` | `tests/test_cache.py` (18) | break-even reported, `None` when a hit costs more than a miss |
| Cost accounting | `cost.py`, `sutra cost` | `tests/test_cost.py` (16), `tests/test_accounting.py` (12) | cached input counted once, cache-write subset validated, no built-in price table |
| Provider usage ingestion | `providers.py` | `tests/test_providers.py` (21) | OpenAI/Anthropic/Google/DeepSeek shapes; missing fields raise, never guess |
| Repository map | `repomap.py`, `sutra map` | `tests/test_repomap.py` (8) | budgeted, deterministic, skips tests and vendor directories |
| Real Hermes execution | `bench/agent/*` | manual, reproducible | branch plugin loaded from an isolated home; tool result compressed before the model call |
| Automatic wiring into a host | — | — | **not implemented**: the host is not modified, only offered the library and CLI |
| Provider prompt-cache visibility | — | — | **not observable** from a post-tool-result hook; no saving is claimed |
| Pre-generation output control | — | — | **not possible** after a tool result is produced; output tokens are already billed |

## How to re-check

```bash
python -m pytest -q
python -m ruff check .
python bench/benchmark.py
python bench/tokens.py
python bench/agent/build_corpus.py
python bench/agent/run_abc.py --corpus bench/agent/corpus --out /tmp/abc.json
```

The last command needs a working `hermes` on `PATH`; it never touches
`~/.hermes`.
