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
import shutil
import sys
from pathlib import Path

from . import __version__, installer, openclaw, runner
from .ui import (
    format_error,
    print_error,
    print_info,
    print_success,
    print_warning,
    print_summary,
    emit_json,
    _json_env,
)

# Detected by `sutra detect`, but deliberately not installed: neither host has
# a supported output-replacement hook, so SUTRA can only be added to their MCP
# config by hand. Naming one must explain that instead of an argparse error.
MANUAL_CLIENTS = ("cursor", "windsurf")

USAGE = """sutra {version}

usage:
  sutra install [--client hermes|claude|opencode|openclaw|codex]... [--all] [--config PATH] [--dry-run] [--json]
  sutra uninstall [--client hermes|claude|opencode|openclaw|codex]... [--all] [--config PATH] [--dry-run] [--json]
  sutra detect [--client NAME]... [--json]   (read-only client discovery)
  sutra status [--json]
  sutra doctor [--client hermes|claude|opencode|openclaw|codex]... [--json]  (prove integrations)
  sutra compress [--tool NAME] [--file PATH]   (stdin otherwise)
  sutra restore --id ID [--mode bytes|lines|search|full] ...
  sutra config
  sutra update [--apply]   (check the package index; --apply upgrades this environment)
  sutra eval run|compare   (development harness; needs a repository checkout)
  sutra --version

`install` auto-detects installed clients unless a `--client` is named. It
writes a backup beside every file it edits and a dry run writes nothing.
"""


def _parser(action: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"sutra {action}",
        description=f"{action.capitalize()} SUTRA's client integrations.")
    parser.add_argument("--client", action="append",
                        choices=list(installer.CLIENTS) + list(MANUAL_CLIENTS),
                        help=("limit the operation to this client (repeatable); "
                              "cursor and windsurf are MCP-only, configured by hand"))
    parser.add_argument("--all", action="store_true",
                        help="every supported client, present or not")
    parser.add_argument("--config", default=None,
                        help="override the OpenCode config path")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the change; write nothing")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output")
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
    if client in MANUAL_CLIENTS:
        print_error(
            f"{client} has no SUTRA installer: it is MCP-only and its built-in "
            "tool output cannot be replaced. Add the sutra MCP server by hand "
            "(see docs/automatic-install.md), or install a supported client: "
            + ", ".join(installer.CLIENTS))
        return 1
    if client == "claude":
        from . import claude_code
        if not claude_code.binary():
            if required:
                print_error("claude is not installed")
                return 1
            print_warning("claude is not installed; skipped")
            return 0
        return claude_code.install(dry_run=args.dry_run)
    if client == "hermes":
        from . import hermes
        if not hermes.binary():
            if required:
                print_error("hermes is not installed")
                return 1
            print_warning("hermes is not installed; skipped")
            return 0
        return hermes.install(dry_run=args.dry_run)
    if client == "codex":
        from . import codex
        if not codex.binary():
            if required:
                print_error("codex is not installed")
                return 1
            print_warning("codex is not installed; skipped")
            return 0
        return codex.install(dry_run=args.dry_run)
    if client == "openclaw":
        # OpenClaw may not be installed on this host yet; the plugin itself is
        # still worth writing so it is ready when OpenClaw appears.
        if shutil.which("openclaw") is None:
            print_warning("openclaw is not installed; skipped")
        return openclaw.install(dry_run=args.dry_run)
    from . import opencode
    status = opencode.install(dry_run=args.dry_run, config_path=args.config)
    if status:
        return status
    path = installer.opencode_config(args.config)
    if not path.exists():
        if args.config:
            print_error(f"{path} does not exist; pass a path that exists")
            return 1
        if args.dry_run:
            print_warning("dry run, no OpenCode config found; dry run would skip the MCP recovery entry")
        else:
            print_warning("no OpenCode config found; the automatic plugin is installed, MCP recovery entry skipped")
        return 0
    return installer.install(path, dry_run=args.dry_run)


