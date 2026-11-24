# Universal automatic integration audit

SUTRA has one deterministic core and five host adapters. This page is the
current audit of what each host actually supports, what `sutra install`
configures, and how each claim is verified. It deliberately separates a host
capability from a SUTRA integration: a binary on `PATH` does not prove that
output replacement is installed or working.

## Current matrix

The authoritative per-client matrix — mechanism, automatic or explicit, install, verification and limits — lives in [integrations.md](integrations.md). This page records the host-API evidence behind those labels and does not maintain a second copy.

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

Codex 0.166.0 `PostToolUse` can replace the model-visible tool result through
its feedback contract: a command hook returns `continue: false` with
`stopReason`, Codex records the run as `Stopped` and continues from that text.
The wire schema still parses and rejects `updatedMCPToolOutput` as unsupported;
the documented replacement is the feedback message, not a field rewrite. SUTRA
uses it for Bash/exec output, runs the result through the shared seam, and keeps
MCP for `sutra_restore`. User hooks are skipped until Codex's one-time `/hooks`
trust review, so the installer prints that instruction instead of editing the
trust ledger. Codex truncates very large output before the hook, which bounds
what can be compressed and restored. Official references:

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

`sutra install` auto-detects Hermes, Claude Code, OpenCode, OpenClaw, and
Codex. A detected host is never reported as automatically integrated merely
because its config file exists:

- Hermes is automatic only after the plugin validates and is enabled;
- Claude Code is automatic only after the hook and MCP connection pass the
  probe;
- OpenCode is automatic only when the generated plugin is present; the doctor
  additionally exercises the generated contract;
- Codex is automatic only after the `PostToolUse` hook is installed and Codex
  has recorded the one-time `/hooks` trust review; before that it is MCP-only.

## Restart and uninstall behavior

| Host | Needed after install | Uninstall |
|---|---|---|
| Hermes | restart the gateway so middleware reloads | disable plugin, remove generated plugin directory |
| Claude Code | start a new session | remove SUTRA-owned hook, remove the user-scope MCP entry |
| OpenCode | restart OpenCode | remove generated plugin, remove SUTRA MCP entry if present |
| Codex | start a new Codex session; run `/hooks` once to trust the SUTRA hook | remove the SUTRA hook entry and `codex mcp remove sutra` |

All adapters fail open. A missing interpreter, malformed host payload, failed
subprocess, or timeout leaves the host's original result untouched. The
installers refuse foreign files and preserve backups; none of them rewrites
another client's compression logic or duplicates the engine.

## Verification commands

```bash
pytest tests -q
ruff check .
sutra detect
sutra doctor
```

The Hermes gateway harness is manual because it needs a live Hermes install:

```bash
scripts/e2e/run.sh
```

The Claude Code, OpenCode and Codex host proofs are the scripted-provider runs
recorded in [automatic-install.md](automatic-install.md); they are not live
model or billing measurements.

A host loop can also be reproduced on a machine with no provider credential.
`scripts/ab/mock_provider.py` serves an OpenAI-shaped stream that asks for one
tool call and then answers, and records what the host actually sent:

```bash
python3 scripts/ab/mock_provider.py --port 8899 --log /tmp/mock.jsonl --bytes 36000
OPENCODE_CONFIG=/tmp/oc-mock/config.json XDG_DATA_HOME=/tmp/oc-mock/data \
  opencode run "Run the shell command and report the output." -m mock/mock-1
```

Point the host's provider `baseURL` at `http://127.0.0.1:8899/v1`. The default
log is `<home>/.sutra/mock-provider/requests.jsonl`, created owner-only; the
server is unauthenticated and loopback-only, so run it on a machine you trust
and stop it when the loop is over. OpenCode
needs its own `XDG_DATA_HOME` because a database written by an older release
answers `Database is not empty and has no session table`; the host also streams
unconditionally, so a provider that answers plain JSON makes it retry until it
is killed. The model is fake and no quality claim follows, but the tool
execution, the adapter hook and the request the host finally submits are real —
and the log's per-message byte sizes and `[sutra:` flags are what prove the
adapter rewrote the result the host sent back.

## Live evidence

The sections above are the API contract. This is what has actually been run,
with the numbers, so a claim can be traced to a session:

