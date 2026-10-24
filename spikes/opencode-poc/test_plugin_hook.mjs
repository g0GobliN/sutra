// Node tests for the real plugin hook code — no OpenCode, no bridge.
// global fetch is stubbed per case; the plugin module itself is imported
// unmodified, so what is exercised is exactly what OpenCode loads.
import assert from "node:assert/strict"
import { writeFileSync, mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import process from "node:process"

const home = mkdtempSync(join(tmpdir(), "sutra-oc-plugin-"))
delete process.env.SUTRA_OC_BRIDGE_TOKEN // the file, not ambient env, is under test
process.env.SUTRA_OC_BRIDGE_TOKEN_FILE = join(home, "token")
process.env.SUTRA_OC_BRIDGE_URL = "http://127.0.0.1:8765"
writeFileSync(process.env.SUTRA_OC_BRIDGE_TOKEN_FILE, "test-token-123\n", { mode: 0o600 })

const plugin = await import("./plugin/sutra-spike.js")
const hooks = await plugin.SutraSpike({})
const after = hooks["tool.execute.after"]

let lastFetch
globalThis.fetch = async (url, options) => {
  lastFetch = { url, options }
  return globalThis.__fetchResponse
}

const output = (text) => ({ title: "t", output: text, metadata: { exit: 0 } })

// 1. success: the compressed copy replaces the output in place; metadata survives.
globalThis.__fetchResponse = {
  ok: true, json: async () => ({ changed: true, marker: true, id: "su_" + "a".repeat(32),
                                output: "head\n[sutra: partial]\ntail" }),
}
const o1 = output("x".repeat(40_000))
await after({ tool: "bash" }, o1)
assert.equal(o1.output, "head\n[sutra: partial]\ntail")
assert.equal(o1.title, "t")
assert.deepEqual(o1.metadata, { exit: 0 })
assert.equal(lastFetch.options.headers["x-sutra-token"], "test-token-123")

// 2. bridge rejects the change: nothing rewritten.
globalThis.__fetchResponse = { ok: true, json: async () => ({ changed: false }) }
const o2 = output("original")
await after({ tool: "bash" }, o2)
assert.equal(o2.output, "original")

// 3. fetch rejects (bridge down): fail open, original preserved.
globalThis.fetch = async () => { throw new Error("connection refused") }
const o3 = output("precious")
await after({ tool: "bash" }, o3)
assert.equal(o3.output, "precious")

// 4. bridge 500: fail open.
globalThis.fetch = async () => ({ ok: false, status: 500, json: async () => ({ error: "boom" }) })
const o4 = output("precious")
await after({ tool: "bash" }, o4)
assert.equal(o4.output, "precious")

// 5. malformed JSON from a compromised bridge: fail open.
globalThis.fetch = async () => ({ ok: true, json: async () => { throw new SyntaxError("bad") } })
const o5 = output("precious")
await after({ tool: "bash" }, o5)
assert.equal(o5.output, "precious")

// 6. hook promising a non-string replacement: ignored, original preserved.
globalThis.fetch = async () => ({ ok: true, json: async () => ({ changed: true, output: 42 }) })
const o6 = output("precious")
await after({ tool: "bash" }, o6)
assert.equal(o6.output, "precious")

// 7. non-string tool output: the hook does not even call the bridge.
let called = 0
globalThis.fetch = async () => { called += 1; throw new Error("must not be called") }
const o7 = { title: "t", output: { structured: true }, metadata: {} }
await after({ tool: "bash" }, o7)
assert.deepEqual(o7.output, { structured: true })
assert.equal(called, 0)

// 8. sutra_restore on an unreachable bridge: a JSON error envelope, never a throw.
const restore = hooks.tool.sutra_restore
const envelope = JSON.parse(await restore.execute({ id: "su_" + "b".repeat(32) }))
assert.equal(envelope.status, "error")
assert.match(envelope.error, /bridge unreachable/)

console.log("plugin hook tests: 8/8 OK")
