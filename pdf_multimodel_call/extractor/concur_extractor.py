"""
extractor/concur_extractor.py
Orchestrates a single file extraction:
  1. Read file → FilePayload
  2. Call LLM → raw JSON string
  3. Parse & validate JSON → ExtractedReport
  4. Record cost
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from clients.base_client import BaseLLMClient
from prompts.concur_prompt import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from utils.cost_tracker import CostTracker
from utils.file_utils import FilePayload, read_file
from utils.logger import get_logger

logger = get_logger(__name__)


# ── output schema ──────────────────────────────────────────────────────────

@dataclass
class ExtractedReport:
    source_file: str
    transactions: list[dict] = field(default_factory=list)
    employee_report: dict = field(default_factory=dict)
    receipts: list[dict] = field(default_factory=list)
    reconciliation: list[dict] = field(default_factory=list)
    raw_json: str = ""
    parse_error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.parse_error is None and bool(self.employee_report)

    @property
    def employee_name(self) -> str:
        return (
            self.employee_report.get("employee_name", "unknown")
            .lower()
            .replace(" ", "_")
        )

    @property
    def report_date(self) -> str:
        raw = self.employee_report.get("report_date", "unknown_date")
        # Normalise common date separators
        return str(raw).replace("/", "-").replace(" ", "_")

    @property
    def output_prefix(self) -> str:
        """E.g.  john_doe_2024-03-15"""
        return f"{self.employee_name}_{self.report_date}"


# ── extractor ──────────────────────────────────────────────────────────────

class ConcurExtractor:
    """
    Extracts structured data from a single SAP Concur report file.

    Designed to be instantiated once and called many times (one call per file).
    """

    def __init__(self, client: BaseLLMClient, cost_tracker: CostTracker) -> None:
        self._client = client
        self._cost_tracker = cost_tracker

    def extract(self, file_path: Path) -> ExtractedReport:
        logger.info("extraction_start", file=file_path.name)

        # 1. Read file
        try:
            payload: FilePayload = read_file(file_path)
        except Exception as exc:
            logger.error("file_read_error", file=file_path.name, error=str(exc))
            return ExtractedReport(
                source_file=file_path.name,
                parse_error=f"File read failed: {exc}",
            )

        # 2. Build user prompt
        # Always use the "See attached document" marker — the payload (PDF)
        # is ALWAYS passed to the client as a file attachment regardless of
        # whether PyMuPDF extracted text from it.  It is the client's job to
        # decide how to send the file (native block, base64, etc.).
        # We never inline the extracted text here — that was the original bug.
        user_prompt = USER_PROMPT_TEMPLATE.format(document_text="[See attached PDF]")

        # 3. Call LLM (timed)
        start = time.perf_counter()
        status = "success"
        error_msg = None
        llm_response = None

        try:
            llm_response = self._client.call(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                payload=payload,   # ← always pass the FilePayload
            )
        except Exception as exc:
            status = "error"
            error_msg = str(exc)
            logger.error("llm_call_failed", file=file_path.name, error=error_msg)

        duration = time.perf_counter() - start

        # 4. Record cost (even on error, use 0 tokens)
        self._cost_tracker.record(
            file_name=file_path.name,
            input_tokens=llm_response.input_tokens if llm_response else 0,
            output_tokens=llm_response.output_tokens if llm_response else 0,
            duration_seconds=duration,
            status=status,
            error_message=error_msg,
        )

        if llm_response is None:
            return ExtractedReport(source_file=file_path.name, parse_error=error_msg)

        # 5. Parse JSON
        return self._parse_response(file_path.name, llm_response.text)

    # ── private ────────────────────────────────────────────────────────────

    def _parse_response(self, file_name: str, raw: str) -> ExtractedReport:
        """Parse LLM JSON output into an ExtractedReport."""
        cleaned = raw.strip()

        # Strip accidental markdown fences
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[-1].lstrip("json").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")].strip()

        try:
            data: dict = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("json_parse_failed", file=file_name, error=str(exc))
            return ExtractedReport(
                source_file=file_name,
                raw_json=raw,
                parse_error=f"JSON decode error: {exc}",
            )

        report = ExtractedReport(
            source_file=file_name,
            transactions=data.get("transactions") or [],
            employee_report=data.get("employee_report") or {},
            receipts=data.get("receipts") or [],
            reconciliation=data.get("reconciliation") or [],
            raw_json=raw,
        )

        logger.info(
            "extraction_complete",
            file=file_name,
            transactions=len(report.transactions),
            receipts=len(report.receipts),
            reconciliation=len(report.reconciliation),
            employee=report.employee_name,
            report_date=report.report_date,
        )
        return report