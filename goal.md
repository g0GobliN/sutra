# SUTRA v0.6 — Goal & Acceptance Criteria

## Mission Statement
Make SUTRA demonstrably more effective at reducing coding-agent tool output while preserving the evidence required to solve engineering tasks.

---

## Measurable Acceptance Criteria

### Phase Zero — Baseline (MUST complete first)
- [x] All existing unit tests pass (`pytest tests/ -x`) — 432 passed, 1 skipped
- [x] Benchmark self-check passes (`python3 bench/benchmark.py --check`)
- [x] A/B runner self-check passes (`python3 scripts/ab/run.py --selfcheck`)
- [x] Current baseline recorded (verified at 6b37e48):
  - Test count: 433 total, 432 pass, 0 fail, 1 skip (`python3 -m pytest -q -p no:cacheprovider tests/`)
  - Benchmark self-check: artifacts are consistent (`python3 bench/benchmark.py --check`)
  - A/B runner self-check: all 8 scorers OK (`python3 scripts/ab/run.py --selfcheck`)
  - Byte reduction across 8 fixtures: 54.3% (1,626,501 → 742,727 bytes ≈ 1.6 MB → 742.7 kB) (`bench/results.json`)
  - Token reduction (o200k_base): 55.2% (585,977 → 262,517 tokens) (`bench/tokens.json`)
  - Token reduction (cl100k_base): NOT measured — gap for Phase One (tokens.json records o200k_base only)
  - Compression latency: deferred — 1-min load 5.14 on 4 CPUs (1.29 per CPU) > limit 1.00 per CPU; needs idle machine
  - Recovery success rate: 8/8 clues recovered (100%) (`bench/recovery.json`)
  - Fail-open test coverage: 15 dedicated fail-open/fail-safe tests (test_sutra.py, test_recovery.py, test_fuzz.py, test_config.py, test_codex_hook.py, test_claude_code_hook.py, test_metrics.py, test_reuse.py)
  - Live-agent evaluation coverage: A/B runner defines 8 tasks + 8 scorers, selfcheck OK; pilot results not executed in baseline (provider access required)

### Phase One — Benchmark Corpus (50+ fixtures)
- [ ] At least 50 reproducible CLI output fixtures committed to `benchmarks/fixtures/`
- [ ] Fixture coverage matrix:
  - JavaScript/TypeScript: Jest ✅, Vitest ✅, TypeScript compiler ✅, ESLint ✅
  - Python: Pytest ✅, Standard tracebacks ✅, Mypy ✅
  - Rust/Go: Cargo check ✅, Cargo test ✅, Go test ✅, Go compiler ✅
  - Build/Packaging: npm ✅, pnpm ✅, Yarn ✅, Docker build ✅
  - General: Git status ✅, Git diff ✅, Git patches ✅, Compiler errors ✅, Dependency failures ✅, Long stack traces ✅, Repeated test output ✅, Large JSON ✅
- [ ] Each fixture has:
  - Provenance documented (real redistributable / synthetic labeled)
  - Expected evidence defined (failed test names, exception types/messages, error codes, file paths, line/col numbers, compiler diagnostics, diff headers/hunks, stack frames, build summaries)
- [ ] Benchmark measurements for each fixture (both tokenizers where available):
  - Original bytes, compressed bytes, byte reduction %
  - Raw token count, compressed token count, token reduction %
  - Processing time (median over 5 runs)
  - Restoration correctness (exact byte match)
  - Essential evidence retention (all designated critical evidence visible or recoverable)
- [ ] Machine-readable JSON reports + human-readable summaries generated
- [ ] Reports reproducible from committed public fixtures only
- [ ] No private evaluation outputs committed

### Phase Two — Structured CLI Parsers
- [ ] Deterministic parser framework integrated with existing engine (`engine.py` classification + compression)
- [ ] Parser contract implemented:
  - `detect(text: str) -> bool` — format detection
  - `extract_evidence(text: str) -> Evidence` — critical evidence extraction
  - `find_repetitive_sections(text: str) -> list[Section]` — passing output to collapse
  - `compress(text: str, limits: Limits) -> CompressedResult` — deterministic compression candidates
  - `fallback(text: str) -> str` — safe return on uncertainty
