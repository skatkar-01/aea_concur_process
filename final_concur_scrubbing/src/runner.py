"""
src/runner.py
──────────────
The single pipeline entry point — used by every execution path.

PRODUCTION FIXES (2024-Q2):
  - BUG 6: TrackerRow.comments is typed str, not Optional[str].  The fallback
            path was passing reconciliation_comment which can be None.
            Fixed with `or ""` coercion at every assignment site.
  - BUG 5 RETRACTED: Removed improper _coerce_approval() function.  Instead,
            now use concur_record.approvals_complete consistently in both
            (AMEX match and no AMEX match) paths.
"""
from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config.settings import get_settings
from src.amex_writer import write_amex_output
from src.concur_extractor import extract_concur_record
from src.concur_writer import write_concur_excel
from src.amex_extractor import extract_statement
from src.reconciler import reconcile, reconcile_amex_only, TrackerRow
from src.resolver import resolve
from src.tracker_writer import MonthInfo, init_month_sheet, patch_cardholder_row
from utils.logging_config import get_logger
from utils.metrics import METRICS, timed
from utils.month_detector import detect_month
from utils.queue_client import PipelineJob

logger = get_logger(__name__)


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class RunResult:
    job:        PipelineJob
    success:    bool
    duration_s: float = 0.0
    error:      Optional[str] = None
    details:    Optional[str] = None


# ── Type-safety helper ────────────────────────────────────────────────────────

def _coerce_comments(value: object) -> str:
    """
    Safely coerce any comments value to str (never None).

    TrackerRow.comments is typed str, but reconciliation_comment and
    similar fields are Optional[str].
    """
    if value is None:
        return ""
    return str(value).strip()


# ── Tracker write helper ──────────────────────────────────────────────────────

def _write_to_tracker(
    mode: str,
    rows_or_row,
    month_info: MonthInfo,
    box_client=None,
    mode_override: Optional[str] = None,
) -> None:
    s = get_settings()

    if mode_override == "cloud" or (mode_override is None and box_client is not None):
        logger.info(f"Writing to Box tracker: file_id={s.box_tracker_file_id}")
        wb = box_client.read_xlsx(s.box_tracker_file_id)
        tmp = Path(tempfile.gettempdir()) / f"tracker_{month_info.sheet_name.replace(' ', '_')}.xlsx"
        wb.save(tmp)
        if mode == "init":
            init_month_sheet(rows_or_row, month_info, tmp)
        else:
            patch_cardholder_row(rows_or_row, month_info, tmp)
        from openpyxl import load_workbook
        updated = load_workbook(tmp)
        box_client.upload_xlsx(s.box_tracker_file_id, updated, "AmEx Checklist.xlsx")
        tmp.unlink(missing_ok=True)
    else:
        logger.info(f"Writing to local tracker: {s.tracker_path}")
        tracker_dir = s.tracker_path
        dynamic_tracker = tracker_dir / f"{month_info.year} New AmEx Checklist.xlsx"
        if mode == "init":
            init_month_sheet(rows_or_row, month_info, dynamic_tracker)
        else:
            patch_cardholder_row(rows_or_row, month_info, dynamic_tracker)


def _get_state(box_client=None):
    s = get_settings()
    if box_client is not None:
        from utils.table_state import table_state_from_settings
        return table_state_from_settings()
    else:
        from utils.state_manager import StateManager
        return StateManager(s.state_path)


# ── AMEX pipeline ─────────────────────────────────────────────────────────────

def _run_amex(
    pdf_path: Path,
    month_info: MonthInfo,
    job: PipelineJob,
    state,
    box_client=None,
) -> str:
    try:
        statement = extract_statement(pdf_path)
        rows      = [reconcile_amex_only(ch) for ch in statement.cardholders]
        write_amex_output(statement, month_info, box_client)
        _write_to_tracker("init", rows, month_info, box_client, job.mode_override)
    except Exception as exc:
        # Log extraction failure but don't attempt to write partial data to tracker
        # AMEX "init" requires full statement data; partial failure means skip tracker update
        log = logger.bind(job_id=job.job_id, file=job.filename, month=month_info.sheet_name)
        log.error("amex_extraction_failed", error=str(exc))
        raise

    if hasattr(state, "mark_amex_initialized"):
        state.mark_amex_initialized(
            month_info.year, month_info.month,
            month_info.sheet_name, job.filename,
        )
    else:
        state.mark_done(
            month_info.year, month_info.month, job.filename,
            sheet_name=month_info.sheet_name,
            amex_file=job.filename,
        )

    METRICS.files_processed.inc()
    return f"{statement.total_cardholders} cardholders, {statement.total_transactions} transactions"


