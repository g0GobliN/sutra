# SUTRA v0.6.0 — Verified Architecture and Release-Gap Audit

**Repository:** g0GobliN/sutra (`/home/goblin01/github_projects/tooltrim`)  
**Branch:** `feat/evidence-driven-output-intelligence` (HEAD: `2fc5328`)  
**Version:** `0.6.0` (in `sutra/__init__.py`, `plugin.yaml`, `pyproject.toml` dynamic)  
**Tags:** `v0.2.0`–`v0.5.0` exist; **no `v0.6.0` tag**  
**PR:** #75 open (description drafted, not yet posted)  
**Safety:** Not pushed, not tagged, not published (release rules respected)

---

## Phase One — Architecture Audit (Evidence-Based)

| Area | Existing Implementation | Verified Status | Remaining Work |
|------|------------------------|-----------------|----------------|
| **Core Engine** | `engine.py`, `core.py`, `config.py`, `parsers.py` | **Verified** — deterministic compression, ANSI stripping, dedupe, head-tail truncation, JSON field compression, traceback preservation, fail-open, token ceiling, per-call config resolution with warn-once fallback | None |
| **CLI** | `cli.py` — `detect`, `status`, `install`, `doctor`, `uninstall`, `restore`, `parse` | **Verified** — all commands execute, read-only detection works, dry-run previews correctly, uninstall removes only SUTRA-owned config | None |
| **Installer** | `installer.py`, `discovery.py` | **Verified** — timestamped backups (`.bak-*`), idempotent, per-client, `--dry-run`, refuses foreign config entries, preserves unrelated hooks/MCP | None |
| **Store/Recovery** | `store.py`, `recovery.py` | **Verified** — bounded store (quota 30s sweep fixed to ~7s), atomic writes, TTL, `0600` files, `0700` dirs, `sutra_restore` protocol (ok/not_found/expired/too_large/unavailable/error) | None |
| **Parsers** | `parsers.py` — 16 families (Jest, Vitest, Pytest, Cargo, Go, npm/pnpm/Yarn, Docker, git, compiler errors, dep failures, long traces, repeated output, large JSON) | **Verified** — all 5 structured parsers tested, CLI `parse` subcommand works | None |
| **Hermes** | `hermes.py` + `scripts/sutra-mcp-server.py` | **Verified (Live)** — native `tool_execution` middleware, E2E tested on Hermes 0.22.5/0.22.7, 38.9KB → 22.6KB, marker present, `sutra_restore` ok | None |
| **OpenCode** | `opencode.py` + generated `sutra.js` plugin | **Verified (Live)** — `tool.execute.after` plugin + MCP recovery, tested on OpenCode 1.19.5, 48.4KB → 18.6KB, marker present, clue recovered | None |
| **OpenClaw** | `openclaw.py` | **Adapter Exists — Live Verification Pending** — unit tests pass (`test_openclaw_adapter.py`, `test_openclaw_doctor.py`); **no `openclaw` binary on this host**; cannot verify plugin loading/host integration | Label accurately — do not claim live verification |
| **Claude Code** | `claude_code.py` + `scripts/sutra-claude-code-hook.py` | **Verified (Live)** — `PostToolUse` hook + MCP recovery, tested on 2.1.268, 70.9KB → 18.6KB, marker present, restore via MCP ok | None |
| **Codex CLI** | `codex.py` + `scripts/sutra-codex-hook.py` | **Verified (Live)** — `PostToolUse` feedback hook + MCP recovery, tested on 0.166.0, 10.1KB → 4.8KB, marker present, hook logged `Stopped`, restore ok; requires one-time `/hooks` trust | None |
| **Windows** | `os.name == "nt"` skip in `test_store.py:143`; CI job defined | **Partial** — CI not run on Windows here; `py` launcher unverified; no native PowerShell flow executed | Native PowerShell install docs + Windows CI verification |
| **macOS** | CI runs on macOS | **Verified** — CI green | None |
| **Linux** | CI runs on Linux (3.11–3.14) | **Verified** — CI green, local 596 passed/1 skipped | None |

---

## Release-Gap Analysis

### What Already Exists (Reused)
- Compression engine with all required properties (deterministic, fail-open, format-preserving)
- Bounded, permission-restricted storage with atomic writes and sweep
- Recovery protocol with explicit statuses
- Full CLI surface (`detect`, `status`, `install --dry-run`, `install`, `doctor`, `uninstall`, `restore`, `parse`)
- Per-client installer with backups, dry-run, idempotence
- Read-only agent discovery
- 16 parser families for structured CLI output
- Adapters for all 5 target agents (Hermes, OpenCode, OpenClaw, Claude Code, Codex)
- MCP stdio server, hook scripts, CLI entry points
- Extensive documentation (README, `docs/*.md`, `SECURITY.md`)
- Test suite: **596 passed, 1 skipped, 0 failed** locally
- CI workflow: Linux (3.11–3.14), macOS, Windows job, lint, `package` job, hyphen-checkout
- Version `0.6.0` consistent across `__init__.py`, `plugin.yaml`, `pyproject.toml`
- `CHANGELOG.md` has `## 0.6.0 - 2026-11-11` entry

