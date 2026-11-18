# Validation report: the branch, Hermes, and what the numbers mean

This report covers one question: does this implementation reduce what an AI agent sends
and pays, without breaking the agent or the task? It separates what was measured, what
was estimated, and what could not be measured in this environment.

Repository: `github.com/g0GobliN/sutra`, branch `feat/sutra-max-effort`, base `main`
at `e4ce1fe`.

## 1. Repository and branch inspected

- Worktree: `/home/goblin01/github_projects/tooltrim-goals`, branch `feat/sutra-max-effort`.
- Before this work the branch was local only; `git ls-remote` showed just `main`.
- The other checkout on this machine (`~/github_projects/tooltrim`) is on an unrelated
  branch with uncommitted changes and was not touched.
- `goals.md` and the `*.local.md` working reports remain untracked and private.

## 2. Existing Hermes installation and version

- Hermes Agent v0.23.1 (2026.9.24), installed at `~/.hermes/hermes-agent` (git commit
  `3b42157858`), Python 3.14.9, OpenAI SDK 2.54.0.
- Production home: `~/.hermes`, with 16 enabled plugins including `sutra`.
- Production SUTRA plugin: `~/.hermes/plugins/sutra`, version **0.6.3**, the public
  release, with no `cache.py`, `context.py`, `redundancy.py` or `providers.py`.

## 3. Actual plugin loading path

- The loader is `hermes_cli/plugins_discovery.py:190 collect_directory_manifests()`,
  which scans `$HERMES_HOME/plugins` for a `plugin.yaml`.
- The branch copy was installed into an isolated home, `~/.hermes-sutra-ab`, by
  `bench/agent/isolated_home.py`, which copies every runtime `*.py` plus `plugin.yaml`
  the same way `sutra install` does.
- `HERMES_HOME=~/.hermes-sutra-ab hermes plugins list` showed `sutra` enabled from that
  copy, and the SUTRA record identifiers appear in the isolated home's stats file.

## 4. New implementation installation result

- 38 files copied; configuration points at the harness provider on a free port;
  `plugins.enabled: [sutra]`; a marker file makes the directory refusable to anything
  but the harness.
- Plugins are re-read per gateway start, so each condition rewrites the home and
  restarts the gateway.

## 5. Verified commit

- The run recorded `plugin_commit` and a dirty flag in
  `/tmp/sutra-ab/abc-corpus.json`. The corpus and live runs were produced from the
  commits listed at the end of this report; `docs/hermes-validation.md` documents the
  procedure.

## 6. Mode execution verification

Proving a mode is not "the variable was set". The gateway daemon keeps the environment
of its first start, so three early runs that merely exported `SUTRA_SEARCH` per
invocation all executed with `off`; the evidence was three identical records in the
`off` stats file at 14:17:04/09/15. After stopping the gateway per condition:

- `off`: no `search_applied`, request 4,572 tokens (prose payload).
- `balanced`: `search_applied: "language"`, `search_saved_bytes: 3901`, 3,590 tokens.
- `aggressive`: identical result, 4× the wall time (1,608 ms vs 377 ms).

`CompressionStats.search_mode` now names the mode in every record, and
`tests/test_search.py::test_stats_and_the_log_name_the_mode_that_ran` checks all four
modes. A defect found here is fixed: field-level search inside a JSON document used to
lose its `search_applied`/`search_saved_bytes` on the outer record and re-run the
search for nothing (commit `98270aa`).

## 7. Benchmark architecture

Two levels:

- **Deterministic benchmarks** (`bench/benchmark.py`, `bench/tokens.py`,
  `bench/maximum.py`, `bench/passes.py`, `bench/context.py`): fixtures, byte and
  o200k token counts, mode comparison, pass marginal gain, selector latency and
  near-duplicate accuracy. Model-free and reproducible.
- **Agent harness** (`bench/agent/`): an isolated Hermes home, a local
  OpenAI-compatible provider that executes scenario commands and returns one tool call
  per request, optional passthrough to a real provider, and `run_abc.py` which runs four
  conditions per scenario, restarts the gateway each time, and records request sizes,
  tokens, tool-result sizes, SUTRA records and wall time.

## 8. Task corpus

Fifteen scenarios, one per category in the brief: repository exploration, code
modification, debugging, multi-file implementation, test execution, long-context
reasoning, repeated tool operations, documentation, structured JSON, strict
preservation, repeated context, multiple tool calls, a diff control, a numeric-array
control, and a build log. Each is a shell command whose output becomes the tool result;
sizes range from 25 kB to 227 kB. Scenario files carry `expect: helpful | neutral |
no-benefit`, and the controls are part of the set so a win cannot come from favourable
sampling.

## 9. A/B/C comparison

Mean request-token change against baseline over the fifteen scenarios (scripted
provider):

| Condition | Mean change | Median wall time |
|---|---:|---:|
| baseline (SUTRA disabled) | — | 24.6 s |
| compression-only (search off) | 46.6% fewer | 24.5 s |
| balanced | 47.0% fewer | 23.5 s |
| aggressive | 47.0% fewer | 25.4 s |

Per-scenario rows are in `docs/hermes-corpus-results.md`. The controls confirm the
harness: diff 0%, structured JSON 0%. Aggressive adds nothing over balanced on this
corpus and costs more time, so the evidence does not support recommending it.

## 10. Actual token usage

