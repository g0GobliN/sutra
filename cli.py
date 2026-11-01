"""The `sutra` console command: the one owner of the command surface.

It parses the arguments, then hands each command to its owner: `install`,
`uninstall` and `status` to the client integrations, and the tool contract
(compress, restore, config) to the bundled `scripts/sutra-cli.py` through
`runner`.

`install` configures every client it can verify. Claude Code gets the native
PostToolUse hook (automatic replacement) plus the MCP recovery server; OpenCode
gets the generated automatic plugin plus, when its config exists, the MCP
recovery entry. The OpenCode plugin runs the Python core per oversized result;
there is no daemon or network listener.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, installer, runner

USAGE = """sutra {version}

usage:
  sutra install [--client hermes|claude|opencode|codex]... [--all] [--config PATH] [--dry-run]
  sutra uninstall [--client hermes|claude|opencode|codex]... [--all] [--config PATH] [--dry-run]
  sutra detect [--client NAME]... [--json]   (read-only client discovery)
  sutra status [--json]
  sutra doctor [--client hermes|claude|opencode|codex]... [--json]  (prove integrations)
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
    if client == "hermes":
        from . import hermes
        if not hermes.binary():
            if required:
                print("sutra: hermes is not installed", file=sys.stderr)
                return 1
            print("sutra: hermes is not installed; skipped")
            return 0
        return hermes.install(dry_run=args.dry_run)
    if client == "codex":
        from . import codex
        if not codex.binary():
            if required:
                print("sutra: codex is not installed", file=sys.stderr)
                return 1
            print("sutra: codex is not installed; skipped")
            return 0
        return codex.install(dry_run=args.dry_run)
    from . import opencode
    status = opencode.install(dry_run=args.dry_run, config_path=args.config)
    if status:
        return status
    path = installer.opencode_config(args.config)
    if not path.exists():
        if args.config:
            print(f"sutra: {path} does not exist; pass a path that exists",
                  file=sys.stderr)
            return 1
        if args.dry_run:
            print("sutra: no OpenCode config found; dry run would skip the MCP "
                  "recovery entry")
        else:
            print("sutra: no OpenCode config found; the automatic plugin is "
                  "installed, MCP recovery entry skipped")
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
    if client == "hermes":
        from . import hermes
        if not hermes.binary():
            print("sutra: hermes is not installed; skipped")
            return 0
        return hermes.uninstall(dry_run=args.dry_run)
    if client == "codex":
        from . import codex
        return codex.uninstall(dry_run=args.dry_run)
    from . import opencode
    status = opencode.uninstall(dry_run=args.dry_run, config_path=args.config)
    path = installer.opencode_config(args.config)
    if path.exists():
        status = max(status, installer.uninstall(path, dry_run=args.dry_run))
    return status


def _install_summary(clients: list[str], results: dict[str, int], dry_run: bool) -> None:
    """One concise, truthful line after an install attempt."""
    try:
        rows = {row.get("client"): row for row in _rows()
                if row.get("client") and "error" not in row}
    except Exception:  # a summary must never turn a completed install into a failure
        rows = {}
    labels: list[str] = []
    for client in clients:
        if results.get(client, 1) != 0:
            labels.append(f"{client}=failed")
            continue
        row = rows.get(client)
        if row is None:
            labels.append(f"{client}=skipped")
        elif client == "claude":
            if row.get("hook") is True and row.get("mcp") == "configured":
                labels.append("claude=configured")
            elif row.get("hook") is True:
                labels.append("claude=hook-only")
            else:
                labels.append("claude=not configured")
        elif client == "hermes":
            if row.get("installed") and row.get("enabled") is True:
                labels.append("hermes=active")
            elif row.get("installed"):
                labels.append("hermes=installed, not enabled")
            else:
                labels.append("hermes=not configured")
        elif client == "opencode":
            if row.get("plugin") == "installed" and row.get("mcp") == "configured":
                labels.append("opencode=configured")
            elif row.get("plugin") == "installed":
                labels.append("opencode=configured, MCP skipped")
            else:
                labels.append("opencode=not configured")
        else:
            if row.get("hook") != "installed":
                labels.append("codex=not configured")
            elif row.get("mcp") != "configured":
                labels.append("codex=hook-only")
            elif row.get("trusted") is not True:
                labels.append("codex=requires /hooks trust")
            else:
                labels.append("codex=active")
    prefix = "sutra: dry-run summary: " if dry_run else "sutra: install summary: "
    print(prefix + " ".join(labels))
    if not dry_run:
        print("sutra: run `sutra doctor` to verify the installed paths")


