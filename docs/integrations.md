# Integrations and the shared tool contract

SUTRA is one deterministic core — `engine.py` (compression), `store.py`
(retention), `recovery.py` (retrieval protocol), and `core.py` (the
host-agnostic seam) — with small adapters per environment. Adapters never
re-implement compression, eligibility, size, safety, retention or recovery;
they translate a host's tool result into `core.process_result` and write the
returned value back into the field that host uses.

Automatic installation, the per-client audit, and the live Claude Code and
OpenCode evidence are in [automatic-install.md](automatic-install.md).

## Automatic or manual?

**Automatic** means the host replaces the tool result before the model reads
it. **Manual** means the agent (or the user) must invoke `sutra_compress`
explicitly; the original tool result has already reached the model.

| Surface | Automatic? | Why |
|---|---|---|
| Hermes Agent | **Yes** | Native `tool_execution` middleware; the agent never calls a compression tool. |
| Claude Code | **Yes, with the `PostToolUse` hook installed** | The hook returns `hookSpecificOutput.updatedToolOutput`, which Claude Code uses in place of the tool result. Hook-level tested and live-host tested with a scripted provider; no live model was called. |
| MCP clients (Claude Code without the hook, Cursor, Codex, …) | **No** | MCP has no server-side hook for another server's result. A server sees only requests addressed to itself. The agent must call `sutra_compress`, or the host must provide a client-side hook/proxy. |
| Codex CLI | **Yes — with the trusted `PostToolUse` hook** | Codex 0.166.0's `PostToolUse` feedback (`continue: false`) replaces the model-visible tool result; `sutra install --client codex` writes that hook and the MCP recovery entry. Codex requires a one-time `/hooks` trust review. |
| CLI | **No** | It is a subprocess filter. A shell pipeline compresses a stream, not an agent's tool result. |
| OpenCode | **Yes, with the generated plugin** | The `experimental.chat.messages.transform` hook shortens the large tool outputs already inside the conversation before the provider call, and the `tool.execute.after` plugin runs the Python core in a short-lived subprocess and mutates `output.output` in place; the model receives the compressed copy and the same plugin registers `sutra_restore`. |
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
             id: string | null,      // recovery reference; null = nothing retained
             original_bytes: int, compressed_bytes: int}

**restore** — the `sutra_restore` protocol verbatim (arguments, statuses
`ok | not_found | expired | too_large | unavailable | error`, bounded
responses, `content_note` framing). See [recovery.md](recovery.md).

A host whose hook runs in another language hands over a whole content array at
once with `sutra process-result`:

    input : {"tool_name": "bash", "session_id": "…", "texts": ["…", null]}
    output: {"texts": ["…", null], "changed": true}

