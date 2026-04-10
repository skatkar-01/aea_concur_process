"""
extractors.py
=============
Prompt definition, pricing constants, and two extract functions.

  extract_with_claude(pdf_path) -> dict   — Claude Haiku 4.5 via Azure AI Foundry
  extract_with_gemini(pdf_path) -> dict   — Gemini 2.0 Flash via Google free API

Docs
----
  Claude PDF  : https://docs.anthropic.com/en/docs/build-with-claude/pdf-support
  Gemini PDF  : https://ai.google.dev/gemini-api/docs/document-processing
  Claude price: https://platform.claude.com/docs/en/about-claude/pricing
  Gemini price: https://ai.google.dev/gemini-api/docs/pricing
"""

import os
import re
import json
import time
import base64
from pathlib import Path

# ── dependency check ──────────────────────────────────────────────────────────
try:
    import anthropic
except ImportError:
    raise ImportError("Run: pip install anthropic")

try:
    from google import genai
    from google.genai import types
except ImportError:
    raise ImportError("Run: pip install google-genai")


# =============================================================================
# PRICING  (sourced from official docs — March 2026)
# =============================================================================

CLAUDE_PRICING = {
    "model":           "claude-haiku-4-5",
    "input_per_1m":    1.00,     # USD — platform.claude.com/docs/en/about-claude/pricing
    "output_per_1m":   5.00,     # USD
    "batch_50pct_off": True,     # Batch API halves both rates
}

GEMINI_PRICING = {
    "model":           "gemini-2.0-flash",
    "input_per_1m":    0.00,     # FREE tier — ai.google.dev/gemini-api/docs/pricing
    "output_per_1m":   0.00,     # Free: 15 RPM · 1M TPM · 1500 RPD
    "paid_input_1m":   0.10,     # paid tier rate if you upgrade
    "paid_output_1m":  0.40,
}


# =============================================================================
# PROMPT  (same text sent to both models)
# =============================================================================
#
# Receipt schema design:
#   Fixed fields  — always present, downstream code can rely on these keys
#   extra{}       — flexible bag for anything else found on the receipt
#                   e.g. tax, tip, subtotal, booking_ref, hotel_nights,
#                        flight_from/to, seat_class, check_in/out, meal_items
# =============================================================================

PROMPT = """
This PDF contains expense transactions followed by receipts and invoices.
Receipts cover Uber rides, hotels, flights, meals and similar travel expenses.

Extract every transaction AND every receipt/invoice found in the PDF.

Return ONLY a valid JSON object with exactly two arrays.
No markdown, no explanation, no text outside the JSON.

{
  "transactions": [
    {
      "txn_id":      "reference or invoice number, else TXN-001 TXN-002 ...",
      "date":        "YYYY-MM-DD or exactly as written",
      "vendor":      "merchant / airline / hotel name",
      "category":    "uber | hotel | flight | meal | taxi | other",
      "amount":      123.45,
      "currency":    "3-letter ISO code e.g. USD INR EUR GBP",
      "description": "short description of what was purchased"
    }
  ],
  "receipts": [
    {
      "receipt_no":  "receipt or invoice number, empty string if not found",
      "date":        "YYYY-MM-DD or exactly as written",
      "vendor":      "merchant name",
      "description": "what was purchased",
      "total":       110.00,
      "currency":    "3-letter ISO code",
      "linked_txn":  "txn_id of the matching transaction if identifiable, else empty",
      "extra": {
        "put any additional fields here that are visible on this receipt",
        "examples: subtotal, tax, tip, booking_ref, hotel_nights,",
        "flight_from, flight_to, seat_class, check_in, check_out,",
        "meal_items, discount, service_charge, loyalty_points — anything extra"
      }
    }
  ]
}

Rules:
- total and amount must be numbers, not strings — use 0 if not visible
- currency must be a 3-letter code — default USD if not visible
- Extract ALL transactions and ALL receipts without skipping any
- extra{} must be a flat key-value object (no nested objects inside extra)
- If a receipt has no extra fields, set extra to an empty object {}
"""


# =============================================================================
# FUNCTION 1 — Claude Haiku 4.5  (Azure AI Foundry)
# Ref: https://docs.anthropic.com/en/docs/build-with-claude/pdf-support
# =============================================================================

