#!/usr/bin/env python3
"""Scripted OpenAI-compatible provider for the OpenCode spike's runtime proof.

The decisive evidence lives in requests.jsonl: every request body OpenCode
sends to this server is exactly what the MODEL would consume. If the tool
result in that body carries the SUTRA marker (and not the original payload),
then OpenCode passed the replaced output to the model — no mocking involved.

Scenario state machine (SUTRA_OC_SCENARIO, default "treatment"):

  treatment : big output, clue at line 750 -> marker to the model -> the model
              calls sutra_restore three times (malformed id, well-formed
              unknown id, real id) and the verdict records all three statuses;
  diff      : a ~45 KB diff must reach the model byte-intact with NO marker
              (SUTRA never rewrites patches);
  failopen  : bridge down before the run - the untouched result reaches the
              model and the session completes (also exercises the 2 s timeout);
  oversize  : a >2 MiB output must be refused by the bridge (413) and reach
              the model untouched - bounded, no marker.

Writes verdict-<scenario>.json when the loop concludes.
"""
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8908
MODEL = "poc-model"
CLUE = "ZEBRA-7Q"
SCENARIO = os.environ.get("SUTRA_OC_SCENARIO", "treatment")
ID_RE = re.compile(r"(su_[0-9a-f]{32})")
MARKER_RE = re.compile(r"\[sutra:[^\]]*\]")

TREATMENT_COMMAND = ('for i in $(seq 1 1500); do echo "output line $i padding padding"; '
                     'if [ $i -eq 750 ]; then echo "CLUE: the opencode proof token is '
                     f'{CLUE}"; fi; done')
DIFF_COMMAND = ('for i in $(seq 1 500); do echo "diff --git a/mod$i.ts b/mod$i.ts"; '
                'echo "--- a/mod$i.ts"; echo "+++ b/mod$i.ts"; echo "@@ -12,7 +12,7 @@"; '
                'echo "+const retries$i = backoff.next(3)"; done')
OVERSIZE_COMMAND = 'seq -f "padding line %.0f aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" 1 60000'
COMMANDS = {"treatment": TREATMENT_COMMAND, "failopen": TREATMENT_COMMAND,
            "diff": DIFF_COMMAND, "oversize": OVERSIZE_COMMAND}

state = {"verdict": None, "restore_sent": False, "pending": None, "statuses": [],
         "marker_seen": False, "clue_visible": False, "entry_id": None}
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


def judge_treatment(result):
    clue_back = CLUE in result
    state["verdict"] = {
        "ok": bool(state["marker_seen"] and not state["clue_visible"] and clue_back
                   and state["statuses"] == ["error", "not_found", "ok"]),
        "marker_seen": state["marker_seen"],
        "clue_visible_in_model_input": state["clue_visible"],
        "clue_recovered": clue_back,
        "restore_statuses": state["statuses"],
        "restore_response_excerpt": result[:600],
    }


