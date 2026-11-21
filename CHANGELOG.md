# Changelog

## Unreleased

### Changed

- Every mode now keeps a head and a tail beside a trimmed result's marker, and the size of that
  preview follows the mode: `1536`/`768` bytes in `balanced` and `aggressive`, `384`/`192` in
  `beast`. A marker-only `beast` trimmed 78.2% of the fixture but made a live session read the
  file twice; with the smaller preview the same task used 3 model calls and 86,999 whole-prompt
  tokens against 5 calls and 143,825 without SUTRA, and the fixture still removes 75.0%.

### Added

- The generated OpenCode plugin now shortens the tool results already inside a conversation before the provider call, through `experimental.chat.messages.transform` and `sutra shorten-results`; only a string whose original the store kept is replaced, and the hook fails open.
- `sutra proxy` now also counts what the provider charged: a `/v1/messages` answer's `usage` is read from JSON or from a stream's first event, its `cache_read_input_tokens` and `cache_creation_input_tokens` are mapped onto the counters' names, and an answer that reports no usage counts as not reported rather than as zero. The answer itself is relayed byte for byte — this is observation, not cache control.
- `sutra proxy` gives the request stage a boundary on hosts that expose only a base URL: Claude Code honours `ANTHROPIC_BASE_URL`, so the client can send `/v1/messages` through SUTRA, where older recoverable tool results are shortened before the request continues upstream. Credentials, the path, the query and the response are left alone, and any rewrite failure forwards the original body. Streaming answers, `HEAD`, `OPTIONS`, `PUT`, `PATCH` and `DELETE` are forwarded unchanged. The Messages-API shape is read as its own: a `tool_result` block inside a user message is shortened in place and a 121,474-byte body with two identical results came out at 3,559 bytes (97.1% removed) with `tool_use_id`, `is_error` and `cache_control` intact.
- `sutra doctor` compares the installed Hermes plugin with this package and reports `copy=stale` with the stages the installed copy does not declare, so a gateway running an older copy is no longer reported as healthy.
- The request stage is exercised through Hermes' own dispatch, not only through
  its admission check. `tests/test_hermes_dispatch.py` (opt-in:
  `SUTRA_TEST_HERMES_DISPATCH=1`, `SUTRA_HERMES_SRC=<checkout>`) starts a host
  plugin manager in a throwaway `HERMES_HOME`, lets it discover and load this
  repository's plugin, submits a request through `hermes_cli.middleware`, and
  asserts that 92,984 bytes go out as 3,297 with one marker whose stored copy is
  byte-identical, that a second submission comes back unchanged, and that the
  `post_api_request` hook records the usage it was handed.
- The request stage keeps a stable prefix and a bounded store under parallel
  sessions. A request already carrying the notice is returned unchanged, so a
  provider prefix cache is not invalidated by re-trimming, and two sessions
  retaining the same payload agree on one id. Retention now writes and enforces
  its budget as one critical section: six threads writing 3 KB payloads into a
  20,000-byte store stayed at or under both ceilings, where the unserialized
  version reached 27,171 bytes.
- `bench/replay_sessions.py` replays the host's own session store through the
  request stage, so the policy is measured on ordinary sessions and not only
  on saved or synthetic requests. It opens `state.db` read-only, rebuilds each
  conversation, and checks that every touched result restores byte for byte and
  every untouched message is unchanged. Two hundred sessions (9,086,662 bytes)
  lose 4.1% in balanced, 6.8% in aggressive and 13.3% in beast at a median of
  0.2 to 0.8 ms per conversation.
- `bench/replay_requests.py` replays request bodies a host saved before
  submission, so the stage is measured on recorded traffic as well as on a
  synthetic conversation. It reports bytes removed per mode and checks two
  safety properties on every file: each touched message still resolves to a
  byte-identical stored copy, and each untouched message is byte-identical to
  what was recorded. Twelve recorded Hermes request bodies (1,075,715 bytes, 197
  tool results) removed 1.1% / 4.3% / 12.8% in balanced / aggressive / beast at a
  median of 13–17 ms per call, with both checks passing; those results have a median
  size of 662 bytes, which is why the balanced 8 KiB threshold rarely fires on
  that corpus.
