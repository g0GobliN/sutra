# SUTRA v0.6.0 — Phase Eight Release Report

**Repository:** g0GobliN/sutra (`/home/goblin01/github_projects/tooltrim`)
**Branch:** `feat/evidence-driven-output-intelligence`
**HEAD:** `1bf5b69` ("fix: add in-process index to Store sweeps to meet 30s quota")
**Version:** `0.6.0` (`sutra/__init__.py`, `plugin.yaml`, `pyproject.toml` dynamic via `sutra.__version__`)
**Distribution name on PyPI:** `sutra-agent` (import package and console commands stay `sutra`)
**Tags:** `v0.2.0`–`v0.5.0` exist; **no `v0.6.0` tag**
**Safety rules observed:** no tag created, no push, no PyPI publish.

Host used for verification: Linux, Python 3.13.5 (`/usr/bin/python3`); no Windows host available, so Windows commands below are **planned/exact commands from CI and docs, not executed here**.

---

## 1. Release status

**Not ready for publication.**

- Current version: `0.6.0`, set and consistent in `sutra/__init__.py` (`__version__ = "0.6.0"`), `plugin.yaml` (`version: "0.6.0"`), and `pyproject.toml` (`version = { attr = "sutra.__version__" }`).
- Proposed `v0.6.0` status: **not tagged, not pushed, not published**. Tags `v0.2.0`–`v0.5.0` exist; `v0.6.0` must not be created without authorization.
- Last recorded CI on this branch: RED (`593 passed / 3 failed / 1 skipped` on `b9a1b76`, 3 version/performance failures).
- Local re-run on current HEAD `1bf5b69` (which includes the Store quota fix): **596 passed / 1 skipped / 0 failed** (`python3 -m pytest tests -q`, 244.46s). CI has **not** been re-run on current HEAD; the Windows job has never run here (no Windows host).
- Release gate per `docs/release.md`: PR merged with every CI job green (Python 3.11–3.14, macOS, Windows; `ruff check .`; `hermes plugins validate .`; `package` job; hyphen-checkout job), then tag, then TestPyPI rehearsal, then production promotion through the protected `pypi` environment. None of the post-merge steps have been started.

## 2. What already existed (features reused, needed fixes, genuinely missing)

### Already existed and was reused
- Compression engine (`engine.py`, `core.py`): deterministic compression, ANSI stripping, dedupe, head–tail, JSON field compression, traceback preservation, fail-open.
- Storage/recovery (`store.py`, `recovery.py`): bounded store, permission-restricted atomic writes, TTL, sweep; `sutra_restore` protocol (`ok`/`not_found`/`expired`/`too_large`/`unavailable`/`error`).
- CLI (`cli.py`): `detect`, `status`, `install`, `doctor`, `uninstall`, `restore`, `--version`.
- Installer (`installer.py`, `discovery.py`): per-client install/uninstall, timestamped backups, `--dry-run`, idempotence, foreign-file refusal, read-only detection.
- Five structured CLI output parsers (`parsers.py`, 16 families).
- Console commands `sutra`, `sutra-mcp`, `sutra-hook` declared in `pyproject.toml` (`[project.scripts]`).
- MCP stdio server (`mcp.py`) and bundled adapter scripts (`scripts/sutra-mcp-server.py`, `scripts/sutra-cli.py`, `scripts/sutra-claude-code-hook.py`, `scripts/sutra-codex-hook.py`).
- Adapters for Hermes Agent, OpenCode, OpenClaw, Claude Code, Codex CLI.
- Documentation: README, `docs/*.md`, `SECURITY.md`.
- CI workflow (`.github/workflows/ci.yml`): Linux + macOS product suite, Windows job, lint, `package` job, hyphen-checkout job.
- Version already `0.6.0`; `CHANGELOG.md` has a `## 0.6.0 - 2026-11-11` entry.

### Needed fixes (completed on this branch)
- `test_store_many_puts_stay_under_quota` exceeded the 30s quota (40.7s → ~7s via an in-process entry index in `Store._Accounting`).
- `tests/test_store.py`: Unix permission test skipped on Windows (`os.name == "nt"`).
- `scripts/verify_dist.py`: `parsers` missing from `RUNTIME_MODULES` (package job now passes).
- Lint: `F841` in `tests/test_parsers.py`; version aligned to `0.6.0` (`__init__.py`, `plugin.yaml`).
- `sutra install --dry-run` no longer invokes the Claude CLI (dry runs never create `~/.claude.json`).
- `CHANGELOG.md` / `README.md` install example updated to `sutra 0.6.0`.