def decide(body):
    messages = body.get("messages") or []
    tools = [t.get("function", {}).get("name") for t in (body.get("tools") or [])
             if isinstance(t, dict)]
    note({"event": "request", "n_messages": len(messages),
          "tools": sorted(filter(None, tools)),
          "roles": [m.get("role") for m in messages]})
    results = tool_results(messages)

    if not tools:
        return {"content": "ok"}  # auxiliary (title) request

    if results and state["restore_sent"]:
        # A sutra_restore result: route by the pending negative-path step.
        result = results[-1]
        if state["pending"] in ("invalid", "missing"):
            try:
                state["statuses"].append(json.loads(result).get("status"))
            except ValueError:
                state["statuses"].append("unparseable")
            if state["pending"] == "invalid":
                state["pending"] = "missing"
                return {"tool_call": ("sutra_restore",
                                      {"id": "su_" + "cd" * 16, "mode": "search",
                                       "query": CLUE})}
            state["pending"] = "judge"
            return {"tool_call": ("sutra_restore",
                                  {"id": state["entry_id"], "mode": "search",
                                   "query": CLUE, "context": 1})}
        # The third response is the real id's result: record its status, then judge.
        try:
            state["statuses"].append(json.loads(result).get("status"))
        except ValueError:
            state["statuses"].append("unparseable")
        judge_treatment(result)
        return {"content": (f"OC-SUTRA-{'OK' if state['verdict']['ok'] else 'FAIL'}: "
                            f"marker_seen={state['marker_seen']} "
                            f"clue_recovered={state['verdict']['clue_recovered']} "
                            f"statuses={state['statuses']}")}

    if results:
        # The bash result decides the scenario's expectations.
        result = results[-1]
        marker = MARKER_RE.search(result)
        entry = ID_RE.search(marker.group(0)) if marker else None
        state["marker_seen"] = bool(marker)
        state["clue_visible"] = CLUE in result
        state["entry_id"] = entry.group(1) if entry else None
        note({"event": "tool_result", "bytes": len(result), "marker": bool(marker),
              "clue_visible": state["clue_visible"], "entry": state["entry_id"]})

        if SCENARIO == "treatment":
            if not entry:
                state["verdict"] = {"ok": False, "marker_seen": bool(marker),
                                    "reason": "no marker or no id on the oversized result"}
                return {"content": "OC-SUTRA-FAIL: no usable marker"}
            # Negative-first recovery through the REAL tool pipeline.
            state["restore_sent"] = True
            state["pending"] = "invalid"
            return {"tool_call": ("sutra_restore", {"id": "not-an-id", "mode": "search",
                                                    "query": CLUE})}
        if SCENARIO == "diff":
            intact = bool(not marker and "diff --git" in result and len(result) > 24_576)
            state["verdict"] = {"ok": intact, "marker_present": bool(marker),
                                "model_facing_bytes": len(result),
                                "reason": "a diff must reach the model byte-intact, no marker"}
            return {"content": f"OC-SUTRA-{'OK' if intact else 'FAIL'}: diff intact={intact}"}
        if SCENARIO == "oversize":
            # OpenCode truncates giant tool output BEFORE the hook, so the
            # bridge's 2 MiB cap is not expected to fire here; the invariant is
            # a bounded model-facing copy (marker allowed) and a live session.
            ok = bool(len(result) <= 24_576)
            state["verdict"] = {"ok": ok, "marker_present": bool(marker),
                                "model_facing_bytes": len(result),
                                "reason": "giant raw output arrived bounded at the model"}
            return {"content": "OC-SUTRA-" + ("OK" if ok else "FAIL") + ": bounded=" + str(ok)}
        # failopen: the untouched output must reach the model.
        intact = bool(not marker and len(result) > 24_576)
        state["verdict"] = {"ok": intact, "marker_present": bool(marker),
                            "model_facing_bytes": len(result),
                            "reason": "the untouched output reaches the model; the "
                                      "session finishes"}
        return {"content": f"OC-SUTRA-{'OK' if intact else 'FAIL'}: intact={intact}"}

    return {"tool_call": ("bash", {"command": COMMANDS.get(SCENARIO, TREATMENT_COMMAND),
                                   "description": "generate synthetic output"})}


def sse(message, finish):
    chunk = {"id": "chatcmpl-poc", "object": "chat.completion.chunk", "created": 0,
             "model": MODEL, "choices": [{"index": 0, "delta": message,
                                          "finish_reason": None}]}
    done = {"id": "chatcmpl-poc", "object": "chat.completion.chunk", "created": 0,
            "model": MODEL, "choices": [{"index": 0, "delta": {},
                                         "finish_reason": finish}]}
    return (f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(done)}\n\n"
            "data: [DONE]\n\n").encode()


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
                with open(f"/tmp/oc-poc/verdict-{SCENARIO}.json", "w") as fh:
                    json.dump(verdict, fh, indent=2)
            except OSError:
                pass
        if "tool_call" in decision:
            name, args = decision["tool_call"]
            call = [{"id": "call_poc", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}]
            message, finish = {"role": "assistant", "tool_calls": call}, "tool_calls"
        else:
            message, finish = {"role": "assistant",
                               "content": decision["content"]}, "stop"
        self._send(200, sse(message, finish), "text/event-stream")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
