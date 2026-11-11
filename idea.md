# SUTRA v0.6 — Evidence-Driven Output Intelligence

## Mission Overview

**Repository:** https://github.com/g0GobliN/sutra
**Working branch:** `feat/evidence-driven-output-intelligence` (to be created from latest `main`)
**Target:** SUTRA v0.6
**Primary Objective:** Make SUTRA demonstrably more effective at reducing coding-agent tool output while preserving the evidence required to solve engineering tasks.

---

## Current Architecture Summary (from code inspection)

### Core Modules
| Module | Purpose | Key Features |
|--------|---------|--------------|
| `engine.py` | Classification & deterministic compression | Kinds: empty, patch, json, traceback, text. Head/tail elision, repeat collapse, JSON field compression, diff protection, traceback tail preservation, fail-open everywhere |
| `core.py` | Host-agnostic adapter seam | `process_result`/`process_text` entry points, `Outcome` contract, exact-duplicate reuse, bypass for `sutra_restore`/`sutra_compress` |
| `config.py` | Single-source configuration resolver | `resolve()` per-call, env vars with validation/fallback warnings, all settings in `Config` dataclass |
| `store.py` | Bounded local storage for originals | Atomic writes, 0700/0600 perms, TTL/quota/entry limits, O_NOFOLLOW, id validation `^su_[0-9a-f]{32}$` |
| `recovery.py` | Retrieval protocol (`sutra_restore`) | Modes: bytes, lines, search, full. JSON field scoping via RFC 6901 pointers. Hard bounds on responses. |
| `reuse.py` | Exact-duplicate reuse within session | SHA-256 keyed, store-verified hits, bounded LRU per session, REUSABLE_RULES filter |
| `metrics.py` | Operational telemetry | Compression/restore/cleanup/reuse counters, JSONL savings log, process-scoped snapshot |
| `policy.py` | Optional cost-aware prompt guidance | Modes: off/balanced/aggressive. Injected via Hermes system-prompt-section API. |
| `utf8.py` | UTF-8 boundary arithmetic | Character-boundary cuts for engine & recovery |
| `bench/benchmark.py` | Reproducible byte/recovery/timing benchmarks | 8 fixture cases, SVG charts, artifact verification, load-aware timing policy |
| `bench/tokens.py` | Token counting (o200k_base) | Optional tiktoken dependency |
| `scripts/ab/run.py` | A/B live-agent evaluation runner | 8 deterministic tasks, paired control/treatment, SQLite session usage, scorer validation |

### Existing Benchmark Fixtures (8 cases)
1. **12,000-line command output** — plain text, head+tail
2. **JSON tool envelope** — decoded, string fields trimmed
3. **Test run, 2,000 cases** — repeat collapse + head/tail
4. **3,000-frame traceback** — error end preserved
5. **432 kB patch** — never rewritten (safety case)
6. **900-row SQL result** — head+tail
7. **8,000-line build log** — one error buried in middle
8. **8,000-item JSON array** — array stays array (shape kept)

### A/B Evaluation Tasks (8 tasks)
1. `log-failure` — find failing test name in run.log
2. `traceback` — exception type + context token
3. `assertion` — variable name in AssertionError
4. `patch` — file modified + exported constant
5. `json-record` — email of record id=450
6. `buried` — clue sentence in build.log
7. `boundary` — retry budget token in service.log
8. `coding` — fix discount.py bug, verify with test

---

## Phase Breakdown (per mission)

### Phase Zero — Baseline and Gap Analysis
- [ ] Run existing unit tests
- [ ] Run existing benchmark self-checks
- [ ] Run existing A/B runner self-checks
- [ ] Inspect existing benchmark reports
- [ ] Establish current compression and latency baseline
- [ ] Verify current recovery behaviour
- [ ] Inspect existing parser coverage (currently none — engine uses generic classification only)
- [ ] Identify missing test coverage

### Phase One — Reproducible Output Benchmark Corpus
- [ ] Extend `benchmarks/` infrastructure to 50+ CLI output fixtures
- [ ] Cover: Jest, Vitest, TypeScript, ESLint, Pytest, Mypy, Cargo, Go, npm/pnpm/yarn, Docker, Git, compiler errors, dependency failures, stack traces, repetitive test output, large JSON
- [ ] Measure: original/compressed bytes, byte reduction %, raw/compressed tokens (cl100k_base, o200k_base), token reduction %, processing time, restoration correctness, essential evidence retention
- [ ] Generate machine-readable JSON + human-readable summaries

### Phase Two — Structured CLI Parsers
- [ ] Build deterministic parser framework integrating with existing engine
- [ ] Parser families: Test runners (Jest, Vitest, Pytest, Cargo test, Go test), Compilers (TypeScript, Mypy, Cargo check, Go), Runtime errors (Python traceback, Node.js), Linters (ESLint), Build (npm, pnpm, yarn, Docker)
- [ ] Shared parser contract: detect format, identify success/failure, extract evidence, identify repetitive passing output, produce deterministic compression candidates, preserve ordering, safe fallback
- [ ] Repetitive output handling: collapse passing tests into counters, preserve all failure details
- [ ] Git diff protection unchanged
- [ ] Fail-open guarantees for every parser

### Phase Three — Adaptive Output Budgets
- [ ] Optional `--max-tokens=1000` flag + env/config equivalent
- [ ] Preserve byte-based config, backward compatible defaults, validate values
- [ ] Explicit tokenizer selection, deterministic byte-based fallback
- [ ] Adaptive truncation: preserve command/context head, essential failures, final summary/tail, remove lower-priority middle, insert recovery reference
- [ ] Use existing storage/recovery abstraction
- [ ] Protected output never blindly truncated

