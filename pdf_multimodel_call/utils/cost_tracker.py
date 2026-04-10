"""
utils/cost_tracker.py
Per-call cost tracking, cumulative totals, and JSON cost reports.
Thread-safe via a simple lock.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CallRecord:
    call_id: str
    timestamp: str
    provider: str
    model: str
    file_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    duration_seconds: float
    status: str = "success"           # success | error
    error_message: Optional[str] = None


@dataclass
class RunReport:
    run_id: str = field(default_factory=lambda: f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    provider: str = ""
    model: str = ""
    total_files: int = 0
    successful_files: int = 0
    failed_files: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_duration_seconds: float = 0.0
    calls: list[CallRecord] = field(default_factory=list)


class CostTracker:
    """
    Thread-safe tracker for LLM call costs across a run.

    Usage:
        tracker = CostTracker(pricing={"input": 0.075, "output": 0.30},
                              provider="gemini", model="gemini-2.0-flash-lite",
                              cost_report_folder="cost_reports")
        ...
        tracker.record(file_name="report.pdf",
                       input_tokens=4000, output_tokens=1200,
                       duration_seconds=3.4)
        ...
        tracker.save_report()
    """

    def __init__(
        self,
        pricing: dict[str, float],
        provider: str,
        model: str,
        cost_report_folder: str = "cost_reports",
    ) -> None:
        self._pricing = pricing          # {"input": X, "output": Y} per 1M tokens
        self._report = RunReport(provider=provider, model=model)
        self._cost_report_folder = Path(cost_report_folder)
        self._lock = threading.Lock()

    # ── public API ─────────────────────────────────────────────────────────

    def record(
        self,
        file_name: str,
        input_tokens: int,
        output_tokens: int,
        duration_seconds: float,
        status: str = "success",
        error_message: Optional[str] = None,
    ) -> CallRecord:
        cost = self._compute_cost(input_tokens, output_tokens)
        record = CallRecord(
            call_id=str(uuid.uuid4())[:8],
            timestamp=datetime.now(timezone.utc).isoformat(),
            provider=self._report.provider,
            model=self._report.model,
            file_name=file_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=cost,
            duration_seconds=round(duration_seconds, 2),
            status=status,
            error_message=error_message,
        )

        with self._lock:
            self._report.calls.append(record)
            self._report.total_input_tokens += input_tokens
            self._report.total_output_tokens += output_tokens
            self._report.total_tokens += input_tokens + output_tokens
            self._report.total_cost_usd += cost
            self._report.total_duration_seconds += duration_seconds
            self._report.total_files += 1
            if status == "success":
                self._report.successful_files += 1
            else:
                self._report.failed_files += 1

        logger.info(
            "llm_call_recorded",
            file=file_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            duration_s=round(duration_seconds, 2),
            status=status,
        )
        return record

    def summary(self) -> dict:
        with self._lock:
            return {
                "total_files": self._report.total_files,
                "successful": self._report.successful_files,
                "failed": self._report.failed_files,
                "total_tokens": self._report.total_tokens,
                "total_cost_usd": round(self._report.total_cost_usd, 6),
            }

    def save_report(self) -> Path:
        self._cost_report_folder.mkdir(parents=True, exist_ok=True)
        self._report.finished_at = datetime.now(timezone.utc).isoformat()

        out_path = self._cost_report_folder / f"{self._report.run_id}.json"
        payload = asdict(self._report)
        payload["total_cost_usd"] = round(payload["total_cost_usd"], 6)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

        logger.info(
            "cost_report_saved",
            path=str(out_path),
            total_cost_usd=round(self._report.total_cost_usd, 6),
            total_tokens=self._report.total_tokens,
        )
        return out_path

    # ── internals ──────────────────────────────────────────────────────────

    def _compute_cost(self, input_tokens: int, output_tokens: int) -> float:
        input_cost = (input_tokens / 1_000_000) * self._pricing.get("input", 0)
        output_cost = (output_tokens / 1_000_000) * self._pricing.get("output", 0)
        return round(input_cost + output_cost, 8)