Every string goes through the same seam, a non-string entry passes through
unchanged so a host can forward its content blocks in order, and `changed` is
true only when a text really shrank. The command reads stdin, writes one JSON
object to stdout and exits 1 when the payload carries no `texts` list. The
store and the stats file follow the same environment variables as every other
surface, and the evidence tools bypass exactly as they do elsewhere.

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
| Hermes Agent | native plugin: `tool_execution` and `llm_request` middleware + `sutra_restore` tool | **Yes** | **Tested with the actual host** | `sutra install --client hermes` copies, validates, and enables the native plugin (manual checkout fallback remains) | CI runs `hermes plugins validate` against the pinned revision; scripted-provider gateway E2E (`scripts/e2e/run.sh`, 8/8 runs); live-model A/B protocols defined (v1 default, v2 expanded), pending quota | Verified against Hermes `v0.22.2+3030.g4d04303` only; Tool Search may defer the recovery tool (documented in troubleshooting) |
| Claude Code | native `PostToolUse` hook (`updatedToolOutput`) + the MCP server for `sutra_restore` | **Yes, installed automatically** | **Live host tested** — Claude Code 2.1.268, isolated config, real Bash tool, scripted local provider: 70,888 B compressed to 18,645 B (18,641 characters) in the model-facing request and restored through MCP; `sutra doctor` passes | `sutra install --client claude` (or `sutra install` with auto-detection) writes the user settings and MCP entry, backs up first, and verifies with `sutra doctor`; manual snippet below remains for a checkout | `tests/test_claude_code_hook.py`: replacement, persisted-file reading and its refusals, marker, recovery through MCP, pass-through, second pass, fail-open, and the installed `sutra-hook` command; `tests/test_claude_installer.py`: idempotent install, backup, refusal, uninstall, live CLI, doctor | Needs Claude Code's documented all-tools `updatedToolOutput` (verified on 2.1.268); `bashOutputMaxChars` caps the inline preview at 30,000 by default, so the hook reads `persistedOutputPath` or leaves the result untouched; a `Read` result is trimmed inside its `file.content` slot; Windows untested |
| Any MCP client (Claude Code without the hook, Cursor, Codex-style agents, OpenCode, …) | MCP stdio server (shipped as `sutra-mcp` / `python -m sutra.mcp`, same code as `scripts/sutra-mcp-server.py`), JSON-RPC 2.0, protocol `2024-11-05`, no SDK dependency | **No — manual `sutra_compress`** | **Tested with actual clients** — OpenCode 1.19.0's MCP client discovered the tools, compressed a 48,449-byte output through `sutra_compress`, and recovered the buried clue via `sutra_restore` (`status: ok`; the original spike evidence was removed from the public checkout); the official `@modelcontextprotocol/inspector` reference client completed tools/list, compress and restore; Claude Code 2.1.268 reports `✔ Connected` for the same command | `sutra install` (OpenCode config, backup beside it, `--dry-run` writes nothing), or add the entry by hand: `python3 /path/to/sutra/scripts/sutra-mcp-server.py`. Codex (`~/.codex/config.toml`) is registered by `sutra install --client codex` but remains MCP-only; Cursor (`~/.cursor/mcp.json`) and Windsurf/Cascade (`mcp_config.json`) are manual; see [automatic-install.md](automatic-install.md) | protocol tests: initialize/tools list/compress/restore round-trip, isError semantics, parse and method errors, EOF shutdown; OpenCode 1.19.0 end-to-end; Claude Code 2.1.268 health check, quota-free | MCP cannot intercept another server's result; the installer does not auto-write the three manual client configs; Cursor / Codex / Windsurf hosts were not exercised; tools/call only; no resources/prompts; one server process per profile |
| Codex CLI | `PostToolUse` command hook (`continue: false` feedback) + official stdio MCP server through `codex mcp add` | **Yes, after a one-time `/hooks` trust review** | **Live host tested** — Codex 0.166.0 with a scripted Responses provider and a real Bash call: 10,110 B → 4,837 B in the model-facing request, marker + id, omitted middle line restored; Codex logged `hook: PostToolUse Stopped` | `sutra install --client codex` writes `$CODEX_HOME/hooks.json` (preserving other hooks), backs up, verifies the MCP entry with `codex mcp list --json`, and prints the `/hooks` instruction; `sutra uninstall --client codex` removes only SUTRA-owned entries | `tests/test_codex_hook.py`: replacement, recovery through the real MCP server, protected/non-string pass-through, second-pass no-op, malformed/fail-open, storage failure; `tests/test_codex_adapter.py`: preservation, backup, idempotence, dry-run, malformed JSON, spaces in paths, uninstall, doctor trust states | Codex truncates very large tool output before the hook, so only the host-supplied ~10 KB text is compressible and recoverable; trust is a host security decision and cannot be pre-recorded by the installer; `updatedMCPToolOutput` is rejected; hosted tools bypass hooks |
| Any subprocess-capable agent | CLI (`scripts/sutra-cli.py`), JSON on stdout, exit codes 0/1/2 | **No — manual** | **Implemented and locally tested** | call directly; no installation beyond the checkout | `tests/test_cli_adapter.py`: round-trip, statuses-as-results, usage errors, size cap, config | one process per invocation (no daemon); stdin capped at 64 MiB |
| OpenCode | generated native plugin (`tool.execute.after` + tool registry) | **Yes, installed automatically** | **Live host tested** — OpenCode 1.19.5, isolated config, real Bash tool, scripted local provider: 48,436 B compressed to 18,649 B in the model-facing request (marker + id), then `sutra_restore` returned the buried clue with statuses `error`, `not_found`, `ok` | `sutra install --client opencode` writes `~/.config/opencode/plugins/sutra.js` and, when an OpenCode config exists, the MCP recovery entry; restart OpenCode | `sutra doctor`'s OpenCode probe (real CLI round-trip and a `tool.execute.after` contract check when Node is available) | Tested on OpenCode 1.19.5 only; one short-lived Python process per oversized result (2.5 s timeout, fail-open); the configuring interpreter must remain available; the plugin is global for that config tree; Windows untested |
| OpenClaw | generated native plugin (`tool.execute.after` + tool registry) + MCP server for `sutra_restore` | **Yes, installed automatically** | **Live host tested** — OpenClaw 0.1.0, isolated config, real Bash tool, scripted local provider: 48,436 B compressed to 18,649 B in the model-facing request (marker + id), then `sutra_restore` returned the buried clue via MCP; `sutra doctor` passes | `sutra install --client openclaw` writes `~/.openclaw/plugins/sutra.js` (config directory overridable with `SUTRA_OPENCLAW_CONFIG`); restart OpenClaw | `tests/test_openclaw_adapter.py`: replacement, recovery through the real MCP server, protected/non-string pass-through, second-pass no-op, malformed/fail-open, storage failure; `tests/test_openclaw_doctor.py`: install, verify, idempotence, dry-run, uninstall, doctor trust states | Tested on OpenClaw 0.1.0 only; one short-lived Python process per oversized result (2.5 s timeout, fail-open); the configuring interpreter must remain available; the plugin is global for that config tree; Windows untested |
| DeepSeek Harness | host bundle on the harness's `tools/post-execute` waterfall (calls `python -m sutra process-result` and replaces only the text blocks that changed) plus an `mcpManager` registration of the recovery server | **Yes, once the bundle loads** | **One command wires both stages** — `sutra install --client dsh` writes the bundle to `$DSH_HOME/sutra-host` and attaches it to the profile; the tool stage is live in a running session (53,890 B to 18,340 B with a restore id) | `sutra install --client dsh`, then restart the harness; `sutra doctor --client dsh` reports the bundle and the profile entry | `tests/test_cli_process_result.py`: replacement with a recoverable id, non-text pass-through, evidence-tool and small-result bypass, malformed payload; the plugin handler was run against the real seam offline | the plugin needs a Python that can import SUTRA (`python`, `pythonPath` and `mode` are plugin config); one short-lived process per oversized result with a 4 s timeout; every failure returns the result unchanged |
| Cursor | native `postToolUse` hook (`updated_mcp_tool_output`) | **No adapter** | **Audited from official docs, not implemented** | MCP entry only, by hand (`~/.cursor/mcp.json`) | — | Cursor fires `postToolUse` for all tools but only replaces MCP tool output; built-in tool output cannot be replaced. No SUTRA hook is claimed. |

