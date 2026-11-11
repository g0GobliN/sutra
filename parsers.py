"""Deterministic structured CLI output parsers for SUTRA (Phase Two).

One parser per CLI output family: Jest, Vitest, pytest, cargo test, go test,
tsc, mypy, cargo check, Go compiler, Python tracebacks, Node.js stack traces,
ESLint, npm, pnpm, Yarn and docker build.

Each parser implements the :class:`Parser` contract::

    detect(text)                 -- does ``text`` look like this format?
    extract_evidence(text)       -- failures/errors worth preserving
    find_repetitive_sections(text)  -- identical passing lines to collapse
    compress(text, limits)       -- deterministic byte-bounded compression
    fallback(text)               -- the input, unchanged, when nothing is certain

All matching is line/regex based: no LLM, no new runtime dependencies. Every
parser fails open -- an exception inside a parser is treated as "not this
format" and the original text falls through to the existing head-tail
compressor (see ``engine._compress_impl``).

``PARSER_REGISTRY.find(text)`` returns the first parser whose ``detect`` is
true, so registration order is significance order: specific formats first.
"""

from __future__ import annotations

import io
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from . import utf8

logger = logging.getLogger(__name__)

__all__ = [
    "Evidence",
    "Section",
    "CompressedResult",
    "Limits",
    "Parser",
    "ParserRegistry",
    "PARSER_REGISTRY",
    "register_parser",
    "CargoCheckParser",
    "CargoTestParser",
    "DockerBuildParser",
    "ESLintParser",
    "GoCompilerParser",
    "GoTestParser",
    "JestParser",
    "MypyParser",
    "NodeStackTraceParser",
    "NpmParser",
    "PnpmParser",
    "PythonTracebackParser",
    "PytestParser",
    "TscParser",
    "VitestParser",
    "YarnParser",
    "ParsedOutput",
    "AnsibleParser",
    "HelmParser",
    "ArgoCDParser",
    "FluxParser",
    "TalosctlParser",
    "CiliumParser",
    "IstioctlParser",
    "VeleroParser",
    "KubebuilderParser",
    "OperatorSdkParser",
]


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Evidence:
    """Critical evidence extracted from one tool result.

    ``failures`` and ``errors`` are the lines worth keeping when output is
    compressed; ``summary`` is the run's final verdict line.
    """

    family: str
    failures: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    summary: str = ""
    passed: int = 0
    failed: int = 0
    total: int = 0


@dataclass(frozen=True)
class Section:
    """One contiguous run of lines within a text's line list.

    ``start`` is inclusive and ``end`` is exclusive, both zero-based indices
    into the result of ``text.splitlines(keepends=True)``.
    """

    start: int
    end: int
    kind: str  # "pass" | "failure" | "summary" | "error"
    body: str


@dataclass(frozen=True)
class CompressedResult:
    """The deterministic result of ``Parser.compress``."""

    text: str
    changed: bool = False
    rule: str = ""
    omitted_bytes: int = 0
    partial: bool = False


@dataclass(frozen=True)
class Limits:
    """Byte budget handed to ``Parser.compress``."""

    max_bytes: int
    head_bytes: int
    tail_bytes: int


@dataclass(frozen=True)
class ParsedOutput:
    """Structured result of parsing one CLI tool's output.

    ``rows`` holds table rows as ``{column: value}`` mappings (header names
    are normalised to lower case), ``key_values`` holds ``key: value`` /
    ``key=value`` fields, and ``raw`` is the original input text for
    reference. ``tool`` and ``subcommand`` identify the CLI family and the
    subcommand that produced the output.
    """

    tool: str
    command: str
    subcommand: str | None
    rows: list[dict[str, str]]
    key_values: dict[str, str]
    raw: str


# --------------------------------------------------------------------------- #
# Parser contract
# --------------------------------------------------------------------------- #


