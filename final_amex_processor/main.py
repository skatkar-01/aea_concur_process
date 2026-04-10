"""
main.py
────────
Entry point for the AMEX Statement Processor.

Usage:
    # Process all PDFs in the default input folder:
    python main.py

    # Process a single file:
    python main.py --file inputs/amex/BAKER_C_Feb_042026.pdf

    # Override folders:
    python main.py --input-dir /data/statements --output-dir /data/xlsx

    # Force-disable cache (always call API):
    python main.py --no-cache

    # Start Prometheus metrics server:
    python main.py --metrics
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich import box

from config.settings import get_settings
from src.pipeline import BatchResult, FileResult, process_batch, process_file
from utils.logging_config import configure_logging, get_logger
from utils.metrics import METRICS, start_metrics_server

console = Console()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AMEX Corporate Statement → XLSX Processor",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--file", "-f", type=Path, default=None,
        help="Process a single PDF instead of the whole input folder.",
    )
    p.add_argument(
        "--input-dir", "-i", type=Path, default=None,
        help="Override the input folder (batch mode).",
    )
    p.add_argument(
        "--output-dir", "-o", type=Path, default=None,
        help="Override the output folder.",
    )
    p.add_argument(
        "--no-cache", action="store_true",
        help="Disable JSON extraction cache (always call the API).",
    )
    p.add_argument(
        "--metrics", action="store_true",
        help="Start the Prometheus metrics HTTP server.",
    )
    p.add_argument(
        "--log-level", default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override LOG_LEVEL from environment.",
    )
    return p.parse_args()


# ── Rich summary ──────────────────────────────────────────────────────────────

def _print_summary(batch: BatchResult) -> None:
    table = Table(
        title="Processing Summary",
        box=box.ROUNDED,
        show_lines=True,
        header_style="bold white on dark_blue",
    )
    table.add_column("PDF",         style="cyan",  no_wrap=True)
    table.add_column("Status",      style="bold",  justify="center")
    table.add_column("Cardholders", justify="right")
    table.add_column("Transactions",justify="right")
    table.add_column("Duration",    justify="right")
    table.add_column("Output / Error", overflow="fold")

    for r in batch.results:
        if r.success:
            status  = "[green]✓ OK[/green]"
            ch_cnt  = str(r.statement.total_cardholders)  if r.statement else "—"
            txn_cnt = str(r.statement.total_transactions) if r.statement else "—"
            detail  = str(r.output_path) if r.output_path else "—"
        else:
            status  = "[red]✗ FAIL[/red]"
            ch_cnt  = "—"
            txn_cnt = "—"
            detail  = f"[red]{r.error}[/red]"

        table.add_row(
            r.pdf_path.name,
            status,
            ch_cnt,
            txn_cnt,
            f"{r.duration_s:.1f}s",
            detail,
        )

    console.print()
    console.print(table)
    console.print(
        f"\n[bold]Total:[/bold] {batch.total} file(s) — "
        f"[green]{len(batch.succeeded)} succeeded[/green], "
        f"[red]{len(batch.failed)} failed[/red]\n"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    """
    Returns:
        0 on full success, 1 if any file failed.
    """
    args     = _parse_args()
    settings = get_settings()

    # Apply CLI overrides before configuring logging
    if args.no_cache:
        # Monkey-patch the cached singleton (acceptable for CLI use)
        object.__setattr__(settings, "cache_enabled", False)  # type: ignore[arg-type]

    log_level = args.log_level or settings.log_level
    configure_logging(
        level=log_level,
        fmt=settings.log_format,
        log_dir=settings.log_dir,
    )
    log = get_logger("main")

    # Ensure all directories exist
    settings.ensure_dirs()

    # Optional metrics server
    if args.metrics or settings.metrics_enabled:
        start_metrics_server(settings.metrics_port)
        log.info("metrics_server_started", port=settings.metrics_port)

    log.info(
        "processor_startup",
        model=settings.azure_openai_model,
        cache=settings.cache_enabled,
        log_level=log_level,
    )

    t0 = time.perf_counter()

    # ── Run pipeline ──────────────────────────────────────────────────────────
    if args.file:
        result = process_file(
            pdf_path=args.file,
            output_dir=args.output_dir,
        )
        # Wrap single result in a BatchResult for uniform summary output
        from src.pipeline import BatchResult as BR
        batch = BR(results=[result])
    else:
        batch = process_batch(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
        )

    # ── Print summary ─────────────────────────────────────────────────────────
    _print_summary(batch)
    log.info(
        "processor_done",
        total=batch.total,
        succeeded=len(batch.succeeded),
        failed=len(batch.failed),
        wall_time_s=round(time.perf_counter() - t0, 2),
    )

    return 0 if not batch.failed else 1


if __name__ == "__main__":
    sys.exit(main())
