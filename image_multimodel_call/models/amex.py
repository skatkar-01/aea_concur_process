"""
models/amex.py
Data models for extracted AMEX statement data.
Imports only from models/enums.py — nothing else in this project.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from models.enums import ExtractionMethod, ValidationStatus


# ── Amount helpers ─────────────────────────────────────────────────────────────

def parse_amount(raw) -> Optional[Decimal]:
    """
    Parse raw value to Decimal.
      None / "" / "null" → None         (blank cell)
      "0.00"             → Decimal(0)   (explicit zero)
      "(90.73)"          → Decimal(-90.73)
      "1,477.97"         → Decimal(1477.97)
    """
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        try:
            return Decimal(str(round(float(raw), 4)))
        except InvalidOperation:
            return None
    s = str(raw).strip()
    if not s or s.lower() in ("null", "none", "n/a", "-", "–", ""):
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace(" ", "")
    try:
        v = Decimal(s)
        return -v if negative else v
    except InvalidOperation:
        return None


def fmt_amount(v: Optional[Decimal]) -> str:
    """None → ''   Decimal → '12.34'   Never adds currency symbols."""
    if v is None:
        return ""
    return f"{v:.2f}"


# ── Core models ────────────────────────────────────────────────────────────────

@dataclass
class AmexTransaction:
    last_name:        str
    first_name:       str
    card_number:      str
    process_date:     Optional[str]      # "MM/DD/YYYY" or None
    merchant_name:    Optional[str]
    transaction_desc: Optional[str]
    current_opening:  Optional[Decimal]  # None = blank cell (not zero)
    charges:          Optional[Decimal]
    credits:          Optional[Decimal]
    current_closing:  Optional[Decimal]
    is_total_row:     bool = False

    def to_dict(self) -> dict:
        return {
            "last_name":        self.last_name,
            "first_name":       self.first_name,
            "card_number":      self.card_number,
            "process_date":     self.process_date,
            "merchant_name":    self.merchant_name,
            "transaction_desc": self.transaction_desc,
            "current_opening":  fmt_amount(self.current_opening) or None,
            "charges":          fmt_amount(self.charges)          or None,
            "credits":          fmt_amount(self.credits)          or None,
            "current_closing":  fmt_amount(self.current_closing)  or None,
            "is_total_row":     self.is_total_row,
        }


@dataclass
class AmexCardholder:
    last_name:    str
    first_name:   str
    card_number:  str
    transactions: list[AmexTransaction] = field(default_factory=list)
    total_row:    Optional[AmexTransaction] = None

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def total_charges(self) -> Optional[Decimal]:
        """Total charges from the cardholder total row."""
        return self.total_row.charges if self.total_row else None

    @property
    def total_credits(self) -> Optional[Decimal]:
        return self.total_row.credits if self.total_row else None

    def data_transactions(self) -> list[AmexTransaction]:
        return [t for t in self.transactions if not t.is_total_row]

    def to_dict(self) -> dict:
        return {
            "last_name":    self.last_name,
            "first_name":   self.first_name,
            "card_number":  self.card_number,
            "transactions": [t.to_dict() for t in self.data_transactions()],
            "total_row":    self.total_row.to_dict() if self.total_row else None,
        }


@dataclass
class AmexStatement:
    """Root model — one per AMEX PDF file."""
    source_file:      str
    company_name:     str
    statement_type:   str
    period:           str
    cardholders:      list[AmexCardholder] = field(default_factory=list)
    extraction_method: ExtractionMethod = ExtractionMethod.AZURE_HYBRID
    extracted_at:     str = ""
    confidence:       float = 0.0
    page_count:       int = 0
    processing_ms:    int = 0

    def all_transactions(self) -> list[AmexTransaction]:
        return [t for ch in self.cardholders for t in ch.data_transactions()]

    def cardholder_by_name(self, last_name: str) -> Optional[AmexCardholder]:
        """Case-insensitive lookup by last name."""
        target = last_name.upper().strip()
        for ch in self.cardholders:
            if ch.last_name.upper().strip() == target:
                return ch
        return None

    def to_dict(self) -> dict:
        return {
            "source_file":       self.source_file,
            "company_name":      self.company_name,
            "statement_type":    self.statement_type,
            "period":            self.period,
            "cardholders":       [ch.to_dict() for ch in self.cardholders],
            "extraction_method": self.extraction_method.value,
            "extracted_at":      self.extracted_at,
            "confidence":        self.confidence,
            "page_count":        self.page_count,
            "processing_ms":     self.processing_ms,
        }
