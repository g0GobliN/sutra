# OpenCode host-experiment artifacts (prototype-grade)

Files behind the two feasibility reports in this directory's parent. These are
experiment records, not production code: scratch directories, no packaging. OpenCode 1.19.0; every run was isolated from the user's own
OpenCode setup via `XDG_CONFIG_HOME` / `XDG_DATA_HOME`.

| File | Role | Used by |
|---|---|---|
| `sutra-proto.js` | the feasibility plugin (Phase 1: `tool.execute.after` replacement only) | output-replacement report |
| `sutra-recovery-plugin.js` | the Phase 2 plugin: replacement + registered `sutra_restore` tool (`tool()` helper, `@opencode-ai/plugin`) | recovery-registration report |
| `driver.py` | subprocess bridge to the unmodified engine; `compress` (adapter path, persistent store), `restore`, `seed-expired` | both |
| `fake_model.py` | scenario-driven scripted provider (127.0.0.1:8909); logs offered tools and every tool-result payload; writes verdicts | both |
| `run-scenarios.sh` | runs the nine recovery scenarios sequentially, one real `opencode run` session each | recovery report |
| `opencode.json` (in the xdg-config tree, not copied) | custom provider pointing at the scripted model | both |
Per-run artifacts (verdict files, per-request evidence logs) are **not
committed** — they are written to the scratch directory by the scripts, and
every number quoted in the reports is reproducible from them. Set
`SUTRA_REPO` (repository root) and optionally `SUTRA_OC_BASE`
(scratch dir, default `/tmp/sutra-oc-proto`), `SUTRA_OC_HOME` (persistent
state), `SUTRA_OC_DRIVER`, and `SUTRA_OC_EVIDENCE` to relocate everything.

The shared scripts evolved between the two experiments (the driver gained
subcommands and persistent state; the fake model became scenario-driven); the
Phase 1 snapshot is in the earlier commit of this branch.
