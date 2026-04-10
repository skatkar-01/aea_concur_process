"""
src/models.py
──────────────
Pydantic v2 domain models.
All data flowing through the pipeline is validated against these schemas.
This catches extraction errors (wrong types, missing required fields)
before any downstream code touches the data.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Helpers ───────────────────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(r"^\((\d+(?:\.\d+)?)\)$")   # "(90.73)" → "-90.73"


def _parse_amount(v: object) -> Optional[float]:
    """
    Normalise amount strings to float:
        null / "" / "null"  → None
        "(90.73)"           → -90.73
        "1,234.56"          → 1234.56
        "$1,234.56"         → 1234.56
    """
    if v is None or v == "" or v == "null":
        return None
    s = str(v).strip().replace(",", "").replace("$", "")
    m = _AMOUNT_RE.match(s)
    if m:
        return -float(m.group(1))
    try:
        return float(s)
    except ValueError:
        return None


# ── Transaction ───────────────────────────────────────────────────────────────

class Transaction(BaseModel):
    last_name:        Optional[str]   = None
    first_name:       Optional[str]   = None
    card_number:      Optional[str]   = None
    process_date:     Optional[str]   = None   # "MM/DD/YYYY" or None
    merchant_name:    Optional[str]   = None
    transaction_desc: Optional[str]   = None
    current_opening:  Optional[float] = None
    charges:          Optional[float] = None
    credits:          Optional[float] = None
    current_closing:  Optional[float] = None
    is_total_row:     bool            = False

    # ------------------------------------------------------------------
    @field_validator(
        "current_opening", "charges", "credits", "current_closing",
        mode="before",
    )
    @classmethod
    def _coerce_amount(cls, v: object) -> Optional[float]:
        return _parse_amount(v)

    @field_validator("process_date", mode="before")
    @classmethod
    def _normalise_date(cls, v: object) -> Optional[str]:
        if v is None or str(v).lower() in {"null", "none", ""}:
            return None
        return str(v).strip()

    @field_validator("last_name", "first_name", "card_number",
                     "merchant_name", "transaction_desc", mode="before")
    @classmethod
    def _clean_str(cls, v: object) -> Optional[str]:
        if v is None or str(v).lower() in {"null", "none", ""}:
            return None
        return str(v).strip() or None


# ── Cardholder ────────────────────────────────────────────────────────────────

class Cardholder(BaseModel):
    last_name:    str              = ""
    first_name:   str              = ""
    card_number:  str              = ""
    transactions: list[Transaction] = Field(default_factory=list)
    total_row:    Optional[Transaction] = None

    @model_validator(mode="after")
    def _inherit_names(self) -> "Cardholder":
        """
        If individual transactions are missing names / card numbers,
        inherit them from the cardholder-level fields.
        """
        for txn in self.transactions:
            txn.last_name   = txn.last_name   or self.last_name   or None
            txn.first_name  = txn.first_name  or self.first_name  or None
            txn.card_number = txn.card_number or self.card_number or None
        return self


# ── Statement (top-level document) ───────────────────────────────────────────

class Statement(BaseModel):
    company_name:   str               = ""
    statement_type: str               = ""
    period:         str               = ""
    cardholders:    list[Cardholder]  = Field(default_factory=list)

    @property
    def total_transactions(self) -> int:
        return sum(len(ch.transactions) for ch in self.cardholders)

    @property
    def total_cardholders(self) -> int:
        return len(self.cardholders)
