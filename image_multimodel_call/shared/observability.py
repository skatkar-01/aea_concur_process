"""
shared/observability.py
Tracks every LLM call across the pipeline.

Metrics per call:
  - model, context label, source_file
  - input_tokens, output_tokens, total_tokens
  - estimated cost (USD)
  - latency_ms
  - success / failure

Aggregates:
  - per source file  (one AMEX PDF, one Concur PDF)
  - per stage        (amex_stage, concur_stage, matching_stage)
  - per run total

Usage:
    collector = MetricsCollector()
    collector.record(...)            # called by AzureClient after each API call
    collector.set_source_file(...)   # called by each stage before processing a file
    summary = collector.summary()   # called at end of run
    collector.write(path)           # writes metrics JSON
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from shared.logger import get_logger

log = get_logger(__name__)

# ── Model pricing (USD per 1M tokens) ─────────────────────────────────────────
# Update these if your deployment uses a different model or pricing tier.
_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o":       {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":  {"input": 0.15,  "output": 0.60},
    "gpt-4-turbo":  {"input": 10.00, "output": 30.00},
    "default":      {"input": 2.50,  "output": 10.00},   # fallback
}


def _cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for one LLM call."""
    pricing = _PRICING.get(model, _PRICING["default"])
    return round(
        (input_tokens  / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"],
        6,
    )


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class LLMCallRecord:
    """One LLM API call."""
    timestamp:     str
    source_file:   str        # which PDF triggered this call
    stage:         str        # e.g. "amex_stage", "matching_stage"
    context:       str        # e.g. "classify page 4", "match txn[0]"
    model:         str
    input_tokens:  int
    output_tokens: int
    total_tokens:  int
    cost_usd:      float
    latency_ms:    int
    success:       bool
    error:         Optional[str] = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class FileMetrics:
    """Aggregated metrics for one source PDF."""
    source_file:      str
    llm_calls:        int   = 0
    input_tokens:     int   = 0
    output_tokens:    int   = 0
    total_tokens:     int   = 0
    cost_usd:         float = 0.0
    total_latency_ms: int   = 0
    failed_calls:     int   = 0

    @property
    def avg_latency_ms(self) -> int:
        return self.total_latency_ms // self.llm_calls if self.llm_calls else 0

    def to_dict(self) -> dict:
        return {
            "source_file":    self.source_file,
            "llm_calls":      self.llm_calls,
            "input_tokens":   self.input_tokens,
            "output_tokens":  self.output_tokens,
            "total_tokens":   self.total_tokens,
            "cost_usd":       round(self.cost_usd, 6),
            "avg_latency_ms": self.avg_latency_ms,
            "failed_calls":   self.failed_calls,
        }


@dataclass
class StageMetrics:
    """Aggregated metrics for one pipeline stage."""
    stage:         str
    llm_calls:     int   = 0
    input_tokens:  int   = 0
    output_tokens: int   = 0
    total_tokens:  int   = 0
    cost_usd:      float = 0.0
    failed_calls:  int   = 0

    def to_dict(self) -> dict:
        return {
            "stage":         self.stage,
            "llm_calls":     self.llm_calls,
            "input_tokens":  self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens":  self.total_tokens,
            "cost_usd":      round(self.cost_usd, 6),
            "failed_calls":  self.failed_calls,
        }


# ── Collector ─────────────────────────────────────────────────────────────────

class MetricsCollector:
    """
    Collector for LLM call metrics.
    Instantiate once in PipelineRun and inject into AzureClient.
    """

    def __init__(self):
        self._calls:         list[LLMCallRecord] = []
        self._current_file:  str                 = ""
        self._current_stage: str                 = ""
        self._run_start:     float               = time.monotonic()

    # ── Context setters (called by pipeline stages) ───────────────────────────

    def set_source_file(self, source_file: str) -> None:
        """Call at the start of processing each PDF."""
        self._current_file = source_file

    def set_stage(self, stage: str) -> None:
        """Call at the start of each pipeline stage."""
        self._current_stage = stage
        log.debug("Metrics stage: %s", stage)

    # ── Recording (called by AzureClient) ─────────────────────────────────────

    def record(
        self,
        context:       str,
        model:         str,
        input_tokens:  int,
        output_tokens: int,
        latency_ms:    int,
        success:       bool,
        error:         Optional[str] = None,
    ) -> None:
        total = input_tokens + output_tokens
        cost  = _cost(model, input_tokens, output_tokens) if success else 0.0

        rec = LLMCallRecord(
            timestamp     = datetime.utcnow().isoformat() + "Z",
            source_file   = self._current_file,
            stage         = self._current_stage,
            context       = context,
            model         = model,
            input_tokens  = input_tokens,
            output_tokens = output_tokens,
            total_tokens  = total,
            cost_usd      = cost,
            latency_ms    = latency_ms,
            success       = success,
            error         = error,
        )
        self._calls.append(rec)

        if success:
            log.debug(
                "LLM %-40s | %5d+%5d=%5d tok | $%.5f | %dms",
                context[:40], input_tokens, output_tokens, total, cost, latency_ms,
            )
        else:
            log.warning("LLM FAILED %-35s | %s", context[:35], error)

    # ── Aggregation ───────────────────────────────────────────────────────────

    def per_file(self) -> dict[str, FileMetrics]:
        result: dict[str, FileMetrics] = {}
        for call in self._calls:
            key = call.source_file or "(unknown)"
            if key not in result:
                result[key] = FileMetrics(source_file=key)
            fm = result[key]
            fm.llm_calls         += 1
            fm.input_tokens      += call.input_tokens
            fm.output_tokens     += call.output_tokens
            fm.total_tokens      += call.total_tokens
            fm.cost_usd          += call.cost_usd
            fm.total_latency_ms  += call.latency_ms
            if not call.success:
                fm.failed_calls  += 1
        return result

    def per_stage(self) -> dict[str, StageMetrics]:
        result: dict[str, StageMetrics] = {}
        for call in self._calls:
            key = call.stage or "(unknown)"
            if key not in result:
                result[key] = StageMetrics(stage=key)
            sm = result[key]
            sm.llm_calls      += 1
            sm.input_tokens   += call.input_tokens
            sm.output_tokens  += call.output_tokens
            sm.total_tokens   += call.total_tokens
            sm.cost_usd       += call.cost_usd
            if not call.success:
                sm.failed_calls += 1
        return result

    def totals(self) -> dict:
        calls         = self._calls
        total_input   = sum(c.input_tokens  for c in calls)
        total_output  = sum(c.output_tokens for c in calls)
        total_tokens  = sum(c.total_tokens  for c in calls)
        total_cost    = sum(c.cost_usd      for c in calls)
        total_latency = sum(c.latency_ms    for c in calls)
        failed        = sum(1 for c in calls if not c.success)
        n             = len(calls)
        return {
            "total_llm_calls":     n,
            "total_input_tokens":  total_input,
            "total_output_tokens": total_output,
            "total_tokens":        total_tokens,
            "total_cost_usd":      round(total_cost, 6),
            "avg_latency_ms":      total_latency // n if n else 0,
            "failed_calls":        failed,
            "success_rate_pct":    round((n - failed) / n * 100, 1) if n else 0.0,
            "run_elapsed_ms":      int((time.monotonic() - self._run_start) * 1000),
        }

    def summary(self) -> dict:
        return {
            "totals":    self.totals(),
            "per_file":  {k: v.to_dict() for k, v in self.per_file().items()},
            "per_stage": {k: v.to_dict() for k, v in self.per_stage().items()},
            "calls":     [c.to_dict() for c in self._calls],
        }

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, metrics_dir: Path) -> Path:
        """Write metrics JSON to outputs/metrics/metrics_<ts>.json."""
        metrics_dir = Path(metrics_dir)
        metrics_dir.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = metrics_dir / f"metrics_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.summary(), f, indent=2)
        log.info("Metrics written: %s", path.name)
        return path

    # ── Console table ─────────────────────────────────────────────────────────

    def print_summary(self) -> None:
        """Print a readable metrics table to the log at INFO level."""
        t   = self.totals()
        div = "─" * 70

        log.info("")
        log.info("┌%s┐", "─" * 68)
        log.info("│  LLM OBSERVABILITY SUMMARY%-41s│", "")
        log.info("├%s┤", "─" * 68)
        log.info("│  %-30s %10s %10s %10s │", "", "INPUT", "OUTPUT", "TOTAL")
        log.info(
            "│  %-30s %10s %10s %10s │",
            "TOKENS",
            f"{t['total_input_tokens']:,}",
            f"{t['total_output_tokens']:,}",
            f"{t['total_tokens']:,}",
        )
        log.info("│  %-30s %33s │", "ESTIMATED COST", f"${t['total_cost_usd']:.4f}")
        log.info(
            "│  %-30s %33s │",
            "LLM CALLS",
            f"{t['total_llm_calls']}  ({t['failed_calls']} failed, "
            f"{t['success_rate_pct']}% success)",
        )
        log.info("│  %-30s %33s │", "AVG LATENCY / CALL", f"{t['avg_latency_ms']}ms")
        log.info("├%s┤", "─" * 68)

        log.info("│  %-20s %8s %8s %8s %10s %8s │",
                 "FILE", "CALLS", "IN TOK", "OUT TOK", "COST", "AVG MS")
        log.info("│  %s │", div[:66])
        for fm in sorted(self.per_file().values(), key=lambda x: -x.cost_usd):
            log.info(
                "│  %-20s %8d %8d %8d %10s %8d │",
                fm.source_file[:20], fm.llm_calls,
                fm.input_tokens, fm.output_tokens,
                f"${fm.cost_usd:.4f}", fm.avg_latency_ms,
            )

        log.info("├%s┤", "─" * 68)
        log.info("│  %-20s %8s %8s %8s %10s        │",
                 "STAGE", "CALLS", "IN TOK", "OUT TOK", "COST")
        log.info("│  %s │", div[:66])
        for sm in sorted(self.per_stage().values(), key=lambda x: -x.cost_usd):
            log.info(
                "│  %-20s %8d %8d %8d %10s        │",
                sm.stage[:20], sm.llm_calls,
                sm.input_tokens, sm.output_tokens,
                f"${sm.cost_usd:.4f}",
            )

        log.info("└%s┘", "─" * 68)
        log.info("")