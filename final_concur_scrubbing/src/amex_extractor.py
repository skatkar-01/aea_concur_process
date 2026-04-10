"""
src/amex_extractor.py
──────────────────────
Responsible for:
  1. Reading a PDF from disk and encoding it as base64
  2. Calling Azure OpenAI (GPT-4o) with extraction prompts
  3. Parsing the JSON response into a validated Statement model
  4. Writing / reading an optional JSON cache to avoid repeat API calls
  5. Emitting structured logs and Prometheus metrics throughout
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    OpenAIError,
)
from pydantic import ValidationError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import get_settings
from src.models import Statement
from utils.logging_config import get_logger
from utils.metrics import METRICS, timed

logger = get_logger(__name__)

# ── Timeout / size constants ──────────────────────────────────────────────────
_DEFAULT_TIMEOUT_S: int = 180          # overridden by settings.api_timeout_seconds
_DEFAULT_MAX_PDF_BYTES: int = 40 * 1024 * 1024   # 40 MB
_DEFAULT_MAX_TOKENS: int = 16000       # Default max tokens for API response

# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a precise financial document parser for American Express corporate statements.
Extract ALL data from the provided PDF statement.
Use image layout for column positions and raw text for character-accurate values.
Watch for fused first_name+card_number and fused date+merchant.
Multi-line descriptions: merge all parts into transaction_desc.
Blank amount cells → null (not "0.00").
Negative = (x.xx) → -x.xx.
Mask Card Numbers.
Return ONLY valid JSON. No markdown fences, no explanations, no preamble.
""".strip()

EXTRACTION_PROMPT = """
Extract ALL data from this AMEX statement.

Merchant names and also all details mentioned in report are legal business names registered with payment networks.
Extract ALL merchant names exactly as printed, including names that may
appear unusual or contain slang. Do not refuse or skip any transaction.

DOCUMENT STRUCTURE:
Header: company name, statement type, period (e.g. JAN_042026)
Columns (L→R): Last Name | First Name | Card No | Process Date |
               Merchant Name | Transaction Description |
               Current Opening | Current Period Charges |
               Current Period Credits | Current Closing

KNOWN LAYOUT EXCEPTIONS:
1. First Name + Card Number fused → split at card pattern ####-######-#####
2. Date + Merchant fused → split at date boundary (M/D/YYYY or MM/DD/YYYY)
3. Multi-line descriptions → merge continuation lines into transaction_desc
4. Sparse amounts → blank columns = null (never "0.00" for blank)
5. Negative amounts → "(90.73)" becomes "-90.73"

Return ONLY valid JSON.
- No explanation
- No trailing commas
- No comments
- Ensure proper closing brackets
- Ensure valid JSON format

STRICT JSON ONLY:
{
  "company_name": "",
  "statement_type": "",
  "period": "",
  "cardholders": [
    {
      "last_name": "",
      "first_name": "",
      "card_number": "",
      "transactions": [
        {
          "last_name": "", "first_name": "", "card_number": "",
          "process_date": "MM/DD/YYYY or null",
          "merchant_name": null,
          "transaction_desc": "",
          "current_opening": null, "charges": null,
          "credits": null, "current_closing": null,
          "is_total_row": false
        }
      ],
      "total_row": {
        "last_name": "", "first_name": "", "card_number": "",
        "process_date": null, "merchant_name": null, "transaction_desc": null,
        "current_opening": null, "charges": null,
        "credits": null, "current_closing": null,
        "is_total_row": true
      }
    }
  ]
}
""".strip()


# ── Singleton client (BUG 3 + BUG 7 FIX) ─────────────────────────────────────

_client: OpenAI | None = None
_client_lock = threading.Lock()   # BUG 7 FIX: guard concurrent first-call


