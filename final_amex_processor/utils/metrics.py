"""
utils/metrics.py
─────────────────
Prometheus metrics for the AMEX processor pipeline.
Exposed on :9090/metrics (or whatever METRICS_PORT is set to).

Usage:
    from utils.metrics import METRICS, start_metrics_server
    start_metrics_server(port=9090)

    with METRICS.extraction_duration.time():
        ...

    METRICS.files_processed.inc()
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        Summary,
        start_http_server,
        REGISTRY,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False


# ── No-op stubs when prometheus_client is not installed ───────────────────────
class _Noop:
    """Silent no-op for all Prometheus metric operations."""
    def inc(self, *a, **kw): pass
    def dec(self, *a, **kw): pass
    def set(self, *a, **kw): pass
    def observe(self, *a, **kw): pass
    def labels(self, *a, **kw): return self

    @contextmanager
    def time(self) -> Generator[None, None, None]:  # type: ignore[override]
        yield


@dataclass
class PipelineMetrics:
    """All counters, gauges, and histograms for the pipeline."""

    # Files
    files_processed:  object = field(default_factory=_Noop)
    files_failed:     object = field(default_factory=_Noop)
    files_cached:     object = field(default_factory=_Noop)

    # Transactions
    transactions_extracted: object = field(default_factory=_Noop)
    cardholders_extracted:  object = field(default_factory=_Noop)

    # Latency
    extraction_duration: object = field(default_factory=_Noop)  # API call
    xlsx_write_duration: object = field(default_factory=_Noop)  # XLSX write
    total_duration:      object = field(default_factory=_Noop)  # end-to-end

    # API
    api_retries:  object = field(default_factory=_Noop)
    api_failures: object = field(default_factory=_Noop)


def _build_metrics() -> PipelineMetrics:
    if not _PROMETHEUS_AVAILABLE:
        return PipelineMetrics()

    return PipelineMetrics(
        files_processed=Counter(
            "amex_files_processed_total",
            "Total PDF files successfully processed",
        ),
        files_failed=Counter(
            "amex_files_failed_total",
            "Total PDF files that raised an error",
        ),
        files_cached=Counter(
            "amex_files_cached_total",
            "Total PDF files served from cache (no API call)",
        ),
        transactions_extracted=Counter(
            "amex_transactions_extracted_total",
            "Total transaction rows extracted across all files",
        ),
        cardholders_extracted=Counter(
            "amex_cardholders_extracted_total",
            "Total cardholder blocks extracted across all files",
        ),
        extraction_duration=Histogram(
            "amex_extraction_duration_seconds",
            "Time spent calling the OpenAI API per file",
            buckets=[1, 5, 10, 20, 30, 60, 120],
        ),
        xlsx_write_duration=Histogram(
            "amex_xlsx_write_duration_seconds",
            "Time spent writing the XLSX file",
            buckets=[0.1, 0.5, 1, 2, 5],
        ),
        total_duration=Histogram(
            "amex_pipeline_duration_seconds",
            "End-to-end wall time per file",
            buckets=[1, 5, 15, 30, 60, 120, 300],
        ),
        api_retries=Counter(
            "amex_api_retries_total",
            "Number of OpenAI API retry attempts",
        ),
        api_failures=Counter(
            "amex_api_failures_total",
            "Number of OpenAI API calls that ultimately failed",
        ),
    )


# Singleton — imported everywhere
METRICS: PipelineMetrics = _build_metrics()


def start_metrics_server(port: int = 9090) -> None:
    """Start the Prometheus HTTP server in a daemon thread."""
    if not _PROMETHEUS_AVAILABLE:
        return
    try:
        start_http_server(port)
    except OSError:
        # Port already in use — skip silently (common in tests)
        pass


@contextmanager
def timed(metric_attr: object, extra_labels: dict | None = None) -> Generator[None, None, None]:
    """
    Context manager to record duration on any Histogram/Summary metric.

    Usage:
        with timed(METRICS.extraction_duration):
            do_work()
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        try:
            metric_attr.observe(elapsed)  # type: ignore[attr-defined]
        except AttributeError:
            pass  # _Noop
