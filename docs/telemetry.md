# SUTRA Telemetry (OpenTelemetry)

SUTRA emits OpenTelemetry metrics for compression operations, recovery, and strategy selection. Telemetry is **disabled by default**: no OpenTelemetry imports, exports, or background threads run unless you explicitly enable it.

When enabled, export is **best-effort**. Exporters run on OpenTelemetry's background threads, use bounded timeouts, and an unreachable, failing, or malformed collector does not change the compression result or the CLI exit status. A short-lived CLI may still wait for the bounded shutdown timeout (several seconds) while the SDK gives up on in-flight metrics or span exports.

## Quick Start

```bash
# Enable telemetry and point to your OTLP collector
export SUTRA_TELEMETRY=1
export SUTRA_TELEMETRY_ENDPOINT=http://localhost:4318/v1/metrics
# Optional: trace sampling ratio (0.0–1.0); metrics are not sampled
export SUTRA_TELEMETRY_SAMPLE_RATE=1.0
```

Or in Python:

```python
from sutra import config

cfg = config.Config(
    telemetry_enabled=True,
    telemetry_endpoint="http://localhost:4318/v1/metrics",
    telemetry_sample_rate=1.0,
    # ... other settings ...
)
```

Install the optional dependencies:

```bash
pip install "sutra-agent[telemetry]"
```

## Metrics Emitted

| Metric | Type | Unit | Description | Attributes |
|--------|------|------|-------------|------------|
| `sutra.compression.ratio` | Histogram | 1 (ratio) | Compression ratio = compressed_bytes / original_bytes | `sutra.compression.rule` |
| `sutra.compression.tokens_saved` | Counter | tokens | Estimated tokens saved by compression | `sutra.compression.rule` |
| `sutra.compression.latency_ms` | Histogram | ms | Time spent in compression | `sutra.compression.rule` |
| `sutra.recovery.count` | Counter | 1 | Recovery operations by outcome | `sutra.recovery.status` |
| `sutra.strategy.selected` | Counter | 1 | Compression strategy selected | `sutra.strategy.name` |

### Attribute Values

**`sutra.compression.rule`** (compression rule applied):
- `head-tail` — kept head + tail, omitted middle
- `json-fields` — JSON object with field limits applied
- `parser:<name>` — parser-specific rule (e.g., `parser:ls`, `parser:ps`)
- `fail-open` — compression failed, returned original
- `json-kept` — output no longer valid JSON, kept original
- `invalid-utf8-kept` — input not valid UTF-8

**`sutra.recovery.status`** (recovery operation outcome):
- `ok` — original recovered successfully
- `missing` — entry ID not found or data corrupted
- `expired` — entry past TTL
- `invalid` — malformed entry ID
- `unavailable` — store disabled
- `orphan` — metadata without data file (cleaned up)
- `temp` — abandoned temp file (cleaned up)
- `quota` — evicted due to quota enforcement

**`sutra.strategy.name`** (strategy selected):
- Same values as `sutra.compression.rule` for compression strategies
- Additional values for recovery: `recovery`

## Configuration

| Environment Variable | Config Field | Default | Description |
|---------------------|--------------|---------|-------------|
| `SUTRA_TELEMETRY` | `telemetry_enabled` | `false` | Enable OpenTelemetry export |
| `SUTRA_TELEMETRY_ENDPOINT` | `telemetry_endpoint` | `http://localhost:4318/v1/metrics` | OTLP HTTP endpoint for metrics; the optional `trace_compression()` helper sends spans to `/v1/traces` on the same host |
| `SUTRA_TELEMETRY_SAMPLE_RATE` | `telemetry_sample_rate` | `1.0` | Trace sampling ratio from `0.0` to `1.0`. Metrics are exact counters/histograms and are emitted whenever telemetry is enabled; `0.0` disables both metric recording and trace spans. |

All three settings are also available on `config.Config` and can be set programmatically.

## Zero-Overhead Guarantee

When `telemetry_enabled=False` (the default):
- No OpenTelemetry modules are imported
- No background threads are started
- No network connections are made
- `get_telemetry()` returns a `_NoopTelemetry` instance
- `compress()`, `store.get()`, and `store.prune()` call that seam, whose methods return immediately
- There are no OpenTelemetry imports or exporter branches in the compression or store hot paths

This is achieved by lazy imports inside `CompressionTelemetry._init_otel()` (only executed when enabled) and by `get_telemetry()` returning `_NoopTelemetry` for disabled or unavailable telemetry.

## Failure Behavior

