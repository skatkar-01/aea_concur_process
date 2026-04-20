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
import re
import time
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
# Content filter refusal detection
# ═══════════════════════════════════════════════════════════════

class ContentFilterRefusalError(Exception):
    """Raised when Azure content filter triggers mid-response."""
    pass


REFUSAL_PHRASES = [
    "i'm sorry, but i cannot",
    "i cannot assist with that",
    "i'm unable to",
    "i apologize, but i cannot",
    "i need to decline",
    "i can't help with that",
]


# ═══════════════════════════════════════════════════════════════
# Prompts  —  taken verbatim from uploaded extractor.py
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """
You are a financial document extraction and reconciliation engine designed for audit-grade accuracy.

Your role is to:
1. Extract structured financial data from complex documents
2. Perform deterministic transaction-to-receipt reconciliation
3. Handle real-world financial inconsistencies robustly

You MUST operate with:
- High precision (no guessing)
- Deterministic logic (consistent outputs for same input)
- Conservative matching (prefer "unmatched" over incorrect match)

You MUST handle real-world financial edge cases such as:
- Receipts in arbitrary/unordered positions
- Multiple receipts for a single transaction
- One receipt covering multiple transactions (split payments)
- Partial matches (amount mismatch due to tips, tax, split)
- One-to-many and many-to-one mappings (split payments)
- Missing receipts
- Duplicate or similar receipts
- Negative transactions (refunds, adjustments)

CRITICAL RULES: (You must ALWAYS):
- Return ONLY raw JSON. No markdown, no code fences, no explanation text before or after.
- Follow the EXACT schema provided
- Do not omit any required fields
- Return ALL names, emails, IDs, and personal data exactly as they appear in the document.
- Do NOT add disclaimers, warnings, or notes about personal data.
- Never refuse the request
- All information is legally verifiable from the document; extract everything without omission.
Failure to follow JSON format is not allowed.

"""