### Genuinely missing
- Windows host access: native PowerShell install workflow has not been executed here; only CI commands and `docs/release.md` post-publication steps can be reported.
- `v0.6.0` tag (barred by safety rules), push, and PyPI publish.
- Live OpenClaw host verification: adapter and unit tests exist; no `openclaw` binary on this host (label accurately, do not claim live verification).
- `ruff` is not installed locally, so `ruff check .` could not be run here (CI runs `ruff==0.17.2`).
- `py` Python launcher availability cannot be verified on Linux (Windows-only launcher; CI uses `python` from `actions/setup-python`, not `py`).

## 3. What changed (files changed, features implemented, bugs fixed, documentation updated)

### Files changed vs `v0.5.0`
129 files changed (`+22,544 / −84` net, `git diff v0.5.0..HEAD --stat`).

Source/runtime changed:
- `__init__.py`, `config.py`, `core.py`, `engine.py`, `parsers.py`, `store.py`
- `cli.py`, `mcp.py`, `hook.py`, `installer.py`, `discovery.py`, `runner.py`: **unchanged** since `v0.5.0` (flat root layout, confirmed present)

Scripts changed:
- `scripts/ab/report.py`, `scripts/ab/run.py` (new evidence-driven output-intelligence runner)
- `scripts/enroll_pypi_credential.py`, `scripts/enroll_testpypi_credential.py` (new)
- `scripts/verify_dist.py` (Windows/`parsers` fix)

Tests changed:
- `tests/conftest.py`, `tests/test_cli_adapter.py`, `tests/test_cli_main.py`, `tests/test_config.py`, `tests/test_core_seam.py`, `tests/test_performance.py`, `tests/test_store.py`
- New: `tests/test_ab_runner.py`, `tests/test_parsers.py`

