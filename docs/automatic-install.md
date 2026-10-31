# Automatic installation and the client audit

The target is one command, then normal agent work:

```bash
sutra detect           # read-only: what is installed, and what it can support
sutra install          # detect and configure every safely supported client
sutra doctor             # prove every client path, including host limits
```

`sutra install` never asks the user to edit JSON, register an MCP server by
hand, or launch a second process. `sutra uninstall` removes only what SUTRA
added, and every edit is backed up first.

## What is shipped today

| Client | Automatic interception | Installer | Verified how |
|---|---|---|---|
| **Claude Code** | **Yes** — native `PostToolUse` hook replacing `updatedToolOutput` | **Yes** — user settings + user-scope MCP entry, then a doctor probe | Live Claude Code 2.1.268 with a scripted provider; real Bash tool execution; marker seen by the model; retained original restored through MCP |
| **Hermes Agent** | **Yes** — native `tool_execution` middleware | **Yes** — copies the plugin, runs `hermes plugins validate`, then `hermes plugins enable sutra` | Real gateway E2E on 0.22.5 (2026-10-31): 38,925 B → 22,597 B, marker present, clue hidden, deferred `tool_search` found `sutra_restore`, and the bridge restored `ok: true`; optional live test covers plugin validate/enable |
| **OpenCode** | **Yes** — generated plugin replaces `tool.execute.after` output | **Yes** — plugin + MCP recovery entry when a config exists | Live OpenCode 1.19.5 with a scripted provider; real Bash tool; 48,436 B → 18,649 B model-facing; clue restored through the plugin's `sutra_restore` |
| **Codex CLI** | **No** — `PostToolUse` has no transparent output replacement | **MCP only** — `codex mcp add sutra -- <python> -m sutra.mcp` | Codex 0.166.0 real `mcp add/list/remove` round trip in an isolated `CODEX_HOME`; official hook schema confirms replacement is unsupported |
| **Cursor** | **No** — `postToolUse` replaces MCP tool output only | Not auto-configured | Official hooks and MCP documentation read; no host run |
| **Windsurf / Cascade** | **No** — no documented output-replacement hook | Not auto-configured | Official MCP documentation read; no host run |

The rest of this page records the evidence behind those labels. A client is not
listed as automatic because its config file resembles another client's.

## Read-only detection

```bash
sutra detect
sutra detect --json
sutra detect --client opencode
```

Detection checks for an executable on `PATH` (with a bounded `--version` probe)
or a known configuration file. An empty directory with a familiar name is not
evidence, and a binary alone never upgrades a client's capability tier.

| Client | Tier when installed | Mechanism |
|---|---|---|
| Hermes Agent | `automatic` | native `tool_execution` middleware |
| Claude Code | `automatic` | `PostToolUse` replacement + MCP recovery |
| OpenCode | `automatic` | generated `tool.execute.after` plugin + `sutra_restore` |
| Cursor | `mcp-only` | MCP server; built-in output cannot be replaced |
| Codex CLI | `mcp-only` | MCP server; hooks do not replace tool output |
| Windsurf / Cascade | `mcp-only` | MCP server; no documented replacement hook |

`detect` writes nothing: no backups, no config edits, no plugin installs. The
JSON envelope is `{"read_only": true, "clients": [...]}`; each row includes
`installed`, `tier`, `automatic`, `mechanism`, `executable`, `version`,
`version_error`, `config`, and the concrete `evidence` strings used. A version
probe that times out leaves the executable as evidence and reports the timeout.

## Hermes Agent

`sutra install --client hermes` copies the native plugin from the installed
Python package into `$HERMES_HOME/plugins/sutra` (or `~/.hermes/plugins/sutra`),
writes a generated marker, runs `hermes plugins validate <dir>`, and then
`hermes plugins enable sutra`. An existing SUTRA plugin is backed up by
renaming it; a foreign plugin or symlink is refused. The installer restores the
old directory if validation or enablement fails. Restart the gateway so the
middleware is loaded. `sutra uninstall --client hermes` disables and removes only
the generated directory. The plugin's real-gateway E2E harness remains
`scripts/e2e/run.sh`.

## Claude Code

### What the installer changes

`claude_code.py` writes the user settings file and registers the MCP server with
Claude Code's own CLI:

- user settings: `$CLAUDE_CONFIG_DIR/settings.json`, or `~/.claude/settings.json`
  when the variable is unset;
- one `PostToolUse` group containing
  `{type: "command", command: "<python>", args: ["-m", "sutra.hook"], timeout: 60}`;
- MCP: `claude mcp add --scope user --transport stdio sutra -- <python> -m sutra.mcp`.

Existing hooks, other settings, and other MCP servers are preserved. The
settings write is atomic, uses a timestamped `.bak-*` backup, and a second
install is a no-op. A different existing `sutra` MCP entry is refused rather
than overwritten; `sutra uninstall --client claude` is the documented repair.

