"""
src/models.py
──────────────
Pydantic v2 domain models.
All data flowing through the pipeline is validated against these schemas.
This catches extraction errors (wrong types, missing required fields)
before any downstream code touches the data.

AMEX models:   Transaction, Cardholder, Statement
Concur models: ConcurTransactionRow, ConcurEmployeeReport, ConcurApprovalEntry,
               ConcurReceipt, ConcurReconEntry, ConcurRecord
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


# ═══════════════════════════════════════════════════════════════════════════════
# Concur models  —  mirror the 5-table LLM extraction schema
# ═══════════════════════════════════════════════════════════════════════════════

# Shared helper for Concur string fields
def _clean_str(v: object) -> Optional[str]:
    if v is None or str(v).lower() in {"null", "none", ""}:
        return None
    return str(v).strip() or None


class ConcurTransactionRow(BaseModel):
    """TABLE 1: transactions — one expense line item from the Concur report."""
    transaction_id:     Optional[str]   = None   # e.g. "txn_1"
    transaction_date:   Optional[str]   = None
    expense_type:       Optional[str]   = None
    business_purpose:   Optional[str]   = None
    vendor_description: Optional[str]   = None
    payment_type:       Optional[str]   = None
    amount:             Optional[float] = None
    cost_center:        Optional[str]   = None
    project:            Optional[str]   = None
    attendees:          Optional[str]   = None
    comments:           Optional[str]   = None

    @field_validator("amount", mode="before")
    @classmethod
    def _amt(cls, v): return _parse_amount(v)

    @field_validator("transaction_id", "transaction_date", "expense_type",
                     "business_purpose", "vendor_description", "payment_type",
                     "cost_center", "project", "attendees", "comments", mode="before")
    @classmethod
    def _clean(cls, v): return _clean_str(v)


class ConcurEmployeeReport(BaseModel):
    """TABLE 2: employee_report — single object with financial totals and identity."""
    employee_name:            Optional[str]   = None
    employee_id:              Optional[str]   = None
    report_id:                Optional[str]   = None
    report_date:              Optional[str]   = None
    approval_status:          Optional[str]   = None   # "Approved" | "Pending" | ...
    payment_status:           Optional[str]   = None
    currency:                 Optional[str]   = None
    report_total:             Optional[float] = None
    personal_expenses:        Optional[float] = None
    total_amount_claimed:     Optional[float] = None
    amount_approved:          Optional[float] = None
    amount_due_employee:      Optional[float] = None
    amount_due_company_card:  Optional[float] = None
    total_paid_by_company:    Optional[float] = None
    amount_due_from_employee: Optional[float] = None
    total_paid_by_employee:   Optional[float] = None

    @field_validator(
        "report_total", "personal_expenses", "total_amount_claimed",
        "amount_approved", "amount_due_employee", "amount_due_company_card",
        "total_paid_by_company", "amount_due_from_employee", "total_paid_by_employee",
        mode="before",
    )
    @classmethod
    def _amt(cls, v): return _parse_amount(v)

    @field_validator("employee_name", "employee_id", "report_id", "report_date",
                     "approval_status", "payment_status", "currency", mode="before")
    @classmethod
    def _clean(cls, v): return _clean_str(v)

    @property
    def is_approved(self) -> bool:
        if not self.approval_status:
            return False
        return "approved" in self.approval_status.lower()

    @property
    def effective_amount(self) -> Optional[float]:
        """Best available total: total_amount_claimed → report_total."""
        return self.total_amount_claimed or self.report_total


class ConcurApprovalEntry(BaseModel):
    """TABLE 3: approval_log — one approval workflow step."""
    date:          Optional[str] = None
    approver_name: Optional[str] = None
    status:        Optional[str] = None
    note:          Optional[str] = None

    @field_validator("date", "approver_name", "status", "note", mode="before")
    @classmethod
    def _clean(cls, v): return _clean_str(v)

    @property
    def is_approved(self) -> bool:
        return bool(self.status and "approved" in self.status.lower())


class ConcurReceipt(BaseModel):
    """TABLE 4: receipts — one receipt/invoice extracted from the PDF."""
    receipt_id: Optional[str]   = None   # e.g. "rcp_1"
    order_id:   Optional[str]   = None
    date:       Optional[str]   = None
    vendor:     Optional[str]   = None
    amount:     Optional[float] = None
    summary:    Optional[str]   = None   # detailed line-item summary

    @field_validator("amount", mode="before")
    @classmethod
    def _amt(cls, v): return _parse_amount(v)

    @field_validator("receipt_id", "order_id", "date", "vendor", "summary", mode="before")
    @classmethod
    def _clean(cls, v): return _clean_str(v)


class ConcurReconEntry(BaseModel):
    """TABLE 5: reconciliation — one transaction↔receipt match result."""
    transaction_id: Optional[str] = None
    receipt_id:     Optional[str] = None
    match_status:   Optional[str] = None   # "matched" | "unmatched"
    confidence:     Optional[str] = None   # "high" | "medium" | "low"
    comment:        Optional[str] = None   # BUG 7 FIX: LLM may emit per-row notes

    @field_validator("transaction_id", "receipt_id", "match_status", "confidence",
                     "comment", mode="before")
    @classmethod
    def _clean(cls, v): return _clean_str(v)

    @property
    def is_matched(self) -> bool:
        return bool(self.match_status and self.match_status.lower() == "matched")

    @property
    def is_high_confidence(self) -> bool:
        return bool(self.confidence and self.confidence.lower() == "high")

class ConcurReportSummary(BaseModel):
    """TABLE 6: report_summary — aggregate comments about the whole report."""
    reconciliation_comment: Optional[str] = None
    approval_comment:       Optional[str] = None

    @field_validator("reconciliation_comment", "approval_comment", mode="before")
    @classmethod
    def _clean(cls, v): return _clean_str(v)

class ConcurRecord(BaseModel):
    """
    Full Concur expense report extracted from a Final Concur Report PDF.
    Wraps all 5 LLM-extracted tables.

    Join key for tracker reconciliation:
        cardholder_name — derived from employee_report.employee_name,
        normalised to "LAST, FIRST" uppercase.
    """
    transactions:    list[ConcurTransactionRow] = Field(default_factory=list)
    employee_report: ConcurEmployeeReport       = Field(default_factory=ConcurEmployeeReport)
    approval_log:    list[ConcurApprovalEntry]  = Field(default_factory=list)
    receipts:        list[ConcurReceipt]        = Field(default_factory=list)
    reconciliation:  list[ConcurReconEntry]     = Field(default_factory=list)
    report_summary:   ConcurReportSummary   = Field(default_factory=ConcurReportSummary)

    @property
    def cardholder_name(self) -> str:
        name = (self.employee_report.employee_name or "").strip()
        if not name:
            return ""
        if "," in name:
            return " ".join(name.upper().replace(",", ", ").split())
        parts = [part for part in name.upper().split() if part]
        if len(parts) == 1:
            return parts[0]
        return f"{parts[-1]}, {' '.join(parts[:-1])}"

    @property
    def amount_submitted(self) -> Optional[float]:
        return self.employee_report.effective_amount

    @property
    def report_pdf_attached(self) -> bool:
        return True   # we have the PDF — we just extracted it

    @property
    def approvals_complete(self) -> bool:
        if self.employee_report.is_approved:
            return True
        if self.approval_log:
            return self.approval_log[-1].is_approved
        return False

    @property
    def receipts_attached(self) -> bool:
        if not self.reconciliation:
            return bool(self.receipts)
        return all(e.is_matched for e in self.reconciliation)

    @property
    def unmatched_entries(self) -> list[ConcurReconEntry]:
        return [e for e in self.reconciliation if not e.is_matched]

    @property
    def matched_count(self) -> int:
        return sum(1 for e in self.reconciliation if e.is_matched)

    @property
    def unmatched_count(self) -> int:
        return len(self.unmatched_entries)

    def get_transaction(self, txn_id: str) -> Optional[ConcurTransactionRow]:
        return next((t for t in self.transactions if t.transaction_id == txn_id), None)

    def get_receipt(self, rcp_id: str) -> Optional[ConcurReceipt]:
        return next((r for r in self.receipts if r.receipt_id == rcp_id), None)


# Legacy alias — code that imported ConcurTransaction still works
ConcurTransaction = ConcurTransactionRow