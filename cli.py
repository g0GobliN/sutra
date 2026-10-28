"""The `sutra` console command: the one owner of the command surface.

It parses the arguments, then hands each command to its owner: `install` and
`uninstall` to `installer`, and the tool contract (compress, restore, config)
to the bundled `scripts/sutra-cli.py` through `runner`.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, installer, runner

USAGE = """sutra {version}

usage:
  sutra install [--client opencode] [--config PATH] [--dry-run]
  sutra uninstall [--client opencode] [--config PATH] [--dry-run]
  sutra compress [--tool NAME] [--file PATH]   (stdin otherwise)
  sutra restore --id ID [--mode bytes|lines|search|full] ...
  sutra config
  sutra --version

The first two commands edit a client config. The rest are the tool contract
(see docs/integrations.md); they print one JSON object on stdout.
"""


def _parser(action: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"sutra {action}",
        description="Register SUTRA's MCP server for a client.")
    parser.add_argument("--client", default="opencode", choices=["opencode"],
                        help="the client whose config to edit (opencode is the verified one)")
    parser.add_argument("--config", default=None, help="override the config path")
    parser.add_argument("--dry-run", action="store_true", help="print the change; write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["install"], ["uninstall"]):
        action = argv[0]
        args = _parser(action).parse_args(argv[1:])
        path = installer.opencode_config(args.config)
        operation = installer.install if action == "install" else installer.uninstall
        return operation(path, dry_run=args.dry_run)
    if argv[:1] and argv[0] in ("compress", "restore", "config"):
        return runner.run_script(
            "sutra-cli.py", argv,
            missing="sutra: sutra-cli.py is missing from this installation")
    if argv[:1] in (["--version"], ["-V"], ["version"]):
        print(f"sutra {__version__}")
        return 0
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE.format(version=__version__))
        return 0
    print(f"sutra: unknown command {argv[0]!r}; run `sutra --help`", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
