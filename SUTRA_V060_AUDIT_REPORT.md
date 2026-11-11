# SUTRA v0.6.0 — Architecture and Release-Gap Audit Report

**Repository:** g0GobliN/sutra (`/home/goblin01/github_projects/tooltrim`)
**Branch:** `feat/evidence-driven-output-intelligence` (HEAD: 5bb0aed)
**Version:** `0.6.0` (already set in `sutra/__init__.py`, `pyproject.toml` uses dynamic version)
**Tags:** v0.2.0 – v0.5.0 exist; **no v0.6.0 tag**
**PR:** #75 open on this branch
**Status:** Not pushed, not tagged, not published (release safety rules respected)

---

## Phase One — Architecture Audit

| Area | Existing implementation | Verified status | Remaining work |
|------|------------------------|-----------------|----------------|
| Core engine | `engine.py`, `core.py`, `config.py`, `parsers.py` | Verified | None — deterministic compression, fail-open, format preservation all implemented and tested |
| CLI | `cli.py` — `detect`, `status`, `install`, `doctor`, `uninstall`, `restore` | Verified | None |
| Installer | `installer.py`, `discovery.py` | Verified | None |
| Store/recovery | `store.py`, `recovery.py` | Verified | None |
| Hermes | `hermes.py` | Verified (live host tested, v0.22.2+) | None |
| OpenCode | `opencode.py` | Verified (live host tested, 1.19.5) | None |
| OpenClaw | `openclaw.py` | Adapter exists, unit tests pass; live host verification pending | Label accurately — do not claim fully verified without live host |
| Claude Code | `claude_code.py` | Verified (live host tested, 2.1.268) | None |
| Codex CLI | `codex.py` | Verified (live host tested, 0.166.0) | None |
| Windows | `os.name == "nt"` skip added to `tests/test_store.py:144` (uncommitted) | Partial — CI not run on Windows | Native PowerShell install docs needed; Windows CI coverage |
| macOS | CI runs on macOS | Verified | None |
| Linux | CI runs on Linux (3.11–3.14) | Verified | None |

## Release-Gap Analysis

### What already exists (reused)
- Compression engine (`engine.py`) with ANSI stripping, dedupe, head-tail, JSON field compression, traceback preservation, fail-open
- Storage (`store.py`) — bounded, permission-restricted, atomic writes, TTL, sweep
- Recovery (`recovery.py`) — `sutra_restore` protocol with statuses ok/not_found/expired/too_large/unavailable/error
- CLI (`cli.py`) — detect/status/install/doctor/uninstall/restore
- Installer (`installer.py`) — backup, dry-run, idempotent, per-client
- Discovery (`discovery.py`) — read-only agent detection
- 5 structured CLI output parsers (`parsers.py`) — 16 families
- Adapters for all 5 target agents (Hermes, OpenCode, OpenClaw, Claude Code, Codex CLI)
- MCP stdio server (`mcp.py`), CLI scripts, hook scripts
- Extensive documentation (README, docs/*.md)
- Test suite: 583 passed, 1 skipped, 1 failed
- CI workflow (`.github/workflows/ci.yml`) — Linux, macOS
- Version already 0.6.0; CHANGELOG has 0.6.0 entry

### What needs fixes
1. **`test_store_many_puts_stay_under_quota`** — takes 40.7s, exceeds 30s quota. CI failure on this branch.
2. **`tests/test_store.py`** — `os.name == "nt"` skip added (uncommitted change, not yet committed)
3. **verify_dist.py** — `parsers` already listed in `RUNTIME_MODULES`; this was already fixed
4. **`os.geteuid()`** — already guarded with `hasattr(os, "geteuid")` in `test_store.py:146`

### What is genuinely missing
- **PR #75 description** — drafted at `/home/goblin01/.hermes/cache/scratch/pr_description.md` (62 lines, human-like structured style) but NOT committed or posted to GitHub
- **OpenClaw live verification** — adapter + unit tests exist, but no live OpenClaw host available; should be labeled accurately per mission rule
- **Windows native PowerShell install instructions** — need verification against actual entry-point config
- **v0.6.0 tag** — not created (and must not be created without authorization)

## OpenClaw Discrepancy Check

- README line 98 lists OpenClaw as "automatic"
- README line 243 compatibility matrix lists OpenClaw as "Automatic — generated tool result plugin"
- docs/integrations.md line 69 says "OpenClaw interception: pending — OpenClaw uses a generated plugin + MCP recovery path (sutra install --client openclaw), not the Hermes tool_execution middleware. Status pending verification after sentinel's doctor passes."
- `openclaw.py` exists (24,997 bytes), `tests/test_openclaw_adapter.py` and `tests/test_openclaw_doctor.py` exist
- No `openclaw` binary on this host, so live integration verification cannot be completed here
- **Recommendation:** Label OpenClaw as "adapter exists + automated tests pass; live integration not verified on this host" — do not claim "Tested with the actual host"

## CI Status

- Linux (3.11–3.14): pass
- macOS: pass
- Windows: not covered
- Lint: clean
- `package` job: `verify_dist.py` already includes `parsers` in RUNTIME_MODULES (fixed)
- **Failing:** `test_store_many_puts_stay_under_quota` — 40.7s vs 30s quota on this machine

## Security / Private Files

- No SOUL.md, goal.md, idea.md, bench/corpus.py, etc. committed (removed from tracking, in .gitignore)
- `tests/test_store.py` has an uncommitted change (Windows skip) — needs commit
- No credentials or secrets in the repository

## Recommended Next Steps (Priority Order)

1. Fix `test_store_many_puts_stay_under_quota` quota (increase bound or optimize store sweep) — unblocks CI
2. Commit the uncommitted `tests/test_store.py` Windows skip change
3. Commit and post PR #75 description from `/home/goblin01/.hermes/cache/scratch/pr_description.md`
4. Label OpenClaw accurately in docs (adapter + tests pass; live host verification pending)
5. Prepare v0.6.0 release report for approval (do NOT tag/publish/push without authorization)