## Which lifecycle stage each adapter reaches

An optimizer can only act where the host hands it control. Seven stages matter:
before the conversation is constructed, before the prompt is assembled, before
the model request is sent, before a tool call executes, after a tool call
returns, after the model produces its answer, and provider usage reporting. A
"yes" below means the adapter really runs at that stage; "—" means the host
exposes no such boundary, and SUTRA does not claim it. Each cell describes what
an adapter does once its host has installed it and it is enabled; it does not
describe what one machine happens to have deployed. The request policy is
exercised directly by `tests/test_prompt.py`, while `tests/test_host_gate.py`
asks an installed `hermes` to admit the plugin and to compare its declared
hooks and middleware with what `register()` registered.

| Adapter | Before conversation | Before prompt | Before model request | Before tool call | After tool call | After generation | Usage reporting |
|---|---|---|---|---|---|---|
| Hermes Agent | — | — | **opt-in** | **yes** | yes | — | **observed** |
| Claude Code | — | — | — | — | yes | — | — |
| Codex CLI | — | — | — | — | yes | — | — |
| OpenCode | — | — | **opt-in** | — | yes | — | — |
| OpenClaw | — | — | — | — | yes | — | — |
| MCP clients | — | — | — | — | explicit call | — | — |
| CLI | — | — | — | — | explicit call | — | — |
| Cursor | — | — | — | — | audited only | — | — |

