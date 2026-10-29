"""The `sutra` console command: the one owner of the command surface.

It parses the arguments, then hands each command to its owner: `install`,
`uninstall` and `status` to the client integrations, and the tool contract
(compress, restore, config) to the bundled `scripts/sutra-cli.py` through
`runner`.

`install` configures every client it can verify. Claude Code gets the native
PostToolUse hook (automatic replacement) plus the MCP recovery server; OpenCode
gets the MCP server only, because its tool hook is still a spike.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, installer, runner

USAGE = """sutra {version}

usage:
  sutra install [--client claude|opencode]... [--all] [--config PATH] [--dry-run]
  sutra uninstall [--client claude|opencode]... [--all] [--config PATH] [--dry-run]
  sutra status [--json]
  sutra doctor [--json]        (prove the installed hook compresses + restores)
  sutra compress [--tool NAME] [--file PATH]   (stdin otherwise)
  sutra restore --id ID [--mode bytes|lines|search|full] ...
  sutra config
  sutra --version

`install` auto-detects installed clients unless a `--client` is named. It
writes a backup beside every file it edits and a dry run writes nothing.
"""


def _parser(action: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"sutra {action}",
        description=f"{action.capitalize()} SUTRA's client integrations.")
    parser.add_argument("--client", action="append", choices=list(installer.CLIENTS),
                        help="limit the operation to this client (repeatable)")
    parser.add_argument("--all", action="store_true",
                        help="every supported client, present or not")
    parser.add_argument("--config", default=None,
                        help="override the OpenCode config path")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the change; write nothing")
    return parser


def _selected(args) -> list[str]:
    if args.client:
        return list(dict.fromkeys(args.client))
    if args.config:
        # Backward-compatible: --config has always meant OpenCode.
        return ["opencode"]
    if args.all:
        return list(installer.CLIENTS)
    return installer.detect_clients()


def _install_one(client: str, args, *, required: bool) -> int:
    if client == "claude":
        from . import claude_code
        if not claude_code.binary():
            if required:
                print("sutra: claude is not installed", file=sys.stderr)
                return 1
            print("sutra: claude is not installed; skipped")
            return 0
        return claude_code.install(dry_run=args.dry_run)
    path = installer.opencode_config(args.config)
    if not path.exists() and not args.dry_run:
        if required:
            print(f"sutra: {path} does not exist; open OpenCode once so it writes "
                  f"its config, or pass --config", file=sys.stderr)
            return 1
        print(f"sutra: {path} does not exist; skipped OpenCode")
        return 0
    return installer.install(path, dry_run=args.dry_run)


def _uninstall_one(client: str, args, *, required: bool) -> int:
    if client == "claude":
        from . import claude_code
        if not claude_code.binary():
            if required:
                print("sutra: claude is not installed", file=sys.stderr)
                return 1
            print("sutra: claude is not installed; skipped")
            return 0
        return claude_code.uninstall(dry_run=args.dry_run)
    path = installer.opencode_config(args.config)
    return installer.uninstall(path, dry_run=args.dry_run)


def _apply(action: str, args) -> int:
    clients = _selected(args)
    if not clients:
        print("sutra: no supported client detected; name one with --client "
              "(claude, opencode)", file=sys.stderr)
        return 1
    required = bool(args.client)
    status = 0
    for client in clients:
        operation = _install_one if action == "install" else _uninstall_one
        status = max(status, operation(client, args, required=required))
    return status


def _rows() -> list[dict]:
    import shutil

    rows: list[dict] = []
    try:
        from . import claude_code
        if claude_code.binary() or claude_code.settings_path().exists():
            rows.append(claude_code.status())
    except Exception as exc:  # a broken settings file is reported, not raised
        rows.append({"client": "claude", "error": str(exc)})
    opencode_path = installer.opencode_config()
    rows.append({
        "client": "opencode",
        "binary": shutil.which("opencode"),
        "config": str(opencode_path),
        "mcp": "configured" if installer.opencode_configured(opencode_path) else "not configured",
        "automatic": False,
    })
    return rows


def _status(as_json: bool = False) -> int:
    import json

    rows = _rows()
    configured = any(
        (row.get("hook") is True) or row.get("mcp") in ("connected", "configured")
        for row in rows)
    if as_json:
        print(json.dumps(rows, indent=2))
        return 0 if configured else 1
    for row in rows:
        if "error" in row:
            print(f"{row['client']}: error: {row['error']}")
            continue
        if row["client"] == "claude":
            hook = "yes" if row.get("hook") is True else "no"
            print(f"claude: binary={'yes' if row.get('binary') else 'no'} "
                  f"hook={hook} mcp={row.get('mcp')} "
                  f"automatic={'yes' if row.get('automatic') else 'no'} "
                  f"settings={row.get('settings')}")
        else:
            print(f"opencode: binary={'yes' if row.get('binary') else 'no'} "
                  f"mcp={row.get('mcp')} automatic=no (manual compression) "
                  f"config={row.get('config')}")
    return 0 if configured else 1


def _doctor(as_json: bool = False) -> int:
    from . import claude_code

    if not claude_code.binary():
        print("sutra: claude is not installed; nothing to diagnose", file=sys.stderr)
        return 1
    report = claude_code.doctor()
    if as_json:
        import json
        print(json.dumps(report, indent=2))
    else:
        print(claude_code.format_doctor(report))
    return 0 if report.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["install"], ["uninstall"]):
        action = argv[0]
        args = _parser(action).parse_args(argv[1:])
        return _apply(action, args)
    if argv[:1] == ["status"]:
        parser = argparse.ArgumentParser(prog="sutra status",
                                         description="Report detected client integrations.")
        parser.add_argument("--json", action="store_true", help="machine-readable output")
        args = parser.parse_args(argv[1:])
        return _status(as_json=args.json)
    if argv[:1] == ["doctor"]:
        parser = argparse.ArgumentParser(prog="sutra doctor",
                                         description="Prove the Claude Code automatic hook works.")
        parser.add_argument("--json", action="store_true", help="machine-readable output")
        args = parser.parse_args(argv[1:])
        return _doctor(as_json=args.json)
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
