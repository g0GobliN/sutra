# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Layout: multi-context

This repo is a **multi-context** project. There is no single glossary at the root: each
subproject owns its vocabulary, and `GLOSSARY-MAP.md` at the repo root points at the
per-context `GLOSSARY.md` files. Read the map first, then only the glossary for the
context the task touches.

```
/
├── GLOSSARY-MAP.md                     ← root map: context → GLOSSARY.md
├── docs/adr/                           ← system-wide decisions
├── GLOSSARY.md                         ← Python package: tool results, request
│                                          stage, modes, store, recovery ids
├── scripts/GLOSSARY.md                 ← A/B runner: arms, fixtures, scorers
├── bench/GLOSSARY.md                   ← benchmarks: corpus, generator, gates
└── docs/                               ← operator-facing prose, not a context
```

Contexts are directories that own behaviour and language: the Python package at the
repository root, `scripts/` (the A/B harness and host templates) and `bench/` (fixture
generation and measurement). `docs/` explains those contexts to users; it carries no
vocabulary of its own.

Neither `GLOSSARY-MAP.md` nor any per-context `GLOSSARY.md` exists yet. They are created
lazily by `/domain-modeling` when a term or decision is actually resolved, and
`GLOSSARY-MAP.md` then points at them. Until then, proceed without them and do not ask
for them to be created upfront.

## Before exploring, read these

- **`GLOSSARY-MAP.md`** at the repo root, when it exists, then the per-context
  `GLOSSARY.md` files it names for the area being changed.
- **`docs/adr/`**: read ADRs that touch the area. A context may also keep its own
  decisions under `<context>/docs/adr/`.

If any of these files don't exist, **proceed silently**. Don't flag their absence and
don't suggest creating them upfront.

## Use the glossary's vocabulary

When output names a domain concept — an issue title, a refactor proposal, a hypothesis, a
test name — use the term as the relevant `GLOSSARY.md` defines it. Don't drift to
synonyms the glossary explicitly avoids.

If the concept needed isn't in the glossary yet, that is a signal: either the wording is
invented language the project does not use (reconsider), or there is a real gap (note it
for `/domain-modeling`).

## Flag ADR conflicts

If the output contradicts an existing ADR, surface it explicitly rather than silently
overriding it:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