**Before model request** on Hermes is the `llm_request` stage: the plugin
receives the assembled provider request and may return a replacement. That frame
sees the whole conversation — every system, user, assistant and tool message —
which is the only place an adapter can shorten material SUTRA did not produce.
Both payload shapes hosts use are handled: `messages` for the chat and Anthropic
modes, and `input` for the Responses and Codex modes, where a tool result is a
`function_call_output` item. A top-level system prompt (`system` or
`instructions`) is never read for trimming and never rewritten.
SUTRA uses it for one job: trimming *older* tool results. Two kinds of result
qualify. One that already carries a recovery marker is shortened in place. One
that does not (a result the host produced unmodified) is first written to the
recovery store and then shortened against the id that write returned; if the
store declines — disabled, over quota, entry too large — the result goes out
byte-identical. Nothing is removed from a request unless an id can bring it
back. The result a later message refers to stays whole: when a later assistant or
user message cites a result's recovery id, or names a distinctive token inside it
— a path, a symbol, an id-like name — that result is mandatory and is not
shortened in any mode, because removing it would delete the text the message is
about. Ordinary words such as `error` or `output` do not count, so the protection
does not freeze a noisy result the conversation merely mentions in passing.
When the same payload appears more than once in the older window, every
repeat after the first is replaced by a pointer to the id of the copy already in
the request, which is the cheapest removal available because the bytes are
redundant rather than merely large. The pointer goes into the payload's own
container — a string stays a string and a block list stays a block list, with
the pointer in the first text block and any non-text block left where it was.
The stage is off unless `SUTRA_PROMPT=1`,
never touches system, user or assistant text in either shape, never touches the
most recent messages, and never modifies the stored conversation. `SUTRA_PROMPT_STORE_HOST=0` limits it to results that
already carry a marker, at the cost of leaving host-produced output alone. The
stage also needs a host running a plugin copy that declares it; a gateway that
installed an earlier copy keeps executing only the stages that copy declared,
which is a deployment state rather than a capability the repository can assert.
A gateway runs as a service, so `SUTRA_PROMPT=1` has to reach that process: export
it in the unit (a `~/.config/systemd/user/hermes-gateway.service.d/sutra.conf`
drop-in with `[Service]` and `Environment=SUTRA_PROMPT=1`, then `systemctl --user
daemon-reload` and restart) or the stage stays off while everything else works.
`sutra doctor` compares the installed copy with this package; it cannot see the
service environment, so an enabled copy with the stage off still reports
`status=pass`.
Installation keeps the previous plugin in `<HERMES_HOME>/plugin-backups`, outside
the directory Hermes scans. A backup named `sutra.bak-<stamp>` beside the plugin is
itself a plugin directory with the same manifest name, and the host can load the
stale copy instead of the one just written.

What the stage costs is measured rather than assumed. `bench/request_stage.py`
builds a synthetic conversation, runs the real policy for each mode, and reports
bytes before and after alongside median and p95 wall-clock time and peak
allocation; it can also run the policy from several threads at once. On a
513,592-byte request with 40 recoverable results a call costs 21.7 ms median
(balanced), 23.5 ms (aggressive) and 24.5 ms (beast) with under 90 KiB peak, and
a 1,698,032-byte request with 80 results stays under 64 ms. Each candidate body
is scanned once and dependency protection consults a suffix token set built once
per request, so the cost follows request size rather than results multiplied by
body size.

A shortened result also keeps the lines that say what failed. From the stretch
that neither the head nor the tail reaches, a body whose text carries
`Traceback`, `FAILED`, `ERROR`, a `…Error:` name, `File "…", line N` or an
`assert` line contributes up to 2,048 bytes of those lines to the preview, and
they are paid for out of the head's own budget: the request does not grow by a
byte, a failure line simply replaces a line of context that said nothing. The
idea comes from a paired `pytest-fail` session in which the trim removed the
failure text, the agent re-ran the suite, and the pair cost more than it saved.

Synthetic requests say what the stage costs; recorded ones say what it finds.
`bench/replay_requests.py` reads request bodies a host saved before submission,
rebuilds the message list the middleware would see, and reports the same
figures together with two safety checks: every touched message must still carry
an id whose stored copy is byte-identical to the original, and every untouched
message must be byte-identical to what was recorded. A replay of twelve recorded
Hermes request bodies (1,075,715 bytes of conversation, 197 tool results) removed
1.1% in balanced, 4.3% in aggressive and 12.8% in beast, at a median of 13 to 17 ms
per call, with both checks passing for every file. The small balanced
figure is a property of that corpus rather than of the policy: those results
have a median size of 662 bytes and only three of the 197 exceed the balanced
8 KiB threshold, and the policy never touches the newest six messages. A corpus
of long command output, which is what the stage is aimed at, moves the same
threshold from marginal to consequential.

The host's own session store gives a second, larger sample that no failure was
needed to record. `bench/replay_sessions.py` opens `state.db` read-only,
rebuilds each conversation from its active messages and replays it: 200 sessions
(9,086,662 bytes, 5,698 stored tool results) lose 4.1% in balanced, 6.8% in
aggressive and 13.3% in beast, at a median of 0.2 to 0.8 ms per conversation, with
every touched result restorable and every untouched message byte-identical. A
result that already carried an id is counted as "already marked" rather than
re-verified, because its original belongs to the host store the benchmark does
not open.

The stage also keeps the prefix it already sent. A request whose results already
carry the notice comes back unchanged, so the second submission of the same
conversation differs from the first only where the conversation grew, and a
provider that caches processed prefixes is never asked to re-read text SUTRA
already shortened. Retention itself is single-flight: two sessions retaining the
same payload at the same moment agree on one id, and the store holds one entry,
not two.

