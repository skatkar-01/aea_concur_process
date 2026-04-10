"""
extractors/amex_extractor.py
AMEX PDF → AmexStatement model.

Routing:
  Digital PDF  → CoordinateParser (rule-based, fast, high accuracy)
                   if confidence < threshold → Azure vision fallback
  Scanned PDF  → Azure vision only

No business validation here — that belongs in processors/.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import get_settings
from extractors.base import BaseExtractor
from models.amex import AmexStatement, AmexCardholder, AmexTransaction, parse_amount
from models.enums import ExtractionMethod
from shared.exceptions import ExtractionError
from shared.logger import get_logger
from shared.pdf_loader import PDFLoader

log = get_logger(__name__)

# ── LLM prompts ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a precise financial document parser for American Express corporate statements.

DOCUMENT STRUCTURE:
Header: company name, statement type, period (e.g. JAN_042026)
Columns (L→R): Last Name | First Name | Card No | Process Date |
               Merchant Name | Transaction Description |
               Current Opening | Current Period Charges |
               Current Period Credits | Current Closing

KNOWN LAYOUT EXCEPTIONS:
1. First Name + Card Number fused: split at card pattern ####-######-#####
2. Date + Merchant fused: split at date boundary (M/D/YYYY or MM/DD/YYYY)
3. Multi-line descriptions: merge continuation lines into transaction_desc
4. Sparse amounts: blank columns = null (never "0.00" for blank)
5. Negative amounts: "(90.73)" → "-90.73"

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
"""

_USER_DIGITAL = """\
Extract ALL data from this AMEX statement.
Use the IMAGE for layout/column positions.
Use the RAW TEXT below for character-accurate values.

Watch for fused first_name+card_number and fused date+merchant.
Multi-line descriptions: merge all parts into transaction_desc.
Blank amount cells → null (not "0.00").

RAW TEXT:
{raw_text}
"""

_USER_SCANNED = """\
Extract ALL data from this AMEX statement image.
Columns L→R: Last Name | First Name | Card No | Process Date |
Merchant Name | Transaction Description | Current Opening | Charges | Credits | Closing

Watch for fused first_name+card_number and fused date+merchant.
Blank cells → null. Negative = (x.xx) → -x.xx.
"""


