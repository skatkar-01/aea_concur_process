"""
pipeline/output_stage.py
Stage 4: Persist all results.
  1. Update tracker (upsert all records)
  2. Write validation report
  3. Write exception report (flagged only)
  4. Write run summary JSON
"""
from __future__ import annotations

from models.tracker import RunSummary, TrackerRecord
from shared.logger import get_logger
from storage.json_store import JSONStore
from storage.report_writer import ReportWriter
from storage.tracker_store import TrackerStore

log = get_logger(__name__)


class OutputStage:
    def __init__(
        self,
        tracker:  TrackerStore,
        reports:  ReportWriter,
        json_store: JSONStore,
    ):
        self._tracker = tracker
        self._reports = reports
        self._json    = json_store

    def run(self, records: list[TrackerRecord], run_summary: RunSummary) -> None:
        """Persist all results. Individual failures are logged but don't stop others."""

        # 1. Update tracker
        try:
            self._tracker.upsert_many(records)
            log.info("  Tracker updated: %d record(s)", len(records))
        except Exception as exc:
            log.error("  Tracker write failed: %s", exc)

        # 2. Write reports
        try:
            paths = self._reports.write_all(records, run_summary)
            for name, path in paths.items():
                log.info("  %s: %s", name, path.name)
        except Exception as exc:
            log.error("  Report write failed: %s", exc)

        # 3. Write run summary JSON
        try:
            self._json.write_run_results(records, run_summary.to_dict())
            log.info("  Run results JSON written")
        except Exception as exc:
            log.error("  Run results JSON failed: %s", exc)
