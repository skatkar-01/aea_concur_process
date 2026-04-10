"""
src/reconciler.py
──────────────────
Compares an AMEX Cardholder against a ConcurRecord and produces a TrackerRow.

PRODUCTION FIXES (2024-Q2):
  - BUG 7: _comments_from_recon_table() accessed entry.comment, but
            ConcurReconEntry in models.py has no comment field — only
            match_status and confidence.  The field name was a stale reference
            to an older model revision.

    Fix:   Removed the entry.comment branch entirely.  The LLM-consolidated
           comment is read from TABLE 6 (report_summary.reconciliation_comment)
           by _comments_from_report_summary(), which is priority-1 and already
           covers the use-case.  _comments_from_recon_table() is now a pure
           structural fallback (priority-2) that synthesises comments from
           match_status + confidence only — no field it doesn't have.

Comment generation priority (unchanged):
  1. report_summary.reconciliation_comment — TABLE 6 LLM-consolidated string
  2. Unmatched / low-confidence entries from TABLE 5 (synthesised, no .comment)
  3. Receipt flags from transaction.comments
  4. AMEX vs Concur total delta (last-resort)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from src.models import (
    ConcurRecord, ConcurReconEntry, ConcurTransactionRow,
    Cardholder, Transaction,
)

logger = logging.getLogger(__name__)

AMOUNT_TOLERANCE = 0.01


# ── Output type ───────────────────────────────────────────────────────────────

@dataclass
class TrackerRow:
    """One row written into the Excel tracker sheet."""
    cardholder_name:  str
    amex_total:       Optional[float]
    concur_submitted: Optional[float]
    report_pdf:       Optional[bool]
    approvals:        Optional[bool]
    receipts:         Optional[bool]
    comments:         str           # never None — callers use _coerce_comments()
    no_charges:       bool = False

    @property
    def is_matched(self) -> bool:
        if self.amex_total is None or self.concur_submitted is None:
            return False
        return abs(self.amex_total - self.concur_submitted) <= AMOUNT_TOLERANCE


# ── Amount formatter ──────────────────────────────────────────────────────────

def _fmt(v: float) -> str:
    prefix = "-$" if v < 0 else "$"
    return f"{prefix}{abs(v):,.2f}"


# ═══════════════════════════════════════════════════════════════
# Comment generation
# ═══════════════════════════════════════════════════════════════

def _comments_from_report_summary(concur: ConcurRecord) -> list[str]:
    """
    Primary source: TABLE 6 report_summary.reconciliation_comment.
    Already consolidated by the LLM — use verbatim.
    """
    if concur.report_summary is None:
        return []
    txt = concur.report_summary.reconciliation_comment
    if not txt:
        return []
    if "no receipt discrepancies" in txt.lower():
        return []
    return [txt.strip()]


def _comments_from_recon_table(concur: ConcurRecord) -> list[str]:
    """
    Secondary source: TABLE 5 structural fields only.

    BUG 7 FIX: removed entry.comment access — ConcurReconEntry has no such
    field.  Comments are now synthesised from match_status and confidence,
    which are the fields that actually exist on the model.
    """
    comments: list[str] = []

    for entry in concur.reconciliation:
        txn = concur.get_transaction(entry.transaction_id or "")
        vendor = (
            (txn.vendor_description or txn.expense_type or "transaction")
            if txn else "transaction"
        )
        amount = txn.amount if txn else None
        amt_str = f" {_fmt(amount)}" if amount is not None else ""

        if not entry.is_matched:
            comments.append(f"Missing receipt: {vendor}{amt_str}")
        elif entry.confidence and entry.confidence.lower() in {"low", "medium"}:
            comments.append(
                f"{entry.confidence.capitalize()}-confidence match: {vendor}{amt_str}"
            )

    return comments


def _comments_from_receipt_flags(concur: ConcurRecord) -> list[str]:
    """Tertiary source: receipt issues noted in transaction.comments field."""
    comments: list[str] = []
    for txn in concur.transactions:
        flag = (txn.comments or "").lower()
        vendor = txn.vendor_description or txn.expense_type or "item"
        amount = txn.amount
        amt_str = f" {_fmt(amount)}" if amount is not None else ""
        if "missing receipt" in flag or "no receipt" in flag:
            comments.append(f"Missing receipt for {vendor}{amt_str}")
        elif "wrong receipt" in flag or "incorrect receipt" in flag:
            comments.append(f"Wrong receipt for {vendor}{amt_str} attached")
    return comments


def _comments_from_delta(amex_cardholder: Cardholder, concur: ConcurRecord) -> list[str]:
    """Last-resort fallback: plain AMEX vs Concur total delta."""
    amex_total = (
        amex_cardholder.total_row.current_closing
        if amex_cardholder.total_row else None
    )
    concur_total = concur.amount_submitted
    if (
        amex_total is not None
        and concur_total is not None
        and abs(amex_total - concur_total) > AMOUNT_TOLERANCE
    ):
        diff = amex_total - concur_total
        return [
            f"Amount delta {_fmt(diff)} unaccounted "
            f"(AMEX {_fmt(amex_total)} vs Concur {_fmt(concur_total)})"
        ]
    return []


def _generate_comments(amex_cardholder: Cardholder, concur: ConcurRecord) -> str:
    """Build the full comment string for column G (priority cascade)."""
    # 1. TABLE 6 — preferred consolidated string
    comments = _comments_from_report_summary(concur)
    if comments:
        return comments[0]

    # 2. TABLE 5 structural fallback
    if concur.reconciliation:
        comments = _comments_from_recon_table(concur)
    else:
        # 3. Receipt flags from transactions
        comments = _comments_from_receipt_flags(concur)

    # 4. Delta fallback
    if not comments:
        comments = _comments_from_delta(amex_cardholder, concur)

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped = []
    for c in comments:
        if c not in seen:
            seen.add(c)
            deduped.append(c)

    return "; ".join(deduped)


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def reconcile(amex_cardholder: Cardholder, concur: ConcurRecord) -> TrackerRow:
    """
    Produce a fully populated TrackerRow for a matched AMEX + Concur pair.
    """
    name = f"{amex_cardholder.last_name}, {amex_cardholder.first_name}".strip(", ")

    amex_total: Optional[float] = (
        amex_cardholder.total_row.current_closing
        if amex_cardholder.total_row else None
    )

    no_charges = amex_total is not None and abs(amex_total) < AMOUNT_TOLERANCE
    if no_charges:
        return TrackerRow(
            cardholder_name=name,
            amex_total=amex_total,
            concur_submitted=None,
            report_pdf=None,
            approvals=None,
            receipts=None,
            comments="",
            no_charges=True,
        )

    comments = _generate_comments(amex_cardholder, concur)

    return TrackerRow(
        cardholder_name=name,
        amex_total=amex_total,
        concur_submitted=concur.amount_submitted,
        report_pdf=concur.report_pdf_attached,
        approvals=concur.approvals_complete,
        receipts=concur.receipts_attached,
        comments=comments,
        no_charges=False,
    )


def reconcile_amex_only(amex_cardholder: Cardholder) -> TrackerRow:
    """
    Produce a TrackerRow for a cardholder whose Concur PDF has not yet arrived.
    Concur columns are left blank (None) — patched later by the watcher.
    """
    name = f"{amex_cardholder.last_name}, {amex_cardholder.first_name}".strip(", ")
    amex_total: Optional[float] = (
        amex_cardholder.total_row.current_closing
        if amex_cardholder.total_row else None
    )
    no_charges = amex_total is not None and abs(amex_total) < AMOUNT_TOLERANCE

    return TrackerRow(
        cardholder_name=name,
        amex_total=amex_total,
        concur_submitted=None,
        report_pdf=None,
        approvals=None,
        receipts=None,
        comments="",
        no_charges=no_charges,
    )