"""Install the automatic DeepSeek Harness adapter.

DeepSeek Harness has no console script and no settings file a third party may
edit: a host capability arrives as a *bundle* — a package directory with a
``dsh.bundle.patch`` entry — and the supported way to attach it is
``dsh plugin --profile <name> add <directory>``. This adapter therefore copies
one small JavaScript bundle to ``<DSH_HOME>/sutra-host`` and asks the harness
to add it, which is the same path a user follows by hand.

The bundle wires every stage this harness exposes:

* ``tools/post-execute`` trims an oversized tool result before the model reads
  it, through ``python -m sutra process-result`` (the same seam the other hosts
  use), so the Python core stays the only implementation of the policy;
* ``mcpManager.register`` exposes the recovery server, so the id the marker
  quotes can be exchanged for the exact bytes the store kept.

The request stage is deliberately absent: the harness hands plugins a model
route (``agent/request``) rather than the outbound message array, and inventing
one would be a claim the host does not support.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

try:
    from .ui import print_error, print_info, print_success, print_warning
except ImportError:  # allow `python -c "import dsh"` run from the package directory
    def print_error(message, /, *, tty=None):
        print(message, file=sys.stderr)

    def print_warning(message, /, *, tty=None):
        print(message)

    def print_info(message, /, *, tty=None):
        print(message)

    def print_success(message, /, *, tty=None):
        print(message)

BUNDLE_NAME = "dsh-sutra-host"
BUNDLE_FILES = ("package.json", "cordis.patch.yml", "index.js")
# The released templates are flat package data, beside the JS adapters.
TEMPLATES = {
    "package.json": "dsh_host_package.json",
    "cordis.patch.yml": "dsh_host_cordis.patch.yml",
    "index.js": "dsh_host_index.js",
}


def home(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    """Where the harness keeps its profiles and plugin packages."""
    if explicit:
        return pathlib.Path(explicit).expanduser()
    return pathlib.Path(os.environ.get("DSH_HOME") or "~/.dsh").expanduser()


def bundle_dir(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    return home(explicit) / "sutra-host"


def profile(explicit: str | None = None) -> str:
    return explicit or os.environ.get("DSH_PROFILE") or "desktop"


def binary() -> str | None:
    return shutil.which("dsh")


def configured(config_home: str | pathlib.Path | None = None, name: str | None = None) -> bool:
    """True when the bundle is copied and the profile already lists it."""
    manifest = home(config_home) / "profiles" / profile(name) / "package.json"
    if not manifest.is_file():
        return False
    try:
        package = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    dependencies = package.get("dependencies") or {}
    return BUNDLE_NAME in dependencies


def template(filename: str) -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / TEMPLATES[filename]


def _patch_text(python: str, python_path: str) -> str:
    return (
        "# Added by `sutra install --client dsh`: one bundle, every stage this\n"
        "# harness exposes. Remove the bundle to withdraw it.\n"
        "- insert:\n"
        f"    - id: {BUNDLE_NAME}\n"
        f"      name: {BUNDLE_NAME}\n"
        "      config:\n"
        f"        python: {python}\n"
        f"        pythonPath: {python_path}\n"
        "        mode: balanced\n"
    )


PRESET_BEGIN = "# sutra: begin preset entry"
PRESET_END = "# sutra: end preset entry"


def profile_patch(name: str | None = None, home_dir: str | pathlib.Path | None = None) -> pathlib.Path:
    """The patch file that composes one profile's presets and plugin entries."""
    return home(home_dir) / "profiles" / profile(name) / "cordis.patch.yml"


def _preset_block(python: str, python_path: str, indent: str) -> list[str]:
    pad = indent
    return [
        f"{pad}{PRESET_BEGIN}",
        f"{pad}- id: {BUNDLE_NAME}",
        f"{pad}  name: {BUNDLE_NAME}",
        f"{pad}  config:",
        f"{pad}    python: {python}",
        f"{pad}    pythonPath: {python_path}",
        f"{pad}    mode: balanced",
        f"{pad}{PRESET_END}",
    ]


def _preset_plugins(lines: list[str]) -> tuple[int, str] | None:
    """The line index and child indent of the first preset's ``plugins:`` list."""
    for index, line in enumerate(lines):
        if line.lstrip().startswith("- id: preset-"):
            break
    else:
        return None
    for position in range(index, len(lines)):
        stripped = lines[position].strip()
        if stripped == "plugins:":
            return position, " " * (len(lines[position]) - len(lines[position].lstrip()) + 2)
        if position + 1 < len(lines) and lines[position + 1].lstrip().startswith("- id: ") is False:
            continue
    return None


