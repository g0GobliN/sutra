# Integrations and the shared tool contract

SUTRA is one deterministic core — `engine.py` (compression), `store.py`
(retention), `recovery.py` (retrieval protocol), and `core.py` (the
host-agnostic seam) — with small adapters per environment. Adapters never
re-implement compression, eligibility, size, safety, retention or recovery;
they translate a host's tool result into `core.process_result` and write the
returned value back into the field that host uses.

## Automatic or manual?

**Automatic** means the host replaces the tool result before the model reads
it. **Manual** means the agent (or the user) must invoke `sutra_compress`
explicitly; the original tool result has already reached the model.

| Surface | Automatic? | Why |
|---|---|---|
| Hermes Agent | **Yes** | Native `tool_execution` middleware; the agent never calls a compression tool. |
| Claude Code | **Yes, with the `PostToolUse` hook installed** | The hook returns `hookSpecificOutput.updatedToolOutput`, which Claude Code uses in place of the tool result. Hook-level tested; a live authenticated Claude Code session has not been run here. |
| MCP clients (Claude Code without the hook, Cursor, Codex, …) | **No** | MCP has no server-side hook for another server's result. A server sees only requests addressed to itself. The agent must call `sutra_compress`, or the host must provide a client-side hook/proxy. |
| CLI | **No** | It is a subprocess filter. A shell pipeline compresses a stream, not an agent's tool result. |
| OpenCode | **Spike only** | The verified `tool.execute.after` hook mutates the result in place, but the production adapter, bridge lifecycle and packaging are deliberately unbuilt. |
| Cursor | **No adapter** | Cursor's documented `postToolUse` hook can replace MCP tool output (`updated_mcp_tool_output`) and nothing else. No adapter is written or claimed. |

The short rule: **MCP tool registration is not interception.** The MCP server
is useful for explicit compression and for `sutra_restore`; it does not make
other MCP tools automatic by itself.

## The shared tool contract

Every adapter calls the same seam. The tool-exposing surfaces (CLI and MCP
server) expose exactly two operations plus introspection:

**compress** — submit a complete tool output, receive the compressed copy.

    input : {tool?: string, output: string}
    output: {changed: bool,          // false = returned untouched
             output: string,         // the text the agent should see
             marker: bool,           // a [sutra: …] marker is present
             id: string | null,      // recovery reference; null = not retained
             original_bytes: int, compressed_bytes: int}

**restore** — the `sutra_restore` protocol verbatim (arguments, statuses
`ok | not_found | expired | too_large | unavailable | error`, bounded
responses, `content_note` framing). See [recovery.md](recovery.md).

Semantics shared by every adapter: deterministic output (no timestamps or
random values); an explicit recovery status is a *result*, never a silent
failure; failures carry a reason and never masquerade as success; storage
follows `HERMES_HOME` / `SUTRA_STORE_DIR`, so isolation is per profile.
Equivalence is enforced by test: `tests/test_adapter_contract.py` proves the
engine, CLI, and MCP surfaces produce identical compression (per-call ids
normalized) and each restores the exact original. The evidence tools
(`sutra_restore`, `sutra_compress`) are skipped by the seam — by exact name or
by an `mcp__<server>__` suffix — and an already-compressed result is below the
threshold, so a second automatic pass over either is a no-op.

## Compatibility matrix

Status labels mean exactly what they say; nothing is listed as supported on
theoretical grounds.

| Agent / client | Mechanism | Automatic? | Status | Install | Verification | Limitations |
|---|---|---|---|---|---|---|
| Hermes Agent | native plugin: `tool_execution` middleware + `sutra_restore` tool | **Yes** | **Tested with the actual host** | `git clone … ~/.hermes/plugins/sutra && hermes plugins enable sutra && hermes tools` | CI runs `hermes plugins validate` against the pinned revision; scripted-provider gateway E2E (`scripts/e2e/run.sh`, 8/8 runs); live-model A/B staged pending quota | Verified against Hermes `v0.22.2+3030.g4d04303` only; Tool Search may defer the recovery tool (documented in troubleshooting) |
| Claude Code | native `PostToolUse` hook (`updatedToolOutput`) + the MCP server for `sutra_restore` | **Yes, hook installed** | **Hook-level tested** — the real hook process is invoked with a `PostToolUse` JSON payload; no live Claude Code session (the local CLI is unauthenticated) | `sutra-hook` (installed) or the checkout's `scripts/sutra-claude-code-hook.py`; add the hook to `.claude/settings.json` (snippet below) and register the MCP server for recovery; point both at the same `SUTRA_STORE_DIR` | `tests/test_claude_code_hook.py`: replacement, marker, recovery through MCP, pass-through, second pass, fail-open, and the installed `sutra-hook` command running the same script | Needs Claude Code's documented all-tools `updatedToolOutput` (introduced in 2.1.121); a live model session has not been exercised here |
| Any MCP client (Claude Code without the hook, Cursor, Codex-style agents, OpenCode, …) | MCP stdio server (shipped as `sutra-mcp` / `python -m sutra.mcp`, same code as `scripts/sutra-mcp-server.py`), JSON-RPC 2.0, protocol `2024-11-05`, no SDK dependency | **No — manual `sutra_compress`** | **Tested with an actual client** — OpenCode 1.19.0's MCP client discovered the tools, compressed a 48,449-byte output through `sutra_compress`, and recovered the buried clue via `sutra_restore` (`status: ok`; verdict in `spikes/opencode-poc/evidence/verdict-mcp-client.json`); plus the official `@modelcontextprotocol/inspector` reference client: tools/list with schemas, end-to-end compress (marker, id, isError false) and restore (status ok, clue recovered) | `sutra install` (edits the client config in place, backup beside it, `--dry-run` writes nothing), or add the entry by hand: `python3 /path/to/sutra/scripts/sutra-mcp-server.py` | protocol tests: initialize/tools list/compress/restore round-trip, isError semantics, parse and method errors, EOF shutdown; OpenCode 1.19.0 end-to-end; Claude Code 2.1.268 health check, quota-free | MCP cannot intercept another server's result; verified end to end with OpenCode's client; Claude Code 2.1.268 reached `✔ Connected` (connection only); Cursor / Codex not exercised; tools/call only; no resources/prompts; one server process per profile |
| Any subprocess-capable agent | CLI (`scripts/sutra-cli.py`), JSON on stdout, exit codes 0/1/2 | **No — manual** | **Implemented and locally tested** | call directly; no installation beyond the checkout | `tests/test_cli_adapter.py`: round-trip, statuses-as-results, usage errors, size cap, config | one process per invocation (no daemon); stdin capped at 64 MiB |
| OpenCode | native plugin hook (`tool.execute.after` + tool registry) | **Spike only** | **Implemented as a verified spike** — real-host proof on OpenCode 1.19.0 (output replacement + recovery + fail-open); production adapter deliberately not built | see [the spike](../spikes/opencode-poc/README.md) | two independent real-host investigations + hardened PoC, full test suite | bridge auth token lifecycle is spike-grade; use the MCP server or the Claude Code hook for a supported path today |
| Cursor | native `postToolUse` hook (`updated_mcp_tool_output`) | **No adapter yet** | **Documented host capability, not implemented** | — | — | Cursor can replace only MCP tool output; non-MCP tool output cannot be replaced. Nothing is claimed here. |