`claude mcp get sutra` must report `Status: ✔ Connected`. The installer does not
claim success from a written file alone: after both edits it runs the same probe
as `sutra doctor`.

### The host limit that matters

Claude Code's `bashOutputMaxChars` defaults to **30,000 characters** ("how many
characters of a successful Bash or PowerShell command's output Claude receives
inline"). Larger output is persisted by Claude Code; the hook receives a
30,000-character preview plus:

```json
{
  "persistedOutputPath": "/…/<session>/tool-results/<id>.txt",
  "persistedOutputSize": 70888
}
```

Compressing that preview would store a truncated "original" and hand the model
an id that cannot restore what the tool printed. The hook therefore:

1. accepts `persistedOutputPath` only for `Bash`/`PowerShell`;
2. requires an absolute `.txt` file directly under a `tool-results` directory,
   inside the session tree named by `transcript_path`;
3. refuses symlinks (`lstat` + `O_NOFOLLOW`), requires the declared size to match
   the file, caps the read at 64 MiB, and decodes UTF-8 strictly;
4. if any check fails, returns `{}` and leaves the tool result untouched — never
   a compressed preview.

When the checks pass, the full file replaces `stdout`, the persisted metadata is
removed, and the normal SUTRA seam compresses it. The model receives one inline
compressed result with a recovery id for the complete original.

This is a real host constraint, not a SUTRA preference. Claude Code versions that
persist output differently, or a tool that does not set these fields, fall back
to the old behaviour: only the text the hook actually received is eligible.

### Live-host evidence (2026-10-29, reproduced 2026-10-30)

Environment: Claude Code **2.1.268** on Linux, an isolated `HOME` and
`CLAUDE_CONFIG_DIR`, and a local Anthropic-compatible endpoint that scripts one
Bash tool call. No provider was contacted and no live model was called.

1. `sutra install --client claude` wrote the hook and MCP entry; Claude Code
   reported `✔ Connected`.
2. The scripted turn ran a Bash command that emitted **70,888 bytes** and buried
   a `CLUE` token in the middle.
3. Claude Code persisted the full result and gave the hook a 29,999-character
   preview plus the path. The hook read the file, compressed the full text, and
   returned **18,645 bytes** (18,641 characters) in the model-facing result.
4. The next model request contained the marker and the id, with **no**
   `<persisted-output>` pointer and no clue (the clue was in the omitted middle).
5. `sutra_restore(id=…, mode="search", query=…)` returned `status: ok` and the
   original clue through the MCP server.
6. `sutra doctor` passed with the same synthetic persisted-output shape.

This is host-level proof: a real Claude Code process executed a real tool and
the model-facing request was inspected. It is not a live-model task-success or
billing measurement.

### Limitations

- The hook uses `python -m sutra.hook`; the interpreter that installed SUTRA must
  still exist. `sutra uninstall` uses the same command to find its entry.
- The `args` form of a hook handler is verified on 2.1.268, the version tested
  here. Older releases may use a different handler schema; the installer does
  not edit project-scope settings, and no claim is made for unexercised versions.
- Windows is not covered by CI or by this probe. The path and `O_NOFOLLOW` code
  use POSIX facilities, with `O_NOFOLLOW` omitted where the platform lacks it.
- MCP must be connected for `sutra_restore` to work. If the MCP registration
  fails or the server cannot connect, install returns a non-zero status and
  rolls back both the MCP entry it added and the hook; it does not leave a
  marker with no recovery path as a "success".
- The doctor probe uses a temporary store and synthetic text. It proves the
  installed command and the recovery seam; it does not prove a model will choose
  to restore.

## The other clients

### OpenCode

`sutra install --client opencode` writes one generated plugin to
`~/.config/opencode/plugins/sutra.js` (or `$XDG_CONFIG_HOME/opencode/plugins/`).
OpenCode loads that directory automatically. The plugin runs the installed
Python core as a short-lived subprocess for each string tool result above the
threshold and mutates `output.output` in place. It does not run a daemon, open a
port, or hold a token. The interpreter and a dedicated `~/.sutra` store home are
baked into the generated file. If OpenCode's config already exists, the
installer also adds the `sutra` MCP recovery entry; if it does not, the automatic
plugin is still installed and the MCP entry is skipped (the plugin has its own
`sutra_restore` tool).

Restart OpenCode after installation. The plugin is global for that config tree
and loads on the next start. `SUTRA_MAX_BYTES` overrides the 24,576-byte
threshold; `SUTRA_OPENCODE_HOME` overrides the store home. A second install is a
no-op; a changed interpreter rewrites the plugin with a timestamped backup, and
an existing file without SUTRA's generated marker is refused, not overwritten.

