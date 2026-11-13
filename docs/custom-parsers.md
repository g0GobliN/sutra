# Custom Parser SDK — SUTRA v0.6.1

## Overview

SUTRA's parser framework already ships with built-in parsers for common CLI tools (Jest, Pytest, Helm, etc.). This SDK lets **any user** add domain-specific parsers without forking SUTRA or modifying the source tree. Parsers are registered via a simple entry-point API or a configuration file, and SUTRA's `compress()` function automatically discovers and uses them.

## Philosophy

- **Zero-fork**: Add parsers from anywhere Python can import them.
- **Fail-open**: A bad custom parser never breaks the output — it simply returns `None` and SUTRA falls through to head-tail elision.
- **First-class**: Custom parsers receive the same `ParsedOutput` and `CompressedResult` types as built-in parsers.
- **Discovery by convention**: No manual registry mutation needed — just register and SUTRA finds them.

## API Specification

### `sutra.parsers.register_custom_parser(name, parser_class)`

Register a custom parser class on the default global registry.

```python
from sutra.parsers import register_custom_parser

class MyCorpParser:
    name = "mycorp-cli"
    
    def detect(self, text: str) -> bool:
        # Return True when text looks like mycorp-cli output
        return "mycorp-cli" in text.lower() and "status" in text.lower()
    
    def parse(self, text: str) -> ParsedOutput:
        # Return structured ParsedOutput from mycorp-cli output
        ...

register_custom_parser("mycorp-cli", MyCorpParser)
```

**Parameters**

| Parameter | Type | Description |
|---|---|---|
| `name` | `str` | The parser's unique identifier. Used for `detect()` matching and as `ParsedOutput.tool`. |
| `parser_class` | `type` | A subclass of `sutra.parsers.BaseParser` (or `_CliParser` for structured CLI output). |

**Returns**: The registered `Parser` instance.

---

### `sutra.parsers.BaseParser` subclass contract

All custom parsers must inherit from `sutra.parsers.BaseParser` and implement the three abstract methods:

| Method | Signature | Description |
|---|---|---|
| `detect(text: str) -> bool` | `-> bool` | Return `True` when `text` matches this parser's format. Must be idempotent and side-effect free. |
| `extract_evidence(text: str) -> Evidence` | `-> Evidence` | Extract `failures`, `errors`, `summary`, `passed`, `failed`, `total` from the output. |
| `compress(text: str, limits: Limits) -> CompressedResult` | `-> CompressedResult` | Deterministically compress the text, keeping failures/errors and collapsing repetitive pass lines. |

Additionally, custom parsers may override:

| Method | Default | Description |
|---|---|---|
| `fallback(text: str) -> str` | `return text` | Safe return on uncertainty. |
| `find_repetitive_sections(text: str) -> list[Section]` | Implementation in `BaseParser` | Finds runs of identical passing lines. |

---

### `sutra.parsers.ParsedOutput` schema

The structured result returned by `parse()` (inherited from `_CliParser`) :

```python
@dataclass(frozen=True)
class ParsedOutput:
    tool: str          # From parser.name
    command: str       # Typically same as tool
    subcommand: str | None  # Subcommand extracted from output, or None
    rows: list[dict[str, str]]  # Table rows: {column: value} mappings
    key_values: dict[str, str]  # Key:=value fields
    raw: str           # Original input text
```

All fields are immutable (`frozen=True`). Custom parsers using `_CliParser.parse()` get this automatically.

---

### Registration flow

#### 1. Programmatic (Python code)

```python
from sutra.parsers import register_custom_parser

class MyParser(BaseParser):
    name = "mytool"
    
    def detect(self, text: str) -> bool:
        return "mytool" in text.lower()
    
    def extract_evidence(self, text: str) -> Evidence:
        ...
    
    def compress(self, text: str, limits: Limits) -> CompressedResult:
        ...

register_custom_parser("mytool", MyParser)
```

#### 2. Configuration file (`sutra_custom_parsers.py`)

Create a small module anywhere on your Python path:

```python
# sutra_custom_parsers.py
from sutra.parsers import register_custom_parser

class MyToolParser(BaseParser):
    name = "mytool"
    # ... implement abstract methods

register_custom_parser("mytool", MyToolParser)
```

Import it early in your app:

```python
import sutra_custom_parsers  # registers parsers at import time
# Now sutra.compress() will discover MyToolParser
```

#### 3. Entry points (`pyproject.toml` / `setup.cfg`)

Add to your package's `pyproject.toml`:

```toml
[project.entry-points.sutra parsers]
mytool = mypackage.parsers:register_mytool
```

And in `mypackage/parsers.py`:

```python
def register_mytool():
    from sutra.parsers import register_custom_parser
    from .my_parser import MyParser
    register_custom_parser("mytool", MyParser)
```

SUTRA's plugin system auto-loads these entry points on startup.

---

## Example: Custom parser for `mycorp-cli`

Here is a complete, working custom parser for a fictional CLI tool `mycorp-cli` that produces table + key-value output.