def _get_client() -> OpenAI:
    """
    Return the process-level OpenAI client, constructing it on first call.
    Thread-safe: uses a lock so parallel workers cannot double-construct.
    """
    global _client
    if _client is not None:
        return _client

    with _client_lock:                # BUG 7 FIX: double-checked locking
        if _client is not None:
            return _client

        s = get_settings()
        timeout_s = float(getattr(s, "api_timeout_seconds", _DEFAULT_TIMEOUT_S))

        # BUG 1 FIX: explicit timeout on client-level httpx pool
        _client = OpenAI(
            api_key=s.azure_openai_api_key,
            base_url=s.azure_openai_base_url,
            max_retries=0,              # retries managed by tenacity (BUG 2)
            timeout=httpx.Timeout(
                connect=15.0,           # TCP handshake
                read=float(timeout_s),  # full response stream
                write=30.0,             # upload (PDF base64 is large)
                pool=10.0,              # connection pool wait
            ),
        )
        logger.info(
            "openai_client_created",
            base_url=s.azure_openai_base_url,
            timeout_s=timeout_s,
        )
    return _client


# ── Cache helpers (BUG 10 FIX) ───────────────────────────────────────────────

_cache_locks: dict[str, threading.Lock] = {}
_cache_locks_guard = threading.Lock()


def _cache_lock_for(key: str) -> threading.Lock:
    """Return a per-cache-key lock (created on demand, never deleted)."""
    with _cache_locks_guard:
        if key not in _cache_locks:
            _cache_locks[key] = threading.Lock()
        return _cache_locks[key]


