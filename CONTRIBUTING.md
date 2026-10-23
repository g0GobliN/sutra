# Contributing to tooltrim

Thanks for looking. This is a small, deliberately conservative plugin: it sits in front of a model's context, so a rule that removes too much is worse than a rule that removes nothing.

## The rules that will not be relaxed

- **Never invent content.** Only remove, collapse, or elide — and always leave a marker naming what was removed.
- **Never rewrite a diff.** Patches and `git diff` output must be byte-identical.
- **Never lose the end of a traceback.**
- **Never raise.** Any unexpected shape returns the original result.
- **Always record the saving** in the stats file so a claim can be checked.

## Development

```bash
git clone https://github.com/<you>/tooltrim
cd tooltrim
python3 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

## What makes a good pull request

1. **One rule per PR.** A new compression rule, a fix to an existing one, or a docs change — not several at once.
2. **A failing test first.** The test should show the shape that is currently mishandled. Use a real tool result if you have one — paste it as a fixture, with secrets removed.
3. **Proof it is still recoverable.** Show the marker text the model would see and how it would get the elided content back.
4. **The byte numbers.** Before and after, for the fixture.

## What a good bug report contains

- The tool that produced the result (`terminal`, `read_file`, a custom tool).
- The result shape — a string, or a dict and which keys.
- The size, and the `TOOLTRIM_*` settings in force.
- What was lost or mangled, and what you expected instead.

A report that says "it broke my output" cannot be acted on; a report with a 4 KB fixture and the settings can be fixed in one commit.

## Scope

In scope: compression rules, safety rules, configuration, measurement, Hermes plugin wiring.

Out of scope: summarising with a model, rewriting content, network calls, telemetry. tooltrim is deterministic and offline on purpose.
