"""
models/enums.py
All enums in one place. Zero imports from this project.
No other module in models/ imports from another models/ file — only from enums.
"""
from __future__ import annotations
from enum import Enum


class ExpenseType(str, Enum):
    RIDE     = "ride"
    MEAL     = "meal"
    HOTEL    = "hotel"
    FLIGHT   = "flight"
    PARKING  = "parking"
    OTHER    = "other"


class ExtractionMethod(str, Enum):
    COORDINATE   = "coordinate"    # rule-based bbox parser
    AZURE_VISION = "azure_vision"  # LLM image-only (scanned)
    AZURE_HYBRID = "azure_hybrid"  # LLM image + raw text (digital)


class MatchConfidence(str, Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


class MatchStatus(str, Enum):
    MATCHED   = "matched"
    MISMATCH  = "mismatch"
    MISSING   = "missing"    # no receipt found for transaction
    UNLINKED  = "unlinked"   # receipt found but not matched to any transaction


class RecordStatus(str, Enum):
    APPROVED        = "approved"
    FLAGGED         = "flagged"
    PENDING_REVIEW  = "pending_review"
    ERROR           = "error"


class ValidationStatus(str, Enum):
    PASSED  = "passed"
    PARTIAL = "partial"
    FAILED  = "failed"


class PageType(str, Enum):
    CONCUR_REPORT   = "concur_report"
    AUDIT_TRAIL     = "audit_trail"
    RECEIPT_RIDE    = "receipt_ride"
    RECEIPT_MEAL    = "receipt_meal"
    RECEIPT_HOTEL   = "receipt_hotel"
    RECEIPT_FLIGHT  = "receipt_flight"
    RECEIPT_PARKING = "receipt_parking"
    RECEIPT_OTHER   = "receipt_other"
    UNKNOWN         = "unknown"

    @classmethod
    def is_receipt(cls, value: str) -> bool:
        return value.startswith("receipt_")

    @classmethod
    def is_concur(cls, value: str) -> bool:
        return value in (cls.CONCUR_REPORT.value, cls.AUDIT_TRAIL.value)