### What Needed Fixes (Completed on This Branch)
1. **Store sweep quota** — `test_store_many_puts_stay_under_quota` exceeded 30s (40.7s → ~7s via in-process entry index in `Store._Accounting`)
2. **Windows CI compatibility** — `os.name == "nt"` skip for Unix permission test; `os.geteuid()` guard
3. **`verify_dist.py`** — `parsers` added to `RUNTIME_MODULES` (package job now passes)
4. **Lint** — `F841` in `tests/test_parsers.py`; version aligned to `0.6.0`
5. **Dry-run Claude side effect** — no longer invokes `claude` CLI during dry-run
6. **Internal files removed** — `SOUL.md`, `goal.md`, `idea.md`, `bench/corpus.py`, bench fixtures, probe artifacts removed from tracking and gitignored

### What Is Genuinely Missing
- **Windows host access** — native PowerShell install workflow not executed; only CI commands/docs available
- **`v0.6.0` tag** — barred by safety rules (no tag/push/publish without authorization)
- **OpenClaw live verification** — adapter + unit tests exist; no `openclaw` binary available
- **`ruff` locally** — lint gate cannot be self-verified (CI pins `ruff==0.17.2`)
- **`py` launcher** — Windows-only, unverifiable on Linux
- **PR #75 description** — drafted at `.hermes/cache/scratch/pr_description.md` but not committed/posted

---

## OpenClaw Discrepancy Check

| Source | Claim | Reality |
|--------|-------|---------|
| README line 98 | "automatic" | ❌ Incorrect — pending live verification |
| README line 243 | "Automatic — generated tool result plugin" | ❌ Incorrect — adapter exists, tests pass, no live host |
| `docs/integrations.md` line 69 | "pending — status pending verification after sentinel's doctor passes" | ✅ Accurate |
| `openclaw.py` | Exists (24,997 bytes) | ✅ Adapter implemented |
| `test_openclaw_adapter.py` / `test_openclaw_doctor.py` | Exist | ✅ Unit tests pass |

**Recommendation:** Label OpenClaw as **"Adapter exists + automated tests pass; live integration not verified on this host"** — do not claim "Tested with the actual host."

---

## CI Status

| Platform | Status |
|----------|--------|
| Linux (3.11–3.14) | ✅ Pass (last run) |
| macOS | ✅ Pass (last run) |
| Windows | ❌ Not run on this branch/HEAD here |
| Lint (`ruff==0.17.2`) | ✅ Clean (CI) |
| `package` job | ✅ Pass (after `parsers` fix in `verify_dist.py`) |
| Hyphen-checkout | ✅ Pass |

**Local re-run on HEAD `2fc5328`:** **596 passed, 1 skipped, 0 failed** (244s) — confirms Store quota fix works.

---

## Security / Private Files

- No `SOUL.md`, `goal.md`, `idea.md`, `bench/corpus.py`, bench fixtures, probe artifacts committed (removed, gitignored)
- No credentials or secrets in repository
- Distribution security: PyPI via GitHub OIDC Trusted Publishing, TestPyPI rehearsal mandatory, `pypi` environment protected by reviewer approval

---

## Recommended Next Steps (Priority Order)

1. **Commit & post PR #75 description** from `.hermes/cache/scratch/pr_description.md` to GitHub PR #75
2. **CI re-run on current HEAD** `2fc5328` — require all jobs green (Linux 3.11–3.14, macOS, Windows, lint, `package`, hyphen-checkout)
3. **Label OpenClaw accurately** in README and docs (adapter + tests only; no live verification)
4. **Prepare native PowerShell install docs** based on actual entry-point config (`sutra.exe` at `venv\Scripts\sutra.exe` in CI, `$HOME\.sutra-venv\Scripts\sutra.exe` for user flow)
5. **Final release-readiness audit** — then await explicit authorization before tag/push/publish

---

## Final Note

SUTRA v0.6.0 architecture is **complete and verified** for all agents except OpenClaw (live host pending). The universal installation experience (`detect` → `status` → `install --dry-run` → `install` → `doctor` → `uninstall`) is implemented and tested. Core engine, storage, recovery, parsers, CLI, installer, and 4 of 5 adapters are live-verified. Windows CI gap and OpenClaw live verification are the only remaining functional gaps. All safety rules respected — no tag, no push, no publish.