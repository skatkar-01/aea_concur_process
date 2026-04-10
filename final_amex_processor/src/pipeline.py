"""
src/pipeline.py
────────────────
Orchestrates the end-to-end processing of one or many PDF files.

  process_file()   — single file
  process_batch()  — all PDFs in the input directory

Both functions return a ProcessingResult dataclass so the caller
(main.py, tests, a web handler, …) can inspect outcomes without
catching exceptions.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from config.settings import get_settings
from src.extractor import extract_statement
from src.models import Statement
from src.writer import write_xlsx
from utils.logging_config import get_logger
from utils.metrics import METRICS, timed

logger = get_logger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class FileResult:
    pdf_path:    Path
    output_path: Optional[Path] = None
    statement:   Optional[Statement] = None
    error:       Optional[str] = None
    duration_s:  float = 0.0

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class BatchResult:
    results: list[FileResult] = field(default_factory=list)

    @property
    def succeeded(self) -> list[FileResult]:
        return [r for r in self.results if r.success]

    @property
    def failed(self) -> list[FileResult]:
        return [r for r in self.results if not r.success]

    @property
    def total(self) -> int:
        return len(self.results)


# ── Single-file pipeline ──────────────────────────────────────────────────────

def process_file(
    pdf_path: Path,
    output_dir: Optional[Path] = None,
) -> FileResult:
    """
    Extract a single PDF and write the corresponding XLSX.

    Args:
        pdf_path:   Source PDF to process.
        output_dir: Directory for the XLSX output.
                    Defaults to settings.output_dir.

    Returns:
        FileResult with success/failure details.
    """
    settings   = get_settings()
    output_dir = output_dir or settings.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / (pdf_path.stem + ".xlsx")
    log = logger.bind(pdf=pdf_path.name, output=output_path.name)
    log.info("pipeline_start")

    start = time.perf_counter()
    try:
        with timed(METRICS.total_duration):
            statement = extract_statement(pdf_path)
            write_xlsx(statement, output_path)

        duration = time.perf_counter() - start
        log.info(
            "pipeline_complete",
            duration_s=round(duration, 2),
            cardholders=statement.total_cardholders,
            transactions=statement.total_transactions,
        )
        return FileResult(
            pdf_path=pdf_path,
            output_path=output_path,
            statement=statement,
            duration_s=duration,
        )

    except Exception as exc:  # noqa: BLE001
        duration = time.perf_counter() - start
        log.error("pipeline_failed", error=str(exc), duration_s=round(duration, 2),
                  exc_info=True)
        METRICS.files_failed.inc()
        return FileResult(
            pdf_path=pdf_path,
            error=str(exc),
            duration_s=duration,
        )


# ── Batch pipeline ────────────────────────────────────────────────────────────

def process_batch(
    input_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    *,
    glob: str = "**/*.pdf",
) -> BatchResult:
    """
    Process every PDF in input_dir (recursive by default).

    Args:
        input_dir:  Root folder to scan. Defaults to settings.input_dir.
        output_dir: XLSX destination.   Defaults to settings.output_dir.
        glob:       Glob pattern for PDF discovery.

    Returns:
        BatchResult containing individual FileResult entries.
    """
    settings   = get_settings()
    input_dir  = input_dir  or settings.input_dir
    output_dir = output_dir or settings.output_dir

    pdfs = sorted(input_dir.glob(glob))
    log  = logger.bind(input_dir=str(input_dir), pdf_count=len(pdfs))

    if not pdfs:
        log.warning("no_pdfs_found")
        return BatchResult()

    log.info("batch_start")
    batch = BatchResult()

    for pdf in pdfs:
        result = process_file(pdf, output_dir)
        batch.results.append(result)

    log.info(
        "batch_complete",
        total=batch.total,
        succeeded=len(batch.succeeded),
        failed=len(batch.failed),
    )
    return batch
