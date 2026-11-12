"""Tests for sutra.telemetry module."""

from __future__ import annotations

import os
import sys
from unittest.mock import Mock, patch

import pytest

# Ensure we import the local sutra package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sutra import config
from sutra.telemetry import (
    M_COMPRESSION_LATENCY_MS,
    M_COMPRESSION_RATIO,
    M_COMPRESSION_TOKENS_SAVED,
    M_RECOVERY_COUNT,
    M_STRATEGY_SELECTED,
    CompressionTelemetry,
    _NoopTelemetry,
    get_telemetry,
)


class TestNoopTelemetry:
    """Tests for the no-op telemetry implementation."""

    def test_noop_methods_exist(self):
        """All public methods exist and are callable."""
        noop = _NoopTelemetry()
        noop.record_compression(100, 50, "head-tail", 1.5, 10)
        noop.record_recovery("ok")
        noop.record_strategy("head-tail")
        noop.shutdown()

    def test_noop_returns_none(self):
        """Noop methods return None."""
        noop = _NoopTelemetry()
        assert noop.record_compression(100, 50, "head-tail", 1.5) is None
        assert noop.record_recovery("ok") is None
        assert noop.record_strategy("head-tail") is None
        assert noop.shutdown() is None


class TestGetTelemetry:
    """Tests for the get_telemetry factory function."""

    def test_returns_noop_when_disabled(self):
        """Returns _NoopTelemetry when telemetry is disabled."""
        cfg = config.Config(
            enabled=True,
            max_bytes=1000,
            head_bytes=200,
            tail_bytes=200,
            json_items=3,
            stats_path="/tmp/stats.json",
            stats_max_bytes=10000,
            store_dir="/tmp/store",
            store_enabled=False,
            store_ttl_hours=24,
            store_max_bytes=10000,
            store_max_entry_bytes=1000,
            store_max_entries=100,
            recovery_max_bytes=500,
            recovery_max_matches=5,
            telemetry_enabled=False,
            telemetry_endpoint="http://localhost:4318/v1/metrics",
            telemetry_sample_rate=1.0,
        )
        telemetry = get_telemetry(cfg)
        assert isinstance(telemetry, _NoopTelemetry)

    def test_returns_compression_telemetry_when_enabled(self):
        """Returns CompressionTelemetry when telemetry is enabled."""
        cfg = config.Config(
            enabled=True,
            max_bytes=1000,
            head_bytes=200,
            tail_bytes=200,
            json_items=3,
            stats_path="/tmp/stats.json",
            stats_max_bytes=10000,
            store_dir="/tmp/store",
            store_enabled=False,
            store_ttl_hours=24,
            store_max_bytes=10000,
            store_max_entry_bytes=1000,
            store_max_entries=100,
            recovery_max_bytes=500,
            recovery_max_matches=5,
            telemetry_enabled=True,
            telemetry_endpoint="http://localhost:4318/v1/metrics",
            telemetry_sample_rate=1.0,
        )
        with patch("sutra.telemetry.CompressionTelemetry.get_instance") as mock_get:
            mock_instance = Mock(spec=CompressionTelemetry)
            mock_get.return_value = mock_instance
            telemetry = get_telemetry(cfg)
            assert telemetry is mock_instance