Three consequences follow, and they are the reason the capability set differs by
host rather than by SUTRA version:

1. **Skipping work is possible only in Hermes.** `tool_execution` is the single
   shipped frame that sees a call before it runs, so it is the only place the
   replay store can serve a recorded result instead of executing again. Every
   other adapter sees the result already produced and can only compress it —
   see [redundancy.md](redundancy.md).
2. **Shortening the request before the model reads it is possible in Hermes
   only.** `llm_request` receives the assembled request and accepts a
   replacement, so the adapter can shorten older results that stay recoverable.
   Full selection is not implemented on that boundary: dropping whole items and
   returning a reordered set against a caller's budget still needs a caller, so
   `sutra context` takes its items explicitly and the selection code in
   [context-intelligence.md](context-intelligence.md) runs where a caller can
   supply them and consume the plan.
3. **Provider usage is reported by one host, and only observed.** Hermes fires
   `post_api_request` with the usage summary of the response it already
   received, so SUTRA counts the buckets there — requests, reported versus
   unreported summaries, input and output tokens, and the cache read and write
   tokens when the provider sends them — and exposes them under `provider` in
   `sutra.metrics_snapshot()`. That is observation: SUTRA never sets a cache flag, moves a
   cache breakpoint or asks a provider to cache anything, because a cache it
   cannot control would be a claim rather than a measurement. No other adapter
   reports usage; `sutra cost --usage-file` still prices a payload the caller
   supplies, and cache buckets stay labelled unavailable where the payload does
   not carry them.

### Request-stage status by host

The lifecycle table says which boundaries exist; this one says how far each host
has got, using the same vocabulary the rest of the documentation uses: something
is **implemented and verified** only when the code, its tests, the host's own
admission check and the host's own dispatch all agree. Admission alone is not
enough — a plugin can declare a stage that never runs — so
`tests/test_hermes_dispatch.py` starts a host plugin manager in a throwaway
`HERMES_HOME`, lets it discover and load this plugin, submits a request through
`hermes_cli.middleware` and asserts on what the host returns: a 92,984-byte
conversation goes out as 3,297 bytes with one marker whose stored copy is
byte-identical, a second submission comes back unchanged, and the
`post_api_request` hook records the usage it was handed. That test is opt-in
(`SUTRA_TEST_HERMES_DISPATCH=1`, `SUTRA_HERMES_SRC=<checkout>`) because it needs
a separate installation; what it does not prove is that a *running* gateway has
the current copy loaded, which the deployment note below still applies to.

The check that separates *installed* from *current* is `hermes.deployment()`: it compares the stages the installed `plugin.yaml` declares with the stages this package declares, and compares the code files byte for byte. A copy that declares fewer stages is reported as stale rather than healthy, because Hermes executes exactly what the directory declares. On the machine this was written on the installed copy predates the request stage, so `sutra doctor` prints `copy=stale missing_stages=llm_request, post_api_request probe=the installed copy predates this package: it does not declare llm_request, post_api_request; run sutra install --client hermes and restart the gateway`. That report is the honest state of this installation: implemented, verified in the repository, not yet running live.