class Parser(ABC):
    name: str = ""

    @abstractmethod
    def detect(self, text: str) -> bool:
        """True when ``text`` looks like this parser's format."""

    @abstractmethod
    def extract_evidence(self, text: str) -> Evidence:
        """Critical evidence worth preserving: failures, errors, summary."""

    @abstractmethod
    def find_repetitive_sections(self, text: str) -> list[Section]:
        """Runs of identical passing lines that may be collapsed."""

    @abstractmethod
    def compress(self, text: str, limits: Limits) -> CompressedResult:
        """Deterministically compress ``text`` within ``limits``."""

    def fallback(self, text: str) -> str:
        """Safe return on uncertainty: the input unchanged."""
        return text


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class ParserRegistry:
    """Ordered list of parsers; ``find`` returns the first match."""

    def __init__(self) -> None:
        self._parsers: list[Parser] = []

    def register(self, parser: Parser) -> Parser:
        self._parsers.append(parser)
        return parser

    def find(self, text: str) -> Parser | None:
        for parser in self._parsers:
            try:
                if parser.detect(text):
                    return parser
            except Exception:  # pragma: no cover - defensive
                logger.exception("sutra: parser %r raised during detect",
                                 parser.name)
        return None

    def __iter__(self):  # pragma: no cover - introspection helper
        return iter(self._parsers)


PARSER_REGISTRY = ParserRegistry()


def register_parser(parser: Parser) -> Parser:
    """Register ``parser`` on the default registry and return it."""
    return PARSER_REGISTRY.register(parser)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# A passing line: a jest/vitest checkmark, a pytest PASSED, or a "… ok"
# cargo/go test result. Only identical consecutive pass lines collapse.
_PASS_RE = re.compile(r"(✓|\bPASSED\b|\bok\b)")
# Never-match regex used as a default for optional signals.
_NEVER_RE = re.compile(r"$^")


def _split_lines(text: str) -> list[str]:
    """Split ``text`` into lines, keeping terminators (LF-only semantics)."""
    return list(io.StringIO(text))


def _strip_ansi(line: str) -> str:
    return _ANSI_RE.sub("", line)


def _utf8_bytes(text: str) -> int:
    return len(text.encode("utf-8", "replace"))


def _fit_under(text: str, max_bytes: int, marker: str) -> str:
    """Elide the middle of ``text`` so its UTF-8 size fits ``max_bytes``.

    Cuts move only to UTF-8 character boundaries so every piece decodes on its
    own. Returns ``text`` unchanged when it already fits.
    """
    data = text.encode("utf-8", "replace")
    if len(data) <= max_bytes:
        return text
    marker_bytes = marker.encode("utf-8", "replace")
    budget = max_bytes - len(marker_bytes)
    if budget <= 0:
        return marker_bytes.decode("utf-8", "replace")
    head_b = budget // 2
    tail_b = budget - head_b
    head_cut = utf8.end_at_or_before(data, head_b)
    tail_start = utf8.start_at_or_after(data, max(0, len(data) - tail_b))
    if tail_start <= head_cut:
        return marker_bytes.decode("utf-8", "replace")
    head = data[:head_cut].decode("utf-8", "replace")
    tail = data[tail_start:].decode("utf-8", "replace")
    return head + marker_bytes.decode("utf-8", "replace") + tail


# --------------------------------------------------------------------------- #
# Shared machinery
# --------------------------------------------------------------------------- #


