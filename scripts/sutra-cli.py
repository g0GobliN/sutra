#!/usr/bin/env python3
"""SUTRA's generic command-line adapter: compression and recovery as JSON.

For agent environments that can run a subprocess but have no native plugin
API. Hermes users do not need this — the plugin's middleware and
``sutra_restore`` tool already cover that host.

Tool contract (shared with the MCP adapter; see docs/integrations.md):

    compress  stdin (or --file) -> one JSON object on stdout
              {changed, output, marker, id, original_bytes, compressed_bytes, rule}
    restore   --id + recovery arguments -> the sutra_restore JSON envelope
              verbatim (status: ok | not_found | expired | too_large |
              unavailable | error)
    config    the resolved configuration as JSON (no secrets: environment-
              derived values and local paths only)

Exit codes: 0 the operation produced a result (including an explicit
recovery status — the status IS the contract); 1 internal failure or input
above the size cap; 2 usage error. Failures are never silent: a non-zero exit
is accompanied by a JSON error object on stdout and a message on stderr.

Deterministic: no timestamps, no random values in any output. Storage is
isolated per ``HERMES_HOME`` (or ``SUTRA_STORE_DIR``), exactly like the
Hermes plugin.

Usage:
    python3 scripts/sutra-cli.py compress [--tool NAME] [--file PATH] < payload
    python3 scripts/sutra-cli.py restore --id su_... [--mode bytes|lines|search|full] \
        [--field /output] [--start N] [--end N] [--query Q] [--context N] \
        [--max-bytes N]
    python3 scripts/sutra-cli.py config
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_INPUT_BYTES = 64 * 1024 * 1024  # the engine refuses larger inputs anyway

RECOVERY_ARG_MAP = {
    "mode": "mode", "field": "field", "start": "start", "end": "end",
    "query": "query", "context": "context",
    "case-sensitive": "case_sensitive", "max-bytes": "max_bytes",
    "max-matches": "max_matches",
}


def load_sutra():
    name = "sutra_cli_pkg"  # unique: never collide with other loaders
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _emit(payload: dict, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return code


def _fail(message: str, code: int) -> int:
    print(f"sutra-cli: {message}", file=sys.stderr)
    _emit({"error": message}, code)
    return code


def _read_input(limit: int) -> str:
    data = sys.stdin.buffer.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"input exceeds {limit:,} bytes")
    return data.decode("utf-8", "replace")


def cmd_compress(args: argparse.Namespace) -> int:
    sutra = load_sutra()
    try:
        text = pathlib.Path(args.file).read_text(encoding="utf-8", errors="replace") \
            if args.file else _read_input(MAX_INPUT_BYTES)
    except (OSError, ValueError) as exc:
        return _fail(f"could not read input: {exc}", 1)
    out = sutra.compress_result(text, tool_name=args.tool or "")
    marker = "[sutra:" in out
    entry_id = None
    if marker:
        import re
        match = re.search(r"id=(su_[0-9a-f]{32})", out)
        entry_id = match.group(1) if match else None
    return _emit_compress(out, text, marker, entry_id)


def _emit_compress(out: str, text: str, marker: bool, entry_id) -> int:
    original = len(text.encode("utf-8", "replace"))
    return _emit({
        "changed": out != text,
        "output": out,
        "marker": marker,
        "id": entry_id,
        "original_bytes": original,
        "compressed_bytes": len(out.encode("utf-8")),
    }, 0)


def cmd_restore(args: argparse.Namespace) -> int:
    sutra = load_sutra()
    request = {"id": args.id}
    for cli_name, protocol_name in RECOVERY_ARG_MAP.items():
        value = getattr(args, cli_name.replace("-", "_"), None)
        if value is not None:
            request[protocol_name] = value
    response = json.loads(sutra.recovery.restore(request))
    print(json.dumps(response, ensure_ascii=False))
    return 0  # an explicit status (not_found, expired, ...) is a result, not a failure


def cmd_config(_args: argparse.Namespace) -> int:
    sutra = load_sutra()
    print(json.dumps(sutra.configuration().as_dict(), ensure_ascii=False, default=str))
    return 0


class _JsonArgumentParser(argparse.ArgumentParser):
    """Usage errors keep the contract: JSON error on stdout, message on stderr."""

    def error(self, message: str):  # pragma: no cover - exercised via subprocess
        print(f"sutra-cli: {message}", file=sys.stderr)
        _emit({"error": message}, 2)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _JsonArgumentParser(prog="sutra-cli", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    compress = sub.add_parser("compress", help="compress a tool output read from stdin")
    compress.add_argument("--tool", default="", help="tool name recorded with the entry (max 64 chars)")
    compress.add_argument("--file", default=None, help="read from this file instead of stdin")
    compress.set_defaults(func=cmd_compress)

    restore = sub.add_parser("restore", help="retrieve retained output (sutra_restore protocol)")
    restore.add_argument("--id", required=True, help="the id from a SUTRA marker")
    for cli_name, protocol_name in RECOVERY_ARG_MAP.items():
        if protocol_name == "mode":
            restore.add_argument("--mode", choices=["bytes", "lines", "search", "full"],
                                 default=None)
        elif protocol_name in ("start", "end", "context", "max-bytes".replace("-", "_"), "max-matches".replace("-", "_")) or cli_name in ("max-bytes", "max-matches"):
            restore.add_argument(f"--{cli_name}", type=int, default=None, dest=cli_name.replace("-", "_"))
        elif protocol_name == "case_sensitive":
            restore.add_argument("--case-sensitive", action="store_true", default=None)
        else:
            restore.add_argument(f"--{cli_name}", default=None)
    restore.set_defaults(func=cmd_restore)

    config = sub.add_parser("config", help="print the resolved configuration as JSON")
    config.set_defaults(func=cmd_config)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 0
    except Exception as exc:  # never a silent failure
        return _fail(f"{type(exc).__name__}: {exc}", 1)


if __name__ == "__main__":
    raise SystemExit(main())