class TestCompressionTelemetry:
    """Tests for CompressionTelemetry class."""

    def test_get_instance_creates_singleton(self):
        """get_instance returns the same instance on subsequent calls."""
        CompressionTelemetry.reset_instance()
        cfg = config.Config(
            enabled=True,
            max_bytes=1000,
            head_bytes=200,
            tail_bytes=200,
            json_items=3,
            stats_path="/tmp/stats.json",
            stats_max_bytes=10000,
            store_dir="/tmp/store",
            store_enabled=False,
            store_ttl_hours=24,
            store_max_bytes=10000,
            store_max_entry_bytes=1000,
            store_max_entries=100,
            recovery_max_bytes=500,
            recovery_max_matches=5,
            telemetry_enabled=False,
            telemetry_endpoint="http://localhost:4318/v1/metrics",
            telemetry_sample_rate=1.0,
        )
        t1 = CompressionTelemetry.get_instance(cfg)
        t2 = CompressionTelemetry.get_instance(cfg)
        assert t1 is t2
        CompressionTelemetry.reset_instance()

    def test_reset_instance(self):
        """reset_instance clears the singleton."""
        CompressionTelemetry.reset_instance()
        cfg = config.Config(
            enabled=True,
            max_bytes=1000,
            head_bytes=200,
            tail_bytes=200,
            json_items=3,
            stats_path="/tmp/stats.json",
            stats_max_bytes=10000,
            store_dir="/tmp/store",
            store_enabled=False,
            store_ttl_hours=24,
            store_max_bytes=10000,
            store_max_entry_bytes=1000,
            store_max_entries=100,
            recovery_max_bytes=500,
            recovery_max_matches=5,
            telemetry_enabled=False,
            telemetry_endpoint="http://localhost:4318/v1/metrics",
            telemetry_sample_rate=1.0,
        )
        t1 = CompressionTelemetry.get_instance(cfg)
        CompressionTelemetry.reset_instance()
        t2 = CompressionTelemetry.get_instance(cfg)
        assert t1 is not t2
        CompressionTelemetry.reset_instance()

    def test_record_compression_noop_when_disabled(self):
        """record_compression is no-op when telemetry is disabled."""
        cfg = config.Config(
            enabled=True,
            max_bytes=1000,
            head_bytes=200,
            tail_bytes=200,
            json_items=3,
            stats_path="/tmp/stats.json",
            stats_max_bytes=10000,
            store_dir="/tmp/store",
            store_enabled=False,
            store_ttl_hours=24,
            store_max_bytes=10000,
            store_max_entry_bytes=1000,
            store_max_entries=100,
            recovery_max_bytes=500,
            recovery_max_matches=5,
            telemetry_enabled=False,
            telemetry_endpoint="http://localhost:4318/v1/metrics",
            telemetry_sample_rate=1.0,
        )
        telemetry = CompressionTelemetry(cfg)
        # Should not raise
        telemetry.record_compression(1000, 500, "head-tail", 1.5, 100)
        telemetry.record_recovery("ok")
        telemetry.record_strategy("head-tail")

    def test_shutdown_noop_when_disabled(self):
        """shutdown is no-op when telemetry is disabled."""
        cfg = config.Config(
            enabled=True,
            max_bytes=1000,
            head_bytes=200,
            tail_bytes=200,
            json_items=3,
            stats_path="/tmp/stats.json",
            stats_max_bytes=10000,
            store_dir="/tmp/store",
            store_enabled=False,
            store_ttl_hours=24,
            store_max_bytes=10000,
            store_max_entry_bytes=1000,
            store_max_entries=100,
            recovery_max_bytes=500,
            recovery_max_matches=5,
            telemetry_enabled=False,
            telemetry_endpoint="http://localhost:4318/v1/metrics",
            telemetry_sample_rate=1.0,
        )
        telemetry = CompressionTelemetry(cfg)
        telemetry.shutdown()  # Should not raise

    def test_telemetry_config_dataclass(self):
        """TelemetryConfig dataclass works correctly."""
        from sutra.telemetry import TelemetryConfig
        tc = TelemetryConfig(enabled=True, endpoint="http://test:4318", sample_rate=0.5)
        assert tc.enabled is True
        assert tc.endpoint == "http://test:4318"
        assert tc.sample_rate == 0.5


class TestMetricsConstants:
    """Tests for metric name constants."""

    def test_metric_names(self):
        """Metric name constants have expected values."""
        from sutra.telemetry import (
            M_COMPRESSION_RATIO,
            M_COMPRESSION_TOKENS_SAVED,
            M_COMPRESSION_LATENCY_MS,
            M_RECOVERY_COUNT,
            M_STRATEGY_SELECTED,
        )
        assert M_COMPRESSION_RATIO == "sutra.compression.ratio"
        assert M_COMPRESSION_TOKENS_SAVED == "sutra.compression.tokens_saved"
        assert M_COMPRESSION_LATENCY_MS == "sutra.compression.latency_ms"
        assert M_RECOVERY_COUNT == "sutra.recovery.count"
        assert M_STRATEGY_SELECTED == "sutra.strategy.selected"


class TestTraceCompression:
    """Tests for the trace_compression context manager."""

    def test_trace_compression_noop_when_disabled(self):
        """trace_compression yields empty dict when disabled."""
        cfg = config.Config(
            enabled=True,
            max_bytes=1000,
            head_bytes=200,
            tail_bytes=200,
            json_items=3,
            stats_path="/tmp/stats.json",
            stats_max_bytes=10000,
            store_dir="/tmp/store",
            store_enabled=False,
            store_ttl_hours=24,
            store_max_bytes=10000,
            store_max_entry_bytes=1000,
            store_max_entries=100,
            recovery_max_bytes=500,
            recovery_max_matches=5,
            telemetry_enabled=False,
            telemetry_endpoint="http://localhost:4318/v1/metrics",
            telemetry_sample_rate=1.0,
        )
        telemetry = CompressionTelemetry(cfg)
        with telemetry.trace_compression("head-tail", 1000) as result:
            assert result == {}
            result["compressed_bytes"] = 500
            result["tokens_saved"] = 50


class _FakeInstrument:
    """Records every record()/add() call made against an OTel instrument."""

    def __init__(self, name: str, kind: str) -> None:
        self.name = name
        self.kind = kind
        self.calls = []

    def record(self, value, attributes=None) -> None:
        self.calls.append(("record", float(value), dict(attributes or {})))

    def add(self, value, attributes=None) -> None:
        self.calls.append(("add", float(value), dict(attributes or {})))


