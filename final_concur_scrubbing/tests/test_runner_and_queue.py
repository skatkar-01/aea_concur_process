"""
tests/test_runner_and_queue.py
───────────────────────────────
Unit tests for:
  - PipelineJob serialisation / deserialisation
  - resolver.resolve() in local mode
  - runner.run_job() with a real local PDF stub
  - table_state job lifecycle (in-memory mock)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.queue_client import PipelineJob


# ── PipelineJob ───────────────────────────────────────────────────────────────

class TestPipelineJob:
    def test_roundtrip_json(self):
        job = PipelineJob(
            filename="BAKER_C_Feb.pdf",
            pdf_type="amex",
            month_key="2026-02",
            source="webhook",
            file_id="box123",
        )
        restored = PipelineJob.from_json(job.to_json())
        assert restored.filename  == job.filename
        assert restored.pdf_type  == job.pdf_type
        assert restored.month_key == job.month_key
        assert restored.file_id   == job.file_id
        assert restored.job_id    == job.job_id

    def test_is_local_true(self):
        job = PipelineJob(
            filename="test.pdf", pdf_type="concur",
            month_key="2026-03", source="local",
            local_path="/tmp/test.pdf",
        )
        assert job.is_local is True

    def test_is_local_false(self):
        job = PipelineJob(
            filename="test.pdf", pdf_type="concur",
            month_key="2026-03", source="webhook",
            file_id="box456",
        )
        assert job.is_local is False

    def test_defaults(self):
        job = PipelineJob(
            filename="x.pdf", pdf_type="amex",
            month_key="2026-03", source="test",
        )
        assert job.retry_count == 0
        assert len(job.job_id)  == 36   # UUID4


# ── Resolver — local mode ─────────────────────────────────────────────────────

class TestResolverLocal:
    def test_resolve_existing_file(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        from src.resolver import resolve
        job = PipelineJob(
            filename="test.pdf", pdf_type="amex",
            month_key="2026-03", source="test",
            local_path=str(pdf),
        )
        with resolve(job) as path:
            assert path == pdf
            assert path.exists()

    def test_resolve_missing_file_raises(self, tmp_path):
        from src.resolver import resolve
        job = PipelineJob(
            filename="missing.pdf", pdf_type="amex",
            month_key="2026-03", source="test",
            local_path=str(tmp_path / "missing.pdf"),
        )
        with pytest.raises(FileNotFoundError):
            with resolve(job) as _:
                pass

    def test_resolve_no_source_raises(self):
        from src.resolver import resolve
        job = PipelineJob(
            filename="test.pdf", pdf_type="amex",
            month_key="2026-03", source="test",
            # neither local_path nor file_id set
        )
        with pytest.raises(ValueError, match="neither local_path nor file_id"):
            with resolve(job) as _:
                pass

    def test_resolve_box_without_client_raises(self):
        from src.resolver import resolve
        job = PipelineJob(
            filename="test.pdf", pdf_type="amex",
            month_key="2026-03", source="test",
            file_id="box_123",
        )
        with pytest.raises(ValueError, match="BoxClient is required"):
            with resolve(job, box_client=None) as _:
                pass


# ── Runner — local mode (mocked pipeline) ─────────────────────────────────────

class TestRunnerLocal:
    def _make_job(self, tmp_path, pdf_type="amex") -> PipelineJob:
        pdf = tmp_path / f"test_{pdf_type}.pdf"
        pdf.write_bytes(b"%PDF-1.4 stub")
        return PipelineJob(
            filename=pdf.name,
            pdf_type=pdf_type,
            month_key="2026-03",
            source="test",
            local_path=str(pdf),
        )

    @patch("src.runner._get_state")
    @patch("src.runner._run_amex")
    def test_run_amex_success(self, mock_amex, mock_state, tmp_path):
        mock_amex.return_value = "3 cardholders"
        mock_state.return_value = MagicMock(
            is_amex_initialized=MagicMock(return_value=False),
            mark_processing=MagicMock(),
        )
        from src.runner import run_job
        job    = self._make_job(tmp_path, "amex")
        result = run_job(job)
        assert result.success is True
        assert result.details == "3 cardholders"

    @patch("src.runner._get_state")
    @patch("src.runner._run_amex")
    def test_run_failure_captured(self, mock_amex, mock_state, tmp_path):
        mock_amex.side_effect = RuntimeError("API timeout")
        mock_state.return_value = MagicMock(
            is_amex_initialized=MagicMock(return_value=False),
            mark_processing=MagicMock(),
            mark_failed=MagicMock(),
        )
        from src.runner import run_job
        job    = self._make_job(tmp_path, "amex")
        result = run_job(job)
        assert result.success is False
        assert "API timeout" in result.error

    @patch("src.runner._get_state")
    @patch("src.runner._run_concur")
    def test_run_concur_success(self, mock_concur, mock_state, tmp_path):
        mock_concur.return_value = "patched BAKER, CHARLIE"
        mock_state.return_value = MagicMock(
            is_amex_initialized=MagicMock(return_value=True),
            mark_processing=MagicMock(),
        )
        from src.runner import run_job
        job    = self._make_job(tmp_path, "concur")
        result = run_job(job)
        assert result.success is True


# ── Month key parser ──────────────────────────────────────────────────────────

class TestParseMonthKey:
    def test_valid(self):
        from src.runner import _parse_month_key
        assert _parse_month_key("2026-03") == (2026, 3)
        assert _parse_month_key("2025-12") == (2025, 12)

    def test_zero_padded(self):
        from src.runner import _parse_month_key
        assert _parse_month_key("2026-01") == (2026, 1)
