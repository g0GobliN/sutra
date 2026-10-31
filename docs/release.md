# Releasing SUTRA

SUTRA is published to PyPI as **`sutra-agent`**. The import package and the
console commands remain `sutra`; the name `sutra` on PyPI belongs to an unrelated
project, which is why the distribution carries a suffix.

Releases are built and uploaded by GitHub Actions through
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/). The build
job never holds an upload credential: each publish job exchanges a GitHub OIDC
identity for a short-lived token that PyPI accepts only from the publisher
registered for this repository, workflow and environment. No long-lived PyPI
API token is stored in the repository, in a workflow file, or on a maintainer's
machine.

This document describes the process any authorized maintainer can follow. It is
not a grant of permission: production publishing is restricted to maintainers
who can approve the protected `pypi` environment (see
[Publication model](#publication-model)).

## Distribution identity

| | Value |
|---|---|
| Distribution name | `sutra-agent` |
| Import package | `sutra` |
| Console commands | `sutra`, `sutra-mcp`, `sutra-hook` |
| Repository | [`g0GobliN/sutra`](https://github.com/g0GobliN/sutra) |
| Publishing workflow | `.github/workflows/release.yml` |
| Production environment | `pypi` |
| Rehearsal environment | `testpypi` |

These values are part of the trust configuration. Changing the distribution
name, the workflow filename, the repository, or an environment name invalidates
the publisher registration on the index and requires it to be registered again.

## Publication model

- **TestPyPI** (`https://test.pypi.org`) is a rehearsal index. Nothing published
  there reaches users, and it is safe to publish the same version there while
  validating a release.
- **PyPI** (`https://pypi.org`) is production. A version published there is
  visible to every user and cannot be reused.
- The workflow's `publish-pypi` job targets the `pypi` environment. That
  environment is protected by required reviewers in the repository settings, so
  the job pauses until an authorized maintainer approves it. The `testpypi`
  environment has no such protection, because a rehearsal cannot affect users.
- Contributors can prepare, test and review a release change, but cannot publish
  it without environment approval and a valid Trusted Publishing registration.

The workflow is started manually (`workflow_dispatch`) with two inputs: the
release tag to build and the target index (`testpypi` or `pypi`). It builds from
the tag, never from a moving branch tip, and the build refuses to continue when
the tag, `__init__.py` and `plugin.yaml` disagree.

## Trusted publisher registration

Both indexes need an entry for this repository. The entries are identical except
for the index and the environment:

| | PyPI | TestPyPI |
|---|---|---|
| Project name | `sutra-agent` | `sutra-agent` |
| Owner | `g0GobliN` | `g0GobliN` |
| Repository | `sutra` | `sutra` |
| Workflow | `release.yml` | `release.yml` |
| Environment | `pypi` | `testpypi` |

Register or inspect the entries from the account's publishing settings:

- PyPI: <https://pypi.org/manage/account/publishing/>
- TestPyPI: <https://test.pypi.org/manage/account/publishing/>

Before an index accepts the entry, the account must meet that index's
authentication requirements. PyPI requires a verified primary email address;
TestPyPI additionally requires two-factor authentication before account changes.
Authorized maintainers are responsible for enabling the security features their
accounts require and for retaining recovery credentials in the team's approved
secret storage. Recovery codes and passwords are never committed to this
repository or pasted into issues, pull requests or chat.

Until the project's first upload, the project does not exist on the index, so
the registration is a **pending publisher** created from the account's
publishing settings rather than from a project page. Using it for the first time
creates the project and converts the pending entry into a regular publisher in
the same upload. A pending publisher does **not** reserve the name: if another
account publishes `sutra-agent` first, the pending entry becomes unusable.

Keep the environment names exactly as listed. They are part of the identity PyPI
verifies, and renaming an environment means registering the publisher again.

## Versioning and changelog

A release version is declared in two files and must agree everywhere:

- `__init__.py` — `__version__`, which `sutra --version` and the built metadata
  read;
- `plugin.yaml` — `version`, which the Hermes plugin manifest reports.

`scripts/check_version.py <tag>` compares both against a tag such as `v1.2.3`
and is the first step of the release workflow. Bump both files in the same
commit.

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/) style:
entries accumulate under `## Unreleased`, and a release moves them under a
`## <version> (YYYY-MM-DD)` heading. Describe user-visible behaviour, not the
internal sequence of commits.

## Preparing a release

1. Bump the version in `__init__.py` and `plugin.yaml`.
2. Move the `CHANGELOG.md` entries from `Unreleased` under the new version.
3. Update the `README.md` install section if what users install or run changed.
4. Open a pull request and wait for CI. Every job must pass:
   - the product suite on Python 3.11, 3.12 and 3.13, plus macOS;
   - `ruff check .`;
   - `hermes plugins validate .` at the pinned Hermes revision;
   - the `package` job, which builds the sdist and wheel, runs
     `twine check --strict`, validates the archive contents and installs the
     wheel in a clean environment;
   - the hyphen-checkout job, which proves the suite runs from a directory whose
     name is not a valid Python module name (as GitHub tarballs extract).
5. Merge the pull request. The release is built from a tag on the merged commit,
   so the merged tree is what users receive.

## Building and validating locally

The same checks CI runs can be reproduced before tagging:

```bash
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
python scripts/verify_dist.py dist --version X.Y.Z
python scripts/verify_dist.py dist --check-index
```

`verify_dist.py` inspects the archives rather than their metadata. It requires
every runtime module, bundled adapter script, `plugin.yaml` and
`opencode_plugin.js` in the wheel; it rejects tests, caches, benchmark and probe
trees, and absolute local paths; and it checks that metadata
declares `License-Expression: MIT`, that the distribution stays dependency-free
and that the three console entry points exist. With `--check-index` it asks PyPI
whether that version already exists and fails the build before an upload could be
rejected halfway.

A successful local run ends with:

```text
OK sutra_agent-X.Y.Z-py3-none-any.whl and sutra_agent-X.Y.Z.tar.gz are a sutra-agent X.Y.Z release
```

## TestPyPI rehearsal

Tag the merged commit, then run the workflow against TestPyPI:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

In GitHub: **Actions → Release → Run workflow**, with `tag=vX.Y.Z` and
`target=testpypi`. The run:

1. checks out the tag and verifies the tag and declared versions agree;
2. builds the sdist and wheel, runs `twine check --strict`, validates the archive
   contents and refuses a version PyPI already serves;
3. installs the **sdist** in clean environments on Python 3.11, 3.12 and 3.13,
   then exercises `sutra --version`, `sutra config`, `sutra compress`,
   `sutra restore`, `sutra-hook` and `sutra status --json`;
4. publishes to TestPyPI through OIDC;
5. waits for TestPyPI to serve the version, installs it there, and runs
   `sutra --version`.

Verify the artifact the way a user would, in a fresh environment:

```bash
python3 -m venv /tmp/sutra-check
/tmp/sutra-check/bin/pip install --no-deps \
    --index-url https://test.pypi.org/simple/ sutra-agent==X.Y.Z
/tmp/sutra-check/bin/sutra --version
```

The expected output is `sutra X.Y.Z`. `--no-deps` is used because the
distribution has no runtime dependencies and the rehearsal index is not a
general-purpose package source; omit it only if you deliberately want pip to
resolve dependencies from PyPI.

## Production publication

Run **Actions → Release → Run workflow** again with the same tag and
`target=pypi`. The build and smoke jobs repeat, then `publish-pypi` waits for
approval of the `pypi` environment. Approval is the final human authorization
step; the workflow then uploads the wheel, the sdist and a signed attestation
through OIDC.

The final job installs `sutra-agent==X.Y.Z` from PyPI into a clean environment
and runs `sutra --version` and `sutra config`, so a release that uploaded
successfully but cannot be installed fails the workflow instead of passing
silently.

## Post-publication verification

- Open <https://pypi.org/project/sutra-agent/> and confirm the long description
  renders: headings, tables, links and the `how-it-works.svg` diagram should all
  display. README links are absolute for this reason.
- Confirm the release shows the wheel, the sdist and the attestation.
- Install from PyPI in a clean environment:

  ```bash
  python3 -m venv ~/.sutra-venv
  ~/.sutra-venv/bin/pip install sutra-agent
  ~/.sutra-venv/bin/sutra --version
  ~/.sutra-venv/bin/sutra install --dry-run
  ```

- If the README still describes installing from a checkout, update it to the
  published instructions in a separate documentation pull request. That change
  is documentation, not part of the release.

## Troubleshooting

- **The version already exists on the index.** The build job stops before any
  upload. Choose a new version; an index never accepts the same version twice.
  This is also why an interrupted upload is not repaired by re-tagging: publish
  the next patch version instead.
- **The upload failed partway through.** Keep the accepted files, investigate
  the transport or permission error, and publish a new version. Do not delete
  and re-upload files under the same name: PyPI blocks reuse of deleted
  filenames as well.
- **A broken release is live.** Use *Yank* on the PyPI release page. Yanking
  hides the version from new resolutions without deleting it or breaking a pin;
  publish a fixed version afterwards and never reuse the yanked number.
- **Trusted Publishing fails with a claim mismatch.** Compare the five fields in
  the table above with the publisher on the index and with the `environment:`
  names in `.github/workflows/release.yml`. Environment names are compared
  exactly, including case.
- **The package job fails on contents.** Run `scripts/verify_dist.py` locally; it
  names the missing or unexpected file. A new runtime module or bundled asset
  must be added to `RUNTIME_MODULES` or `PACKAGE_DATA` in that script and to
  `[tool.setuptools.package-data]` in `pyproject.toml` in the same change.
- **The TestPyPI install cannot find the package.** Confirm the run reached
  `verify-testpypi` and that the version exists on TestPyPI; index propagation can
  take a short time, and the verification job polls for up to ten minutes.
- **A release was started with the wrong tag.** Nothing is uploaded until the
  build and smoke jobs pass; if a publish job already ran on TestPyPI, fix the
  problem and publish the next version to production. Tags that were never
  published can be deleted before they are used, but never move a tag that PyPI
  has already served.

## Release checklist

- [ ] Version bumped in `__init__.py` and `plugin.yaml`, changelog updated.
- [ ] Pull request merged with every CI job green, including `package`.
- [ ] Tag pushed for the merged commit.
- [ ] TestPyPI rehearsal passed, including install-back verification.
- [ ] `pip install --no-deps --index-url https://test.pypi.org/simple/
      sutra-agent==X.Y.Z` reports the expected version.
- [ ] Production run approved for the `pypi` environment.
- [ ] PyPI page renders and the published version installs and runs.
- [ ] Install instructions in the README updated if they changed.
