"""
models/concur.py
Data models for SAP Concur expense report data.
Imports only from models/enums.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from models.enums import ValidationStatus


@dataclass
class ConcurTransaction:
    transaction_date:  Optional[str]
    expense_type:      Optional[str]
    business_purpose:  Optional[str]
    vendor:            Optional[str]
    payment_type:      Optional[str]
    amount:            float
    cost_center:       Optional[str]
    project:           Optional[str]
    attendees:         list[str] = field(default_factory=list)
    receipt_required:  Optional[str] = None
    notes:             Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "transaction_date":  self.transaction_date,
            "expense_type":      self.expense_type,
            "business_purpose":  self.business_purpose,
            "vendor":            self.vendor,
            "payment_type":      self.payment_type,
            "amount":            self.amount,
            "cost_center":       self.cost_center,
            "project":           self.project,
            "attendees":         self.attendees,
            "receipt_required":  self.receipt_required,
            "notes":             self.notes,
        }


@dataclass
class ConcurTotals:
    report_total:          float = 0.0
    personal_expenses:     float = 0.0
    total_claimed:         float = 0.0
    amount_approved:       float = 0.0
    amount_due_employee:   float = 0.0
    amount_due_card:       float = 0.0
    total_paid_by_company: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class AuditEntry:
    timestamp: Optional[str]
    actor:     Optional[str]
    action:    Optional[str]
    detail:    Optional[str]

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ConcurReport:
    """Root model — one per Concur PDF."""
    source_file:      str
    report_name:      Optional[str]
    report_id:        Optional[str]
    report_date:      Optional[str]
    approval_status:  Optional[str]
    payment_status:   Optional[str]
    receipts_received: Optional[str]
    currency:         Optional[str]
    employee_name:    Optional[str]
    employee_id:      Optional[str]
    transactions:     list[ConcurTransaction] = field(default_factory=list)
    totals:           ConcurTotals = field(default_factory=ConcurTotals)
    audit_trail:      list[AuditEntry] = field(default_factory=list)
    extracted_at:     str = ""
    processing_ms:    int = 0

    @property
    def total_claimed(self) -> float:
        return self.totals.total_claimed

    def to_dict(self) -> dict:
        return {
            "source_file":       self.source_file,
            "report_name":       self.report_name,
            "report_id":         self.report_id,
            "report_date":       self.report_date,
            "approval_status":   self.approval_status,
            "payment_status":    self.payment_status,
            "receipts_received": self.receipts_received,
            "currency":          self.currency,
            "employee_name":     self.employee_name,
            "employee_id":       self.employee_id,
            "transactions":      [t.to_dict() for t in self.transactions],
            "totals":            self.totals.to_dict(),
            "audit_trail":       [a.to_dict() for a in self.audit_trail],
            "extracted_at":      self.extracted_at,
            "processing_ms":     self.processing_ms,
        }