def wire_preset(python: str, python_path: str, *, name: str | None = None,
                home_dir: str | pathlib.Path | None = None) -> str:
    """Put the bundle inside the profile's agent preset, idempotently.

    A profile-level entry loads beside the presets, and the harness scopes
    ``tools/post-execute`` to the calling agent's own scope chain, so only an
    entry composed *inside* the preset ever sees a model tool call.
    """
    path = profile_patch(name, home_dir)
    if not path.is_file():
        return "no-file"
    text = path.read_text(encoding="utf-8")
    if BUNDLE_NAME in text:
        return "present"
    lines = text.splitlines()
    located = _preset_plugins(lines)
    if located is None:
        return "no-preset"
    position, indent = located
    block = _preset_block(python, python_path, indent)
    lines[position + 1:position + 1] = block
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "wired"


def unwire_preset(*, name: str | None = None, home_dir: str | pathlib.Path | None = None) -> bool:
    path = profile_patch(name, home_dir)
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == PRESET_BEGIN)
        end = next(index for index, line in enumerate(lines) if line.strip() == PRESET_END)
    except StopIteration:
        return False
    del lines[start:end + 1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def install(*, dry_run: bool = False, python: str | None = None,
            python_path: str | None = None, name: str | None = None,
            home_dir: str | pathlib.Path | None = None,
            cli: list[str] | None = None) -> int:
    interpreter = python or sys.executable or "python3"
    # The seam must import the same tree this installer runs from: a source
    # checkout is not on the interpreter's path, and a stale `sutra` found there
    # would answer "unknown command" for every call and silently do nothing.
    default_path = os.environ.get("SUTRA_PYTHONPATH") or str(pathlib.Path(__file__).resolve().parent.parent)
    path = python_path if python_path is not None else default_path
    target = bundle_dir(home_dir)
    sources = {filename: template(filename) for filename in BUNDLE_FILES}
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        print_error(f"the DeepSeek Harness bundle template is missing: {', '.join(missing)}")
        return 1
    if shutil.which(interpreter) is None and not pathlib.Path(interpreter).exists():
        print_error(f"{interpreter} is not runnable; pass --python with an interpreter that can import sutra")
        return 1
    prefix = list(cli) if cli is not None else ([found] if (found := binary()) else [])
    if not prefix and not dry_run:
        print_error("dsh is not on PATH; install DeepSeek Harness first, or pass --dry-run to inspect the bundle")
        return 1
    if dry_run:
        print_warning(f"dry run, would write {target} and run `dsh plugin --profile {profile(name)} add {target}`")
        return 0
    target.mkdir(parents=True, exist_ok=True)
    for filename in BUNDLE_FILES:
        if filename == "cordis.patch.yml":
            (target / filename).write_text(_patch_text(interpreter, path), encoding="utf-8")
        else:
            shutil.copyfile(sources[filename], target / filename)
    print_success(f"sutra: wrote the host bundle at {target}")
    command = [*prefix, "plugin", "--profile", profile(name), "add", str(target)]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        print_error(f"dsh plugin add failed: {exc}")
        return 1
    if finished.returncode != 0:
        detail = (finished.stderr or finished.stdout or "").strip().splitlines()[-1:]
        print_error(f"dsh plugin add failed ({finished.returncode}): {' '.join(detail)}")
        return 1
    print_success(f"sutra: attached the bundle to the {profile(name)} profile")
    wired = wire_preset(interpreter, path, name=name, home_dir=home_dir)
    if wired == "wired":
        print_success("sutra: placed the bundle inside the profile's agent preset, where model tool calls reach it")
    elif wired == "present":
        print_info("sutra: the profile's agent preset already lists the bundle")
    elif wired == "no-preset":
        print_warning("sutra: the profile declares no agent preset; add the bundle to a preset by hand")
    print_warning("Restart DeepSeek Harness once so the bundle loads; `sutra doctor` reports dsh after that")
    return 0


def uninstall(*, dry_run: bool = False, name: str | None = None,
              home_dir: str | pathlib.Path | None = None,
              cli: list[str] | None = None) -> int:
    target = bundle_dir(home_dir)
    prefix = list(cli) if cli is not None else ([found] if (found := binary()) else [])
    if dry_run or not prefix:
        print_warning(f"would run `dsh plugin --profile {profile(name)} remove {BUNDLE_NAME}` and delete {target}")
        return 0
    finished = subprocess.run(
        [*prefix, "plugin", "--profile", profile(name), "remove", BUNDLE_NAME],
        capture_output=True, text=True, timeout=600,
    )
    if finished.returncode != 0:
        print_warning("dsh plugin remove reported a problem; the profile may need one manual pass")
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    if unwire_preset(name=name, home_dir=home_dir):
        print_success("sutra: removed the bundle from the profile's agent preset")
    print_success("sutra: removed the DeepSeek Harness bundle")
    return 0


def last_saves(home_dir: str | pathlib.Path | None = None, *, limit: int = 20_000) -> dict:
    """What the seam has actually saved in this harness, from its own log.

    A bundle can be attached, report a healthy doctor line, and still never
    reach a model tool call — that is exactly how the preset defect hid. The
    stats log is the only evidence that the stage ran, so the doctor reports it
    instead of implying it.
    """
    path = bundle_dir(home_dir).with_name("sutra") / "stats.jsonl"
    if not path.is_file():
        return {"rewrites": 0, "saved_bytes": 0, "last_at": ""}
    rewrites = 0
    saved = 0
    last = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= limit:
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                trimmed = int(record.get("saved_bytes") or 0)
                if trimmed <= 0:
                    continue
                rewrites += 1
                saved += trimmed
                last = str(record.get("at") or "")[:40] or last
    except OSError:
        return {"rewrites": 0, "saved_bytes": 0, "last_at": ""}
    return {"rewrites": rewrites, "saved_bytes": saved, "last_at": last}


def format_doctor(report: dict) -> str:
    """One line naming what the harness has actually done, not only what is wired."""
    if "error" in report:
        return f"dsh: error: {report['error']}"
    parts = [
        "dsh:",
        f"status={report.get('status', 'fail')}",
        f"profile={report.get('profile', '?')}",
        f"copy={report.get('copy', 'stale')}",
        f"attached={'yes' if report.get('attached') else 'no'}",
        f"preset={report.get('preset', 'missing')}",
    ]
    if report.get("stage") == "saved":
        parts.append(f"stage=saved rewrites={report.get('rewrites', 0)}")
        parts.append(f"saved={_megabytes(report.get('saved_bytes') or 0)}")
        parts.append(f"last={report.get('last_saved') or 'unknown'}")
    else:
        parts.append("stage=never")
    line = " ".join(parts)
    problems = report.get("problems") or []
    if problems:
        line += " · " + "; ".join(problems[:3])
    return line


def _megabytes(count: int) -> str:
    """A byte count a human can compare at a glance."""
    size = float(count)
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} MB"