class _BaseParser(Parser):
    """Common machinery; subclasses set FAIL_RE / SUMMARY_RE / detect().

    ``compress`` keeps failure lines, their context and summary lines, drops
    everything else, and collapses identical consecutive pass lines into
    ``[N tests passed, omitted]``.
    """

    FAIL_RE = _NEVER_RE
    SUMMARY_RE = _NEVER_RE
    CONTEXT = 2

    def _clean(self, line: str) -> str:
        return _strip_ansi(line)

    def _keep_mask(self, lines: list[str]) -> list[bool]:
        keep = [False] * len(lines)
        for i, line in enumerate(lines):
            cleaned = self._clean(line)
            if (self.FAIL_RE.search(cleaned)
                    or self.SUMMARY_RE.search(cleaned)):
                lo = max(0, i - self.CONTEXT)
                hi = min(len(lines), i + self.CONTEXT + 1)
                for k in range(lo, hi):
                    keep[k] = True
        return keep

    def find_repetitive_sections(self, text: str) -> list[Section]:
        lines = _split_lines(text)
        sections: list[Section] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if _PASS_RE.search(self._clean(line)):
                j = i + 1
                while j < len(lines) and lines[j] == line:
                    j += 1
                if j - i > 1:
                    sections.append(Section(i, j, "pass",
                                            "".join(lines[i:j])))
                    i = j
                    continue
            i += 1
        return sections

    def extract_evidence(self, text: str) -> Evidence:
        lines = _split_lines(text)
        failures = [lines[i].rstrip("\n")
                    for i in range(len(lines))
                    if self.FAIL_RE.search(self._clean(lines[i]))]
        summary = ""
        for i in range(len(lines) - 1, -1, -1):
            if self.SUMMARY_RE.search(self._clean(lines[i])):
                summary = lines[i].rstrip("\n")
                break
        failed = len(failures)
        passed = 0
        match = re.search(r"(\d+)\s+passed", summary, re.I)
        if match:
            passed = int(match.group(1))
        match = re.search(r"(\d+)\s+failed", summary, re.I)
        if match:
            failed = int(match.group(1))
        return Evidence(family=self.name, failures=tuple(failures),
                        errors=tuple(failures), summary=summary,
                        passed=passed, failed=failed,
                        total=passed + failed)

    def _emit(self, kept: list[str], text: str, limits: Limits
              ) -> CompressedResult:
        """Turn kept lines into a :class:`CompressedResult`, eliding if needed."""
        if not kept:
            return CompressedResult(text=text, changed=False)
        result = "".join(kept)
        if result == text:
            return CompressedResult(text=text, changed=False)
        if _utf8_bytes(result) <= limits.max_bytes:
            return CompressedResult(
                text=result, changed=True, rule=self.name,
                omitted_bytes=_utf8_bytes(text) - _utf8_bytes(result),
                partial=True)
        marker = (f"[sutra: {_utf8_bytes(text) - _utf8_bytes(result)} bytes "
                  f"omitted · parser: {self.name}]\n")
        fitted = _fit_under(result, limits.max_bytes, marker)
        return CompressedResult(
            text=fitted, changed=True, rule=self.name,
            omitted_bytes=_utf8_bytes(text) - _utf8_bytes(fitted),
            partial=True)

    def compress(self, text: str, limits: Limits) -> CompressedResult:
        if not self.detect(text):
            return CompressedResult(text=text, changed=False)
        lines = _split_lines(text)
        # Collapse every run of identical passing lines (PASS/PASSED/ok/checkmark)
        # into a single "[N tests passed, omitted]" marker *before* the
        # failure-aware keep mask runs, so long passing runs are summarized
        # wherever they appear instead of only inside failure context.
        collapsed: list[str] = []
        cursor = 0
        for section in self.find_repetitive_sections(text):
            collapsed.extend(lines[cursor : section.start])
            collapsed.append(
                f"[{section.end - section.start} tests passed, omitted]\n"
            )
            cursor = section.end
        collapsed.extend(lines[cursor:])
        keep = self._keep_mask(collapsed)
        if not any(keep):
            return CompressedResult(text=text, changed=False)
        out_lines: list[str] = []
        i = 0
        while i < len(collapsed):
            if not keep[i]:
                i += 1
                continue
            line = collapsed[i]
            j = i + 1
            while (j < len(collapsed) and keep[j] and collapsed[j] == line
                   and _PASS_RE.search(self._clean(line))):
                j += 1
            if j - i > 1:
                out_lines.append(f"[{j - i} tests passed, omitted]\n")
                i = j
                continue
            out_lines.append(line)
            i += 1
        return self._emit(out_lines, text, limits)


class _CliParser(Parser):
    """Base for structured CLI output parsers (helm, argocd, flux, ...).

    Unlike the compression parsers above, these extract structured table and
    key-value data from a CLI tool's output. Their main entry point is
    :meth:`parse`, which returns a :class:`ParsedOutput`.

    The compression contract (``detect``, ``extract_evidence``,
    ``find_repetitive_sections``, ``compress``) is implemented as fail-open
    no-ops: these parsers are registered for discovery and structured
    ``parse()``, not for byte-bounded compression. ``detect`` must be
    overridden by each subclass and must be specific enough not to match
    unrelated output.
    """

    name = ""

    def detect(self, text: str) -> bool:
        raise NotImplementedError

    def extract_evidence(self, text: str) -> Evidence:
        return Evidence(family=self.name)

    def find_repetitive_sections(self, text: str) -> list[Section]:
        return []

    def compress(self, text: str, limits: Limits) -> CompressedResult:
        # Structured parsers do not compress; return input unchanged.
        return CompressedResult(text=text, changed=False)

    def fallback(self, text: str) -> str:
        return text

    def parse(self, text: str) -> ParsedOutput:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Shared helpers for structured CLI output parsing
