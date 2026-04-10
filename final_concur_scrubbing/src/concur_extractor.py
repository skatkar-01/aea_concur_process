"""
src/concur_extractor.py
────────────────────────
Extracts structured data from a Final Concur Report PDF using the
OpenAI Responses API.

Prompts and LLM call pattern taken verbatim from the uploaded extractor.py.
Returns (ConcurRecord, LLMMetrics) so the caller can log cost/token data.

5-table output mirrors the uploaded extraction schema:
  TABLE 1  transactions      → list[ConcurTransactionRow]
  TABLE 2  employee_report   → ConcurEmployeeReport
  TABLE 3  approval_log      → list[ConcurApprovalEntry]
  TABLE 4  receipts          → list[ConcurReceipt]
  TABLE 5  reconciliation    → list[ConcurReconEntry]
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI, OpenAIError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from config.settings import get_settings
from src.models import ConcurRecord
from utils.logging_config import get_logger
from utils.metrics import (
    METRICS, MetricsTimer, LLMMetrics,
    build_metrics, record_metrics, timed,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
# Prompts  —  taken verbatim from uploaded extractor.py
# ═══════════════════════════════════════════════════════════════

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
- comment             (single plain-English string capturing every issue or discrepancy
                       for this transaction — see Comment Rules below)

COMMENT RULES for reconciliation.comment:
1. "Good"                        → receipt is present and correct; set match_status=matched, confidence=high
3. "Wrong receipt for <Vendor> <Amount> attached"
                              → a receipt exists but belongs to a different transaction;
                                set match_status=unmatched, confidence=low
4. "Missing <Vendor> <Amount> attached"
   "Missing receipt for <Vendor> <Amount> attached"
                              → no receipt found for this transaction;
                                set match_status=unmatched, confidence=low
5. Multi-line cells containing multiple issues (quoted block with newlines)
                              → split into individual comments; if a transaction has more than one
                                issue, duplicate the reconciliation row once per comment,
                                incrementing a sub-index only when necessary to preserve all comments.

For ALL cases:
- reconciliation_comment = the verbatim comment string from the document
- If a comment references a vendor and amount, verify they match the linked transaction
  and flag any discrepancy in the comment field as-is.
- Copy vendor name and amount exactly as written in the source document.
- If there is no issue → set comment to null
  
========================
Table 6:REPORT SUMMARY
========================
Two aggregate comment fields that roll up all issues found across the entire report.

Fields (MANDATORY):
- reconciliation_comment  (string)
  → One consolidated comment listing ALL receipt issues across ALL transactions.
  → Format: bullet-style list as a single string, each issue on a new line starting with "- "
  → Include: wrong receipts, missing receipts, N/A items.
  → Example:
     "- Wrong receipt for Delta -$360.09 attached
      - Missing Burgers and Bourbon $57.64
      - Wrong receipt for JetBlue -$83.00 & -$293.09 attached"
  → If no issues → "No receipt discrepancies found."

- approval_comment  (string)
  → One consolidated comment listing ALL approval issues across the report.
  → Include: missing approvers, pending approvals, rejected steps, unapproved amounts.
  → Format: bullet-style list as a single string, each issue on a new line starting with "- "
  → Example:
     "- Approval missing from Finance Manager
      - VP approval pending for expenses above $500
      - Report submitted but not yet approved"
  → If no issues → "All approvals complete."

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
  "reconciliation": [...],
  "report_summary": {
    "reconciliation_comment": "- Wrong receipt for Delta -$360.09 attached; - Missing Burgers and Bourbon $57.64",
    "approval_comment": "- Approval missing from Finance Manager; - VP approval pending"
  } 
}
"""


# ═══════════════════════════════════════════════════════════════
# Cache helpers  (SHA-256 content-addressed, same as AMEX extractor)
# ═══════════════════════════════════════════════════════════════