def doctor(*, name: str | None = None, home_dir: str | pathlib.Path | None = None) -> dict:
    target = bundle_dir(home_dir)
    files = [filename for filename in BUNDLE_FILES if (target / filename).is_file()]
    attached = configured(home_dir, name)
    in_preset = "wired" if (profile_patch(name, home_dir).is_file()
                            and BUNDLE_NAME in profile_patch(name, home_dir).read_text(encoding="utf-8")) else "missing"
    cli = binary()
    saves = last_saves(home_dir)
    problems = []
    if not files:
        problems.append("the host bundle is not copied")
    if not attached:
        problems.append("the profile does not list dsh-sutra-host")
    if in_preset != "wired":
        problems.append("no agent preset composes dsh-sutra-host, so model tool calls never reach it")
    if not saves["rewrites"]:
        problems.append("no tool result has been trimmed in this harness yet")
    if cli is None:
        problems.append("dsh is not on PATH")
    status = "pass" if not problems else ("partial" if files else "fail")
    return {
        "status": status,
        "bundle": str(target),
        "files": files,
        "profile": profile(name),
        "attached": attached,
        "preset": in_preset,
        "binary": cli or "",
        "problems": problems,
        "copy": "current" if len(files) == len(BUNDLE_FILES) else "stale",
        "stage": "saved" if saves["rewrites"] else "never",
        "rewrites": saves["rewrites"],
        "saved_bytes": saves["saved_bytes"],
        "last_saved": saves["last_at"],
    }
