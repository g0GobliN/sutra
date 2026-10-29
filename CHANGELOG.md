# Changelog

## Unreleased (since v0.2.0)

### Added

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
