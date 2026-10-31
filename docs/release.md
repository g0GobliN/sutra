# Releasing SUTRA

Publishing runs from `.github/workflows/release.yml` through PyPI Trusted
Publishing: the build job never sees a credential, and each publish job
exchanges a GitHub OIDC identity for a single-use upload token. There is no
long-lived PyPI API token in a repository secret, a workflow file, or a
maintainer's shell history.

The distribution is **`sutra-agent`**. The import package and the commands stay
`sutra` (`sutra`, `sutra-mcp`, `sutra-hook`); `sutra` on PyPI is an unrelated
package.

## Trusted publisher configuration

Both entries point at the same repository and workflow and differ only in the
index and the environment:

| | PyPI | TestPyPI |
|---|---|---|
| Project name | `sutra-agent` | `sutra-agent` |
| Owner | `g0GobliN` | `g0GobliN` |
| Repository | `sutra` | `sutra` |
| Workflow | `release.yml` | `release.yml` |
| Environment | `pypi` | `testpypi` |

Both indexes refuse to save a publisher until the account is in a publishable
state: the primary email address must be verified, and TestPyPI additionally
requires two-factor authentication before any account change. TestPyPI's 2FA
cannot be turned off once enabled, so enable it deliberately and store the
recovery codes first.

Until the first upload the project does not exist on either index, so each
entry is a **pending publisher**, added from the account sidebar rather than a
project page:

- PyPI: <https://pypi.org/manage/account/publishing/>
- TestPyPI: <https://test.pypi.org/manage/account/publishing/>

A pending publisher is converted into a normal publisher the first time it is
used, and the project is created in that same upload. A pending publisher does
**not** reserve the name: if another account registers `sutra-agent` first, the
pending entry is invalidated and the name is gone.

Both environments already exist on the repository. `pypi` has a required
reviewer, so publishing pauses for an approval; `testpypi` has no protection.
Keep the environment names exactly as above — they are part of the identity
PyPI checks, and renaming one means re-registering the publisher.

## Preparing a release

1. Bump the version in **both** places that declare it: `__init__.py`
   (`__version__`) and `plugin.yaml` (`version`). `sutra --version`, the plugin
   manifest and the built artifact must agree; `scripts/check_version.py`
   enforces the tag against both.
2. Move the `CHANGELOG.md` entries from "Unreleased" under the new version.
3. If the release changes what users install, update the `README.md` install
   section.
4. Merge to `main`. CI must be green: the suite on 3.11–3.13 and macOS,
   `ruff check .`, `hermes plugins validate .`, and the packaging job that
   builds the artifacts and installs them into a clean environment.

## Publishing

The workflow builds from the release tag, not from a branch tip.

1. Tag the merged commit: `git tag v0.3.0 && git push origin v0.3.0`.
2. Run **Release** with `tag=v0.3.0`, `target=testpypi`. It builds, runs
   `twine check --strict`, validates the artifact contents, refuses a version
   that already exists, installs the built sdist on 3.11/3.12/3.13, exercises
   `sutra compress`/`restore`, publishes to TestPyPI, and then installs the
   published release back from that index.
3. Rehearse the install the user will actually run:

   ```bash
   python3 -m venv /tmp/sutra-check
   /tmp/sutra-check/bin/pip install --no-deps \
       --index-url https://test.pypi.org/simple/ sutra-agent==0.3.0
   /tmp/sutra-check/bin/sutra --version
   ```

4. Run **Release** again with `tag=v0.3.0`, `target=pypi`. Approve the `pypi`
   environment when GitHub asks; the approval is the last human step. PyPI
   receives the wheel, the sdist and a signed attestation, and the final job
   installs `sutra-agent==0.3.0` from PyPI and runs it.

TestPyPI and PyPI are independent: publishing a rehearsal there does not touch
the real index, and TestPyPI files may be deleted to retry the same version.

## After the first publication

Once `sutra-agent` is live, the README's "not on PyPI yet" paragraph becomes
wrong. Replace the checkout instructions with the published one:

```bash
python3 -m venv ~/.sutra-venv
~/.sutra-venv/bin/pip install sutra-agent
~/.sutra-venv/bin/sutra install
```

Publish that README change separately; it is a documentation edit, not a
release step.

## When something goes wrong

- **A version is already on the index.** The build job stops before upload.
  Bump the version; PyPI never accepts the same version twice.
- **The upload failed halfway.** PyPI keeps the files it accepted, and the
  build job's existing-version guard then stops a re-run. Delete the incomplete
  release on PyPI and run the workflow again for the same tag; do not change
  the version to work around a transport error.
- **A broken release is live.** Use *Yank* on the release page: it hides the
  version from new resolutions without deleting it or breaking someone who
  already pinned it. Publish a fixed version; never reuse the yanked number.
- **Trusted publishing fails with a claim mismatch.** Compare the four fields
  in the table above with the publisher on the index and with the workflow's
  `environment:` value. The environment name is compared exactly.
