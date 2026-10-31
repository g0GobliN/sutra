# Contributing to SUTRA

Thanks for looking. By participating, you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

This is a small, deliberately conservative plugin: it sits in front of a model's context and writes tool output to disk, so a rule that removes too much — or promises bytes it did not keep — is worse than a rule that removes nothing.

## The rules that will not be relaxed

- **Never invent content.** Only remove, collapse, or elide — and always leave a marker naming what was removed.
- **Never rewrite a diff.** Patches and `git diff` output must be byte-identical.
- **Never reshape a payload.** A JSON array in is a JSON array out; a document in re-parses as a document. If a truncation cannot be represented without changing the schema, do not perform it.
- **Never lose the end of a traceback.**
- **Never cite an id that is not on disk.** The marker is written after the original is stored, or it says the original was not retained.
- **Never exceed a configured bound.** `SUTRA_MAX_BYTES` caps the rewrite; the store caps entry size, total size, count and age; retrieval caps its response.
- **Never run retrieved text.** Recovery is literal and read-only.
- **Never raise.** Any unexpected shape returns the original result.
- **Always record the saving** in the stats file so a claim can be checked.

## Development

```bash
git clone https://github.com/<you>/sutra
cd sutra
python3 -m venv .venv
.venv/bin/pip install pytest ruff
.venv/bin/python -m pytest tests -q
.venv/bin/python bench/benchmark.py      # optional: refreshes every number in the README
```

If you have Hermes installed, also run the loader the way Hermes does:

```bash
hermes plugins validate .
```

The documentation map, including the authoritative integration matrix and the
release runbook, is in [docs/README.md](docs/README.md).

That check loads the plugin, calls `register()`, and verifies the manifest
declares exactly the tools and middleware that were registered. A plugin that
fails it does not load in a real session.

## What makes a good pull request

1. **One rule per PR.** A new compression rule, a fix to an existing one, a
   recovery change, or a docs change — not several at once.
2. **A failing test first.** The test should show the shape that is currently
   mishandled. Use a real tool result if you have one — paste it as a fixture,
   with secrets removed.
3. **Proof it is still recoverable.** Show the marker text the model would see,
   the `sutra_restore` call that gets the elided content back, and the byte
   count of both.
4. **The byte numbers.** Before and after, for the fixture, from
   `bench/benchmark.py`.
5. **No new dependencies** unless the standard library genuinely cannot do the
   job. The plugin currently has zero.

## What a good bug report contains

- The tool that produced the result (`terminal`, `read_file`, a custom tool).
- The result shape — a string, or a dict and which keys.
- The size, and the `SUTRA_*` settings in force.
- What was lost or mangled, and what you expected instead.
- If recovery failed: the id, the mode, and the response status.

A report that says "it broke my output" cannot be acted on; a report with a 4 KB fixture and the settings can be fixed in one commit.

## Scope

In scope: compression rules, safety rules, the original-output store, the
retrieval protocol, configuration, measurement, Hermes plugin wiring, and the
stdio MCP server that exposes the same two tools to any MCP client.

Out of scope: summarising with a model, rewriting content, network calls,
telemetry, and adapters that reimplement SUTRA for another agent framework. A
new MCP client is a config entry against the existing server, not a second
implementation; anything that copies the rules into a framework-specific path
needs its own discussion first. SUTRA is deterministic and offline on purpose.
