# Repository settings

SUTRA's governance lives in two places. Files in this repository describe the
automation, and a diff shows every change to them. Settings on github.com decide
whether that automation can block a merge, who approves a release, and which
tokens a workflow receives. A workflow cannot set those, and a merged file does
not prove they are on.

This page separates the two, records what was inspected on 2026-11-19, and lists
the changes that need an administrator. Re-check a value before relying on it;
GitHub state changes outside this repository.

## Enforced by files in this repository

| File | Runs on | What it enforces |
| --- | --- | --- |
| `.github/workflows/ci.yml` | pull request, push to `main`, `v*` tag | the suite on Python 3.11–3.14 Linux, macOS 3.13, Windows 3.11–3.14; the installed wheel exercised through the CLI; a hyphenated checkout; Hermes plugin validation and the live adapter tests; tag/version agreement |
| `.github/workflows/lint.yml` | pull request, push to `main`, `v*` tag | `ruff check .` at the pinned version, plus the GitHub configuration validator |
| `.github/workflows/security.yml` | pull request, push to `main`, weekly | `pip-audit` over every non-dev runtime requirement, plus the workflow trust-boundary checks |
| `.github/workflows/codeql.yml` | pull request, push to `main`, weekly | CodeQL on the Python sources with the `security-extended` query pack |
| `.github/workflows/dependency-review.yml` | pull request | fails a pull request that introduces a high or critical advisory |
| `.github/workflows/release.yml` | manual dispatch only | tests the tagged commit, builds once, publishes checksums, rehearses on TestPyPI, then promotes to PyPI behind the `pypi` environment |
| `.github/workflows/benchmark.yml` | pull request, push to `main` | measures the fixtures on 3.11–3.14; publishes the page only from `main` |
| `.github/workflows/stale.yml` | weekly | marks inactive issues after 180 days, closes after 45 more, and exempts assigned, milestone, security and pinned items |
| `.github/dependabot.yml` | weekly | grouped action and telemetry updates |
| `.github/CODEOWNERS` | every pull request | names the owner of each area |
| `.github/ISSUE_TEMPLATE/` | new issue | bug, feature and documentation forms; security reports route to the private advisory page |
| `.github/PULL_REQUEST_TEMPLATE.md` | new pull request | summary, testing, compatibility, security and documentation evidence |

## Inspected on github.com

### Branch protection for `main`

The repository also has one active repository ruleset named `main`
(id 25769461, branch target, updated 2026-11-15). Treat the values below as the
protection state at inspection time, not as something this repository
guarantees.

| Setting | Current value |
| --- | --- |
| Require a pull request before merging | yes |
| Required approving reviews | 0 |
| Dismiss stale approvals on new commits | yes |
| Require review from code owners | no |
| Required status checks | `test (3.11)`, `test (3.12)`, `test (3.13)`; strict (branch must be current) |
| Require conversation resolution | yes |
| Require linear history | yes |
| Allow force pushes | no |
| Allow branch deletion | no |
| Include administrators | yes |
| Require signed commits | no |

### Security features

| Setting | State |
| --- | --- |
| Dependabot alerts | enabled |
| Dependabot security updates | enabled |
| Secret scanning | enabled |
| Secret scanning push protection | enabled |
| Secret scanning validity checks | disabled |
| Secret scanning non-provider patterns | disabled |

### Actions

| Setting | State |
| --- | --- |
| Actions enabled | yes, all actions allowed |
| Default `GITHUB_TOKEN` permission | read-only |
| Require actions pinned to a full-length commit SHA | no |
| Allow GitHub Actions to approve pull requests | no |

### Environments

| Environment | Protection |
| --- | --- |
| `pypi` | required reviewers: `@g0GobliN`; administrators may bypass |
| `testpypi` | none |

### Merge and repository flags

Squash and rebase merges are allowed, merge commits are not. Branches are
deleted after merge, automatic merge is available, and the repository has
issues, projects and a wiki. Discussions are off and commit sign-off is not
required.

## Administrator actions

None of these can be done by a workflow, and none of them are claimed below as
already done.

1. **Require the remaining CI checks.** Only three of the four Python legs are
   required, so a change that breaks 3.14 still merges. Add `test (3.14)`; add
   `package`, `validate` and `hyphen-checkout` once you accept their runtime,
   and `lint` from `lint.yml`. Do not require the Windows job while it reports
   the single context `test (windows-latest)` for four matrix legs: give the
   job a `name:` that includes `${{ matrix.python-version }}` first, then
   require those contexts.
2. **Do not require `dependency-review` or `analyze` yet.** CodeQL analysis is
   not produced for every pull request (forks and Dependabot branches differ),
   and a required check that never reports blocks the merge permanently.
3. **Enable "Require actions pinned to a full-length commit SHA".** Every
   action in this repository is pinned to a SHA, and the lint workflow enforces
   that in the file it validates. The repository setting closes the gap for any
   workflow added later.
4. **Enable secret scanning validity checks** and consider non-provider
   patterns once you accept the extra alert volume.
5. **Keep review requirements at 0 while there is a single maintainer.** GitHub
   does not count an author's own review, and it does not accept a code owner's
   review of their own change. Requiring one approval or code-owner review
   would make every pull request unmergeable until a second maintainer with
   write access exists; `CODEOWNERS` already routes the review request.
6. **Decide about the wiki.** `has_wiki` is on while the documentation lives in
   `docs/`. A wiki is a second, unversioned documentation surface; disabling it
   keeps a single source.
7. **Release approval.** The `pypi` environment already requires a reviewer and
   allows an administrator bypass. The bypass is the recovery path for a stuck
   run; keep it only if you can be the reviewer of your own dispatch, which
   single-maintainer operation needs.

## Re-checking

```bash
gh api repos/g0GobliN/sutra --jq '{default_branch, allow_merge_commit, allow_squash_merge,
  allow_rebase_merge, delete_branch_on_merge, has_wiki, has_discussions, security_and_analysis}'
gh api repos/g0GobliN/sutra/branches/main/protection
gh api repos/g0GobliN/sutra/rulesets
gh api repos/g0GobliN/sutra/actions/permissions/workflow
gh api repos/g0GobliN/sutra/environments
```

The workflows' own trust boundaries are checked without GitHub access:

```bash
python scripts/check_github_config.py
```

## What is deliberately absent

- No funding configuration: there is no sponsorship account, and a
  configuration pointing at a profile page promises nothing.
- No public security issue form: `SECURITY.md` and the issue chooser both point
  at the private advisory page, and a form that collects vulnerability details
  in public would contradict them.
- No GitHub release artifacts: publishing is Trusted Publishing from a tagged
  build, and the changelog is the release record.