# ── Concur pipeline ───────────────────────────────────────────────────────────

def _run_concur(
    pdf_path: Path,
    month_info: MonthInfo,
    job: PipelineJob,
    state,
    box_client=None,
) -> str:
    log = logger.bind(job_id=job.job_id, file=job.filename, month=month_info.sheet_name)

    if not state.is_amex_initialized(month_info.year, month_info.month):
        raise RuntimeError(
            f"AMEX not yet initialised for {month_info.sheet_name}. "
            "Concur job will retry — AMEX must be processed first."
        )

    try:
        concur_record, llm_metrics = extract_concur_record(pdf_path, no_cache=job.no_cache)
        log.info(
            "concur_llm_metrics",
            tokens_in=llm_metrics.input_tokens,
            tokens_out=llm_metrics.output_tokens,
            cost_usd=llm_metrics.cost_usd,
            latency_s=llm_metrics.latency_seconds,
            status=llm_metrics.status,
        )

        amex_ch = _find_amex_cardholder(
            concur_record.cardholder_name, month_info, state, box_client
        )

        if amex_ch:
            tracker_row = reconcile(amex_ch, concur_record)
        else:
            # When no AMEX match, build TrackerRow manually from Concur data
            # Use same fields as reconcile() function for consistency
            tracker_row = TrackerRow(
                cardholder_name=concur_record.cardholder_name,
                amex_total=None,
                concur_submitted=concur_record.amount_submitted,
                report_pdf=concur_record.report_pdf_attached,
                approvals=concur_record.approvals_complete,  # Use same field as reconcile()
                receipts=concur_record.receipts_attached,
                comments=_coerce_comments(concur_record.report_summary.reconciliation_comment if concur_record.report_summary else None),
            )

    except Exception as exc:
        # Extraction failed — write error marker to tracker so row shows as attempted
        error_msg = str(exc)
        log.error(
            "concur_extraction_failed",
            error=error_msg,  # Limit error message length
            error_type=type(exc).__name__,
            file=job.filename,
        )
        error_comment = f"Extraction failed: {error_msg[:100]}"
        tracker_row = TrackerRow(
            cardholder_name="[EXTRACTION FAILED]",
            amex_total=None,
            concur_submitted=None,
            report_pdf=False,
            approvals=None,
            receipts=False,
            comments=error_comment,
        )
        _write_to_tracker("patch", tracker_row, month_info, box_client)
        # Re-raise so run_job can handle it and mark state as failed
        raise

    _write_to_tracker("patch", tracker_row, month_info, box_client)

    s = get_settings()
    output_base = s.output_dir
    try:
        excel_path = write_concur_excel(concur_record, output_base, month_info, job.filename)
        log.info("concur_excel_written", excel_file=str(excel_path))
    except Exception as exc:
        log.warning("concur_excel_write_failed", error=str(exc))

    if hasattr(state, "mark_concur_processed"):
        state.mark_concur_processed(month_info.year, month_info.month, job.filename)
    else:
        state.mark_done(month_info.year, month_info.month, job.filename,
                        sheet_name=month_info.sheet_name)

    METRICS.files_processed.inc()
    
    # Log successful completion summary
    log.info(
        "concur_processing_complete",
        cardholder=concur_record.cardholder_name,
        amount=concur_record.amount_submitted,
        transactions=len(concur_record.transactions),
        approvals=concur_record.approvals_complete,
        receipts=concur_record.receipts_attached,
        tokens_in=llm_metrics.input_tokens,
        tokens_out=llm_metrics.output_tokens,
        cost_usd=round(llm_metrics.cost_usd, 4),
        latency_s=llm_metrics.latency_seconds,
        status=llm_metrics.status,
    )
    
    return f"patched row for {concur_record.cardholder_name}"


