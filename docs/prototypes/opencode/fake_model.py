#!/usr/bin/env python3
"""Scenario-driven scripted provider for the OpenCode recovery prototype.

The scenario is read from /tmp/sutra-oc-proto/scenario.json on every request,
so the runner can switch cases without restarting the server. Every request
logs the offered tools (discovery evidence) and every tool-result payload
(ground truth of what the model sees). Verdicts land in
/tmp/sutra-oc-proto/verdict-<scenario>.json.
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8909
MODEL = "echo"
CLUE = "483920"
MARKER_RE = re.compile(r"\[sutra:[^\]]*\]")
ID_RE = re.compile(r"(su_[0-9a-f]{32})")
BASE = os.environ.get("SUTRA_OC_BASE", "/tmp/sutra-oc-proto")
SCENARIO_PATH = f"{BASE}/scenario.json"

lock = threading.Lock()
state = {"scenario": None, "restore_calls": 0, "results": {}}


def log(event):
    event["t"] = time.strftime("%H:%M:%S")
    with open(f"{BASE}/evidence.jsonl", "a") as fh:
        fh.write(json.dumps(event, default=str) + "\n")


def verdict(name, data):
    data["scenario"] = name
    with open(f"{BASE}/verdict-{name}.json", "w") as fh:
        json.dump(data, fh, indent=2)
    log({"event": "verdict", **data})


def flatten(content):
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content
                        if isinstance(p, dict))
    return content if isinstance(content, str) else str(content)


def tool_results_by_call(messages):
    """call_id -> (tool_name, content): repeated calls keep separate entries."""
    id_to_name = {}
    for m in messages:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                id_to_name[tc["id"]] = tc["function"]["name"]
    out = {}
    for m in messages:
        if m.get("role") == "tool":
            out[m.get("tool_call_id", "?")] = (
                id_to_name.get(m.get("tool_call_id"), "?"), flatten(m.get("content")))
    return out


def issue(name, args, seq=[0]):
    seq[0] += 1
    return {"tool_call": (f"call_p{seq[0]}", name, args)}


def handle_scenario(name, tools, state):
    """Return the next model action for the scenario. One bash turn first."""
    bash = state.get("bash_content")
    restores = state.get("restore_payloads", [])
    if not bash:  # first turn: force the cat
        return issue("bash", {"command":
                     f"cat {BASE}/fixture/payload.log"})
    entry = ID_RE.search(MARKER_RE.search(bash).group(0)) if MARKER_RE.search(bash) else None

    if name == "full-flow":
        if not restores:
            if not entry:
                verdict(name, {"pass": False, "reason": "no id in marker"})
                return {"content": "PROTO-FAIL: no id"}
            return issue("sutra_restore", {"id": entry.group(1), "mode": "search",
                                           "query": CLUE, "context": 1})
        payload = restores[-1]
        verdict(name, {"pass": payload.get("status") == "ok"
                       and CLUE in payload.get("content", ""),
                       "restore_status": payload.get("status"),
                       "clue_recovered": CLUE in payload.get("content", "")})
        return {"content": f"PROTO-{'PASS' if payload.get('status') == 'ok' else 'FAIL'}"}

    if name == "no-recovery":
        marker = MARKER_RE.search(bash)
        verdict(name, {"pass": bool(marker and CLUE not in bash),
                       "marker_present": bool(marker),
                       "clue_visible": CLUE in bash})
        return {"content": "PROTO-DONE: no recovery attempted"}

    if name == "invalid-id":
        if not restores:
            return issue("sutra_restore", {"id": "su_" + "zz" * 16,
                                           "mode": "search", "query": "x"})
        payload = restores[-1]
        verdict(name, {"pass": payload.get("status") == "error"
                       and "invalid id" in payload.get("error", ""),
                       "status": payload.get("status"),
                       "error": payload.get("error")})
        return {"content": "PROTO-DONE"}

    if name == "unknown-id":
        if not restores:
            return issue("sutra_restore", {"id": "su_" + "ab" * 16,
                                           "mode": "search", "query": "x"})
        payload = restores[-1]
        verdict(name, {"pass": payload.get("status") == "not_found",
                       "status": payload.get("status"),
                       "note": payload.get("note", "")[:120]})
        return {"content": "PROTO-DONE"}

    if name == "missing-arg":
        if not restores:
            return issue("sutra_restore", {"mode": "search", "query": "x"})
        payload = restores[-1]
        verdict(name, {"pass": payload.get("status") == "error",
                       "status": payload.get("status"),
                       "error": payload.get("error")})
        return {"content": "PROTO-DONE"}

    if name == "expired":
        scenario = json.load(open(SCENARIO_PATH))
        if not restores:
            return issue("sutra_restore", {"id": scenario["seeded_id"],
                                           "mode": "search", "query": "aged"})
        payload = restores[-1]
        verdict(name, {"pass": payload.get("status") == "expired",
                       "status": payload.get("status")})
        return {"content": "PROTO-DONE"}

    if name == "expired-swept":
        # Contract nuance: at the host, the hook's compression seeds the store
        # sweep, which prunes aged entries BEFORE the restore can see them.
        # recovery.md's not_found row explicitly covers "pruned", so either
        # expired (deleted on lookup) or not_found (already swept) is
        # contract-conformant; record which one the host produced.
        scenario = json.load(open(SCENARIO_PATH))
        if not restores:
            return issue("sutra_restore", {"id": scenario["seeded_id"],
                                           "mode": "search", "query": "aged"})
        payload = restores[-1]
        verdict(name, {"pass": payload.get("status") in ("expired", "not_found"),
                       "status": payload.get("status"),
                       "note": "sweep-first host: pruned entries surface as not_found"})
        return {"content": "PROTO-DONE"}

    if name == "too-large":
        if not restores:
            return issue("sutra_restore", {"id": entry.group(1), "mode": "full"})
        payload = restores[-1]
        verdict(name, {"pass": payload.get("status") == "too_large",
                       "status": payload.get("status"),
                       "original_bytes": payload.get("original_bytes")})
        return {"content": "PROTO-DONE"}

    if name == "repeat":
        if len(restores) < 2 and entry:
            return issue("sutra_restore", {"id": entry.group(1), "mode": "search",
                                           "query": CLUE, "context": 1})
        ok = len(restores) == 2 and all(p.get("status") == "ok"
                                        and CLUE in p.get("content", "")
                                        for p in restores)
        verdict(name, {"pass": ok, "calls": len(restores),
                       "statuses": [p.get("status") for p in restores],
                       "identical": (restores[0].get("content") ==
                                     restores[1].get("content")) if len(restores) == 2 else None})
        return {"content": "PROTO-DONE"}

    if name == "storage-failure":
        marker = MARKER_RE.search(bash)
        not_retained = bool(marker and "original not retained" in marker.group(0))
        if not restores:
            return issue("sutra_restore", {"id": "su_" + "cd" * 16,
                                           "mode": "search", "query": "x"})
        payload = restores[-1]
        verdict(name, {"pass": not_retained and payload.get("status") == "not_found",
                       "marker_says_not_retained": not_retained,
                       "restore_status": payload.get("status")})
        return {"content": "PROTO-DONE"}

    return {"content": "PROTO-DONE: unknown scenario"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, payload, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._send(200, json.dumps({"object": "list", "data": [
                {"id": MODEL, "object": "model"}]}).encode(), "application/json")
        else:
            self._send(404, b"{}", "application/json")

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            self._send(404, b"{}", "application/json")
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        tools = [t["function"]["name"] for t in (body.get("tools") or [])
                 if t.get("type") == "function"]
        scenario = "unknown"
        try:
            scenario = json.load(open(SCENARIO_PATH))["name"]
        except (OSError, ValueError, KeyError):
            pass
        with lock:
            if scenario != state["scenario"]:
                state.update({"scenario": scenario, "results": {},
                              "seen_ids": set(), "bash_content": None,
                              "restore_payloads": []})
            results = tool_results_by_call(body.get("messages") or [])
            for cid, (name_, content) in results.items():
                if cid in state["seen_ids"]:
                    continue
                state["seen_ids"].add(cid)
                if name_ == "bash":
                    state["bash_content"] = content
                elif name_ == "sutra_restore":
                    try:
                        payload = json.loads(content)
                    except ValueError:
                        payload = {"status": "unparseable", "raw": content[:200]}
                    state["restore_payloads"].append(payload)
            log({"event": "request", "scenario": scenario, "tools": tools,
                 "tool_result_names": sorted(results),
                 "restore_offered": "sutra_restore" in tools})
            if not tools:  # auxiliary calls (title gen etc.)
                message, finish = {"role": "assistant", "content": "ok"}, "stop"
            else:
                action = handle_scenario(scenario, tools, state)
                if "tool_call" in action:
                    call_id, name_, args = action["tool_call"]
                    call = [{"id": call_id, "type": "function",
                             "function": {"name": name_,
                                          "arguments": json.dumps(args)}}]
                    message, finish = {"role": "assistant",
                                       "tool_calls": call}, "tool_calls"
                else:
                    message, finish = {"role": "assistant",
                                       "content": action["content"]}, "stop"
        if body.get("stream"):
            chunk = {"id": "c", "object": "chat.completion.chunk", "created": 0,
                     "model": MODEL,
                     "choices": [{"index": 0, "delta": message,
                                  "finish_reason": None}]}
            done = {"id": "c", "object": "chat.completion.chunk", "created": 0,
                    "model": MODEL,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
            self._send(200, (f"data: {json.dumps(chunk)}\n\n"
                             f"data: {json.dumps(done)}\n\n"
                             "data: [DONE]\n\n").encode(), "text/event-stream")
        else:
            self._send(200, json.dumps({
                "id": "c", "object": "chat.completion", "created": 0, "model": MODEL,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}}).encode(), "application/json")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
