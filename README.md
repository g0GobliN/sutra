<p align="center">
  <img src="https://raw.githubusercontent.com/g0GobliN/sutra/main/public/logo/sutraThumb.png" alt="SUTRA" width="520">
</p>

<h1 align="center">SUTRA</h1>

<p align="center"><strong>Less output. More signal.</strong></p>

<p align="center">
  <a href="https://pypi.org/project/sutra-agent/"><img src="https://img.shields.io/pypi/v/sutra-agent.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/sutra-agent/"><img src="https://img.shields.io/pypi/pyversions/sutra-agent.svg" alt="Python versions"></a>
  <a href="https://github.com/g0GobliN/sutra/actions/workflows/ci.yml"><img src="https://github.com/g0GobliN/sutra/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/g0GobliN/sutra" alt="MIT license"></a>
</p>

SUTRA is a deterministic optimization layer for AI coding agents. It reduces oversized tool results, reuses exact repeated output, and applies bounded text-optimization strategies — while preserving a path back to the original content.

It does not ask another model to summarize a tool result. The shared core uses deterministic rules, validates every optimization candidate against protected content, and keeps omitted output recoverable when local storage is enabled.

**One core. Multiple agent integrations. Recoverable output. No compression-model calls.**

> **Measured baseline:** across eight deterministic tool-output fixtures, SUTRA reduced payload bytes by **54.3%** and `o200k_base` tokenizer counts by **55.2%**. All **8/8** planted clues remained visible or recoverable, and patches were left unchanged. These are offline fixture measurements — not provider-billed tokens, dollar savings, or live-agent task-success results. [Benchmark method](docs/benchmark.md).

## Quick start

Requires Python 3.11 or newer.

```bash
python -m pip install sutra-agent

# Choose the optimization effort (balanced is the default)
sutra mode balanced

# Find supported clients; this command is read-only
sutra detect

# Preview the integration changes, then install
sutra install --dry-run
sutra install

# Verify the installed integrations
sutra doctor
```

Restart the relevant agent after installation. Codex CLI also requires a one-time review and trust action in `/hooks`. The installer backs up files it changes; `sutra uninstall` removes SUTRA-owned entries.

### Choose one mode

For normal use, the mode is the only optimization choice you need. SUTRA selects its deterministic plans, pass limits, combinations and reuse policy from that mode. Advanced environment variables remain available for compatibility and debugging, but are not required.

| Mode | Behaviour |
|---|---|
| `off` | Disables the optimization plans; base compression and recovery remain active |
| `balanced` | Default. Runs the essential formatting and wording plans with safe exact reuse |
| `aggressive` | Explores more passes and combinations; allows reuse that requires revalidation |
| `beast` | Uses the largest bounded search effort, including triple combinations |

```bash
sutra mode                 # show the resolved mode and its source
sutra mode balanced        # set balanced
sutra mode aggressive      # explore more combinations
sutra mode beast           # use the largest bounded search
sutra mode off             # disable optimization plans
sutra mode off --clear     # clear the saved mode selection
```

The mode is stored in `~/.sutra/mode` unless `SUTRA_MODE_FILE` is set. An explicit `SUTRA_MODE` environment variable takes precedence. Existing `SUTRA_SEARCH` settings remain supported for migration. See [Optimization modes](docs/modes.md).

A mode changes search effort, not the preservation contract. A candidate is applied only when it is smaller and passes the protected-span validator. A larger mode does not guarantee a larger saving or a better result.

## Why SUTRA exists

Tool output can dominate an agent's context. A test command may return thousands of lines when the agent needs one failure. The full result can then be carried through later turns even when most of it is no longer useful.

SUTRA reduces the visible result and leaves a marker that explains what was omitted and how to retrieve it.

## How it works

