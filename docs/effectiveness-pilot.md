# Effectiveness pilot: deterministic fixtures and host adapters

**Status: completed pilot; live-agent arm not run.** This report records a
deterministic, offline measurement of SUTRA's tool-output handling plus
adapter-surface checks for the four supported hosts. It is not a live-model A/B
result, not a provider bill, and not evidence of live task success.

The pilot was run to find out where SUTRA works, where it does not, and what
still needs a live experiment. The result is deliberately split into three
evidence classes: synthetic fixture handling, host adapter probes, and the
unmeasured live-agent questions.

## 1. Evaluation metadata

| Field | Value |
|---|---|
| Evaluation date | 1 November 2026 |
| SUTRA version | 0.4.2 |
| Commit | `f7bb129ebfb60e4482402b179d44cfd6befc1122` |
| Working tree at run | clean |
| Operating system | Linux x86_64 (`6.12.113+deb13-amd64`, glibc 2.41) |
| Python | CPython 3.13.5 |
| Tokenizer | `tiktoken 0.14.0`, `o200k_base` |
| Hosts probed | Hermes 0.22.5, Claude Code 2.1.268, OpenCode 1.19.0; Codex CLI was not installed |
| Model / provider | not applicable — no model was called |
| Deterministic fixtures | 23 |
| Fixture measurement pass | one pass; five core-latency trials per fixture |
| Restoration calls | 66; all successful |
| Host adapter checks | 18; all passed |
| Live agent runs | 0 — see [Unavailable metrics](#8-unavailable-metrics) |

No API key, model account, authentication or paid inference was used. The host
probe for Codex exercised the repository's real hook script, but not a live
Codex CLI session, because the binary was absent from the pilot environment.

## 2. Method

The deterministic suite generated 23 fixed tool-output payloads covering large
terminal output, repeated logs, JSON responses, deeply nested JSON, stack
traces, error messages, git diffs, test output, build output, package-manager
output, long file listings, mixed ANSI output, Unicode and multilingual text,
CRLF and LF line endings, malformed JSON, hostile input, and payloads that must
remain unchanged.

Each payload was passed through the real shared core, store and recovery seam
with a temporary `HERMES_HOME`. The runner did not invoke a model. For each
payload it recorded:

- original and result UTF-8 byte counts;
- original and result token counts from the pinned tokenizer;
- reduction percentage, using `((original - result) / original) * 100`;
- the selected rule and whether the payload changed;
- core compression latency, pipeline latency including retention, and
  restoration latency;
- whether protected content changed;
- whether the original restored byte-for-byte;
- whether every planted clue was visible or recoverable.

Host probes drove the documented adapter surface without installing anything
or starting a gateway:

- Claude Code: the real `PostToolUse` hook script with a synthetic event;
- Codex: the real `PostToolUse` hook script with a synthetic Bash event;
- OpenCode: the generated `tool.execute.after` plugin loaded by Node, including
  its `sutra_restore` tool;
- Hermes: the in-process `tool_execution` middleware and `register()` entry
  point.

The probes checked large-result replacement, small/protected pass-through,
recovery and malformed-payload fail-open behaviour. They do not prove that a
live host loads the installed hook or plugin.

## 3. Baseline and SUTRA results

The deterministic baseline is the original tool output. The SUTRA arm is the
same payload after the shared compression path. There is no model response in
either arm.

| Scope | Fixtures | Original bytes | SUTRA bytes | Saved | Reduction |
|---|---:|---:|---:|---:|---:|
| Rewritten fixtures | 17 | 3,184,324 | 286,233 | 2,898,091 | **91.01%** |
| All fixtures | 23 | 3,667,746 | 769,655 | 2,898,091 | **79.02%** |
| Protection cases | 6 | 483,422 | 483,422 | 0 | **0.00%** |

The protection cases are intentionally unchanged. They are not misses.

## 4. Byte reduction

The 17 rewritten fixtures saved **2,898,091 bytes** from **3,184,324 bytes**,
a **91.01%** reduction. Including the six protection cases, the suite saved the
same absolute bytes from **3,667,746 bytes**, a **79.02%** reduction.

The difference between the two percentages is the point of the protection
policy: a compressor that shrinks a diff or reshapes a JSON array can make the
aggregate look better while making the result less trustworthy.

## 5. Token reduction

| Scope | Original tokens | SUTRA tokens | Saved | Reduction |
|---|---:|---:|---:|---:|
| Rewritten fixtures | 1,148,921 | 103,268 | 1,045,653 | **91.01%** |
| All fixtures | 1,343,010 | 297,357 | 1,045,653 | **77.86%** |
| Protection cases | 194,089 | 194,089 | 0 | **0.00%** |

These are local tokenizer estimates from `tiktoken 0.14.0` with `o200k_base`.
They are **not provider input tokens**, not prompt-cache accounting and not a
billing measurement. See [Unavailable metrics](#8-unavailable-metrics).

## 6. Per-workload findings

The following table is the aggregate for each payload class. `n` is the number
of fixtures in that class. Token reduction can differ slightly from byte
reduction because token boundaries do not align with byte boundaries.

| Payload class | n | Bytes before → after | Byte reduction | Token reduction |
|---|---:|---:|---:|---:|
| Repeated logs | 1 | 414,078 → 963 | **99.77%** | 99.72% |
| Long file listing | 1 | 556,926 → 18,627 | **96.66%** | 96.75% |
| Stack trace | 1 | 298,421 → 10,418 | **96.51%** | 96.40% |
| Large terminal output | 1 | 450,074 → 18,652 | **95.86%** | 95.91% |
| Build output | 1 | 293,004 → 18,644 | **93.64%** | 93.74% |
| Mixed ANSI output | 1 | 148,917 → 11,897 | **92.01%** | 95.21% |
| Unicode / multilingual | 1 | 114,408 → 18,631 | **83.72%** | 83.64% |
| CRLF lines | 1 | 112,908 → 18,663 | **83.47%** | 83.72% |
| Package-manager output | 1 | 102,636 → 18,639 | **81.84%** | 81.96% |
| LF lines | 1 | 100,905 → 18,640 | **81.53%** | 81.86% |
| Test output | 1 | 100,475 → 18,621 | **81.47%** | 81.79% |
| Malformed input | 2 | 101,401 → 18,610 | **81.65%** | 81.82% |
| JSON response | 2 | 188,833 → 38,518 | **79.60%** | 79.68% |
| Hostile input | 2 | 134,262 → 37,306 | **72.21%** | 58.93% |
| Deeply nested JSON | 1 | 67,076 → 19,404 | **71.07%** | 70.66% |
| Error message | 1 | 1,080 → 1,080 | 0.00% | 0.00% |
| Git diff | 1 | 165,922 → 165,922 | 0.00% | 0.00% |
| Should remain unchanged | 3 | 316,420 → 316,420 | 0.00% | 0.00% |

### Which workloads benefit most

Repeated logs, long file listings, stack traces, terminal output and build
output are the strongest cases in this pilot: all exceed 93% byte reduction.
JSON benefits when a large string field can be trimmed while the document keeps
its shape: the JSON response and deeply nested JSON classes landed between 71%
and 80%.

### Which workloads should bypass compression

The pilot supports leaving these alone:

- **Diffs and patches.** Byte-identical; a rewritten patch can hide a change.
- **Large JSON arrays with no large string field.** Eliding items to make the
  array smaller would change its shape.
- **Small errors and below-threshold text.** Nothing is worth removing.
- **Empty and non-UTF-8 strings.** There is no safe rewrite or byte estimate to
  invent.
- **The smaller hostile-input case** (30,012 bytes) is the weakest rewrite at
  37.78%; it is a candidate for a future bypass or a narrower rule.

## 7. Protection, restoration and reliability

### Protection

All six protection cases were unchanged, byte-for-byte:

- the 165,922-byte git diff;
- the 313,890-byte JSON array;
- the 1,080-byte error message;
- the 2,530-byte below-threshold payload;
- the empty payload;
- the non-UTF-8 string.

JSON documents that were rewritten kept their top-level type, keys, array
lengths and non-string values. The visible formatting is re-encoded, so
indentation and whitespace are not preserved; the original bytes remain
recoverable. This is the documented JSON limitation, not a new result.

There were **0 failed checks** across the 23 fixtures. No protected content was
modified, and no rewritten fixture contained a visible line absent from the
original.

### Restoration

- **17/17** rewritten fixtures restored to the **exact original bytes**.
- Every planted clue was either visible in the compressed copy or returned by a
  literal `sutra_restore` search.
- **66** restoration calls were made in total (bounded byte paging plus clue
  searches); all succeeded.
- Restoration latency: median **1.36 ms**, p95 **2.91 ms**, maximum **5.15 ms**.

This proves retention and retrievability in the deterministic fixture suite. It
does not prove that a live agent will choose to call the recovery tool.

### Fail-open

The deterministic run had **0 uncaught exceptions**. The host adapter probes
included malformed JSON, protected/small payloads and a deliberately broken
interpreter for OpenCode; all returned the original or a no-op without a crash.
**18/18** adapter checks passed.

No live fail-open events are reported because no live session was run.

## 8. Unavailable metrics

These are not zero results. They were not measured.

### Live task success

**Unavailable.** The six task classes — failing tests, application debugging,
build failure, git diff analysis, structured-output inspection and recovery —
were not run against a live agent.

To measure it, a maintainer must explicitly authorize paid inference and
provide a provider account or API key for the selected host and model. The
comparison must run the baseline (SUTRA disabled) and treatment (SUTRA enabled)
arms with the same model, model version, system instructions, task, tools,
environment and timeout. Suggested credentials: `ANTHROPIC_API_KEY` for Claude
Code, an OpenAI credential for Codex CLI, and the selected provider credential
for OpenCode or Hermes. Codex additionally requires trusting the hook once via
`/hooks`. A baseline failure is an invalid task, not a SUTRA success.

### Provider-reported input-token reduction

**Unavailable.** No provider usage exists for this pilot. Local tokenizer counts
are reported above and are not a substitute.

Measuring it requires the same live authorization and credentials, plus a host
that exposes usage: Claude Code `-p --output-format json`, Codex
`codex exec --json`, Hermes `session_model_usage`, or OpenCode JSON events.
Tasks must be paired, with the same model in both arms and enough repetitions to
show the spread. Prompt caching must be reported separately because it makes
re-sent context cheaper than raw input-token counts suggest.

### Live host ranking

**Unavailable.** The adapter probes passed 18/18 checks, but a live ranking
requires paired live sessions on all four hosts with the same model, tasks and
repetitions, and provider usage where available. This pilot cannot say which
host is best in real use.

## 9. Host adapter checks

| Host | Version measured | Surface probed | Checks | Live session validated |
|---|---|---:|---:|---|
| Claude Code | 2.1.268 | `PostToolUse` hook (`updatedToolOutput`) | 4/4 | no |
| Codex CLI | not installed | `PostToolUse` hook (`continue: false` / `stopReason`) | 4/4 | no |
| OpenCode | 1.19.0 | generated `tool.execute.after` plugin + `sutra_restore` | 5/5 | no |
| Hermes | 0.22.5 | native `tool_execution` middleware + recovery tool | 5/5 | no |

The checks covered:

- large-result replacement and recovery;
- below-threshold passthrough;
- diff protection;
- malformed-payload fail-open;
- for OpenCode, failure to start the embedded interpreter;
- for Hermes, registration of the middleware and `sutra_restore` tool, and
  identity pass-through for an unknown result shape.

### Adapter latency

These are single observations after a warm-up on a loaded host, not p95 values.
They include the adapter's subprocess or plugin overhead and the store write;
they are not comparable to core compression latency.

| Host | Large-result probe | Note |
|---|---:|---|
| Hermes | 500.97 ms | in-process middleware; no per-result subprocess |
| Claude Code | 717.46 ms | Python hook subprocess |
| Codex | 666.71 ms | Python hook subprocess; CLI not installed |
| OpenCode | 3,022.05 ms | Node plugin + Python subprocess |
| OpenCode direct CLI path | 792.40 ms | the subprocess the plugin launches |

OpenCode has the largest measured adapter overhead and should be profiled on an
idle host before a live comparison. Hermes has the lowest measured overhead of
the four surfaces.

Codex's apparent large-result reduction was 98.93%, compared with 95.86% for the
other hosts. That is not a like-for-like advantage: Codex truncates very large
output before the hook and the hook is capped at 8 KiB, so its retained original
is already host-truncated.

## 10. Latency

| Phase | Samples | Median | p95 | Maximum |
|---|---:|---:|---:|---:|
| Core compression | 115 | 15.34 ms | 141.40 ms | 240.45 ms |
| Pipeline including store | 23 | 33.24 ms | 224.20 ms | 247.14 ms |
| Restoration | 66 | 1.36 ms | 2.91 ms | 5.15 ms |

Core compression is the pure engine, without store I/O or host subprocess
startup. Pipeline includes retention. Restoration is bounded `sutra_restore`
work. The host overhead is reported separately above.

The machine was not isolated for benchmarking. These numbers describe this
pilot; they are not a performance guarantee and should be repeated on an idle
host and on other platforms.

## 11. Pilot gates

These gates were set after the deterministic pilot and before this report. They
are practical thresholds for the next evaluation, not universal laws.

| Gate | Threshold | Pilot |
|---|---|---|
| Byte reduction on rewritten fixtures | ≥ 50% | 91.01%, met |
| Tokenizer reduction on rewritten fixtures | ≥ 50% | 91.01%, met |
| Planted information visible or restored | 100% | 17/17, met |
| Byte-exact restoration | 100% | 17/17, met |
| Protection cases unchanged | 100% | 6/6, met |
| Adapter fail-open checks | 100% | 18/18, met |
| Core compression p95 | ≤ 250 ms | 141.40 ms, met |
| Pipeline p95 | ≤ 500 ms | 224.20 ms, met |
| Restoration p95 | ≤ 50 ms | 2.91 ms, met |
| Live task success | not defined until a live pilot | unavailable |
| Provider token reduction | measured only; never inferred | unavailable |

A future result that misses a gate should be reported as “no demonstrated
improvement in this condition”, followed by a targeted investigation — not as a
reason to tune the compressor to the benchmark.

## 12. Failed or incomplete runs

- Deterministic fixtures: **0 failed checks**; all 23 completed.
- Host adapter checks: **0 failed**; 18/18 completed.
- Codex live host: **incomplete by environment**; the binary was not installed,
  so only the hook script was probed. No Codex version is reported.
- Live agent arms: **not started**. No task success, provider usage or live host
  ranking exists.

No failed run was removed from the pilot.

## 13. Limitations

- The deterministic results are synthetic tool-output handling. They do not
  measure reasoning, task success or whether an agent asks for recovery.
- Token estimates use one pinned tokenizer and can differ from a provider's.
- The pilot is one measurement pass on one loaded Linux host. It provides no
  statistical confidence.
- Host probes use the real adapter scripts and plugin, but not a live host
  process. They cannot prove host loading, trust, authentication or task
  completion.
- Codex truncates output before its hook; its retained original is the supplied
  text. A live Codex run must account for that.
- JSON re-encoding normalises visible formatting; the original bytes are
  recoverable, but the visible copy is not byte-identical.
- Prompt caching and auxiliary model calls can change provider usage; a live
  experiment must report them rather than treating raw token counts as a bill.
- The evaluation harness and raw result files are internal research tooling.
  They are not part of this repository and are not published with this report.

## 14. Reproduction

The public repository does not contain the evaluation harness, fixtures or raw
result files. This report records the method and the completed measurements so
the claim can be audited; it does not publish a runnable benchmark harness.

An independent implementation can reproduce the design by:

1. Generating deterministic payloads for the classes in §2, including large
   terminal output, repeated logs, JSON, nested JSON, stack traces, diffs, test,
   build and package-manager output, listings, ANSI, Unicode, CRLF/LF,
   malformed/hostile input and protection cases.
2. Running each payload through the real shared core with a temporary store.
3. Counting UTF-8 bytes before and after, and tokenising both with
   `tiktoken==0.14.0` and `o200k_base`.
4. Verifying that every protection case is byte-identical and that every
   rewritten payload restores to the exact original bytes through the real
   store and recovery tool.
5. Driving the Claude Code, Codex, OpenCode and Hermes adapter surfaces with
   synthetic host payloads, including malformed input and a below-threshold
   case.

Reproducing the **live** arm additionally requires explicit paid-inference
authorization, a model account and provider credentials. The protocol for that
comparison is documented in [ab-benchmark.md](ab-benchmark.md); it has not been
run.

## 15. Conclusion

This pilot supports four narrow claims:

- SUTRA can substantially reduce bytes and tokenizer-estimated tokens on
  eligible tool output.
- It can restore every rewritten fixture in this suite byte-for-byte.
- It preserves diffs, JSON shape and the other protection cases in this suite.
- The four adapter surfaces fail open on the malformed and protected payloads
  exercised here.

It does **not** support claims about live task success, provider-reported token
savings or a best host. Those require the opt-in live experiment described
above. A result showing no improvement in a scenario is a valid research result,
and this pilot deliberately reports the protected and weak cases alongside the
strong ones.

For the existing deterministic benchmark and its method, see
[benchmark.md](benchmark.md). For recovery semantics and bounds, see
[recovery.md](recovery.md).