- The request stage now costs about a tenth of what it did: a 513,592-byte
  request with 40 recoverable results went from 207.6 ms to 21.7 ms median, with
  the same bytes removed. Dependency protection consults one suffix token set per
  request instead of scanning every candidate body per comparison, and the
  shared retention memo is guarded so concurrent sessions cannot corrupt it.
  `bench/request_stage.py` reproduces the measurement (bytes, median/p95 time,
  peak allocation, and a multi-thread run).


- `sutra stats` aggregates the durable savings log (`SUTRA_STATS_FILE` plus its
  rotated `.1` file) into rewrites, before/after bytes, saved bytes, partial and
  fail-open counts, and per-rule and per-tool breakdowns, with `--json` and
  `--file` for machine use and alternate logs. It says plainly that counters
  living in a host process (restores, request-stage rewrites, observed provider
  usage) are not visible to it, and a line that does not parse — or parses but
  carries a non-numeric value where a byte count belongs — is counted as
  unreadable instead of hiding the rest of the file or aborting the report
  (`tests/test_cli_stats.py`).
- Request-stage decisions are now counted per mode. `sutra.metrics_snapshot()`
  gained a `prompt` block with `requests`, `rewritten`, `unchanged`,
  `original_bytes`, `trimmed_bytes`, `saved_bytes` and a `by_mode` table, so the
  depth each mode reaches can be measured on real traffic rather than only on a
  fixture. A counter failure is swallowed: it never breaks the request.
- Request-stage trimming now protects what the conversation still depends on: a
  tool result that a later assistant or user message cites by its recovery id, or
  names through a distinctive token inside it (a path, a symbol, an id-like
  name), is treated as mandatory and left byte-identical in every mode. Ordinary
  words such as `error` do not qualify, so a noisy result merely mentioned in
  passing is still shortened. On one 48 KB request with two 20 KB results, naming
  the first in a later message leaves 26,893 bytes instead of 5,573 when neither
  is named (`tests/test_prompt.py`).
- Collapsing a repeated block payload keeps the payload's container: the pointer
  replaces the text, a block list stays a block list, and non-text blocks such as
  images stay where they were, so a request shape that expects blocks is never
  handed a bare string (`tests/test_prompt.py`).
- The request-stage caps now follow the mode: `balanced` trims 8 results per
  request above 8 KiB, `aggressive` 16 above 4 KiB, and `beast` 32 above 2 KiB
  with the preview dropped so a trimmed result becomes its marker alone. On one
  539 KB request those settings remove 15.8%, 31.7% and 78.2% of the bytes
  respectively (`tests/test_mode_policy.py`), and an explicit `SUTRA_PROMPT_*`
  variable still overrides the default of the mode in force.
- The Hermes `llm_request` stage can now shorten tool results SUTRA never produced.
  A host-produced result is written to the recovery store first and then
  trimmed against the id that write returned, so every removed byte stays
  restorable through `sutra_restore`; when the store declines, the result is
  sent byte-identical. `SUTRA_PROMPT_STORE_HOST=0` keeps the earlier
  behaviour of touching only results that already carry a marker.
- A tool result repeated in the older part of a request keeps its first copy and
  every later copy becomes a pointer quoting that copy's recovery id, so the
  redundant bytes leave without a second store write. Shortened and collapsed
  messages share the per-request `SUTRA_PROMPT_MAX_MESSAGES` budget.
- The request stage reads both payload shapes a host can send: `messages` for
  chat and Anthropic modes, `input` for Responses and Codex modes, where a tool
  result is a `function_call_output` item. A top-level system prompt (`system`,
  `instructions`) is never trimmed. On a 68 KB Responses request holding one
  large result and one repeat of it, the copy sent to the provider was 2.8 KB.
