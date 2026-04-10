#!/usr/bin/env python3
"""
main.py — AEA Concur Process
CLI entry point only. No business logic here.

Usage:
  python main.py                         # process all files
  python main.py --period JAN_2026       # filter by period
  python main.py --amex  path/to/amex/   # override input folders
  python main.py --concur path/to/concur/

Exit codes:
  0 = all approved
  1 = one or more flagged
  2 = config error
  3 = pipeline error
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="AEA Concur — AMEX reconciliation + Concur validation"
    )
    ap.add_argument("--period",  default="",  help="Filter by period string (e.g. JAN_2026)")
    ap.add_argument("--amex",    default=None, type=Path, help="AMEX input folder override")
    ap.add_argument("--concur",  default=None, type=Path, help="Concur input folder override")
    ap.add_argument("--output",  default=None, type=Path, help="Output folder override")
    ap.add_argument("--env",     default=None, choices=["development", "test", "production"])
    args = ap.parse_args()

    # Apply CLI overrides to environment before settings are loaded
    if args.env:
        os.environ["APP_ENV"] = args.env
    if args.amex:
        os.environ["AMEX_INPUT_FOLDER"] = str(args.amex)
    if args.concur:
        os.environ["CONCUR_INPUT_FOLDER"] = str(args.concur)
    if args.output:
        os.environ["OUTPUT_FOLDER"] = str(args.output)

    from shared.exceptions import AEAConcurError, ConfigurationError
    from pipeline.run import PipelineRun

    try:
        run     = PipelineRun(period=args.period)
        summary = run.execute()
    except ConfigurationError as exc:
        print(f"\n❌ Config error: {exc}", file=sys.stderr)
        return 2
    except AEAConcurError as exc:
        print(f"\n❌ Pipeline error: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130

    # Exit 1 if any records were flagged
    if summary.flagged > 0:
        print(
            f"\n🚩 {summary.flagged} cardholder(s) flagged for review. "
            "See outputs/reports/ for details.",
            file=sys.stderr,
        )
        return 1

    print(f"\n✅ All {summary.approved} cardholder(s) approved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