class _FakeMeter:
    """Minimal stand-in for opentelemetry.metrics.Meter (no SDK required)."""

    def __init__(self) -> None:
        self.instruments = {}

    def create_histogram(self, name, unit=None, description=None):
        inst = _FakeInstrument(name, "histogram")
        self.instruments[name] = inst
        return inst

    def create_counter(self, name, unit=None, description=None):
        inst = _FakeInstrument(name, "counter")
        self.instruments[name] = inst
        return inst


class TestMetricEmission:
    """Metric emission tests using a fake OTel meter."""

    def _enabled_cfg(self, **overrides):
        data = {
            "enabled": True,
            "max_bytes": 1000,
            "head_bytes": 200,
            "tail_bytes": 200,
            "json_items": 3,
            "stats_path": "/tmp/stats.json",
            "stats_max_bytes": 10000,
            "store_dir": "/tmp/store",
            "store_enabled": False,
            "store_ttl_hours": 24,
            "store_max_bytes": 10000,
            "store_max_entry_bytes": 1000,
            "store_max_entries": 100,
            "recovery_max_bytes": 500,
            "recovery_max_matches": 5,
            "telemetry_enabled": True,
            "telemetry_endpoint": "http://localhost:4318/v1/metrics",
            "telemetry_sample_rate": 1.0,
        }
        data.update(overrides)
        return config.Config(**data)

    def test_emits_all_five_metrics(self):
        meter = _FakeMeter()
        tel = CompressionTelemetry(self._enabled_cfg(), meter=meter)

        tel.record_compression(2000, 500, "head-tail", 12.5, tokens_saved=300)
        tel.record_recovery("ok")
        tel.record_strategy("head-tail")

        assert M_COMPRESSION_RATIO in meter.instruments
        assert M_COMPRESSION_TOKENS_SAVED in meter.instruments
        assert M_COMPRESSION_LATENCY_MS in meter.instruments
        assert M_RECOVERY_COUNT in meter.instruments
        assert M_STRATEGY_SELECTED in meter.instruments

        ratio = meter.instruments[M_COMPRESSION_RATIO]
        assert ("record", 0.25, {"sutra.compression.rule": "head-tail"}) in ratio.calls

        tokens = meter.instruments[M_COMPRESSION_TOKENS_SAVED]
        assert ("add", 300.0, {"sutra.compression.rule": "head-tail"}) in tokens.calls

        latency = meter.instruments[M_COMPRESSION_LATENCY_MS]
        assert ("record", 12.5, {"sutra.compression.rule": "head-tail"}) in latency.calls

        recovery = meter.instruments[M_RECOVERY_COUNT]
        assert ("add", 1.0, {"sutra.recovery.status": "ok"}) in recovery.calls

        strategy = meter.instruments[M_STRATEGY_SELECTED]
        assert ("add", 1.0, {"sutra.strategy.name": "head-tail"}) in strategy.calls

    def test_zero_bytes_produces_no_ratio_metric(self):
        meter = _FakeMeter()
        tel = CompressionTelemetry(self._enabled_cfg(), meter=meter)
        tel.record_compression(0, 0, "head-tail", 1.0, tokens_saved=5)
        assert meter.instruments[M_COMPRESSION_RATIO].calls == []
        assert meter.instruments[M_COMPRESSION_TOKENS_SAVED].calls == [
            ("add", 5.0, {"sutra.compression.rule": "head-tail"})
        ]

    def test_sample_rate_zero_emits_nothing(self):
        meter = _FakeMeter()
        tel = CompressionTelemetry(
            self._enabled_cfg(telemetry_sample_rate=0.0), meter=meter
        )
        tel.record_compression(2000, 500, "head-tail", 12.5, tokens_saved=300)
        tel.record_recovery("ok")
        tel.record_strategy("head-tail")
        assert all(len(inst.calls) == 0 for inst in meter.instruments.values())

    def test_sample_rate_one_emits_everything(self):
        meter = _FakeMeter()
        tel = CompressionTelemetry(self._enabled_cfg(), meter=meter)
        tel.record_compression(2000, 500, "head-tail", 12.5)
        tel.record_recovery("ok")
        tel.record_strategy("head-tail")
        assert len(meter.instruments[M_COMPRESSION_RATIO].calls) == 1
        assert len(meter.instruments[M_RECOVERY_COUNT].calls) == 1
        assert len(meter.instruments[M_STRATEGY_SELECTED].calls) == 1

    def test_disabled_creates_no_instruments(self):
        meter = _FakeMeter()
        tel = CompressionTelemetry(
            config.Config(
                enabled=True,
                max_bytes=1000,
                head_bytes=200,
                tail_bytes=200,
                json_items=3,
                stats_path="/tmp/stats.json",
                stats_max_bytes=10000,
                store_dir="/tmp/store",
                store_enabled=False,
                store_ttl_hours=24,
                store_max_bytes=10000,
                store_max_entry_bytes=1000,
                store_max_entries=100,
                recovery_max_bytes=500,
                recovery_max_matches=5,
                telemetry_enabled=False,
            ),
            meter=meter,
        )
        tel.record_compression(2000, 500, "head-tail", 12.5)
        assert meter.instruments == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])