"""
main.py
Entry point for the SAP Concur Report Extractor.

Usage:
    python main.py
    python main.py --input ./reports --output ./results --provider gemini
    python main.py --input ./reports --provider claude --model claude-3-5-haiku-20241022
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from config import AppConfig
from clients.client_factory import create_client
from extractor.concur_extractor import ConcurExtractor
from processor.output_processor import OutputProcessor
from utils.cost_tracker import CostTracker
from utils.file_utils import list_report_files
from utils.logger import get_logger, setup_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SAP Concur Report Extractor — LLM-powered structured data extraction"
    )
    parser.add_argument("--input",    default=None, help="Input folder (overrides .env INPUT_FOLDER)")
    parser.add_argument("--output",   default=None, help="Output folder (overrides .env OUTPUT_FOLDER)")
    parser.add_argument("--provider", default=None, help="LLM provider: gemini | claude | azure_openai")
    parser.add_argument("--model",    default=None, help="Model name override")
    parser.add_argument("--log-level",default=None, help="Log level: DEBUG | INFO | WARNING | ERROR")
    parser.add_argument("--n",default=None, help="Number of files to process (for testing)")
    return parser.parse_args()


def apply_arg_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    """CLI flags take precedence over .env values."""
    if args.input:
        config.paths.input_folder = args.input
    if args.output:
        config.paths.output_folder = args.output
    if args.provider:
        config.llm.provider = args.provider.lower()
    if args.model:
        config.llm.model = args.model
    if args.log_level:
        config.log_level = args.log_level.upper()
    if args.n:
        config.n = int(args.n)
    return config


def run(config: AppConfig) -> int:
    """
    Main pipeline.
    Returns exit code: 0 = success, 1 = partial failure, 2 = total failure.
    """
    logger = get_logger("main")

    logger.info(
        "run_start",
        provider=config.llm.provider,
        model=config.llm.model,
        input_folder=config.paths.input_folder,
        output_folder=config.paths.output_folder,
    )

    # ── 1. Discover input files ────────────────────────────────────────────
    try:
        files = list_report_files(config.paths.input_folder)
    except FileNotFoundError as exc:
        logger.error("input_folder_missing", error=str(exc))
        print(f"\n❌  {exc}\n   Create the folder or pass --input <path>\n")
        return 2

    if not files:
        logger.warning("no_input_files", folder=config.paths.input_folder)
        print(f"\n⚠️  No supported files found in '{config.paths.input_folder}'.\n")
        return 0

    print(f"\n📂  Found {len(files)} file(s) in '{config.paths.input_folder}'\n")

    # ── 2. Build shared components ─────────────────────────────────────────
    pricing   = config.get_model_pricing()
    tracker   = CostTracker(
        pricing=pricing,
        provider=config.llm.provider,
        model=config.llm.model,
        cost_report_folder=config.paths.cost_report_folder,
    )
    client    = create_client(config.llm)
    extractor = ConcurExtractor(client=client, cost_tracker=tracker)
    processor = OutputProcessor(output_folder=config.paths.output_folder)

    # ── 3. Process each file ───────────────────────────────────────────────
    results: dict[str, bool] = {}
    run_start = time.perf_counter()
    if config.n==0:
        config.n = len(files)
    for idx, file_path in enumerate(files[:config.n], start=1):
        print(f"  [{idx}/{len(files)}] Processing: {file_path.name} ...", end=" ", flush=True)

        report = extractor.extract(file_path)

        if report.is_valid:
            saved = processor.save(report)
            results[file_path.name] = True
            print(f"✅  → {report.output_prefix}")
            for table, path in saved.items():
                logger.debug("output_file", table=table, path=str(path))
        else:
            results[file_path.name] = False
            print(f"❌  FAILED ({report.parse_error})")
            logger.error(
                "report_extraction_failed",
                file=file_path.name,
                reason=report.parse_error,
            )

    total_elapsed = time.perf_counter() - run_start

    # ── 4. Save cost report ────────────────────────────────────────────────
    cost_path = tracker.save_report()
    summary   = tracker.summary()

    # ── 5. Print final summary ─────────────────────────────────────────────
    succeeded = sum(v for v in results.values())
    failed    = len(results) - succeeded

    print("\n" + "─" * 55)
    print("  Run Summary")
    print("─" * 55)
    print(f"  Files processed : {len(results)}")
    print(f"  ✅ Succeeded     : {succeeded}")
    print(f"  ❌ Failed        : {failed}")
    print(f"  Total tokens    : {summary['total_tokens']:,}")
    print(f"  Total cost      : ${summary['total_cost_usd']:.6f} USD")
    print(f"  Elapsed         : {total_elapsed:.1f}s")
    print(f"  Cost report     : {cost_path}")
    print("─" * 55 + "\n")

    logger.info(
        "run_complete",
        succeeded=succeeded,
        failed=failed,
        total_cost_usd=summary["total_cost_usd"],
        total_tokens=summary["total_tokens"],
        elapsed_s=round(total_elapsed, 1),
    )

    if failed == len(results):
        return 2
    if failed > 0:
        return 1
    return 0


def main() -> None:
    args   = parse_args()
    config = AppConfig.from_env()
    config = apply_arg_overrides(config, args)

    # Logging must be set up before any logger is used
    setup_logging(log_level=config.log_level, log_folder=config.paths.log_folder)

    # Ensure required folders exist
    for folder in (
        config.paths.input_folder,
        config.paths.output_folder,
        config.paths.log_folder,
        config.paths.cost_report_folder,
    ):
        Path(folder).mkdir(parents=True, exist_ok=True)

    sys.exit(run(config))


if __name__ == "__main__":
    main()
