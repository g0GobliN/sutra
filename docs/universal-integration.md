# Universal automatic integration audit

SUTRA has one deterministic core and four host adapters. This page is the
current audit of what each host actually supports, what `sutra install`
configures, and how each claim is verified. It deliberately separates a host
capability from a SUTRA integration: a binary on `PATH` does not prove that
output replacement is installed or working.

## Current matrix

| Host | Automatic interception | `sutra install` | `sutra doctor` | Evidence |
|---|---|---|---|---|
| **Hermes Agent** | **Yes** — native `tool_execution` middleware | **Yes** — copies the plugin into `$HERMES_HOME/plugins/sutra`, runs `hermes plugins validate`, then `hermes plugins enable sutra` | `plugin`, `enabled`, and host validation; the separate `scripts/e2e/run.sh` exercises a real gateway, terminal tool, middleware, retention, Tool Search bridge, and recovery | Real gateway E2E on Hermes 0.22.5 (2026-10-31): 38,925 B → 22,597 B, marker present, clue hidden, Tool Search found the tool, bridge restore returned `ok: true`; the optional live test covers the plugin validate/enable round trip |
| **Claude Code** | **Yes** — native `PostToolUse` hook returning `hookSpecificOutput.updatedToolOutput`, plus MCP recovery | **Yes** — user settings hook and user-scope MCP entry; verifies MCP connection and runs the hook probe | Hook presence, MCP connection, a synthetic persisted-output event, and recovery through the shared seam | Live Claude Code 2.1.268 scripted-provider proof in [automatic-install.md](automatic-install.md); hook-level tests run against the current CLI |
| **OpenCode** | **Yes (native tools)** — generated `tool.execute.after` plugin mutating `output.output`; native `sutra_restore` tool | **Yes** — generated plugin plus the MCP recovery entry when a config exists | Generated plugin, embedded interpreter/import, real CLI compress/restore, protected diff, and the Node hook contract | Live OpenCode 1.19.5 scripted-provider proof; Node tests invoke the real Python CLI |
| **Codex CLI** | **No — host limitation** | **MCP only** — `codex mcp add sutra -- <python> -m sutra.mcp`; no automatic output replacement is claimed | MCP entry/command/enabled state; reports `status=limited`, `automatic=no` | Codex 0.166.0 real `mcp add/list/remove` round trip against an isolated `CODEX_HOME` |

The important distinction: **MCP registration is not interception**. Codex
officially supports MCP servers, so SUTRA can install the recovery/compress
tools there, but Codex does not expose a documented post-tool result rewrite.
The installer says so instead of pretending the user has automatic compression.

## Host API evidence

### Hermes Agent

Hermes plugins are directories containing `plugin.yaml` and a `register(ctx)`
function. The `tool_execution` middleware receives `next_call` and may replace
the returned tool result; the native `sutra_restore` tool is registered in the
same plugin. Hermes requires the plugin name in `plugins.enabled`, so SUTRA uses
`hermes plugins enable` rather than editing the host's YAML. Official
references:

- <https://hermes-agent.nousresearch.com/docs/developer-guide/plugins>
- <https://hermes-agent.nousresearch.com/docs/developer-guide/middleware>

The installer deliberately refuses a symlink or a foreign plugin directory,
backs up an existing SUTRA plugin by renaming it, validates the new copy, and
restores the old copy if validation or enablement fails. Uninstall runs
`hermes plugins disable sutra` and removes only a directory carrying SUTRA's
generated marker. A running gateway must be restarted to reload middleware.

### Claude Code

Claude Code's `PostToolUse` hook may replace the tool result through
`hookSpecificOutput.updatedToolOutput`. For built-in tools the replacement must
match the tool's native output shape; SUTRA preserves the original
`tool_response` object instead of returning a bare string. Large Bash results
are read from the host's `persistedOutputPath` only after the path, size, file
type, symlink, and session-tree checks pass. Official references:

- <https://code.claude.com/docs/en/hooks>
- <https://code.claude.com/docs/en/agent-sdk/hooks>

### OpenCode

OpenCode 1.19.5 calls `tool.execute.after` with a mutable `output` object and
uses that same object after the hook returns. SUTRA mutates only
`output.output`. MCP tool calls are not covered by this replacement path in
1.19.5 because the hook receives the raw MCP result before text assembly; SUTRA
does not claim MCP-tool interception. The generated plugin skips non-string
outputs, evidence tools, and fail-open errors. Official references:

- <https://opencode.ai/docs/plugins/>
- <https://github.com/sst/opencode/blob/v1.19.5/packages/plugin/src/index.ts>

### Codex CLI

Codex 0.166.0 `PostToolUse` supports blocking/feedback and
`additionalContext`, but not transparent output replacement. Its wire schema
parses `updatedMCPToolOutput` and then marks the hook failed because the field
is unsupported; the original result passes through. There is no documented
post-tool transform or MCP response filter. SUTRA therefore installs the
standard MCP server as the best legitimate alternative, and `sutra doctor`
reports a host limit rather than a broken automatic integration. Official
references:

- <https://learn.chatgpt.com/docs/hooks>
- <https://github.com/openai/codex/blob/main/codex-rs/hooks/src/schema.rs>

## One-command flow

```bash
sutra detect                 # read-only: what is installed and capable
sutra install                # configure every detected supported host
sutra doctor                 # prove the installed paths; show host limits
sutra status                 # current per-host state
sutra uninstall              # remove only SUTRA-owned entries
```

`sutra install` auto-detects Hermes, Claude Code, OpenCode, and Codex. A
detected host is never reported as automatically integrated merely because
its config file exists:

- Hermes is automatic only after the plugin validates and is enabled;
- Claude Code is automatic only after the hook and MCP connection pass the
  probe;
- OpenCode is automatic only when the generated plugin is present; the doctor
  additionally exercises the generated contract;
- Codex is MCP-only even after a successful `codex mcp add`.

## Restart and uninstall behavior

| Host | Needed after install | Uninstall |
|---|---|---|
| Hermes | restart the gateway so middleware reloads | disable plugin, remove generated plugin directory |
| Claude Code | start a new session | remove SUTRA-owned hook, remove the user-scope MCP entry |
| OpenCode | restart OpenCode | remove generated plugin, remove SUTRA MCP entry if present |
| Codex | no restart claimed; new session after MCP config change | `codex mcp remove sutra` |

All adapters fail open. A missing interpreter, malformed host payload, failed
subprocess, or timeout leaves the host's original result untouched. The
installers refuse foreign files and preserve backups; none of them rewrites
another client's compression logic or duplicates the engine.

## Verification commands

```bash
pytest tests -q
pytest spikes -q
ruff check .
sutra detect
sutra doctor
```

The Hermes gateway harness is manual because it needs a live Hermes install:

```bash
scripts/e2e/run.sh
```

The Claude Code and OpenCode host proofs are the scripted-provider runs
recorded in [automatic-install.md](automatic-install.md); they are not live
model or billing measurements.

## Deliberately not claimed

- Codex automatic output replacement — the host does not support it.
- Cursor or Windsurf automatic replacement — no supported generic result hook.
- Compression of MCP tool results through OpenCode's after-hook in 1.19.5.
- A live model task-success score or invoice saving.
- Windows verification. The current CI coverage is Linux and macOS.