def _apply(action: str, args) -> int:
    clients = _selected(args)
    if not clients:
        print("sutra: no supported client detected; name one with --client "
              "(hermes, claude, opencode, codex)", file=sys.stderr)
        return 1
    required = bool(args.client)
    status = 0
    results: dict[str, int] = {}
    for client in clients:
        operation = _install_one if action == "install" else _uninstall_one
        try:
            code = operation(client, args, required=required)
        except Exception as exc:  # one broken adapter must not block the others
            print(f"sutra: {client} {action} failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            code = 1
        results[client] = code
        status = max(status, code)
    if action == "install":
        _install_summary(clients, results, args.dry_run)
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
    from . import opencode
    opencode_path = installer.opencode_config()
    rows.append({
        "client": "opencode",
        "binary": shutil.which("opencode"),
        "config": str(opencode_path),
        "mcp": "configured" if installer.opencode_configured(opencode_path) else "not configured",
        "plugin": "installed" if opencode.configured() else "not installed",
        "automatic": opencode.configured(),
        "home": str(opencode.default_home()),
    })
    try:
        from . import hermes
        if hermes.binary() or hermes.plugin_dir().exists() or hermes.config_path().exists():
            rows.append(hermes.status())
    except Exception as exc:
        rows.append({"client": "hermes", "error": str(exc)})
    try:
        from . import codex
        if codex.binary() or codex.config_path().exists():
            rows.append(codex.status())
    except Exception as exc:
        rows.append({"client": "codex", "error": str(exc)})
    return rows


def _status(as_json: bool = False) -> int:
    import json

    rows = _rows()
    configured = any(
        row.get("hook") in (True, "installed")
        or row.get("installed") is True
        or row.get("plugin") == "installed"
        or row.get("automatic") is True
        or row.get("mcp") in ("connected", "verified", "configured")
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
        elif row["client"] == "hermes":
            print(f"hermes: binary={'yes' if row.get('binary') else 'no'} "
                  f"plugin={'installed' if row.get('installed') else 'not installed'} "
                  f"enabled={'yes' if row.get('enabled') is True else 'no'} "
                  f"automatic={'yes' if row.get('automatic') else 'no'} "
                  f"plugin_path={row.get('plugin')}")
        elif row["client"] == "codex":
            trusted = row.get("trusted")
            trusted_text = ("unknown" if trusted is None
                            else ("yes" if trusted else "no"))
            print(f"codex: binary={'yes' if row.get('binary') else 'no'} "
                  f"hook={row.get('hook', 'not installed')} "
                  f"trusted={trusted_text} mcp={row.get('mcp')} "
                  f"automatic={'yes' if row.get('automatic') else 'no'} "
                  f"tier={row.get('tier')} reason={row.get('host_limit')}")
        else:
            print(f"opencode: binary={'yes' if row.get('binary') else 'no'} "
                  f"plugin={row.get('plugin')} mcp={row.get('mcp')} "
                  f"automatic={'yes' if row.get('automatic') else 'no'} "
                  f"config={row.get('config')}")
    return 0 if configured else 1


def _detect(as_json: bool = False, clients: list[str] | None = None) -> int:
    from . import discovery

    rows = discovery.detect(clients)
    if as_json:
        import json
        print(json.dumps({"read_only": True, "clients": rows}, indent=2))
    else:
        print(discovery.format_detect(rows))
    return 0


def _not_installed(client: str) -> dict:
    return {
        "client": client,
        "status": "not-installed",
        "ok": True,
        "automatic": False,
        "probe": "client is not installed",
    }


def _doctor(as_json: bool = False, clients: list[str] | None = None) -> int:
    import shutil

    from . import claude_code, codex, hermes, opencode

    selected = clients or ["claude", "opencode", "hermes", "codex"]
    reports = []
    for client in selected:
        try:
            if client == "claude":
                reports.append(claude_code.doctor() if (
                    claude_code.binary() or claude_code.settings_path().exists())
                    else _not_installed(client))
            elif client == "opencode":
                reports.append(opencode.doctor() if (
                    shutil.which("opencode") or opencode.plugin_path().exists())
                    else _not_installed(client))
            elif client == "hermes":
                reports.append(hermes.doctor() if (
                    hermes.binary() or hermes.plugin_dir().exists()
                    or hermes.config_path().exists()) else _not_installed(client))
            elif client == "codex":
                reports.append(codex.doctor() if (
                    codex.binary() or codex.config_path().exists())
                    else _not_installed(client))
        except Exception as exc:  # a broken adapter must not hide the other three
            reports.append({"client": client, "status": "fail", "ok": False,
                            "automatic": False,
                            "error": f"{type(exc).__name__}: {exc}"})
    if as_json:
        import json
        print(json.dumps(reports, indent=2))
    else:
        for report in reports:
            client = report.get("client")
            if report.get("status") == "not-installed":
                print(f"{client}: not installed")
                continue
            if report.get("error"):
                print(f"{client}: error: {report['error']}")
                continue
            if client == "claude":
                formatter = claude_code.format_doctor
            elif client == "opencode":
                formatter = opencode.format_doctor
            elif client == "hermes":
                formatter = hermes.format_doctor
            else:
                formatter = codex.format_doctor
            print(formatter(report))
    failed = any(
        report.get("error")
        or report.get("status") == "fail"
        or (report.get("ok") is False
            and report.get("status") not in ("not-installed", "limited"))
        for report in reports)
    return 1 if failed else 0


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
    if argv[:1] == ["detect"]:
        parser = argparse.ArgumentParser(
            prog="sutra detect",
            description="Read-only discovery of supported agents; writes nothing.")
        parser.add_argument("--client", action="append",
                            choices=["hermes", "claude", "opencode", "cursor",
                                     "codex", "windsurf"],
                            help="limit the report to this client (repeatable)")
        parser.add_argument("--json", action="store_true", help="machine-readable output")
        args = parser.parse_args(argv[1:])
        return _detect(as_json=args.json, clients=args.client)
    if argv[:1] == ["doctor"]:
        parser = argparse.ArgumentParser(
            prog="sutra doctor",
            description="Prove the installed automatic integrations work.")
        parser.add_argument("--client", action="append",
                            choices=["hermes", "claude", "opencode", "codex"],
                            help="limit the probe to this client (repeatable)")
        parser.add_argument("--json", action="store_true", help="machine-readable output")
        args = parser.parse_args(argv[1:])
        return _doctor(as_json=args.json, clients=args.client)
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
