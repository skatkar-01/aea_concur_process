"""
processors/page_classifier.py
Classifies every PDF page by type.
Input: list of page dicts. Output: same list with 'classification' key added.
No I/O. No business logic.
"""
from __future__ import annotations
from typing import Optional

from config.settings import get_settings
from models.enums import PageType
from shared.exceptions import LLMCallError, LLMResponseParseError
from shared.logger import get_logger

log = get_logger(__name__)

_PROMPT = """\
Classify this single page from an expense report PDF.

Page types and distinguishing features:
- concur_report  : SAP Concur branding, employee/report ID, expense line table
- audit_trail    : timestamp columns, "Approval Status Change", step names
- receipt_ride   : Uber/Lyft/taxi — trip fare, pickup/dropoff, driver name
- receipt_meal   : restaurant name, food items, table/server, subtotal/tax/tip
- receipt_hotel  : hotel name, check-in/out dates, room rate, folio number
- receipt_flight : airline, flight number, origin→destination, booking reference
- receipt_parking: parking facility, entry/exit time, duration, rate
- receipt_other  : any other vendor (conference, office supply, rental, etc.)
- unknown        : blank, cover, or cannot classify

Return ONLY valid JSON — no markdown:
{
  "page_type": "<type>",
  "is_image_capture": <true if photo/scan>,
  "vendor_name": "<vendor name or null>",
  "amount_visible": "<total amount as string or null>",
  "date_visible": "<date as found or null>",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "notes": "<one reason>"
}
"""

_VALID_TYPES = {pt.value for pt in PageType}


class PageClassifier:
    def __init__(self, azure_client):
        self._llm = azure_client

    def classify_pages(self, pages: list[dict]) -> list[dict]:
        """
        Classify all pages. Returns same list with 'classification' added.
        Failures → UNKNOWN (never crash the pipeline).
        """
        classified = []
        total = len(pages)
        for page in pages:
            pnum = page["page_num"]
            try:
                result = self._classify_one(page)
            except Exception as exc:
                log.error("Classification failed page %d: %s", pnum, exc)
                result = self._fallback(pnum)

            classified.append({**page, "classification": result})
            log.info(
                "  page %3d/%d: %-22s vendor=%-20s amount=%-10s %s %s",
                pnum, total,
                result["page_type"],
                result.get("vendor_name") or "n/a",
                result.get("amount_visible") or "n/a",
                "📷" if result.get("is_image_capture") else "📄",
                result.get("confidence", "?"),
            )
        return classified

    def _classify_one(self, page: dict) -> dict:
        settings = get_settings()
        content  = []

        text = page.get("text", "").strip()
        if text:
            content.append(self._llm.text_block(
                f"Page {page['page_num']}:\n"
                + self._llm.truncate(text, settings.pdf_text_char_limit)
            ))
        if page.get("image_b64"):
            content.append(self._llm.image_block(page["image_b64"]))

        if not content:
            return self._fallback(page["page_num"], "empty page")

        content.append(self._llm.text_block(_PROMPT))

        result = self._llm.call_json(
            messages=self._llm.user_message(content),
            max_completion_tokens=settings.llm_max_completion_tokens_small,
            context=f"classify page {page['page_num']}",
            required_keys=["page_type", "confidence"],
        )

        if result.get("page_type") not in _VALID_TYPES:
            log.warning(
                "Unknown page_type '%s' page %d — defaulting UNKNOWN",
                result.get("page_type"), page["page_num"],
            )
            result["page_type"] = PageType.UNKNOWN.value

        return result

    @staticmethod
    def _fallback(page_num: int, reason: str = "classification failed") -> dict:
        return {
            "page_type":        PageType.UNKNOWN.value,
            "is_image_capture": False,
            "vendor_name":      None,
            "amount_visible":   None,
            "date_visible":     None,
            "confidence":       "LOW",
            "notes":            reason,
        }