- The Hermes `post_api_request` hook is now observed: provider usage summaries
  are counted under `provider` in the stats snapshot, including cache-read and
  cache-write tokens when the provider reports them, and summaries that did not
  arrive are counted as unreported rather than as zero. This is measurement
  only — SUTRA sets no cache flag and asks for no cache behaviour it cannot
  control.
- Four optimization modes behind one switch: `SUTRA_MODE=off|balanced|aggressive|beast`.
  A mode selects the deterministic plans, bounded passes, combination depth and
  repeated-call reuse policy; `sutra mode` shows or persists the choice in
  `~/.sutra/mode`. `off` remains the default and keeps the existing head/tail
  compression and recovery path. The legacy `SUTRA_SEARCH` values still map to
  their previous policies.
- A single orchestrator now owns mode policy, plan ordering, candidate
  evaluation, SACE selection and the recorded decision. JSON documents and
  patches never enter the optimization layer.
- Caveman gained adjacent repeated-word and exact repeated-sentence removal and
  a tool-description entry point. Everything still passes the exact
  protected-span check.
- The live-model A/B experiment now has explicit protocol versions. `v1` keeps
  the original frozen eight tasks and is the default for a live run; `v2` is
  the expanded 30-task corpus and is opt-in. Result rows and filenames carry the
  protocol, and `scripts/ab/report.py` prints it and refuses mixed or unlabelled
  evidence instead of silently aggregating different task sets. No live model
  was called.

### Changed

- `balanced` is now the default `SUTRA_MODE`. A normal installation obtains
  optimization by installing and optionally selecting a mode; no search,
  language, plan, reuse or budget variable is required. Existing installations
  keep the legacy `SUTRA_SEARCH`/`SUTRA_LANGUAGE` paths, and `SUTRA_MODE=off`
  preserves the previous no-optimization path.

- A rewrite is now returned only when the original is stored and the marker
  quotes its id. A disabled store, an over-quota entry, a failed write or a
  failed marker pass returns the original with a `recovery-*-kept` rule instead
  of an unrecoverable smaller result.
- Release verification now polls TestPyPI and PyPI for up to 20 minutes,
  logging each JSON-API and simple-index attempt and backing off from 10 to 60
  seconds. Index propagation no longer requires a manual rerun, while the
  checks still require the exact version and both built distribution files.
- The normal CI product-suite jobs install the `telemetry` extra and run
  CLI-level tests for dead, malformed, and hanging OTLP endpoints. Those tests
  prove compression still exits 0, emits one parseable JSON object, and writes
  no exporter noise.

### Fixed

- The A/B runner reads the session id from either stream and can run a single
  task. `hermes chat -q` prints `session_id:` on stderr while the interactive
  form prints `Session:` on stdout, so a runner that read only stdout recorded
  every live result as `session_id=None` and `usage=unreported` — the row the
  experiment exists to read was silently missing. `--task NAME` (repeatable)
  runs one task of the selected protocol; `docs/ab-benchmark.md` states both
  and the balanced-mode admission limit that keeps small conversations
  unrewritten.

## 0.6.3 - 2026-11-14

### Fixed

- Optional OpenTelemetry telemetry can no longer break compression when the
  SDK or an exporter fails to initialize. `get_telemetry()` now falls back to
  a no-op, partial SDK initialization is shut down, and exporter threads are
  not left running.
- A dead, unreachable, malformed, or hanging OTLP collector no longer adds
  OpenTelemetry's default 30-second shutdown wait or writes exporter failures
  to an unconfigured CLI's stderr. Export and shutdown now use a bounded
  2-second budget per exporter. The CLI still exits 0 and emits one JSON
  object when compression finishes.
- `SUTRA_TELEMETRY_SAMPLE_RATE` now controls trace sampling through
  OpenTelemetry's `TraceIdRatioBased` sampler. The previous implementation
  only used the value as an on/off gate, so `0.5` still emitted every metric
  and trace. `0.0` disables spans and metric recording; `1.0` samples every
  span. Metrics remain exact counters and histograms and are not
  probabilistically downsampled; this is now stated in the docs.
