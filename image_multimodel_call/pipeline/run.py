"""
pipeline/run.py
Changes:
  - _clear_outputs() before each run
  - MetricsCollector created and injected into AzureClient + all stages
  - set_stage() called before each stage
  - metrics.print_summary() + metrics.write() at end
"""
from __future__ import annotations
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config.settings import get_settings
from models.tracker import RunSummary, TrackerRecord
from pipeline.amex_stage import AmexStage
from pipeline.concur_stage import ConcurStage
from pipeline.matching_stage import MatchingStage
from pipeline.output_stage import OutputStage
from shared.azure_client import AzureClient
from shared.exceptions import AEAConcurError, ConfigurationError
from shared.logger import get_logger, setup_logging
from shared.observability import MetricsCollector
from storage.json_store import JSONStore
from storage.report_writer import ReportWriter
from storage.tracker_store import TrackerStore

log = get_logger(__name__)

# Cleared before every run. logs/ is intentionally excluded.
_CLEAR_ON_RUN = ["tracker", "reports", "json", "metrics"]


class PipelineRun:
    def __init__(self, period: str = ""):
        self.period   = period
        self.run_id   = str(uuid.uuid4())[:8].upper()
        self.settings = get_settings()
        self._setup_logging()
        self.settings.validate()

        self._metrics = MetricsCollector()
        self._llm     = AzureClient(self.settings, metrics=self._metrics)
        out           = self.settings.output_folder

        self._tracker = TrackerStore(out / "tracker")
        self._json    = JSONStore(out / "json")
        self._reports = ReportWriter(out / "reports")

        self._amex_stage     = AmexStage(self._llm, self._json, self._metrics)
        self._concur_stage   = ConcurStage(self._llm, self._json, self._metrics)
        self._matching_stage = MatchingStage(self._llm, self._metrics)
        self._output_stage   = OutputStage(self._tracker, self._reports, self._json)

    def execute(self) -> RunSummary:
        started_at = datetime.now(timezone.utc).isoformat()
        t0         = time.monotonic()

        log.info("=" * 70)
        log.info("AEA Concur Pipeline — RUN %s | period=%s", self.run_id, self.period or "all")
        log.info("  AMEX   : %s", self.settings.amex_input_folder)
        log.info("  Concur : %s", self.settings.concur_input_folder)
        log.info("  Output : %s", self.settings.output_folder)
        log.info("=" * 70)

        self._clear_outputs()

        summary = RunSummary(run_id=self.run_id, started_at=started_at, period=self.period)
        records: list[TrackerRecord] = []

        log.info("\n[1/4] AMEX Extraction")
        self._metrics.set_stage("amex_stage")
        amex_results = self._amex_stage.run(self.settings.amex_input_folder, self.period)
        summary.amex_files = amex_results.files_processed
        for err in amex_results.errors:
            summary.error_details.append(f"AMEX: {err}")

        log.info("\n[2/4] Concur Extraction")
        self._metrics.set_stage("concur_stage")
        concur_results = self._concur_stage.run(self.settings.concur_input_folder, self.period)
        summary.concur_files = concur_results.files_processed
        for err in concur_results.errors:
            summary.error_details.append(f"Concur: {err}")

        log.info("\n[3/4] Matching")
        self._metrics.set_stage("matching_stage")
        records = self._matching_stage.run(
            amex_statements  = amex_results.statements,
            concur_reports   = concur_results.reports,
            receipts_by_file = concur_results.receipts_by_file,
        )
        summary.cardholders_total = len(records)

        log.info("\n[4/4] Output")
        self._metrics.set_stage("output_stage")
        self._output_stage.run(records, summary)

        summary.approved      = sum(1 for r in records if r.status.value == "approved")
        summary.flagged       = sum(1 for r in records if r.status.value == "flagged")
        summary.errors        = len(summary.error_details)
        summary.processing_ms = int((time.monotonic() - t0) * 1000)
        summary.completed_at  = datetime.now(timezone.utc).isoformat()

        self._metrics.print_summary()
        self._metrics.write(self.settings.output_folder / "metrics")

        log.info("")
        log.info("=" * 70)
        log.info("PIPELINE COMPLETE [%dms]", summary.processing_ms)
        log.info(
            "  Cardholders: %d  ✅ %d approved  🚩 %d flagged  ❌ %d errors",
            summary.cardholders_total, summary.approved, summary.flagged, summary.errors,
        )
        log.info("=" * 70)
        return summary

    def _clear_outputs(self) -> None:
        """Remove and recreate output subdirs before each run. logs/ is preserved."""
        out = self.settings.output_folder
        for subdir in _CLEAR_ON_RUN:
            target = out / subdir
            if target.exists():
                shutil.rmtree(target)
                log.debug("Cleared: %s", target)
            target.mkdir(parents=True, exist_ok=True)
        log.info("Output directories cleared: %s", _CLEAR_ON_RUN)

    def _setup_logging(self) -> None:
        s = self.settings
        setup_logging(level=s.log_level, log_file=s.log_file if s.log_to_file else None)