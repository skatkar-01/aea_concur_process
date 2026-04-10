"""
src/extractor.py
─────────────────
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
import time
from pathlib import Path
from typing import Any

from openai import OpenAI, OpenAIError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging

from config.settings import get_settings
from src.models import Statement
from utils.logging_config import get_logger
from utils.metrics import METRICS, timed

logger = get_logger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a precise financial document parser for American Express corporate statements.
Extract ALL data from the provided PDF statement.
Use image layout for column positions and raw text for character-accurate values.
Watch for fused first_name+card_number and fused date+merchant.
Multi-line descriptions: merge all parts into transaction_desc.
Blank amount cells → null (not "0.00").
Negative = (x.xx) → -x.xx.
""".strip()

EXTRACTION_PROMPT = """
Extract ALL data from this AMEX statement.

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

Return ONLY valid JSON — no markdown, no explanation:
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


# ── Internal helpers ──────────────────────────────────────────────────────────

def _pdf_to_base64(pdf_path: Path) -> str:
    """Read a PDF and return its base64-encoded content."""
    with open(pdf_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _cache_key(pdf_path: Path) -> str:
    """Stable cache key: SHA-256 of the PDF bytes (content-addressed)."""
    sha = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _cache_path(pdf_path: Path, cache_dir: Path) -> Path:
    key = _cache_key(pdf_path)
    return cache_dir / f"{pdf_path.stem}__{key[:12]}.json"


def _load_cache(cache_file: Path) -> dict[str, Any] | None:
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("cache_read_failed", path=str(cache_file), error=str(exc))
    return None


def _write_cache(cache_file: Path, data: dict[str, Any]) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(data, f, indent=2)
        logger.debug("cache_written", path=str(cache_file))
    except OSError as exc:
        logger.warning("cache_write_failed", path=str(cache_file), error=str(exc))


def _strip_markdown_fences(text: str) -> str:
    """Remove ```json … ``` or ``` … ``` wrappers the model sometimes adds."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first line (```json or ```) and last line (```)
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    return text


# ── Retry-wrapped API call ────────────────────────────────────────────────────

def _make_retry_decorator(max_attempts: int, wait_base: int):
    return retry(
        retry=retry_if_exception_type(OpenAIError),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=wait_base, min=wait_base, max=60),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )


def _call_api(client: OpenAI, b64: str, model: str, filename: str) -> str:
    """
    Single API call using the OpenAI Responses API.

    Uses `client.responses.create` (not chat.completions) with:
      - "input_file"  content block  → PDF sent as base64 data URI
      - "input_text"  content block  → extraction prompt
      - response.output_text         → plain text from the model
    """
    response = client.responses.create(
        model=model,
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
    return response.output_text.strip()


# ── Public API ────────────────────────────────────────────────────────────────

def extract_statement(pdf_path: Path) -> Statement:
    """
    Full extraction pipeline for a single PDF file.

    Steps:
      1. Check cache → return early if hit
      2. Encode PDF as base64
      3. Call Azure OpenAI with retry / back-off
      4. Strip markdown fences, parse JSON
      5. Validate with Pydantic Statement model
      6. Write result to cache
      7. Return validated Statement

    Raises:
        FileNotFoundError: if pdf_path does not exist
        json.JSONDecodeError: if the model returns unparseable JSON
        pydantic.ValidationError: if extracted data fails schema validation
        openai.OpenAIError: if all retries are exhausted
    """
    settings = get_settings()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    log = logger.bind(pdf=pdf_path.name)
    log.info("extraction_start")

    # ── 1. Cache check ────────────────────────────────────────────────────────
    if settings.cache_enabled:
        cp = _cache_path(pdf_path, settings.cache_dir)
        cached = _load_cache(cp)
        if cached is not None:
            log.info("cache_hit", cache_file=cp.name)
            METRICS.files_cached.inc()
            statement = Statement.model_validate(cached)
            METRICS.cardholders_extracted.inc(statement.total_cardholders)
            METRICS.transactions_extracted.inc(statement.total_transactions)
            return statement

    # ── 2. Encode PDF ─────────────────────────────────────────────────────────
    log.debug("encoding_pdf")
    b64 = _pdf_to_base64(pdf_path)

    # ── 3. Build client & call API with retry ─────────────────────────────────
    # Uses standard OpenAI client pointed at the Azure endpoint via base_url.
    # This matches the original: OpenAI(api_key=..., base_url=...)
    client = OpenAI(
        api_key=settings.azure_openai_api_key,
        base_url=settings.azure_openai_base_url,
        max_retries=0,   # retries managed by tenacity below
    )

    retry_decorator = _make_retry_decorator(
        max_attempts=settings.max_retries,
        wait_base=settings.retry_wait_seconds,
    )
    retried_call = retry_decorator(_call_api)

    raw_text: str
    with timed(METRICS.extraction_duration):
        try:
            raw_text = retried_call(
                client, b64, settings.azure_openai_model, pdf_path.name
            )
            log.info("api_call_success", chars=len(raw_text))
        except OpenAIError as exc:
            METRICS.api_failures.inc()
            log.error("api_call_failed", error=str(exc))
            raise

    # ── 4. Parse JSON ─────────────────────────────────────────────────────────
    clean = _strip_markdown_fences(raw_text)
    try:
        raw_dict: dict[str, Any] = json.loads(clean)
    except json.JSONDecodeError as exc:
        log.error("json_parse_failed", raw_preview=clean[:200], error=str(exc))
        raise

    # ── 5. Validate with Pydantic ─────────────────────────────────────────────
    statement = Statement.model_validate(raw_dict)
    log.info(
        "extraction_complete",
        cardholders=statement.total_cardholders,
        transactions=statement.total_transactions,
    )

    # ── 6. Write cache ────────────────────────────────────────────────────────
    if settings.cache_enabled:
        _write_cache(cp, raw_dict)

    # ── 7. Metrics ────────────────────────────────────────────────────────────
    METRICS.cardholders_extracted.inc(statement.total_cardholders)
    METRICS.transactions_extracted.inc(statement.total_transactions)

    return statement
