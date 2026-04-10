"""
processors/receipt_grouper.py
Groups consecutive receipt pages that belong to the same receipt.
Input: list of classified receipt page dicts. Output: list[ReceiptGroup].
No I/O. No business logic.

Two-pass approach:
  Pass 1 — Rule-based (fast, zero LLM cost):
    explicit continuation signals, same vendor, no final total, unknown next page
  Pass 2 — LLM (ambiguous cases only):
    shows two consecutive pages, asks: same receipt or different?
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

from config.settings import get_settings
from models.enums import PageType
from shared.logger import get_logger

log = get_logger(__name__)


@dataclass
class ReceiptGroup:
    pages:               list  = field(default_factory=list)
    page_numbers:        list  = field(default_factory=list)
    receipt_type:        str   = PageType.UNKNOWN.value
    vendor_hint:         Optional[str] = None
    is_multi_page:       bool  = False
    grouping_confidence: str   = "HIGH"
    grouping_reason:     str   = ""

    def add_page(self, page: dict) -> None:
        self.pages.append(page)
        self.page_numbers.append(page["page_num"])
        if len(self.pages) > 1:
            self.is_multi_page = True

    @property
    def is_image_capture(self) -> bool:
        return any(p.get("is_image_dominant") for p in self.pages)


# ── Regex patterns ────────────────────────────────────────────────────────────

_CONTINUATION_RE = re.compile(
    r"continued\s+on\s+(next|following)\s+page"
    r"|page\s+\d+\s+of\s+\d+"
    r"|see\s+(next|following)\s+page"
    r"|subtotal\s+carried\s+forward"
    r"|balance\s+forward|total\s+forward"
    r"|folio\s+(page\s+)?\d+\s+of\s+\d+",
    re.IGNORECASE,
)
_PAGE_FOOTER_RE = re.compile(
    r"(?:^|\s)(\d{1,2})\s*/\s*(\d{1,2})\s*$", re.MULTILINE
)
_TERMINAL_RE = re.compile(
    r"thank\s+you\s+for\s+"
    r"|amount\s+(paid|charged|due)"
    r"|payment\s+(received|processed|approved|charged)"
    r"|balance\s+due\s*[:\s]*\$?\s*0\.00"
    r"|auth(?:orization)?\s*(code|#|no)[:\s]*\w+"
    r"|approved\s+\$[\d,]+\.\d{2}",
    re.IGNORECASE,
)
_FINAL_TOTAL_RE = re.compile(
    r"(?:^|\n)\s*(?:total|grand\s+total|amount\s+due|total\s+due|total\s+charged)"
    r"\s*[:\s]*\$?\s*([\d,]+\.\d{2})",
    re.IGNORECASE | re.MULTILINE,
)

_CONTINUITY_PROMPT = """\
Two consecutive pages from an expense report PDF.
Are they ONE receipt (page 2 continues page 1) or TWO different receipts?

