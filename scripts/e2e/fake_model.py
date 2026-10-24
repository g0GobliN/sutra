#!/usr/bin/env python3
"""A scripted OpenAI-compatible endpoint that drives a real Hermes agent loop.

The point: prove the plugin's *integration* — real agent, real terminal tool,
real middleware, real store, real recovery tool — without any model provider.
Hermes connects to this server as if it were the inference provider; the script
plays a deterministic model that walks SUTRA's evidence loop:

1. issue a terminal command whose output is ~39 kB with a clue buried in the
   middle (under Hermes' own terminal cap, over SUTRA's 24,576 B threshold);
2. verify the compressed result carries a SUTRA marker and the clue is gone;
3. ``tool_search`` for ``sutra_restore`` (plugin tools are deferred behind the
   Tool Search bridge on current Hermes hosts);
4. call ``sutra_restore`` through the bridge and check the clue came back;
5. answer ``E2E-SUTRA-OK``/``E2E-SUTRA-FAIL`` and write ``verdict.json``.

Run with ``run.sh``. See scripts/e2e/README.md for preconditions.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "sutra-e2e"
ID_RE = re.compile(r"(su_[0-9a-f]{32})")
MARKER_RE = re.compile(r"\[sutra:[^\]]*\]")

lock = threading.Lock()
state: dict = {"verdict": None, "pending": {}, "seq": 0}


def note(out_dir: str, event: dict) -> None:
    event["t"] = time.strftime("%H:%M:%S")
    with open(f"{out_dir}/transcript.jsonl", "a") as fh:
        fh.write(json.dumps(event, default=str) + "\n")


def issue(out_dir: str, name: str, args: dict) -> dict:
    state["seq"] += 1
    call_id = f"call_e2e_{state['seq']}"
    state["pending"][call_id] = name
    note(out_dir, {"event": "issue", "tool": name, "args": args})
    return {"tool_call": (call_id, name, args)}


def tool_results_by_call(messages: list) -> dict:
    """Map the tool names this script issued to their returned result text."""
    id_to_name: dict[str, str] = {}
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                id_to_name[tc["id"]] = tc["function"]["name"]
    out: dict[str, str] = {}
    for m in messages:
        if m.get("role") == "tool":
            name = id_to_name.get(m.get("tool_call_id"), "?")
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content
                                   if isinstance(p, dict))
            out[name] = content if isinstance(content, str) else str(content)
    return out


def decide(cfg, body: dict) -> dict:
    """Content-based turn state: what should the scripted model do now?"""
    out_dir = cfg.out_dir
    messages = body.get("messages") or []
    tools = {t["function"]["name"]: t["function"]
             for t in (body.get("tools") or [])
             if t.get("type") == "function" and "function" in t}
    results = tool_results_by_call(messages)
    note(out_dir, {"event": "request", "n_messages": len(messages),
                   "tools": sorted(tools),
                   "results": {k: len(v) for k, v in results.items()}})

    if not tools:  # title-generation and similar auxiliary calls: answer, move on
        return {"content": "ok"}

    # The bridge delivered the sutra_restore result: judge the whole loop.
    for bridge_name in ("tool_call", "sutra_restore"):
        if bridge_name in results:
            restored = results[bridge_name]
            payload = None
            try:
                parsed = json.loads(restored)
                if isinstance(parsed, list):
                    parsed = next((e.get("result", e) for e in parsed
                                   if isinstance(e, dict)
                                   and e.get("name") in ("sutra_restore", bridge_name)),
                                  parsed)
                if isinstance(parsed, dict):
                    payload = parsed
            except (ValueError, TypeError):
                pass
            if payload is None or "status" not in payload:
                state["verdict"] = {"ok": False,
                                    "reason": f"{bridge_name} result is not a restore envelope",
                                    "response": restored[:1500]}
                break
            restored = json.dumps(payload)
            clue_back = cfg.clue in restored
            status = payload.get("status")
            state["verdict"] = {
                "ok": bool(state.get("marker_seen") and not state.get("clue_visible")
                           and clue_back and status == "ok"),
                "marker_seen": bool(state.get("marker_seen")),
                "clue_visible_in_compressed": bool(state.get("clue_visible")),
                "clue_recovered": clue_back,
                "restore_status": status,
                "search_found_tool": bool(state.get("search_found_tool")),
            }
            break

    if state.get("verdict") is not None:
        return {"content": (
            f"E2E-SUTRA-{'OK' if state['verdict']['ok'] else 'FAIL'}: "
            f"marker_seen={state.get('marker_seen')} "
            f"clue_hidden={not state.get('clue_visible')} "
            f"clue_recovered={state['verdict'].get('clue_recovered')} "
            f"restore_status={state['verdict'].get('restore_status')}")}

    # tool_search returned: call sutra_restore through the bridge.
    if "tool_search" in results and "tool_call" not in results:
        found = "sutra_restore" in results["tool_search"]
        state["search_found_tool"] = found
        note(out_dir, {"event": "search_result", "found": found,
                       "response": results["tool_search"][:400]})
        if found and state.get("entry_id"):
            return issue(out_dir, "tool_call", {"calls": [{
                "name": "sutra_restore",
                "arguments": {"id": state["entry_id"], "mode": "search",
                              "query": cfg.query, "context": 1}}]})
        state["verdict"] = {"ok": False,
                            "reason": "tool_search did not surface sutra_restore",
                            "search_response": results["tool_search"][:1500]}
        return {"content": "E2E-SUTRA-FAIL: tool_search did not list sutra_restore"}

    # Terminal result back: verify the marker, then search for the recovery tool.
    if "terminal" in results:
        result = results["terminal"]
        marker = MARKER_RE.search(result or "")
        entry = ID_RE.search(marker.group(0)) if marker else None
        state["marker_seen"] = bool(marker)
        state["clue_visible"] = cfg.clue in (result or "")
        state["entry_id"] = entry.group(1) if entry else None
        note(out_dir, {"event": "tool_result", "bytes": len(result or ""),
                       "marker": marker.group(0)[:300] if marker else None,
                       "clue_visible": state["clue_visible"],
                       "entry": state["entry_id"]})
        if state["entry_id"]:
            return issue(out_dir, "tool_search",
                         {"queries": ["sutra restore omitted tool output"]})
        state["verdict"] = {
            "ok": False, "marker_seen": bool(marker), "clue_recovered": False,
            "reason": ("marker present but no id (original not retained)" if marker
                       else "no SUTRA marker on an oversized result")}
        return {"content": f"E2E-SUTRA-FAIL: {state['verdict']['reason']}"}

    # Fresh conversation: run the payload command.
    if "terminal" in tools:
        params = tools["terminal"].get("parameters") or {}
        props = params.get("properties") or {}
        arg_name = next((n for n in ("command", "cmd") if n in props),
                        (params.get("required") or ["command"])[0])
        return issue(out_dir, "terminal", {arg_name: cfg.command})
    return {"content": "E2E-SUTRA-FAIL: no terminal tool available"}


class Handler(BaseHTTPRequestHandler):
    cfg = None  # set in main()

    def log_message(self, *a):  # keep the console quiet
        pass

    def _send(self, code: int, payload: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path.endswith("/models"):
            self._send(200, json.dumps({"object": "list", "data": [
                {"id": MODEL, "object": "model"}]}).encode(), "application/json")
        else:
            self._send(404, b"{}", "application/json")

    def do_POST(self) -> None:
        if not self.path.endswith("/chat/completions"):
            self._send(404, b"{}", "application/json")
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        with lock:
            decision = decide(self.cfg, body)
            verdict = state.get("verdict")
        if verdict is not None:
            try:
                with open(f"{self.cfg.out_dir}/verdict.json", "w") as fh:
                    json.dump(verdict, fh, indent=2)
            except OSError:
                pass
        if "tool_call" in decision:
            call_id, name, args = decision["tool_call"]
            call = [{"id": call_id, "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}]
            message, finish = {"role": "assistant", "tool_calls": call}, "tool_calls"
        else:
            message, finish = {"role": "assistant",
                               "content": decision["content"]}, "stop"
        if body.get("stream"):
            chunk = {"id": "chatcmpl-e2e", "object": "chat.completion.chunk",
                     "created": 0, "model": MODEL,
                     "choices": [{"index": 0, "delta": message, "finish_reason": None}]}
            done = {"id": "chatcmpl-e2e", "object": "chat.completion.chunk",
                    "created": 0, "model": MODEL,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
            self._send(200, (f"data: {json.dumps(chunk)}\n\n"
                             f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n").encode(),
                       "text/event-stream")
        else:
            completion = {"id": "chatcmpl-e2e", "object": "chat.completion",
                          "created": 0, "model": MODEL,
                          "choices": [{"index": 0, "message": message,
                                       "finish_reason": finish}],
                          "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                    "total_tokens": 2}}
            self._send(200, json.dumps(completion).encode(), "application/json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8907)
    parser.add_argument("--out-dir", default="/tmp/sutra-e2e")
    parser.add_argument("--clue", default="CLUE: the magic word is umbrella-7f3a")
    parser.add_argument("--query", default="umbrella-7f3a",
                        help="the search text sent to sutra_restore")
    parser.add_argument("--command", default=(
        '{ seq 1 4000; echo "CLUE: the magic word is umbrella-7f3a"; seq 4002 8000; }'),
        help="the terminal command the scripted model runs first")
    cfg = parser.parse_args()
    Handler.cfg = cfg
    state["verdict"] = None
    ThreadingHTTPServer(("127.0.0.1", cfg.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