- Enabled telemetry re-reads its endpoint and sample settings and rebuilds the
  exporter when they change, instead of keeping the first configuration for
  the lifetime of the process.
- A source checkout reports the package `__version__` instead of a hardcoded
  telemetry version.

### Changed

- `SECURITY.md`, `docs/contract.md`, `CONTRIBUTING.md`,
  `docs/semantic-retrieval.md`, and `docs/telemetry.md` now describe the
  opt-in OTLP exporter accurately instead of claiming that SUTRA has no
  telemetry or network code at all.
- Telemetry documentation now covers the 2-second export budget, clean CLI
  output on exporter failure, trace-only sampling, and why metric volume is
  not reduced by `SUTRA_TELEMETRY_SAMPLE_RATE`.

### Verification

- Telemetry tests: 22 passed. Full test suite: 728 passed, 16 skipped.
  `ruff check .` passed.
- Real OpenTelemetry 1.45.2 runs against the built wheel covered disabled
  telemetry, dead and unresolvable endpoints, invalid URLs, malformed HTTP
  responses, HTTP 500, and hanging responses. Every compression run exited 0
  with one parseable JSON object on stdout and no stderr output; explicit
  shutdown left no exporter threads behind.
- Published `sutra-agent 0.6.2` reproduces the old 12.44-second dead-endpoint
  delay and stderr noise. These fixes are source-only for the previously
  published release; 0.6.3 is the first release containing them.

## 0.6.2 - 2026-11-14

### Fixed

- Windows installs no longer crash when console or command output is not UTF-8
  (for example cp932) or a command returns no output to split into lines.
- CLI failures now print an actionable message instead of an internal traceback,
  and the summary accounting matches what was actually installed or skipped.
- The release workflow can promote the exact TestPyPI artifact when production
  is dispatched from `main`; the previous branch check rejected that valid path.

## 0.5.0 - 2026-11-06

### Added

- **Agent cost policy.** A bounded, clearly labelled guidance section is
  injected through Hermes' documented `register_system_prompt_section` plugin
  API. `SUTRA_POLICY=off|balanced|aggressive` selects the text; `off` is the
  default because the replay measures a per-turn prompt cost and no live run
  has yet shown a behavioural gain. `balanced` is recommended when enabled.
  The section states that host instructions, the user's task and repository
  rules take precedence, and names the work that must never be skipped. It is re-resolved per new session, so `off` adds no prompt bytes
  without editing host configuration. A Hermes build without the API still
  gets compression and recovery.
- **Exact-duplicate reuse.** A result that exactly matches an earlier
  compressed plain-text result in the same session is replaced by a marker
  quoting the earlier id. The store entry is verified for existence, size,
  hash and TTL first; diffs, JSON and tracebacks are never replaced. The index
  is bounded to 64 sessions × 128 digests and is never durable.
- **Model-free cost replay.** `eval/replay.py` runs eight deterministic Hermes
  workloads through the real core, store and recovery seam and reports
  tool-result context tokens, reuse, latency, policy prompt cost and clue
  retention. It calls no model and makes no provider-cost claim.
- **`sutra status` reports optimization state.** One quiet line shows
  compression, policy mode, reuse, recovery availability and the size cap.

### Changed

- README positions SUTRA as agent cost-optimization middleware rather than
  only a tool-output compressor. No new performance claim is made: the replay
  numbers are tool-result estimates and the policy's behavioural effect is
  explicitly not measured.
- Version 0.5.0.

### Verification

- Public pytest, ruff, the model-free replay and the isolated Hermes plugin
  validation are recorded in the release pull request. The live-model A/B has
  still not been run.

## 0.4.2 (2026-11-01)

Published to PyPI on 2026-11-01. The immutable `v0.4.2` tag was cut before
publication, so its copy of this heading still says `(unreleased)`; this
post-release update is on `main` and does not move the tag.

### Fixed