1. **The host runs its tool normally.** SUTRA does not re-run or change the command.
2. **An adapter passes the result to the shared core.** The core checks size, payload shape, tool type and protection rules.
3. **Deterministic transformations are evaluated.** Oversized text can be reduced; active modes can try bounded formatting and wording plans.
4. **Candidates are validated.** Protected spans must survive, and a candidate must be smaller before it can replace the input.
5. **The original is retained locally when storage is enabled.** Retention has size, count and lifetime limits.
6. **The model receives the smaller result and a recovery marker.** The marker identifies the omitted content and provides the relevant `sutra_restore` request.
7. **Exact repeats can become references.** Reuse is allowed only when the earlier stored result is verified and the repeated bytes match exactly.

Unexpected payloads, parsing errors and internal failures are handled fail-open: the host receives the original result unchanged.

## What SUTRA protects

- **Patches and diffs:** never rewritten by the optimization layer.
- **Structured JSON:** never byte-sliced; rewrites must remain valid JSON and preserve the document's shape. Formatting may be normalised when a JSON document is re-encoded.
- **Protected spans:** paths, URLs, commands, identifiers, numbers, negations, error terms, security terms and code fences are checked before an optimization is accepted.
- **Recovery:** an id is emitted only when the original has actually been retained. If storage is disabled or retention fails, the original is returned unchanged rather than a marker that cannot be restored.
- **Failure behaviour:** unexpected adapter or core errors leave the host's original result untouched.

The local store is **not encrypted**. It is permission-protected and bounded, but processes running as the same user can read it. Tool output may contain secrets; set `SUTRA_STORE=0` if you do not want originals retained.

See [the contract](docs/contract.md), [recovery protocol](docs/recovery.md) and [security model](SECURITY.md).

## Integrations

SUTRA uses a shared core with small host-specific adapters. Automatic replacement is possible only when a host exposes a supported result-replacement mechanism.

| Client | Integration | Behaviour |
|---|---|---|
| Hermes Agent | Native `tool_execution` and `llm_request` middleware | Automatic result processing, plus request-stage trimming of older recoverable results before submission; enable the `sutra` toolset for recovery |
| Claude Code | `PostToolUse` hook + MCP recovery | Automatic replacement when the hook is installed; hook and MCP server must share a store |
| OpenCode | `tool.execute.after` plugin + MCP recovery | Automatic replacement of supported string results |
| OpenClaw | Generated plugin + MCP recovery | Automatic result processing |
| Codex CLI | Trusted `PostToolUse` feedback hook + MCP recovery | Automatic after the one-time `/hooks` trust review |
| DeepSeek Harness | Host bundle on `tools/post-execute` + MCP recovery | Automatic once `sutra install --client dsh` has attached the bundle and the harness has restarted; the installer also composes it inside the profile's agent preset, because the harness scopes tool events to the calling agent |
| Other MCP clients | MCP server | Explicit `sutra_compress` and `sutra_restore` calls; MCP registration alone does not intercept other tools |
| Cursor / Windsurf | Manual MCP configuration only | No SUTRA automatic built-in-tool result replacement adapter |

The exact mechanisms, tested versions, installation details and host-specific limitations are maintained in the [integration matrix](docs/integrations.md). To install one client only, use `sutra install --client hermes|claude|opencode|openclaw|codex|dsh`.

## Evidence and benchmarks

The baseline benchmark uses eight deterministic fixtures shaped like common tool results: command output, a JSON envelope, test output, a traceback, a patch, SQL rows, a build log and a JSON array.

| Measurement | Result |
|---|---:|
| Payload bytes | **54.3% reduction** |
| `o200k_base` tokenizer count | **55.2% reduction** |
| Planted clues visible or recovered | **8/8** |
| Patch bytes changed | **0** |
| JSON-array bytes changed | **0** |

The unchanged patch and JSON-array cases are intentional protection results, not failed compression. The measurements are offline and deterministic. They do not establish provider billing savings, live-model behaviour or task success.

The mode benchmark is separate: it compares `off`, `balanced`, `aggressive` and `beast` across committed and generated workloads, recording byte changes, recovery status and the orchestrator's decision trace. Its measurements do not turn byte savings into provider-token or cost claims.