def extract_with_claude(pdf_path: str) -> dict:
    """
    Send the PDF to Claude Haiku 4.5 on Azure AI Foundry.

    Per Claude PDF docs:
      - Encode PDF as base64
      - Send as a 'document' content block with media_type 'application/pdf'
      - Token counts come back in message.usage
    """
    endpoint = os.environ.get("AZURE_CLAUDE_ENDPOINT", "").rstrip("/")
    api_key  = os.environ.get("AZURE_CLAUDE_KEY", "")

    if not endpoint:
        return _err("claude-haiku-4-5", "Azure AI Foundry",
                    "AZURE_CLAUDE_ENDPOINT not set")
    if not api_key:
        return _err("claude-haiku-4-5", "Azure AI Foundry",
                    "AZURE_CLAUDE_KEY not set")

    # Read and base64-encode the PDF bytes (required by Claude document block)
    pdf_b64 = base64.standard_b64encode(Path(pdf_path).read_bytes()).decode("utf-8")

    # Azure AI Foundry base_url pattern per Anthropic Azure docs
    client = anthropic.Anthropic(
        api_key  = api_key,
        base_url = f"{endpoint}/models",
    )

    t0 = time.time()
    try:
        message = client.messages.create(
            model      = "claude-haiku-4-5",
            max_tokens = 8096,
            messages   = [
                {
                    "role": "user",
                    "content": [
                        {
                            # ── document block — exact structure from Claude PDF docs ──
                            "type": "document",
                            "source": {
                                "type":       "base64",
                                "media_type": "application/pdf",
                                "data":       pdf_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": PROMPT,
                        },
                    ],
                }
            ],
        )
    except Exception as exc:
        return _err("claude-haiku-4-5", "Azure AI Foundry", str(exc))

    latency       = round(time.time() - t0, 2)
    raw_text      = message.content[0].text
    input_tokens  = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    parsed        = _parse_json(raw_text)
    cost          = _cost(input_tokens, output_tokens,
                          CLAUDE_PRICING["input_per_1m"],
                          CLAUDE_PRICING["output_per_1m"])

    return {
        "model":         "claude-haiku-4-5",
        "provider":      "Azure AI Foundry",
        "latency_sec":   latency,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "pricing": {
            "input_cost":  round((input_tokens  / 1_000_000) * CLAUDE_PRICING["input_per_1m"],  6),
            "output_cost": round((output_tokens / 1_000_000) * CLAUDE_PRICING["output_per_1m"], 6),
            "total_cost":  cost,
            "rate":        f"${CLAUDE_PRICING['input_per_1m']} input / "
                           f"${CLAUDE_PRICING['output_per_1m']} output per 1M tokens",
            "source":      "platform.claude.com/docs/en/about-claude/pricing",
        },
        "transactions":  parsed.get("transactions", []),
        "receipts":      parsed.get("receipts", []),
        "raw_response":  raw_text,
        "error":         None,
    }


# =============================================================================
# FUNCTION 2 — Gemini 2.0 Flash  (Google free API)
# Ref: https://ai.google.dev/gemini-api/docs/document-processing
# =============================================================================

def extract_with_gemini(pdf_path: str) -> dict:
    """
    Send the PDF to Gemini 2.0 Flash via the Google AI free API.

    Per Gemini document-processing docs:
      - Pass raw PDF bytes using types.Part.from_bytes()
      - mime_type must be 'application/pdf'
      - SDK handles encoding — no manual base64 needed
      - Token counts come back in response.usage_metadata
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return _err("gemini-2.0-flash", "Google AI (free)", "GEMINI_API_KEY not set")

    pdf_bytes = Path(pdf_path).read_bytes()
    client    = genai.Client(api_key=api_key)

    t0 = time.time()
    try:
        response = client.models.generate_content(
            model    = "gemini-2.0-flash",
            contents = [
                # ── types.Part.from_bytes — exact pattern from Gemini PDF docs ──
                types.Part.from_bytes(
                    data      = pdf_bytes,
                    mime_type = "application/pdf",
                ),
                PROMPT,
            ],
            config = types.GenerateContentConfig(
                temperature       = 0.1,
                max_output_tokens = 8096,
            ),
        )
    except Exception as exc:
        return _err("gemini-2.0-flash", "Google AI (free)", str(exc))

    latency       = round(time.time() - t0, 2)
    raw_text      = response.text
    input_tokens  = getattr(response.usage_metadata, "prompt_token_count",     0) or 0
    output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
    parsed        = _parse_json(raw_text)

    return {
        "model":         "gemini-2.0-flash",
        "provider":      "Google AI (free tier)",
        "latency_sec":   latency,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "pricing": {
            "input_cost":  0.0,
            "output_cost": 0.0,
            "total_cost":  0.0,
            "rate":        "FREE — $0.00 on free tier",
            "free_limits": "15 RPM · 1,000,000 TPM · 1,500 RPD",
            "paid_rate":   f"${GEMINI_PRICING['paid_input_1m']} input / "
                           f"${GEMINI_PRICING['paid_output_1m']} output per 1M (if upgraded)",
            "source":      "ai.google.dev/gemini-api/docs/pricing",
        },
        "transactions":  parsed.get("transactions", []),
        "receipts":      parsed.get("receipts", []),
        "raw_response":  raw_text,
        "error":         None,
    }


# =============================================================================
# PRIVATE HELPERS
# =============================================================================

def _parse_json(raw: str) -> dict:
    """Strip markdown fences if present, then parse JSON."""
    text = raw.strip()

    # Remove ```json ... ``` or ``` ... ``` fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$",        "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: find first {...} block in the response
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {
            "transactions":  [],
            "receipts":      [],
            "_parse_error":  text[:300],
        }


def _cost(input_tok: int, output_tok: int,
          input_rate: float, output_rate: float) -> float:
    return round(
        (input_tok  / 1_000_000) * input_rate +
        (output_tok / 1_000_000) * output_rate,
        6,
    )


def _err(model: str, provider: str, message: str) -> dict:
    return {
        "model": model, "provider": provider,
        "latency_sec": 0, "input_tokens": 0, "output_tokens": 0,
        "pricing": {"total_cost": 0},
        "transactions": [], "receipts": [],
        "raw_response": "", "error": message,
    }
