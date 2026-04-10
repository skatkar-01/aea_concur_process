"""
models/receipt.py
Universal receipt model covering all expense types.
Imports only from models/enums.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from models.enums import ExpenseType


@dataclass
class ReceiptLineItem:
    description: str
    amount:      float
    quantity:    Optional[int] = None


@dataclass
class Receipt:
    """
    Universal receipt — one per parsed receipt group (may span multiple pages).
    All monetary values are float. None = field was blank on the document.
    """
    # Provenance
    source_pages:       list[int]
    receipt_type:       ExpenseType
    is_multi_page:      bool = False
    is_image_capture:   bool = False   # True if photo/scan rather than digital PDF
    grouping_confidence: str = "HIGH"

    # Core fields (present in all receipt types)
    vendor:             Optional[str]  = None
    date:               Optional[str]  = None
    total_charged:      float          = 0.0
    payment_method:     Optional[str]  = None
    payment_last4:      Optional[str]  = None
    payment_date:       Optional[str]  = None
    reference_number:   Optional[str]  = None

    # Financial breakdown
    subtotal:           float = 0.0
    tax:                float = 0.0
    tip:                float = 0.0
    fees:               dict  = field(default_factory=dict)   # surcharges, service charges etc.

    # Ride-specific
    base_fare:          Optional[float] = None
    distance_miles:     Optional[str]   = None
    duration_minutes:   Optional[str]   = None
    origin_address:     Optional[str]   = None
    destination_address: Optional[str]  = None
    driver_name:        Optional[str]   = None
    trip_id:            Optional[str]   = None

    # Meal-specific
    restaurant_name:    Optional[str]   = None
    num_guests:         Optional[int]   = None
    items:              list[ReceiptLineItem] = field(default_factory=list)

    # Hotel-specific
    hotel_name:         Optional[str]   = None
    check_in_date:      Optional[str]   = None
    check_out_date:     Optional[str]   = None
    num_nights:         Optional[int]   = None
    rate_per_night:     Optional[float] = None
    room_charges:       Optional[float] = None
    confirmation_number: Optional[str]  = None
    folio_number:       Optional[str]   = None
    guest_name:         Optional[str]   = None

    # Flight-specific
    airline:            Optional[str]   = None
    flight_number:      Optional[str]   = None
    origin:             Optional[str]   = None
    destination:        Optional[str]   = None
    travel_date:        Optional[str]   = None
    passenger_name:     Optional[str]   = None
    booking_reference:  Optional[str]   = None
    cabin_class:        Optional[str]   = None
    base_fare_flight:   Optional[float] = None
    taxes_and_fees:     Optional[float] = None

    # Parking-specific
    facility_name:      Optional[str]   = None
    entry_date:         Optional[str]   = None
    exit_date:          Optional[str]   = None
    duration_hours:     Optional[str]   = None

    # Parse metadata
    parse_error:        Optional[str]   = None
    extracted_at:       str = ""

    @property
    def effective_vendor(self) -> Optional[str]:
        """Returns the best available vendor name across all receipt types."""
        return (
            self.vendor or self.restaurant_name or
            self.hotel_name or self.airline or
            self.facility_name
        )

    @property
    def effective_date(self) -> Optional[str]:
        return (
            self.date or self.travel_date or
            self.check_in_date or self.entry_date or
            self.payment_date
        )

    def to_dict(self) -> dict:
        """Serialise to dict, excluding None fields and internal metadata."""
        skip = {"items"}
        result = {
            "source_pages":        self.source_pages,
            "receipt_type":        self.receipt_type.value,
            "is_multi_page":       self.is_multi_page,
            "is_image_capture":    self.is_image_capture,
            "grouping_confidence": self.grouping_confidence,
            "items": [
                {"description": i.description, "amount": i.amount}
                for i in self.items
            ],
        }
        for k, v in self.__dict__.items():
            if k in skip or k in result:
                continue
            if v is None or v == [] or v == {}:
                continue
            if k == "receipt_type":
                continue
            result[k] = v.value if hasattr(v, "value") else v
        return result
