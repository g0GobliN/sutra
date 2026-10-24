#!/usr/bin/env python3
"""Scripted OpenAI-compatible provider for the OpenCode spike's runtime proof.

The decisive evidence lives in requests.jsonl: every request body OpenCode
sends to this server is exactly what the MODEL would consume. If the tool
result in that body carries the SUTRA marker (and not the original 45 KB),
then OpenCode passed the replaced output to the model — no mocking involved.

Turn machine (content-based, like the Hermes harness):
  1. no tool result -> issue a bash call whose output is ~45 KB with a clue
     buried mid-output (under OpenCode's 30,000-char bash cap is impossible at
     45 KB — the cap truncates to ~30,000 chars, still above SUTRA's
     24,576-byte default threshold);
  2. bash result back -> record marker/clue evidence, call sutra_restore;
  3. restore result back -> judge and answer "OC-SUTRA-OK/FAIL: ...".
Writes verdict.json when the loop concludes.
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8908
MODEL = "poc-model"
CLUE = "ZEBRA-7Q"
COMMAND = ('for i in $(seq 1 1500); do echo "output line $i padding padding"; '
           'if [ $i -eq 750 ]; then echo "CLUE: the opencode proof token is '
           f'{CLUE}"; fi; done')
ID_RE = re.compile(r"(su_[0-9a-f]{32})")

state = {"verdict": None, "restore_sent": False}
lock = threading.Lock()


def note(event):
    event["t"] = time.strftime("%H:%M:%S")
    with open("/tmp/oc-poc/requests.jsonl", "a") as fh:
        fh.write(json.dumps(event, default=str) + "\n")


def tool_results(messages):
    out = []
    for m in messages:
        if m.get("role") == "tool":
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content
                                   if isinstance(p, dict))
            out.append(content if isinstance(content, str) else str(content))
    return out


def decide(body):
    messages = body.get("messages") or []
    tools = [t.get("function", {}).get("name") for t in (body.get("tools") or [])
             if isinstance(t, dict)]
    note({"event": "request", "n_messages": len(messages), "tools": sorted(filter(None, tools)),
          "roles": [m.get("role") for m in messages]})
    results = tool_results(messages)

    if not tools:
        return {"content": "ok"}  # auxiliary (title) request

    if results:
        # The most recent tool result decides the turn.
        result = results[-1]
        if state.get("restore_sent"):
            # A restore response: judge.
            clue_back = CLUE in result
            marker_seen = state.get("marker_seen")
            state["verdict"] = {
                "ok": bool(marker_seen and not state.get("clue_visible") and clue_back),
                "marker_seen": bool(marker_seen),
                "clue_visible_in_model_input": bool(state.get("clue_visible")),
                "clue_recovered": clue_back,
                "restore_response_excerpt": result[:600],
            }
            return {"content": (f"OC-SUTRA-{'OK' if state['verdict']['ok'] else 'FAIL'}: "
                                f"marker_seen={marker_seen} clue_recovered={clue_back}")}
        marker = re.search(r"\[sutra:[^\]]*\]", result)
        entry = ID_RE.search(marker.group(0)) if marker else None
        state["marker_seen"] = bool(marker)
        state["clue_visible"] = CLUE in result
        note({"event": "tool_result", "bytes": len(result), "marker": bool(marker),
              "clue_visible": state["clue_visible"], "entry": entry.group(1) if entry else None})
        if entry:
            state["restore_sent"] = True
            return {"tool_call": ("sutra_restore", {"id": entry.group(1), "mode": "search",
                                                    "query": CLUE, "context": 1})}
        state["verdict"] = {"ok": False, "marker_seen": bool(marker),
                            "reason": "no marker or no id on the oversized result"}
        return {"content": "OC-SUTRA-FAIL: no usable marker"}

    return {"tool_call": ("bash", {"command": COMMAND, "description": "generate large output"})}


def sse(message, finish):
    chunk = {"id": "chatcmpl-poc", "object": "chat.completion.chunk", "created": 0,
             "model": MODEL, "choices": [{"index": 0, "delta": message, "finish_reason": None}]}
    done = {"id": "chatcmpl-poc", "object": "chat.completion.chunk", "created": 0,
            "model": MODEL, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
    return (f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(done)}\n\ndata: [DONE]\n\n").encode()


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
        with lock:
            decision = decide(body)
            verdict = state.get("verdict")
        if verdict is not None:
            try:
                with open("/tmp/oc-poc/verdict.json", "w") as fh:
                    json.dump(verdict, fh, indent=2)
            except OSError:
                pass
        if "tool_call" in decision:
            name, args = decision["tool_call"]
            call = [{"id": "call_poc", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}]
            message, finish = {"role": "assistant", "tool_calls": call}, "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": decision["content"]}, "stop"
        self._send(200, sse(message, finish), "text/event-stream")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
