# Automatic installation and the client audit

The authoritative per-client matrix and status definitions are in [integrations.md](integrations.md). This page records what `sutra install` changes, how it rolls back, and the evidence for each host.

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
| **Codex CLI** | **Yes** — `PostToolUse` feedback replacement (`continue: false`) | **Yes** — writes `$CODEX_HOME/hooks.json` and registers MCP with `codex mcp add` | Codex 0.166.0 with a scripted Responses provider and a real Bash call: 10,110 B → 4,837 B model-visible, marker present, omitted middle line restored; Codex logged `hook: PostToolUse Stopped`; trust is a one-time `/hooks` review |
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
| Codex CLI | `automatic` when the SUTRA hook is installed, otherwise `mcp-only` | `PostToolUse` feedback replacement + MCP recovery |
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

The installer does not claim success from a written file alone: after both edits
it asks Claude Code for the MCP status, then runs the same probe as
`sutra doctor`. Diagnosis is read-only: `sutra status` and `sutra doctor` read
the user-scope entry from `.claude.json` and never run `claude mcp get` (which
creates the file when absent). The doctor starts the configured MCP command and
restores the probe id through it, so a configured but unusable server is a
failure, not a green label.

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

Codex 0.166.0 supports lifecycle hooks. The installer writes a user-level
`PostToolUse` handler to `$CODEX_HOME/hooks.json` (or the `CODEX_HOME` it finds)
and keeps the MCP entry for recovery:

- the handler runs `<python> <site-packages>/sutra/scripts/sutra-codex-hook.py`
  for `Bash|exec_command`, with a 30-second timeout;
- the hook extracts the string `tool_response`, runs the shared SUTRA seam, and
  returns `{"continue": false, "stopReason": "<compressed text + marker>"}`;
- Codex replaces the model-visible tool result with that text, records the hook
  run as `Stopped` (not `Blocked`), and continues the turn. The original result
  is not re-sent;
- `codex mcp add sutra -- <python> -m sutra.mcp` supplies `sutra_restore`. The
  hook and MCP server share `SUTRA_STORE_DIR` / `HERMES_HOME`.

Codex rejects `updatedMCPToolOutput` as unsupported, so the feedback contract is
the only supported replacement path; it is not a transparent field rewrite, and
the host records an intervention. **Trust is a one-time Codex decision**: user
hooks are skipped until reviewed. The installer prints `/hooks`; it does not
edit Codex's trust ledger, which would bypass that review. `sutra doctor`
reports `limited` until the trusted hash is recorded and `ok` afterwards.

Large-output limit: Codex truncates very large tool output before the hook runs
(the observed shape begins with `Warning: truncated output` and is about 10 KB).
The adapter therefore uses 8,192-byte eligibility with 3,072/1,536-byte head and
tail budgets. The marker restores exactly the text Codex supplied; output Codex
already discarded cannot be recovered.

Live evidence (2026-11-01): Codex CLI 0.166.0, isolated `CODEX_HOME`, a local
scripted Responses provider and a real Bash `seq 1 20000` call. The second model
request carried **4,837 bytes** with the marker and id instead of the 10,110-byte
host-truncated result (no middle `10000` line); Codex logged `hook: PostToolUse
Stopped`; `sutra restore --mode lines --start 1000 --end 1002` returned
`status: ok` and the omitted line. No provider or model was contacted.

Installation edits are idempotent, preserve other hooks and settings, write a
timestamped backup, refuse malformed JSON, and support `--dry-run`. Uninstall
removes only the SUTRA handler and MCP entry, and preserves unrelated hooks.

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
Hermes, Claude Code, OpenCode and Codex each have such a path; the installer
configures all four, and Codex's hook additionally needs the one-time `/hooks`
trust review before Codex will run it.

## Diagnostic

```bash
sutra doctor                         # all four clients, including not-installed rows
sutra doctor --client opencode       # focus on one integration
sutra doctor --client codex          # hook probe, trust state and MCP
sutra doctor --json                  # machine-readable list of reports
```

`doctor` does not accept a configuration file as proof. For Claude Code it
checks the hook is present in the exact settings file, reads the MCP entry from
`.claude.json` without invoking the Claude CLI, runs the configured hook command
on a synthetic persisted-output event, then starts the configured MCP server and
restores the marker's id through it. Status and doctor create no files and make
no backups in the user's config.

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