- [ ] Parser families implemented with tests:
  - **Test runners**: Jest, Vitest, Pytest, Cargo test, Go test
  - **Compilers**: TypeScript `tsc`, Mypy, Cargo check, Go compiler
  - **Runtime errors**: Python traceback, Node.js stack traces
  - **Linters**: ESLint
  - **Build**: npm, pnpm, Yarn, Docker build
- [ ] Repetitive output handling:
  - Collapse passing tests into deterministic counters (e.g., `[142 tests passed, omitted]`)
  - Preserve ALL failure details: names, assertions, expected/actual, stack traces, file paths, line numbers, error codes, summaries
  - Never discard a failure due to misclassification
- [ ] Git diff protection: standard unified diffs, multi-file diffs, binary indicators, renames, large patches, malformed diff-like content — all remain byte-identical
- [ ] Fail-open tests for every parser:
  - Detection failure → original returned
  - Parsing exception → original returned
  - Malformed input → original returned
  - Unknown format → original returned
  - Invalid parser result → original returned
  - Parser exceptions cannot break tool execution (dedicated tests)

### Phase Three — Adaptive Output Budgets
- [ ] `SUTRA_MAX_TOKENS` environment/config equivalent documented and working
|- [ ] Backward compatibility: existing byte-based config preserved, current defaults unchanged
- [ ] Validation: invalid/negative values rejected with clear errors
- [ ] Tokenizer selection explicit (cl100k_base, o200k_base)
- [ ] Deterministic byte-based fallback when tokenizer unavailable
- [ ] No silent substitution of byte estimates for token counts
- [ ] Adaptive truncation algorithm:
  1. Preserve command/context from beginning
  2. Preserve essential failure information
  3. Preserve final summary and relevant tail
  4. Remove lower-priority middle sections
  5. Insert recovery reference pointing to actually-stored entry
  6. Verify result satisfies budget OR explicitly reports budget unsatisfiable
- [ ] Configurable head/tail limits (not fixed line counts)
- [ ] Semantic evidence prioritized over arbitrary line counts
- [ ] Recovery uses existing storage/recovery abstraction (no separate system)
- [ ] Protected output never blindly truncated (git diffs, binary, unknown structured, security-sensitive, already-protected)
- [ ] When protected output exceeds budget → explicit safe outcome (correctness > compression)

### Phase Four — Live Agent Evaluation
- [ ] Existing `scripts/ab/run.py` extended and validated
- [ ] Paired experiments: Control (SUTRA off) vs Treatment (SUTRA on)
- [ ] Identical conditions verified: model, settings, repo state, tasks, tools, environment, criteria
- [ ] Minimum 8 tasks (existing pilot), target 20-30 representative repository tasks
- [ ] Actual coding tasks (not planted string location)
- [ ] Metrics measured independently:
  - Task completion rate
  - Test correctness
  - Regression rate
  - Input tokens, output tokens
  - Provider-reported usage
  - Tool calls, recovery calls
  - Execution time
  - Failure rate
  - Estimated/actual cost where available
- [ ] Failed/incomplete sessions recorded (not excluded)
- [ ] No SWE-bench equivalence claims without recognized protocol
- [ ] Statistical reporting: sample size, repetitions, confidence intervals, model/version, provider, task selection, known limitations
- [ ] Distinguish deterministic compression savings from provider billing savings
- [ ] No intelligence-preservation claims based only on token reduction
- [ ] Inconclusive results reported as such

### Phase Five — Testing & Regression Protection
- [ ] New tests added for:
  1. Every supported parser
  2. Passing test aggregation
  3. Multiple simultaneous failures
  4. Malformed output
  5. Unknown output
  6. Long stack traces
  7. Large compiler diagnostics
  8. Adaptive budget boundaries
  9. Recovery references
  10. Exact restoration
  11. Parser exceptions
  12. Storage failures
  13. Git diff protection
  14. JSON protection
  15. Binary input
  16. Existing adapter compatibility
  17. Configuration compatibility
  18. Performance regressions
- [ ] Full existing test suite passes
- [ ] All benchmark self-checks pass
- [ ] Package build and installation verified
- [ ] Relevant adapter tests pass
- [ ] Static analysis and linting pass
- [ ] Tested on supported Python versions (CI matrix)
- [ ] No existing tests weakened
- [ ] Pre-existing defects fixed if in scope and safe