### Phase Four — Live Agent Evaluation
- [ ] Extend existing `scripts/ab/run.py` infrastructure
- [ ] Paired experiments: Control (SUTRA disabled) vs Treatment (SUTRA enabled)
- [ ] Identical: model, settings, repo state, tasks, tools, environment, criteria
- [ ] Metrics: task completion, test correctness, regression rate, input/output tokens, provider usage, tool calls, recovery calls, execution time, failure rate, cost
- [ ] Statistical honesty: sample size, repetitions, confidence intervals, model/provider, task selection, limitations
- [ ] Begin with 8-task pilot, expand to 20-30 tasks

### Phase Five — Testing and Regression Protection
- [ ] Tests for: every parser, passing test aggregation, multiple failures, malformed output, unknown output, long stack traces, large diagnostics, adaptive budget boundaries, recovery references, exact restoration, parser exceptions, storage failures, git diff protection, JSON protection, binary input, adapter compatibility, config compatibility, performance regressions
- [ ] Run full existing test suite
- [ ] Run all benchmark self-checks
- [ ] Package build/install checks
- [ ] Static analysis and linting
- [ ] Never weaken existing tests

### Phase Six — Documentation
- [ ] Update: README.md, CHANGELOG.md, benchmark methodology, parser support docs, config reference, CLI usage, recovery docs, architecture docs
- [ ] Document: new flags, config options, supported parsers, limitations, recovery behavior, benchmark methodology, reproduction commands, verified metrics
- [ ] Preserve SUTRA identity: "Less Output. More Signal."

### CI and Release Quality
- [ ] Inspect existing workflows
- [ ] Add/extend CI: unit tests, parser regression tests, fail-open tests, benchmark self-checks, package build, static analysis
- [ ] Keep live-agent experiments optional, separate from CI
- [ ] No paid API calls for standard CI
- [ ] No auto-release without authorization

### Pull Request and Delivery
- [ ] PR titled: `feat: evidence-driven output intelligence`
- [ ] PR description with all 12 required sections
- [ ] Logical, independently understandable commits
- [ ] All checks passing
- [ ] Review complete diff for regressions, security, complexity, data leakage, breaking changes, incorrect claims, unsafe truncation, incomplete recovery
- [ ] Merge only when satisfied; verify main HEAD, CI, package build, version consistency, release readiness

---

## Key Constraints (Non-Negotiable)

1. **Inspect before implementing** — never assume proposed architecture is absent
2. **Preserve existing architecture** — extend, don't replace; keep public APIs, adapter contracts, config compatibility, recovery protocol, storage format, CLI/MCP contracts, Hermes integration, fail-open guarantees, zero mandatory runtime dependencies
3. **Execution rules** — working branch from latest main, never work on main, ~10-20 logical commits, conventional commit messages, no AI co-authors, no private data in commits
4. **Fail-open everywhere** — any error returns original text
5. **Never invent content** — only remove/collapse/elide with markers
6. **Preserve payload format** — JSON stays JSON, diffs byte-identical
6. **Configuration single-source** — `config.resolve()` per-call
7. **No credentials in summaries** — use [REDACTED]
8. **Tokenizer deps optional** — never silently substitute estimates

---

## Implementation Map (What Exists vs What's Needed)

| Area | Exists | Incomplete | Needs Improvement | Genuinely New |
|------|--------|------------|-------------------|---------------|
| Deterministic compression | ✅ Full | | | |
| Head/tail truncation | ✅ Full | | | |
| Exact-duplicate reuse | ✅ Full | | | |
| Local storage & recovery | ✅ Full | | | |
| Recovery references | ✅ Full | | | |
| Fail-open behavior | ✅ Full | | | |
| Benchmark runners | ✅ 8 fixtures | | 50+ fixtures needed | |
| A/B evaluation | ✅ 8 tasks | | 20-30 tasks needed | |
| Hermes integration | ✅ Full | | | |
| CLI adapter | ✅ Full | | | |
| MCP adapter | ✅ Full | | | |
| Structured CLI parsers | ❌ None | | | ✅ Parser framework + families |
| Adaptive token budgets | ❌ None | | | |
| Token benchmarking | ✅ o200k_base | cl100k_base missing | | |
| Test coverage | ✅ Extensive | Parser tests missing | Fail-open parser tests | |
| Documentation | ✅ Good | | Parser docs, budget docs | |

---

## Next Steps

1. Create working branch `feat/evidence-driven-output-intelligence` from `main`
2. Run Phase Zero baseline (tests, benchmarks, self-checks)
3. Record baseline measurements
4. Begin Phase One: extend benchmark corpus to 50+ fixtures
5. Parallel: design parser framework (Phase Two)
6. Implement adaptive budgets (Phase Three)
7. Extend A/B evaluation (Phase Four)
8. Complete testing (Phase Five)
9. Update documentation (Phase Six)
10. CI, PR, delivery

---

## Agent Delegation Plan

| Agent | Responsibility |
|-------|----------------|
| **Grik (CEO)** | Ownership, coordination, architecture decisions, PR review, final delivery verification |
| **Forge** | Implementation: parser framework, adaptive budgets, benchmark extensions, CLI flags |
| **Scout** | Root-cause investigation if baseline tests fail, parser defect analysis |
| **Sentinel** | Independent code audit of all changes, security review, fail-open verification |
| **Pixel** | Product-level: CLI UX, benchmark report formats, documentation structure |
| **Rook** | Engineering execution: test infrastructure, CI integration, package build verification |