def _pdf_to_base64(pdf_path: Path) -> str:
    with open(pdf_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _cache_key(pdf_path: Path) -> str:
    sha = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _cache_path(pdf_path: Path, cache_dir: Path) -> Path:
    return cache_dir / f"concur__{pdf_path.stem}__{_cache_key(pdf_path)[:12]}.json"


def _load_cache(cache_file: Path) -> dict[str, Any] | None:
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("concur_cache_read_failed", path=str(cache_file), error=str(exc))
    return None


def _write_cache(cache_file: Path, data: dict[str, Any]) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as exc:
        logger.warning("concur_cache_write_failed", path=str(cache_file), error=str(exc))


def _parse_response_text(raw: str) -> dict:
    """Strip markdown fences and parse JSON with error recovery.
    
    Attempts standard JSON parsing first, then tries recovery strategies:
    - Removes unterminated strings (last incomplete value)
    - Closes unclosed objects/arrays
    """
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    
    # Try standard JSON parsing first
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("json_parse_failed_trying_recovery", error=str(e), char_pos=e.pos)
        
        # Recovery strategy: remove likely unterminated string and close structure
        if '"' in raw:
            # Find last quote and truncate there, then close with }
            last_quote = raw.rfind('"')
            if last_quote > 0:
                # Check if there's content after last quote (likely unterminated value)
                after_quote = raw[last_quote+1:].strip()
                if after_quote and not after_quote.startswith((',', '}', ']')):
                    # Likely unterminated string - truncate and close
                    raw = raw[:last_quote] + '"'
                    # Count open braces/brackets and close them
                    open_braces = raw.count('{') - raw.count('}')
                    open_brackets = raw.count('[') - raw.count(']')
                    raw += ']' * open_brackets + '}' * open_braces
                    logger.warning("json_recovery_applied", truncated_at=last_quote, closed_braces=open_braces, closed_brackets=open_brackets)
        
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e2:
            logger.error("json_recovery_failed", original_error=str(e), recovery_error=str(e2))
            raise


# ═══════════════════════════════════════════════════════════════
# API call with retry
# ═══════════════════════════════════════════════════════════════

def _make_retry_decorator(max_attempts: int, wait_base: int):
    return retry(
        retry=retry_if_exception_type(OpenAIError),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=wait_base, min=wait_base, max=60),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )


def _call_api(client: OpenAI, b64: str, model: str, filename: str):
    """
    OpenAI Responses API call — input_file + input_text pattern,
    verbatim from uploaded extractor.py.
    Returns the raw response object so the caller can read usage tokens.
    """
    try:
        return client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "filename": filename,
                            "file_data": f"data:application/pdf;base64,{b64}",
                        },
                        {"type": "input_text", "text": EXTRACTION_PROMPT},
                    ],
                },
            ],
        )
    except OpenAIError as exc:
            # Print full error body from Azure
            print(f"[API ERROR] status={getattr(exc, 'status_code', '?')}")
            print(f"[API ERROR] body={getattr(exc, 'body', '?')}")
            print(f"[API ERROR] message={str(exc)}")
            raise


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def extract_concur_record(pdf_path: Path) -> tuple[ConcurRecord, LLMMetrics]:
    """
    Extract and validate all 5 tables from a Concur expense report PDF.

    Returns:
        (ConcurRecord, LLMMetrics)  — record for reconciliation,
                                      metrics for cost/token logging.

    Steps:
      1. Cache check (SHA-256 content-addressed)
      2. Encode PDF as base64
      3. Call OpenAI Responses API (input_file + input_text)
      4. Read token usage from response.usage
      5. Parse JSON → 5-table dict
      6. Validate with Pydantic ConcurRecord model
      7. Write cache
      8. Build and record LLMMetrics → logs/metrics.jsonl

    Raises:
        FileNotFoundError  — pdf_path does not exist
        json.JSONDecodeError — model returned unparseable JSON
        pydantic.ValidationError — extracted data fails schema
        openai.OpenAIError — all retries exhausted
    """
    settings = get_settings()

    if not pdf_path.exists():
        raise FileNotFoundError(f"Concur PDF not found: {pdf_path}")

    log = logger.bind(pdf=pdf_path.name)
    log.info("concur_extraction_start")

    # ── 1. Cache check ────────────────────────────────────────────────────────
    if settings.cache_enabled:
        cp = _cache_path(pdf_path, settings.cache_dir)
        cached = _load_cache(cp)
        if cached is not None:
            log.info("concur_cache_hit", cache_file=cp.name)
            dummy = build_metrics(
                pdf_file=pdf_path.name,
                model=settings.azure_openai_model,
                input_tokens=0, output_tokens=0,
                cost_per_1k_input=settings.cost_per_1k_input,
                cost_per_1k_output=settings.cost_per_1k_output,
                latency=0.0, status="cache_hit",
            )
            return ConcurRecord.model_validate(cached), dummy

    # ── 2. Encode PDF ─────────────────────────────────────────────────────────
    log.debug("encoding_pdf")
    b64 = _pdf_to_base64(pdf_path)

    # ── 3. API call with MetricsTimer (verbatim pattern from uploaded code) ───
    # Use httpx.Timeout to properly set timeouts for large PDF processing
    # timeout=(connection, read, write, pool) — all set to handle 20+ minute operations
    timeout_config = httpx.Timeout(
        timeout=settings.api_timeout_seconds,
        connect=60,                              # 60s to establish connection
        read=settings.api_timeout_seconds,       # Full timeout for reading response
        write=30,                                # 30s to send data
        pool=10,                                 # 10s to acquire connection from pool
    )
    
    client = OpenAI(
        api_key=settings.azure_openai_api_key,
        base_url=settings.azure_openai_base_url,
        max_retries=0,
        http_client=httpx.Client(timeout=timeout_config),
    )
    try:
        retried_call = _make_retry_decorator(
            settings.max_retries, settings.retry_wait_seconds
        )(_call_api)
    except OpenAIError as exc:
            METRICS.api_failures.inc()
            log.error("api_call_failed", error=str(exc))
            # ADD THESE TWO LINES:
            import traceback
            traceback.print_exc()
            raise

    with MetricsTimer() as timer:
        try:
            response = retried_call(client, b64, settings.azure_openai_model, pdf_path.name)
        except OpenAIError as exc:
            METRICS.api_failures.inc()
            metrics = build_metrics(
                pdf_file=pdf_path.name,
                model=settings.azure_openai_model,
                input_tokens=0, output_tokens=0,
                cost_per_1k_input=settings.cost_per_1k_input,
                cost_per_1k_output=settings.cost_per_1k_output,
                latency=timer.elapsed, status="error",
                error_message=str(exc),
            )
            record_metrics(metrics)
            log.error("concur_api_failed", error=str(exc))
            raise

    # ── 4. Read token usage ───────────────────────────────────────────────────
    usage         = getattr(response, "usage", None)
    input_tokens  = getattr(usage, "input_tokens",  0) if usage else 0
    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0

    # ── 5. Parse JSON ─────────────────────────────────────────────────────────
    try:
        raw_dict: dict[str, Any] = _parse_response_text(response.output_text)
    except (json.JSONDecodeError, AttributeError) as exc:
        metrics = build_metrics(
            pdf_file=pdf_path.name,
            model=settings.azure_openai_model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_per_1k_input=settings.cost_per_1k_input,
            cost_per_1k_output=settings.cost_per_1k_output,
            latency=timer.elapsed, status="parse_error",
            error_message=str(exc),
        )
        record_metrics(metrics)
        log.error("concur_json_parse_failed", error=str(exc))
        raise

    # ── 6. Validate ───────────────────────────────────────────────────────────
    record = ConcurRecord.model_validate(raw_dict)

    # ── 7. Cache ──────────────────────────────────────────────────────────────
    if settings.cache_enabled:
        _write_cache(cp, raw_dict)

    # ── 8. Build and record LLMMetrics ────────────────────────────────────────
    metrics = build_metrics(
        pdf_file=pdf_path.name,
        model=settings.azure_openai_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_per_1k_input=settings.cost_per_1k_input,
        cost_per_1k_output=settings.cost_per_1k_output,
        latency=timer.elapsed,
    )
    record_metrics(metrics)
    METRICS.files_processed.inc()

    log.info(
        "concur_extraction_complete",
        name=record.cardholder_name,
        amount=record.amount_submitted,
        transactions=len(record.transactions),
        receipts=len(record.receipts),
        matched=record.matched_count,
        unmatched=record.unmatched_count,
        tokens_in=input_tokens,
        tokens_out=output_tokens,
        cost_usd=round(metrics.cost_usd, 4),
        latency_s=timer.elapsed,
    )

    return record, metrics