### Phase Six — Documentation

### CI & Release Quality
- [ ] Existing workflows inspected before modification
- [ ] CI jobs added/extended for:
  - Unit tests
  - Parser regression tests
  - Fail-open tests
  - Benchmark self-checks
  - Package build
  - Static analysis
- [ ] Live-agent experiments optional and explicitly separate from ordinary CI
- [ ] No paid model API calls for standard CI
- [ ] No credentials in GitHub Actions
- [ ] No auto-release without explicit release authorization
- [ ] Version number changed only when release ready

### Pull Request & Delivery
- [ ] PR titled: `feat: evidence-driven output intelligence`
- [ ] PR description contains all 12 required sections
- [ ] Implementation commits logical and independently understandable
- [ ] All required checks pass
- [ ] Complete PR diff reviewed for:
  - Unnecessary dependencies
  - Duplicate implementations
  - Public/private data leakage
  - Breaking API changes
  - Incorrect benchmark claims
  - Unsafe truncation
  - Incomplete recovery behavior
- [ ] Merge only when all requirements satisfied and repository policy permits
- [ ] Post-merge: main HEAD verified, CI verified, package build verified, version consistency verified, release readiness verified
- [ ] No PyPI publish or public release without explicit authorization

---

## Engineering Standards (Non-Negotiable)

| Standard | Requirement |
|----------|-------------|
| **Inspect first** | Read actual code before changing; identify smallest reliable implementation |
| **Preserve behavior** | No changes unless explicitly authorized; backward compatibility mandatory |
| **Fail-open** | Every parser, every path, every exception → original text returned |
| **No invented content** | Only remove/collapse/elide with explicit markers |
| **Format preservation** | JSON stays JSON, diffs byte-identical, tracebacks keep error end |
| **Single config source** | `config.resolve()` per-call only |
| **Tokenizer optional** | Never mandatory runtime dependency; explicit fallback |
| **Evidence preservation** | Critical evidence visible OR recoverable with exact restoration |
| **Test-driven** | Tests before code; RED-GREEN-REFACTOR; never weaken tests |
| **Honest reporting** | No fabricated progress, test results, or metrics |
| **Verified delivery** | Working artifacts backed by real tool output |

---

## Branch Strategy

```
main (protected)
  └── feat/evidence-driven-output-intelligence (working branch)
        ├── Phase 0: baseline
        ├── Phase 1: benchmarks/corpus
        ├── Phase 2: parsers
        ├── Phase 3: adaptive budgets
        ├── Phase 4: A/B evaluation
        ├── Phase 5: testing
        └── Phase 6: docs + CI
```

- ~10-20 logical commits
- Conventional commit messages (feat:, fix:, test:, docs:, ci:, refactor:)
- No AI co-author trailers
- No private data, credentials, local paths, personal info
- No private agent instructions or local dev files
- Benchmark reports: distinguish public fixtures from private evaluation

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Parser misclassifies failure as passing | Medium | High | Extensive fail-open tests; conservative detection; never discard failure |
| Token budget unsatisfiable for protected output | Medium | Medium | Explicit safe outcome; correctness priority |
| A/B evaluation blocked by provider access | High | Medium | Complete all local work; document exact blocker; continue independent work |
| Benchmark fixtures not redistributable | Low | Medium | Use synthetic fixtures; clearly label provenance |
| Performance regression in compression latency | Low | High | Performance regression tests; baseline comparison in CI |
| Breaking adapter contract | Low | High | Adapter contract tests; compatibility verification |

---

## Definition of Done

The mission is complete ONLY when ALL acceptance criteria above are demonstrated and verified.

**Final Report Must Include:**
1. Mission status (Completed / Partially completed / Blocked)
2. Repository: starting main SHA, final main SHA or PR branch SHA, PR URL, merge status
3. Implementation: modules added, modules modified, features completed, existing functionality reused
4. Verification: test counts, benchmark results, parser coverage, fail-open results, recovery results, latency, package build, CI status
5. Agent effectiveness: control vs treatment task completion, token usage, cost, execution time, sample size, limitations
6. Remaining issues: genuine outstanding work only
7. Recommendation: ready for next release or requires another iteration

---

## Current Status: PHASE ZERO — Baseline

**Next Action:** Create working branch and run baseline measurements.