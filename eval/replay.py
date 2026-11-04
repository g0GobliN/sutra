#!/usr/bin/env python3
"""Run the model-free Hermes cost replay and write a machine-readable summary.

    python3 eval/replay.py [--quick] [--output PATH]

No model is called, no provider credit is spent, and no network request is
made. ``--quick`` runs the first two workloads in every mode; the full run is
eight workloads.
"""

from __future__ import annotations

import argparse
import json
import pathlib

from harness import format_table, run_all
from workloads import workloads

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "eval" / "results" / "phase2" / "hermes-cost-summary.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="replay", description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true",
                        help="run two workloads instead of all eight")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help="where to write the JSON summary")
    args = parser.parse_args(argv)

    selected = workloads()[:2] if args.quick else workloads()
    summary = run_all(selected)
    path = pathlib.Path(args.output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(format_table(summary))
    quality = summary["totals"]["balanced"]["quality"]
    print(f"\nworkloads: {len(selected)}  clues: "
          f"{quality['visible']} visible + {quality['recoverable']} recoverable "
          f"+ {quality['lost']} lost")
    print("provider cost: unavailable (no model call)")
    print(f"wrote {path}")
    return 0 if quality["lost"] == 0 and quality["exact_restore_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
