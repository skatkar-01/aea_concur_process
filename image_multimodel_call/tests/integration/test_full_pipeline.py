"""
tests/integration/test_full_pipeline.py
Integration tests using sample PDFs from inputs/.
Skipped automatically if no PDFs are present in inputs/.
"""
from __future__ import annotations
import pytest
from pathlib import Path

AMEX_DIR   = Path("inputs/amex")
CONCUR_DIR = Path("inputs/concur")

has_amex   = any(AMEX_DIR.glob("*.pdf"))   if AMEX_DIR.exists()   else False
has_concur = any(CONCUR_DIR.glob("*.pdf")) if CONCUR_DIR.exists() else False

skip_no_inputs = pytest.mark.skipif(
    not (has_amex and has_concur),
    reason="No sample PDFs in inputs/ — skipping integration tests",
)


@skip_no_inputs
class TestFullPipeline:
    """
    These tests run the real pipeline against sample files.
    They require valid Azure credentials in .env.
    """

    def test_pipeline_runs_without_error(self):
        from pipeline.run import PipelineRun
        run     = PipelineRun(period="")
        summary = run.execute()
        assert summary.errors == 0 or summary.cardholders_total > 0

    def test_tracker_written(self, tmp_path):
        import os
        os.environ["OUTPUT_FOLDER"] = str(tmp_path)
        from pipeline.run import PipelineRun
        run = PipelineRun()
        run.execute()
        tracker_csv = tmp_path / "tracker" / "tracker.csv"
        assert tracker_csv.exists()

    def test_reports_written(self, tmp_path):
        import os
        os.environ["OUTPUT_FOLDER"] = str(tmp_path)
        from pipeline.run import PipelineRun
        run = PipelineRun()
        run.execute()
        reports = list((tmp_path / "reports").glob("*.txt"))
        assert len(reports) >= 2   # validation + exception


@skip_no_inputs
class TestAmexExtractor:
    def test_extracts_cardholders(self):
        from shared.azure_client import AzureClient
        from config.settings import get_settings
        from extractors.amex_extractor import AmexExtractor
        pdf = next(AMEX_DIR.glob("*.pdf"))
        ext = AmexExtractor(AzureClient(get_settings()))
        stmt = ext.extract(pdf)
        assert len(stmt.cardholders) > 0

    def test_total_row_present(self):
        from shared.azure_client import AzureClient
        from config.settings import get_settings
        from extractors.amex_extractor import AmexExtractor
        pdf = next(AMEX_DIR.glob("*.pdf"))
        ext = AmexExtractor(AzureClient(get_settings()))
        stmt = ext.extract(pdf)
        for ch in stmt.cardholders:
            assert ch.total_row is not None, f"Missing total_row for {ch.last_name}"


@skip_no_inputs
class TestConcurExtractor:
    def test_extracts_transactions(self):
        from shared.azure_client import AzureClient
        from config.settings import get_settings
        from extractors.concur_extractor import ConcurExtractor
        pdf = next(CONCUR_DIR.glob("*.pdf"))
        ext = ConcurExtractor(AzureClient(get_settings()))
        rpt = ext.extract(pdf)
        assert rpt.employee_name is not None
        assert len(rpt.transactions) > 0

    def test_total_claimed_nonzero(self):
        from shared.azure_client import AzureClient
        from config.settings import get_settings
        from extractors.concur_extractor import ConcurExtractor
        pdf = next(CONCUR_DIR.glob("*.pdf"))
        ext = ConcurExtractor(AzureClient(get_settings()))
        rpt = ext.extract(pdf)
        assert rpt.total_claimed > 0
