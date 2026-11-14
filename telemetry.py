"""OpenTelemetry telemetry for SUTRA compression operations.

This module provides zero-overhead telemetry when disabled. When enabled, it
uses opentelemetry-api and opentelemetry-sdk as optional lazy dependencies to
emit metrics for compression ratios, tokens saved, latency, recovery operations,
and strategy selection.

Metrics emitted:
- sutra.compression.ratio (Histogram) - compression ratio (compressed/original)
- sutra.compression.tokens_saved (Counter) - estimated tokens saved by compression
- sutra.compression.latency_ms (Histogram) - compression latency in milliseconds
- sutra.recovery.count (Counter) - recovery operations by status
- sutra.strategy.selected (Counter) - compression strategy selected
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional

from . import config

logger = logging.getLogger(__name__)

# Metric names as constants for consistency
M_COMPRESSION_RATIO = "sutra.compression.ratio"
M_COMPRESSION_TOKENS_SAVED = "sutra.compression.tokens_saved"
M_COMPRESSION_LATENCY_MS = "sutra.compression.latency_ms"
M_RECOVERY_COUNT = "sutra.recovery.count"
M_STRATEGY_SELECTED = "sutra.strategy.selected"

# Attributes for metrics
ATTR_RULE = "sutra.compression.rule"
ATTR_STATUS = "sutra.recovery.status"
ATTR_STRATEGY = "sutra.strategy.name"

# Telemetry is best-effort. A dead collector must not keep a short-lived CLI
# process alive waiting for the SDK's default 30-second shutdown budget.
EXPORT_TIMEOUT_SECONDS = 2.0
EXPORT_TIMEOUT_MILLIS = int(EXPORT_TIMEOUT_SECONDS * 1000)


@dataclass(frozen=True)
class TelemetryConfig:
    """Telemetry configuration snapshot."""
    enabled: bool
    endpoint: str
    sample_rate: float


class _NoopTelemetry:
    """Zero-overhead no-op telemetry when disabled."""

    def record_compression(
        self,
        original_bytes: int,
        compressed_bytes: int,
        rule: str,
        latency_ms: float,
        tokens_saved: int = 0,
    ) -> None:
        pass

    def record_recovery(self, status: str) -> None:
        pass

    def record_strategy(self, strategy: str) -> None:
        pass

    def shutdown(self) -> None:
        pass


class CompressionTelemetry:
    """OpenTelemetry metrics emitter for SUTRA compression.

    Lazy-initializes OTel SDK only when telemetry is enabled. All public
    methods are no-ops when disabled, ensuring zero overhead.
    """

    _instance: Optional["CompressionTelemetry"] = None

    def __init__(
        self,
        cfg: config.Config | None = None,
        *,
        meter: Any = None,
    ):
        """Initialize telemetry from config.

        Args:
            cfg: Resolved SUTRA config. If None, reads from environment.
            meter: Optional OTel meter for testing. If provided, skips OTel SDK init.
        """
        cfg = cfg or config.resolve()
        self._config = TelemetryConfig(
            enabled=cfg.telemetry_enabled,
            endpoint=cfg.telemetry_endpoint,
            sample_rate=cfg.telemetry_sample_rate,
        )
        self._enabled = self._config.enabled
        self._meter = meter
        self._tracer = None
        self._meter_provider = None
        self._tracer_provider = None
        self._histogram_ratio = None
        self._counter_tokens_saved = None
        self._histogram_latency = None
        self._counter_recovery = None
        self._counter_strategy = None

        if self._enabled:
            try:
                self._init_otel()
            except Exception:
                # Telemetry is optional and best-effort. Never let an exporter
                # or SDK initialization failure reach compression.
                logger.debug("sutra: telemetry initialization failed; disabling",
                             exc_info=True)
                self._enabled = False
                # Clean up any provider already created before the failure, so
                # a partially initialized attempt cannot leave a worker thread.
                self.shutdown()

    def _init_otel(self) -> None:
        """Initialize OpenTelemetry SDK and metrics instruments."""
        # OTel exporters log connection/export failures through their own
        # logger. In a CLI with no logging configuration, Python's last-resort
        # handler prints those errors to stderr; a best-effort telemetry path
        # must not add that noise to the command's real output. Applications
        # that configure logging still receive the records.
        otel_logger = logging.getLogger("opentelemetry")
        if not any(isinstance(handler, logging.NullHandler) for handler in otel_logger.handlers):
            otel_logger.addHandler(logging.NullHandler())

        if self._meter is not None:
            # Meter was injected (e.g., for testing) - just create instruments
            self._create_instruments()
            return

        try:
            # Lazy imports — only executed when telemetry is enabled
            from opentelemetry import metrics, trace
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError as exc:
            logger.warning(
                "sutra: telemetry enabled but opentelemetry dependencies not installed; "
                "disabling telemetry (%s)",
                exc,
            )
            self._enabled = False
            return

        # Create resource identifying this service
        resource = Resource.create({
            "service.name": "sutra",
            "service.version": self._get_version(),
        })

        # Metrics: OTLP HTTP exporter with periodic reader
        metric_exporter = OTLPMetricExporter(
            endpoint=self._config.endpoint,
            timeout=EXPORT_TIMEOUT_SECONDS,
        )
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=10_000,
            export_timeout_millis=EXPORT_TIMEOUT_MILLIS,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        self._meter_provider = meter_provider

        # Traces: OTLP HTTP exporter with batch processor
        trace_exporter = OTLPSpanExporter(
            endpoint=self._config.endpoint.replace("/v1/metrics", "/v1/traces"),
            timeout=EXPORT_TIMEOUT_SECONDS,
        )
        trace_provider = TracerProvider(
            resource=resource,
            sampler=TraceIdRatioBased(self._config.sample_rate),
        )
        trace_provider.add_span_processor(BatchSpanProcessor(
            trace_exporter,
            export_timeout_millis=EXPORT_TIMEOUT_MILLIS,
        ))
        trace.set_tracer_provider(trace_provider)
        self._tracer_provider = trace_provider

        # Use the providers directly so a settings change can build a new
        # provider even though OpenTelemetry's module-level provider can only
        # be installed once.
        self._meter = meter_provider.get_meter("sutra", self._get_version())
        self._tracer = trace_provider.get_tracer("sutra", self._get_version())

        self._create_instruments()

        logger.debug("sutra: OpenTelemetry initialized (endpoint=%s)", self._config.endpoint)

    def _create_instruments(self) -> None:
        """Create OTel metric instruments from the current meter."""
        if self._meter is None:
            return
        self._histogram_ratio = self._meter.create_histogram(
            M_COMPRESSION_RATIO,
            unit="1",
            description="Compression ratio (compressed_bytes / original_bytes)",
        )
        self._counter_tokens_saved = self._meter.create_counter(
            M_COMPRESSION_TOKENS_SAVED,
            unit="tokens",
            description="Estimated tokens saved by compression",
        )
        self._histogram_latency = self._meter.create_histogram(
            M_COMPRESSION_LATENCY_MS,
            unit="ms",
            description="Compression latency in milliseconds",
        )
        self._counter_recovery = self._meter.create_counter(
            M_RECOVERY_COUNT,
            unit="1",
            description="Recovery operations by status",
        )
        self._counter_strategy = self._meter.create_counter(
            M_STRATEGY_SELECTED,
            unit="1",
            description="Compression strategy selected",
        )

    def _get_version(self) -> str:
        """Get the running SUTRA version, including from a source checkout."""
        try:
            from . import __version__
            return __version__
        except Exception:
            try:
                from importlib.metadata import version
                return version("sutra-agent")
            except Exception:
                return "unknown"

    @classmethod
    def get_instance(cls, cfg: config.Config | None = None) -> "CompressionTelemetry":
        """Get or create the singleton telemetry instance.

        Args:
            cfg: Optional config to use for initialization. Reinitializes when
                the telemetry settings differ from the current instance.

        Returns:
            The singleton CompressionTelemetry instance.
        """
        cfg = cfg or config.resolve()
        desired = TelemetryConfig(
            enabled=cfg.telemetry_enabled,
            endpoint=cfg.telemetry_endpoint,
            sample_rate=cfg.telemetry_sample_rate,
        )
        if cls._instance is not None and cls._instance._config != desired:
            cls._instance.shutdown()
            cls._instance = None
        if cls._instance is None:
            cls._instance = cls(cfg)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        if cls._instance is not None:
            cls._instance.shutdown()
            cls._instance = None

    # --- public API ---------------------------------------------------------

    def record_compression(
        self,
        original_bytes: int,
        compressed_bytes: int,
        rule: str,
        latency_ms: float,
        tokens_saved: int = 0,
    ) -> None:
        """Record a compression operation.

        Args:
            original_bytes: Size of original output in bytes.
            compressed_bytes: Size after compression in bytes.
            rule: Compression rule that was applied (e.g., "head-tail", "json-fields").
            latency_ms: Time taken to compress in milliseconds.
            tokens_saved: Estimated tokens saved (optional).
        """
        if not self._enabled or self._config.sample_rate <= 0.0:
            return

        try:
            # Compression ratio histogram
            if original_bytes > 0:
                ratio = compressed_bytes / original_bytes
                self._histogram_ratio.record(ratio, {ATTR_RULE: rule})

            # Tokens saved counter
            if tokens_saved > 0:
                self._counter_tokens_saved.add(tokens_saved, {ATTR_RULE: rule})

            # Latency histogram
            self._histogram_latency.record(latency_ms, {ATTR_RULE: rule})

        except Exception:
            logger.debug("sutra: compression metric failed", exc_info=True)

    def record_recovery(self, status: str) -> None:
        """Record a recovery operation.

        Args:
            status: Recovery status ("ok", "missing", "expired", "error", etc.)
        """
        if not self._enabled or self._config.sample_rate <= 0.0:
            return

        try:
            self._counter_recovery.add(1, {ATTR_STATUS: status})
        except Exception:
            logger.debug("sutra: recovery metric failed", exc_info=True)

    def record_strategy(self, strategy: str) -> None:
        """Record the compression strategy selected.

        Args:
            strategy: Strategy name (e.g., "head-tail", "json-fields", "parser:ls").
        """
        if not self._enabled or self._config.sample_rate <= 0.0:
            return

        try:
            self._counter_strategy.add(1, {ATTR_STRATEGY: strategy})
        except Exception:
            logger.debug("sutra: strategy metric failed", exc_info=True)

    @contextmanager
    def trace_compression(self, rule: str, original_bytes: int):
        """Context manager to trace a compression operation with timing.

        Args:
            rule: Compression rule being applied.
            original_bytes: Original size in bytes.

        Yields:
            A dict to accumulate results (compressed_bytes, tokens_saved).
        """
        if (
            not self._enabled
            or self._tracer is None
            or self._config.sample_rate <= 0.0
        ):
            yield {}
            return

        start = time.perf_counter()
        result = {}
        try:
            with self._tracer.start_as_current_span(
                "sutra.compress",
                attributes={
                    ATTR_RULE: rule,
                    "sutra.original_bytes": original_bytes,
                },
            ) as span:
                yield result
                if "compressed_bytes" in result:
                    span.set_attribute("sutra.compressed_bytes", result["compressed_bytes"])
                if "tokens_saved" in result:
                    span.set_attribute("sutra.tokens_saved", result["tokens_saved"])
        finally:
            latency_ms = (time.perf_counter() - start) * 1000
            compressed_bytes = result.get("compressed_bytes", original_bytes)
            tokens_saved = result.get("tokens_saved", 0)
            self.record_compression(
                original_bytes=original_bytes,
                compressed_bytes=compressed_bytes,
                rule=rule,
                latency_ms=latency_ms,
                tokens_saved=tokens_saved,
            )

    def shutdown(self, timeout_millis: int = EXPORT_TIMEOUT_MILLIS) -> None:
        """Shutdown telemetry, flushing within a bounded best-effort timeout."""
        meter_provider = self._meter_provider
        tracer_provider = self._tracer_provider
        self._meter_provider = None
        self._tracer_provider = None
        try:
            if meter_provider is not None:
                meter_provider.shutdown(timeout_millis=timeout_millis)
            if tracer_provider is not None:
                tracer_provider.shutdown()
        except Exception:
            logger.debug("sutra: telemetry shutdown failed", exc_info=True)
        finally:
            self._enabled = False
            self._tracer = None


def get_telemetry(cfg: config.Config | None = None) -> CompressionTelemetry | _NoopTelemetry:
    """Get the telemetry instance (singleton).

    Returns a no-op implementation when telemetry is disabled, ensuring
    zero overhead for the hot path.

    Args:
        cfg: Optional config to use for initialization.

    Returns:
        CompressionTelemetry instance or _NoopTelemetry.
    """
    try:
        if cfg is None:
            cfg = config.resolve()

        if not cfg.telemetry_enabled:
            return _NoopTelemetry()

        return CompressionTelemetry.get_instance(cfg)
    except Exception:
        # The telemetry seam must fail open: a broken optional dependency or
        # exporter can never change the compression result.
        logger.debug("sutra: telemetry unavailable; using no-op", exc_info=True)
        return _NoopTelemetry()
