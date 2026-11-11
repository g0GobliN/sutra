# SUTRA Architecture Documentation

## Overview

SUTRA (Less Output. More Signal.) is a deterministic cost-optimization middleware for AI coding agents. Its architecture centers on **recoverable tool-output compression** without model calls, preserving evidence while reducing context growth.

### Core Philosophy
- **Deterministic**: Same input always produces identical compression and markers
- **Recoverable**: Every elided byte can be retrieved when needed
- **Fail-open**: Any error leaves original results untouched
- **Host-agnostic**: Works across Hermes, Claude Code, OpenCode, Codex, CLI, MCP clients
- **Agent-agnostic**: Same core for all AI platforms

## System Components

### 1. SUTRA Core Engine (`engine.py`)

**Purpose**: Classification and deterministic compression of tool outputs

#### Compression Kinds:
- **empty**: Zero-byte or whitespace-only results
- **patch**: Git diff output (never rewritten)
- **json**: JSON documents with field name compression
- **traceback**: Python/Node.js stack traces with error-end preservation
- **text**: Plain text with head/tail truncation and repeat collapse

#### Key Features:
- **Head/tail elision**: Configurable byte limits for beginning/end preservation
- **Repeat collapse**: Identical consecutive lines replaced with counters
- **JSON field compression**: Non-critical string fields can be replaced with markers
- **Diff protection**: Git patches remain byte-identical
- **Traceback safety**: Error information preserved at end of stack traces
- **Fail-open everywhere**: Parser errors return original content

### 2. Host Adapter Seam (`core.py`)

**Purpose**: Unified interface between SUTRA core and host-specific adapters

#### Key Functions:
- **`process_result(result, tool_name)`**: Process raw tool execution results
- **`process_text(text, tool_name)`**: Process already-extracted text
- **`restore(request)`**: Execute recovery protocol per `sutra_restore`
- **`outcome_json(outcome)`**: Serialize compression results to JSON

#### Evidence Tool Bypass:
SUTRA recognizes its own tool calls (`sutra_restore`, `sutra_compress`) and
avoids double-processing:
```python
if tool_name in ["sutra_restore", "sutra_compress"]:
    return outcome  # skip processing
```

### 3. Local Storage (`store.py`)

**Purpose**: Bounded retention of original tool outputs for recovery

#### Key Characteristics:
- **Permission protection**: Directory `0700`, files `0600`
- **Atomic writes**: Write to temp file then rename
- **Unique IDs**: `su_[0-9a-f]{32}` format using cryptographically secure randomness
- **Bounded retention**: Configurable entry count, total size, TTL
- **O_NOFOLLOW safety**: Symlink attacks prevented at read time

#### Storage Limits (defaults):
- **Maximum entries**: 2,000 per profile
- **Entry size cap**: 32 MiB per original
- **Total store size**: 128 MiB
- **Retention window**: 7 days (168 hours)

### 4. Recovery Protocol (`recovery.py`)

**Purpose**: Retrieve elided evidence when agents need it

#### Supported Modes:
- **`bytes`**: Exact byte range from original
- **`lines`**: Line range from original
- **`search`**: Literal substring scan (at most 200 chars, 20 matches)
- **`full`**: Entire original when fits in response cap (8 KiB default)

#### Response Statuses:
- **`ok`**: Successful recovery with content
- **`not_found`**: Entry missing, expired, or hash mismatch
- **`expired`**: Entry beyond TTL
- **`too_large`**: Requested range exceeds response cap
- **`unavailable`**: Storage system unavailable
- **`error`**: Internal processing error

### 5. Exact-Duplicate Reuse (`reuse.py`)

**Purpose**: Replace identical tool results with markers in same session

#### Key Features:
- **Hash-based tracking**: SHA-256 of compressed result
- **Store verification**: Before marking, verify entry exists with matching hash
- **Bounded index**: 64 sessions × 128 digests per process
- **Exclusion rules**: Diffs, JSON, tracebacks never replaced

### 6. Configuration (`config.py`)

**Purpose**: Single-source configuration resolution with per-call defaults

