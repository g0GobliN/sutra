"""The `sutra` console command.

`install`/`uninstall` are implemented here (installer.py); compress, restore
and config stay in scripts/sutra-cli.py, which ships with the package and is
the single implementation the checkout and the wheel share.
"""

from __future__ import annotations

import pathlib
import runpy
import sys

from . import __version__, installer

HERE = pathlib.Path(__file__).resolve().parent

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


def _run_script(name: str, argv: list[str]) -> int:
    script = HERE / "scripts" / name
    if not script.is_file():
        print(f"sutra: {name} is missing from this installation", file=sys.stderr)
        return 1
    sys.argv = [str(script), *argv]
    runpy.run_path(str(script), run_name="__main__")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["install"], ["uninstall"]):
        return installer.main(argv)
    if argv[:1] and argv[0] in ("compress", "restore", "config"):
        return _run_script("sutra-cli.py", argv)
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
