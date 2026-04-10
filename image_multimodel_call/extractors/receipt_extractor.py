"""
extractors/receipt_extractor.py
Receipt pages (any type, any format) → list[Receipt] models.

Flow:
  1. Classify pages (page_classifier)
  2. Group multi-page receipts (receipt_grouper)
  3. Parse each group with type-specific LLM prompt

No business validation here.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import get_settings
from extractors.base import BaseExtractor
from models.enums import ExpenseType, PageType
from models.receipt import Receipt, ReceiptLineItem
from processors.page_classifier import PageClassifier
from processors.receipt_grouper import MultiPageReceiptGrouper, ReceiptGroup
from shared.exceptions import ExtractionError
from shared.logger import get_logger
from shared.pdf_loader import PDFLoader

log = get_logger(__name__)

# ── Per-type JSON schemas ──────────────────────────────────────────────────────
_PREAMBLE = """\
Extract structured data from this receipt. May span multiple pages — provided in order.
Rules: JSON only. Monetary values = numbers (52.01 not "$52.01").
Dates: keep as found. Missing fields: null. Final total = amount charged to payment card.
"""

_SCHEMAS: dict[str, str] = {
    PageType.RECEIPT_RIDE.value: """{
  "receipt_type":"ride","vendor":null,"date":null,"pickup_time":null,"dropoff_time":null,
  "total_charged":0.0,"base_fare":0.0,"surcharges":{},"taxes":0.0,"tip":0.0,
  "distance_miles":null,"duration_minutes":null,
  "origin_address":null,"destination_address":null,
  "driver_name":null,"trip_id":null,"payment_method":null,"payment_last4":null,"payment_date":null
}""",
    PageType.RECEIPT_MEAL.value: """{
  "receipt_type":"meal","vendor":null,"restaurant_name":null,"address":null,
  "date":null,"time":null,"total_charged":0.0,"subtotal":0.0,"tax":0.0,"tip":0.0,
  "service_charge":0.0,"num_guests":null,"items":[],
  "payment_method":null,"payment_last4":null
}""",
    PageType.RECEIPT_HOTEL.value: """{
  "receipt_type":"hotel","vendor":null,"hotel_name":null,"address":null,
  "check_in_date":null,"check_out_date":null,"num_nights":null,"room_type":null,
  "rate_per_night":0.0,"total_charged":0.0,"room_charges":0.0,
  "taxes_and_fees":0.0,"other_charges":{},
  "guest_name":null,"confirmation_number":null,"folio_number":null,
  "payment_method":null,"payment_last4":null
}""",
    PageType.RECEIPT_FLIGHT.value: """{
  "receipt_type":"flight","vendor":null,"airline":null,
  "booking_reference":null,"ticket_number":null,"passenger_name":null,
  "issue_date":null,"travel_date":null,"origin":null,"origin_code":null,
  "destination":null,"destination_code":null,"flight_number":null,"cabin_class":null,
  "base_fare":0.0,"taxes_and_fees":0.0,"baggage_fee":0.0,"seat_fee":0.0,
  "total_charged":0.0,"payment_method":null,"payment_last4":null
}""",
    PageType.RECEIPT_PARKING.value: """{
  "receipt_type":"parking","vendor":null,"facility_name":null,"address":null,
  "entry_date":null,"entry_time":null,"exit_date":null,"exit_time":null,
  "duration_hours":null,"subtotal":0.0,"taxes":0.0,"total_charged":0.0,
  "validation_applied":false,"ticket_number":null,
  "payment_method":null,"payment_last4":null
}""",
    PageType.RECEIPT_OTHER.value: """{
  "receipt_type":"other","vendor":null,"date":null,"description":null,
  "items":[],"subtotal":0.0,"tax":0.0,"total_charged":0.0,
  "reference_number":null,"payment_method":null,"payment_last4":null
}""",
}
_SCHEMAS[PageType.UNKNOWN.value] = _SCHEMAS[PageType.RECEIPT_OTHER.value]

_TYPE_TO_EXPENSE: dict[str, ExpenseType] = {
    PageType.RECEIPT_RIDE.value:    ExpenseType.RIDE,
    PageType.RECEIPT_MEAL.value:    ExpenseType.MEAL,
    PageType.RECEIPT_HOTEL.value:   ExpenseType.HOTEL,
    PageType.RECEIPT_FLIGHT.value:  ExpenseType.FLIGHT,
    PageType.RECEIPT_PARKING.value: ExpenseType.PARKING,
    PageType.RECEIPT_OTHER.value:   ExpenseType.OTHER,
    PageType.UNKNOWN.value:         ExpenseType.OTHER,
}


class ReceiptExtractor(BaseExtractor):
    """Extracts all receipts from a PDF that contains Concur + receipt pages."""

    def __init__(self, azure_client):
        super().__init__(azure_client)
        self._classifier = PageClassifier(azure_client)
        self._grouper    = MultiPageReceiptGrouper(azure_client)

    def extract(self, pdf_path: Path) -> list[Receipt]:
        """
        Extract all receipts from a PDF.
        Returns list[Receipt]. Empty list if no receipt pages found.
        """
        t0 = time.monotonic()
        log.info("Extracting receipts: %s", pdf_path.name)

        try:
            pages = PDFLoader.load(pdf_path)
        except Exception as exc:
            raise ExtractionError(str(exc), source_file=pdf_path.name) from exc

        # Classify every page
        classified = self._classifier.classify_pages(pages)

        # Keep only receipt pages
        receipt_pages = [
            p for p in classified
            if PageType.is_receipt(p["classification"]["page_type"])
        ]
        log.info("  %d receipt page(s) from %d total", len(receipt_pages), len(pages))

        if not receipt_pages:
            return []

        # Group multi-page receipts
        groups = self._grouper.group(receipt_pages)
        log.info(
            "  %d receipt group(s) (%d multi-page)",
            len(groups), sum(1 for g in groups if g.is_multi_page),
        )

        # Parse each group
        receipts: list[Receipt] = []
        for group in groups:
            try:
                receipt = self._parse_group(group)
                receipts.append(receipt)
            except Exception as exc:
                log.error(
                    "  receipt parse failed pages %s: %s",
                    group.page_numbers, exc,
                )
                # Return partial record rather than crash
                receipts.append(Receipt(
                    source_pages   = group.page_numbers,
                    receipt_type   = _TYPE_TO_EXPENSE.get(group.receipt_type, ExpenseType.OTHER),
                    vendor         = group.vendor_hint,
                    parse_error    = str(exc),
                    extracted_at   = datetime.now(timezone.utc).isoformat(),
                ))

        ms = int((time.monotonic() - t0) * 1000)
        log.info("  %d receipt(s) extracted [%dms]", len(receipts), ms)
        return receipts

    def _parse_group(self, group: ReceiptGroup) -> Receipt:
        settings = get_settings()
        schema   = _SCHEMAS.get(group.receipt_type, _SCHEMAS[PageType.RECEIPT_OTHER.value])
        multi_ctx = (
            f"This receipt spans {len(group.pages)} pages (PDF pages {group.page_numbers}).\n"
            "All pages are below in order. Combine them for the COMPLETE receipt.\n\n"
        ) if group.is_multi_page else ""

        prompt  = f"{_PREAMBLE}\n{multi_ctx}Extract into this schema:\n{schema}"
        content = []
        for page in group.pages:
            content.append(self._llm.text_block(f"--- PAGE {page['page_num']} ---"))
            text = page.get("text", "").strip()
            if text:
                content.append(self._llm.text_block(
                    self._llm.truncate(text, settings.pdf_text_char_limit)
                ))
            if page.get("image_b64"):
                content.append(self._llm.image_block(page["image_b64"]))
        content.append(self._llm.text_block(prompt))

        data = self._llm.call_json(
            messages=self._llm.user_message(content),
            max_completion_tokens=settings.llm_max_completion_tokens_large,
            context=f"receipt pages {group.page_numbers}",
            required_keys=["receipt_type", "total_charged"],
        )

        return self._build_receipt(data, group)

    def _build_receipt(self, data: dict, group: ReceiptGroup) -> Receipt:
        def _f(key: str) -> Optional[float]:
            v = data.get(key)
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def _fi(key: str) -> Optional[int]:
            v = data.get(key)
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        items = []
        for raw_item in data.get("items", []) or []:
            if isinstance(raw_item, dict):
                items.append(ReceiptLineItem(
                    description = raw_item.get("description", ""),
                    amount      = float(raw_item.get("amount", 0.0) or 0.0),
                ))

        expense_type = _TYPE_TO_EXPENSE.get(group.receipt_type, ExpenseType.OTHER)

        # Build fees dict (surcharges / other_charges)
        fees = data.get("surcharges") or data.get("other_charges") or {}
        if not isinstance(fees, dict):
            fees = {}

        return Receipt(
            source_pages        = group.page_numbers,
            receipt_type        = expense_type,
            is_multi_page       = group.is_multi_page,
            is_image_capture    = group.is_image_capture,
            grouping_confidence = group.grouping_confidence,
            vendor              = data.get("vendor"),
            date                = data.get("date") or data.get("check_in_date") or data.get("travel_date") or data.get("entry_date"),
            total_charged       = float(data.get("total_charged") or 0.0),
            payment_method      = data.get("payment_method"),
            payment_last4       = data.get("payment_last4"),
            payment_date        = data.get("payment_date"),
            reference_number    = (
                data.get("confirmation_number") or data.get("booking_reference") or
                data.get("trip_id") or data.get("ticket_number") or
                data.get("folio_number") or data.get("reference_number")
            ),
            subtotal            = float(data.get("subtotal") or data.get("room_charges") or 0.0),
            tax                 = float(data.get("tax") or data.get("taxes") or data.get("taxes_and_fees") or 0.0),
            tip                 = float(data.get("tip") or 0.0),
            fees                = fees,
            base_fare           = _f("base_fare"),
            distance_miles      = data.get("distance_miles"),
            duration_minutes    = data.get("duration_minutes"),
            origin_address      = data.get("origin_address"),
            destination_address = data.get("destination_address"),
            driver_name         = data.get("driver_name"),
            trip_id             = data.get("trip_id"),
            restaurant_name     = data.get("restaurant_name"),
            num_guests          = _fi("num_guests"),
            items               = items,
            hotel_name          = data.get("hotel_name"),
            check_in_date       = data.get("check_in_date"),
            check_out_date      = data.get("check_out_date"),
            num_nights          = _fi("num_nights"),
            rate_per_night      = _f("rate_per_night"),
            room_charges        = _f("room_charges"),
            confirmation_number = data.get("confirmation_number"),
            folio_number        = data.get("folio_number"),
            guest_name          = data.get("guest_name"),
            airline             = data.get("airline"),
            flight_number       = data.get("flight_number"),
            origin              = data.get("origin"),
            destination         = data.get("destination"),
            travel_date         = data.get("travel_date"),
            passenger_name      = data.get("passenger_name"),
            booking_reference   = data.get("booking_reference"),
            cabin_class         = data.get("cabin_class"),
            taxes_and_fees      = _f("taxes_and_fees"),
            facility_name       = data.get("facility_name"),
            entry_date          = data.get("entry_date"),
            exit_date           = data.get("exit_date"),
            duration_hours      = data.get("duration_hours"),
            extracted_at        = datetime.now(timezone.utc).isoformat(),
        )