Real-host evidence (2026-10-31, OpenCode 1.19.5): an isolated config loaded the
generated plugin; a real Bash tool produced 48,436 bytes with a buried clue at
line 750; the model-facing request contained 18,649 bytes with the marker and no
clue; the model called `sutra_restore` with a malformed id (`error`), an unknown
id (`not_found`), and the marker's id (`ok`), which returned the exact buried
lines. The session completed normally. The adapter's Node tests use the real
Python CLI and cover install, idempotence, backup, foreign-file refusal,
uninstall, and recovery; OpenCode itself is not run in CI.

Limitations: tested on OpenCode 1.19.5 only; a plugin process failure is
fail-open (the original result reaches the model), so no compression is
guaranteed if the interpreter disappears; the 2.5 s subprocess timeout bounds
each oversized result; Windows was not exercised.

### Codex CLI

Verified from the official Codex documentation and the 0.166.0 binary:

- MCP config is `~/.codex/config.toml` (or project `.codex/config.toml`) with a
  `[mcp_servers.sutra]` table; `codex mcp add sutra -- <python> -m sutra.mcp`
  is the official CLI path. `sutra install --client codex` uses that command,
  verifies the entry with `codex mcp list --json`, and refuses a different
  existing `sutra` entry. `sutra uninstall --client codex` uses `codex mcp
  remove sutra`.
- `PostToolUse` runs for Bash, `apply_patch`, MCP, and other local tools, but it
  has **no field for replacing the tool result**. `decision: "block"` and
  `continue: false` replace the result with hook *feedback* and continue from
  that message; `updatedMCPToolOutput` is parsed but rejected as unsupported.
  Using those to inject a compressed result would turn every large output into
  a hook intervention, not a transparent rewrite, so no automatic adapter is
  claimed. MCP registration only provides explicit `sutra_compress` and
  `sutra_restore` calls.
- `sutra doctor --client codex` reports `status=limited` and `automatic=no`
  when the MCP entry is valid. It does not fail merely because the host has no
  supported automatic path; a missing entry is still a real failure.

### Cursor

- Global MCP config is `~/.cursor/mcp.json`; project config is
  `.cursor/mcp.json`; the `mcpServers` entry uses `type: "stdio"`, `command`,
  `args`, and optional `env`.
- `postToolUse` fires for all tools, but the only output-replacement field is
  `updated_mcp_tool_output`, and it applies to MCP tools only. Built-in Shell,
  Read, Write, and other tool output cannot be replaced. That is not enough for
  SUTRA's generic interception, so Cursor is MCP-only and not auto-configured.

### Windsurf / Cascade

The legacy Cascade agent reads `mcp_config.json` under
`~/.config/devin/mcp_config.json` on macOS/Linux (`%APPDATA%\devin\mcp_config.json`
on Windows), with a top-level `mcpServers` object. The current documentation is
published under the Devin Desktop name, which is a moving target; the file has no
documented post-tool output hook. It remains a manual MCP client and is not
auto-configured.

## Why MCP registration is not called automatic

MCP is a tool protocol, not an interception layer. Registering `sutra` gives the
model two tools; it does not make another server's results pass through SUTRA.
Only a host hook that replaces the result before the model reads it is automatic.
That is why Hermes, Claude Code, and OpenCode are the clients this installer
configures for automatic use today, and why Codex says "MCP only" even though
its MCP entry is now installed by one command.

## Diagnostic

```bash
sutra doctor                         # all four clients, including not-installed rows
sutra doctor --client opencode       # focus on one integration
sutra doctor --client codex          # MCP state and the explicit host limit
sutra doctor --json                  # machine-readable list of reports
```

`doctor` does not accept a configuration file as proof. For Claude Code it
checks the hook is present in the exact settings file, asks Claude Code whether
the MCP server is connected, runs the configured hook command on a synthetic
persisted-output event, then restores the marker's id through the recovery seam.

For OpenCode it checks the generated plugin at the global path, parses the
interpreter baked into it, confirms that interpreter can import `sutra.cli`,
and runs the same CLI path the plugin uses against a temporary store. That probe
must replace an oversized synthetic result with a marker plus id, restore the
buried clue through the shared core, and leave a diff byte-identical. When Node
is available it additionally loads the generated plugin contract through
`tool.execute.after`, compresses and restores through the plugin's own
`sutra_restore`, and repeats the protected-diff check. A missing Node is
reported as `skip`, not as a pass and not as a failure.

Every check is reported as `pass`, `fail`, or `skip` with a reason. Codex is
reported as `limited` with `automatic=no` when its MCP entry is valid; absent
clients are reported as `not-installed` instead of being silently skipped.
Exit status is non-zero only for a real failure; a skipped optional hook check
yields `partial` with exit `0`, and a host limitation does not masquerade as a
successful automatic integration. Temporary stores are used, so doctor never
reads or writes the user's recovery entries. `sutra status` remains the short,
configuration-oriented view; `sutra doctor` is the executed proof.
