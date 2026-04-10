"""
main.py
Entry point for the Expense Reconciliation Pipeline.

Reads all PDF files from INPUT_FOLDER, extracts structured data via Azure OpenAI,
and writes formatted Excel reports to OUTPUT_FOLDER.

Usage:
    python main.py
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

from config import load_config
from extractor import extract
from excel_writer import write_excel
from metrics import LLMMetrics

# ─────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────

def _setup_logging(log_level: str) -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8"),
    ]

    logging.basicConfig(level=log_level, format=fmt, datefmt=datefmt, handlers=handlers)


# ─────────────────────────────────────────────────────────────
# Output filename convention
# ─────────────────────────────────────────────────────────────

def _output_path(output_folder: Path, pdf_path: Path) -> Path:
    """
    Convention: {stem}_reconciliation_{YYYYMMDD_HHMMSS}.xlsx
    Example:    Baker_$906.50_reconciliation_20260326_143022.xlsx
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_stem = pdf_path.stem.replace(" ", "_")
    filename  = f"{safe_stem}_reconciliation_{timestamp}.xlsx"
    return output_folder / filename


# ─────────────────────────────────────────────────────────────
# Per-file processor
# ─────────────────────────────────────────────────────────────

def _process_file(pdf_path: Path, output_folder: Path, cfg) -> LLMMetrics | None:
    logger = logging.getLogger("pipeline")
    logger.info("─" * 60)
    logger.info("Processing: %s", pdf_path.name)

    try:
        result, metrics = extract(pdf_path, cfg)
    except Exception as exc:
        logger.error("Extraction failed for '%s': %s", pdf_path.name, exc, exc_info=True)
        return None

    try:
        out_path = _output_path(output_folder, pdf_path)
        write_excel(result, out_path)
    except Exception as exc:
        logger.error("Excel write failed for '%s': %s", pdf_path.name, exc, exc_info=True)
        return metrics

    logger.info(
        "Done ✓  |  tokens: %d in / %d out  |  cost: $%.4f  |  %.1fs  →  %s",
        metrics.input_tokens, metrics.output_tokens,
        metrics.cost_usd, metrics.latency_seconds,
        out_path.name,
    )
    return metrics


# ─────────────────────────────────────────────────────────────
# Run summary
# ─────────────────────────────────────────────────────────────

def _log_summary(all_metrics: list[LLMMetrics]) -> None:
    logger = logging.getLogger("pipeline")
    successful = [m for m in all_metrics if m and m.status in ("success", "cache_hit")]
    total_cost   = sum(m.cost_usd        for m in successful)
    total_tokens = sum(m.total_tokens    for m in successful)
    total_time   = sum(m.latency_seconds for m in successful)

    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("  Files processed : %d", len(all_metrics))
    logger.info("  Successful      : %d", len(successful))
    logger.info("  Failed          : %d", len(all_metrics) - len(successful))
    logger.info("  Total tokens    : %d", total_tokens)
    logger.info("  Total cost      : $%.4f", total_cost)
    logger.info("  Total time      : %.1fs", total_time)
    logger.info("=" * 60)


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────

def main() -> None:
    cfg = load_config()
    _setup_logging(cfg.log_level)

    logger = logging.getLogger("pipeline")
    logger.info("Expense Reconciliation Pipeline starting")
    logger.info("Input  folder : %s", cfg.input_folder)
    logger.info("Output folder : %s", cfg.output_folder)
    logger.info("Model         : %s", cfg.model)
    logger.info("Cache enabled : %s", cfg.cache_enabled)

    input_folder  = Path(cfg.input_folder)
    output_folder = Path(cfg.output_folder)

    if not input_folder.exists():
        logger.error("Input folder '%s' does not exist. Aborting.", input_folder)
        sys.exit(1)

    pdf_files = sorted(input_folder.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDF files found in '%s'. Nothing to do.", input_folder)
        sys.exit(0)

    logger.info("Found %d PDF file(s) to process", len(pdf_files))

    all_metrics = []
    for pdf_path in pdf_files:
        metrics = _process_file(pdf_path, output_folder, cfg)
        all_metrics.append(metrics)

    _log_summary(all_metrics)

    # Exit with error code if any file failed
    failed = sum(1 for m in all_metrics if m is None or m.status == "error")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