#### Configuration Categories:
- **Compression settings**: `SUTRA_ENABLED`, `SUTRA_MAX_BYTES`, `SUTRA_HEAD_BYTES`, `SUTRA_TAIL_BYTES`
- **Storage settings**: `SUTRA_STORE`, `SUTRA_STORE_DIR`, `SUTRA_STORE_TTL_HOURS`, quotas
- **Recovery settings**: `SUTRA_RECOVERY_MAX_BYTES`, `SUTRA_RECOVERY_MAX_MATCHES`
- **Policy settings**: `SUTRA_POLICY`, `SUTRA_REUSE`, `SUTRA_REUSE_MIN_BYTES`

#### Resolution Process:
1. Parse environment variables
2. Apply validation and fallback defaults
3. Merge with system defaults
4. Return validated `Config` object

### 6. Client Adapters

**Purpose**: Translate host-specific tool execution to SUTRA core

#### Hermes Agent:
- **Integration**: Native `tool_execution` middleware
- **Mechanism**: Automatic replacement via Hermes plugin middleware
- **Verification**: CI, scripted-provider E2E (`scripts/e2e/run.sh`)
- **Configuration**: `sutra install --client hermes`

#### Claude Code:
- **Mechanism**: `PostToolUse` hook with `sutra-mcp` recovery
- **Requirements**: Hook installation, one-time trust review
- **Verification**: Live host test with scripted provider
- **Configuration**: `sutra install --client claude`

#### OpenCode:
|- **Mechanism**: Generated `tool.execute.after` plugin
|- **Configuration**: `sutra install --client opencode`
|- **Verification**: OpenCode 1.19.0 with real Bash tool verification

#### Codex CLI:
- **Mechanism**: Trusted `PostToolUse` hook + MCP recovery
- **Requirement**: One-time `/hooks` trust review
- **Verification**: Live host test with Bash tool call
- **Configuration**: `sutra install --client codex`

#### OpenClaw:
- **Mechanism**: Node.js plugin
- **Configuration**: `sutra install --client openclaw`

#### MCP Server:
|- **Protocol**: JSON-RPC 2.0, protocol `2024-11-05`
|- **Purpose**: Explicit compression/restore for non-automatic clients
|- **Implementation**: `scripts/sutra-mcp-server.py`

#### CLI Adapter:
- **Purpose**: Subprocess filter for agents without native adapters
- **Implementation**: `scripts/sutra-cli.py`
- **Commands**: `compress`, `restore`, `config`

## Integration Architecture

### Automatic vs Manual Integration

| Host | Automatic? | Reason |
|------|------------|--------|
| Hermes Agent | Yes | Native middleware |
| Claude Code | Yes | `PostToolUse` hook verified | |
| OpenCode | Yes | Generated plugin | |
| Codex CLI | Yes | Trusted hook (post-trust) | |
| OpenClaw | Yes | Node.js plugin | |
| MCP clients | No | No server-side interception | |
| CLI | No | Subprocess filter | |

### Integration Flow

```text
host adapter                    tool execution                    SUTRA core
      │                           runs a tool                         (engine.py, store.py)
      ▼                           raw result                         ↓
   process_result()               │                     classify → rewrite
      │                           ▼                       ↓
   write back                      marker               store original
      │                           marker              id written to store
      ▼                           │                       │
   model sees                     model reads            recovery ready
   smaller result                 smaller result
```

## Safety Guarantees

### Diff Protection
- Git patches remain byte-identical
- Multi-file diffs, binary indicators, renames all preserved
- Malformed diff-like content treated as text

### JSON Preservation
- JSON documents decoded and re-encoded
- Schema preserved (arrays stay arrays)
- Duplicate keys collapse to last value
- Non-critical fields can be compressed

### Traceback Safety
- Exception type and context preserved
- Stack frames retained in full
- Error end never dropped

### Fail-Open Guarantees
- **Detection failure**: Return original
- **Parser exception**: Return original
- **Malformed input**: Return original
- **Unknown format**: Return original
- **Storage failure**: Return compressed but no id
- **Recovery failure**: Return marker without id

## Performance Characteristics

### Compression Cost
```
Input size  →  Core compress  →  Store write  →  Marker generation
0.1 KB      0.0 ms          0.0 ms        0.0 ms
10 KB       0.7 ms          0.3 ms        0.0 ms
100 KB      22.1 ms         1.3 ms        0.1 ms
1 MB        170.3 ms        8.8 ms        0.1 ms
5 MB        912.2 ms       49.6 ms        0.1 ms
```

