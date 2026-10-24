# OpenCode host-experiment artifacts (prototype-grade)

Files behind the two feasibility reports in this directory's parent. These are
experiment records, not production code: absolute paths, scratch directories,
no packaging. OpenCode 1.19.0; every run was isolated from the user's own
OpenCode setup via `XDG_CONFIG_HOME` / `XDG_DATA_HOME`.

| File | Role | Used by |
|---|---|---|
| `sutra-proto.js` | the feasibility plugin (Phase 1: `tool.execute.after` replacement only) | output-replacement report |
| `sutra-recovery-plugin.js` | the Phase 2 plugin: replacement + registered `sutra_restore` tool (`tool()` helper, `@opencode-ai/plugin`) | recovery-registration report |
| `driver.py` | subprocess bridge to the unmodified engine; `compress` (adapter path, persistent store), `restore`, `seed-expired` | both |
| `fake_model.py` | scenario-driven scripted provider (127.0.0.1:8909); logs offered tools and every tool-result payload; writes verdicts | both |
| `run-scenarios.sh` | runs the nine recovery scenarios sequentially, one real `opencode run` session each | recovery report |
| `opencode.json` (in the xdg-config tree, not copied) | custom provider pointing at the scripted model | both |
| `verdict*.json` | per-scenario outcomes. `verdict-expired.first-run.json` is the miscalibrated first assertion (superseded by `expired-swept`, kept for honesty) | recovery report |
| `evidence-feasibility.jsonl`, `evidence-recovery.jsonl` | per-request hook + provider logs (ground truth of model-facing payloads) | both |
| `verdict.json` | the Phase 1 replacement verdict | output-replacement report |

The shared scripts evolved between the two experiments (the driver gained
subcommands and persistent state; the fake model became scenario-driven); the
Phase 1 snapshot is in the earlier commit of this branch.