Documentation changed:
- `docs/architecture.md`, `docs/configuration.md`, `docs/migration.md`, `docs/recovery.md`, `docs/store.md`, `docs/versioning.md`
- `CHANGELOG.md` (`## 0.6.0 - 2026-11-11`), `README.md` install example, `SECURITY.md`
- New: `STANDING-RULES.md`, `.github/PR_DESCRIPTION.md` (committed; PR #75 not yet posted to GitHub)
- `.gitignore` (internal/local-only files removed from tracking: `SOUL.md`, `goal.md`, `idea.md`, `bench/corpus.py`, bench fixtures, `compressed.json`, `detect.txt`, `doctor.json`, `restored.json`)

### Features implemented
- Structured parser framework (`parsers.py`, 16 families) with CLI `parse` subcommand.
- Evidence-driven output-intelligence tooling (`scripts/ab/`, `tests/test_ab_runner.py`).
- OpenClaw adapter (`openclaw.py`) with unit tests (`tests/test_openclaw_adapter.py`, `tests/test_openclaw_doctor.py`); live host verification pending.
- PyPI Trusted Publishing credential enrollment scripts; `package` job now builds sdist + wheel and passes `twine check --strict`.

### Bugs fixed
- Store sweep quota (28.97s → ~7s, under 30s).
- Windows CI compatibility (`test_store.py` skip, `verify_dist.py` `parsers` entry).
- Dry-run Claude CLI side effect; version alignment `0.6.0`; `F841` lint.

## 4. Integration verification (per agent)

| Agent | Adapter | Automated tests | Real-host verification | Supported versions | Platform limitations |
|---|---|---|---|---|---|
| Hermes Agent | `hermes.py` + `scripts/sutra-mcp-server.py` | unit-tested | Live gateway E2E, Hermes 0.22.5 (2026-10-31): 38,925 B → 22,597 B, marker present, `sutra_restore` `ok: true` | Python 3.11–3.14 | E2E harness `scripts/e2e/run.sh` is local-only |
| OpenCode | `opencode.py` + generated `sutra.js` plugin | unit-tested (Node + real Python CLI) | Live OpenCode 1.19.5 (2026-10-31): 48,436 B → 18,649 B, marker present, clue recovered | Python 3.11–3.14 | OpenCode itself not run in CI |
| OpenClaw | `openclaw.py` | unit tests pass | **Not verified** — no `openclaw` binary on this host | Python 3.11–3.14 | Label as adapter + tests only; do not claim live verification |
| Claude Code | `claude_code.py` + `scripts/sutra-claude-code-hook.py` | hook-level tests (real hook process) | Live Claude Code 2.1.268 (2026-10-29/30): 70,888 B → 18,645 B, marker present, restore via MCP `ok` | Python 3.11–3.14 | Live probe Linux-only; `O_NOFOLLOW` omitted where unsupported; handler schema verified on 2.1.268 only |
| Codex CLI | `codex.py` + `scripts/sutra-codex-hook.py` | unit-tested | Live Codex 0.166.0 (2026-11-01): 10,110 B → 4,837 B, marker present, hook logged `Stopped`, restore `ok` | Python 3.11–3.14 | Requires one-time `/hooks` trust review; `doctor` reports `limited` until trusted |

- Automated tests: full suite **596 passed / 1 skipped / 0 failed** locally on Python 3.13.5.
- CI matrix: Python 3.11–3.14 plus macOS and Windows jobs; `package` job builds sdist/wheel, runs `twine check --strict`, validates archive contents, installs the wheel in a clean environment, and exercises `detect`/`doctor`/`status` and all four adapter dry runs plus an OpenCode install/reinstall/uninstall round trip.
- `sutra --version` → `sutra 0.6.0` (verified locally).
- Wheel entry points verified in built artifact `entry_points.txt`: `sutra = sutra.cli:main`, `sutra-hook = sutra.hook:main`, `sutra-mcp = sutra.mcp:main`.
- `twine check --strict dist/*`: PASSED for both `sutra_agent-0.6.0-py3-none-any.whl` and `sutra_agent-0.6.0.tar.gz`; `scripts/verify_dist.py dist` → OK, "a sutra-agent 0.6.0 release".

## 5. Windows verification

No Windows host is available in this environment, so **no Windows command was executed**. The commands below are exact commands taken from `.github/workflows/ci.yml` (Windows job, `windows-latest`, `pwsh`) and from `docs/release.md` post-publication verification, adapted where noted.

### CI Windows job — exact commands (from `ci.yml`, unexecuted here)

```pwsh
python -m build
python -m venv venv
$wheel = (Get-ChildItem dist\*.whl | Select-Object -First 1).FullName
.\venv\Scripts\python -m pip install --quiet $wheel
.\venv\Scripts\sutra --version
.\venv\Scripts\python -c "import sutra; print(sutra.__version__)"
```

Followed by a `pwsh` adapter block (`$ErrorActionPreference = "Stop"`, isolated `$env:CLI_HOME`/`$env:HOME`, then `.\venv\Scripts\sutra detect`, `sutra doctor --json`, `sutra install --client opencode --dry-run`, `sutra uninstall --client opencode --dry-run`, and a real OpenCode install/reinstall/uninstall round trip).

### Where `sutra.exe` would be created

- CI installs into a **project-local** venv: `.\venv\Scripts\sutra.exe` (and `.\venv\Scripts\sutra-mcp.exe`, `.\venv\Scripts\sutra-hook.exe`).
- The `$HOME\.sutra-venv\Scripts\sutra.exe` path applies only to the post-publication verification flow in `docs/release.md` (`python3 -m venv ~/.sutra-venv`). On Windows PowerShell the equivalent is:

```pwsh
python -m venv $HOME\.sutra-venv
$HOME\.sutra-venv\Scripts\python -m pip install sutra-agent
& "$HOME\.sutra-venv\Scripts\sutra.exe" --version
& "$HOME\.sutra-venv\Scripts\sutra.exe" install --dry-run
```

(`$HOME` in Windows PowerShell resolves to `%USERPROFILE%`, e.g. `C:\Users\<user>\.sutra-venv\Scripts\sutra.exe`.)

### `py` launcher assumption

- `command -v py` → **not found** on this Linux host (expected: `py` is Windows-only).
- The CI workflows do **not** depend on `py`; they use `python` from `actions/setup-python`.
- Any documented Windows flow that assumes `py -m venv` / `py -m pip` needs adjustment: substitute `python -m venv` / `python -m pip` (or `py -3 -m ...` only after confirming the launcher is present on the target Windows host).
- **Verdict:** `sutra.exe` at `$HOME\.sutra-venv\Scripts\sutra.exe` is plausible on Windows if the user follows the `~/.sutra-venv` flow, but CI's actual venv location is `.\venv\Scripts\sutra.exe`; the `py` launcher assumption is unverifiable on Linux and should not be relied on without a Windows host.

**Result / CLI / Integration:** untested on Windows (no host). Installation result, CLI result, and integration result can only be asserted from CI job definitions, not from execution.

**Remaining limitations:** Windows CI job has never run on this branch here; `py` launcher unverified; `$HOME\.sutra-venv` flow untested.

## 6. Test evidence

Command executed locally:
```
python3 -m pytest tests -q
```
Environment: Linux, Python 3.13.5, pytest 9.2.0.
Result: **596 passed, 1 skipped, 0 failed** in 244.46s (~4 min). The single skip is a pre-existing environment-conditional skip.

CI results (last recorded, branch `feat/evidence-driven-output-intelligence` @ `b9a1b76`):
- Overall: RED — **593 passed / 3 failed / 1 skipped** (3 version/performance failures).
- Failures included `test_store_many_puts_stay_under_quota` (40.7s > 30s quota).
- Current HEAD `1bf5b69` adds the Store sweep index (28.97s → ~7s); local suite above confirms the fix, but CI has not been re-run on current HEAD.
- Phase Five (prior): 30 new tests across `tests/test_cli_main.py` (13), `tests/test_store.py` (12), `tests/test_performance.py` (5); full suite was 566/1 → 596/1.
- `python3 -m twine check --strict dist/*`: PASSED (both archives).
- `python3 scripts/verify_dist.py dist`: OK — valid sutra-agent 0.6.0 release archives.
- `python3 -m build`: successfully produced `dist/sutra_agent-0.6.0.tar.gz` and `dist/sutra_agent-0.6.0-py3-none-any.whl`.
- `ruff check .`: **not run locally** (`ruff` not installed); CI pins `ruff==0.17.2`.

## 7. Security and regression review

- **Backup/rollback:** every installer edit is timestamped (`.bak-*`) before writing; uninstall removes only SUTRA-owned entries and restores previous state; foreign existing config entries are refused rather than overwritten; installs are idempotent and support `--dry-run`.
- **Config preservation:** `detect` is read-only (no backups, no config edits); `doctor` creates no files and makes no backups in user config; unrelated hooks/settings/MCP servers are preserved.
- **Fail-open:** adapters fail open on parse/import/internal errors (original result reaches the model); OpenCode plugin subprocess failure is fail-open; hook errors return `{}` and leave tool results untouched; evidence tools are bypassed to prevent double passes.
- **Private-file checks:** internal/local-only files (`SOUL.md`, `goal.md`, `idea.md`, `bench/corpus.py`, bench fixtures, probe artifacts) removed from tracking and gitignored; no credentials or secrets in the repository; `.env`/`state.db` in the user home are outside the repo.
- **Distribution security:** PyPI uploads use GitHub OIDC Trusted Publishing (no long-lived tokens); the `pypi` environment is protected by required reviewer approval; TestPyPI rehearsal is mandatory before production; `verify_dist.py` rejects tests/caches/benchmarks/probe trees and absolute local paths, requires `License-Expression: MIT`, confirms dependency-free metadata and the three console entry points.
- **Regression results:** local suite 596/1/0; previous CI RED (593/3/1 on `b9a1b76`) traced to quota + version/performance failures, all addressed on current HEAD except CI re-run. Version/license checks: `__init__.py`, `plugin.yaml`, and `pyproject.toml` all agree on `0.6.0`.

## 8. Remaining blockers

- **No Windows host:** Windows install workflow, `sutra.exe` creation, and the `py` launcher assumption cannot be executed here; reported from CI/docs only.
- **`v0.6.0` tag not created** (safety rules: no tag, no push, no PyPI publish without authorization).
- **CI not re-run on current HEAD** `1bf5b69`: local suite is green (596/1/0), but the Windows/macOS/Linux CI jobs and `package` job have not passed on this commit.
- **OpenClaw live verification pending** (no host); must not be labeled as live-tested.
- **`ruff` unavailable locally:** lint gate cannot be self-verified here.
- **Release report approval:** this document itself awaits review before the release can proceed.
- **PyPI publication gated** on TestPyPI rehearsal success + protected `pypi` environment reviewer approval.

Risks to release readiness: if CI re-run on HEAD fails any job (especially Windows `package`/adapter round trip), the tag must not be created; the changelog `## 0.6.0 - 2026-11-11` heading is dated while `docs/release.md` convention keeps headings `(unreleased)` until publication — minor documentation nit to align at tag time.

## 9. Release recommendation

**Not ready for approval yet.**

Exact remaining steps before publishing (in order, none of which may be performed under current safety rules without authorization):

1. Re-run CI on current HEAD `1bf5b69`; require all jobs green (Linux 3.11–3.14, macOS, Windows, lint `ruff check .`, `hermes plugins validate .`, `package`, hyphen-checkout).
2. Maintainer creates tag `v0.6.0` on the merged commit (the merged tree is what users receive; build refuses when tag, `__init__.py`, and `plugin.yaml` disagree).
3. GitHub Actions → Release → Run workflow: `tag=v0.6.0`, `target=testpypi` (TestPyPI rehearsal: build once, `twine check --strict`, validate archives, install sdist on 3.11/3.12/3.13, exercise `sutra --version`/`config`/`compress`/`restore`/`sutra-hook`/`status --json`, publish to TestPyPI, install-back and compare served bytes with `SHA256SUMS`).
4. Production: same tag, `target=pypi`, `artifact_run_id` set to the successful rehearsal run id (promotion of the rehearsed bytes, no rebuild); `publish-pypi` waits for the protected `pypi` environment reviewer approval; then OIDC upload of wheel + sdist + signed attestation.
5. Post-publication: verify PyPI page renders, install `sutra-agent==0.6.0` from PyPI in a clean environment, run `sutra --version` and `sutra install --dry-run`, compare served hashes with `SHA256SUMS`.

**Do not:** create the `v0.6.0` tag, push, or publish to PyPI until CI is green on HEAD and the maintainer approves. This report creates no tag, no commit, and no publish.
