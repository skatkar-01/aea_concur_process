"""
metrics.py
Captures and persists LLM call metrics: tokens, cost, latency.
Each record is appended as a JSON line to logs/metrics.jsonl.
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

METRICS_FILE = Path("logs/metrics.jsonl")


@dataclass
class LLMMetrics:
    timestamp: str
    pdf_file: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    latency_seconds: float
    status: str          # "success" | "error"
    error_message: str   # "" if success


def compute_cost(input_tokens: int, output_tokens: int,
                 cost_per_1k_input: float, cost_per_1k_output: float) -> float:
    return (input_tokens / 1000 * cost_per_1k_input +
            output_tokens / 1000 * cost_per_1k_output)


def record_metrics(metrics: LLMMetrics) -> None:
    """Append one metrics record to the JSONL file."""
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with METRICS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(metrics)) + "\n")
    logger.debug("Metrics recorded: %s", asdict(metrics))


class MetricsTimer:
    """Context manager that measures wall-clock elapsed time."""

    def __init__(self):
        self._start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *_):
        self.elapsed = round(time.monotonic() - self._start, 3)


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
        cost_usd=round(compute_cost(input_tokens, output_tokens,
                                    cost_per_1k_input, cost_per_1k_output), 6),
        latency_seconds=latency,
        status=status,
        error_message=error_message,
    )