Return ONLY valid JSON — no markdown:
{
  "same_receipt": true or false,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "reason": "<one sentence>",
  "page1_has_final_total": true or false,
  "page2_has_own_header": true or false
}
"""


def _has_continuation(text: str) -> bool:
    if _CONTINUATION_RE.search(text):
        return True
    m = _PAGE_FOOTER_RE.search(text)
    if m:
        n, total = int(m.group(1)), int(m.group(2))
        return total > 1 and n < total
    return False


def _has_terminal(text: str) -> bool:
    return bool(_TERMINAL_RE.search(text))


def _has_final_total(text: str) -> bool:
    return bool(_FINAL_TOTAL_RE.search(text))


def _vendor_hint(page: dict) -> Optional[str]:
    text = page.get("text", "").strip()
    if not text:
        return None
    for line in text.split("\n")[:6]:
        line = line.strip()
        if len(line) >= 4 and not re.match(r"^[\d\s/\-]+$", line):
            return line
    return None


def _vendor_match(a: Optional[str], b: Optional[str]) -> bool:
    """Requires 4+ char token overlap to avoid false positives."""
    if not a or not b:
        return False
    if a.lower().strip() == b.lower().strip():
        return True
    tokens_a = {t for t in re.split(r"\W+", a.lower()) if len(t) >= 4}
    tokens_b = {t for t in re.split(r"\W+", b.lower()) if len(t) >= 4}
    return bool(tokens_a & tokens_b)


class MultiPageReceiptGrouper:
    def __init__(self, azure_client):
        self._llm = azure_client

    def group(self, receipt_pages: list[dict]) -> list[ReceiptGroup]:
        if not receipt_pages:
            return []
        if len(receipt_pages) == 1:
            g = ReceiptGroup(
                receipt_type   = receipt_pages[0]["classification"]["page_type"],
                vendor_hint    = _vendor_hint(receipt_pages[0]),
                grouping_reason = "Single page",
            )
            g.add_page(receipt_pages[0])
            return [g]

        max_per_group = get_settings().pdf_batch_size * 2
        groups: list[ReceiptGroup] = []
        i = 0

        while i < len(receipt_pages):
            current = receipt_pages[i]
            group   = ReceiptGroup(
                receipt_type    = current["classification"]["page_type"],
                vendor_hint     = _vendor_hint(current),
                grouping_reason = "Lead page",
            )
            group.add_page(current)

            while i + 1 < len(receipt_pages) and len(group.pages) < max_per_group:
                nxt   = receipt_pages[i + 1]
                merge = self._should_merge(current, nxt)

                if merge["merge"]:
                    group.add_page(nxt)
                    group.grouping_reason    = merge["reason"]
                    group.grouping_confidence = merge["confidence"]
                    nxt_type = nxt["classification"]["page_type"]
                    if nxt_type not in (PageType.UNKNOWN.value, "unknown"):
                        group.receipt_type = nxt_type
                    current = nxt
                    i += 1
                    log.debug(
                        "  merged page %d into group %s: %s",
                        nxt["page_num"], group.page_numbers, merge["reason"],
                    )
                else:
                    break

            log.info(
                "  receipt group pages=%s type=%s vendor=%s multi=%s",
                group.page_numbers, group.receipt_type,
                group.vendor_hint or "?", group.is_multi_page,
            )
            groups.append(group)
            i += 1

        return groups

    def _should_merge(self, current: dict, nxt: dict) -> dict:
        cur_text = current.get("text", "")
        cur_type = current["classification"]["page_type"]
        nxt_type = nxt["classification"]["page_type"]

        # Rule 1: explicit continuation marker
        if _has_continuation(cur_text):
            return {"merge": True, "confidence": "HIGH",
                    "reason": "Explicit continuation signal"}

        # Rule 2: terminal payment signal → receipt is done
        if _has_terminal(cur_text) and cur_text.strip():
            return {"merge": False, "confidence": "HIGH",
                    "reason": "Terminal payment signal — receipt complete"}

        # Rule 3: no final total + next page has no independent header
        if (
            cur_text.strip()
            and not _has_final_total(cur_text)
            and not nxt["classification"].get("date_visible")
            and not nxt["classification"].get("vendor_name")
            and nxt_type in (PageType.UNKNOWN.value, cur_type)
        ):
            return {"merge": True, "confidence": "MEDIUM",
                    "reason": "No final total + next page has no independent header"}

        # Rule 4: same vendor on consecutive pages
        v_cur = current["classification"].get("vendor_name") or _vendor_hint(current)
        v_nxt = nxt["classification"].get("vendor_name") or _vendor_hint(nxt)
        if _vendor_match(v_cur, v_nxt):
            if _has_terminal(cur_text) and _has_final_total(cur_text):
                return {"merge": False, "confidence": "HIGH",
                        "reason": f"Same vendor ({v_cur}) but page is complete"}
            return {"merge": True, "confidence": "HIGH",
                    "reason": f"Same vendor on consecutive pages: '{v_cur}'"}

        # Rule 5: next page completely unidentifiable
        if (
            nxt_type == PageType.UNKNOWN.value
            and not nxt["classification"].get("vendor_name")
            and not nxt["classification"].get("date_visible")
            and not nxt["classification"].get("amount_visible")
        ):
            return {"merge": True, "confidence": "MEDIUM",
                    "reason": "Next page has no identifying markers — likely continuation"}

        # Pass 2: LLM for ambiguous cases
        return self._llm_check(current, nxt)

    def _llm_check(self, page_a: dict, page_b: dict) -> dict:
        content = []
        for label, page in (("PAGE A", page_a), ("PAGE B", page_b)):
            content.append(self._llm.text_block(
                f"=== {label} (page {page['page_num']}) ==="
            ))
            text = page.get("text", "").strip()
            if text:
                content.append(self._llm.text_block(
                    self._llm.truncate(text, 2000)
                ))
            if page.get("image_b64"):
                content.append(self._llm.image_block(page["image_b64"]))
        content.append(self._llm.text_block(_CONTINUITY_PROMPT))

        try:
            result = self._llm.call_json(
                messages=self._llm.user_message(content),
                max_completion_tokens=get_settings().llm_max_completion_tokens_small,
                context=f"continuity {page_a['page_num']}↔{page_b['page_num']}",
                required_keys=["same_receipt", "confidence"],
            )
            return {
                "merge":      bool(result.get("same_receipt", False)),
                "confidence": result.get("confidence", "MEDIUM"),
                "reason":     f"LLM: {result.get('reason', '')}",
            }
        except Exception as exc:
            log.warning(
                "  LLM continuity check failed %d↔%d: %s — defaulting separate",
                page_a["page_num"], page_b["page_num"], exc,
            )
            return {"merge": False, "confidence": "LOW",
                    "reason": f"LLM failed ({type(exc).__name__}) — safe default: separate"}