### Storage Retention
- **Per entry**: up to 32 MiB
- **Total store**: 128 MiB
- **Entry lifetime**: 7 days (configurable)
- **Cleanup**: Automatic on access, periodic sweep

### Benchmark Results (Eight Deterministic Fixtures)

| Payload | Before | After | Saved |
|---------|--------|-------|-------|
| 12,000-line command output | 60.9 kB | 18.6 kB | 70.0% |
| JSON tool envelope | 85.9 kB | 19.2 kB | 77.7% |
| Test run, 2,000 cases | 99.8 kB | 18.6 kB | 81.4% |
| 3,000-frame traceback | 359.4 kB | 10.3 kB | 97.1% |
|| 432 kB patch | 432.1 kB | 432.1 kB | 0.0% |
| 900-row SQL result | 46.6 kB | 18.5 kB | 60.1% |
| 8,000-line build log | 334.9 kB | 18.6 kB | 94.5% |
|| 8,000-item JSON array | 206.9 kB | 206.9 kB | 0.0% |

**Total**: 1.63 MB → 742.7 kB (54.3%)

## Recovery Protocol

### Marker Format
```text
… [sutra: partial · 133,522 bytes elided (large output) · 
   original 151,920 B retained 168h · 
   retrieve: sutra_restore(id="su_3e72c41e…", mode="bytes", start=12259, end=145781)] …
```

### Information Provided
1. **Completeness**: Is this complete or partial?
2. **What missing**: How much was elided and why?
3. **Availability**: Is the original still retained?
4. **How to retrieve**: Exact `sutra_restore` call

### Recovery Modes
- **`bytes`**: Precise byte range
- **`lines`**: Line range
- **`search`**: Literal substring
- **`full`**: Entire content (if fits)

### Response Bounds
- **Maximum response**: 8 KiB default, 64 KiB hard limit
- **Maximum matches**: 5 lines default, 20 hard limit
- **Individual line cap**: Response cap per line (clipped if longer)

## State Management

### Session State
- **Exact-duplicate reuse index**: Per-process, resets on restart
- **Store state**: Persistent across sessions, profile-isolated
- **Session state**: No shared mutable state between agents

### State Cleanup
- **Storage TTL**: 7 days default, then cleaned on access
- **Index bounds**: 64 sessions × 128 digests
- **Manual cleanup**: `sutra uninstall` removes only SUTRA entries

## Deployment Considerations

### Multi-Profile Isolation
```bash
# Each profile gets its own store directory
$HERMES_HOME/cache/sutra-store/
SUTRA_STORE_DIR/$profile_name/
```

### Security Considerations
- **Store encryption**: None (tool output can contain secrets)
- **Permission protection**: Directory `0700`, files `0600`
- **Filesystem protection**: Symlink attacks prevented

### Monitoring
- **Statistics logging**: JSONL file with compression metrics
- **Health checks**: `sutra doctor` validates installation
- **Status reporting**: `sutra status` shows current state

## Future Extensibility

### Parser Framework
SUTRA's parser framework allows extending compression to specific CLI output:
- **Test runners**: Jest, Vitest, Pytest, Cargo test, Go test
- **Compilers**: TypeScript `tsc`, Mypy, Cargo check, Go compiler
- **Runtime errors**: Python traceback, Node.js stack traces
- **Linters**: ESLint
- **Build tools**: npm, pnpm, Yarn, Docker

### Adaptive Budgets (Phase 3)
- **Tokenizer selection**: cl100k_base, o200k_base options

### Live-Agent Evaluation (Phase 4)
- **A/B testing framework**: `scripts/ab/run.py`
- **Paired experiments**: Control vs treatment
- **Comprehensive metrics**: Task completion, token usage, cost

## References

- [SUTRA README](../README.md): Product overview and installation
- [Configuration docs](../docs/configuration.md): Complete configuration reference
- [Recovery docs](../docs/recovery.md): Detailed recovery protocol
- [Integrations docs](../docs/integrations.md): Client-specific integration details
- [Benchmark docs](../docs/benchmark.md): Methodology and results
- [Contract docs](../docs/contract.md): Correctness guarantees and tests

This architecture document provides the foundation for understanding SUTRA's deterministic, recoverable tool-output compression system while maintaining the safety guarantees required for production AI coding assistance.