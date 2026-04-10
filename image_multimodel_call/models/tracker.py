"""
models/tracker.py
Tracker data models — the central record of every cardholder's processing state.
Imports only from models/enums.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from models.enums import MatchConfidence, MatchStatus, RecordStatus


@dataclass
class FieldMatchResult:
    """Result of comparing one field (amount/date/vendor/description) between txn and receipt."""
    field_name:        str
    matched:           bool
    transaction_value: Optional[str]
    receipt_value:     Optional[str]
    note:              Optional[str] = None


@dataclass
class TransactionMatchResult:
    """Match result for one Concur transaction against its receipt."""
    transaction_index:  int
    receipt_pages:      list[int]
    receipt_type:       Optional[str]
    overall_match:      bool
    confidence:         MatchConfidence
    status:             MatchStatus
    field_results:      list[FieldMatchResult] = field(default_factory=list)
    discrepancies:      list[str]              = field(default_factory=list)
    summary:            str                    = ""

    def to_dict(self) -> dict:
        return {
            "transaction_index":  self.transaction_index,
            "receipt_pages":      self.receipt_pages,
            "receipt_type":       self.receipt_type,
            "overall_match":      self.overall_match,
            "confidence":         self.confidence.value,
            "status":             self.status.value,
            "field_results": [
                {
                    "field":             f.field_name,
                    "matched":           f.matched,
                    "transaction_value": f.transaction_value,
                    "receipt_value":     f.receipt_value,
                    "note":              f.note,
                }
                for f in self.field_results
            ],
            "discrepancies": self.discrepancies,
            "summary":       self.summary,
        }


@dataclass
class AmountReconciliationResult:
    """Result of comparing AMEX total vs Concur total for one cardholder."""
    employee_name:   str
    period:          str
    amex_total:      Optional[float]
    concur_total:    Optional[float]
    difference:      float
    matched:         bool
    note:            str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class TrackerRecord:
    """
    One row in the master tracker — one per cardholder per period.
    This is the primary output of the pipeline and the source of truth
    for the current state of each cardholder's expense report.
    """
    # Identity
    employee_name:    str
    employee_id:      Optional[str]
    period:           str
    report_id:        Optional[str]

    # AMEX data
    amex_source_file:   Optional[str]  = None
    amex_total_charges: Optional[float] = None
    amex_total_credits: Optional[float] = None

    # Concur data
    concur_source_file:   Optional[str]  = None
    concur_total_claimed: Optional[float] = None
    concur_amount_approved: Optional[float] = None

    # Reconciliation
    amounts_match:    Optional[bool]  = None
    amount_diff:      Optional[float] = None

    # Receipt matching
    transactions_total:   int = 0
    receipts_matched:     int = 0
    receipts_missing:     int = 0
    receipts_mismatch:    int = 0
    all_receipts_matched: Optional[bool] = None

    # Flags and notes
    flags:    list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes:    Optional[str] = None

    # Status
    status:       RecordStatus = RecordStatus.PENDING_REVIEW
    last_updated: str = field(
        default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    )

    # Full detail (stored in JSON, not tracker sheet)
    transaction_match_results: list[TransactionMatchResult] = field(default_factory=list)

    def update_status(self) -> None:
        """Derive status from match results. Call after all matching is complete."""
        hard_fail = (
            self.amounts_match is False
            or self.receipts_missing > 0
            or self.receipts_mismatch > 0
            or len(self.flags) > 0
        )
        if hard_fail:
            self.status = RecordStatus.FLAGGED
        elif self.amounts_match and self.all_receipts_matched:
            self.status = RecordStatus.APPROVED
        else:
            self.status = RecordStatus.PENDING_REVIEW
        self.last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    def to_tracker_row(self) -> dict:
        """Flat dict for writing to tracker Excel/CSV. One row per cardholder."""
        return {
            "employee_name":        self.employee_name,
            "employee_id":          self.employee_id or "",
            "period":               self.period,
            "report_id":            self.report_id or "",
            "amex_source_file":     self.amex_source_file or "",
            "amex_total_charges":   f"{self.amex_total_charges:.2f}" if self.amex_total_charges is not None else "",
            "amex_total_credits":   f"{self.amex_total_credits:.2f}" if self.amex_total_credits is not None else "",
            "concur_source_file":   self.concur_source_file or "",
            "concur_total_claimed": f"{self.concur_total_claimed:.2f}" if self.concur_total_claimed is not None else "",
            "amounts_match":        "YES" if self.amounts_match else ("NO" if self.amounts_match is False else ""),
            "amount_diff":          f"{self.amount_diff:.2f}" if self.amount_diff is not None else "",
            "transactions_total":   self.transactions_total,
            "receipts_matched":     self.receipts_matched,
            "receipts_missing":     self.receipts_missing,
            "receipts_mismatch":    self.receipts_mismatch,
            "all_receipts_matched": "YES" if self.all_receipts_matched else ("NO" if self.all_receipts_matched is False else ""),
            "flags":                " | ".join(self.flags),
            "warnings":             " | ".join(self.warnings),
            "notes":                self.notes or "",
            "status":               self.status.value.upper(),
            "last_updated":         self.last_updated,
        }

    def to_dict(self) -> dict:
        """Full dict including transaction match results for JSON output."""
        d = self.to_tracker_row()
        d["transaction_match_results"] = [
            r.to_dict() for r in self.transaction_match_results
        ]
        return d


@dataclass
class RunSummary:
    """Summary of one full pipeline run."""
    run_id:            str
    started_at:        str
    completed_at:      str = ""
    period:            str = ""
    amex_files:        int = 0
    concur_files:      int = 0
    cardholders_total: int = 0
    approved:          int = 0
    flagged:           int = 0
    errors:            int = 0
    processing_ms:     int = 0
    error_details:     list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# Fixed column order for tracker sheet — never changes between runs
TRACKER_COLUMNS = [
    "employee_name", "employee_id", "period", "report_id",
    "amex_source_file", "amex_total_charges", "amex_total_credits",
    "concur_source_file", "concur_total_claimed",
    "amounts_match", "amount_diff",
    "transactions_total", "receipts_matched", "receipts_missing", "receipts_mismatch",
    "all_receipts_matched",
    "flags", "warnings", "notes",
    "status", "last_updated",
]