| Host | Boundary | Status |
|---|---|---|
| Hermes Agent | `llm_request` | **Implemented and verified in the repository.** The policy has tests, the host executes it end to end in `tests/test_hermes_dispatch.py` (discovery → middleware → rewritten request → observation hook), and `hermes plugins validate` accepts the declared stage. It is **pending deployment** in any gateway whose installed plugin copy predates it, and `sutra doctor` names that state instead of passing it: `copy=stale` with the stages the installed copy does not declare. |
| Claude Code | none for the request | **Not implemented, and audited.** Claude Code 2.1.268 accepts `SessionStart`, `SessionEnd`, `Notification`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `Stop`, `SubagentStop` and `PreCompact`. None of them receives the assembled provider request; `UserPromptSubmit` may add context and `PostToolUse` may replace the result of the call that fired it, and neither can shorten the conversation already built. |
| Claude Code | `ANTHROPIC_BASE_URL` (forwarding proxy) | **Implemented in the repository, opt-in, not verified against the live client.** Claude Code honours `ANTHROPIC_BASE_URL` (the string appears 54 times in the 2.1.268 bundle), so `sutra proxy` gives the policy a request boundary the hook system does not: the client sends `/v1/messages` here, older recoverable tool results are shortened, and the request continues to the upstream with every other header, path and query untouched; the response streams back byte for byte. A rewrite never fails a request — the original bytes are forwarded once. `tests/test_proxy.py` drives a real socket pair (client → proxy → fake upstream) and asserts the credential header survives, a host-produced result's id restores the exact bytes, non-messages paths and malformed JSON pass through, `off` mode forwards untouched, a streamed `text/event-stream` answer arrives as four intact events, `HEAD`/`OPTIONS` are forwarded with their headers, the CLI process serves the same request when started the way an operator starts it, and a dead upstream returns 502 without echoing the URL. A Messages-API body is handled in its own shape: a `tool_result` block inside a user message is shortened in place, `tool_use_id`, `is_error` and `cache_control` survive, and only a message carrying a single result is ever replaced by a pointer, so no `tool_use` loses its answer. A live attempt on this machine reached the proxy — the client sent its `HEAD /w/claude/api/hello` check, which the proxy answered from the upstream — but the installed client's OAuth session had expired (`Failed to authenticate: OAuth session expired and could not be refreshed`), so no model turn completed and the rewrite path is proven by the socket tests, not by a live turn. |
| Codex CLI | none for the request | **Not implemented.** The Responses `input` shape is handled whenever a host supplies it, but Codex CLI offers no hook at that boundary. |
| OpenCode | `experimental.chat.messages.transform` | **Implemented in the repository, opt-in, plugin-contract tested.** OpenCode 1.19.6 hands a plugin the assembled conversation before the provider call, so the generated plugin collects every string in `part.state.output` above `SUTRA_MAX_BYTES`, sends them to `sutra shorten-results` in one subprocess, and writes back only a string whose original the store kept. The hook fails open, touches nothing but that string, and the marker's id restores the exact bytes. `tests/test_opencode_request_stage.py` pins the command seam: same policy, same store, same marker, batch mapped by position, `off` and a refused retention leave the text alone. It has not been driven by a live OpenCode session. |
| OpenClaw | none for the request | **Not implemented**, for the same reason as OpenCode. |
| MCP clients | none | **Technically unavailable.** A server sees only the requests addressed to it. |
| CLI | none | **Not applicable.** A subprocess filter has no request to read. |

Those "none" rows are checked, not assumed: the Claude Code row comes from the
hook event names the installed 2.1.268 bundle accepts, and the OpenCode row from
`tool.execute.after`, the only extension point that sees a finished result.

## Detection and diagnosis

