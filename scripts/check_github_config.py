#!/usr/bin/env python3
"""Validate the repository's GitHub configuration without contacting GitHub.

Two groups of checks:

  --config    The files are valid and internally consistent: every workflow
              parses and declares a top-level ``permissions`` block, every
              issue form matches the issue-form schema, ``dependabot.yml``
              uses supported ecosystems and schedules, and every absolute
              CODEOWNERS pattern names a path that exists in this checkout.

  --security  The automation does not hand trust to pull requests: no
              ``write-all``, no ``pull_request_target``, every action pinned
              to a full commit SHA with a version comment, no write permission
              reachable from a ``pull_request`` run without a guard, no
              ``secrets`` use in a pull-request-triggered workflow, no
              credential-shaped literal, and no publishing workflow that a
              pull request or a push can start.

With neither flag both groups run. Failures are printed one per line and the
exit status is 1; a clean run prints one OK line per group.

PyYAML is a development dependency only: the lint and security jobs install a
pinned version. It is deliberately absent from pyproject.toml and MANIFEST.in,
so the wheel and the sdist keep the zero-dependency contract that
scripts/verify_dist.py enforces.
"""

from __future__ import annotations

import argparse
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
GITHUB = ROOT / ".github"
WORKFLOW_SUFFIXES = {".yml", ".yaml"}
ISSUE_FORM_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}
INPUT_FIELD_TYPES = {"input", "textarea", "dropdown", "checkboxes"}
DEPENDABOT_ECOSYSTEMS = {
    "bun", "bundler", "cargo", "composer", "devcontainers", "docker",
    "dotnet-sdk", "elm", "github-actions", "gomod", "gradle", "gitsubmodule",
    "hex", "maven", "mix", "npm", "nuget", "pip", "pub", "swift", "terraform",
    "uv",
}
SCHEDULE_INTERVALS = {"daily", "weekly", "monthly"}
# A pull-request run may upload code-scanning results; that permission cannot
# change repository content, so CodeQL keeps it without an escape hatch.
PR_SAFE_WRITE_KEYS = {"security-events"}
# The permission keys GitHub accepts. A typo here silently drops the
# permission, so the mistake has to be reported instead.
PERMISSION_KEYS = {
    "actions", "attestations", "checks", "contents", "deployments",
    "discussions", "id-token", "issues", "models", "packages", "pages",
    "pull-requests", "repository-projects", "security-events", "statuses",
}
CREDENTIAL_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?:^|[^A-Za-z0-9_-])sk-[A-Za-z0-9]{20,}"),
)

failures: list[str] = []


def fail(reason: str) -> None:
    failures.append(reason)


def rel(path: pathlib.Path) -> str:
    return str(path.relative_to(ROOT))


def workflow_paths() -> list[pathlib.Path]:
    return sorted(
        path for path in (GITHUB / "workflows").iterdir()
        if path.suffix in WORKFLOW_SUFFIXES and path.is_file())


