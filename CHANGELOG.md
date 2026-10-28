# Changelog

## Unreleased (since v0.2.0)

### Added

- Generic adapters over the shared deterministic core — no compression or
  recovery logic duplicated anywhere:
  - **MCP stdio server** (`scripts/sutra-mcp-server.py`, JSON-RPC 2.0,
    protocol `2024-11-05`, standard library only): exposes `sutra_compress`
    and `sutra_restore` to any MCP client. Verified end to end with two real
    clients — OpenCode 1.19.0 and the official
    `@modelcontextprotocol/inspector` reference client — plus protocol tests
    over real pipes in CI. Claude Code verified to configuration level
    (registration recognized by `claude mcp get`; end-to-end pending an
    authenticated session).
  - **CLI** (`scripts/sutra-cli.py`): compress / restore / config as JSON on
    stdout; exit codes 0/1/2; every failure carries a machine-readable error.
- Cross-adapter equivalence tests: the engine, the CLI, and the MCP server
    produce identical compression on the same fixture, and each restores the
    exact original (`sha256_verified`).
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
  is bounded at 120 s (over 2× the slowest 44.5 s observed here) and reports
  "could not read the live plugin state" instead of a misleading
  plugin-mismatch abort.

### Documentation

- README restructured as an entry point (positioning up front, installation
  verification, guarantees stated once, benchmark details moved to
  `docs/benchmark.md`; full troubleshooting in `docs/troubleshooting.md`).
- `docs/contract.md`: the correctness contract, guarantee by guarantee, with
  the test that enforces each.

## v0.2.0

Recovery store, `sutra_restore`, markers with ids and offsets, 0.2.0
hardening. See the
[v0.2.0 release notes](https://github.com/g0GobliN/sutra/releases/tag/v0.2.0).