def _uninstall_one(client: str, args, *, required: bool) -> int:
    # Uninstall never requires the host binary: the artifacts SUTRA wrote must
    # still be removable after a client is uninstalled or renamed. Each adapter
    # handles a missing host and reports what it could not clean up.
    if client in MANUAL_CLIENTS:
        print_info(f"{client} is MCP-only and SUTRA never installs into it; "
                   "nothing to uninstall")
        return 0
    if client == "claude":
        from . import claude_code
        return claude_code.uninstall(dry_run=args.dry_run)
    if client == "hermes":
        from . import hermes
        return hermes.uninstall(dry_run=args.dry_run)
    if client == "codex":
        from . import codex
        return codex.uninstall(dry_run=args.dry_run)
    if client == "openclaw":
        return openclaw.uninstall(dry_run=args.dry_run)
    from . import opencode
    status = opencode.uninstall(dry_run=args.dry_run, config_path=args.config)
    path = installer.opencode_config(args.config)
    if path.exists():
        status = max(status, installer.uninstall(path, dry_run=args.dry_run))
    return status


def _apply(action: str, args) -> int:
    clients = _selected(args)
    if not clients:
        message = ("no supported client detected; name one with --client "
                   "(hermes, claude, opencode, openclaw, codex); cursor and "
                   "windsurf are MCP-only and configured by hand")
        json_mode = getattr(args, "json", False) or _json_env()
        if json_mode:
            emit_json(status="fail", message=message,
                      error={"category": "no-clients", "message": message})
        else:
            print_error(message)
        return 1
    required = bool(args.client)
    status = 0
    results: dict[str, int] = {}
    for client in clients:
        operation = _install_one if action == "install" else _uninstall_one
        try:
            code = operation(client, args, required=required)
        except Exception as exc:  # one broken adapter must not block the others
            if isinstance(exc, PermissionError):
                friendly = format_error(
                    "permission-error",
                    details=str(exc),
                    step1="Run `sutra doctor` to diagnose the issue",
                    step2="Check file permissions and ensure the config is not read-only",
                )
            else:
                # Never replace the adapter's own error with a generic category:
                # the exception text is the only clue to what actually broke.
                friendly = (f"{client} {action} failed: "
                            f"{type(exc).__name__}: {exc}\n"
                            f"💡 Run `sutra doctor --client {client}` to diagnose.")
            print_error(friendly)
            code = 1
        results[client] = code
        status = max(status, code)
    verb = "install" if action == "install" else "uninstall"
    json_mode = getattr(args, "json", False) or _json_env()
    if json_mode:
        skipped_clients: list[str] = []
        if action == "install":
            # A zero exit only means the adapter did not fail: a client whose
            # host is absent is skipped, and must not be reported as configured.
            try:
                rows = {row.get("client"): row for row in _rows()
                        if row.get("client") and "error" not in row}
            except Exception:
                rows = {}
            skipped_clients = [
                client for client, code in results.items()
                if code == 0 and (client not in rows
                                  or not _row_configured(rows[client]))
            ]
        succeeded = sum(1 for client, code in results.items()
                        if code == 0 and client not in skipped_clients)
        failed = sum(1 for code in results.values() if code != 0)
        if failed:
            json_status = "fail"
        elif skipped_clients:
            json_status = "partial"
        else:
            json_status = "success"
        integrated_clients = []
        for client, code in results.items():
            if code != 0:
                label = "failed"
            elif client in skipped_clients:
                label = "skipped"
            else:
                label = "configured" if action == "install" else "removed"
            integrated_clients.append(f"{client}: {label}")
        if action == "install":
            next_steps = ["Run `sutra doctor` to verify the installation"]
        else:
            next_steps = ["Run `sutra doctor` to verify the removal"]
        emit_json(
            step=len(results),
            total=len(results),
            status=json_status,
            message=f"{verb.capitalize()} complete",
            success=succeeded,
            failed=failed,
            skipped=len(skipped_clients),
            integrated_clients=integrated_clients,
            next_steps=next_steps,
        )
    elif action == "install":
        _install_summary(clients, results, args.dry_run)
    return status


