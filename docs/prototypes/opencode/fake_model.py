#!/usr/bin/env python3
"""Scripted OpenAI-compatible endpoint for the OpenCode replacement prototype.

Turn 1: force `bash` to cat the 29.7 KB fixture (clue buried mid-file).
Turn 2: LOG the full tool-result content (ground truth of what the model
received), then answer with the verdict. No recovery involved — the single
question is whether the plugin's output replacement reached the model.
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8909
MODEL = "echo"
CLUE = "CLUE: the release key is 483920"
MARKER_RE = re.compile(r"\[sutra:[^\]]*\]")
EVIDENCE = "/tmp/sutra-oc-proto/evidence.jsonl"
STATE = {"turn": 0, "tool_bytes": None}

lock = threading.Lock()


def log(event):
    event["t"] = time.strftime("%H:%M:%S")
    with open(EVIDENCE, "a") as fh:
        fh.write(json.dumps(event, default=str) + "\n")


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
        tool_msgs = [m for m in (body.get("messages") or []) if m.get("role") == "tool"]
        log({"event": "request", "n_tools": len(tools), "tools": tools[:8],
             "n_tool_results": len(tool_msgs)})

        verdict = None
        if tool_msgs and STATE["tool_bytes"] is None:
            content = tool_msgs[-1].get("content")
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content
                                   if isinstance(p, dict))
            content = content if isinstance(content, str) else str(content)
            STATE["tool_bytes"] = len(content)
            marker = MARKER_RE.search(content)
            verdict = {
                "question": "did the hook's replacement reach the model?",
                "tool_result_bytes": len(content),
                "fixture_bytes": 29_701,
                "marker_present": bool(marker),
                "marker": marker.group(0)[:200] if marker else None,
                "clue_visible": CLUE in content,
                "replacement_reached_model": bool(marker and CLUE not in content),
            }
            with open("/tmp/sutra-oc-proto/verdict.json", "w") as fh:
                json.dump(verdict, fh, indent=2)
            message, finish = {"role": "assistant", "content":
                               f"PROTO-{'PASS' if verdict['replacement_reached_model'] else 'FAIL'}: "
                               f"received {len(content)} bytes"}, "stop"
        else:
            call = [{"id": "call_proto", "type": "function",
                     "function": {"name": "bash",
                                  "arguments": json.dumps(
                                      {"command": "cat /tmp/sutra-oc-proto/fixture/payload.log"})}}]
            message, finish = {"role": "assistant", "tool_calls": call}, "tool_calls"
        if body.get("stream"):
            chunk = {"id": "c", "object": "chat.completion.chunk", "created": 0,
                     "model": MODEL,
                     "choices": [{"index": 0, "delta": message, "finish_reason": None}]}
            done = {"id": "c", "object": "chat.completion.chunk", "created": 0,
                    "model": MODEL, "choices": [{"index": 0, "delta": {},
                                                 "finish_reason": finish}]}
            self._send(200, (f"data: {json.dumps(chunk)}\n\n"
                             f"data: {json.dumps(done)}\n\ndata: [DONE]\n\n").encode(),
                       "text/event-stream")
        else:
            self._send(200, json.dumps({
                "id": "c", "object": "chat.completion", "created": 0, "model": MODEL,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}}).encode(), "application/json")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