def _pdf_to_base64(pdf_path: Path) -> str:
    """Read a PDF and return its base64-encoded content."""
    with open(pdf_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _cache_key(pdf_path: Path) -> str:
    """Stable cache key: SHA-256 of PDF bytes (content-addressed)."""
    sha = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _cache_path(pdf_path: Path, cache_dir: Path) -> Path:
    key = _cache_key(pdf_path)
    return cache_dir / f"{pdf_path.stem}__{key[:12]}.json"


def _load_cache(cache_file: Path) -> dict[str, Any] | None:
    if not cache_file.exists():
        return None
    try:
        with open(cache_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("cache_read_failed", path=str(cache_file), error=str(exc))
        # Corrupt cache file — delete it so next run rebuilds cleanly
        try:
            cache_file.unlink(missing_ok=True)
        except OSError:
            pass
    return None


def _write_cache(cache_file: Path, data: dict[str, Any]) -> None:
    """
    Atomically write cache: write to .tmp then os.replace().
    Holds a per-key threading lock to prevent race between parallel workers
    processing the same PDF. (BUG 10 FIX)
    """
    lock = _cache_lock_for(str(cache_file))
    with lock:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            import os
            os.replace(tmp, cache_file)
            logger.debug("cache_written", path=str(cache_file))
        except OSError as exc:
            logger.warning("cache_write_failed", path=str(cache_file), error=str(exc))
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


# ── Debug file helpers (NEW) ──────────────────────────────────────────────────

def _save_failed_response(content: str, stage: str, pdf_name: str, log) -> Path | None:
    """
    Save failed JSON response to debug directory for inspection.
    
    Args:
        content: The JSON content that failed to parse
        stage: Stage identifier (e.g., "stage1_cleaned", "stage2_extracted")
        pdf_name: Name of the PDF being processed
        log: Logger instance
        
    Returns:
        Path to saved file, or None if save failed
    """
    try:
        settings = get_settings()
        debug_dir = settings.cache_dir / "failed_responses"
        debug_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        # Clean pdf_name for filesystem
        safe_pdf_name = re.sub(r'[^\w\-.]', '_', pdf_name)
        filename = f"{safe_pdf_name}_{stage}_{timestamp}.json"
        filepath = debug_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        log.error(
            "failed_response_saved",
            stage=stage,
            path=str(filepath),
            content_length=len(content),
            debug_dir=str(debug_dir),
        )
        return filepath
    except (OSError, AttributeError) as exc:
        log.warning(
            "failed_to_save_debug_file",
            stage=stage,
            error=str(exc),
        )
        return None


def _get_error_context(text: str, error_pos: int, context_chars: int = 150) -> dict[str, Any]:
    """
    Extract context around a JSON parsing error position.
    
    Args:
        text: The full text that failed to parse
        error_pos: Character position where error occurred
        context_chars: Number of characters to show before/after error
        
    Returns:
        Dict with before, at, and after context
    """
    start = max(0, error_pos - context_chars)
    end = min(len(text), error_pos + context_chars)
    
    return {
        "error_pos": error_pos,
        "context_before": text[start:error_pos],
        "error_char": text[error_pos] if error_pos < len(text) else "<EOF>",
        "context_after": text[error_pos:end],
        "total_length": len(text),
    }


# ── JSON extraction helpers (BUG 4 FIX + ENHANCED) ────────────────────────────

def _strip_markdown_fences(text: str) -> str:
    """
    Remove ```json … ``` or ``` … ``` wrappers the model sometimes adds.

    Handles:
      - Opening fence on its own line (```json or ```)
      - Opening fence immediately before the brace (```json{...}```)
      - Trailing ``` after the closing brace
    """
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:].strip()
        else:
            text = re.sub(r"^```[a-z]*", "", text).strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    return text


def _extract_json_object(text: str) -> str | None:
    """
    Fallback: scan for the outermost balanced { … } JSON object.

    Handles:
      - "Here is the extracted data:\\n{...}"
      - "{...}\\n\\nI hope this helps!"
      - Partial truncation (returns None so caller can raise cleanly)
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start: i + 1]

    return None   # Unbalanced — likely truncated


def _repair_json(json_str: str) -> str:
    """
    Repair malformed JSON by:
      1. Remove any non-JSON preamble/trailing text
      2. Auto-closing unterminated strings
      3. Removing trailing commas before ] or }
      4. Adding missing closing brackets/braces
      5. Fix escaped quotes inside strings
      6. Handle mid-string truncation

    BUG FIX: Models sometimes return incomplete JSON with unterminated strings.
    This attempts to salvage what we can get.
    """
    # Remove leading/trailing whitespace
    json_str = json_str.strip()
    
    # Remove any leading text before first {
    start_idx = json_str.find("{")
    if start_idx > 0:
        json_str = json_str[start_idx:]
    elif start_idx == -1:
        # No { found, return as-is (will fail gracefully)
        return json_str
    
    # Check for mid-string truncation (doesn't end with } or ])
    if not json_str.endswith('}') and not json_str.endswith(']'):
        # Count quotes to detect unterminated string
        quote_count = 0
        escape_next = False
        for ch in json_str:
            if escape_next:
                escape_next = False
                continue
            if ch == '\\':
                escape_next = True
                continue
            if ch == '"':
                quote_count += 1
        
        # Odd number of quotes = unterminated string
        if quote_count % 2 == 1:
            logger.warning(
                "json_repair_truncated_string_detected",
                quote_count=quote_count,
                ends_with=json_str[-50:] if len(json_str) > 50 else json_str,
            )
            json_str += '"'  # Close unterminated string
    
    # Remove control characters but preserve important whitespace
    cleaned = []
    for ch in json_str:
        if ord(ch) >= 32 or ch in "\n\r\t":
            cleaned.append(ch)
        elif ch == '\x00':  # Null byte
            continue
        else:
            cleaned.append(' ')  # Replace other control chars with space
    json_str = "".join(cleaned)

    # Close unterminated strings
    result = []
    in_string = False
    escape_next = False
    
    for i, ch in enumerate(json_str):
        if escape_next:
            escape_next = False
            result.append(ch)
            continue
        
        if ch == '\\' and in_string:
            escape_next = True
            result.append(ch)
            continue
        
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        
        # If we're in a string and hit control chars or problematic endings, close the string
        if in_string and ch in '\n\r\x00':
            result.append('"')  # Close unterminated string
            in_string = False
            result.append(' ')  # Replace control char with space
            continue
        
        result.append(ch)
    
    # Force close any remaining unterminated string at end
    if in_string:
        result.append('"')
    
    json_str = "".join(result)
    
    # Remove trailing commas (aggressive pass)
    json_str = re.sub(r',\s*([}\]])', r'\1', json_str)  # ,} or ,] → } or ]
    
    # Remove duplicate quotes
    json_str = json_str.replace('""', '"')
    
    # Balance braces and brackets (add missing closes)
    open_braces = json_str.count('{') - json_str.count('}')
    open_brackets = json_str.count('[') - json_str.count(']')
    
    # Add missing closing braces/brackets
    if open_braces > 0:
        json_str += "}" * open_braces
    if open_brackets > 0:
        json_str += "]" * open_brackets
    
    return json_str


def _clean_and_parse_json(raw_text: str, log, pdf_name: str = "unknown") -> dict[str, Any]:
    """
    Three-stage JSON cleaning pipeline with comprehensive debugging.

    Stage 1 — strip markdown fences (fast path, covers ~95% of cases).
    Stage 2 — brace-scan for outermost object (preamble / trailing text).
    Stage 3 — repair malformed JSON (unterminated strings, missing braces).

    All failures are saved to debug files for inspection.

    Raises ValueError with a diagnostic preview if all stages fail.
    """
    # Stage 1: Strip markdown fences
    cleaned = _strip_markdown_fences(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as stage1_exc:
        error_pos = getattr(stage1_exc, 'pos', 0)
        error_line = getattr(stage1_exc, 'lineno', '?')
        error_col = getattr(stage1_exc, 'colno', '?')
        
        context = _get_error_context(cleaned, error_pos)
        
        log.warning(
            "json_stage1_failed",
            error=str(stage1_exc),
            error_line=error_line,
            error_col=error_col,
            error_pos=error_pos,
            context_before=context["context_before"][-100:],  # Last 100 chars before error
            error_char=context["error_char"],
            context_after=context["context_after"][:100],  # First 100 chars after error
            total_length=len(cleaned),
        )
        
        # Save full response for debugging
        # _save_failed_response(cleaned, "stage1_cleaned", pdf_name, log)

    # Stage 2: Extract outermost JSON object
    extracted = _extract_json_object(raw_text)
    if extracted:
        try:
            result = json.loads(extracted)
            log.info("json_stage2_success", extracted_len=len(extracted))
            return result
        except json.JSONDecodeError as stage2_exc:
            error_pos = getattr(stage2_exc, 'pos', 0)
            context = _get_error_context(extracted, error_pos)
            
            log.warning(
                "json_stage2_failed",
                error=str(stage2_exc),
                error_pos=error_pos,
                context_before=context["context_before"][-100:],
                error_char=context["error_char"],
                context_after=context["context_after"][:100],
                extracted_len=len(extracted),
            )
            
            # _save_failed_response(extracted, "stage2_extracted", pdf_name, log)

    # Stage 3: Repair malformed JSON
    candidates = [extracted, raw_text] if extracted else [raw_text]
    
    for idx, candidate in enumerate(candidates):
        if not candidate or not candidate.strip().startswith("{"):
            continue
        
        source = "extracted" if candidate == extracted else "raw"
        
        try:
            repaired = _repair_json(candidate)
            result = json.loads(repaired)
            log.info(
                "json_stage3_success", 
                repaired_len=len(repaired), 
                original_len=len(candidate),
                source=source,
            )
            return result
        except json.JSONDecodeError as stage3_exc:
            error_pos = getattr(stage3_exc, 'pos', 0)
            context = _get_error_context(repaired if 'repaired' in locals() else candidate, error_pos)
            
            log.warning(
                "json_stage3_failed",
                error=str(stage3_exc)[:200],
                error_pos=error_pos,
                context_before=context["context_before"][-100:],
                error_char=context["error_char"],
                context_after=context["context_after"][:100],
                source=source,
            )
            
            # Save both original and repaired versions
            # _save_failed_response(candidate, f"stage3_{source}_original_{idx}", pdf_name, log)
            # if 'repaired' in locals():
                # _save_failed_response(repaired, f"stage3_{source}_repaired_{idx}", pdf_name, log)

    # All stages failed - save raw response and raise with detailed error
    _save_failed_response(raw_text, "raw_original", pdf_name, log)
    
    settings = get_settings()
    debug_dir = settings.cache_dir / "failed_responses"
    
    raise ValueError(
        f"No JSON object found in API response after 3 stages. "
        f"Raw response length: {len(raw_text)}. "
        f"First 500 chars: {raw_text[:500]}\n"
        f"Last 500 chars: {raw_text[-500:]}\n"
        f"Debug files saved to: {debug_dir}\n"
        f"Check files matching pattern: {pdf_name}_*.json"
    )


# ── Retry logic (BUG 2 FIX) ──────────────────────────────────────────────────

def _is_retryable(exc: BaseException) -> bool:
    """
    Return True if the exception warrants a retry.

    Covers all transient Azure OpenAI / httpx failure modes:
      - APITimeoutError      — request exceeded timeout
      - APIConnectionError   — TCP-level failure
      - httpx.ReadTimeout    — belt-and-suspenders (some SDK versions don't wrap)
      - httpx.ConnectTimeout — ditto
      - httpx.RemoteProtocolError — server dropped the connection mid-stream
      - APIStatusError 429   — rate-limited
      - APIStatusError 5xx   — server-side transient error
    """
    if isinstance(exc, (
        APITimeoutError,
        APIConnectionError,
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
        httpx.RemoteProtocolError,
    )):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in {429, 500, 502, 503, 504}
    return False


# BUG 5 FIX: decorator built ONCE at module load, not per-file inside
#             extract_statement(). Per-file rebuild lost the attempt counter.
# BUG 6 FIX: max= cap on wait_exponential prevents indefinite wait growth.

def _build_retry_decorator(max_attempts: int, wait_base: int, wait_max: int):
    return retry(
        retry=retry_if_exception(_is_retryable),    # BUG 2 FIX: predicate, not type
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(
            multiplier=wait_base,
            min=wait_base,
            max=wait_max,                           # BUG 6 FIX: bounded max wait
        ),
        before_sleep=before_sleep_log(
            logging.getLogger(__name__), logging.WARNING
        ),
        reraise=True,
    )


# Lazily built on first call to extract_statement (needs settings)
_retry_decorator = None
_retry_decorator_lock = threading.Lock()


def _get_retry_decorator():
    global _retry_decorator
    if _retry_decorator is not None:
        return _retry_decorator
    with _retry_decorator_lock:
        if _retry_decorator is not None:
            return _retry_decorator
        s = get_settings()
        max_attempts = getattr(s, "max_retries", 4)
        wait_base    = int(getattr(s, "retry_wait_seconds", 10))
        wait_max     = int(getattr(s, "retry_wait_max_seconds", 120))
        _retry_decorator = _build_retry_decorator(max_attempts, wait_base, wait_max)
    return _retry_decorator


# ── API call (ENHANCED) ───────────────────────────────────────────────────────

def _call_api(
    client: OpenAI,
    b64: str,
    model: str,
    filename: str,
    timeout_s: float,
    max_tokens: int,
) -> str:
    """
    Single API call using the OpenAI Responses API.

    Timeout is set on BOTH the client (httpx pool) AND here (per-call) as
    defence-in-depth — some SDK versions honour only one. (BUG 1 FIX)

    Args:
        client: OpenAI client instance
        b64: Base64-encoded PDF content
        model: Model name to use
        filename: PDF filename for logging
        timeout_s: Request timeout in seconds
        max_tokens: Maximum tokens for response (prevents truncation)

    Raises:
        APITimeoutError:    request exceeded the configured timeout
        APIConnectionError: network-level failure
        APIStatusError:     non-2xx HTTP response
        ValueError:         API returned empty response body or truncated response
    """
    try:
        response = client.responses.create(
            model=model,
            timeout=timeout_s,      # BUG 1 FIX: explicit per-call timeout
            max_output_tokens=max_tokens,  # NEW: Prevent truncation
            input=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "filename": filename,
                            "file_data": f"data:application/pdf;base64,{b64}",
                        },
                        {
                            "type": "input_text",
                            "text": EXTRACTION_PROMPT,
                        },
                    ],
                },
            ],
        )
        
        output = response.output_text.strip()
        
        if not output:
            raise ValueError(
                f"API returned empty response for {filename!r}. "
                f"Response object: {response}"
            )
        
        # Check for truncation
        finish_reason = getattr(response, 'finish_reason', None)
        if finish_reason == 'length':
            logger.warning(
                "response_truncated_by_max_tokens",
                file=filename,
                model=model,
                output_len=len(output),
                finish_reason=finish_reason,
                max_tokens=max_tokens,
            )
            raise ValueError(
                f"API response truncated due to max_tokens limit ({max_tokens}). "
                f"Response length: {len(output)}. "
                f"Increase max_tokens in settings or via dynamic calculation."
            )
        
        logger.debug(
            "api_call_complete",
            file=filename,
            model=model,
            response_len=len(output),
            finish_reason=finish_reason,
        )
        
        return output

    except OpenAIError as exc:
        logger.error(
            "api_error",
            file=filename,
            model=model,
            exc_type=type(exc).__name__,
            status=getattr(exc, "status_code", "?"),
            body=getattr(exc, "body", "?"),
            message=str(exc),
        )
        raise


def _call_with_model_fallback(
    client: OpenAI,
    b64: str,
    pdf_filename: str,
    pdf_size_kb: int,
    timeout_s: float,
    primary_model: str,
    fallback_models: list[str],
) -> dict[str, Any]:
    """
    Try extraction with primary model, then fallback to other models if retries fail.

    Flow:
      1. Calculate dynamic max_tokens based on PDF size
      2. Try primary_model with full retry loop
      3. Call API, parse JSON, validate schema — if ANY error, try fallback model
      4. If all retries exhausted for model, try next fallback_model
      5. Log each model attempt and fallback transition
      6. Return on first successful extraction (valid JSON + schema validation)
      7. Raise final exception if all models fail

    Args:
        client: OpenAI client (thread-safe singleton)
        b64: Base64-encoded PDF
        pdf_filename: Name of PDF for logging
        pdf_size_kb: Size of PDF in KB (for dynamic max_tokens)
        timeout_s: API timeout in seconds
        primary_model: First model to try (e.g. "gpt-5-mini")
        fallback_models: List of fallback models if primary fails

    Returns:
        Parsed and validated JSON dict from successful extraction

    Raises:
        Exception: All models failed after all retries and validation attempts
    """
    retry_decorator = _get_retry_decorator()
    settings = get_settings()
    
    # Calculate dynamic max_tokens based on PDF size
    base_tokens = int(getattr(settings, "max_tokens", _DEFAULT_MAX_TOKENS))
    tokens_per_kb = int(getattr(settings, "max_tokens_per_kb", 50))
    max_tokens_cap = int(getattr(settings, "max_tokens_cap", 32000))
    
    dynamic_max_tokens = min(
        base_tokens + (pdf_size_kb * tokens_per_kb),
        max_tokens_cap
    )

    log = logger.bind(
        pdf=pdf_filename,
        models=[primary_model] + fallback_models,
        max_tokens=dynamic_max_tokens,
    )

    # Try primary model, then fallback models in order
    models_to_try = [primary_model] + fallback_models
    last_exception: Exception | None = None

    for attempt_idx, model in enumerate(models_to_try):
        is_primary = (model == primary_model)
        try:
            log.info(
                "model_attempt_start",
                model=model,
                is_primary=is_primary,
                attempt_idx=attempt_idx + 1,
                total_models=len(models_to_try),
                max_tokens=dynamic_max_tokens,
            )
            
            # STEP 1: Call API with retries
            retried_call = retry_decorator(_call_api)
            raw_text = retried_call(
                client,
                b64,
                model,
                pdf_filename,
                timeout_s,
                dynamic_max_tokens,
            )
            
            # STEP 2: Parse JSON — any error triggers fallback
            try:
                raw_dict: dict[str, Any] = _clean_and_parse_json(raw_text, log, pdf_filename)
            except (ValueError, json.JSONDecodeError) as json_exc:
                log.warning(
                    "model_attempt_json_invalid",
                    model=model,
                    is_primary=is_primary,
                    exc_type=type(json_exc).__name__,
                    error=str(json_exc)[:500],  # Truncate long errors
                    attempt_idx=attempt_idx + 1,
                )
                raise json_exc  # Propagate to outer catch to trigger fallback
            
            # STEP 3: Validate with Pydantic — any error triggers fallback
            try:
                statement = Statement.model_validate(raw_dict)
            except ValidationError as val_exc:
                log.warning(
                    "model_attempt_validation_failed",
                    model=model,
                    is_primary=is_primary,
                    exc_type=type(val_exc).__name__,
                    error=str(val_exc)[:500],
                    attempt_idx=attempt_idx + 1,
                )
                raise val_exc  # Propagate to outer catch to trigger fallback
            
            # SUCCESS: All validations passed
            log.info(
                "model_attempt_success",
                model=model,
                is_primary=is_primary,
                cardholders=statement.total_cardholders,
                transactions=statement.total_transactions,
                attempt_idx=attempt_idx + 1,
            )
            return raw_dict

        except Exception as exc:
            # ANY error → log and try next model
            last_exception = exc
            is_last_model = (model == models_to_try[-1])
            log.warning(
                "model_attempt_failed",
                model=model,
                is_primary=is_primary,
                exc_type=type(exc).__name__,
                error=str(exc)[:500],  # Truncate long errors
                is_last_model=is_last_model,
                attempt_idx=attempt_idx + 1,
            )
            if is_last_model:
                # All models exhausted
                log.error(
                    "all_models_failed",
                    num_models=len(models_to_try),
                    last_exc=type(last_exception).__name__,
                    error=str(last_exception)[:500],
                )
                raise

    # Should not reach here (last iteration always raises)
    if last_exception:
        raise last_exception
    raise RuntimeError(f"Unexpected: no models provided to {pdf_filename!r}")


# ── Public API ────────────────────────────────────────────────────────────────

def extract_statement(pdf_path: Path) -> Statement:
    """
    Full extraction pipeline for a single AMEX PDF.

    Steps:
      1. Validate file exists and is within size limit  (BUG 9)
      2. Check cache → return early if hit
      3. Log PDF size
      4. Encode PDF as base64
      5. Call Azure OpenAI with bounded retry / back-off  (BUG 1/2/5/6)
      6. Three-stage JSON cleaning + parse with debug file saving (ENHANCED)
      7. Validate with Pydantic Statement model  (BUG 8)
      8. Atomic cache write  (BUG 10)
      9. Return validated Statement

    Raises:
        FileNotFoundError:      pdf_path does not exist
        ValueError:             PDF too large, or model returned unparseable JSON
        pydantic.ValidationError: extracted data fails schema validation
        openai.APITimeoutError: all retries exhausted due to timeouts
        openai.OpenAIError:     all retries exhausted for other API errors
    """
    settings = get_settings()

    # ── 1. Existence + size check (BUG 9 FIX) ────────────────────────────────
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf_bytes = pdf_path.stat().st_size
    pdf_kb = int(pdf_bytes / 1024)
    max_bytes = int(getattr(settings, "max_pdf_bytes", _DEFAULT_MAX_PDF_BYTES))
    if pdf_bytes > max_bytes:
        raise ValueError(
            f"PDF too large: {pdf_path.name} is {pdf_bytes / 1024 / 1024:.1f} MB "
            f"(limit {max_bytes / 1024 / 1024:.0f} MB). "
            "Reduce PDF size or increase settings.max_pdf_bytes."
        )

    log = logger.bind(pdf=pdf_path.name)

    # ── 2. Cache check ────────────────────────────────────────────────────────
    cp: Path | None = None
    if settings.cache_enabled:
        cp = _cache_path(pdf_path, settings.cache_dir)
        cached = _load_cache(cp)
        if cached is not None:
            log.info("cache_hit", cache_file=cp.name)
            METRICS.files_cached.inc()
            try:
                statement = Statement.model_validate(cached)
            except ValidationError as exc:
                # Stale / corrupt cache — rebuild from API
                log.warning(
                    "cache_schema_mismatch",
                    error=str(exc),
                    cache_file=cp.name,
                )
                cp.unlink(missing_ok=True)
            else:
                METRICS.cardholders_extracted.inc(statement.total_cardholders)
                METRICS.transactions_extracted.inc(statement.total_transactions)
                return statement

    # ── 3. Log PDF size ───────────────────────────────────────────────────────
    log.info("extraction_start", pdf_bytes=pdf_bytes, pdf_kb=pdf_kb)

    # ── 4. Encode PDF ─────────────────────────────────────────────────────────
    b64 = _pdf_to_base64(pdf_path)

    # ── 5. Call API with bounded retry (BUG 1/2/5/6 FIX) ─────────────────────
    client = _get_client()              # BUG 3 FIX: singleton client

    timeout_s = float(getattr(settings, "api_timeout_seconds", _DEFAULT_TIMEOUT_S))
    
    # Build fallback models from .env AZURE_OPENAI_MODEL1 and AZURE_OPENAI_MODEL2
    fallback_models = []
    if getattr(settings, "azure_openai_model1", ""):
        fallback_models.append(settings.azure_openai_model1)
    if getattr(settings, "azure_openai_model2", ""):
        fallback_models.append(settings.azure_openai_model2)

    raw_dict: dict[str, Any]
    statement: Statement
    with timed(METRICS.extraction_duration):
        try:
            # _call_with_model_fallback() handles:
            # 1. API call with retries
            # 2. JSON parsing (triggers fallback on error)
            # 3. Schema validation (triggers fallback on error)
            # Returns only if all 3 steps succeed
            raw_dict = _call_with_model_fallback(
                client,
                b64,
                pdf_path.name,
                pdf_kb,  # NEW: Pass PDF size for dynamic max_tokens
                timeout_s,
                primary_model=settings.azure_openai_model,
                fallback_models=fallback_models,
            )
            # Re-validate here to ensure statement object is available
            statement = Statement.model_validate(raw_dict)
            log.info(
                "api_call_success",
                cardholders=statement.total_cardholders,
                transactions=statement.total_transactions,
            )
        except (OpenAIError, httpx.TimeoutException, ValueError, json.JSONDecodeError, ValidationError) as exc:
            METRICS.api_failures.inc()
            log.error(
                "api_call_failed",
                exc_type=type(exc).__name__,
                error=str(exc)[:500],  # Truncate long errors
                pdf_kb=pdf_kb,
            )
            raise

    # ── 6. Cache write (BUG 10 FIX) ────────────────────────────────────────────
    if settings.cache_enabled and cp is not None:
        _write_cache(cp, raw_dict)

    # ── 7. Metrics ────────────────────────────────────────────────────────────
    # (validation already done in _call_with_model_fallback)
    METRICS.cardholders_extracted.inc(statement.total_cardholders)
    METRICS.transactions_extracted.inc(statement.total_transactions)
    return statement