def _dry_run_label(label: str) -> str:
    """Say what the dry run *would* do, not what the current state is."""
    replacements = (
        ("=not configured", "=would configure"),
        ("=hook-only", "=would add MCP"),
        ("=installed, not enabled", "=would enable"),
        ("=configured, MCP skipped", "=would configure, MCP skipped"),
    )
    for old, new in replacements:
        if label.endswith(old):
            return label[: -len(old)] + new
    return label


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
        elif client == "openclaw":
            if row.get("plugin") == "installed":
                labels.append("openclaw=configured")
            else:
                labels.append("openclaw=not configured")
        else:
            if row.get("hook") != "installed":
                labels.append("codex=not configured")
            elif row.get("mcp") != "configured":
                labels.append("codex=hook-only")
            elif row.get("trusted") is not True:
                labels.append("codex=requires /hooks trust")
            else:
                labels.append("codex=active")
    if dry_run:
        labels = [_dry_run_label(label) for label in labels]
    prefix = "sutra: dry-run summary: " if dry_run else "sutra: install summary: "
    print(prefix + " ".join(labels))
    if not dry_run:
        print("sutra: run `sutra doctor` to verify the installed paths")


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
        from . import openclaw
        if shutil.which("openclaw") or openclaw.plugin_path().exists():
            openclaw_path = openclaw.plugin_path()
            rows.append({
                "client": "openclaw",
                "binary": shutil.which("openclaw"),
                "config": str(openclaw_path),
                "plugin": "installed" if openclaw.configured() else "not installed",
                "automatic": openclaw.configured(),
                "home": str(openclaw.default_home()),
            })
    except Exception:  # a broken adapter or missing openclaw is silently skipped
        pass
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


def _optimization_line() -> str:
    """One quiet line: what SUTRA will do for this profile, without internals."""
    from . import configuration, policy

    cfg = configuration()
    return (f"sutra: optimization={'on' if cfg.enabled else 'off'} "
            f"policy={policy.mode(cfg)} reuse={'on' if cfg.reuse else 'off'} "
            f"recovery={'on' if cfg.store_enabled else 'off'} "
            f"max_bytes={cfg.max_bytes}")


def _row_configured(row: dict) -> bool:
    """True when a status row shows a client as configured/active."""
    return (
        row.get("hook") in (True, "installed")
        or row.get("installed") is True
        or row.get("plugin") == "installed"
        or row.get("automatic") is True
        or row.get("mcp") in ("connected", "verified", "configured")
    )


def _status(as_json: bool = False) -> int:
    rows = _rows()
    configured = any(_row_configured(row) for row in rows)
    if as_json:
        import json
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
        elif row["client"] == "openclaw":
            print(f"openclaw: binary={'yes' if row.get('binary') else 'no'} "
                  f"plugin={row.get('plugin')} "
                  f"automatic={'yes' if row.get('automatic') else 'no'} "
                  f"config={row.get('config')}")
        else:
            print(f"opencode: binary={'yes' if row.get('binary') else 'no'} "
                  f"plugin={row.get('plugin')} mcp={row.get('mcp')} "
                  f"automatic={'yes' if row.get('automatic') else 'no'} "
                  f"config={row.get('config')}")
    print(_optimization_line())
    configured_count = sum(1 for row in rows if _row_configured(row))
    error_count = sum(1 for row in rows if "error" in row)
    unconfigured = len(rows) - configured_count - error_count
    print_summary(
        configured_count,
        error_count,
        unconfigured,
        integrated_clients=[
            f"{row.get('client', '?')}: {'configured' if _row_configured(row) else 'not configured'}"
            for row in rows
        ],
        next_steps=(["Run `sutra install --all` to configure detected clients"]
                    if not configured else None),
        title="SUTRA STATUS REPORT",
    )
    return 0 if configured else 1


