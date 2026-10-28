# Integrations and the shared tool contract

SUTRA is one deterministic core — `engine.py` (compression), `store.py`
(retention), `recovery.py` (retrieval protocol) — with small adapters per
environment. Adapters never re-implement compression or recovery; they
transport the same contract.

## The shared tool contract

Every adapter exposes exactly two operations plus introspection:

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
normalized) and each restores the exact original.

## Compatibility matrix

Status labels mean exactly what they say; nothing is listed as supported on
theoretical grounds.

| Agent / client | Mechanism | Status | Install | Verification | Limitations |
|---|---|---|---|---|---|
| Hermes Agent | native plugin: `tool_execution` middleware + `sutra_restore` tool | **Tested with the actual host** | `git clone … ~/.hermes/plugins/sutra && hermes plugins enable sutra && hermes tools` | CI runs `hermes plugins validate` against the pinned revision; scripted-provider gateway E2E (`scripts/e2e/run.sh`, 8/8 runs); live-model A/B staged pending quota | Verified against Hermes `v0.22.2+3030.g4d04303` only; Tool Search may defer the recovery tool (documented in troubleshooting) |
| Any MCP client (Claude Code, Cursor, Codex-style agents, OpenCode, …) | MCP stdio server (shipped as `sutra-mcp` / `python -m sutra.mcp`, same code as `scripts/sutra-mcp-server.py`), JSON-RPC 2.0, protocol `2024-11-05`, no SDK dependency | **Tested with an actual client** — OpenCode 1.19.0's MCP client discovered the tools, compressed a 48,449-byte output through `sutra_compress`, and recovered the buried clue via `sutra_restore` (`status: ok`; verdict in `spikes/opencode-poc/evidence/verdict-mcp-client.json`); plus the official `@modelcontextprotocol/inspector` reference client: tools/list with schemas, end-to-end compress (marker, id, isError false) and restore (status ok, clue recovered) | `sutra install` (edits the client config in place, backup beside it, `--dry-run` writes nothing), or add the entry by hand: `python3 /path/to/sutra/scripts/sutra-mcp-server.py` | protocol tests: initialize/tools list/compress/restore round-trip, isError semantics, parse and method errors, EOF shutdown; real-client run scripted-provider, quota-free | verified against OpenCode's client only; Claude Code / Cursor / Codex clients not yet exercised; tools/call only; no resources/prompts; one server process per profile |
| Any subprocess-capable agent | CLI (`scripts/sutra-cli.py`), JSON on stdout, exit codes 0/1/2 | **Implemented and locally tested** | call directly; no installation beyond the checkout | `tests/test_cli_adapter.py`: round-trip, statuses-as-results, usage errors, size cap, config | one process per invocation (no daemon); stdin capped at 64 MiB |
| OpenCode | native plugin hook (`tool.execute.after` + tool registry) | **Implemented as a verified spike** — real-host proof on OpenCode 1.19.0 (output replacement + recovery + fail-open); production adapter deliberately not built | see [the spike](../spikes/opencode-poc/README.md) | two independent real-host investigations + hardened PoC, 182-test suite | bridge auth token lifecycle is spike-grade; use the MCP server for a supported path today |
| Claude Code | MCP (the server above), project-scoped `.mcp.json` | **Configuration-tested** — registered and recognized by Claude Code 2.1.268 (`claude mcp get sutra`: correct command, args, env); connection and tool invocation **blocked: the local CLI is not authenticated** (`claude auth status` → `authMethod: none`), so no model-backed session can run here | `.mcp.json` in the project root with the server command; approve when prompted | `claude mcp get sutra` (local, quota-free) | end-to-end run needs an authenticated Claude Code session |
| Cursor (native), Codex-style agents | MCP (the server above) | **Planned verification** — clients not installed in this environment | same MCP config shape, per client docs | pending: a run in each client's environment | none claimed until verified |

## Adapter usage

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

- **Unit + integration tests (CI, every push)**: engine rules, store,
  recovery protocol, Hermes middleware contract, adapter contracts
  (CLI/MCP over real subprocesses and pipes).
- **Scripted-provider E2E (manual, quota-free)**: real Hermes gateway runs
  the plugin end to end — compression, marker, deferred-tool discovery,
  recovery — `scripts/e2e/run.sh`.
- **Live-model experiments (pending quota)**: the frozen A/B protocol
  ([ab-benchmark.md](ab-benchmark.md)); no task-success or billing claim
  exists yet.

These layers are not interchangeable and are never reported as each other.
