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
import contextlib
import io
import json
import os
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
  sutra map [--root PATH] [--budget N] [--json]
  sutra redundancy check --key K [--dependency PATH]... [--command CMD] [--file PATH] [--json]
  sutra redundancy record --key K [--dependency PATH]... [--result FINGERPRINT] [--file PATH]
  sutra redundancy forget --key K [--file PATH]
  sutra context [--file PATH] [--budget N] [--unit tokens|bytes] [--near-duplicates] [--json]
  sutra cache [--json] [--write-cost W --read-cost R --miss-cost M --reuses N]
  sutra stats [--file PATH] [--json]   (aggregate the durable savings log)
  sutra proxy [--host H] [--port P] [--upstream URL] [--verbose]
             (forward a host's requests through the request stage; point the host's
              base URL at it, e.g. ANTHROPIC_BASE_URL=http://127.0.0.1:8787)
  sutra shorten-results [--session ID]   (rewrite tool outputs handed over one by one;
              JSON {{"texts": [...]}} on stdin, {{"texts": [...], "changed": N}} back)
  sutra cost [--baseline-input N] [--baseline-output N] [--optimized-input N] [--optimized-output N]
             [--baseline-cached N] [--optimized-cached N] [--input-price P] [--output-price P]
             [--cached-price P] [--json]   (accounting with your own prices)
  sutra cost --original-input N [--billed-input N] [--cached N] [--output N] [--model-calls N]
             [--tool-calls N] [--processing-ms MS] [--validation-ms MS] [--provider-ms MS]
             [--total-ms MS] [--extra-cost C] [--tokenizer NAME] [--useful-tasks N]
             [--baseline-estimated] [--input-price P --output-price P] [--json]
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
            print_warning("openclaw is not installed; writing the plugin so it "
                          "is ready when OpenClaw starts")
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
    json_mode = getattr(args, "json", False) or _json_env()
    for client in clients:
        operation = _install_one if action == "install" else _uninstall_one
        try:
            if json_mode:
                # The adapters predate --json and print their own progress and
                # summaries. Keep stdout machine-readable: discard those lines
                # and let this command emit the one JSON object callers parse.
                with contextlib.redirect_stdout(io.StringIO()):
                    code = operation(client, args, required=required)
            else:
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
    if json_mode:
        skipped_clients: list[str] = []
        if action == "install":
            # A zero exit only means the adapter did not fail: a client whose
            # host is absent is skipped, and must not be reported as configured.
            try:
                rows = {row.get("client"): row for row in _rows(args.config)
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
        _install_summary(clients, results, args.dry_run, args.config)
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


def _install_summary(clients: list[str], results: dict[str, int], dry_run: bool,
                     config_path: str | None = None) -> None:
    """One concise, truthful line after an install attempt."""
    try:
        rows = {row.get("client"): row for row in _rows(config_path)
                if row.get("client") and "error" not in row}
    except Exception:  # a summary must never turn a completed install into a failure
        rows = {}
    labels: list[str] = []
    for client in clients:
        if results.get(client, 1) != 0:
            labels.append(f"{client}=failed")
            continue
        row = rows.get(client)
        if row is None and dry_run and client == "openclaw":
            # OpenClaw needs no host process: a dry run will write the plugin
            # even though no status row exists yet. "skipped" would be false.
            labels.append("openclaw=would configure")
            continue
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


def _rows(config_path: str | None = None) -> list[dict]:
    import shutil

    rows: list[dict] = []
    try:
        from . import claude_code
        if claude_code.binary() or claude_code.settings_path().exists():
            rows.append(claude_code.status())
    except Exception as exc:  # a broken settings file is reported, not raised
        rows.append({"client": "claude", "error": str(exc)})
    from . import opencode
    opencode_path = installer.opencode_config(config_path)
    installed_plugin = opencode.configured(config_path)
    rows.append({
        "client": "opencode",
        "binary": shutil.which("opencode"),
        "config": str(opencode_path),
        "mcp": "configured" if installer.opencode_configured(opencode_path) else "not configured",
        "plugin": "installed" if installed_plugin else "not installed",
        "automatic": installed_plugin,
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
            f"mode={cfg.mode} policy={policy.mode(cfg)} "
            f"reuse={'on' if cfg.reuse_active else 'off'} "
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
            current = row.get("current")
            print(f"hermes: binary={'yes' if row.get('binary') else 'no'} "
                  f"plugin={'installed' if row.get('installed') else 'not installed'} "
                  f"enabled={'yes' if row.get('enabled') is True else 'no'} "
                  f"copy={'unknown' if current is None else ('current' if current else 'stale')} "
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
                       if row.get("client") in installer.CLIENTS
                       and row.get("installed")]
        manual = [row for row in rows
                  if row.get("client") in MANUAL_CLIENTS and row.get("installed")]
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


def _map(argv: list[str]) -> int:
    from . import repomap

    parser = argparse.ArgumentParser(
        prog="sutra map",
        description=("Print a character-budgeted map of the repository's "
                     "top-level definitions, so reuse is cheaper than a "
                     "search. Read-only; no model call."))
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--budget", type=int, default=repomap.DEFAULT_BUDGET,
                        help="maximum characters in the map")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output")
    args = parser.parse_args(argv)
    try:
        lines = repomap.build_lines(args.root, budget=args.budget)
    except (OSError, ValueError) as exc:
        print_error(f"sutra map: {exc}")
        return 2
    if args.json:
        print(json.dumps({"root": str(args.root), "budget": args.budget,
                          "lines": lines}, indent=2, sort_keys=True))
    else:
        for line in lines:
            print(line)
    return 0


def _context(argv: list[str]) -> int:
    from . import context as context_module

    parser = argparse.ArgumentParser(
        prog="sutra context",
        description=("Select context items under a token or byte budget with "
                     "dependency preservation, mandatory items and optional "
                     "near-duplicate removal. Reads JSON from --file or stdin; "
                     "writes nothing."))
    parser.add_argument("--file", default=None,
                        help="JSON items, or an object with items and budget "
                             "('-' reads stdin, the default)")
    parser.add_argument("--budget", type=int, default=None,
                        help="total budget; required unless the JSON has one")
    parser.add_argument("--unit", choices=("tokens", "bytes"), default="tokens")
    parser.add_argument("--near-duplicates", action="store_true",
                        help="report and drop near-duplicates nothing else needs")
    parser.add_argument("--near-distance", type=int,
                        default=context_module.DEFAULT_NEAR_DISTANCE,
                        help="SimHash distance treated as near-duplicate")
    parser.add_argument("--weights", default=None,
                        help="five floats: relevance,importance,dependency,"
                             "usefulness,recency")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        if args.file and args.file != "-":
            with open(args.file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = json.load(sys.stdin)
        budget = args.budget
        if isinstance(payload, dict):
            raw_items = payload.get("items", [])
            if budget is None:
                budget = payload.get("budget")
        else:
            raw_items = payload
        if budget is None:
            raise ValueError("no budget given (--budget or JSON budget)")
        if not isinstance(raw_items, list):
            raise ValueError("items must be a JSON list")
        items = [context_module.Item.from_dict(entry) for entry in raw_items]
        weights = None
        if args.weights:
            parts = [part.strip() for part in args.weights.split(",")]
            if len(parts) != 5:
                raise ValueError("--weights needs five comma-separated numbers")
            weights = context_module.Weights(
                relevance=float(parts[0]), importance=float(parts[1]),
                dependency=float(parts[2]), usefulness=float(parts[3]),
                recency=float(parts[4]))
        result = context_module.select(
            items, budget, unit=args.unit, weights=weights,
            near_duplicates=args.near_duplicates,
            near_distance=args.near_distance)
    except (OSError, ValueError, TypeError, context_module.CycleError) as exc:
        print_error(f"sutra context: {exc}")
        return 2

    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return 0
    print(f"selected {len(result.selected)} of {len(result.selected) + len(result.dropped)} "
          f"items, {result.total_cost}/{result.budget} {result.unit}, "
          f"value {result.value:.6f}")
    print(f"algorithm: {result.algorithm}")
    if result.estimated:
        print("token costs: estimated from bytes (items without token_cost)")
    if not result.feasible:
        print_error(f"budget conflict: {result.conflict}")
    for item_id in result.selected:
        print(f"  keep  {item_id}")
    for item_id, reason in result.dropped:
        print(f"  drop  {item_id}: {reason}")
    for dropped, kept in result.duplicates:
        print(f"  near-duplicate {dropped} ~ {kept}")
    return 0 if result.feasible else 1


def _redundancy(argv: list[str]) -> int:
    from . import redundancy

    parser = argparse.ArgumentParser(
        prog="sutra redundancy",
        description=("Classify whether repeated tool work may be reused "
                     "(SAFE_TO_REUSE), needs revalidation, or must execute. "
                     "Verification commands always must execute. Reads and "
                     "writes a local JSON ledger; runs nothing."))
    parser.add_argument("action", choices=("check", "record", "forget"))
    parser.add_argument("--key", default=None,
                        help="call key from redundancy.call_fingerprint")
    parser.add_argument("--tool", default="",
                        help="tool name; with --arguments, computes the key")
    parser.add_argument("--arguments", default="",
                        help="canonical call arguments; with --tool, computes the key")
    parser.add_argument("--dependency", action="append", default=[],
                        metavar="PATH",
                        help="file the result depends on (repeatable)")
    parser.add_argument("--root", default=".",
                        help="directory dependency paths are relative to")
    parser.add_argument("--command", default="",
                        help="the command, so verification shapes are refused")
    parser.add_argument("--result", default="",
                        help="fingerprint of the recorded result")
    parser.add_argument("--ttl", type=int, default=None,
                        help="seconds until the record expires (default 3600)")
    parser.add_argument("--file", default=None,
                        help="ledger path (default SUTRA_REDUNDANCY_FILE or "
                             "~/.sutra/redundancy.json)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    key = args.key or (redundancy.call_fingerprint(args.tool, args.arguments)
                       if args.tool else "")
    if not key:
        print_error("sutra redundancy: pass --key or --tool with --arguments")
        return 2
    ledger = (redundancy.Ledger(args.file, ttl_seconds=args.ttl)
              if args.file else redundancy.Ledger.from_env())
    if args.action == "record":
        current = redundancy.snapshot(args.dependency, root=args.root)
        ledger.record(key, dependencies=current,
                      result_fingerprint=args.result,
                      ttl_seconds=args.ttl)
        if args.json:
            print(json.dumps({"key": key, "recorded": True,
                              "dependencies": current.as_dict()},
                             indent=2, sort_keys=True))
        else:
            print(f"recorded {key[:12]}… with "
                  f"{len(current.files)} dependency file(s)")
        return 0
    if args.action == "forget":
        removed = ledger.forget(key)
        if args.json:
            print(json.dumps({"key": key, "removed": removed},
                             indent=2, sort_keys=True))
        else:
            print("forgotten" if removed else "no such record")
        return 0
    current = redundancy.snapshot(args.dependency, root=args.root)
    decision = ledger.classify(key, dependencies=current,
                               command=args.command)
    if args.json:
        print(json.dumps(decision.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"{decision.classification}: {decision.reason}")
        if decision.changed:
            print("changed: " + ", ".join(decision.changed))
    return 0 if decision.reusable else 1


def _cache(argv: list[str]) -> int:
    from . import cache

    parser = argparse.ArgumentParser(
        prog="sutra cache",
        description=("Report which of the five cache layers this project "
                     "actually owns, and optionally evaluate the break-even "
                     "arithmetic for one layer. Runs nothing and calls no "
                     "provider."))
    parser.add_argument("--write-cost", type=float, default=None,
        help="cost to write one cache entry, in your billing unit")
    parser.add_argument("--read-cost", type=float, default=None,
        help="cost to serve one hit")
    parser.add_argument("--miss-cost", type=float, default=None,
        help="cost of the call the cache replaces")
    parser.add_argument("--reuses", type=int, default=None,
        help="reuses to evaluate")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = cache.report()
    costs = (args.write_cost, args.read_cost, args.miss_cost, args.reuses)
    if any(value is not None for value in costs):
        if any(value is None for value in costs):
            print_error("sutra cache: pass all of --write-cost, --read-cost, "
                        "--miss-cost and --reuses, or none")
            return 2
        try:
            payload["economics"] = cache.parse_costs(
                args.write_cost, args.read_cost, args.miss_cost, args.reuses)
        except (TypeError, ValueError) as exc:
            print_error(f"sutra cache: {exc}")
            return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    for entry in payload["layers"]:
        print(f"{entry['layer']:<20} {entry['status']}")
        print(f"  stores: {entry['stores']}")
        print(f"  note:   {entry['note']}")
    economics = payload.get("economics")
    if economics:
        print("")
        print("economics: " + economics["formula"])
        print(f"  saving per reuse: {economics['saving_per_reuse']}")
        break_even = economics["break_even_reuses"]
        print("  break-even reuses: "
              + ("never (a hit costs at least a miss)" if break_even is None
                 else f"{break_even}"))
        print(f"  net at {economics['reuses']} reuses: {economics['net']} "
              f"(worth it: {economics['worth_it']})")
    return 0


def _stats(argv: list[str]) -> int:
    """Aggregate the durable savings log; process counters are not readable here."""
    import json as _json

    from . import config as _config
    from . import metrics as _metrics

    parser = argparse.ArgumentParser(
        prog="sutra stats",
        description=("Aggregate the durable savings log: how many tool results were "
                     "rewritten, how many bytes were saved, and by which rule and "
                     "tool. Counters that live in a host process (restores, "
                     "request-stage rewrites, observed provider usage) are not "
                     "visible to this command."))
    parser.add_argument("--file", default=None,
                        help="savings log to read (default: the configured stats path)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    path = (os.path.abspath(os.path.expanduser(args.file)) if args.file
            else _config.resolve().stats_path)
    try:
        report = _metrics.read_savings_log(path)
    except OSError as exc:
        print_error(f"sutra stats: cannot read {path}: {exc}")
        return 1
    request_report = _metrics.read_request_log(path)
    if args.json:
        report["request_stage"] = request_report
        print(_json.dumps(report, indent=2, sort_keys=True))
        return 0
    if report["records"]:
        print(f"sutra stats: {path}")
    else:
        print(f"sutra stats: no tool-result rewrites in {path}")
        if report["unreadable"]:
            print(f"unreadable lines: {report['unreadable']}")
        print("The log is written when a tool result is rewritten; an untouched "
              "installation legitimately has no records yet.")
    if report["records"]:
        if len(report["files"]) > 1:
            print(f"including the rotated log {report['files'][0]}")
        print(f"  rewrites: {report['records']}")
        print(f"  bytes: {report['original_bytes']:,} before "
              f"→ {report['compressed_bytes']:,} after "
              f"({report['saved_bytes']:,} saved, {report['saved_pct']}%)")
        if report["partial"] or report["fail_open"] or report["unreadable"]:
            print(f"  partial: {report['partial']}  fail open: {report['fail_open']}  "
                  f"unreadable lines: {report['unreadable']}")
        if report["first_at"]:
            print(f"  from {report['first_at']} to {report['last_at']}")
        for title, key in (("rule", "by_rule"), ("tool", "by_tool")):
            rows = sorted(report[key].items(),
                          key=lambda item: (-item[1]["saved_bytes"], item[0]))
            if not rows:
                continue
            print(f"  by {title}:")
            for name, row in rows[:8]:
                print(f"    {name}: {row['records']} rewrite(s), "
                      f"{row['saved_bytes']:,} bytes saved")
    if request_report["records"]:
        print(f"request stage: {request_report['records']} rewrite(s), "
              f"{request_report['saved']:,} bytes removed "
              f"({request_report['saved_pct']}%)")
        rows = sorted(request_report["by_mode"].items(),
                      key=lambda item: (-item[1]["saved"], item[0]))
        for name, row in rows[:8]:
            print(f"  by mode {name}: {row['records']} rewrite(s), "
                  f"{row['saved']:,} bytes removed")
    else:
        print(f"request stage: no rewrite recorded in "
              f"{request_report['path']}")
    print("Counters for restores and observed provider usage live in the host "
          "process (`sutra.metrics_snapshot()`); this log holds rewritten tool "
          "results and the request-stage rewrites beside it.")
    return 0


def _shorten_results(argv: list[str]) -> int:
    """Shorten tool outputs handed over one by one, JSON in and JSON out.

    OpenCode's ``chat.messages.transform`` hook lets a plugin rewrite the
    results already inside the assembled conversation, but it hands them over
    as separate bodies rather than as one request. This command applies the
    same policy to each body: an entry changes only when the store keeps a copy
    first, and an entry the store refuses comes back unchanged.
    """
    from . import config as _config
    from . import prompt as _prompt

    parser = argparse.ArgumentParser(
        prog="sutra shorten-results",
        description="Rewrite tool outputs handed over one by one (JSON on stdin).",
    )
    parser.add_argument(
        "--session",
        default="",
        help="Session id recorded with the stored copies (falls back to the payload).",
    )
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError) as exc:
        print_error(f"sutra shorten-results: cannot read the request: {type(exc).__name__}")
        return 1
    if not isinstance(payload, dict) or not isinstance(payload.get("texts"), list):
        print_error("sutra shorten-results: expected {\"texts\": [...]} on stdin")
        return 1
    texts = payload["texts"]
    cfg = _config.resolve()
    if not cfg.prompt_enabled or cfg.mode == "off":
        print(json.dumps({"texts": texts, "changed": 0}))
        return 0
    session_id = str(args.session or payload.get("session_id") or "")
    retain = _prompt.host_retainer(session_id=session_id)
    head = _prompt.HEAD_BYTES if cfg.prompt_preview else 0
    tail = _prompt.TAIL_BYTES if cfg.prompt_preview else 0
    rewritten = []
    changed = 0
    for text in texts:
        if not isinstance(text, str):
            rewritten.append(text)
            continue
        shorter = _prompt.shorten_result(text, head=head, tail=tail, retain=retain)
        if shorter is None:
            rewritten.append(text)
        else:
            rewritten.append(shorter)
            changed += 1
    print(json.dumps({"texts": rewritten, "changed": changed}))
    return 0


def _process_result(argv: list[str]) -> int:
    """Rewrite one tool result a host hands over as its own payload.

    Some hosts let a plugin replace a tool result but never expose the
    conversation, so there is no request to rewrite: the payload on stdin is
    the whole unit of work. Each text entry goes through the seam a native
    host uses, the store keeps the original first, and an entry whose original
    cannot be kept comes back unchanged.
    """
    from . import core as _core

    parser = argparse.ArgumentParser(
        prog="sutra process-result",
        description="Rewrite one tool result (JSON on stdin).",
    )
    parser.add_argument(
        "--session",
        default="",
        help="Session id recorded with the stored copies (falls back to the payload).",
    )
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError) as exc:
        print_error(f"sutra process-result: cannot read the payload: {type(exc).__name__}")
        return 1
    if not isinstance(payload, dict) or not isinstance(payload.get("texts"), list):
        print_error("sutra process-result: expected {\"texts\": [...]} on stdin")
        return 1
    tool_name = str(payload.get("tool_name") or "")
    session_id = str(args.session or payload.get("session_id") or "")
    rewritten = []
    changed = False
    for text in payload["texts"]:
        if not isinstance(text, str):
            rewritten.append(text)
            continue
        shorter = _core.process_result(text, tool_name=tool_name, session_id=session_id)
        rewritten.append(shorter)
        changed = changed or shorter != text
    print(json.dumps({"texts": rewritten, "changed": changed}))
    return 0


def _cost(argv: list[str]) -> int:
    from . import cost as cost_module

    parser = argparse.ArgumentParser(
        prog="sutra cost",
        description=("Report token and cost savings from explicit measurements and "
                     "your own prices. SUTRA ships no price table and makes no "
                     "provider call here."))
    parser.add_argument("--baseline-input", type=int, default=0)
    parser.add_argument("--baseline-output", type=int, default=0)
    parser.add_argument("--baseline-cached", type=int, default=0)
    parser.add_argument("--optimized-input", type=int, default=0)
    parser.add_argument("--optimized-output", type=int, default=0)
    parser.add_argument("--optimized-cached", type=int, default=0)
    parser.add_argument("--input-price", type=float, default=None,
                        help="price per million input tokens (optional)")
    parser.add_argument("--output-price", type=float, default=None,
                        help="price per million output tokens (optional)")
    parser.add_argument("--cached-price", type=float, default=None,
                        help="price per million cached input tokens (optional)")
    parser.add_argument("--cache-write-price", type=float, default=None,
                        help="price per million cache-write tokens (optional)")
    parser.add_argument("--usage-file", default=None,
                        help="read provider usage metadata from this JSON file")
    parser.add_argument("--provider", default=None,
                        choices=["openai", "anthropic", "google", "deepseek"],
                        help="provider whose usage shape the file holds")
    parser.add_argument("--original-input", type=int, default=None,
                        help="full accounting: input tokens before optimization")
    parser.add_argument("--billed-input", type=int, default=None,
                        help="full accounting: input tokens the provider billed")
    parser.add_argument("--cached", type=int, default=None,
                        help="full accounting: provider-reported cached input tokens")
    parser.add_argument("--output", type=int, default=None,
                        help="full accounting: output tokens")
    parser.add_argument("--model-calls", type=int, default=None)
    parser.add_argument("--tool-calls", type=int, default=None)
    parser.add_argument("--processing-ms", type=float, default=None)
    parser.add_argument("--validation-ms", type=float, default=None)
    parser.add_argument("--provider-ms", type=float, default=None)
    parser.add_argument("--total-ms", type=float, default=None)
    parser.add_argument("--extra-cost", type=float, default=None,
                        help="SUTRA's own cost, in the same unit as the prices")
    parser.add_argument("--tokenizer", default="",
                        help="tokenizer name used for both measurements")
    parser.add_argument("--useful-tasks", type=int, default=None,
                        help="completed tasks, for quality-adjusted efficiency")
    parser.add_argument("--baseline-estimated", action="store_true",
                        help="mark the baseline as an estimate, not a measurement")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    reported = None
    if args.usage_file is not None:
        from . import providers as providers_module
        if args.provider is None:
            print_error("sutra cost: --usage-file needs --provider")
            return 2
        try:
            with open(args.usage_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            reported = providers_module.usage_from(args.provider, payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print_error(f"sutra cost: {exc}")
            return 2
        if args.original_input is None:
            print_error("sutra cost: --usage-file needs --original-input, the "
                        "provider reports the optimized run only")
            return 2
    elif args.provider is not None:
        print_error("sutra cost: --provider needs --usage-file")
        return 2
    pricing = None
    if args.input_price is not None and args.output_price is not None:
        try:
            pricing = cost_module.Pricing(
                input_per_million=args.input_price,
                output_per_million=args.output_price,
                cached_input_per_million=args.cached_price,
                cache_write_per_million=args.cache_write_price)
        except ValueError as exc:
            print_error(f"sutra cost: {exc}")
            return 2
    full = args.original_input is not None
    if full:
        try:
            accounting = cost_module.Accounting(
                original_input_tokens=args.original_input or 0,
                billed_input_tokens=(
                    reported.input_tokens if reported is not None
                    else (args.billed_input
                          if args.billed_input is not None
                          else (args.optimized_input
                                if args.optimized_input else 0))),
                cached_input_tokens=(
                    reported.cached_input_tokens if reported is not None
                    else (args.cached if args.cached is not None
                          else args.optimized_cached)),
                cache_write_input_tokens=(
                    reported.cache_write_tokens if reported is not None else 0),
                output_tokens=(reported.output_tokens if reported is not None
                               else (args.output if args.output is not None
                                     else args.optimized_output)),
                model_calls=args.model_calls or 0,
                tool_calls=args.tool_calls or 0,
                processing_ms=args.processing_ms or 0.0,
                validation_ms=args.validation_ms or 0.0,
                provider_latency_ms=args.provider_ms,
                total_latency_ms=args.total_ms,
                extra_cost=args.extra_cost or 0.0,
                tokenizer=args.tokenizer,
                baseline_estimated=args.baseline_estimated,
                useful_tasks=args.useful_tasks)
        except ValueError as exc:
            print_error(f"sutra cost: {exc}")
            return 2
        report = cost_module.accounting_report(accounting, pricing)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        tokens = report["tokens"]
        print(f"tokens: {tokens['original_total']} -> "
              f"{tokens['optimized_total']} (saved {tokens['saved']})")
        if tokens["percent_saved"] is None:
            print("percent saved: unavailable (no measured input tokens)")
        else:
            print(f"percent saved: {tokens['percent_saved']}% "
                  f"({tokens['reduction_basis']})")
        calls = report["calls"]
        print(f"calls: {calls['models']} model, {calls['tools']} tool")
        latency = report["latency"]
        print(f"overhead: {latency['overhead_ms']} ms "
              f"(processing {latency['processing_ms']} + validation "
              f"{latency['validation_ms']})")
        cost = report["cost"]
        print(f"cost: {cost['basis']}")
        if cost["basis"] == "explicit pricing":
            print(f"  baseline: {cost['baseline']:.6f}"
                  f"{' (estimated)' if cost['estimated_baseline'] else ''}")
            print(f"  actual:   {cost['actual']:.6f} "
                  f"(includes {cost['extra_cost']} overhead)")
            print(f"  net saved: {cost['net_savings']:.6f}")
        quality = report["quality"]
        if quality["efficiency"] is not None:
            print(f"efficiency: {quality['efficiency']} useful tasks per unit cost")
        return 0

    try:
        baseline = cost_module.Usage(args.baseline_input, args.baseline_output,
                                     args.baseline_cached)
        optimized = cost_module.Usage(args.optimized_input, args.optimized_output,
                                      args.optimized_cached)
    except ValueError as exc:
        print_error(f"sutra cost: {exc}")
        return 2
    report = cost_module.savings_report(baseline=baseline, optimized=optimized,
                                        pricing=pricing)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    print(f"tokens: {report['baseline']['tokens']} -> "
          f"{report['optimized']['tokens']} "
          f"(saved {report['saved_tokens']})")
    if report["percent_saved"] is None:
        print(f"percent saved: unavailable ({report['percent_saved_reason']})")
    else:
        print(f"percent saved: {report['percent_saved']}%")
    cost = report["cost"]
    print(f"cost: {cost['basis']}")
    if cost["basis"] == "explicit pricing":
        print(f"  baseline: ${cost['baseline_usd']:.6f}")
        print(f"  optimized: ${cost['optimized_usd']:.6f}")
        print(f"  saved: ${cost['saved_usd']:.6f}")
    return 0


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
                                     "openclaw", "codex", "windsurf"],
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
    if argv[:1] == ["mode"]:
        return _mode(argv[1:])
    if argv[:1] == ["cache"]:
        return _cache(argv[1:])
    if argv[:1] == ["cost"]:
        return _cost(argv[1:])
    if argv[:1] == ["stats"]:
        return _stats(argv[1:])
    if argv[:1] == ["shorten-results"]:
        return _shorten_results(argv[1:])
    if argv[:1] == ["process-result"]:
        return _process_result(argv[1:])
    if argv[:1] == ["proxy"]:
        from .proxy import run as run_proxy
        return run_proxy(argv[1:])
    if argv[:1] == ["context"]:
        return _context(argv[1:])
    if argv[:1] == ["redundancy"]:
        return _redundancy(argv[1:])
    if argv[:1] == ["map"]:
        return _map(argv[1:])
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


def _mode(argv: list[str]) -> int:
    """Show or persist the unified optimization mode (off/balanced/aggressive/beast)."""
    import json as _json

    from . import config as _config
    from . import orchestrator

    parser = argparse.ArgumentParser(
        prog="sutra mode",
        description="Show or set SUTRA's optimization mode. The choice is "
                    "persisted for future processes; running hosts resolve it "
                    "on their next tool result.")
    parser.add_argument("name", nargs="?", default=None,
                        choices=list(_config.MODES),
                        help="mode to persist")
    parser.add_argument("--clear", action="store_true",
                        help="remove the persisted mode file")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if args.clear:
        path = _config.mode_file_path()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print_error(f"sutra mode: could not remove {path}: {exc}")
            return 1
        print(f"sutra mode: cleared {path}")
        return 0
    if args.name is not None:
        try:
            path = _config.write_mode(args.name)
        except (OSError, ValueError) as exc:
            print_error(f"sutra mode: {exc}")
            return 1
        print(f"sutra mode: {args.name} persisted to {path}")
        return 0
    cfg = _config.resolve()
    policy = orchestrator.policy_for(cfg)
    payload = {
        "mode": cfg.mode,
        "source": cfg.mode_source,
        "mode_file": _config.mode_file_path(),
        "policy": policy.as_dict(),
        "engines": [engine.as_dict() for engine in orchestrator.engines()],
    }
    if args.json:
        print(_json.dumps(payload, indent=2))
        return 0
    print(f"mode: {cfg.mode} (source: {cfg.mode_source})")
    print(f"policy: passes={policy.passes} combine={policy.combine} "
          f"triple={policy.triple} caveman={policy.language} "
          f"reuse={policy.reuse} budget_ms={policy.budget_ms:g}")
    print(f"persisted: {_config.mode_file_path()}")
    for engine in orchestrator.engines():
        state = "automatic" if engine.automatic else "on request"
        print(f"  {engine.name:9s} {engine.plane:9s} {state}: {engine.note}")
    return 0


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