# --------------------------------------------------------------------------- #

_WS_SPLIT = re.compile(r"\s{2,}|\t")
_SEPARATOR_RE = re.compile(r"^[\s\-:|+]+$")
_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*[:=]\s*(.*)\s*$")
_HOST_STATUS_RE = re.compile(r"^(\S+)\s*\|\s*(\S+)\s*=>\s*(.*)$", re.MULTILINE)


def _split_row(line: str) -> list[str]:
    """Split one table row into cells on runs of whitespace or tabs."""
    return [cell for cell in _WS_SPLIT.split(line.strip()) if cell]


def _parse_table(text: str) -> list[dict[str, str]]:
    """Parse whitespace-delimited table output into ``{column: value}`` rows.

    The first non-blank line is the header; a separator line (only dashes,
    colons, pipes and spaces) is skipped. Header names are normalised to
    lower case. Data rows are split on whitespace and zipped with the header;
    missing cells are blank and extra cells are dropped.
    """
    lines = [ln.rstrip("\n") for ln in text.splitlines() if ln.strip()]
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in lines:
        if _SEPARATOR_RE.fullmatch(line.strip()):
            continue
        if header is None:
            header = [col.lower() for col in _split_row(line)]
            continue
        cells = _split_row(line)
        row: dict[str, str] = {}
        for idx, col in enumerate(header):
            row[col] = cells[idx] if idx < len(cells) else ""
        rows.append(row)
    if header is None:
        return []
    return rows


def _parse_key_values(text: str) -> dict[str, str]:
    """Extract ``key: value`` / ``key=value`` fields from ``text``."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _KV_RE.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def _has_structure(text: str) -> bool:
    """True when ``text`` looks like structured table / key-value output."""
    return bool(_parse_table(text)) or bool(_parse_key_values(text)) or any(
        "|" in line or "\t" in line for line in text.splitlines()
    )


def _word_in(text: str, word: str) -> bool:
    """True when ``word`` appears as a standalone token in ``text``.

    Avoids substring false positives (e.g. ``age`` matching ``storage``).
    """
    return re.search(
        r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", text
    ) is not None


def _detect_subcommand(text: str, known: tuple[str, ...]) -> str | None:
    """Return the first known subcommand token found in ``text``."""
    for token in text.split():
        candidate = token.lstrip("-")
        if candidate in known:
            return candidate
    return None


def _json_scalar(value) -> str:
    """Coerce one JSON value into a short string for key/value output."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


class AnsibleParser(_CliParser):
    """Ansible: ``ansible <host> -m <module>`` and ``ansible-inventory --list``."""

    name = "ansible"
    PRIMARY_SUBCOMMAND = "ansible"
    SUBCOMMANDS = ("inventory", "playbook", "ansible")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        if _HOST_STATUS_RE.search(clean):
            return True
        stripped = clean.strip()
        if stripped.startswith("{") and '"' in clean:
            return True
        return "ansible" in clean.lower() and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        key_values: dict[str, str] = _parse_key_values(text)
        rows: list[dict[str, str]] = []

        # ansible-inventory --list emits JSON.
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                key_values = {k: _json_scalar(v) for k, v in data.items()}
            elif isinstance(data, list):
                rows = [{"item": _json_scalar(v)} for v in data]

        # ansible <host> -m ping style:  host | STATUS => {...}
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            match = _HOST_STATUS_RE.match(line.strip())
            if not match:
                continue
            host, status = match.group(1), match.group(2)
            payload_lines = [match.group(3).strip()]
            # Collect indented continuation lines (multi-line JSON payload),
            # including a lone closing bracket that ends the payload.
            j = idx + 1
            while j < len(lines) and lines[j]:
                stripped_j = lines[j].strip()
                if lines[j][0] not in " \t" and stripped_j not in ("}", "]"):
                    break
                payload_lines.append(stripped_j)
                j += 1
            payload = " ".join(payload_lines)
            inner: dict[str, str] = {}
            try:
                inner = json.loads(payload) if payload.startswith("{") else {}
            except json.JSONDecodeError:
                inner = {}
            rows.append({
                "host": host,
                "status": status,
                "payload": payload,
                **{k: _json_scalar(v) for k, v in inner.items()},
            })

        if not rows and not key_values:
            rows = _parse_table(text)
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


