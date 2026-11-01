"""Production must promote the one verified build, and must never skip the rehearsal.

`.github/workflows/release.yml` is the only path a release can be published
through, so these tests read it as text and assert the job graph: production
needs the TestPyPI verification, which needs the TestPyPI publication, which
needs the single build. Production downloads that artifact, re-verifies its
SHA-256 manifest and contains no checkout or build step of its own.

The file is parsed with a small job-block reader rather than PyYAML: the suite
runs under pytest alone (the distribution stays dependency-free), so a YAML
parser must not become a test dependency. The reader asserts that it found the
expected jobs, so a reformat that defeats it fails loudly instead of passing.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
RELEASE_DOC = ROOT / "docs" / "release.md"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _job_blocks() -> dict[str, str]:
    """Return every `jobs:` entry as its raw block text."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    except StopIteration:
        raise AssertionError("release.yml has no top-level jobs: mapping") from None
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            if current is not None:
                blocks[current].append(line)
            continue
        if _indent(line) == 0:
            break
        if _indent(line) == 2 and line.rstrip().endswith(":"):
            current = line.strip()[:-1]
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    return {name: "\n".join(body) for name, body in blocks.items()}


def _needs(block: str) -> set[str]:
    """Read a job's `needs:` value in its scalar, inline-list or block-list form."""
    lines = block.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^ {4}needs:\s*(.*)$", line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline.startswith("["):
            return {item.strip().strip("'\"")
                    for item in inline.strip("[]").split(",") if item.strip()}
        if inline:
            return {inline.strip("'\"")}
        values = set()
        for follow in lines[index + 1:]:
            if not follow.strip():
                continue
            item = re.match(r"^ {6}-\s+(.+)$", follow)
            if not item:
                break
            values.add(item.group(1).strip().strip("'\""))
        return values
    return set()


def test_the_release_jobs_are_present():
    jobs = _job_blocks()
    assert {"build", "smoke", "publish-testpypi", "verify-testpypi",
            "publish-pypi", "verify-pypi"} <= set(jobs)


def test_the_workflow_builds_the_distribution_exactly_once():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("python -m build") == 1
    assert "python -m build" in _job_blocks()["build"]


def test_production_needs_the_testpypi_verification():
    production = _job_blocks()["publish-pypi"]
    needs = _needs(production)
    assert "verify-testpypi" in needs, (
        "publish-pypi must depend on verify-testpypi; depending only on build "
        "or smoke lets a production dispatch skip the TestPyPI rehearsal")
    assert "build" in needs


def test_the_rehearsal_is_not_conditional_on_the_target():
    jobs = _job_blocks()
    for name in ("publish-testpypi", "verify-testpypi"):
        conditions = "\n".join(line for line in jobs[name].splitlines()
                               if re.match(r"^ {4}if:", line))
        assert "inputs.target" not in conditions, (
            f"{name} must run for a production dispatch too; conditioning it on "
            f"the target is the release bypass this workflow had")
    assert {"build", "smoke"} <= _needs(jobs["publish-testpypi"])
    assert {"build", "publish-testpypi"} <= _needs(jobs["verify-testpypi"])


def test_the_testpypi_verification_checks_the_served_files():
    verify = _job_blocks()["verify-testpypi"]
    assert "test.pypi.org/simple/" in verify, "the package must be installed from TestPyPI"
    assert "sutra-agent==${VERSION}" in verify
    assert "SHA256SUMS" in verify, "the served files must be compared with the build"
    assert "digests" in verify and "sha256" in verify
    assert "sutra --version" in verify
    assert "for attempt in $(seq 1 30)" in verify, (
        "the JSON API can lead the simple index; pip resolution must be retried")
    assert "sleep 10" in verify


def test_production_promotes_and_never_rebuilds():
    production = _job_blocks()["publish-pypi"]
    assert "actions/checkout" not in production, "production must not check out the tree"
    assert "python -m build" not in production
    assert "actions/download-artifact@v4" in production
    assert "SHA256SUMS" in production
    checked = production.index("sha256sum -c SHA256SUMS")
    published = production.index("gh-action-pypi-publish")
    assert checked < published, "the checksums must be verified before the upload"
    assert "packages-dir: dist/" in production


def test_production_uses_oidc_and_the_protected_environment():
    production = _job_blocks()["publish-pypi"]
    assert re.search(r"^ {4}environment:\n {6}name: pypi$", production, re.MULTILINE)
    assert "id-token: write" in production
    assert "attestations: true" in production
    assert "inputs.target == 'pypi'" in production


def test_a_rehearsal_artifact_can_be_promoted_without_rebuilding():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "artifact_run_id:" in text, (
        "production must be able to reuse the exact artifact a separate "
        "TestPyPI rehearsal verified; a rebuild can differ byte for byte")
    build = _job_blocks()["build"]
    assert "if: inputs.artifact_run_id != ''" in build
    assert "gh run download" in build
    assert "--name dist" in build
    assert "sha256sum -c SHA256SUMS" in build
    assert "head_sha" in build and "head_branch" in build, (
        "a reused artifact must come from a run for this tag's commit")
    assert "if: inputs.artifact_run_id == ''" in build, (
        "the build step must not run when a verified artifact is reused")


def test_the_build_uploads_one_checksummed_artifact():
    build = _job_blocks()["build"]
    assert build.count("upload-artifact") == 1
    assert "SHA256SUMS" in build
    assert "sha256sum dist/*.whl" in build
    assert "> SHA256SUMS" in build
    assert "if-no-files-found: error" in build
    assert "overwrite: false" in build
    # The manifest identifies the bytes; it must not land inside the directory
    # that the publish action uploads.
    assert "dist/SHA256SUMS" not in build


def test_the_pypi_verification_checks_served_files_and_retries():
    verify = _job_blocks()["verify-pypi"]
    assert "actions/download-artifact@v4" in verify
    assert "sha256sum -c SHA256SUMS" in verify
    assert "https://pypi.org/simple/" in verify
    assert "sutra-agent==${VERSION}" in verify
    assert "for attempt in $(seq 1 30)" in verify, (
        "the JSON API can lead the simple index; pip resolution must be retried")
    assert "sleep 10" in verify
    assert "SHA256SUMS" in verify
    assert "digests" in verify and "sha256" in verify


def test_the_release_document_describes_the_same_path():
    text = RELEASE_DOC.read_text(encoding="utf-8")
    assert "SHA256SUMS" in text
    assert "verify-testpypi" in text
    assert "promotion" in text, "production must be documented as a promotion"
    assert "artifact_run_id" in text, (
        "the release guide must explain how to promote the rehearsal artifact")
    assert "The build and smoke jobs repeat" not in text, (
        "the documentation still claims production rebuilds the distribution")