class AmexExtractor(BaseExtractor):

    def extract(self, pdf_path: Path) -> AmexStatement:
        """
        Extract AMEX statement from PDF.
        Returns AmexStatement. Raises ExtractionError on unrecoverable failure.
        """
        t0       = time.monotonic()
        settings = get_settings()
        log.info("Extracting AMEX: %s", pdf_path.name)

        try:
            pages = PDFLoader.load(pdf_path)
        except Exception as exc:
            raise ExtractionError(str(exc), source_file=pdf_path.name) from exc

        # Classify: digital (has text layer) or scanned (image only)
        total_chars = sum(len(p.get("text", "")) for p in pages)
        is_digital  = total_chars > len(pages) * settings.pdf_min_text_chars

        try:
            if is_digital:
                doc, method = self._extract_digital(pages, pdf_path.name, settings)
            else:
                doc, method = self._extract_scanned(pages, pdf_path.name)
        except Exception as exc:
            raise ExtractionError(
                f"Extraction failed: {exc}", source_file=pdf_path.name
            ) from exc

        doc.source_file      = pdf_path.name
        doc.extraction_method = method
        doc.extracted_at     = datetime.now(timezone.utc).isoformat()
        doc.page_count       = len(pages)
        doc.processing_ms    = int((time.monotonic() - t0) * 1000)

        log.info(
            "  extracted %d cardholder(s) via %s [%dms]",
            len(doc.cardholders), method.value, doc.processing_ms,
        )
        return doc

    def _extract_digital(
        self, pages: list[dict], source_file: str, settings
    ) -> tuple[AmexStatement, ExtractionMethod]:
        """Primary: coordinate parser. Fallback: Azure vision."""
        # Try coordinate parser first
        try:
            from extractors._coordinate_parser import CoordinateParser
            all_words = [w for p in pages for w in p.get("words", [])]
            if all_words:
                doc, confidence = CoordinateParser().parse(all_words, source_file)
                if confidence >= settings.confidence_threshold:
                    log.info("  coordinate parser used (conf=%.2f)", confidence)
                    return doc, ExtractionMethod.COORDINATE
                log.info(
                    "  coordinate conf %.2f < %.2f — Azure fallback",
                    confidence, settings.confidence_threshold,
                )
        except Exception as exc:
            log.debug("  coordinate parser unavailable: %s", exc)

        # Azure fallback
        raw_text = "\n\n".join(
            f"--- PAGE {p['page_num']} ---\n{p.get('text','')}"
            for p in pages
        )
        user_text = _USER_DIGITAL.format(raw_text=raw_text[:10000])
        content   = [self._llm.text_block(user_text)]
        for p in pages[:10]:
            if p.get("image_b64"):
                content.append(self._llm.image_block(p["image_b64"], "image/png"))

        result = self._llm.call_json(
            messages=self._llm.system_user_message(_SYSTEM_PROMPT, content),
            max_completion_tokens=get_settings().llm_max_completion_tokens_large,
            context=f"amex digital {source_file}",
            required_keys=["cardholders"],
        )
        return self._build_model(result, source_file), ExtractionMethod.AZURE_HYBRID

    def _extract_scanned(
        self, pages: list[dict], source_file: str
    ) -> tuple[AmexStatement, ExtractionMethod]:
        content = [self._llm.text_block(_USER_SCANNED)]
        for p in pages[:10]:
            if p.get("image_b64"):
                content.append(self._llm.image_block(p["image_b64"], "image/png"))

        result = self._llm.call_json(
            messages=self._llm.system_user_message(_SYSTEM_PROMPT, content),
            max_completion_tokens=get_settings().llm_max_completion_tokens_large,
            context=f"amex scanned {source_file}",
            required_keys=["cardholders"],
        )
        return self._build_model(result, source_file), ExtractionMethod.AZURE_VISION

    def _build_model(self, data: dict, source_file: str) -> AmexStatement:
        """Convert LLM JSON dict → AmexStatement model."""
        cardholders = []
        for ch_raw in data.get("cardholders", []):
            transactions = []
            for t in ch_raw.get("transactions", []):
                transactions.append(AmexTransaction(
                    last_name        = t.get("last_name", ""),
                    first_name       = t.get("first_name", ""),
                    card_number      = t.get("card_number", ""),
                    process_date     = t.get("process_date"),
                    merchant_name    = t.get("merchant_name"),
                    transaction_desc = t.get("transaction_desc"),
                    current_opening  = parse_amount(t.get("current_opening")),
                    charges          = parse_amount(t.get("charges")),
                    credits          = parse_amount(t.get("credits")),
                    current_closing  = parse_amount(t.get("current_closing")),
                    is_total_row     = bool(t.get("is_total_row", False)),
                ))

            total_raw = ch_raw.get("total_row")
            total_row = None
            if total_raw:
                total_row = AmexTransaction(
                    last_name        = total_raw.get("last_name", ""),
                    first_name       = total_raw.get("first_name", ""),
                    card_number      = total_raw.get("card_number", ""),
                    process_date     = None,
                    merchant_name    = None,
                    transaction_desc = None,
                    current_opening  = parse_amount(total_raw.get("current_opening")),
                    charges          = parse_amount(total_raw.get("charges")),
                    credits          = parse_amount(total_raw.get("credits")),
                    current_closing  = parse_amount(total_raw.get("current_closing")),
                    is_total_row     = True,
                )

            cardholders.append(AmexCardholder(
                last_name    = ch_raw.get("last_name", ""),
                first_name   = ch_raw.get("first_name", ""),
                card_number  = ch_raw.get("card_number", ""),
                transactions = transactions,
                total_row    = total_row,
            ))

        return AmexStatement(
            source_file    = source_file,
            company_name   = data.get("company_name", ""),
            statement_type = data.get("statement_type", ""),
            period         = data.get("period", ""),
            cardholders    = cardholders,
        )