class HelmParser(_CliParser):
    """Helm: ``helm list``, ``helm status <release>``, ``helm repo list``."""

    name = "helm"
    PRIMARY_SUBCOMMAND = "list"
    SUBCOMMANDS = ("list", "status", "repo", "get", "install", "uninstall",
                   "upgrade", "rollback")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        return _word_in(clean.lower(), "chart") and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        rows = _parse_table(text)
        key_values = _parse_key_values(text)
        # Key/value output (e.g. ``version: 1.22.0``) is sometimes misread as a
        # degenerate single-column table.  Prefer the key/value interpretation
        # when that happens.
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


class ArgoCDParser(_CliParser):
    """Argo CD: ``argocd app list``, ``argocd app get <name>``."""

    name = "argocd"
    PRIMARY_SUBCOMMAND = "app"
    SUBCOMMANDS = ("app", "app list", "app get", "app sync", "login", "logout")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        return _word_in(clean.lower(), "syncpolicy") and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        rows = _parse_table(text)
        key_values = _parse_key_values(text)
        # Key/value output (e.g. ``version: 1.22.0``) is sometimes misread as a
        # degenerate single-column table.  Prefer the key/value interpretation
        # when that happens.
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


class FluxParser(_CliParser):
    """Flux: ``flux get kustomizations``, ``flux get sources``."""

    name = "flux"
    PRIMARY_SUBCOMMAND = "get"
    SUBCOMMANDS = ("get", "source", "kustomization", "helmrelease", "notify")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        return _word_in(clean.lower(), "age") and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        rows = _parse_table(text)
        key_values = _parse_key_values(text)
        # Key/value output (e.g. ``version: 1.22.0``) is sometimes misread as a
        # degenerate single-column table.  Prefer the key/value interpretation
        # when that happens.
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


class TalosctlParser(_CliParser):
    """Talosctl: ``talosctl get nodes``, ``talosctl get clusters``."""

    name = "talosctl"
    PRIMARY_SUBCOMMAND = "get"
    SUBCOMMANDS = ("get", "nodes", "clusters", "version", "dashboard")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        return _word_in(clean.lower(), "role") and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        rows = _parse_table(text)
        key_values = _parse_key_values(text)
        # Key/value output (e.g. ``version: 1.22.0``) is sometimes misread as a
        # degenerate single-column table.  Prefer the key/value interpretation
        # when that happens.
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


class CiliumParser(_CliParser):
    """Cilium: ``cilium status``, ``cilium service list``, ``cilium endpoint list``."""

    name = "cilium"
    PRIMARY_SUBCOMMAND = "status"
    SUBCOMMANDS = ("status", "service", "endpoint", "config", "trace")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        return _word_in(clean.lower(), "controller") and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        rows = _parse_table(text)
        key_values = _parse_key_values(text)
        # Key/value output (e.g. ``version: 1.22.0``) is sometimes misread as a
        # degenerate single-column table.  Prefer the key/value interpretation
        # when that happens.
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


class IstioctlParser(_CliParser):
    """Istioctl: ``istioctl version``, ``istioctl proxy-config``."""

    name = "istioctl"
    PRIMARY_SUBCOMMAND = "version"
    SUBCOMMANDS = ("version", "proxy-config", "analyze", "dashboard")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        return _word_in(clean.lower(), "mesh") and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        rows = _parse_table(text)
        key_values = _parse_key_values(text)
        # Key/value output (e.g. ``version: 1.22.0``) is sometimes misread as a
        # degenerate single-column table.  Prefer the key/value interpretation
        # when that happens.
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


class VeleroParser(_CliParser):
    """Velero: ``velero get backups``, ``velero get restore``."""

    name = "velero"
    PRIMARY_SUBCOMMAND = "get"
    SUBCOMMANDS = ("get", "backup", "restore", "schedule", "delete")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        return _word_in(clean.lower(), "expires") and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        rows = _parse_table(text)
        key_values = _parse_key_values(text)
        # Key/value output (e.g. ``version: 1.22.0``) is sometimes misread as a
        # degenerate single-column table.  Prefer the key/value interpretation
        # when that happens.
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


