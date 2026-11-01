<p align="center">
  <img src="https://raw.githubusercontent.com/g0GobliN/sutra/main/public/logo/sutraThumb.png" alt="SUTRA" width="520">
</p>

# SUTRA

**Less output. More signal.**

SUTRA is a deterministic tool-output optimization and recovery layer for AI coding agents. It processes eligible tool results after a command runs and before the model reads them, so a large log costs less context on every following turn — without a model paraphrasing it.

One core. Multiple agents. Recoverable output. No compression model calls.

> **Eight-fixture benchmark:** payload size reduced by **54.3%**, o200k tokenizer counts by **55.2%**; **8/8** planted clues recovered; **0** diffs touched. The fixtures are generated tool-output shapes and the token figure is not a provider bill — [method](#evidence-and-benchmarks).

[Why](#the-problem-tool-output-is-re-read-all-session) · [How it works](#how-sutra-handles-a-tool-result) · [Architecture](#architecture) · [Comparison](#how-sutra-compares-with-caveman-and-ponytail) · [Evidence](#evidence-and-benchmarks) · [Install](#install) · [Integrations](#supported-integrations) · [Limitations](#limitations)

## The problem: tool output is re-read all session

An agent runs `pytest -q`, receives 12,000 lines, and needs one failure. The useful part may be twenty lines, but the tool result stays in context and is re-sent on every subsequent turn. Prompt compression can shave an instruction once; tool output grows with every command an agent runs.

```text
turn 1   ── tool output 60.9 kB ──► model
turn 2   ── tool output 60.9 kB ──► model   (re-sent)
turn 3   ── tool output 60.9 kB ──► model   (re-sent)
         ...                               context grows with the session
```

The agent rarely needs the whole result: it needs the failing line, the stack frame, the row that matters — and a route back to everything else.

## How SUTRA handles a tool result

1. **The tool runs normally.** SUTRA does not wrap, delay or re-execute it.
2. **The host adapter hands the result to the shared core.** The core decides whether the result is eligible: size, payload shape, evidence tools, diffs.
3. **The core rewrites only what is safe to remove.** ANSI escapes, repeated lines and oversized middles are elided; diffs stay byte-identical, JSON keeps its shape, a traceback keeps its error end.
4. **The original is retained locally** when storage is enabled, with a bounded lifetime.
5. **The model sees a smaller result with a marker** naming what was removed, how much, and the exact `sutra_restore` call that returns it.
6. **Nothing is generated.** No summariser, no model call, no interpretation, no invented content.

If anything is unexpected — a malformed payload, a failed store write, an internal exception — SUTRA fails open and the host receives the original result unchanged.

## What makes SUTRA different

- **Deterministic, not probabilistic.** The same bytes produce the same rewrite and the same marker. No compression model decides what matters.
- **Recoverable by construction.** An id is written only after the original is on disk. A marker never cites bytes that were not kept.
- **Agent-agnostic core.** Hermes, Claude Code, OpenCode, Codex, MCP and the CLI all call the same eligibility, compression, storage and recovery code.
- **Host-appropriate integration.** Automatic replacement only where the host documents a result-replacing hook; explicit tools or MCP registration where it does not.
- **Safety boundaries that hold.** Diffs and patches are never rewritten, JSON is never byte-sliced, traceback tails are never dropped.
- **Fail-open at every adapter.** A broken host payload or a missing interpreter leaves normal agent execution alone.

| Capability | What it means in practice |
|---|---|
| Deterministic rules, no compression model | Logs are never interpreted or paraphrased by another model |
| Local original retention | Anything SUTRA omitted can be retrieved later, by id |
| Native host adapters | No manual compression step on hosts with a result hook |
| Diff and shape protection | Code changes are never silently rewritten; JSON stays JSON |
| Fail-open design | A malformed result leaves the agent running normally |
| Shared core | The same rules, markers and recovery across every integration |

## Architecture

```text
AI coding agent
      │  runs a tool
      ▼
tool execution
      │  raw result
      ▼
host adapter
  ├─ Hermes: tool_execution middleware          automatic
  ├─ Claude Code: PostToolUse hook              automatic
  ├─ OpenCode: tool.execute.after plugin        automatic
  ├─ Codex: PostToolUse hook (trust)             automatic
  └─ other clients: CLI or MCP server           explicit calls
      │
      ▼
shared SUTRA core
  ├─ eligibility: size, payload shape, evidence tools, diffs
  ├─ deterministic rewrite + a marker naming what was removed
  └─ local retention (TTL, quota, bounded reads)
      │
      ▼
model reads the smaller result
      │  marker quotes: sutra_restore(id="…", mode="…")
      ▼
original retrieved from the local store when needed
```

The recovery path is separate from the compression path: compression can run with storage disabled, but then the marker says the original was not retained and the model can only re-run the command narrower. Recovery reads the local store by validated id and bounded mode.

## How SUTRA compares with Caveman and Ponytail

SUTRA and [Caveman](https://github.com/JuliusBrussee/caveman) approach context efficiency from different architectural directions. Both address unnecessary context consumption, but SUTRA specialises in deterministic, recoverable tool-output processing at the integration layer. [Ponytail](https://github.com/DietrichGebert/ponytail) pulls a third lever: how much code the agent writes at all.

| Dimension | SUTRA | Caveman | Ponytail |
|---|---|---|---|
| Primary focus | Deterministic tool-output processing | Agent brevity plus the inputs it reads | How much code the agent writes |
| Where it acts | Between a tool result and the model, through host adapters | Skill plus CLI/proxy around the agent | Skill/rules before code is generated |
| What it transforms | Logs, JSON documents, tracebacks; diffs untouched | Replies, tool-readable files, web pages; routing and blocks in 4.0 | The agent's plan and implementation choices |
| Original recovery | Local bounded store, id marker, `sutra_restore` | Docs state originals stay on disk and can be pulled back | Not a compression layer; no recovery surface claimed |
| Model calls for compression | None — deterministic rules | No separate compression model for the voice; file compressors are tooling | None; the effect depends on the host model following its rules |
| Prompt handling | Does not touch prompts or replies | Docs state prompts are never rewritten | A prompt/rules layer by design |
| Agent support | Four first-class adapters plus MCP/CLI; automatic only where a result hook exists | 30+ agents via skill/installer | Many agents via skill/plugin |

The closest overlap is Caveman's "read less" path, which also shrinks what the model reads and keeps originals retrievable. SUTRA's difference is scope and guarantee: one deterministic, byte-level transform on the tool-result path, one local recovery protocol, and no automatic claim on a host that lacks a result hook.

Caveman's README reports 33.2% fewer input tokens across whole sessions (older engine), 8.5% fewer output tokens in a JetBrains paired test, and one HTML case 9.9% worse; Ponytail reports 45% fewer tokens and 53% less code on its 39-task benchmark. Those are their own measurements on their own workloads. Sources: [Caveman README](https://github.com/JuliusBrussee/caveman), [Caveman product page](https://caveman.so/products/caveman), [Ponytail README](https://github.com/DietrichGebert/ponytail). Details may change.

## Evidence and benchmarks

![Horizontal bar chart: bytes before and after SUTRA on eight payload shapes. Total 1.63 MB → 742.7 kB, 54.3% saved; the patch and JSON-array rows are unchanged on purpose.](https://raw.githubusercontent.com/g0GobliN/sutra/main/bench/savings.svg)

| Payload | Rule | Bytes before → after | o200k tokens before → after | Saved |
|---|---|---:|---:|---:|
| 12,000-line command output | `head-tail` | 60.9 kB → 18.6 kB | 35,000 → 10,149 | 71.0% |
| JSON tool envelope | `json-fields` | 85.9 kB → 19.2 kB | 26,012 → 5,670 | 78.2% |
| Test run, 2,000 cases | `head-tail` | 99.8 kB → 18.6 kB | 29,998 → 5,529 | 81.6% |
| 3,000-frame traceback | `traceback` | 359.4 kB → 10.3 kB | 126,015 → 3,680 | 97.1% |
| 432 kB patch | `diff-kept` | 432.1 kB → 432.1 kB | 120,039 → 120,039 | 0.0% |
| 900-row SQL result | `head-tail` | 46.6 kB → 18.5 kB | 18,906 → 7,543 | 60.1% |
| 8,000-line build log | `head-tail` | 334.9 kB → 18.6 kB | 127,005 → 6,905 | 94.6% |
| 8,000-item JSON array | `json-kept` | 206.9 kB → 206.9 kB | 103,002 → 103,002 | 0.0% |
| **Total** | | **1.63 MB → 742.7 kB (54.3%)** | **585,977 → 262,517 (55.2%)** | |

The patch row saves nothing on purpose: a compressor that shrinks diffs can hide a code change. The JSON-array row saves nothing because keeping the array an array leaves nothing safe to remove. Five buried clues needed one `sutra_restore` call (about 700 B each; the single-line JSON envelope cost 8.9 kB); three were already visible.

What the numbers are — and are not:

- Payload bytes and `tiktoken o200k_base` tokenizer counts on the payload text.
- Not provider input tokens, not prompt-cache effects, not an invoice, and not a live-agent task-success score. No model was called for any number here.
- 8/8 proves the clue is retained and retrievable; it does not prove an agent will ask for it.

Method, fixtures, per-phase timings, memory, recovery costs and reproduction: [docs/benchmark.md](https://github.com/g0GobliN/sutra/blob/main/docs/benchmark.md). The frozen live-model A/B protocol is in [docs/ab-benchmark.md](https://github.com/g0GobliN/sutra/blob/main/docs/ab-benchmark.md); it has not been run.

## Safety, recovery and trust

Every rewrite leaves a marker in place of the omitted bytes:

```text
… [sutra: partial · 133,522 bytes elided (large output) · id=su_3e72c41e… ·
   original 151,920 B retained 168h ·
   retrieve: sutra_restore(id="su_3e72c41e…", mode="bytes", start=12259, end=145781)] …
```

The marker answers four questions: is this complete, what is missing and why, is the original still available, and exactly how to get it. `sutra_restore` supports exact byte ranges, line ranges, literal search and `full` when it fits, with paging that reproduces the original slice byte for byte.

Safety properties you can rely on:

- **Diffs and patches are byte-identical.** Always.
- **JSON keeps its shape.** Documents are decoded and re-encoded, never byte-sliced; an array stays an array.
- **Tracebacks keep their error end.** Only the middle is elided.
- **An id means the bytes exist.** If storage is off, full or the write failed, the marker says "not retained" and quotes no id.
- **Fail-open.** Any unexpected shape, parse error or internal exception leaves the original result untouched.
- **Bounded storage.** Defaults: 32 MiB per entry, 128 MiB total, 2,000 entries, 7-day TTL, 8 KiB recovery response (64 KiB hard cap). The store is local and **not encrypted**; it is protected by filesystem permissions, and tool output can contain secrets. Set `SUTRA_STORE=0` to opt out.

Read the full threat model in [SECURITY.md](https://github.com/g0GobliN/sutra/blob/main/SECURITY.md), the guarantees in [docs/contract.md](https://github.com/g0GobliN/sutra/blob/main/docs/contract.md), and the recovery protocol in [docs/recovery.md](https://github.com/g0GobliN/sutra/blob/main/docs/recovery.md).

## Install

The distribution is `sutra-agent`; the `sutra` name on PyPI belongs to an unrelated package.

```bash
python3 -m venv ~/.sutra-venv
~/.sutra-venv/bin/pip install sutra-agent
~/.sutra-venv/bin/sutra --version        # sutra 0.4.2

~/.sutra-venv/bin/sutra detect           # read-only: what is installed
~/.sutra-venv/bin/sutra install --dry-run
~/.sutra-venv/bin/sutra install          # configure every detected client
~/.sutra-venv/bin/sutra doctor           # prove the installed paths
```

To work from a checkout instead (contributors or unreleased changes):

```bash
git clone https://github.com/g0GobliN/sutra
~/.sutra-venv/bin/pip install ./sutra
```

`sutra install` auto-detects Hermes, Claude Code, OpenCode and Codex. Each change is backed up first and can be removed with `sutra uninstall`; `--dry-run` prints the change and writes nothing. The command ends with a per-client summary (`configured`, `active`, `requires /hooks trust`, `skipped` or `failed`) and the next verification step. Restart the host after installing a plugin or hook. Codex also requires a one-time trust review: run `/hooks` and trust the SUTRA `PostToolUse` entry before it can replace results.

```bash
~/.sutra-venv/bin/sutra uninstall        # remove only SUTRA-owned entries
```

If you only want one client, use `--client hermes|claude|opencode|codex`. The installer's behaviour and evidence are recorded in [docs/automatic-install.md](https://github.com/g0GobliN/sutra/blob/main/docs/automatic-install.md).

## Supported integrations

| Host | Mode | Verified against | What to expect |
|---|---|---|---|
| **Hermes Agent** | Automatic — native `tool_execution` middleware | 0.22.5 | Plugin validated and enabled by the installer; restart the gateway; enable the `sutra` toolset for recovery |
| **Claude Code** | Automatic — `PostToolUse` hook + MCP recovery | 2.1.268 | Result replaced before the model reads it; start a new session; hook and MCP must share a store |
| **OpenCode** | Automatic — generated `tool.execute.after` plugin + MCP recovery | 1.19.5 | Oversized string results replaced in place; restart OpenCode |
| **Codex CLI** | **Automatic — `PostToolUse` feedback hook + MCP recovery** | 0.166.0 | Compressed result replaces the model-visible tool output; trust the hook once via `/hooks`; start a new session. Codex truncates very large output before the hook, so only the supplied text is compressible and recoverable |
| **Any MCP client** | Explicit | protocol `2024-11-05` | Register `python -m sutra.mcp`; MCP registration is not interception |
| **Any subprocess-capable agent** | Explicit CLI | Python 3.11–3.13 | `sutra compress` / `sutra restore` as JSON; one process per call |
| **Cursor, Windsurf/Cascade** | No adapter | — | No generic built-in-tool result hook; MCP registration only, by hand |

The authoritative, per-client matrix — mechanisms, install paths, evidence and known limits — is [docs/integrations.md](https://github.com/g0GobliN/sutra/blob/main/docs/integrations.md). The host-API audit is [docs/universal-integration.md](https://github.com/g0GobliN/sutra/blob/main/docs/universal-integration.md).

## Configuration

Common settings, read on every call:

| Variable | Default | Meaning |
|---|---|---|
| `SUTRA_ENABLED` | `1` | `0` disables compression and retention |
| `SUTRA_MAX_BYTES` | `24576` | results at or below this are untouched |
| `SUTRA_HEAD_BYTES` / `SUTRA_TAIL_BYTES` | `12288` / `6144` | bytes kept from the start and end |
| `SUTRA_STORE` | `1` | `0` keeps compression but stops retaining originals |
| `SUTRA_STORE_DIR` | `$HERMES_HOME/cache/sutra-store` | where originals live |
| `SUTRA_STORE_TTL_HOURS` | `168` | retention window |
| `SUTRA_RECOVERY_MAX_BYTES` | `8192` | default response cap (64 KiB hard max) |

The complete table, including store quotas, stats-log rotation and retrieval bounds, is in [docs/configuration.md](https://github.com/g0GobliN/sutra/blob/main/docs/configuration.md).

## Limitations

- **Automatic replacement depends on the host.** Codex's `PostToolUse` hook must be trusted once in `/hooks`, and Codex truncates very large tool output before the hook sees it; the retained original is that truncated text. Cursor and Windsurf have no supported generic result-replacement hook and get explicit tools only.
- **JSON re-encoding normalises formatting.** Indentation and whitespace inside a re-encoded document are not preserved, and duplicate keys collapse to the last value. The original bytes remain recoverable, so nothing is lost, but the visible copy is not byte-identical.
- **Large JSON arrays with no large string fields are not compressed at all** — the price of keeping their shape.
- **Retrieval is bounded.** One entry ≤32 MiB, one response ≤8 KiB by default, at most 20 literal matches; a single line longer than the response cap is returned clipped.
- **The store is not encrypted.** It is local, permission-protected and expiring, but any process running as you can read it.
- **The recovery tool may need enabling.** In Hermes, `sutra_restore` is gated by the plugin toolset; without it the marker degrades to "re-run narrower".
- **No live-model A/B has been run.** No task-success, invoice or provider-token claim is made.
- **Windows coverage is functional, not live-host.** CI runs the full core/CLI suite, installs the wheel, exercises detection and all four adapter dry runs, and verifies OpenCode install/reinstall/uninstall. The live Claude, Codex and Hermes host probes remain Linux-only; Windows host binaries were not authenticated or exercised.

## Development and contributing

```bash
python3 -m venv .venv && .venv/bin/pip install pytest ruff
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pytest spikes -q
.venv/bin/ruff check .
```

Read [CONTRIBUTING.md](https://github.com/g0GobliN/sutra/blob/main/CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](https://github.com/g0GobliN/sutra/blob/main/CODE_OF_CONDUCT.md) before opening a pull request: one rule per change, a failing test first, and proof that the elided bytes are still recoverable. Bug reports are most useful with the smallest fixture, the result shape, the byte size and the `SUTRA_*` settings in force. Security issues go through a private advisory, not a public issue — see [SECURITY.md](https://github.com/g0GobliN/sutra/blob/main/SECURITY.md).

Browse the documentation index at [docs/README.md](https://github.com/g0GobliN/sutra/blob/main/docs/README.md). Releases are prepared and published only by authorized maintainers, following [docs/release.md](https://github.com/g0GobliN/sutra/blob/main/docs/release.md).

## Licence

MIT.