def _detect(as_json: bool = False, clients: list[str] | None = None) -> int:
    from . import discovery

    rows = discovery.detect(clients)
    if as_json:
        emit_json(read_only=True, clients=rows)
    else:
        print(discovery.format_detect(rows))
        installable = [row for row in rows
                       if row.get("client") in installer.CLIENTS]
        manual = [row for row in rows if row.get("client") in MANUAL_CLIENTS]
        if installable:
            next_steps = ["Run `sutra install --all` to configure detected clients"]
        elif manual:
            next_steps = ["Cursor/Windsurf are MCP-only; add the sutra MCP server "
                          "by hand (docs/automatic-install.md)"]
        else:
            next_steps = ["No supported clients detected"]
        detected = [row for row in rows if row.get("installed")]
        print_summary(
            len(detected),
            0,
            len(rows) - len(detected),
            integrated_clients=[
                f"{row.get('client', '?')}: "
                f"{'detected' if row.get('installed') else 'not detected'}"
                for row in rows
            ],
            next_steps=next_steps,
            title="SUTRA DETECTION REPORT",
        )
    return 0


def _not_installed(client: str) -> dict:
    return {
        "client": client,
        "status": "not-installed",
        "ok": True,
        "automatic": False,
        "probe": "client is not installed",
    }


def _manual_client_report(client: str) -> dict:
    return {
        "client": client,
        "status": "manual",
        "ok": True,
        "automatic": False,
        "tier": "mcp-only",
        "probe": "no SUTRA adapter; add the sutra MCP server by hand",
    }


def _doctor(as_json: bool = False, clients: list[str] | None = None) -> int:
    import shutil

    from . import claude_code, codex, hermes, opencode, openclaw

    selected = clients or ["claude", "opencode", "hermes", "codex", "openclaw"]
    reports = []
    for client in selected:
        try:
            if client in MANUAL_CLIENTS:
                reports.append(_manual_client_report(client))
            elif client == "claude":
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
            elif client == "openclaw":
                reports.append(openclaw.doctor(node=shutil.which("node")) if (
                    shutil.which("openclaw") or openclaw.plugin_path().exists())
                    else _not_installed(client))
        except Exception as exc:  # a broken adapter must not hide the other three
            reports.append({"client": client, "status": "fail", "ok": False,
                            "automatic": False,
                            "error": f"{type(exc).__name__}: {exc}"})
    failed_count = sum(
        1 for report in reports
        if report.get("error")
        or report.get("status") == "fail"
        or (report.get("ok") is False
            and report.get("status") not in ("not-installed", "limited"))
    )
    failed = failed_count > 0
    if as_json:
        import json
        print(json.dumps(reports, indent=2))
        return 1 if failed else 0
    for report in reports:
        client = report.get("client")
        if report.get("status") == "not-installed":
            print(f"{client}: not installed")
            continue
        if report.get("status") == "manual":
            print(f"{client}: MCP-only (no SUTRA adapter); add the sutra MCP "
                  "server by hand (docs/automatic-install.md)")
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
        elif client == "openclaw":
            formatter = openclaw.format_doctor
        else:
            formatter = codex.format_doctor
        print(formatter(report))
    installed_count = sum(
        1 for report in reports
        if report.get("status") not in ("not-installed", "manual")
    )
    not_installed_count = sum(
        1 for report in reports if report.get("status") == "not-installed"
    )
    if failed:
        next_steps = ["Run `sutra install --all` to configure missing clients"]
    elif installed_count == 0 and any(
            report.get("status") == "manual" for report in reports):
        next_steps = ["Cursor/Windsurf are MCP-only; add the sutra MCP server "
                      "by hand (docs/automatic-install.md)"]
    elif installed_count == 0:
        next_steps = ["No automatic integration is installed; run "
                      "`sutra install --all` or add the sutra MCP server by hand"]
    elif any(report.get("status") == "manual" for report in reports):
        next_steps = ["Cursor/Windsurf are MCP-only; add the sutra MCP server "
                      "by hand (docs/automatic-install.md)"]
    else:
        next_steps = ["All probed clients are healthy"]
    print_summary(
        len(reports) - failed_count - not_installed_count,
        failed_count,
        not_installed_count,
        integrated_clients=[
            f"{report.get('client', '?')}: {report.get('status', '?')}"
            for report in reports
        ],
        next_steps=next_steps,
        title="SUTRA DOCTOR REPORT",
    )
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
                            choices=(list(installer.CLIENTS)
                                     + list(MANUAL_CLIENTS)),
                            help="limit the probe to this client (repeatable)")
        parser.add_argument("--json", action="store_true", help="machine-readable output")
        args = parser.parse_args(argv[1:])
        return _doctor(as_json=args.json, clients=args.client)
    if argv[:1] == ["eval"]:
        return _eval_main(argv[1:])
    if argv[:1] == ["update"]:
        return _update(argv[1:])
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
    print_error(f"unknown command {argv[0]!r}; run `sutra --help`")
    return 2