```python
# my_corp_parsers.py
from sutra.parsers import BaseParser, ParsedOutput, Evidence, Limits, Section
from sutra.parsers.parsers import _parse_table, _parse_key_values, Evidence
import re

class MyCorpParser(BaseParser):
    """Parser for mycorp-cli tool output."""

    name = "mycorp-cli"

    def detect(self, text: str) -> bool:
        """Return True when text looks like mycorp-cli output."""
        clean = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
        return "mycorp-cli" in clean.lower() and "status" in clean.lower()

    def extract_evidence(self, text: str) -> Evidence:
        """Extract failures/errors and summary from mycorp-cli output."""
        lines = text.splitlines()
        failures = []
        summary = ""
        
        for i, line in enumerate(lines):
            cleaned = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", line)
            if "ERROR" in cleaned.upper() or "FAIL" in cleaned.upper():
                failures.append(cleaned.rstrip("\n"))
            # Summary is the last non-empty line
            if cleaned.strip() and not any(kw in cleaned.upper() for kw in ["ERROR", "FAIL", "PASS"]):
                summary = cleaned.rstrip("\n")
        
        import re
        passed = failed = total = 0
        m = re.search(r"(\d+)\s+passed", summary)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+)\s+failed", summary)
        if m:
            failed = int(m.group(1))
        total = passed + failed
        
        return Evidence(
            family=self.name,
            failures=tuple(failures),
            errors=tuple(failures),
            summary=summary,
            passed=passed,
            failed=failed,
            total=total,
        )

    def compress(self, text: str, limits: Limits) -> CompressedResult:
        """Compress mycorp-cli output, keeping failure lines and collapsing repetitive passes."""
        if not self.detect(text):
            return CompressedResult(text=text, changed=False)
        
        from sutra.parsers.parsers import _split_lines, _strip_ansi, _PASS_RE, CompressedResult
        
        lines = _split_lines(text)
        
        # Keep lines with failures or summary, plus context
        keep = [False] * len(lines)
        for i, line in enumerate(lines):
            cleaned = _strip_ansi(line)
            if "ERROR" in cleaned.upper() or "FAIL" in cleaned.upper():
                lo = max(0, i - 1)
                hi = min(len(lines), i + 2)
                for k in range(lo, hi):
                    keep[k] = True
        
        kept_lines = [lines[i] for i in range(len(lines)) if keep[i]]
        return self._emit(kept_lines, text, limits)

    def _emit(self, kept: list[str], text: str, limits: Limits) -> CompressedResult:
        """Emit compressed result from kept lines."""
        if not kept:
            return CompressedResult(text=text, changed=False)
        result = "\n".join(kept)
        if result == text:
            return CompressedResult(text=text, changed=False)
        
        from sutra.parsers.parsers import _utf8_bytes, _fit_under
        
        if _utf8_bytes(result) <= limits.max_bytes:
            return CompressedResult(
                text=result, changed=True, rule=self.name,
                omitted_bytes=_utf8_bytes(text) - _utf8_bytes(result),
                partial=True,
            )
        
        marker = (f"[sutra: {_utf8_bytes(text) - _utf8_bytes(result)} bytes "
                  f"omitted · parser: {self.name}]\n")
        fitted = _fit_under(result, limits.max_bytes, marker)
        return CompressedResult(
            text=fitted, changed=True, rule=self.name,
            omitted_bytes=_utf8_bytes(text) - _utf8_bytes(fitted),
            partial=True,
        )

    def parse(self, text: str) -> ParsedOutput:
        """Parse mycorp-cli output into structured ParsedOutput."""
        subcommand = None  # mycorp-cli doesn't have meaningful subcommands
        key_values = _parse_key_values(text)
        rows = _parse_table(text)
        
        # If key-values were found but rows is degenerate, prefer key-values
        if key_values and rows and all(len(row) <= 1 for row in rows):
            rows = []
        
        if not rows and not key_values and text.strip():
            rows = [{"output": line} for line in text.splitlines() if line.strip()]
        
        return ParsedOutput(
            tool=self.name,
            command=self.name,
            subcommand=subcommand,
            rows=rows,
            key_values=key_values,
            raw=text,
        )
```

### Using the example parser

```python
# main.py
import sutra
from my_corp_parsers import register_my_corp_parser  # or import the module that registers

# Register the parser (this happens automatically if you import my_corp_parsers)
register_my_corp_parser()

# Now compress mycorp-cli output
result = sutra.compress(mycorp_output_text)
print(f"Compressed ({result[1]['rule']}): {len(result[0])} bytes (changed={result[1]['changed']})")

# Parse the output
parsed = sutra.parsers.PARSER_REGISTRY.find(mycorp_output_text)
if parsed:
    output = parsed.parse(mycorp_output_text)
    print(f"Parsed tool={output.tool}, rows={len(output.rows)}, key_values={output.key_values}")
```

---

## Discovery and loading

Custom parsers are discovered through three mechanisms, in priority order:

1. **`register_custom_parser()` calls** — explicit runtime registration.
2. **Module-side effects** — importing a module that calls `register_custom_parser()` at module load time.
3. **Entry points** — `pyproject.toml`/`setup.cfg` `[project.entry-points.sutra parsers]` definitions that SUTRA auto-loads on startup.

SUTRA scans `importlib.metadata.entry_points()` for `sutra parsers` groups at startup and calls each one. This means you can register parsers purely through package installation.

---

## Best practices

| Practice | Reason |
|---|---|
| **Keep `detect()` specific** | A broad `detect()` prevents other parsers from matching. Include enough identifiers to avoid false positives. |
| **Make `compress()` deterministic** | The compression result must be byte-deterministic for the same input + limits. |
| **Fail open** | If `detect()` returns `True` but parsing fails, return the original text unchanged (or `CompressedResult(text=text, changed=False)`). |
| **Document `name` choice** | The `name` field becomes `ParsedOutput.tool` and appears in logs and reports. Use a unique, lowercase identifier. |
| **Test with real output** | Use actual `mycorp-cli` output to verify `detect()`, `extract_evidence()`, `compress()`, and `parse()` all work end-to-end. |
| **Keep `parse()` lightweight** | `parse()` is for structured extraction; heavy compression logic belongs in `compress()`. |

---

## Version

**SUTRA v0.6.1** — Custom Parser SDK