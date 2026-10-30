#!/usr/bin/env python3
"""SUTRA's generic command-line adapter: compression and recovery as JSON.

For agent environments that can run a subprocess but have no native plugin
API. Hermes users do not need this — the plugin's middleware and
``sutra_restore`` tool already cover that host.

Tool contract (shared with the MCP adapter; see docs/integrations.md):

    compress  stdin (or --file) -> one JSON object on stdout
              {changed, output, marker, id, original_bytes, compressed_bytes}
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
        [--case-sensitive | --ignore-case] [--max-bytes N]
    python3 scripts/sutra-cli.py config
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from _sutra import load_sutra

MAX_INPUT_BYTES = 64 * 1024 * 1024  # the engine refuses larger inputs anyway

RECOVERY_ARG_MAP = {
    "mode": "mode", "field": "field", "start": "start", "end": "end",
    "query": "query", "context": "context",
    "max-bytes": "max_bytes", "max-matches": "max_matches",
}


def _emit(payload: dict, code: int) -> int:
    # ASCII escapes keep the JSON wire valid even when the host's stdout is a
    # non-UTF-8 locale; the caller gets the same string after json.loads().
    print(json.dumps(payload, ensure_ascii=True))
    return code


def _fail(message: str, code: int) -> int:
    print(f"sutra-cli: {message}", file=sys.stderr)
    _emit({"error": message}, code)
    return code


def _decode_utf8(data: bytes, source: str) -> str:
    """Decode strictly: replacement characters are a silent data loss."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{source} is not valid UTF-8 (byte {exc.start})") from None


def _read_input(limit: int) -> str:
    data = sys.stdin.buffer.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"input exceeds {limit:,} bytes")
    return _decode_utf8(data, "stdin")


def cmd_compress(args: argparse.Namespace) -> int:
    sutra = load_sutra()
    try:
        if args.file:
            data = pathlib.Path(args.file).read_bytes()
            text = _decode_utf8(data, str(args.file))
        else:
            text = _read_input(MAX_INPUT_BYTES)
    except (OSError, ValueError) as exc:
        return _fail(f"could not read input: {exc}", 1)
    outcome = sutra.core.process_text(text, tool_name=args.tool or "")
    return _emit(sutra.core.outcome_json(outcome), 0)


def cmd_restore(args: argparse.Namespace) -> int:
    sutra = load_sutra()
    request = {"id": args.id}
    for cli_name, protocol_name in RECOVERY_ARG_MAP.items():
        value = getattr(args, cli_name.replace("-", "_"), None)
        if value is not None:
            request[protocol_name] = value
    if args.case_sensitive is not None:
        request["case_sensitive"] = args.case_sensitive
    response = json.loads(sutra.core.restore(request))
    return _emit(response, 0)  # an explicit recovery status is a result, not a failure


def cmd_config(_args: argparse.Namespace) -> int:
    sutra = load_sutra()
    return _emit(sutra.configuration().as_dict(), 0)


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
        else:
            restore.add_argument(f"--{cli_name}", default=None)
    case = restore.add_mutually_exclusive_group()
    case.add_argument("--case-sensitive", dest="case_sensitive", action="store_true",
                      default=None, help="match case (default)")
    case.add_argument("--ignore-case", dest="case_sensitive", action="store_false",
                      default=None, help="match case-insensitively")
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