def _update(argv: list[str]) -> int:
    """Check the configured package index, and upgrade only when asked."""
    from . import update as update_module

    parser = argparse.ArgumentParser(
        prog="sutra update",
        description="Check the configured package index for a newer release; "
                    "--apply upgrades this environment with pip.")
    parser.add_argument("--apply", action="store_true",
                        help="install the newer release into this environment")
    args = parser.parse_args(argv)

    try:
        current = update_module.installed_version()
    except Exception:
        print_error(f"{update_module.PACKAGE} is not installed in this Python environment")
        return 2

    if update_module.is_source_checkout():
        print_info("this is a source checkout (editable install); update it with `git pull`")
        return 1 if args.apply else 0

    try:
        latest = update_module.latest_version()
    except Exception as exc:
        if not args.apply:
            print_error(f"could not check for updates: {type(exc).__name__}: {exc}")
            return 2
        print_info(f"could not check for updates ({type(exc).__name__}); asking pip directly")
        latest = current

    newer = update_module.is_newer(latest, current)
    ahead = update_module.is_newer(current, latest)

    if args.apply:
        if newer:
            print_info(f"sutra {latest} is available (installed: {current})")
        elif ahead:
            print_info(f"sutra {current} is newer than the latest published release ({latest})")
            print_info("asking pip for the newest version this environment can install")
        else:
            print_info(f"sutra {current} is current; asking pip for any newer build")
        try:
            code = update_module.upgrade()
        except update_module.UpdateError as exc:
            print_error(f"{exc}; the installed version is unchanged")
            return 1
        if code != 0:
            print_error(f"pip exited with status {code}; the installed version is unchanged")
            return 1
        try:
            installed = update_module.installed_version()
        except Exception:
            installed = latest
        if installed == current:
            print_success(f"sutra {installed} is already the latest version pip can install")
        else:
            print_success(f"sutra is now {installed}; restart any agent that loads it")
        return 0

    if ahead:
        print_info(f"sutra {current} is newer than the latest published release ({latest})")
        print_info("this looks like an unreleased or development build")
        return 0
    if not newer:
        print_success(f"sutra {current} is the latest release")
        return 0

    print_info(f"sutra {latest} is available (installed: {current})")
    print_info("run `sutra update --apply`, or "
               f"`{sys.executable} -m pip install --upgrade {update_module.PACKAGE}`")
    return 0


def _eval_main(argv: list[str]) -> int:
    """Delegate to the Phase 1 eval harness (eval/run_harness.py).

    Imported lazily so the harness never becomes a hard dependency of the
    sutra CLI, and so ``eval/`` stays importable from the project root.
    """
    repo_root = Path(__file__).resolve().parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from eval.run_harness import main as _harness_main
    except ImportError:
        print_error("the eval harness is a development tool and is not part "
                    "of an installed package")
        print_info("run it from a repository checkout: python eval/run_harness.py")
        return 2

    return _harness_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
