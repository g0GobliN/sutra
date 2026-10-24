#!/usr/bin/env python3
"""SUTRA bridge for the OpenCode feasibility spike.

A small loopback HTTP server the OpenCode plugin talks to over 127.0.0.1. It
keeps SUTRA's Python implementation untouched and in-process (no per-call
subprocesses), and keeps ALL storage isolated from any other SUTRA installation:

    HERMES_HOME defaults to /tmp/sutra-opencode-home  (never ~/.hermes)

Endpoints (all JSON, loopback only, bodies capped at 2 MiB):

    GET  /health   -> {"ok": true, "version": ...}   (no storage paths)
    POST /compress {"tool", "output"} -> {"changed", "output", "marker", "id"}
    POST /restore  {"id", ...}        -> the verbatim sutra_restore JSON response

Every POST requires the X-Sutra-Token header matching the token the bridge
generated at startup. The token lives in a 0600 file inside a 0700 directory
(HERMES_HOME/sutra-oc-bridge-token; only its PATH is printed). The file is
created with O_EXCL|O_NOFOLLOW, a pre-existing file must be a regular 0600
file or the bridge refuses to start, and a symlink is never followed — the
bridge cannot silently fall back to an insecure token. Setting
SUTRA_OC_BRIDGE_TOKEN is the operator-managed alternative: it overrides the
file on both sides and no file is written. Loopback binding plus the token
keep another local process from using the bridge as a compression oracle or
from polluting the store; retained-output reads additionally need an
unguessable 128-bit id.

The bridge is proof-of-concept code, not the production adapter: no TLS, no
persistence beyond SUTRA's own store.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import secrets
import stat as stat_module
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_PORT = 8765


def load_sutra():
    """Load the plugin package exactly as Hermes and the tests do."""
    name = "sutra_opencode_spike"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sutra = load_sutra()
ID_RE = re.compile(r"id=(su_[0-9a-f]{32})")


class Handler(BaseHTTPRequestHandler):
    daemon_threads = True  # a vanished client must never block shutdown

    def log_message(self, *a):  # quiet
        pass

    def _send(self, code: int, body: str, ctype: str = "application/json") -> None:
        payload = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the caller gave up (hook timeout); nothing to answer

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            # Drain first: answering before the client finishes writing would
            # break its pipe mid-request.
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65_536))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise ValueError("body too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def _authorized(self) -> bool:
        expected = os.environ.get("SUTRA_OC_BRIDGE_TOKEN") or ""
        provided = str(self.headers.get("X-Sutra-Token") or "")
        return bool(expected) and secrets.compare_digest(provided, expected)

    def do_GET(self):
        if self.path == "/health":
            # Deliberately no storage paths: they are not exposed to callers.
            self._send(200, json.dumps({"ok": True, "version": sutra.__version__}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        try:
            if not self._authorized():
                self._send(401, json.dumps({"error": "missing or wrong X-Sutra-Token"}))
                return
            if self.path == "/compress":
                body = self._body()
                text = body.get("output")
                if not isinstance(text, str):
                    self._send(400, json.dumps({"error": "output must be a string"}))
                    return
                out = sutra.compress_result(text, tool_name=str(body.get("tool") or "")[:64])
                marker = "[sutra:" in out
                match = ID_RE.search(out)
                self._send(200, json.dumps({
                    "changed": out != text,
                    "output": out,
                    "marker": marker,
                    "id": match.group(1) if match else None,
                }))
            elif self.path == "/restore":
                body = self._body()
                self._send(200, sutra.recovery.restore(body), "application/json")
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except ValueError as exc:
            self._send(413, json.dumps({"error": str(exc)}))
        except Exception as exc:  # fail open: the hook decides what to do
            self._send(500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}))


def secure_token_write(path: pathlib.Path, token: str) -> None:
    """Create the 0600 token file without following symlinks, or refuse loudly.

    A pre-existing file must be a regular 0600 file and is then reused (a
    restart keeps serving the token the plugin already read). Anything else —
    a symlink, a directory, a loose mode — stops the bridge instead of
    silently degrading to an insecure token.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        info = os.lstat(path)
        if stat_module.S_ISLNK(info.st_mode) or not stat_module.S_ISREG(info.st_mode):
            raise SystemExit(f"refusing insecure token file (not a regular file): {path}")
        mode = stat_module.S_IMODE(info.st_mode)
        if mode != 0o600:
            raise SystemExit(f"refusing insecure token file (mode {oct(mode)}, "
                             f"expected 0o600): {path}")
        return  # well-formed file from a previous run; reuse it
    with os.fdopen(fd, "w") as fh:
        fh.write(token + "\n")


def main() -> int:
    port = int(os.environ.get("SUTRA_OC_BRIDGE_PORT") or DEFAULT_PORT)
    # Isolation is the point: a spike must never write to a live SUTRA store.
    os.environ.setdefault("HERMES_HOME", "/tmp/sutra-opencode-home")
    home = pathlib.Path(os.environ["HERMES_HOME"])
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat_module.S_IMODE(home.stat().st_mode) & 0o077:
        os.chmod(home, 0o700)  # tighten a pre-existing loose directory
    token_path = home / "sutra-oc-bridge-token"
    operator_token = os.environ.get("SUTRA_OC_BRIDGE_TOKEN")
    if operator_token:
        # Operator-managed token: no file is written; the plugin must be given
        # SUTRA_OC_BRIDGE_TOKEN too (it prefers the env over the file).
        os.environ["SUTRA_OC_BRIDGE_TOKEN"] = operator_token
        print(f"sutra-opencode bridge on 127.0.0.1:{port}, HERMES_HOME={home}, "
              f"sutra {sutra.__version__}, token source: environment", flush=True)
    else:
        secure_token_write(token_path, secrets.token_hex(32))
        os.environ["SUTRA_OC_BRIDGE_TOKEN"] = token_path.read_text(encoding="utf-8").strip()
        # The token value is never printed; the plugin reads the 0600 file.
        print(f"sutra-opencode bridge on 127.0.0.1:{port}, HERMES_HOME={home}, "
              f"sutra {sutra.__version__}, token file {token_path}", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