- [Benchmark method, results and reproduction](docs/benchmark.md)
- [Optimization model and formulas](docs/optimization-model.md)
- [Mode benchmark harness](bench/modes.py)
- [Benchmark source and charts](bench/)
- [Benchmark workflow and run history](https://github.com/g0GobliN/sutra/actions/workflows/benchmark.yml)

The current results page is published at **https://g0goblin.github.io/sutra/**. CI also uploads per-Python benchmark reports as workflow artifacts, so a run can be reproduced without trusting the page.

## Configuration

Most users only need `sutra mode`. The full configuration reference documents every setting and its default.

A few useful controls:

| Setting | Default | Purpose |
|---|---|---|
| `SUTRA_MODE` | `balanced` | Selects `off`, `balanced`, `aggressive` or `beast` |
| `SUTRA_ENABLED` | `1` | Disables compression and retention when set to `0` |
| `SUTRA_MAX_BYTES` | `24576` | Results at or below this size are left untouched |
| `SUTRA_STORE` | `1` | Set to `0` to stop retaining originals |
| `SUTRA_STORE_TTL_HOURS` | `168` | Original retention lifetime |
| `SUTRA_POLICY` | `off` | Separate, optional Hermes session-guidance setting |

`SUTRA_MODE` and `SUTRA_POLICY` are different controls: the first selects SUTRA's optimization effort; the second controls optional session guidance for Hermes. The policy guidance is bounded, does not override host or repository instructions, and must never tell an agent to skip required work.

See [all configuration options](docs/configuration.md) and [agent policy details](docs/agent-policy.md).

## Documentation

| Start here | What it covers |
|---|---|
| [Integrations](docs/integrations.md) | Supported clients, mechanisms, evidence and limits |
| [Optimization modes](docs/modes.md) | Mode selection, effort bounds and migration |
| [Configuration](docs/configuration.md) | Environment variables, defaults and storage bounds |
| [Recovery](docs/recovery.md) | Markers, restore modes, statuses and paging |
| [Contract](docs/contract.md) | Behavioural guarantees and their tests |
| [Benchmark method](docs/benchmark.md) | Fixtures, measurements, limitations and reproduction |
| [Optimization model](docs/optimization-model.md) | Scoring formulas and implementation map |
| [Context intelligence](docs/context-intelligence.md) | Explicit budgeted context selection |
| [Troubleshooting](docs/troubleshooting.md) | Integration and recovery problems |
| [Security](SECURITY.md) | Threat model and responsible disclosure |
| [Contributing](CONTRIBUTING.md) | Development workflow and pull-request expectations |

The complete documentation index is in [docs/README.md](docs/README.md).

## Development

```bash
git clone https://github.com/g0GobliN/sutra.git
cd sutra
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
```

For benchmark reproduction, install the benchmark extras listed in [bench/requirements.txt](bench/requirements.txt) and follow [docs/benchmark.md](docs/benchmark.md).

## Limitations

- **No live-model A/B result is published.** The live-model protocols are documented, but provider-token reduction, task-success impact and net billing savings remain unmeasured.
- **Host capabilities differ.** Automatic replacement depends on the host's documented hooks. MCP registration by itself is an explicit tool interface, not interception.
- **Recovery is bounded.** The store has entry, total-size, count and TTL limits; each restore response is capped.
- **The store is not encrypted.** Use filesystem permissions appropriately or disable retention.
- **JSON formatting can change.** A re-encoded JSON document can have normalised whitespace; the original remains recoverable when stored.
- **Request-stage trimming is implemented for one host.** The context selector is a library and CLI. Hermes Agent also exposes a request stage that receives the assembled conversation before submission, and SUTRA uses it to shorten older tool results that it can keep restorable — both results it produced earlier and results the host produced, which are retained first. That path is covered by unit tests and by the host's own plugin admission check; it has not been measured on a live gateway, and hosts whose hooks receive one tool result at a time (Claude Code, Codex CLI, OpenCode, OpenClaw) are not connected to it.
- **Larger modes are not guaranteed to save more.** They explore more bounded candidates; only validated, smaller candidates are accepted.

## License

SUTRA is released under the [MIT License](LICENSE).
