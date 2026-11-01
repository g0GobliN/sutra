# Changelog

## 0.4.2 (unreleased)

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