class KubebuilderParser(_CliParser):
    """Kubebuilder: ``kubebuilder version``, ``kubebuilder create api``."""

    name = "kubebuilder"
    PRIMARY_SUBCOMMAND = "version"
    SUBCOMMANDS = ("version", "create", "init", "edit")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        return _word_in(clean.lower(), "version") and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        rows = _parse_table(text)
        key_values = _parse_key_values(text)
        # Key/value output (e.g. ``version: 1.22.0``) is sometimes misread as a
        # degenerate single-column table.  Prefer the key/value interpretation
        # when that happens.
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


class OperatorSdkParser(_CliParser):
    """Operator SDK: ``operator-sdk version``, ``operator-sdk generate``."""

    name = "operator-sdk"
    PRIMARY_SUBCOMMAND = "version"
    SUBCOMMANDS = ("version", "generate", "run", "bundle")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        return _word_in(clean.lower(), "commit") and _has_structure(clean)

    def parse(self, text: str) -> ParsedOutput:
        subcommand = _detect_subcommand(text, self.SUBCOMMANDS) or self.PRIMARY_SUBCOMMAND
        rows = _parse_table(text)
        key_values = _parse_key_values(text)
        # Key/value output (e.g. ``version: 1.22.0``) is sometimes misread as a
        # degenerate single-column table.  Prefer the key/value interpretation
        # when that happens.
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        return ParsedOutput(tool=self.name, command=self.name,
                            subcommand=subcommand, rows=rows,
                            key_values=key_values, raw=text)


# --------------------------------------------------------------------------- #
# Concrete parsers (registration order = significance order)
# --------------------------------------------------------------------------- #


class TscParser(_BaseParser):
    """TypeScript compiler: ``error TS2451 file.ts(10,5): ...``"""

    name = "tsc"
    ERROR_RE = re.compile(r"error TS\d+")
    FAIL_RE = ERROR_RE
    SUMMARY_RE = _NEVER_RE

    def detect(self, text: str) -> bool:
        return bool(self.ERROR_RE.search(_strip_ansi(text)))

    def compress(self, text: str, limits: Limits) -> CompressedResult:
        if not self.detect(text):
            return CompressedResult(text=text, changed=False)
        lines = _split_lines(text)
        kept = [ln for ln in lines if self.ERROR_RE.search(self._clean(ln))]
        return self._emit(kept, text, limits)

    def extract_evidence(self, text: str) -> Evidence:
        lines = _split_lines(text)
        errors = [ln.rstrip("\n") for ln in lines
                  if self.ERROR_RE.search(self._clean(ln))]
        return Evidence(family=self.name, failures=tuple(errors),
                        errors=tuple(errors), failed=len(errors),
                        total=len(errors))


class JestParser(_BaseParser):
    name = "jest"
    FAIL_RE = re.compile(r"[✕●]\s|Expected:|Received:|^\s+at\s")
    SUMMARY_RE = re.compile(r"Tests:")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if re.search(r"(?m)^\s*(?:PASS|FAIL)\s", clean):
            score += 1
        if "Test Suites:" in clean:
            score += 2
        if "Tests:" in clean:
            score += 1
        if re.search(r"\b●\s", clean):
            score += 1
        return score >= 2

    def extract_evidence(self, text: str) -> Evidence:
        ev = super().extract_evidence(text)
        lines = _split_lines(text)
        errors = [lines[i].rstrip("\n") for i in range(len(lines))
                  if re.search(r"Expected:|Received:",
                               self._clean(lines[i]))]
        return Evidence(family=ev.family, failures=ev.failures,
                        errors=tuple(errors), summary=ev.summary,
                        passed=ev.passed, failed=ev.failed, total=ev.total)


class VitestParser(_BaseParser):
    name = "vitest"
    FAIL_RE = re.compile(r"\b×\s|AssertionError:|Expected:|Received:|^\s+at\s")
    SUMMARY_RE = re.compile(r"Test Files|Tests ")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if "Test Files" in clean:
            score += 3
        if re.search(r"(?m)^\s*FAIL\s", clean):
            score += 1
        if re.search(r"\b×\s", clean):
            score += 1
        if re.search(r"Tests ", clean):
            score += 1
        return score >= 3


