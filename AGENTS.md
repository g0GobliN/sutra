# Agent instructions

## Public repository boundary

This repository is public. Publish only what users and contributors need:

- product source, tests, packaging, and CI;
- public documentation and reviewed evidence that supports a published claim;
- small, intentional fixtures with no secrets or personal data.

Keep everything else local. Local probes, raw logs, machine-specific
configuration, caches, temporary scripts, private notes, benchmark runs, and
unreviewed artifacts must stay on disk and be covered by `.gitignore`; they are
not part of the public repository. If a local artifact later becomes useful
evidence, reduce it to the smallest reviewed form and commit only that.

## Staging and preserving work

- Inspect `git status --short` before every commit.
- Stage explicit paths. Never use `git add -A`, `git add .`, or a broad glob.
- Do not stage, commit, amend, or push changes you did not make. The working
  tree may contain the user's own release preparation.
- Never reset, clean, stash, or force-checkout over uncommitted work.
- Never commit secrets, tokens, credentials, session logs, personal paths, or
  generated caches. `.env` files are ignored; do not paste their values into
  source, tests, docs, or chat.

## Delivery

Run the focused tests for the files you changed, then the relevant full suite
and `ruff check .`. Record exact commands and results; do not report a check
that did not run. Open a reviewable PR from a clean branch; do not tag, bump the
version, or publish unless the user explicitly asks for that release step.
