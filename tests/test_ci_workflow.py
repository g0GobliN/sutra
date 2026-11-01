"""CI must stay read-only and exercise the installed CLI, not just the source."""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_uses_a_read_only_token_by_default():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^permissions:\n  contents: read$", text), (
        "CI must declare contents: read so pull requests cannot inherit a "
        "write-capable default token")
    assert "contents: write" not in text
    assert "write-all" not in text


def test_the_package_job_exercises_the_installed_cli():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "Exercise the installed CLI across all four adapters" in text
    for command in (
        "sutra detect",
        "sutra doctor --json",
        "sutra status --json",
        "sutra install --client \"$client\" --dry-run",
        "sutra uninstall --client \"$client\" --dry-run",
        "sutra install --client opencode",
        "sutra uninstall --client opencode",
    ):
        assert command in text, command
    assert "before=$(find \"$CLI_HOME\"" in text
    assert "after=$(find \"$CLI_HOME\"" in text
    assert 'test "$before" = "$after"' in text