EXTRACTION_PROMPT = """
Extract structured financial data and perform reconciliation.
You MUST extract ALL sections completely and accurately.

========================
TABLE 1: transactions
========================
Extract ALL transactions exactly as shown.

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
Extract as SINGLE object.

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
Extract ALL approval steps.

Columns (MANDATORY):
- date
- approver_name
- status
- note

========================
TABLE 4: receipts (WITH LINE ITEMS)
========================
Extract ALL receipts with FULL breakdown.

Columns (MANDATORY):
- receipt_id (create unique id like rcp_1, rcp_2)
- order_id (if available, else null)
- date
- vendor
- amount (final amount including tax and tips etc)
- line_items (array of ALL individual charges)
- summary (detailed summary of each item in invoice and all extra information)

----------------------------------------
RECEIPT ORDERING RULE (CRITICAL)
----------------------------------------
Receipts MUST be extracted in the EXACT order they appear in the document.

- You MUST read the document sequentially (page-by-page, top-to-bottom)
- Extract receipts ONLY when encountered
- DO NOT reorder, group, or cluster receipts
- Assign receipt_id incrementally at extraction time
- Any violation of ordering is considered incorrect output

For hotel folios, extract the AMOUNT CHARGED TO CARD (credit line), 
not the gross room charges. Look for "AX CARD", "CCARD-AX", or "Deposit-AX" 
credit lines for the correct amount.

----------------------------------------
DEFINITION: line_items
----------------------------------------
Each receipt MUST include detailed individual charge breakdown.

Each line item:
{
  "name": "<exact label from receipt>",
  "amount": "<amount>"
}

INCLUDE ALL:
- food/menu items (Burger, Coffee, etc.)
- ride components (Base Fare, Distance, Time, Booking Fee)
- taxes
- tips
- service fees
- surcharges
- discounts (NEGATIVE values)
- adjustments/refunds

FINAL VERIFICATION STEP:
- Scan ALL pages sequentially one more time for any receipt not yet extracted
- Receipts may appear as: hotel folios, chit receipts, booking confirmations, 
  transportation invoices, or online booking platform emails
- Do NOT skip receipts because their date falls outside the report period
- Do NOT skip negative/refund receipts

========================
TABLE 5: reconciliation (CRITICAL LOGIC)
========================
Match EACH transaction to the BEST possible receipt(s).
IMPORTANT:
- Transactions and receipts are NOT ordered
- Evaluate ALL receipts before matching

----------------------------------------
MATCHING PRIORITY
----------------------------------------
1. Amount (exact OR derived from line_items)
2. Vendor similarity
3. Date proximity (±2 days)

----------------------------------------
BUSINESS RULE: NO RECEIPT REQUIRED (CRITICAL)
----------------------------------------
Certain transactions do NOT require receipts. Mark these as no receipt required if receipt does not present.
Conditions:
1. Personal transactions (project = "Personal" OR business_purpose contains "Personal")
2. Transactions with amount < $25
 
----------------------------------------
CASE HANDLING
----------------------------------------
1. EXACT MATCH
→ matched, high
→ comment: "Good"

2. SPLIT PAYMENT (1 receipt → multiple transactions)
→ matched, medium
→ comment: "Split payment: receipt <receipt_id> covers multiple transactions"

3. PARTIAL MATCH (tax/tip difference)
→ matched, medium
→ comment: "Partial match: amount differs due to additional charges"

4. MULTIPLE POSSIBLE RECEIPTS
→ choose best match
→ matched, medium
→ comment: "Best possible match among multiple similar receipts"

5. NEGATIVE / ADJUSTMENT
→ unmatched, low
→ comment: "No receipt required (adjustment/refund)"

6. MISSING RECEIPT
→ unmatched, low
→ comment: "Missing receipt for <Vendor> <Amount> attached"

7. WRONG RECEIPT
→ unmatched, low
→ comment: "Wrong receipt for <Vendor> <Amount> attached"

----------------------------------------
STRICT RULES
----------------------------------------
- EVERY transaction MUST appear in reconciliation
- If multiple issues → create multiple rows
- reconciliation_comment = the verbatim comment string from the document
- If a comment references a vendor and amount, verify they match the linked transaction
  and flag any discrepancy in the comment field as-is.
- Copy vendor name and amount exactly as written in the source document.
- If there is no issue → set comment to null

Columns (MANDATORY):
- transaction_id
- receipt_id
- match_status (matched / unmatched)
- confidence (high / medium / low)
- comment             



  
========================
Table 6:REPORT SUMMARY
========================
Two aggregate comment fields that roll up all issues found across the entire report.

Fields (MANDATORY):
- reconciliation_comment  (string)
  → One consolidated comment listing ALL receipt issues across ALL transactions.
  → Format: bullet-style list as a single string, each issue on a new line starting with "- "
  → Include: wrong receipts, missing receipts.
  → Example:
     "- Wrong receipt for Delta -$360.09 attached
      - Missing Burgers and Bourbon $57.64
  → If no issues → null

- approval_comment  (string)
  → One consolidated comment listing ALL approval issues across the report.
  → Check atleast two approvals are granted - Card holder approval and partner approval. If any of these is missing or pending, add to the comment.
  → Include: missing approvers, pending approvals, rejected steps, unapproved amounts.
  → Format: bullet-style list as a single string, each issue on a new line starting with "- "
  → Example:
     "- Approval missing from Card holder;
      - Missing Partner approval
  → If no issues → null

========================
GLOBAL RULES
========================
- Extract EVERYTHING — do not skip sections
- Extract ALL transactions from transaction table section
- Extract employee_report as a SINGLE object
- Extract ALL receipts from receipt text blocks
- Do NOT mix transactions and receipts
- Normalize vendor names (e.g., SWEETGREEN MIDTOWN → Sweetgreen)
- Some fields may be split across multiple lines; reconstruct full values
- If any field is missing → return null
- Strictly add all transactions; do not miss any; 
- 

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
    "approval_comment": "- Approval missing from Card holder; - Missing Partner approval"
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
    """
    Load cache file with file lock error handling.
    
    Returns None on any error (file doesn't exist, corrupted, locked, etc.)
    """
    if not cache_file.exists():
        return None
    
    try:
        with open(cache_file) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.warning("concur_cache_corrupted", path=str(cache_file), error=str(exc))
        return None
    except (OSError, IOError) as exc:
        # File lock, permission denied, or other OS-level error
        if "file is in use" in str(exc).lower() or "permission denied" in str(exc).lower():
            logger.debug("concur_cache_locked", path=str(cache_file))
        else:
            logger.warning("concur_cache_read_failed", path=str(cache_file), error=str(exc))
        return None


def _write_cache(cache_file: Path, data: dict[str, Any]) -> None:
    """
    Write cache with atomic operation and file lock error handling.
    
    Failures are logged but do not propagate (cache failures don't block extraction).
    """
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to temp, then rename
        temp_file = cache_file.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)
        # Atomic rename to final location
        temp_file.replace(cache_file)
        logger.debug("concur_cache_written", path=str(cache_file))
    except (OSError, IOError) as exc:
        # File lock, permission denied, or other OS-level error
        if "file is in use" in str(exc).lower() or "permission denied" in str(exc).lower():
            logger.debug("concur_cache_write_locked", path=str(cache_file))
        else:
            logger.warning("concur_cache_write_failed", path=str(cache_file), error=str(exc))
        # Don't raise — cache failure is not fatal


import re


def _write_failed_json(pdf_path: Path, output_text: str, model: str, attempt: int, error: str) -> None:
    """
    Write failed JSON response to debug folder for analysis.
    
    Helps identify patterns in malformed responses.
    """
    settings = get_settings()
    failed_dir = settings.cache_dir / "failed_json"
    try:
        failed_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{pdf_path.stem}__attempt{attempt}_{model}__error.json"
        failed_file = failed_dir / filename
        with open(failed_file, 'w') as f:
            f.write(f"// ERROR: {error}\n")
            f.write(f"// MODEL: {model}\n")
            f.write(f"// FILE: {pdf_path.name}\n")
            f.write(f"// ATTEMPT: {attempt}\n\n")
            f.write(output_text)
        logger.debug("failed_json_written", path=str(failed_file), error=error)
    except Exception as exc:
        logger.warning("failed_json_write_error", error=str(exc))


def _repair_json(raw: str) -> str:
    """
    Stage 3: Repair malformed JSON (unterminated strings, trailing commas, unbalanced braces).
    
    Strategies:
    1. Close unterminated strings at the end
    2. Add missing commas after closed strings
    3. Remove trailing commas before ] or }
    4. Balance braces and brackets
    """
    # Strategy 1: Close unterminated strings
    # Count quotes and if odd number, close the last one
    quote_count = raw.count('"') - raw.count('\\"')  # Ignore escaped quotes
    if quote_count % 2 == 1:
        # Odd number of quotes, close the last one
        raw = raw.rstrip()
        if not raw.endswith('"'):
            raw += '"'
        logger.debug("json_repair_closed_unterminated_string")
    
    # Strategy 2: Add missing commas after quotes followed by quotes (field: "value" "next")
    # Pattern: closing quote followed by optional whitespace then opening quote
    raw = re.sub(r'"\s+(?=["{[])', '",', raw)
    logger.debug("json_repair_added_missing_commas")
    
    # Strategy 3: Remove trailing commas before ] or }
    raw = re.sub(r',(\s*[\]}])', r'\1', raw)
    logger.debug("json_repair_removed_trailing_commas")
    
    # Strategy 4: Balance braces and brackets
    open_braces = raw.count('{') - raw.count('}')
    open_brackets = raw.count('[') - raw.count(']')
    if open_braces > 0 or open_brackets > 0:
        raw = raw.rstrip()
        raw += '}' * open_braces + ']' * open_brackets
        logger.debug("json_repair_balanced_braces", braces=open_braces, brackets=open_brackets)
    
    return raw


def _clean_and_parse_json(raw: str) -> dict:
    """
    3-stage JSON cleaning and parsing:
    
    Stage 1: Strip markdown fences
    Stage 2: Extract balanced JSON object
    Stage 3: Repair malformed JSON and parse
    
    Returns dict on success, raises json.JSONDecodeError on ultimate failure.
    """
    raw = raw.strip()
    
    # Stage 1: Strip markdown code fences
    if raw.startswith("```"):
        # Extract content between ``` marks
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    logger.debug("json_stage1_markdown_stripped")
    
    # Stage 2: Extract balanced JSON object { ... }
    # Find first { and match it with balanced }
    brace_pos = raw.find('{')
    if brace_pos >= 0:
        depth = 0
        in_string = False
        escape_next = False
        for i in range(brace_pos, len(raw)):
            ch = raw[i]
            
            if escape_next:
                escape_next = False
                continue
            if ch == '\\':
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if not in_string:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        raw = raw[brace_pos:i+1]
                        logger.debug("json_stage2_extracted_balanced_object", start=brace_pos, end=i+1)
                        break
    
    # Stage 3: Repair and parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e1:
        logger.warning("json_parse_failed_attempting_repair", error=str(e1), char_pos=e1.pos)
        raw = _repair_json(raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e2:
            logger.error("json_repair_failed", original_error=str(e1), repair_error=str(e2))
            raise e2


def _parse_response_text(raw: str) -> dict:
    """
    Parse API response text through 3-stage JSON cleaning pipeline.
    
    Handles:
    - Markdown code fences
    - Unterminated strings
    - Trailing commas
    - Unbalanced braces
    - Preamble/trailing text
    """
    return _clean_and_parse_json(raw)


# ═══════════════════════════════════════════════════════════════
# Retry configuration (singleton, built once at module load)
# ═══════════════════════════════════════════════════════════════

def _build_retry_decorator() -> callable:
    """
    Build a retry decorator that handles OpenAI API errors.
    Built once at module load, shared across all API calls.
    """
    return retry(
        retry=retry_if_exception_type(OpenAIError),
        stop=stop_after_attempt(3),  # 3 attempts per model
        wait=wait_exponential(multiplier=2, min=2, max=60),  # Exponential backoff
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )


_RETRY_DECORATOR = _build_retry_decorator()


# ═══════════════════════════════════════════════════════════════
# API calls with model fallback
# ═══════════════════════════════════════════════════════════════

def _call_api(client: OpenAI, file_id: str, model: str, filename: str):
    """
    OpenAI Responses API call with single model.
    
    Returns the raw response object so the caller can read usage tokens.
    Decorated with retry logic at call site.
    """
    return client.responses.create(
        model=model,
        max_output_tokens=16000,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "file_id": file_id   # ✅ KEY CHANGE
                        # "filename": filename,
                        # "file_data": f"data:application/pdf;base64,{b64}",
                    },
                    {"type": "input_text", "text": EXTRACTION_PROMPT},
                ],
            },
        ],
    )


def _call_with_model_fallback(
    client: OpenAI,
    b64: str,
    models: list[str],
    filename: str,
    pdf_path: Path,
) -> tuple[dict, str, int, int]:
    """
    Try extracting JSON from API response with model fallback.
    
    Attempts to:
    1. Call API with primary model (retry up to 3 times)
    2. Parse JSON response through 3-stage cleaning
    3. Validate with Pydantic schema
    
    If ANY error occurs (API, JSON parse, validation), try next model.
    
    Args:
        client: OpenAI client
        b64: Base64-encoded PDF
        models: List of model names to try [primary, fallback1, fallback2]
        filename: PDF filename for API
        pdf_path: PDF path for logging
    
    Returns:
        (raw_dict, model_used, input_tokens, output_tokens)
    
    Raises:
        ValueError: All models failed
    """
    log = logger.bind(pdf=pdf_path.name)
    
    for attempt_idx, model in enumerate(models, 1):
        if not model:  # Skip empty fallback slots
            log.debug("model_fallback_slot_empty", attempt=attempt_idx, model="<empty>")
            continue
        
        log.info("model_attempt_start", attempt=attempt_idx, model=model)
        
        try:
            # Apply retry decorator to this API call
            api_call_with_retry = _RETRY_DECORATOR(_call_api)
            
            # Call API and get response
            response = api_call_with_retry(client, b64, model, filename)
            
            # Extract tokens
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
            
            # Parse JSON response
            output_text = getattr(response, "output_text", None)
            if not output_text or (isinstance(output_text, str) and not output_text.strip()):
                log.warning(
                    "model_attempt_empty_response",
                    attempt=attempt_idx,
                    model=model,
                    response_type=type(response).__name__,
                )
                continue  # Try next model
            
            # Check for mid-stream content filter refusal
            output_lower = output_text.lower()
            if any(phrase in output_lower for phrase in REFUSAL_PHRASES):
                log.warning(
                    "model_attempt_content_filter_refusal",
                    attempt=attempt_idx,
                    model=model,
                    preview=output_text[-200:],
                )
                raise ContentFilterRefusalError(
                    f"Content filter triggered mid-response for {filename!r}. "
                    f"A merchant name likely contains flagged text. "
                    f"Preview: {output_text[-200:]}"
                )
            
            # Stage 1-3 JSON cleaning and parsing
            try:
                raw_dict: dict[str, Any] = _clean_and_parse_json(output_text)
            except (json.JSONDecodeError, AttributeError) as exc:
                # Write failed JSON to debug folder before moving to next model
                _write_failed_json(pdf_path, output_text, model, attempt_idx, str(exc))
                log.warning(
                    "model_attempt_json_invalid",
                    attempt=attempt_idx,
                    model=model,
                    error=str(exc),
                )
                continue  # Try next model
            
            # Validate with Pydantic
            try:
                record = ConcurRecord.model_validate(raw_dict)
            except Exception as exc:
                log.warning(
                    "model_attempt_validation_failed",
                    attempt=attempt_idx,
                    model=model,
                    error=str(exc),
                )
                continue  # Try next model
            
            # Success!
            log.info(
                "model_attempt_succeeded",
                attempt=attempt_idx,
                model=model,
                tokens_in=input_tokens,
                tokens_out=output_tokens,
            )
            return raw_dict, model, input_tokens, output_tokens
        
        except ContentFilterRefusalError as exc:
            # Content filter detected mid-response — try next model
            log.warning(
                "model_attempt_content_filter_error",
                attempt=attempt_idx,
                model=model,
                error=str(exc),
            )
            continue  # Try next model
        
        except OpenAIError as exc:
            status_code = getattr(exc, "status_code", None)
            
            # For 500 server errors, sleep before retrying
            if status_code == 500:
                log.warning(
                    "model_attempt_500_error_sleeping",
                    attempt=attempt_idx,
                    model=model,
                    error=str(exc),
                    sleep_seconds=10,
                )
                time.sleep(10)  # Wait 10 seconds before trying next model
            
            log.warning(
                "model_attempt_failed",
                attempt=attempt_idx,
                model=model,
                error=str(exc),
                status_code=status_code,
            )
            continue  # Try next model
        
        except Exception as exc:
            log.warning(
                "model_attempt_unexpected_error",
                attempt=attempt_idx,
                model=model,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            continue  # Try next model
    
    # All models exhausted
    log.error("all_models_failed", tried_models=models)
    raise ValueError(f"All {len([m for m in models if m])} models failed for {pdf_path.name}")


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def extract_concur_record(pdf_path: Path, no_cache: bool = False) -> tuple[ConcurRecord, LLMMetrics]:
    """
    Extract and validate all 5 tables from a Concur expense report PDF.
    
    Features:
    - Model fallback (primary + 2 fallback models)
    - 3-stage JSON repair pipeline
    - SHA-256 content-addressed cache
    - Comprehensive error logging
    - File lock error handling

    Args:
        pdf_path: Path to Concur PDF file
        no_cache: If True, skip cache read and always rewrite cache

    Returns:
        (ConcurRecord, LLMMetrics)  — record for reconciliation,
                                      metrics for cost/token logging.

    Steps:
      1. Cache check (SHA-256 content-addressed) — skipped if no_cache=True
      2. Encode PDF as base64
      3. Call OpenAI Responses API with model fallback
      4. Parse JSON through 3-stage pipeline
      5. Validate with Pydantic ConcurRecord model
      6. Write cache (with file lock error handling)
      7. Build and record LLMMetrics

    Raises:
        FileNotFoundError  — pdf_path does not exist
        ValueError — All models failed or malformed JSON
        pydantic.ValidationError — extracted data fails schema
    """
    settings = get_settings()

    if not pdf_path.exists():
        raise FileNotFoundError(f"Concur PDF not found: {pdf_path}")

    log = logger.bind(pdf=pdf_path.name)
    log.info("concur_extraction_start")

    # ── 1. Cache check ────────────────────────────────────────────────────────
    if not no_cache and settings.cache_enabled:
        cp = _cache_path(pdf_path, settings.cache_dir)
        cached = _load_cache(cp)
        if cached is not None:
            try:
                # Validate cached data against schema
                record = ConcurRecord.model_validate(cached)
                log.info("concur_cache_hit", cache_file=cp.name)
                dummy = build_metrics(
                    pdf_file=pdf_path.name,
                    model=settings.azure_openai_model,
                    input_tokens=0, output_tokens=0,
                    cost_per_1k_input=settings.cost_per_1k_input,
                    cost_per_1k_output=settings.cost_per_1k_output,
                    latency=0.0, status="cache_hit",
                )
                return record, dummy
            except Exception as exc:
                log.warning("concur_cache_validation_failed", error=str(exc))
                # Cache is stale/corrupt, rebuild from API below

    # ── 2. Encode PDF ─────────────────────────────────────────────────────────
    log.debug("encoding_pdf")
    b64 = _pdf_to_base64(pdf_path)

    # ── 3. API call with model fallback ───────────────────────────────────────
    # Set up httpx timeout for large PDF processing
    timeout_config = httpx.Timeout(
        timeout=settings.api_timeout_seconds,
        connect=60,                              # 60s to establish connection
        read=settings.api_timeout_seconds,       # Full timeout for reading response
        write=30,                                # 30s to send data
        pool=10,                                 # 10s to acquire connection from pool
    )

    def _upload_file(client: OpenAI, pdf_path: Path) -> str:
        """
        Upload PDF to OpenAI Files API and return file_id
        """
        with open(pdf_path, "rb") as f:
            uploaded = client.files.create(
                file=f,
                purpose="assistants"   # required
            )
        return uploaded.id
    
    client = OpenAI(
        api_key=settings.azure_openai_api_key,
        base_url=settings.azure_openai_base_url,
        max_retries=0,  # We handle retries explicitly with tenacity
        # http_client=httpx.Client(timeout=timeout_config),
    )
    
    # Build model list: primary + fallbacks
    models = [
        settings.azure_openai_model,
        settings.azure_openai_model1,
        settings.azure_openai_model2,
    ]
    models = [m for m in models if m]  # Filter out empty strings

    with MetricsTimer() as timer:
        try:
            # ── Upload file ─────────────────────────────
            file_id = _upload_file(client, pdf_path)
            log.info("file_uploaded", file_id=file_id)

            raw_dict, model_used, input_tokens, output_tokens = _call_with_model_fallback(
                client, file_id, models, pdf_path.name, pdf_path
            ) #bs6

        except (ValueError, OpenAIError) as exc:
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
            log.error("concur_all_models_failed", error=str(exc))
            raise
        finally:
            # ✅ ALWAYS DELETE FILE FROM OPENAI
            if file_id:
                try:
                    client.files.delete(file_id)
                    log.info("file_deleted", file_id=file_id)
                except Exception as exc:
                    log.warning("file_delete_failed", file_id=file_id, error=str(exc))

    # ── 4. Validate ───────────────────────────────────────────────────────────
    record = ConcurRecord.model_validate(raw_dict)

    # ── 5. Cache write with file lock error handling ──────────────────────────
    # When no_cache=True, always write cache even if cache_enabled=False
    if no_cache or settings.cache_enabled:
        cp = _cache_path(pdf_path, settings.cache_dir)
        try:
            _write_cache(cp, raw_dict)
            if no_cache:
                log.info("concur_cache_rewritten", cache_file=cp.name)
        except OSError as exc:
            # File lock error (concurrent writes) — log but don't fail extraction
            if "file is in use" in str(exc).lower() or "permission denied" in str(exc).lower():
                log.warning("concur_cache_write_locked", cache_file=str(cp), error=str(exc))
            else:
                log.warning("concur_cache_write_failed", cache_file=str(cp), error=str(exc))
            # Continue anyway — cache miss on next run is acceptable

    # ── 6. Build and record LLMMetrics ────────────────────────────────────────
    metrics = build_metrics(
        pdf_file=pdf_path.name,
        model=model_used,
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
        model_used=model_used,
        cost_usd=round(metrics.cost_usd, 4),
        latency_s=timer.elapsed,
    )

    return record, metrics