- **Diagnosis no longer invokes host CLIs that mutate configuration.** Claude
  Code `status` and `doctor` read the user-scope MCP entry from `.claude.json`
  instead of running `claude mcp get` (which creates that file). Codex `status`
  and `doctor` read `config.toml` instead of running `codex mcp list`. Claude's
  doctor now starts the configured MCP server and restores the probe id through
  it, proving the recovery path rather than merely reading a label.
- **One broken adapter no longer hides the others.** `sutra install` and
  `sutra doctor` catch per-client exceptions, report that client as failed and
  continue with the remaining hosts. A non-zero result is preserved.
- **`sutra status` counts installed hooks and plugins as configured.** A Codex
  hook or Hermes/OpenCode plugin previously returned a failure status when MCP
  was not separately registered.
- **Codex dry-run no longer invokes the Codex CLI.** It reads `config.toml` for
  the existing MCP entry and prints the intended action.
- **Doctor states are honest about missing hosts.** Claude Code fails when its
  executable is gone; Hermes reports a stale plugin without an executable as a
  failure; OpenCode reports a missing host as `partial` instead of a green
  pass.

### Added

- `sutra install` prints a concise per-client summary with `configured`,
  `active`, `requires /hooks trust`, `hook-only`, `skipped` or `failed`, plus
  the next verification command. `--dry-run` prints the same summary without
  claiming a change.
- Regression tests for read-only diagnosis, Codex dry-run, per-adapter failure
  isolation, status classification and the OpenCode missing-host state.

## 0.4.1 (2026-11-01)

### Fixed

- `sutra install --dry-run --client claude` invoked `claude mcp get` before
  printing its preview; the Claude CLI creates `~/.claude.json` even for that
  read, so a dry run left a file behind. Dry runs for install and uninstall now
  never invoke the Claude CLI and state the intended MCP action instead. The
  regression test fails on any CLI call and checks that no `.claude.json` is
  created.

## 0.4.0 (2026-11-01)

### Added

- **Codex CLI automatic integration**: `sutra install --client codex` now
  writes a user-level `PostToolUse` hook to `$CODEX_HOME/hooks.json` in addition
  to the official MCP recovery entry. The hook returns `continue: false` with
  the compressed result; Codex 0.166.0 replaces the model-visible tool output
  with that text and marks the hook run `Stopped` (no error). Other hooks and
  settings are preserved, every edit is backed up, installation is idempotent,
  malformed JSON is refused, and uninstall removes only SUTRA-owned entries.
  Codex requires a one-time trust review (`/hooks`); `sutra doctor` reports
  `limited` until Codex records that trust and `ok` afterwards.
- Codex hook limits: because Codex truncates large tool output before the hook
  sees it, the adapter caps eligibility at 8,192 bytes with 3,072/1,536-byte
  head and tail budgets. It compresses only that host-supplied text; output
  Codex already discarded is not recoverable. Hook-level tests cover
  replacement, recovery through MCP, evidence-tool bypass, protected shapes,
  malformed JSON and fail-open behavior.
- Codex live evidence (2026-11-01): Codex CLI 0.166.0 with a scripted Responses
  provider executed a real Bash tool call; the hook reduced the model-visible
  result from 10,110 to 4,837 bytes (52%) with a marker and stored original,
  Codex logged `hook: PostToolUse Stopped`, and `sutra restore` returned an
  omitted middle line with `status: ok`. No provider or model was contacted.
- `sutra detect` reports Codex as `automatic` only when the SUTRA hook is
  installed, and `mcp-only` otherwise. `sutra status` and `sutra doctor` report
  the hook path and Codex's recorded trust state.

### Fixed

- A production dispatch after a separate TestPyPI rehearsal rebuilt the same
  tag; archive timestamps differ across runner toolchains, so the new wheel no
  longer matched the bytes TestPyPI served and `verify-testpypi` blocked the
  release. The workflow now accepts `artifact_run_id`, verifies that run was
  dispatched for the release tag and built its commit, downloads that immutable
  `dist` artifact and re-checks `SHA256SUMS` instead of rebuilding. Production
  remains a promotion of exactly the rehearsed bytes.