| surface | how it was run | result |
|---|---|---|
| Hermes tool stage | the user's gateway compiling `main`, `SUTRA_PROMPT=1` | 1,893 rewrites over 127,784,080 bytes of tool output, 97,986,207 removed (76.7 %); `bash` 84.4 % of 96.8 MB, `terminal` 89.1 % of 11.3 MB, `read_file` 29.5 % of 18.7 MB |
| Hermes request stage | the same gateway, `balanced` | 19 rewrites across 10 sessions, 1,613,059 → 1,303,779 bytes (19.2 %) |
| Hermes whole-session pairs | `scripts/ab/run.py --task trace-deep`, protocol v2, gateway restarted per arm | control 156,119 and 112,529 prompt tokens; treatment 168,476 and 171,791; treatment with a 25 % savings floor 108,777 and 137,378 |
| DeepSeek Harness tool stage | `sutra install --client dsh`, then real sessions in this harness | 22 rewrites over 245.2 KB of tool output, 125.5 KB removed (51.2 %); `bash` 21 of them at 51.7 %, `read` 1 at 2.7 %; `sutra doctor --client dsh` reads the seam's log and reports `stage=saved rewrites=22 saved=125.6 KB` |
| Claude Code hook | `claude_code.doctor()` through the installed hook | compressed and restored 73,893 → 18,637 bytes; the host reported the MCP server connected |
| Claude Code live turn | `claude -p` on a 24,376-byte traceback | **not run**: the host answered `Failed to authenticate: OAuth session expired` |
| OpenCode, Codex, OpenClaw | installed with SUTRA (`opencode 1.19.6`, `codex-cli 0.171.0`, `OpenClaw 2026.11.6`; OpenCode and OpenClaw plugins written, Codex hook written and awaiting `/hooks` trust) | **no live turn**: Codex answers `Failed to refresh token … refresh token was already used`, the stored OpenCode Go tokens answer 403, and no provider key is set on this machine |
| OpenCode host loop, scripted provider | `scripts/ab/mock_provider.py` (OpenAI-shaped SSE asking for one `bash` call), `opencode run` with `XDG_DATA_HOME` isolated to a fresh database | the host executed the command (36,001 bytes) and its next request carried the tool message at **18,671 bytes with `[sutra: partial … id=su_…]`**; `sutra_restore` was in the advertised tool list and the original was on disk under `~/.sutra/cache/sutra-store/` |
| `sutra proxy` | unit tests only | **no live pair** |

Two limits follow. The request stage's saving is a byte measurement: on the
paired task it billed more prompt tokens than the control until a savings floor
refused the small rewrites, and two seeds per arm cannot separate that from
trajectory noise. And only Hermes and DeepSeek Harness have been driven end to
end by a model, so the Claude Code, Codex and OpenClaw rows describe an
installed and protocol-checked path, not an observed session. OpenCode's loop
was driven end to end with the scripted provider above: that proves the plugin,
the tool execution and the bytes the host submitted, and it says nothing about
how a real model behaves.

### Reproducing the numbers

The tool-stage rows come from the hosts' own logs, and each log is one command away.
For the harness:

```console
$ SUTRA_STATS_FILE=$DSH_HOME/sutra/stats.jsonl sutra stats
sutra stats: /home/goblin01/.dsh/sutra/stats.jsonl
  rewrites: 22
  bytes: 251,060 before → 122,481 after (128,579 saved, 51.2%)
  partial: 3  fail open: 0  unreadable lines: 0
  by rule:
    head-tail: 3 rewrite(s), 126,343 bytes saved
    search: 19 rewrite(s), 2,236 bytes saved
  by tool:
    bash: 21 rewrite(s), 128,513 bytes saved
    read: 1 rewrite(s), 66 bytes saved
```

Two limits are visible in that output. Most rewrites are the small `search` rule, which
trims indentation for about a hundred bytes each: frequent, harmless, and not where the
saving lives. The bytes come from the three `head-tail` rewrites. The Hermes rows are read
the same way from `SUTRA_STATS_FILE` at that host's default path.

## Deliberately not claimed

- Codex output restoration beyond the text Codex itself supplies to the hook.
- Cursor or Windsurf automatic replacement — no supported generic result hook.
- Compression of MCP tool results through OpenCode's after-hook in 1.19.5.
- A quality advantage outside the planted-frame fixture, or an invoice-level
  saving: the paired trial above ran two seeds per arm.
- Windows live-host verification. Windows has functional full-suite CI coverage,
  but the live Claude/Codex/Hermes host probes have not been run on Windows and
  remain Linux-only.
