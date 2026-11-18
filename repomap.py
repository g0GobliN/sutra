"""A bounded, deterministic map of what already exists in a repository.

Reusing an existing helper is only cheap when finding it does not cost a search.
A repository map does that with no model: list tracked source files, pull the
top-level definitions out with a regex per language, and print one line per
folder inside a character budget, listing shared code first when the budget is
tight.

``sutra map`` is that technique, kept dependency-free: stdlib only, no network,
no model, and honest about its limits (a regex is not a parser, so
nested and dynamic definitions are missed).

The map is advisory. It is never used to decide what SUTRA may drop, and it
does not read file contents into the model by itself: the caller prints it or
ignores it.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SOURCE_SUFFIXES = {
    ".py": "py", ".pyi": "py",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "js", ".tsx": "js",
    ".go": "go", ".rs": "rs", ".rb": "rb",
    ".java": "jvm", ".kt": "jvm", ".swift": "jvm", ".cs": "jvm", ".php": "jvm",
}
SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "out", "vendor", "venv", ".venv",
    "__pycache__", "coverage", ".next", "target", "migrations", "fixtures",
    "tests", "test", "__tests__", "spec", "specs",
}
SKIP_FILES = re.compile(
    r"(^|/)(test_[^/]*|[^/]*(_test|\.test|\.spec|\.gen|\.min|\.d)\.[a-z]+)$")
SHARED = re.compile(
    r"(util|helper|lib|common|shared|service|component|hook|core|model|schema|api)",
    re.IGNORECASE)
DEFAULT_BUDGET = 2000
MAX_FILES = 4000
_PATTERNS = {
    "py": (re.compile(r"^(?:async\s+)?def\s+([A-Za-z]\w*)", re.MULTILINE),
           re.compile(r"^class\s+([A-Za-z]\w*)", re.MULTILINE)),
    "js": (re.compile(
        r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|var|interface|type|enum)\s+([A-Za-z_$][\w$]*)",
        re.MULTILINE),
        re.compile(r"^export\s*\{([^}]*)\}", re.MULTILINE)),
    "go": (re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)", re.MULTILINE),
           re.compile(r"^type\s+([A-Z]\w*)", re.MULTILINE)),
    "rs": (re.compile(
        r"^pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type)\s+(\w+)",
        re.MULTILINE),),
    "rb": (re.compile(r"^\s*(?:def|class|module)\s+([A-Za-z][\w.]*)",
                      re.MULTILINE),),
    "jvm": (re.compile(
        r"^\s*(?:public\s+)?(?:static\s+)?(?:final\s+)?(?:class|interface|enum|record)\s+(\w+)",
        re.MULTILINE),),
}


@dataclass(frozen=True)
class Folder:
    """One folder's definitions, in file order."""

    path: str
    names: tuple[str, ...]
    shared: bool

    def line(self) -> str:
        return f"{self.path or '.'}/: {', '.join(self.names)}"


def _git_files(root: Path) -> list[str] | None:
    """Tracked files, or None when this is not a usable git checkout."""
    try:
        completed = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True,
            timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return [line for line in completed.stdout.splitlines() if line][:MAX_FILES * 5]


def _walk_files(root: Path) -> list[str]:
    found: list[str] = []
    for current, directories, files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        if depth >= 6 or len(found) > MAX_FILES * 5:
            continue
        directories[:] = sorted(
            name for name in directories
            if not name.startswith(".") and name not in SKIP_DIRS)
        for name in sorted(files):
            found.append(
                str(Path(current, name).relative_to(root)).replace(os.sep, "/"))
    return found


def _names(path: Path, language: str) -> tuple[str, ...]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    names: list[str] = []
    for pattern in _PATTERNS.get(language, ()):
        for match in pattern.finditer(text):
            captured = match.group(1)
            for part in captured.split(","):
                name = part.strip().split(" as ")[-1].strip()
                name = name.split(".")[-1].strip()
                if name and name.isidentifier() and name not in names:
                    names.append(name)
    return tuple(names)


def build(root: str | Path = ".", *, budget: int = DEFAULT_BUDGET,
          max_files: int = MAX_FILES) -> str:
    """Return a character-budgeted map of top-level definitions by folder."""
    root_path = Path(root).resolve()
    if budget <= 0:
        raise ValueError("budget must be positive")
    listed = _git_files(root_path)
    if listed is None:
        listed = _walk_files(root_path)
    folders: dict[str, list[str]] = {}
    seen_files = 0
    for relative in listed:
        if seen_files >= max_files:
            break
        if not relative or relative.startswith("."):
            continue
        if SKIP_FILES.search(relative):
            continue
        parts = relative.split("/")
        if any(part in SKIP_DIRS for part in parts[:-1]):
            continue
        suffix = Path(relative).suffix.lower()
        language = SOURCE_SUFFIXES.get(suffix)
        if language is None:
            continue
        file_path = root_path / relative
        if not file_path.is_file():
            continue
        names = _names(file_path, language)
        if not names:
            continue
        seen_files += 1
        folder = "/".join(parts[:-1])
        entry = folders.setdefault(folder, [])
        for name in names:
            if name not in entry:
                entry.append(name)

    ordered = sorted(
        (Folder(folder, tuple(names), bool(SHARED.search(folder)))
         for folder, names in folders.items()),
        key=lambda entry: (not entry.shared, entry.path))

    lines: list[str] = []
    used = 0
    truncated = False
    for folder in ordered:
        line = folder.line()
        if used + len(line) + 1 > budget:
            truncated = True
            break
        lines.append(line)
        used += len(line) + 1
    if truncated:
        note = f"... map truncated at {budget} characters"
        if used + len(note) + 1 <= budget:
            lines.append(note)
    return "\n".join(lines)


def build_lines(root: str | Path = ".", *, budget: int = DEFAULT_BUDGET,
                max_files: int = MAX_FILES) -> list[str]:
    """Same as :func:`build`, but as lines (useful for tests and JSON output)."""
    text = build(root, budget=budget, max_files=max_files)
    return text.splitlines() if text else []


__all__ = ["Folder", "build", "build_lines", "DEFAULT_BUDGET"]
