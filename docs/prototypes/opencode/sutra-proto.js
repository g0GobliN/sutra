// SUTRA OpenCode replacement prototype — question under test:
// does a tool.execute.after hook that REPLACES output.output deliver the
// replaced text to the MODEL (not just the UI) on opencode 1.19.0?
//
// Mirrors the working caveman-blocks.js shape: plugin factory, bash filter,
// spawnSync to the Python core, fail open on any error. Logs everything it
// sees and returns to /tmp/sutra-oc-proto/evidence.jsonl as ground truth.
import { spawnSync } from "node:child_process";
import { appendFileSync } from "node:fs";

const DRIVER = process.env.SUTRA_OC_DRIVER || "/tmp/sutra-oc-proto/driver.py";
const EVIDENCE = process.env.SUTRA_OC_EVIDENCE || "/tmp/sutra-oc-proto/evidence.jsonl";

function log(event) {
  try { appendFileSync(EVIDENCE, JSON.stringify(event) + "\n"); } catch {}
}

export const SutraProto = async () => ({
  "tool.execute.after": async (input, output) => {
    log({ hook: "after", tool: input.tool, call_id: input.callID,
          output_keys: Object.keys(output ?? {}),
          output_type: typeof output?.output,
          bytes_in: typeof output?.output === "string" ? output.output.length : -1 });
    if (input.tool !== "bash") return;
    const original = String(output?.output ?? "");
    if (!original) return;
    try {
      const r = spawnSync("python3", [DRIVER], {
        input: JSON.stringify({ text: original }),
        encoding: "utf8", timeout: 30_000,
      });
      if (r.status !== 0 || !r.stdout) {
        log({ hook: "driver_failed", status: r.status, stderr: (r.stderr || "").slice(0, 300) });
        return; // fail open
      }
      const { out, stats } = JSON.parse(r.stdout);
      output.output = out;  // THE MUTATION UNDER TEST: full replacement
      log({ hook: "replaced", bytes_in: original.length, bytes_out: out.length,
            rule: stats.rule, changed: stats.changed });
    } catch (e) {
      log({ hook: "error", message: String(e).slice(0, 300) });
    }
  },
});