## Adapter usage

### Claude Code (automatic)

Add the hook to project or user settings, with an absolute path. An install
provides `sutra-hook`; a checkout runs the script it wraps:

```json
{
  "hooks": {
    "PostToolUse": [
      {"hooks": [{"type": "command",
                  "command": "/home/you/.sutra-venv/bin/sutra-hook"}]}
    ]
  }
}
```

Checkout alternative: `python3 /path/to/sutra/scripts/sutra-claude-code-hook.py`.

Add the MCP server for recovery:

```json
{"mcpServers": {"sutra": {"command": "/home/you/.sutra-venv/bin/python",
                          "args": ["-m", "sutra.mcp"]}}}
```

Checkout alternative: `python3 /path/to/sutra/scripts/sutra-mcp-server.py`.

The hook and the MCP server must use the same store. Set `SUTRA_STORE_DIR` in
both environments (or set `HERMES_HOME` for both); otherwise the marker's id
resolves against a different directory and `sutra_restore` answers
`not_found`. With the defaults, both use `$HERMES_HOME/cache/sutra-store`.

The hook is fail-open: a parse error, a missing `tool_response`, a non-text
result, or any internal failure prints `{}` and leaves the original tool result
untouched. When storage is unavailable it still compresses, but the marker says
"original not retained" and quotes no id.

### CLI

```bash
# compress a tool output (stdin or --file), one JSON object out
python3 scripts/sutra-cli.py compress --tool bash < output.txt
# retrieve retained evidence; the recovery protocol, verbatim
python3 scripts/sutra-cli.py restore --id su_… --mode search --query "error" --context 2
# resolved configuration (no secrets)
python3 scripts/sutra-cli.py config
```

Exit codes: `0` a result was produced (including explicit recovery statuses);
`1` internal failure or input above 64 MiB (JSON error on stdout); `2` usage
error. Uninstall: delete the scripts; retained originals live under
`HERMES_HOME` (see [troubleshooting](troubleshooting.md#removing-it)).

### MCP server

The package ships the same server as `sutra-mcp` and `python -m sutra.mcp`;
the checkout path remains available. `sutra install` registers it for OpenCode
by editing that client's config textually (backup written first);
`sutra install --dry-run` prints the change and writes nothing, and
`sutra uninstall` removes exactly the entry it added. For any other client,
add:

```json
{"mcpServers": {"sutra": {"command": "python3",
                          "args": ["/path/to/sutra/scripts/sutra-mcp-server.py"]}}}
```

The server speaks newline-delimited JSON-RPC 2.0 on stdio, exposes
`sutra_compress` and `sutra_restore`, and exits cleanly on stdin EOF. Point
`HERMES_HOME` at a dedicated directory to isolate its store from a Hermes
profile. Uninstall: remove the server entry from the client config.

## What is verified where

- **Real host (Hermes)**: scripted-provider E2E runs the real gateway, real
  terminal tool, real middleware, real store and recovery through the host's
  Tool Search bridge — `scripts/e2e/run.sh`; recorded 8/8 lifetime and 2/2 on
  2026-10-24. This harness is manual (it needs a Hermes installation), not CI.
- **Real host (OpenCode spike)**: OpenCode 1.19.0 with a scripted provider;
  the model-facing request log is the evidence ([runtime proof](../spikes/opencode-poc/evidence/runtime-proof.md)).
  Prior pass; not rerun here.
- **Hook level (Claude Code)**: the real hook process is invoked exactly as
  Claude Code invokes a shell hook, with a `PostToolUse` JSON event on stdin.
  `tests/test_claude_code_hook.py` proves replacement, marker, recovery through
  the MCP server, pass-through and fail-open. This is **not** a live Claude Code
  session.
- **Unit + protocol tests (CI, every push)**: engine rules, store, recovery
  protocol, Hermes middleware contract, the `core` seam, and adapter contracts
  (CLI/MCP over real subprocesses and pipes; the hook over real stdin/stdout).
- **Live-model experiments (pending quota)**: the frozen A/B protocol
  ([ab-benchmark.md](ab-benchmark.md)); no task-success or billing claim
  exists yet.

These layers are not interchangeable and are never reported as each other.