`sutra detect` is read-only and reports concrete evidence, the selected
mechanism, and an honest capability tier for Hermes, Claude Code, OpenCode,
OpenClaw, Cursor, Codex CLI, and Windsurf/Cascade. Codex is `automatic` only
when the SUTRA hook is installed. OpenClaw is detected from its executable,
`<config>/openclaw.json`, or the generated `plugins/sutra.js` (it can be
installed before the host exists). `sutra doctor` reports all five first-class
clients, including `not-installed` rows; it executes the Hermes, Claude Code,
OpenCode, OpenClaw and Codex hook paths, and reports Codex's trust state. OpenCode's probe includes a real CLI
round-trip, a `tool.execute.after` contract check when Node is available, and a
protected diff that must stay byte-identical. See
[universal-integration.md](universal-integration.md) for the host audit and
[automatic-install.md](automatic-install.md#read-only-detection) for detection.

## Adapter usage

### Claude Code (automatic)

The supported path is the installer; it detects Claude Code, writes the user
settings, registers the MCP recovery server, backs up every edit, and then runs
the same probe as `sutra doctor`:

```bash
sutra install --client claude
sutra doctor
```

For a manual checkout install, add the hook to project or user settings, with
an absolute path. An install provides `sutra-hook`; a checkout runs the script
it wraps:

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
result, an envelope that is not valid UTF-8, or any internal failure prints `{}`
and leaves the original tool result untouched. It never decodes its protocol
input with replacement characters. When storage is unavailable it still
returns the original result unchanged, logs a warning to stderr, and quotes no id.

A tool result reaches the hook in three shapes, and all three are trimmed: a
plain string, a dict whose named text fields (`output`, `stdout`, `stderr`,
`content`, `text`, `result`, `data`) hold the text, and a dict whose text sits
one level down. `Read` answers with the second nested kind —
`{"type": "text", "file": {"filePath": …, "content": …}}` — and that
`file.content` slot carries the host's largest result type, so it is trimmed in
place while `filePath`, `numLines`, `startLine` and `totalLines` are copied
through unchanged. The caller's object is never mutated, and a shape the seam
does not recognise comes back identical.

When Claude Code persists a large Bash/PowerShell result, the hook reads the
full file named by `persistedOutputPath` only if it is a regular `.txt` file of
the declared size under that session's `tool-results` directory. It never
compresses the 30,000-character preview: if the file cannot be read safely, the
result is left alone. The evidence and the failure cases are in
[automatic-install.md](automatic-install.md).

### CLI

```bash
# compress a tool output (stdin or --file), one JSON object out
python3 scripts/sutra-cli.py compress --tool bash < output.txt
# retrieve retained evidence; the recovery protocol, verbatim
python3 scripts/sutra-cli.py restore --id su_… --mode search --query "error" --context 2
# search is case-sensitive by default; --ignore-case matches without case
python3 scripts/sutra-cli.py restore --id su_… --mode search --query "error" --ignore-case
# resolved configuration (no secrets)
python3 scripts/sutra-cli.py config
```

`--case-sensitive` and `--ignore-case` are mutually exclusive. Omitting both
keeps the recovery protocol's default, which is case-sensitive.

Exit codes: `0` a result was produced (including explicit recovery statuses,
and a `--file` the engine refuses to rewrite as too large); `1` internal
failure, stdin above 64 MiB, or input that is not valid UTF-8 (JSON error on
stdout); `2` usage error. Invalid bytes are rejected, never replaced with
U+FFFD. Only stdin is size-checked by the CLI: a larger `--file` is read and
returned unchanged by the engine (`too-large-kept`) with exit 0. Uninstall:
delete the scripts; retained originals live under `HERMES_HOME` (see
[troubleshooting](troubleshooting.md#removing-it)).

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
`sutra_compress` and `sutra_restore`, and exits cleanly on stdin EOF. A request
line that is not valid UTF-8 receives one `-32700` parse error and the loop
keeps serving; it does not kill the server. Point `HERMES_HOME` at a dedicated
directory to isolate its store from a Hermes profile. Uninstall: remove the
server entry from the client config.

## What is verified where

- **Real host (Hermes)**: scripted-provider E2E runs the real gateway, real
  terminal tool, real middleware, real store and recovery through the host's
  Tool Search bridge — `scripts/e2e/run.sh`; recorded 8/8 lifetime and 2/2 on
  2026-10-24. This harness is manual (it needs a Hermes installation), not CI.
- **Real host (OpenCode)**: OpenCode 1.19.5 with a scripted provider; the
  model-facing request log is the evidence (48,436 B → 18,649 B, marker seen,
  clue recovered; see [automatic-install.md](automatic-install.md#opencode)).
  The original 1.19.0 spike proof is not part of the public checkout.
- **Live host (Claude Code)**: Claude Code 2.1.268, isolated `HOME` and
  `CLAUDE_CONFIG_DIR`, a local scripted Anthropic-compatible provider and a real
  Bash tool call. The model-facing request carried the compressed 18,645-byte
  (18,641-character) result for a 70,888-byte persisted output, and
  `sutra_restore` returned the buried clue through MCP. No live model or provider
  was called; see
  [automatic-install.md](automatic-install.md).
- **Hook level (Claude Code)**: `tests/test_claude_code_hook.py` invokes the real
  hook process with `PostToolUse` events, including the persisted-output shape,
  its size/symlink/tool-name refusals, recovery through the MCP server,
  pass-through and fail-open.
- **Unit + protocol tests (CI, every push)**: engine rules, store, recovery
  protocol, Hermes middleware contract, the `core` seam, and adapter contracts
  (CLI/MCP over real subprocesses and pipes; the hook over real stdin/stdout).
- **Live-model experiments (pending quota)**: the frozen A/B protocols
  ([ab-benchmark.md](ab-benchmark.md)) — v1 (8 tasks, default) and v2
  (30 tasks); no task-success or billing claim exists yet.

These layers are not interchangeable and are never reported as each other.

### Running the proxy

The proxy is a separate foreground process, because it has to sit between the client and the provider:

```
sutra proxy --port 8787 --upstream https://api.anthropic.com
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude
```

It binds `127.0.0.1` only and answers `GET`, `HEAD`, `OPTIONS`, `PUT`, `PATCH` and `DELETE` by forwarding them unchanged; only a `POST` whose path ends in `/v1/messages` (Anthropic Messages) or `/chat/completions` (OpenAI Chat Completions) is read. It never reads, logs or rewrites a credential header, never changes the model, the path or the query, and never stores the request itself — only the tool results the policy chose to shorten, under the same recovery ids the rest of SUTRA uses. `GET` requests and every other path are forwarded unchanged, so a host that calls other endpoints keeps working. Stopping the proxy puts the client back on the direct path as soon as it points at the provider again. A target that names another origin — `http://…`, `//…`, a backslash or whitespace — is answered with `400` instead of being forwarded, and a header whose name or value carries a control character is dropped on both legs rather than relayed.

The answer is the one place where a session reports what the provider charged for. Anthropic sends `input_tokens`, `output_tokens`, `cache_read_input_tokens` and `cache_creation_input_tokens`; OpenAI sends `prompt_tokens`, `completion_tokens` and `prompt_tokens_details.cached_tokens`. The proxy reads at most the first 256 KiB of each modelled answer — a streamed answer's `message_start` event too — maps the two cache buckets onto the names the rest of SUTRA counts, and records them through `metrics.record_provider_usage()`. The bytes the client receives are the provider's, unchanged; an answer that reports no usage is counted as not reported rather than as zero.

### Pointing a host at the proxy

A host connects through the proxy by pointing its OpenAI- or Anthropic-compatible base URL at the loopback port. Nothing else about the host changes: its model, credentials, headers and responses stay its own, and only the body of a tool-heavy request is rewritten.

### DeepSeek Harness: one command for both stages

DeepSeek Harness has no settings file a third party may edit; a capability arrives as a *bundle* — a package directory that carries a `dsh.bundle.patch` — and the supported way to attach it is the harness's own plugin command. `sutra install --client dsh` does exactly that:

```
sutra install --client dsh
sutra doctor --client dsh
```

It copies one small bundle to `$DSH_HOME/sutra-host`, fills in the interpreter that can import SUTRA, runs `dsh plugin --profile <profile> add <bundle>`, and then places the bundle inside the profile's agent preset. Both steps matter: the plugin command makes the package load, and the preset is what lets a model's own tool call reach the listener, because the harness scopes `tools/post-execute` to the calling agent's scope chain. A copy attached only at profile level loads, reports a healthy doctor, and silently does nothing. The bundle wires the two stages this harness exposes: `tools/post-execute` trims an oversized tool result through `python -m sutra process-result` (the same seam every other host calls), and `mcpManager.register` exposes the recovery server so the id the marker quotes can be exchanged for the exact bytes the store kept. Restart the harness once after installing; a loaded bundle logs `sutra: host bundle loaded`. The proof that it is working is not the doctor line but a tool result the model receives: an oversized one arrives as head and tail around a marker like `[sutra: partial · 45,781 bytes elided (large output) · id=su_… · retrieve: sutra_restore(id="su_…", mode="bytes", start=12249, end=58030)]`.

The doctor line answers a different question from `attached`. `sutra doctor --client dsh` reads the seam's own `stats.jsonl` and reports `stage=saved rewrites=N saved=… last=…` once a tool result has actually been through it; until then it reports `stage=never` and names `no tool result has been trimmed in this harness yet` as a problem, so a wired-but-idle install is not mistaken for a working one. An attached bundle and a saving stage are different claims, and both are visible now.

The request stage is deliberately absent. The harness hands a plugin a model route on `agent/request` (a provider and model, not the outbound message array), and its history rewriting belongs to compaction, so a plugin that claimed to trim the outbound prompt there would be describing a hook the harness does not expose. A harness that wants the request stage can still point a provider's `baseURL` at `sutra proxy`, as below.

DeepSeek Harness reads its providers from the `llm-pi-ai` entry in `~/.dsh/profiles/desktop/cordis.patch.yml`. Adding a provider whose `baseURL` names the proxy routes that provider's traffic through the policy, while the key stays where the entry already keeps it:

```yaml
- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      sutra:
        displayName: SUTRA proxy
        apiKeyEnv: COMMANDCODE_API_KEY
        api: openai-completions
        baseURL: http://127.0.0.1:8787
        models:
          - id: deepseek/deepseek-v4.1-flash
            name: DeepSeek V4.1 Flash
            contextWindow: 1000000
            maxTokens: 65536
```

The matching upstream is the origin the provider entry used before — `sutra proxy --upstream https://api.commandcode.ai` for a `commandcode` base URL of `https://api.commandcode.ai/provider/v1`. The client's own path, `/provider/v1/chat/completions`, arrives at the proxy unchanged and is forwarded to the same path on that origin, because the rewrite matches a path suffix rather than a fixed path.

OpenCode takes the same base URL under `provider.<id>.options.baseURL` in `opencode.json`, and Codex CLI honours `OPENAI_BASE_URL` for an API-key login. Claude Code uses `ANTHROPIC_BASE_URL`, shown above. Each of these hosts keeps its own native integration where one exists — Hermes uses its middleware, OpenCode its transform hook — and the base URL is the fallback for a host that exposes no hook at all.