def load(path: pathlib.Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        fail(f"{rel(path)}: not valid YAML: {error}")
        return {}
    if not isinstance(data, dict):
        fail(f"{rel(path)}: top level must be a mapping")
        return {}
    return data


def trigger_of(workflow: dict) -> dict:
    # YAML 1.1 parses the bare key ``on`` as the boolean True.
    value = workflow.get("on", workflow.get(True))
    if isinstance(value, str):
        return {value: {}}
    if isinstance(value, list):
        return {name: {} for name in value}
    return value if isinstance(value, dict) else {}


def permissions_of(block: object) -> dict:
    if isinstance(block, str):
        return {"*": block}
    return block if isinstance(block, dict) else {}


def write_keys(permissions: object) -> set[str]:
    parsed = permissions_of(permissions)
    if parsed.get("*") == "write-all":
        return {"*"}
    return {key for key, value in parsed.items() if value == "write"}


def guarded(job: dict) -> bool:
    """A job is out of a pull request's reach when its ``if`` names the event."""
    condition = job.get("if")
    if not isinstance(condition, str):
        return False
    return "github.event_name" in condition or "github.ref" in condition


def check_config() -> int:
    checked = 0
    for path in workflow_paths():
        workflow = load(path)
        checked += 1
        if not workflow:
            continue
        if not trigger_of(workflow):
            fail(f"{rel(path)}: no trigger")
        if not isinstance(workflow.get("permissions"), (dict, str)):
            fail(f"{rel(path)}: missing top-level permissions block")
        else:
            for key in permissions_of(workflow["permissions"]):
                if key != "*" and key not in PERMISSION_KEYS:
                    fail(f"{rel(path)}: unknown permission {key!r}")
                checked += 1
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            fail(f"{rel(path)}: no jobs")
            continue
        for name, job in jobs.items():
            if not isinstance(job, dict):
                fail(f"{rel(path)}: job {name!r} is not a mapping")
                continue
            if "uses" not in job and "runs-on" not in job:
                fail(f"{rel(path)}: job {name!r} has neither runs-on nor uses")
            if "permissions" in job:
                for key in permissions_of(job["permissions"]):
                    if key != "*" and key not in PERMISSION_KEYS:
                        fail(f"{rel(path)}: job {name!r} has unknown permission {key!r}")
                    checked += 1

    template = GITHUB / "PULL_REQUEST_TEMPLATE.md"
    if not template.is_file() or not template.read_text(encoding="utf-8").strip():
        fail("pull request template is missing or empty")
    checked += 1

    checked += check_issue_forms()
    checked += check_dependabot()
    checked += check_codeowners()
    return checked


def check_issue_forms() -> int:
    directory = GITHUB / "ISSUE_TEMPLATE"
    if not directory.is_dir():
        fail(".github/ISSUE_TEMPLATE is missing")
        return 0
    checked = 0
    for path in sorted(directory.iterdir()):
        if path.suffix not in WORKFLOW_SUFFIXES or not path.is_file():
            continue
        form = load(path)
        checked += 1
        if path.name == "config.yml":
            if "blank_issues_enabled" in form and not isinstance(
                    form["blank_issues_enabled"], bool):
                fail(f"{rel(path)}: blank_issues_enabled must be a boolean")
            links = form.get("contact_links", [])
            if not isinstance(links, list):
                fail(f"{rel(path)}: contact_links must be a list")
                continue
            for link in links:
                if not isinstance(link, dict) or not {"name", "url", "about"} <= set(link):
                    fail(f"{rel(path)}: every contact link needs name, url and about")
                elif not str(link["url"]).startswith(("https://", "http://")):
                    fail(f"{rel(path)}: contact link {link['name']!r} needs an http(s) url")
                else:
                    checked += 1
            continue
        if not isinstance(form.get("name"), str) or not form["name"].strip():
            fail(f"{rel(path)}: missing name")
        if not isinstance(form.get("description"), str):
            fail(f"{rel(path)}: missing description")
        body = form.get("body")
        if not isinstance(body, list) or not body:
            fail(f"{rel(path)}: missing body list")
            continue
        seen: set[str] = set()
        for item in body:
            if not isinstance(item, dict):
                fail(f"{rel(path)}: every body item must be a mapping")
                continue
            kind = item.get("type")
            if kind not in ISSUE_FORM_TYPES:
                fail(f"{rel(path)}: unknown body type {kind!r}")
                continue
            checked += 1
            if kind == "markdown":
                if not str(item.get("attributes", {}).get("value", "")).strip():
                    fail(f"{rel(path)}: markdown block without value")
                continue
            identifier = item.get("id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
                fail(f"{rel(path)}: body item needs a slug id, found {identifier!r}")
            elif identifier in seen:
                fail(f"{rel(path)}: duplicate body id {identifier!r}")
            else:
                seen.add(identifier)
            attributes = item.get("attributes", {})
            if not isinstance(attributes, dict) or not str(
                    attributes.get("label", "")).strip():
                fail(f"{rel(path)}: {identifier!r} needs attributes.label")
            if kind == "dropdown":
                options = attributes.get("options", [])
                if not isinstance(options, list) or not options:
                    fail(f"{rel(path)}: dropdown {identifier!r} needs options")
                elif len(str(options)) > 0 and not all(
                        isinstance(option, str) for option in options):
                    fail(f"{rel(path)}: dropdown {identifier!r} options must be strings")
                else:
                    checked += len(options)
            if kind == "checkboxes":
                labels = attributes.get("options", [])
                if not isinstance(labels, list) or not labels:
                    fail(f"{rel(path)}: checkboxes {identifier!r} needs options")
                elif not all(isinstance(option, dict) and "label" in option
                             for option in labels):
                    fail(f"{rel(path)}: checkbox {identifier!r} options need labels")
            validations = item.get("validations", {})
            if not isinstance(validations, dict):
                fail(f"{rel(path)}: {identifier!r} validations must be a mapping")
            elif "required" in validations and not isinstance(validations["required"], bool):
                fail(f"{rel(path)}: {identifier!r} required must be a boolean")
    return checked


def check_dependabot() -> int:
    path = GITHUB / "dependabot.yml"
    if not path.is_file():
        fail(".github/dependabot.yml is missing")
        return 0
    config = load(path)
    checked = 1
    if config.get("version") != 2:
        fail(f"{rel(path)}: version must be 2")
    updates = config.get("updates")
    if not isinstance(updates, list) or not updates:
        fail(f"{rel(path)}: updates must be a non-empty list")
        return checked
    for update in updates:
        if not isinstance(update, dict):
            fail(f"{rel(path)}: every update must be a mapping")
            continue
        ecosystem = update.get("package-ecosystem")
        if ecosystem not in DEPENDABOT_ECOSYSTEMS:
            fail(f"{rel(path)}: unsupported package-ecosystem {ecosystem!r}")
        checked += 1
        if not str(update.get("directory", "")).startswith("/"):
            fail(f"{rel(path)}: {ecosystem!r} needs an absolute directory")
        intervals = update.get("schedule", {}).get("interval")
        if intervals not in SCHEDULE_INTERVALS:
            fail(f"{rel(path)}: {ecosystem!r} needs a supported schedule interval")
        limit = update.get("open-pull-requests-limit")
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            fail(f"{rel(path)}: {ecosystem!r} open-pull-requests-limit must be positive")
        groups = update.get("groups", {})
        if not isinstance(groups, dict):
            fail(f"{rel(path)}: {ecosystem!r} groups must be a mapping")
        elif not all(isinstance(group, dict) for group in groups.values()):
            fail(f"{rel(path)}: {ecosystem!r} every group must be a mapping")
        else:
            checked += len(groups)
    return checked


def check_codeowners() -> int:
    path = GITHUB / "CODEOWNERS"
    if not path.is_file():
        fail(".github/CODEOWNERS is missing")
        return 0
    checked = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        fields = stripped.split()
        if len(fields) < 2:
            fail(f"{rel(path)}:{number}: a rule needs a pattern and an owner")
            continue
        pattern, owners = fields[0], fields[1:]
        checked += 1
        for owner in owners:
            if not (owner.startswith("@") or "@" in owner):
                fail(f"{rel(path)}:{number}: {owner!r} is not a user, team or e-mail")
        if not pattern.startswith("/"):
            continue
        literal = re.split(r"[*?\[]", pattern, maxsplit=1)[0].rstrip("/")
        target = ROOT / literal.lstrip("/")
        if not target.exists():
            fail(f"{rel(path)}:{number}: {literal!r} does not exist in this checkout")
    return checked


def check_security() -> int:
    checked = 0
    for path in workflow_paths():
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        name = rel(path)
        workflow = load(path)
        triggers = trigger_of(workflow)
        for number, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            checked += 1
            if "write-all" in line:
                fail(f"{name}:{number}: write-all is never used")
            if re.match(r"\s*pull_request_target:", line):
                fail(f"{name}:{number}: pull_request_target is not used")
            if " uses: " in line:
                reference = line.split(" uses: ", 1)[1].strip()
                if reference.startswith(("./", "docker://")):
                    continue
                if "@" not in reference:
                    fail(f"{name}:{number}: action without a reference: {reference}")
                    continue
                commit, _, comment = reference.partition("#")
                sha = commit.split("@", 1)[1].strip()
                if not re.fullmatch(r"[0-9a-f]{40}", sha):
                    fail(f"{name}:{number}: {sha!r} is not a full commit SHA")
                if not comment.strip():
                    fail(f"{name}:{number}: pinned action needs a version comment")
                elif not any(character.isdigit() for character in comment):
                    fail(f"{name}:{number}: version comment has no version")
            for pattern in CREDENTIAL_PATTERNS:
                if pattern.search(line):
                    fail(f"{name}:{number}: credential-shaped literal")
        if "gh-action-pypi-publish" in text:
            if set(triggers) != {"workflow_dispatch"}:
                fail(f"{name}: a publishing workflow must only run on workflow_dispatch")
        if "pull_request" not in triggers:
            continue
        if write_keys(workflow.get("permissions")):
            fail(f"{name}: a pull-request workflow must declare read-only top-level permissions")
        if "secrets." in _code_only(text):
            fail(f"{name}: a pull-request workflow must not reference secrets")
        for job_name, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            writes = write_keys(job.get("permissions"))
            if not writes or guarded(job):
                continue
            unexpected = writes - PR_SAFE_WRITE_KEYS
            if unexpected:
                fail(f"{name}: job {job_name!r} can ask for {sorted(unexpected)} "
                     "during a pull request")
    return checked


def _code_only(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", action="store_true", help="check the configuration files")
    parser.add_argument("--security", action="store_true", help="check the trust boundaries")
    arguments = parser.parse_args()
    run_config = arguments.config or not arguments.security
    run_security = arguments.security or not arguments.config

    if run_config:
        count = check_config()
        report("config", count)
    if run_security:
        count = check_security()
        report("security", count)
    return 1 if failures else 0


def report(group: str, count: int) -> None:
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
    else:
        print(f"OK   {group}: {count} checks")


if __name__ == "__main__":
    raise SystemExit(main())
