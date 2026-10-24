#!/usr/bin/env python3
"""Stdin/stdout bridge: JSON {text} in, JSON {out, stats} out.

Loads the real SUTRA engine from the repository the way Hermes loads it and
runs the same deterministic compression the Hermes adapter applies. Any
failure exits non-zero and the JS side fails open.
"""
import importlib.util
import json
import os
import sys
import tempfile

REPO = "/home/goblin01/github_projects/tooltrim"
os.environ.setdefault("HERMES_HOME", tempfile.mkdtemp(prefix="sutra-oc-driver-"))

spec = importlib.util.spec_from_file_location(
    "sutra_plugin", f"{REPO}/__init__.py", submodule_search_locations=[REPO])
sutra = importlib.util.module_from_spec(spec)
sys.modules["sutra_plugin"] = sutra
spec.loader.exec_module(sutra)

request = json.loads(sys.stdin.read() or "{}")
out, stats = sutra.compress(request.get("text", ""))
print(json.dumps({"out": out, "stats": {k: stats[k] for k in
                                        ("rule", "changed", "original_bytes",
                                         "compressed_bytes")}}))
