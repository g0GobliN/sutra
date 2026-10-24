#!/usr/bin/env python3
"""SUTRA↔OpenCode prototype bridge: JSON on stdin, JSON on stdout.

Subcommands:
  compress       {text} → runs the real adapter path (compress_result: compress
                 + retain + marker with id) → {out, stats}
  restore        {id, mode, ...} → recovery.restore(args) → the JSON envelope
  seed-expired   none → places an entry aged past the TTL in the persistent
                 store → {id} (for the expiry scenario)

State is persistent across invocations via SUTRA_OC_HOME (used as HERMES_HOME),
so the hook's compressions and the recovery tool's reads share one store, and
the durable stats JSONL accumulates there.
"""
import importlib.util
import json
import os
import sys
import time

REPO = os.environ.get("SUTRA_REPO")
if not REPO or not os.path.exists(os.path.join(REPO, "__init__.py")):
    sys.exit("set SUTRA_REPO to the SUTRA repository root (containing __init__.py)")
os.environ["HERMES_HOME"] = os.environ.get(
    "SUTRA_OC_HOME", "/tmp/sutra-oc-proto/home")

spec = importlib.util.spec_from_file_location(
    "sutra_plugin", f"{REPO}/__init__.py", submodule_search_locations=[REPO])
sutra = importlib.util.module_from_spec(spec)
sys.modules["sutra_plugin"] = sutra
spec.loader.exec_module(sutra)

subcommand = sys.argv[1] if len(sys.argv) > 1 else "compress"
request = json.loads(sys.stdin.read() or "{}")

if subcommand == "compress":
    text = request.get("text", "")
    out = sutra.compress_result(text, tool_name="bash")
    # compress_result returns the result only; the durable JSONL carries the
    # stats record (last row when the text was rewritten).
    row = {}
    stats_path = sutra.stats_path()
    if os.path.exists(stats_path):
        lines = open(stats_path).read().splitlines()
        if lines:
            row = json.loads(lines[-1])
    keep = {k: row.get(k) for k in ("rule", "changed", "original_bytes",
                                    "compressed_bytes", "stored", "id")}
    keep["rewritten"] = out != text
    print(json.dumps({"out": out, "stats": keep}))
elif subcommand == "restore":
    print(sutra.recovery.restore(request))
elif subcommand == "seed-expired":
    store = sutra.store.store_from_env()
    aged = time.time() - (store.config.ttl_hours + 24) * 3_600
    entry = store.put("AGED-PAYLOAD the aged clue is here\n" + "filler\n" * 500,
                      tool_name="seed", now=aged)
    print(json.dumps({"id": entry.id if entry else None}))
else:
    print(json.dumps({"status": "error", "error": f"unknown subcommand {subcommand}"}))
    sys.exit(2)