## 0.3.0 (2026-10-31)

0.3.0 was published to PyPI on 2026-10-31. Entries describe the changes in that
release.

### Added

- **Hermes automatic installer**: `sutra install --client hermes` copies the
  native plugin from the installed package into `$HERMES_HOME/plugins/sutra`,
  validates it with `hermes plugins validate`, and enables it with `hermes
  plugins enable sutra`. Existing SUTRA plugins are backed up; foreign plugins
  and symlinks are refused. `sutra uninstall --client hermes` disables and
  removes only the generated directory. The real-gateway E2E harness remains
  `scripts/e2e/run.sh`.
- **Codex CLI MCP integration**: `sutra install --client codex` uses the
  official `codex mcp add` command, verifies the entry through `codex mcp list
  --json`, refuses a different existing entry, and removes it with `codex mcp
  remove`. This is explicitly **MCP-only**: Codex 0.166.0 parses but rejects
  `updatedMCPToolOutput` and has no supported post-tool output replacement, so
  no automatic-compression claim is made.
- **All-client `sutra doctor`**: reports Claude Code, OpenCode, Hermes, and
  Codex, including `not-installed` rows and Codex's `status=limited`,
  `automatic=no` host limit. A real Codex MCP round trip is covered against an
  isolated `CODEX_HOME`; Hermes' real-gateway proof remains separate.
- **OpenCode automatic adapter**: `sutra install --client opencode` generates
  `~/.config/opencode/plugins/sutra.js` with the installing interpreter and a
  dedicated `~/.sutra` store home baked in. The plugin replaces oversized
  string results through `tool.execute.after` using a short-lived Python
  subprocess (no daemon, port, or token), registers `sutra_restore`, and fails
  open. Generated files are mode 0600, reinstall is idempotent, updates keep a
  timestamped backup, and a foreign file is refused. Verified on OpenCode
  1.19.5 with a real Bash tool and scripted provider: 48,436 B → 18,649 B in
  the model-facing request, marker present, clue recovered through the host's
  tool dispatch. Adapter tests run the generated plugin under Node against the
  real Python CLI; OpenCode itself is not run in CI.
- **Read-only `sutra detect`**: discovers Hermes, Claude Code, OpenCode,
  Cursor, Codex CLI, and Windsurf/Cascade from an executable (with a bounded
  `--version` probe) or a known config file. Reports the concrete evidence,
  selected mechanism, and an honest `automatic` / `mcp-only` / `not installed`
  tier. It never writes, backs up, or installs anything; empty familiar
  directories are not treated as evidence. Supports `--json` and `--client`.
- **OpenCode `sutra doctor` probe**: verifies the generated plugin, the
  embedded interpreter, a real CLI compression/restore round-trip through the
  shared core, and a protected diff that stays byte-identical. When Node is
  available it also exercises the generated `tool.execute.after` contract and
  the plugin's `sutra_restore`. Checks report pass/fail/skip; skipped optional
  checks are `partial` with exit 0, while real failures exit non-zero.
- **Host-agnostic adapter seam** (`core.py`): `Outcome`, `process_text`,
  `process_result`, `restore`, `outcome_json`, and the evidence-tool bypass
  list. The Hermes middleware, CLI, MCP server and Claude Code hook translate
  their native result into the seam and write the returned value back;
  compression, eligibility, size, safety, retention, metrics and recovery stay
  in the engine/store/recovery modules. The CLI and MCP server serialize the
  same `Outcome` instead of re-parsing the marker text to reconstruct the id.
- **Claude Code `PostToolUse` hook** (`scripts/sutra-claude-code-hook.py`):
  replaces a tool result before the model sees it via
  `hookSpecificOutput.updatedToolOutput`; recovery stays on the existing MCP
  `sutra_restore` path. Non-text results pass through; parse, import and
  internal errors fail open; evidence tools are skipped so a second pass is a
  no-op. Hook-level tests invoke the real hook process with a `PostToolUse`
  JSON payload; no live Claude Code session was run (the local CLI is
  unauthenticated).
