# Parsers — built-in and custom

SUTRA ships deterministic parsers for common CLI output families (pytest, Jest,
cargo, Go, TypeScript, linters, package managers, Docker, Kubernetes tooling and
more). They live in `sutra/parsers.py` and are tried before the generic
head-tail compressor.

A parser can also be registered at runtime with the public `register_parser()`
API. There is **no** configuration-file loader, entry-point discovery or
`register_custom_parser()` function; a custom parser is active only after the
module that registers it has been imported in the current process.

## The `Parser` contract

`Parser` is an abstract base class. A concrete parser implements four methods
and may override `fallback()`:

```python
class Parser(ABC):
    name: str = ""

    def detect(self, text: str) -> bool: ...
    def extract_evidence(self, text: str) -> Evidence: ...
    def find_repetitive_sections(self, text: str) -> list[Section]: ...
    def compress(self, text: str, limits: Limits) -> CompressedResult: ...
    def fallback(self, text: str) -> str: ...
```

| Type | Fields |
|---|---|
| `Evidence` | `family`, `failures`, `errors`, `summary`, `passed`, `failed`, `total` |
| `Section` | `start`, `end`, `kind`, `body` (line indices from `splitlines(keepends=True)`) |
| `Limits` | `max_bytes`, `head_bytes`, `tail_bytes` |
| `CompressedResult` | `text`, `changed`, `rule`, `omitted_bytes`, `partial` |
| `ParsedOutput` | `tool`, `command`, `subcommand`, `rows`, `key_values`, `raw` |

`Evidence`, `Section`, `Limits` and `CompressedResult` are frozen dataclasses and
are exported from `sutra.parsers`. `ParsedOutput` is used by the built-in
structured CLI parsers; the engine does not call a `parse()` method, so a custom
parser that wants structured output must expose and call its own parser method.

## Registering a parser

`register_parser(parser_instance)` appends an **instance** to the process-wide
`PARSER_REGISTRY` and returns it. `PARSER_REGISTRY.find(text)` returns the first
registered parser whose `detect()` returns true, so registration order matters:
built-ins are registered first, and a custom parser is checked after them.

```python
from sutra.parsers import (
    CompressedResult,
    Evidence,
    Limits,
    Parser,
    register_parser,
)


class MyCorpParser(Parser):
    name = "mycorp-cli"

    def detect(self, text: str) -> bool:
        lowered = text.lower()
        return "mycorp-cli" in lowered and "status" in lowered

    def extract_evidence(self, text: str) -> Evidence:
        lines = [line.rstrip("\n") for line in text.splitlines(keepends=True)]
        failures = tuple(line for line in lines if "error" in line.lower())
        summary = next(
            (line for line in reversed(lines) if "mycorp-cli" in line.lower()),
            "",
        )
        return Evidence(
            family=self.name,
            failures=failures,
            errors=failures,
            summary=summary,
        )

    def find_repetitive_sections(self, text: str) -> list:
        return []

    def compress(self, text: str, limits: Limits) -> CompressedResult:
        if not self.detect(text):
            return CompressedResult(text=text)
        kept = [
            line
            for line in text.splitlines(keepends=True)
            if "error" in line.lower() or "mycorp-cli" in line.lower()
        ]
        output = "".join(kept)
        if (
            not output
            or output == text
            or len(output.encode("utf-8")) > limits.max_bytes
        ):
            return CompressedResult(text=text)
        return CompressedResult(
            text=output,
            changed=True,
            rule=self.name,
            omitted_bytes=len(text.encode("utf-8"))
            - len(output.encode("utf-8")),
            partial=True,
        )


register_parser(MyCorpParser())
```

The parser is now eligible for `sutra.compress()` in this process:

```python
from sutra.parsers import PARSER_REGISTRY

parser = PARSER_REGISTRY.find(mycorp_output)
if parser is not None:
    evidence = parser.extract_evidence(mycorp_output)
```

## Runtime behavior and failure handling

- The engine calls `detect()` and `compress()` only for plain-text payloads.
  JSON, patches and tracebacks take their own paths and never reach a parser.
- A parser result is used only when `result.text` differs from the input. Any
  exception in `detect()` or `compress()` is logged and SUTRA falls through to
  the head-tail compressor, so a broken parser cannot break the tool call.
- A parser that returns malformed JSON for an original JSON document is
  rejected and the original text is kept.
- Compression must be deterministic for the same input and limits, and should
  respect `limits.max_bytes`. A result larger than the input is pointless even
  if the engine accepts it.
- `fallback()` defaults to returning the input unchanged; override it only if
  your parser needs a different safe result.

## Testing a parser

Use real captured output and check all four methods directly:

```python
parser = MyCorpParser()
assert parser.detect(sample)
assert parser.find_repetitive_sections(sample) == []
result = parser.compress(sample, Limits(24_576, 8_192, 8_192))
assert len(result.text.encode("utf-8")) <= 24_576
```

There is no unregister API. Import the registration module before compressing;
tests should use a fresh registry or a uniquely named parser to avoid affecting
the built-in order.
