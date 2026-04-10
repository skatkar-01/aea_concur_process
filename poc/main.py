"""
main.py
=======
Run Claude + Gemini on a PDF, print cost comparison, save results to JSON.

Install
-------
  pip install anthropic google-genai

Env vars
--------
  AZURE_CLAUDE_ENDPOINT   https://<your-resource>.services.ai.azure.com
  AZURE_CLAUDE_KEY        your Azure AI Foundry key
  GEMINI_API_KEY          your Google AI Studio key (free at aistudio.google.com)

Run
---
  python main.py transactions.pdf
  python main.py transactions.pdf --output results.json
  python main.py transactions.pdf --model gemini        # Gemini only (free, no Azure needed)
  python main.py transactions.pdf --model claude        # Claude only
"""

import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

from extractors import (
    extract_with_claude,
    extract_with_gemini,
    CLAUDE_PRICING,
    GEMINI_PRICING,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv optional — env vars can still be set manually
 

# =============================================================================
# COMPARISON PRINTER
# =============================================================================

def print_comparison(claude: dict, gemini: dict):
    """Print side-by-side result and cost comparison to terminal."""
    W  = 56
    C  = 24
    print(f"\n{'─' * W}")
    print("  COMPARISON")
    print(f"{'─' * W}")

    def row(label, c_val, g_val):
        print(f"  {label:<18}  {str(c_val):<{C}}  {g_val}")

    row("",              "Claude Haiku 4.5",          "Gemini 2.0 Flash")
    row("",              "Azure AI Foundry",           "Google AI (free)")
    print(f"  {'─'*18}  {'─'*C}  {'─'*C}")

    # Status
    c_status = "OK" if not claude["error"] else f"ERR — {claude['error'][:30]}"
    g_status = "OK" if not gemini["error"] else f"ERR — {gemini['error'][:30]}"
    row("Status",        c_status,                     g_status)
    row("Latency",       f"{claude['latency_sec']}s",  f"{gemini['latency_sec']}s")

    print(f"  {'─'*18}  {'─'*C}  {'─'*C}")

    # Tokens
    row("Input tokens",  f"{claude['input_tokens']:,}",  f"{gemini['input_tokens']:,}")
    row("Output tokens", f"{claude['output_tokens']:,}", f"{gemini['output_tokens']:,}")

    print(f"  {'─'*18}  {'─'*C}  {'─'*C}")

    # Cost
    cp = claude["pricing"]
    gp = gemini["pricing"]
    row("Input cost",    f"${cp.get('input_cost',  0):.6f}",  "$0.000000 (free)")
    row("Output cost",   f"${cp.get('output_cost', 0):.6f}",  "$0.000000 (free)")
    row("TOTAL COST",    f"${cp.get('total_cost',  0):.6f}",  "$0.000000")
    row("Rate",
        f"${CLAUDE_PRICING['input_per_1m']}/${CLAUDE_PRICING['output_per_1m']} per 1M",
        gp.get("free_limits", "FREE"))

    print(f"  {'─'*18}  {'─'*C}  {'─'*C}")

    # Extraction counts
    row("Transactions",  len(claude["transactions"]),   len(gemini["transactions"]))
    row("Receipts",      len(claude["receipts"]),       len(gemini["receipts"]))

    print(f"{'─' * W}")

    # ── Extrapolated cost table ────────────────────────────────────────────────
    # Use actual input/output tokens to scale per page
    i_tok = claude["input_tokens"]
    o_tok = claude["output_tokens"]

    # Estimate pages processed: Claude docs say ~2000 tokens per PDF page average
    est_pages = max(1, i_tok // 2000)

    print(f"\n  COST EXTRAPOLATION  (Claude Haiku 4.5 — est. ~{est_pages} page/s processed)")
    print(f"  {'Pages':<8}  {'Input tokens':<16}  {'Output tokens':<16}  {'Cost (USD)'}")
    print(f"  {'─'*6:<8}  {'─'*14:<16}  {'─'*14:<16}  {'─'*12}")

    for n_pages in [10, 50, 100, 500, 1000]:
        scale   = n_pages / est_pages
        i_scale = int(i_tok * scale)
        o_scale = int(o_tok * scale)
        cost    = round(
            (i_scale / 1_000_000) * CLAUDE_PRICING["input_per_1m"] +
            (o_scale / 1_000_000) * CLAUDE_PRICING["output_per_1m"],
            4,
        )
        print(f"  {n_pages:<8}  {i_scale:>14,}  {o_scale:>14,}  ${cost:.4f}")

    print(f"\n  Gemini 2.0 Flash: $0.0000 for all page counts on free tier\n")


# =============================================================================
# JSON SAVER
# =============================================================================

def save_results(claude: dict, gemini: dict, output_path: str, pdf_path: str):
    """Save both model results to a single JSON file."""

    # Remove raw_response from saved output to keep file clean
    # (add --raw flag later if needed)
    def clean(r: dict) -> dict:
        return {k: v for k, v in r.items() if k != "raw_response"}

    output = {
        "run_at":   datetime.now().isoformat(),
        "pdf_file": pdf_path,
        "claude":   clean(claude),
        "gemini":   clean(gemini),
    }

    Path(output_path).write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  Saved → {output_path}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Extract transactions and receipts from a PDF — Claude vs Gemini"
    )
    parser.add_argument(
        "pdf",
        help="Path to the PDF file",
    )
    parser.add_argument(
        "--output", "-o",
        default="extraction_results.json",
        help="Output JSON file path (default: extraction_results.json)",
    )
    parser.add_argument(
        "--model",
        choices=["both", "claude", "gemini"],
        default="both",
        help="Which model to run (default: both)",
    )
    args = parser.parse_args()

    # ── Validate PDF ──────────────────────────────────────────────────────────
    pdf_path = args.pdf
    if not Path(pdf_path).exists():
        sys.exit(f"Error: file not found — {pdf_path}")

    size_kb = Path(pdf_path).stat().st_size // 1024
    print(f"\nPDF    : {pdf_path}  ({size_kb} KB)")
    print(f"Time   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Models : {args.model}\n")

    # ── Run extractions ───────────────────────────────────────────────────────
    claude_result = {"model": "claude-haiku-4-5", "provider": "Azure AI Foundry",
                     "latency_sec": 0, "input_tokens": 0, "output_tokens": 0,
                     "pricing": {"input_cost": 0, "output_cost": 0, "total_cost": 0},
                     "transactions": [], "receipts": [], "raw_response": "", "error": "skipped"}

    gemini_result = {"model": "gemini-2.0-flash", "provider": "Google AI (free tier)",
                     "latency_sec": 0, "input_tokens": 0, "output_tokens": 0,
                     "pricing": {"input_cost": 0, "output_cost": 0, "total_cost": 0},
                     "transactions": [], "receipts": [], "raw_response": "", "error": "skipped"}

    if args.model in ("both", "claude"):
        print("Running Claude Haiku 4.5 (Azure AI Foundry)...")
        claude_result = extract_with_claude(pdf_path)
        if claude_result["error"]:
            print(f"  ERROR: {claude_result['error']}")
        else:
            print(f"  OK — {len(claude_result['transactions'])} transactions, "
                  f"{len(claude_result['receipts'])} receipts, "
                  f"{claude_result['latency_sec']}s, "
                  f"cost ${claude_result['pricing']['total_cost']:.6f}")

    if args.model in ("both", "gemini"):
        print("Running Gemini 2.0 Flash (Google free API)...")
        gemini_result = extract_with_gemini(pdf_path)
        if gemini_result["error"]:
            print(f"  ERROR: {gemini_result['error']}")
        else:
            print(f"  OK — {len(gemini_result['transactions'])} transactions, "
                  f"{len(gemini_result['receipts'])} receipts, "
                  f"{gemini_result['latency_sec']}s, "
                  f"cost $0.000000 (free)")

    # ── Comparison + save ─────────────────────────────────────────────────────
    if args.model == "both":
        print_comparison(claude_result, gemini_result)

    save_results(claude_result, gemini_result, args.output, pdf_path)


if __name__ == "__main__":
    main()