Telemetry never changes the result of a compression or restore, and it does not raise into the caller:
- Exporter connection, HTTP, timeout, and response-format failures are caught inside the SDK and by the SUTRA seam.
- A failed SDK initialization falls back to `_NoopTelemetry`; compression continues. A malformed endpoint is treated as an export failure by the SDK and cannot affect the result.
- Export requests use a 2-second timeout. `CompressionTelemetry.shutdown()` also uses a bounded timeout.
- Exporter diagnostics are suppressed from an unconfigured CLI's stderr so a broken collector cannot corrupt command output. Configure Python logging at `DEBUG` level to see them.
- A dead collector can delay a short-lived CLI for several seconds at process shutdown (each exporter has a bounded 2-second budget); it cannot change the exit code or stdout.

## Sampling

`SUTRA_TELEMETRY_SAMPLE_RATE` is a trace sampling ratio: `0.0` disables spans, `1.0` samples every span, and values in between use OpenTelemetry's `TraceIdRatioBased` sampler. Compression metrics are counters and histograms, not independent events; they are emitted whenever telemetry is enabled and are not probabilistically downsampled. Use the collector or `SUTRA_TELEMETRY=0` if metric volume is a problem.

## Integration Points

### Engine (compression)
```python
from sutra import engine, config

cfg = config.resolve()
out, stats = engine.compress(large_output, cfg=cfg)
# The engine records metrics automatically (no spans):
# - sutra.compression.ratio
# - sutra.compression.tokens_saved
# - sutra.compression.latency_ms
# - sutra.strategy.selected
```

### Store (recovery)
```python
from sutra import store

s = store.store_from_env()
entry = s.put(output)
# Later...
lookup = s.get(entry.id)
# Telemetry recorded automatically:
# - sutra.recovery.count (status: ok/missing/expired/...)
```

Store pruning also records recovery metrics:
```python
report = s.prune()
# Records: sutra.recovery.count for expired, orphan, temp, quota evictions
```

### Traces

The engine does not create trace spans. Library callers that want a span around a compression can use `CompressionTelemetry.trace_compression()`; it also records the compression metrics on exit. Spans are sent to the endpoint with `/v1/metrics` replaced by `/v1/traces`, and the configured sample ratio applies to them.

## Exporter Setup

### OpenTelemetry Collector (recommended)
```yaml
# otelcol-config.yaml
receivers:
  otlp:
    protocols:
      http:

exporters:
  prometheus:
    endpoint: "0.0.0.0:9464"
  logging:
    loglevel: debug

service:
  pipelines:
    metrics:
      receivers: [otlp]
      exporters: [prometheus, logging]
    traces:
      receivers: [otlp]
      exporters: [logging]
```

Run:
```bash
otelcol-contrib --config otelcol-config.yaml
```

Then point SUTRA at it:
```bash
export SUTRA_TELEMETRY_ENDPOINT=http://localhost:4318/v1/metrics
```

### Direct to Prometheus (metrics only)
Use the [OpenTelemetry Prometheus Exporter](https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/exporter/opentelemetry-exporter-prometheus) if you don't need traces. The built-in engine path records metrics only.

### Datadog / New Relic / Honeycomb / etc.
Configure the OTLP endpoint for your vendor:
```bash
export SUTRA_TELEMETRY_ENDPOINT=https://otlp.your-vendor.com/v1/metrics
```

## Testing Telemetry

```python
from sutra.telemetry import CompressionTelemetry, get_telemetry
from sutra import config

# Use a test config with telemetry disabled (default)
cfg = config.Config(telemetry_enabled=False, ...)
telemetry = get_telemetry(cfg)

# Verify no-op behavior
telemetry.record_compression(1000, 500, "head-tail", 1.5, 50)
telemetry.record_recovery("ok")
telemetry.record_strategy("head-tail")
# No exceptions, no network calls

# For integration tests with real OTel, enable and use a test exporter
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ImportError: opentelemetry...` | Dependencies not installed | `pip install "sutra-agent[telemetry]"` |
| Metrics not appearing | Collector not running / wrong endpoint | Check `SUTRA_TELEMETRY_ENDPOINT` and collector logs |
| High CPU/memory | High metric volume from many compressions | Disable telemetry, or aggregate/drop metrics at the collector; `SUTRA_TELEMETRY_SAMPLE_RATE` only affects traces |
| CLI exits a few seconds later with no collector | Bounded in-flight export timeout at shutdown | Expected for enabled telemetry; fix the endpoint or set `SUTRA_TELEMETRY=0` |
| No exporter errors on stderr | Best-effort diagnostics are suppressed by default | Configure Python logging at `DEBUG`; check the collector |
| "telemetry enabled but opentelemetry dependencies not installed" warning | Enabled without optional deps | Install extras or disable telemetry |

## Version Compatibility

| SUTRA | OpenTelemetry API/SDK |
|-------|----------------------|
| 0.6.1+ | >=1.24 |

The telemetry module uses stable OpenTelemetry 1.x APIs. Breaking changes in OTel 2.x will require a SUTRA major version bump.