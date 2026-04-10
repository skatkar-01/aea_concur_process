"""
utils/metrics.py
─────────────────
Two complementary metric systems in one module:

1. LLMMetrics (from uploaded extractor.py)
   ─────────────────────────────────────────
   Dataclass capturing per-call token counts, cost, and latency.
   Appended as JSON lines to logs/metrics.jsonl for offline analysis.
   build_metrics() / record_metrics() / MetricsTimer match the
   uploaded code's API exactly so extractor.py imports work unchanged.

2. Prometheus metrics (existing pipeline infrastructure)
   ──────────────────────────────────────────────────────
   Counters and histograms exposed on :9090/metrics.
   Used by watcher.py, runner.py, Azure Function triggers.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

# ═══════════════════════════════════════════════════════════════
# 1. LLM call metrics  (matches uploaded extractor.py exactly)
# ═══════════════════════════════════════════════════════════════

METRICS_FILE = Path("logs/metrics.jsonl")


@dataclass
class LLMMetrics:
    timestamp:       str
    pdf_file:        str
    model:           str
    input_tokens:    int
    output_tokens:   int
    total_tokens:    int
    cost_usd:        float
    latency_seconds: float
    status:          str    # "success" | "cache_hit" | "error" | "parse_error"
    error_message:   str    # "" if success


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    cost_per_1k_input: float,
    cost_per_1k_output: float,
) -> float:
    return (
        input_tokens  / 1000 * cost_per_1k_input +
        output_tokens / 1000 * cost_per_1k_output
    )


def build_metrics(
    pdf_file: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_per_1k_input: float,
    cost_per_1k_output: float,
    latency: float,
    status: str = "success",
    error_message: str = "",
) -> LLMMetrics:
    return LLMMetrics(
        timestamp=datetime.now(timezone.utc).isoformat(),
        pdf_file=pdf_file,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cost_usd=round(
            compute_cost(input_tokens, output_tokens,
                         cost_per_1k_input, cost_per_1k_output), 6
        ),
        latency_seconds=latency,
        status=status,
        error_message=error_message,
    )


def record_metrics(metrics: LLMMetrics) -> None:
    """Append one LLMMetrics record as a JSON line to logs/metrics.jsonl."""
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with METRICS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(metrics)) + "\n")


class MetricsTimer:
    """Context manager measuring wall-clock elapsed time."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self) -> "MetricsTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_) -> None:
        self.elapsed = round(time.monotonic() - self._start, 3)


# ═══════════════════════════════════════════════════════════════
# 2. Prometheus pipeline metrics (existing infrastructure)
# ═══════════════════════════════════════════════════════════════

try:
    from prometheus_client import Counter, Histogram, start_http_server
    _PROM = True
except ImportError:
    _PROM = False


class _Noop:
    def inc(self, *a, **kw): pass
    def observe(self, *a, **kw): pass
    def labels(self, *a, **kw): return self

    @contextmanager
    def time(self) -> Generator[None, None, None]:
        yield


@dataclass
class PipelineMetrics:
    files_processed:        object
    files_failed:           object
    files_cached:           object
    transactions_extracted: object
    cardholders_extracted:  object
    extraction_duration:    object
    xlsx_write_duration:    object
    total_duration:         object
    api_retries:            object
    api_failures:           object


def _build_pipeline_metrics() -> PipelineMetrics:
    if not _PROM:
        return PipelineMetrics(*[_Noop() for _ in range(10)])
    return PipelineMetrics(
        files_processed=Counter("amex_files_processed_total", "Files successfully processed"),
        files_failed=Counter("amex_files_failed_total", "Files that raised an error"),
        files_cached=Counter("amex_files_cached_total", "Files served from cache"),
        transactions_extracted=Counter("amex_transactions_extracted_total", "Total transaction rows"),
        cardholders_extracted=Counter("amex_cardholders_extracted_total", "Total cardholder blocks"),
        extraction_duration=Histogram("amex_extraction_duration_seconds", "API call latency",
                                       buckets=[1, 5, 10, 20, 30, 60, 120]),
        xlsx_write_duration=Histogram("amex_xlsx_write_duration_seconds", "XLSX write latency",
                                       buckets=[0.1, 0.5, 1, 2, 5]),
        total_duration=Histogram("amex_pipeline_duration_seconds", "End-to-end latency",
                                  buckets=[1, 5, 15, 30, 60, 120, 300]),
        api_retries=Counter("amex_api_retries_total", "OpenAI retry attempts"),
        api_failures=Counter("amex_api_failures_total", "OpenAI calls that ultimately failed"),
    )


METRICS: PipelineMetrics = _build_pipeline_metrics()


def start_metrics_server(port: int = 9090) -> None:
    if not _PROM:
        return
    try:
        start_http_server(port)
    except OSError:
        pass


@contextmanager
def timed(metric_attr: object) -> Generator[None, None, None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        try:
            metric_attr.observe(elapsed)   # type: ignore[attr-defined]
        except AttributeError:
            pass
