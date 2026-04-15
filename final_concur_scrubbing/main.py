"""
Manual local entrypoint for the combined AMEX + Concur tracker pipeline.

Examples:
    python main.py --file "C:/Box/AEA - Concur/2026/03-MARCH/AmEx Statements/file.pdf"
    python main.py --file "C:/tmp/report.pdf" --pdf-type concur --month-key 2026-03
    python main.py --input-dir "C:/Users/SKatkar/Box/AEA - Concur"
    python main.py --input-dir "C:/path" --batch-size 5  # Process with 5 parallel workers
    python main.py --file "file.pdf" --no-cache  # Force fresh API call and rewrite cache
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich import box
from rich.console import Console
from rich.table import Table

from config.settings import get_settings
from src.runner import RunResult, run_job
from utils.logging_config import configure_logging, get_logger
from utils.metrics import start_metrics_server
from utils.month_detector import PdfType, classify_pdf, detect_month
from utils.queue_client import PipelineJob

console = Console()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combined AMEX + Concur tracker pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--file",
        "-f",
        type=Path,
        default=None,
        help="Process one PDF and update the tracker.",
    )
    parser.add_argument(
        "--input-dir",
        "-i",
        type=Path,
        default=None,
        help="Recursively process all PDFs under this folder.",
    )
    parser.add_argument(
        "--pdf-type",
        choices=["amex", "concur"],
        default=None,
        help="Override AMEX/Concur classification for --file.",
    )
    parser.add_argument(
        "--month-key",
        default=None,
        help="Fallback month key in YYYY-MM format when folder path has no month segment.",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="Start Prometheus metrics server.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override LOG_LEVEL from environment.",
    )
    parser.add_argument(
        "--count",
        "-n",
        type=int,
        default=None,
        help="Limit number of files taken per month and type (amex/concur).",
    )

    parser.add_argument(
        "--batch-size",
        "-b",
        type=int,
        default=None,
        help="Batch size for processing multiple files (default: process all at once).",
    )

    parser.add_argument(
        "--mode",
        "-m",
        type=str,
        default=None,
        help="Processing mode (local or cloud).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cache and rewrite extracted data (forces fresh API call).",
    )
    return parser.parse_args()


def _infer_pdf_type(pdf_path: Path, override: str | None) -> str | None:
    if override:
        return override

    settings = get_settings()
    pdf_type = classify_pdf(
        pdf_path,
        settings.amex_subfolder,
        settings.concur_subfolder,
    )
    if pdf_type == PdfType.AMEX:
        return "amex"
    if pdf_type == PdfType.CONCUR:
        return "concur"

    lowered = pdf_path.name.lower()
    if "amex" in lowered or "statement" in lowered:
        return "amex"
    if "concur" in lowered or "report" in lowered:
        return "concur"
    return None


def _infer_month_key(pdf_path: Path, override: str | None) -> str | None:
    if override:
        return override
    month_info = detect_month(pdf_path)
    if month_info:
        return f"{month_info.year}-{month_info.month:02d}"
    return None


def _build_job(
    pdf_path: Path,
    *,
    source: str,
    pdf_type_override: str | None = None,
    month_key_override: str | None = None,
    mode_override: str | None = None,
    no_cache: bool = False,
) -> PipelineJob | None:
    # Skip monthly aggregate files (ALL_*) in AMEX folder
    # Pattern: ALL_MAR_062026.pdf (ALL_ + month + MMYYYY date)
    # These contain all transactions for a month in one consolidated file
    filename = pdf_path.name
    if filename.startswith("ALL_") and "AmEx" in str(pdf_path):
        # Verify it matches ALL_[MONTH]_MMYYYY.pdf pattern before skipping
        import re
        if re.match(r"ALL_[A-Z]{3}_[01]\d[0-9]{4}\.pdf", filename):
            return None  # Skip aggregate file
    
    pdf_type = _infer_pdf_type(pdf_path, pdf_type_override)
    month_key = _infer_month_key(pdf_path, month_key_override)
    if pdf_type is None or month_key is None:
        return None
    return PipelineJob(
        filename=pdf_path.name,
        pdf_type=pdf_type,
        month_key=month_key,
        source=source,
        local_path=str(pdf_path),
        mode_override=mode_override,
        no_cache=no_cache,
    )


def _collect_jobs(args: argparse.Namespace) -> list[PipelineJob]:
    if args.file:
        job = _build_job(
            args.file,
            source="manual",
            pdf_type_override=args.pdf_type,
            month_key_override=args.month_key,
            mode_override=args.mode,
            no_cache=args.no_cache,
        )
        return [job] if job else []
    settings = get_settings()
    scan_dir = args.input_dir or settings.box_base_path
    pdfs = sorted(scan_dir.rglob("*.pdf"))

    jobs: list[PipelineJob] = []
    for pdf in pdfs:
        job = _build_job(
            pdf,
            source="manual_batch",
            month_key_override=args.month_key,
            mode_override=args.mode,
            no_cache=args.no_cache,
        )
        if job:
            jobs.append(job)

    # sort jobs by month, prefer amex before concur, then filename
    sorted_jobs = sorted(
        jobs, key=lambda job: (job.month_key, 0 if job.pdf_type == "amex" else 1, job.filename)
    )

    # if --count provided, limit to that many files per (month_key, pdf_type)
    if args.count and args.count > 0:
        limited = []
        counts = {}
        for job in sorted_jobs:
            key = (job.month_key, job.pdf_type)
            if counts.get(key, 0) < args.count:
                limited.append(job)
                counts[key] = counts.get(key, 0) + 1
        return limited

    return sorted_jobs


def _print_summary(results: list[RunResult]) -> None:
    table = Table(
        title="Combined Tracker Pipeline",
        box=box.ROUNDED,
        show_lines=True,
        header_style="bold white on dark_blue",
    )
    table.add_column("PDF", style="cyan", no_wrap=True)
    table.add_column("Type", no_wrap=True)
    table.add_column("Month", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Duration", justify="right")
    table.add_column("Details / Error", overflow="fold")

    for result in results:
        status = "[green]OK[/green]" if result.success else "[red]FAIL[/red]"
        table.add_row(
            result.job.filename,
            result.job.pdf_type,
            result.job.month_key,
            status,
            f"{result.duration_s:.1f}s",
            result.details or result.error or "",
        )

    console.print()
    console.print(table)
    succeeded = sum(1 for result in results if result.success)
    failed = len(results) - succeeded
    
    # Compute batch statistics
    total_time = sum(r.duration_s for r in results)
    concur_count = sum(1 for r in results if r.job.pdf_type == "concur")
    amex_count = sum(1 for r in results if r.job.pdf_type == "amex")
    concur_succeeded = sum(1 for r in results if r.success and r.job.pdf_type == "concur")
    amex_succeeded = sum(1 for r in results if r.success and r.job.pdf_type == "amex")
    
    console.print(
        f"\n[bold]Summary:[/bold]\n"
        f"  Total: {len(results)} file(s) | [green]{succeeded} succeeded[/green] | [red]{failed} failed[/red]\n"
        f"  AMEX: {amex_count} file(s) | [green]{amex_succeeded} succeeded[/green]\n"
        f"  Concur: {concur_count} file(s) | [green]{concur_succeeded} succeeded[/green]\n"
        f"  Total Duration: {total_time:.1f}s\n"
    )


def main() -> int:
    args = _parse_args()
    settings = get_settings()

    configure_logging(
        level=args.log_level or settings.log_level,
        fmt=settings.log_format,
        log_dir=settings.log_dir,
    )
    settings.ensure_dirs()

    if args.metrics or settings.metrics_enabled:
        start_metrics_server(settings.metrics_port)

    log = get_logger("main")
    jobs = _collect_jobs(args)
    for job in jobs:
        console.print(job)
    if not jobs:
        log.warning(
            "no_jobs_found",
            file=str(args.file) if args.file else None,
            input_dir=str(args.input_dir) if args.input_dir else str(settings.box_base_path),
            month_key=args.month_key,
            pdf_type=args.pdf_type,
        )
        console.print(
            "[yellow]No processable PDFs found.[/yellow] "
            "Use --pdf-type and --month-key for files outside the Box folder layout."
        )
        return 1

    t0 = time.perf_counter()

    # Determine batch size (number of parallel workers)
    # Default: 1 (sequential) if not specified
    batch_size = args.batch_size or 1
    
    if batch_size > 1:
        # Parallel processing with ThreadPoolExecutor
        log.info(
            "parallel_processing_start",
            total_jobs=len(jobs),
            workers=batch_size,
        )
        results = _run_jobs_parallel(jobs, batch_size, log)
    else:
        # Sequential processing (backward compatible)
        log.info("sequential_processing_start", total_jobs=len(jobs))
        results = []
        for idx, job in enumerate(jobs, 1):
            result = run_job(job)
            results.append(result)
            
            # Console progress output
            status_symbol = "[green]✓[/green]" if result.success else "[red]✗[/red]"
            progress_bar = f"[bold][{idx}/{len(jobs)}][/bold]"
            if result.success:
                console.print(
                    f"{status_symbol} {progress_bar} {job.filename} "
                    f"[dim]({result.details or 'OK'}) - {result.duration_s:.1f}s[/dim]"
                )
            else:
                error_preview = (result.error or "Unknown error")[:100]
                console.print(
                    f"{status_symbol} {progress_bar} {job.filename} "
                    f"[red][ERROR: {error_preview}][/red] - {result.duration_s:.1f}s"
                )

    _print_summary(results)

    # Enhanced logging with detailed breakdown
    succeeded = sum(1 for result in results if result.success)
    concur_count = sum(1 for r in results if r.job.pdf_type == "concur")
    amex_count = sum(1 for r in results if r.job.pdf_type == "amex")
    concur_succeeded = sum(1 for r in results if r.success and r.job.pdf_type == "concur")
    amex_succeeded = sum(1 for r in results if r.success and r.job.pdf_type == "amex")
    
    log.info(
        "manual_run_complete",
        jobs=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        amex_total=amex_count,
        amex_succeeded=amex_succeeded,
        concur_total=concur_count,
        concur_succeeded=concur_succeeded,
        wall_time_s=round(time.perf_counter() - t0, 2),
        parallel=batch_size > 1,
        workers=batch_size,
    )
    return 0 if all(result.success for result in results) else 1


def _run_jobs_parallel(
    jobs: list[PipelineJob],
    max_workers: int,
    log,
) -> list[RunResult]:
    """
    Process jobs in parallel using ThreadPoolExecutor.

    Args:
        jobs: List of PipelineJob objects to process
        max_workers: Number of concurrent worker threads
        log: Logger instance

    Returns:
        List of RunResult objects in original job order
    """
    # Map to store results in original order
    job_to_index = {id(job): i for i, job in enumerate(jobs)}
    results_by_index: dict[int, RunResult] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(run_job, job): job for job in jobs}

        completed = 0
        for future in as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
                idx = job_to_index[id(job)]
                results_by_index[idx] = result
                completed += 1

                status = "success" if result.success else "failed"
                status_symbol = "[green]✓[/green]" if result.success else "[red]✗[/red]"
                
                # Log to structured logging
                log.info(
                    "parallel_job_completed",
                    pdf=job.filename,
                    status=status,
                    completed=completed,
                    total=len(jobs),
                    duration_s=round(result.duration_s, 2),
                )
                
                # Console output for real-time progress
                progress_bar = f"[bold][{completed}/{len(jobs)}][/bold]"
                if result.success:
                    console.print(
                        f"{status_symbol} {progress_bar} {job.filename} "
                        f"[dim]({result.details or 'OK'}) - {result.duration_s:.1f}s[/dim]"
                    )
                else:
                    error_preview = (result.error or "Unknown error")[:100]
                    console.print(
                        f"{status_symbol} {progress_bar} {job.filename} "
                        f"[red][ERROR: {error_preview}][/red] - {result.duration_s:.1f}s"
                    )
            except Exception as exc:
                idx = job_to_index[id(job)]
                completed += 1
                error_str = str(exc)
                log.error(
                    "parallel_job_exception",
                    pdf=job.filename,
                    error=error_str,
                    completed=completed,
                    total=len(jobs),
                    exc_info=True,
                )
                # Console output for exception
                progress_bar = f"[bold][{completed}/{len(jobs)}][/bold]"
                error_preview = error_str[:100]
                console.print(
                    f"[red]✗[/red] {progress_bar} {job.filename} "
                    f"[red][EXCEPTION: {error_preview}][/red]"
                )
                # Create a failed result for this job
                results_by_index[idx] = RunResult(
                    job=job,
                    success=False,
                    duration_s=0.0,
                    error=error_str,
                )

    # Return results in original job order
    return [results_by_index[i] for i in range(len(jobs))]


if __name__ == "__main__":
    sys.exit(main())