- **`sutra-hook` console command** (`hook.py`): the Claude Code `PostToolUse`
  adapter now has an installed command that runs the same bundled
  `scripts/sutra-claude-code-hook.py`. Previously the only documented hook
  command was an absolute path into a checkout, which a pip install does not
  have. CI also runs the product suite on macOS; Windows is not covered yet.
- `docs/integrations.md`: an explicit automatic-vs-manual matrix. MCP tool
  registration is documented as manual, not interception.
- Generic adapters over the shared deterministic core — no compression or
  recovery logic duplicated anywhere:
  - **MCP stdio server** (`scripts/sutra-mcp-server.py`, JSON-RPC 2.0,
    protocol `2024-11-05`, standard library only): exposes `sutra_compress`
    and `sutra_restore` to any MCP client. Verified end to end with two real
    clients — OpenCode 1.19.0 and the official
    `@modelcontextprotocol/inspector` reference client — plus protocol tests
    over real pipes in CI. Claude Code 2.1.268 reports the server as
    `✔ Connected` in `claude mcp list` (a health check needs no signed-in
    session); model-backed tool invocation is untested because the local CLI
    is not authenticated (`authMethod: none`).
  - **CLI** (`scripts/sutra-cli.py`): compress / restore / config as JSON on
    stdout; exit codes 0/1/2; every failure carries a machine-readable error.
- Cross-adapter equivalence tests: the engine, the CLI, and the MCP server
    produce identical compression on the same fixture, and each restores the
    exact original (`sha256_verified`).
