"""
extractor.py
Calls Azure OpenAI to extract structured data from an expense PDF.
Returns parsed JSON result and raw LLM metrics.
"""

import base64
import json
import logging
from pathlib import Path

from openai import OpenAI

from config import Config
from metrics import MetricsTimer, build_metrics, LLMMetrics, record_metrics

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a data extraction assistant.
CRITICAL RULES:
- Return ALL names, emails, IDs, and personal data exactly as they appear in the document.
- Do NOT redact, mask, anonymize, or replace any value with [REDACTED] or similar placeholders.
- Do NOT add disclaimers, warnings, or notes about personal data.
- Return ONLY raw JSON. No markdown, no code fences, no explanation text before or after.
"""

EXTRACTION_PROMPT = """
You are an expert in financial document extraction and reconciliation.
Extract structured data from the document and return FIVE tables in STRICT JSON format.

IMPORTANT: Return ALL names, emails, employee IDs, approver names exactly as written. Never redact anything.

========================
TABLE 1: transactions
========================
Columns (MANDATORY):
- transaction_id (create unique id like txn_1, txn_2)
- transaction_date
- expense_type
- business_purpose
- vendor_description
- payment_type
- amount
- cost_center
- project
- attendees
- comments

========================
TABLE 2: employee_report
========================
Columns (MANDATORY):
- employee_name
- employee_id
- report_id
- report_date
- approval_status
- payment_status
- currency
- report_total
- personal_expenses
- total_amount_claimed
- amount_approved
- amount_due_employee
- amount_due_company_card
- total_paid_by_company
- amount_due_from_employee
- total_paid_by_employee

========================
TABLE 3: approval_log
========================
Columns (MANDATORY):
- date
- approver_name
- status
- note

========================
TABLE 4: receipts
========================
Columns (MANDATORY):
- receipt_id (create unique id like rcp_1, rcp_2)
- order_id (if available, else null)
- date
- vendor
- amount (final amount including tax and tips etc)
- summary (detailed summary of each item in invoice and all extra information)

========================
TABLE 5: reconciliation
========================
Columns (MANDATORY):
- transaction_id
- receipt_id
- match_status (matched / unmatched)
- confidence (high / medium / low)

========================
RULES
========================
- Extract ALL transactions from transaction table section
- Extract employee_report as a SINGLE object
- Extract ALL receipts from receipt text blocks
- Do NOT mix transactions and receipts
- Normalize vendor names (e.g., SWEETGREEN MIDTOWN → Sweetgreen)
- Some fields may be split across multiple lines; reconstruct full values
- If any field is missing → return null
- Strictly add all transactions; do not miss any; avoid duplicates

RECONCILIATION LOGIC:
- Match transactions to receipts using amount (primary), date (exact or near), vendor similarity
- All match → matched (high confidence)
- Partial match → matched (medium/low)
- No match → unmatched

========================
OUTPUT FORMAT (STRICT JSON ONLY)
========================
{
  "transactions": [...],
  "employee_report": {...},
  "approval_log": [...],
  "receipts": [...],
  "reconciliation": [...]
}
"""


# ─────────────────────────────────────────────────────────────
# Cache helpers
# ─────────────────────────────────────────────────────────────

def _cache_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".cache.json")


def _load_cache(pdf_path: Path) -> dict | None:
    cp = _cache_path(pdf_path)
    if cp.exists():
        logger.info("Cache hit — loading from '%s'", cp)
        with cp.open(encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_cache(pdf_path: Path, data: dict) -> None:
    cp = _cache_path(pdf_path)
    with cp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.debug("Cache saved to '%s'", cp)


# ─────────────────────────────────────────────────────────────
# Response parsing
# ─────────────────────────────────────────────────────────────

def _parse_response_text(raw: str) -> dict:
    """Strip markdown fences and parse JSON."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────
# Main extraction function
# ─────────────────────────────────────────────────────────────

def extract(pdf_path: Path, cfg: Config) -> tuple[dict, LLMMetrics]:
    """
    Extract structured data from a PDF.

    Returns:
        (result_dict, metrics)
    Raises:
        Exception on unrecoverable LLM or parse errors (metrics still recorded).
    """
    # --- Cache shortcut ---
    if cfg.cache_enabled:
        cached = _load_cache(pdf_path)
        if cached is not None:
            dummy_metrics = build_metrics(
                pdf_file=pdf_path.name, model=cfg.model,
                input_tokens=0, output_tokens=0,
                cost_per_1k_input=cfg.cost_per_1k_input,
                cost_per_1k_output=cfg.cost_per_1k_output,
                latency=0.0, status="cache_hit",
            )
            return cached, dummy_metrics

    # --- Read & encode PDF ---
    logger.info("Reading PDF: %s", pdf_path.name)
    with pdf_path.open("rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    # --- Call LLM ---
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

    logger.info("Calling model '%s' for '%s'", cfg.model, pdf_path.name)
    with MetricsTimer() as timer:
        try:
            response = client.responses.create(
                model=cfg.model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_file",
                                "filename": pdf_path.name,
                                "file_data": f"data:application/pdf;base64,{b64}",
                            },
                            {"type": "input_text", "text": EXTRACTION_PROMPT},
                        ],
                    },
                ],
            )
        except Exception as exc:
            metrics = build_metrics(
                pdf_file=pdf_path.name, model=cfg.model,
                input_tokens=0, output_tokens=0,
                cost_per_1k_input=cfg.cost_per_1k_input,
                cost_per_1k_output=cfg.cost_per_1k_output,
                latency=timer.elapsed, status="error",
                error_message=str(exc),
            )
            record_metrics(metrics)
            raise

    # --- Extract token usage ---
    usage = getattr(response, "usage", None)
    input_tokens  = getattr(usage, "input_tokens",  0) if usage else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0

    # --- Parse JSON ---
    try:
        result = _parse_response_text(response.output_text)
    except (json.JSONDecodeError, AttributeError) as exc:
        metrics = build_metrics(
            pdf_file=pdf_path.name, model=cfg.model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_per_1k_input=cfg.cost_per_1k_input,
            cost_per_1k_output=cfg.cost_per_1k_output,
            latency=timer.elapsed, status="parse_error",
            error_message=str(exc),
        )
        record_metrics(metrics)
        raise

    # --- Build & record success metrics ---
    metrics = build_metrics(
        pdf_file=pdf_path.name, model=cfg.model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cost_per_1k_input=cfg.cost_per_1k_input,
        cost_per_1k_output=cfg.cost_per_1k_output,
        latency=timer.elapsed,
    )
    record_metrics(metrics)

    logger.info(
        "Extraction complete — tokens: %d in / %d out | cost: $%.4f | %.1fs",
        input_tokens, output_tokens, metrics.cost_usd, timer.elapsed,
    )

    # --- Cache result ---
    _save_cache(pdf_path, result)

    return result, metrics
