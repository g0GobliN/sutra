// The SUTRA host bundle: every stage this harness exposes, in one package.
//
//   tools/post-execute  rewrite a tool result the model is about to read
//   mcpManager.register expose sutra_restore so a marker's id can be exchanged
//                       for the exact bytes the store kept
//
// Both stages call the same seam (`python -m sutra process-result`), so a host
// with no Python-side plugin still trims and still recovers.

import { spawn, spawnSync } from 'node:child_process'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const name = 'dsh-sutra-host'

// Cordis reads `inject` as a service-name map: an object key would be taken as
// a service called "required". Only the tool service is a hard dependency, so
// the bundle starts as soon as it exists; anything else is read defensively.
export const inject = ['tools']

const DEFAULTS = {
  python: 'python3',
  pythonPath: '',
  mode: 'balanced',
  home: join(homedir(), '.dsh', 'sutra'),
  timeoutMs: 4000,
  minTextBytes: 2048,
}

/** Run one seam call; null means "nothing to do", never an error. */
function seam(config, args, stdin) {
  return new Promise((resolve) => {
    let child
    try {
      child = spawn(config.python, args, {
        env: {
          ...process.env,
          PYTHONPATH: config.pythonPath || process.env.PYTHONPATH || '',
          SUTRA_MODE: config.mode,
          SUTRA_STORE_DIR: join(config.home, 'store'),
          SUTRA_STATS_FILE: join(config.home, 'stats.jsonl'),
        },
        stdio: ['pipe', 'pipe', 'ignore'],
      })
    } catch {
      resolve(null)
      return
    }
    const timer = setTimeout(() => {
      try {
        child.kill('SIGKILL')
      } catch {}
      resolve(null)
    }, config.timeoutMs)
    let out = ''
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', (chunk) => {
      out += chunk
    })
    child.on('error', () => {
      clearTimeout(timer)
      resolve(null)
    })
    child.on('close', () => {
      clearTimeout(timer)
      try {
        resolve(JSON.parse(out))
      } catch {
        resolve(null)
      }
    })
    try {
      child.stdin.end(stdin)
    } catch {
      clearTimeout(timer)
      resolve(null)
    }
  })
}

/** Text blocks of a decision's content, in order. */
function textBlocks(content) {
  if (!Array.isArray(content)) return []
  return content
    .map((block, index) => ({ block, index }))
    .filter(({ block }) => block && block.type === 'text' && typeof block.text === 'string')
}

export function apply(ctx, rawConfig) {
  const config = { ...DEFAULTS, ...(rawConfig ?? {}) }

  ctx.on('tools/post-execute', async (exec, result, next) => {
    const decision = await next()
    try {
      if (decision.kind !== 'accept') return decision
      const blocks = textBlocks(decision.content)
      const big = blocks.filter(({ block }) => block.text.length >= config.minTextBytes)
      if (big.length === 0) return decision
      const reply = await seam(
        config,
        ['-m', 'sutra', 'process-result'],
        JSON.stringify({ tool_name: exec.name, texts: big.map(({ block }) => block.text) }),
      )
      if (reply === null || reply.changed !== true) return decision
      const content = decision.content.slice()
      big.forEach(({ block, index }, position) => {
        const text = reply.texts?.[position]
        if (typeof text === 'string' && text !== block.text) {
          content[index] = { ...block, text }
        }
      })
      return { ...decision, content }
    } catch {
      return decision
    }
  })

  // Recovery: the marker quotes an id, so the harness needs the tool that
  // answers it. Registered for this bundle's lifetime, withdrawn on disposal.
  const mcp = typeof ctx.get === 'function' ? ctx.get('mcpManager') : ctx.mcpManager
  if (mcp !== undefined) {
    try {
      mcp.register('sutra', {
        transport: 'stdio',
        command: config.python,
        args: ['-m', 'sutra.mcp'],
        env: {
          PYTHONPATH: config.pythonPath || '',
          SUTRA_MODE: config.mode,
          SUTRA_STORE_DIR: join(config.home, 'store'),
          SUTRA_STATS_FILE: join(config.home, 'stats.jsonl'),
        },
        cwd: '',
        startupTimeoutMs: 20000,
      })
    } catch (error) {
      ctx.logger?.warn?.(`sutra: recovery server not registered: ${String(error)}`)
    }
  }

  // Report the wiring once, so a user can tell "installed" from "loaded".
  const probe = spawnSync(config.python, ['-m', 'sutra', 'doctor'], {
    env: { ...process.env, PYTHONPATH: config.pythonPath || '' },
    encoding: 'utf8',
    timeout: config.timeoutMs,
  })
  const ready = probe.status === 0
  ctx.logger?.info?.(`sutra: host bundle loaded (tool stage ${ready ? 'ready' : 'unproven'})`)
}