- **Installable package** (`pyproject.toml`, distribution `sutra-agent`;
  PyPI's `sutra` is an unrelated package): `pip install <checkout>` provides
  the `sutra` and `sutra-mcp` commands, the `sutra` import package, and the
  bundled CLI/MCP scripts, with no dependency outside the standard library.
  A clean-venv install was verified end to end: `sutra_compress` took a
  78,134-byte output to 18,639 bytes, `sutra_restore --mode search` recovered
  the buried clue, and the bounded full restore refused `too_large` as
  designed.
- `sutra install` / `sutra uninstall` (`installer.py`): registers the MCP
  server in OpenCode's config by editing its text — JSONC comments and every
  other server survive, a timestamped backup is written first, `--dry-run`
  writes nothing, a second install is a no-op, and uninstall restores the
  file byte for byte (verified against a copy of a real commented config).
- `docs/integrations.md`: the shared tool contract, per-adapter install and
  usage, and a compatibility matrix that states each client's actual
  verification level.
- CI: a `hyphen-checkout` job that runs the suite from a `sutra-main`
    directory (the GitHub tarball name).
- Correctness regression tests: the exact 24,576-byte threshold boundary,
  byte-identical determinism across calls, chained-traceback handling, and
  multi-error logs (last error visible, earlier errors recoverable by id).

### Fixed

- The production release job could start without a TestPyPI rehearsal:
  `publish-pypi` needed only `build` and `smoke`. It now needs
  `verify-testpypi`, downloads the single checksummed build artifact,
  re-verifies `SHA256SUMS`, and publishes those exact files without a checkout
  or a rebuild. `docs/release.md` describes the promotion path and
  `tests/test_release_workflow.py` enforces it.
- The test suite failed entirely (collection errors) in checkouts whose
  directory name is not a valid Python module name — the default for GitHub
  tarballs and zip archives. The repository root is the plugin package, and
  pytest ≥ 9 imports ancestor package `__init__.py` files at test setup.
- Bench scripts registered the plugin under the test conftest's module name;
  the double registration poisoned `sys.modules` (a second instance whose
  `store` attribute was never re-bound). Bench loaders now use a unique name.
- Malformed MCP `tools/call` params (truthy non-dict `params` or
  `arguments`) terminated the server process; they now answer `-32602`, and
  a last-resort guard ensures no single message can kill the request loop.
- The A/B dry run probed the operator's host before doing any mechanics
  (`hermes plugins list`, measured 26.6 s on a loaded machine), so a dry-run
  test with a 120 s budget could spend it waiting instead of exercising the
  run. A dry run is now hermetic and records an unknown state; the live probe
  is bounded at 120 s by default (over 2× the slowest 44.5 s observed here;
  set it with `--probe-timeout`) and reports "could not read the live plugin
  state" instead of a misleading plugin-mismatch abort.
- The bundled scripts share one package loader, and the `sutra` / `sutra-mcp`
  handover makes it importable: those commands run a script through `runpy`,
  which — unlike a direct execution — does not put `scripts/` on `sys.path`.
  Without the fix the installed commands failed with `No module named
  '_sutra'`; a test now runs the real handover from a directory where only the
  package root is importable.

- The benchmark published best-of-three **minima** and one unlabelled "peak
  allocation" number with no environment, load or raw trials, so a 44.5 ms
  figure and a 567.70 ms observation on the same host could not be reconciled.
  Timing is now split per phase (compress, store write, full rewrite, restore),
  reported as a median with min/max and every trial kept, gated by a load
  policy that defers on a busy machine, and memory is reported as Python heap
  peak and process RSS delta separately.
- A head or tail cut that landed inside a multi-byte UTF-8 character was
decoded with `errors="ignore"`, so up to two bytes vanished from the visible
copy without being counted as omitted, and the marker's `start` or `end`
could point inside a character. Cuts now move to the nearest character
boundary: the visible head is exactly the bytes before `start`, and the
visible tail is exactly the bytes from `end` on.
- The store's `0700` was only applied when it created the directory: a
  pre-existing world-traversable store kept its mode and exposed every entry
  name. An existing directory is now tightened, and one owned by another user,
  a symlink, or a non-directory is refused. A failed metadata write also now
  removes the data file it wrote, instead of leaving an unreadable orphan
  occupying quota until the next sweep.
- Metadata was read with a plain `open` while data reads used `O_NOFOLLOW`, so
  a symlink at an entry's `.json` path redirected the lookup. Both are now
  opened `O_NOFOLLOW`; the stats log got the same treatment, where a planted
  symlink made SUTRA append its JSON records to an unrelated file.

### Documentation

- `docs/benchmark.md` now labels each claim by measurement class: deterministic
  bytes, deterministic recovery, wall-clock phases, tokenizer counts. Timing
  figures are medians of five trials with the raw spread and the run's
  platform, CPU count, RAM, load and git SHA recorded; a loaded run is marked
  deferred instead of published as representative. The historical
  2026-10-25 artifacts are preserved under `bench/reference/` with hashes.
- README restructured as an entry point (positioning up front, installation
  verification, guarantees stated once, benchmark details moved to
  `docs/benchmark.md`; full troubleshooting in `docs/troubleshooting.md`).
- `docs/contract.md`: the correctness contract, guarantee by guarantee, with
  the test that enforces each.

## v0.2.0

Recovery store, `sutra_restore`, markers with ids and offsets, 0.2.0
hardening. See the
[v0.2.0 release notes](https://github.com/g0GobliN/sutra/releases/tag/v0.2.0).

## 0.6.0 - 2026-11-11

### Added
- **Phase Six documentation completion.** All architecture, migration, configuration,
  recovery, benchmark and store operations guides completed with verified metrics.
- **CLI `--max-tokens` acceptance criterion removed from goal.md.** The implementation
  only has environment variable `SUTRA_MAX_TOKENS`; the CLI flag was never implemented.

### Fixed
- Foreign text removed from architecture documentation (Cyrillic line 133, Korean line 146).
- Non-existent test file reference corrected (test_hermes_plugin.py → test_hermes_adapter.py).
- README install example updated to match released version (sutra 0.6.0).
- CHANGELOG updated to include 0.6.0 release.
