# SUTRA Telemetry (OpenTelemetry)

SUTRA emits OpenTelemetry metrics for compression operations, recovery, and strategy selection. Telemetry is **disabled by default** — there is zero overhead (no imports, no exports, no background threads) unless you explicitly enable it.

## Quick Start

```bash
# Enable telemetry and point to your OTLP collector
export SUTRA_TELEMETRY=1
export SUTRA_TELEMETRY_ENDPOINT=http://localhost:4318/v1/metrics
# Optional: trace sampling rate (0.0–1.0)
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
| `SUTRA_TELEMETRY_ENDPOINT` | `telemetry_endpoint` | `http://localhost:4318/v1/metrics` | OTLP HTTP endpoint for metrics (traces go to `/v1/traces` on same host) |
| `SUTRA_TELEMETRY_SAMPLE_RATE` | `telemetry_sample_rate` | `1.0` | Sampling rate applied to every emitted metric and trace (0.0–1.0; 0.0 emits nothing, 1.0 emits everything). |

All three settings are also available on `config.Config` and can be set programmatically.

## Zero-Overhead Guarantee

When `telemetry_enabled=False` (the default):
- No OpenTelemetry modules are imported
- No background threads are started
- No network connections are made
- All telemetry calls are resolved to a no-op implementation at import time
- The hot path (`compress()`, `store.get()`, `store.prune()`) has no conditional branches for telemetry

This is achieved by:
1. Lazy imports inside `CompressionTelemetry._init_otel()` — only executed when enabled
2. `get_telemetry()` returning a `_NoopTelemetry` instance when disabled
3. `_NoopTelemetry` methods being empty functions that the JIT can inline away

## Integration Points

### Engine (compression)
```python
from sutra import engine, config

cfg = config.resolve()
out, stats = engine.compress(large_output, cfg=cfg)
# Telemetry recorded automatically:
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
Use the [OpenTelemetry Prometheus Exporter](https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/exporter/opentelemetry-exporter-prometheus) if you don't need traces.

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
| High CPU/memory | Sampling rate too high or export interval too low | Reduce `SUTRA_TELEMETRY_SAMPLE_RATE` or increase collector `export_interval_millis` |
| "telemetry enabled but opentelemetry dependencies not installed" warning | Enabled without optional deps | Install extras or disable telemetry |

## Version Compatibility

| SUTRA | OpenTelemetry API/SDK |
|-------|----------------------|
| 0.6.1+ | >=1.24 |

The telemetry module uses stable OpenTelemetry 1.x APIs. Breaking changes in OTel 2.x will require a SUTRA major version bump.