class PytestParser(_BaseParser):
    name = "pytest"
    FAIL_RE = re.compile(r"\bFAILED\b|^\s*E\s+|Error\b|Traceback"
                         r"|^\s*assert ")
    SUMMARY_RE = re.compile(r"short test summary info| passed | failed,"
                            r"| passed in | failed in ")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if "test session starts" in clean:
            score += 2
        if re.search(r"\bPASSED\b", clean):
            score += 1
        if re.search(r"\bFAILED\b", clean):
            score += 1
        if "FAILURES" in clean:
            score += 2
        if "short test summary info" in clean:
            score += 2
        return score >= 3


class CargoTestParser(_BaseParser):
    name = "cargo-test"
    FAIL_RE = re.compile(r"\.\.\.\s+FAILED\s*$|panicked|panic:"
                         r"|^test result: FAILED")
    SUMMARY_RE = re.compile(r"test result:|failures:")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if "test result:" in clean:
            score += 3
        if re.search(r"(?m)^test .*\.\.\. (ok|FAILED)\s*$", clean):
            score += 2
        if "running " in clean:
            score += 1
        if "failures:" in clean:
            score += 2
        return score >= 3


class GoTestParser(_BaseParser):
    name = "go-test"
    FAIL_RE = re.compile(r"^--- FAIL:|panic:|expected |got |\bError\b")
    SUMMARY_RE = re.compile(r"^--- (FAIL|PASS)|^ok |^FAIL\b")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if "--- FAIL:" in clean:
            score += 3
        if "--- PASS:" in clean:
            score += 1
        if re.search(r"^ok\s+\S+\s+\d", clean, re.M):
            score += 1
        if re.search(r"^FAIL\s", clean, re.M):
            score += 2
        return score >= 3


class MypyParser(_BaseParser):
    name = "mypy"
    ERROR_RE = re.compile(r":\s*error:")
    FAIL_RE = ERROR_RE
    SUMMARY_RE = re.compile(r"Found \d+ error")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if self.ERROR_RE.search(clean):
            score += 2
        if re.search(r"\bnote:\b", clean):
            score += 1
        if re.search(r"Found \d+ errors?", clean):
            score += 2
        return score >= 3

    def extract_evidence(self, text: str) -> Evidence:
        lines = _split_lines(text)
        errors = [ln.rstrip("\n") for ln in lines
                  if self.ERROR_RE.search(self._clean(ln))]
        return Evidence(family=self.name, failures=tuple(errors),
                        errors=tuple(errors), failed=len(errors),
                        total=len(errors))


class CargoCheckParser(_BaseParser):
    name = "cargo-check"
    ERROR_RE = re.compile(r"error\[E\d+\]")
    FAIL_RE = re.compile(r"error\[|error:|warning:")
    SUMMARY_RE = re.compile(r"error: could not compile|warning: unused")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if self.ERROR_RE.search(clean):
            score += 4
        if re.search(r"--> [^\s:]+:\d+:\d+", clean):
            score += 1
        if "error: could not compile" in clean:
            score += 2
        return score >= 4


class GoCompilerParser(_BaseParser):
    """Go compiler: ``./main.go:7:16: cannot use ...``"""

    name = "go-compiler"
    ERROR_RE = re.compile(r"^\./[^\s:]+:\d+:\d+:", re.M)
    FAIL_RE = ERROR_RE
    SUMMARY_RE = _NEVER_RE

    def detect(self, text: str) -> bool:
        return bool(self.ERROR_RE.search(_strip_ansi(text)))

    def compress(self, text: str, limits: Limits) -> CompressedResult:
        if not self.detect(text):
            return CompressedResult(text=text, changed=False)
        lines = _split_lines(text)
        kept = [ln for ln in lines if self.ERROR_RE.search(self._clean(ln))]
        return self._emit(kept, text, limits)


class PythonTracebackParser(_BaseParser):
    name = "python-traceback"
    TRACEBACK_RE = re.compile("^Traceback " + re.escape("(most recent call last):") + "[ \t]*$", re.M)
    FAIL_RE = re.compile(r".*")
    SUMMARY_RE = _NEVER_RE

    def detect(self, text: str) -> bool:
        return bool(self.TRACEBACK_RE.search(_strip_ansi(text)))

    def extract_evidence(self, text: str) -> Evidence:
        return Evidence(family=self.name, failures=(text,), errors=(text,),
                        failed=1, total=1)