- Scripted corpus: request tokens are an o200k measurement of the serialized request,
  not provider usage.
- Live runs: the proxy records the provider's own `usage` object. Examples:
  - 03 debugging: baseline 58,794 prompt tokens / 550 output; balanced 29,803 / 450.
  - 11 repeated context: baseline 39,936 / 424; balanced 27,576 / 516.
  - The same runs exposed cached-input fields (23,232 and 25,344 tokens), which is
    provider accounting, not an inference.

## 11. Actual cost results

**Unavailable.** No paid provider was called. The live runs used a free model whose
reported cost is 0, so no dollar figure exists for this branch. `sutra cost --usage-file
--provider` accepts real usage from OpenAI, Anthropic, Google and DeepSeek; a report can
be produced as soon as such a run is authorised.

## 12. Task-success and quality comparison

**Not measured.** The corpus provider is scripted, so the corpus has no quality signal.
The live runs produced equivalent answers on the two comparable scenarios by manual
inspection (same footer, same worker range, same format), which is a sanity check and
not a score. A real quality comparison needs the frozen protocol in
`docs/ab-benchmark.md`, an evaluator, and repeated runs to separate SUTRA's effect from
model noise. The task description's 12 categories are represented in the corpus, but
"did the agent finish the task correctly" is still unmeasured.

## 13. Latency and overhead

- Corpus wall time: 23.5–25.4 s medians; the spread between runs is larger than
  between conditions in this sample.
- Single-payload measurement: 116.75 ms (off), 377.33 ms (balanced), 1,608.80 ms
  (aggressive) for the same 51 kB tool result. Aggressive's 4× cost is real; its output
  was identical to balanced.
- SUTRA-side costs: redundancy classification 15.54 µs median, file snapshot ~23 µs/kB,
  context selection 6.3–318 ms depending on size (see `docs/benchmark.md`,
  `docs/context-intelligence.md`).

## 14. Failed and inconclusive experiments

- Three early mode runs were invalid because the gateway held its first-start
  environment; re-run correctly, all three modes are proved.
- One corpus condition failed on a local transport error and looked like an 85%
  saving because only the first request reached the server. The harness now marks
  failed runs invalid, excludes them from means, and retries a transport failure; the
  condition was re-run cleanly.
- Scenario 09 (300 small JSON objects) is a genuine no-benefit case and is reported as
  0%, not hidden in an average.
- Scenario 14's 74% comes from trimming the terminal tool's own truncation envelope, not
  from an array-preview feature; a raw numeric array is left alone.
- One live scenario was inconclusive: the free model looped (48 requests baseline vs
  25 balanced) while SUTRA left the payload untouched.

## 15. Architecture recommendations

`docs/next-architecture.md` evaluates eight directions. The short version:

1. **Pre-call request middleware** is reachable: Hermes has an `llm_request` stage that
   receives the provider request and may replace it, and an `llm_execution` stage that
   wraps the call. SUTRA currently declares only `tool_execution`. This is the only
   surface that reaches conversation-level selection, prefix shaping and output caps.
2. **Provider prompt-cache visibility** is already available through the proxy's usage
   objects; it should be recorded in every comparison, because a pre-call rewrite that
   changes earlier messages would reduce those hits.
3. **Semantic answer reuse is not recommended**: "same question" does not imply "same
   answer", and the exact-reuse path is safe precisely because it requires identical
   input.
4. **A proxy is not justified yet.** Measure the multi-turn multiplier and an
   experimental `llm_request` adapter first.

## 16. Tests and lint

- `python -m pytest -q`: **991 passed, 1 skipped in 322.12 s**.
- `ruff check .`: clean.
- New test files: `tests/test_context.py` (29), `test_redundancy.py` (18), `test_cache.py`
  (18), `test_accounting.py` (12), `test_providers.py` (21), `test_repomap.py` (8), plus
  additions to `test_search.py`, `test_optimize.py`, `test_failure.py`,
  `test_config.py`, `test_invariants.py`.

## 17. Files changed

71 files, +10,008 / −32 against `main`. The new runtime modules are `cache.py`,
`context.py`, `cost.py` (extended), `providers.py`, `redundancy.py`, `repomap.py`; new
benchmarks under `bench/` and `bench/agent/`; new documentation under `docs/`.

## 18. Commit and branch

Branch `feat/sutra-max-effort`, pushed to `origin`. The validation commits are
`98270aa` (mode and field-level reporting), `b75e579` (harness and corpus), `df05d36`
(docs and live plumbing), `8877979` (next architecture), `7476b9c` (corpus results and
invalid-run filtering), `2cc95b3` (live runs and gateway handling). The full branch
contains the optimization work listed in the pull request.

## 19. Pull request and review

Pull request: https://github.com/g0GobliN/sutra/pull/96. Review status: open; CI runs
the four-Python test matrix and the Windows matrix on the pull request.

## 20. Merge status

Not merged. Nothing is tagged or released, and the package version is unchanged.

## 21. Remaining work

- A scored quality A/B using `docs/ab-benchmark.md` and an evaluator.
- Multi-turn measurements, so the re-send multiplier on every saving is known.
- An experimental `llm_request` adapter with a conservative selection policy, measured
  against the cache numbers above.
- Provider-billed cost figures, which need an authorised paid run.
- A decision on whether the search layer ships opt-in (current default: off).
