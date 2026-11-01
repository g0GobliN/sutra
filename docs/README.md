# Documentation

SUTRA's documentation is split by the question it answers. Start with the
[README](../README.md) for the product and installation; use this index when you
need the detail behind a claim.

## Getting started and integrations

| Document | Answers |
|---|---|
| [README](../README.md) | What SUTRA is, how to install it, and what it supports |
| [integrations.md](integrations.md) | The authoritative per-client matrix: mechanism, automatic or explicit, install, verification, limits |
| [automatic-install.md](automatic-install.md) | What `sutra install` changes for each client, with backups and rollback |
| [universal-integration.md](universal-integration.md) | Host-API evidence: which hook each agent documents, including Codex's PostToolUse feedback contract |
| [configuration.md](configuration.md) | Every environment variable, default and bound |

## Correctness and recovery

| Document | Answers |
|---|---|
| [contract.md](contract.md) | The guarantees, each with the test that enforces it |
| [recovery.md](recovery.md) | Marker grammar, restore modes, response statuses and bounds |
| [json-array-preview.md](json-array-preview.md) | Why a safe generic array preview is deferred |
| [semantic-retrieval.md](semantic-retrieval.md) | Why semantic ranking is not offered yet |

## Evidence

| Document | Answers |
|---|---|
| [benchmark.md](benchmark.md) | Fixtures, method, timings, memory and reproduction |
| [effectiveness-pilot.md](effectiveness-pilot.md) | The completed deterministic and host-adapter pilot, with live metrics marked unavailable |
| [ab-benchmark.md](ab-benchmark.md) | The frozen live-model A/B protocol (not yet run) |

## Operations and contribution

| Document | Answers |
|---|---|
| [troubleshooting.md](troubleshooting.md) | Restore statuses, plugin state, removal and common failures |
| [../SECURITY.md](../SECURITY.md) | Threat model, storage protections, prompt-injection handling |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Development, tests and pull-request expectations |
| [release.md](release.md) | Trusted Publishing, versioning, TestPyPI rehearsal and production approval |

## Historical prototypes

The `docs/prototypes/` directory records how the OpenCode integration was proven
before it shipped, and `../spikes/opencode-poc/` holds the original bridge spike
and its evidence. They are kept as engineering evidence, not as supported
entry points.