class NodeStackTraceParser(_BaseParser):
    name = "node-stack-trace"
    ERROR_RE = re.compile(r"\b(Error|TypeError|ReferenceError|SyntaxError"
                          r"|RangeError):")
    FAIL_RE = re.compile(r"^\s+at\s|(Error|TypeError|ReferenceError"
                         r"|SyntaxError|RangeError):")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if self.ERROR_RE.search(clean):
            score += 2
        if re.search(r"(?m)^\s+at\s", clean):
            score += 2
        return score >= 3


class ESLintParser(_BaseParser):
    name = "eslint"
    FAIL_RE = re.compile(r"\b(error|warning)\s+")
    SUMMARY_RE = re.compile(r"problems")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if re.search(r"\bproblems\b", clean):
            score += 2
        if re.search(r"\bno-[a-z]+\b", clean):
            score += 2
        if re.search(r"(?m)^\./\S+\s*$", clean):
            score += 1
        if self.FAIL_RE.search(clean):
            score += 1
        return score >= 3


class NpmParser(_BaseParser):
    name = "npm"
    ERR_RE = re.compile(r"npm ERR!")
    FAIL_RE = ERR_RE
    SUMMARY_RE = _NEVER_RE

    def detect(self, text: str) -> bool:
        return bool(self.ERR_RE.search(_strip_ansi(text)))

    def extract_evidence(self, text: str) -> Evidence:
        lines = _split_lines(text)
        errs = [ln.rstrip("\n") for ln in lines
                if self.ERR_RE.search(self._clean(ln))]
        return Evidence(family=self.name, failures=tuple(errs),
                        errors=tuple(errs), failed=len(errs), total=len(errs))


class PnpmParser(_BaseParser):
    name = "pnpm"
    FAIL_RE = re.compile(r"(?m)^error |ECONNREFUSED|ETIMEDOUT|fetch failed")
    SUMMARY_RE = _NEVER_RE

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if "::::::::::" in clean:
            score += 3
        if re.search(r"(?m)^error ", clean):
            score += 1
        if "ECONNREFUSED" in clean or "ETIMEDOUT" in clean:
            score += 1
        return score >= 4


class YarnParser(_BaseParser):
    name = "yarn"
    FAIL_RE = re.compile(r"(?m)^error |^\s+at\s|Couldn't|version not")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if re.search(r"(?m)^yarn (add|install) v", clean):
            score += 2
        if re.search(r"error Error:", clean):
            score += 2
        if re.search(r"\[\d/4\]", clean):
            score += 1
        return score >= 4


class DockerBuildParser(_BaseParser):
    name = "docker"
    STEP_RE = re.compile(r"(?m)^#\d+\s+\[\d+/\d+\]")
    FAIL_RE = re.compile(r"ERROR:|failed to solve|failed|exit code"
                         r"|did not complete successfully")
    SUMMARY_RE = re.compile(r"failed to solve|ERROR:")

    def detect(self, text: str) -> bool:
        clean = _strip_ansi(text)
        score = 0
        if self.STEP_RE.search(clean):
            score += 3
        if "ERROR:" in clean:
            score += 2
        if "failed to solve" in clean:
            score += 2
        return score >= 4


# --------------------------------------------------------------------------- #
# Registry population (specific formats first)
# --------------------------------------------------------------------------- #

register_parser(TscParser())
register_parser(JestParser())
register_parser(VitestParser())
register_parser(PytestParser())
register_parser(CargoTestParser())
register_parser(GoTestParser())
register_parser(MypyParser())
register_parser(CargoCheckParser())
register_parser(GoCompilerParser())
register_parser(PythonTracebackParser())
register_parser(ESLintParser())
register_parser(NpmParser())
register_parser(PnpmParser())
register_parser(YarnParser())
register_parser(DockerBuildParser())
register_parser(NodeStackTraceParser())

# Structured CLI output parsers for Kubernetes / DevOps tooling (v0.6.1).
register_parser(AnsibleParser())
register_parser(HelmParser())
register_parser(ArgoCDParser())
register_parser(FluxParser())
register_parser(TalosctlParser())
register_parser(CiliumParser())
register_parser(IstioctlParser())
register_parser(VeleroParser())
register_parser(OperatorSdkParser())
register_parser(KubebuilderParser())