def _find_amex_cardholder(name: str, month_info: MonthInfo, state, box_client=None):
    """Re-extract AMEX statement (from cache — no API cost) to get Cardholder."""
    s = get_settings()

    amex_filename = None
    if hasattr(state, "get_amex_file"):
        amex_filename = state.get_amex_file(month_info.year, month_info.month)
    else:
        snap = state.snapshot()
        month_data = snap.get(f"{month_info.year}-{month_info.month:02d}", {})
        amex_filename = month_data.get("amex_file")

    if not amex_filename:
        return None

    if box_client is not None:
        amex_file_id = getattr(state, "get_amex_file_id",
                               lambda *_: None)(month_info.year, month_info.month)
        if not amex_file_id:
            return None
        amex_job = PipelineJob(
            filename=amex_filename, pdf_type="amex",
            month_key=f"{month_info.year}-{month_info.month:02d}",
            source="reconcile_lookup", file_id=amex_file_id,
        )
    else:
        base = getattr(s, "box_base_path", s.tracker_path.parent)
        candidates = list(base.rglob(amex_filename))
        if not candidates:
            return None
        amex_job = PipelineJob(
            filename=amex_filename, pdf_type="amex",
            month_key=f"{month_info.year}-{month_info.month:02d}",
            source="reconcile_lookup", local_path=str(candidates[0]),
        )

    try:
        with resolve(amex_job, box_client) as pdf_path:
            statement = extract_statement(pdf_path)
    except Exception as exc:
        logger.warning("amex_reload_failed", error=str(exc))
        return None

    target = name.upper().strip()
    for ch in statement.cardholders:
        ch_name = f"{ch.last_name}, {ch.first_name}".upper().strip()
        if ch_name == target:
            return ch
    return None


# ── Public entry point ────────────────────────────────────────────────────────

def run_job(job: PipelineJob, box_client=None) -> RunResult:
    """
    Execute a single pipeline job end-to-end.

    This is the ONLY function called by:
      - Azure Queue trigger Function
      - watcher.py (local watchdog)
      - main.py --file (manual)
      - pytest (integration tests)
    """
    log = logger.bind(job_id=job.job_id, file=job.filename, type=job.pdf_type)
    log.info("run_job_start", source=job.source, retry=job.retry_count)
    start = time.perf_counter()
    state = _get_state(box_client)

    try:
        if hasattr(state, "mark_processing"):
            state.mark_processing(
                *_parse_month_key(job.month_key),
                job.filename, job.job_id,
            )

        with resolve(job, box_client) as pdf_path:
            month_info = detect_month(pdf_path)
            if month_info is None:
                year, month = _parse_month_key(job.month_key)
                import calendar
                month_info = MonthInfo(
                    year=year, month=month,
                    sheet_name=f"{calendar.month_name[month]} {year}",
                    col_b_header=f"{calendar.month_name[month]} 4, {year} Statement Total",
                )

            with timed(METRICS.total_duration):
                if job.pdf_type == "amex":
                    detail = _run_amex(pdf_path, month_info, job, state, box_client)
                elif job.pdf_type == "concur":
                    detail = _run_concur(pdf_path, month_info, job, state, box_client)
                else:
                    raise ValueError(f"Unknown pdf_type: {job.pdf_type!r}")

        duration = time.perf_counter() - start
        log.info("run_job_success", duration_s=round(duration, 2), detail=detail)
        return RunResult(job=job, success=True, duration_s=duration, details=detail)

    except Exception as exc:
        duration = time.perf_counter() - start
        error_str = str(exc)
        log.error("run_job_failed", error=error_str, duration_s=round(duration, 2),
                  exc_info=True)

        METRICS.files_failed.inc()
        if hasattr(state, "mark_failed"):
            state.mark_failed(
                *_parse_month_key(job.month_key),
                job.filename, error_str, job.retry_count,
            )
        return RunResult(job=job, success=False, duration_s=duration, error=error_str)


def _parse_month_key(month_key: str) -> tuple[int, int]:
    parts = month_key.split("-")
    return int(parts[0]), int(parts[1])