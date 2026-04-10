"""
processors/amount_reconciler.py
Reconciles AMEX cardholder totals against Concur report totals.

This is Match 1 of 2 — the high-level check:
  "Does the total on the AMEX statement match what was submitted in Concur?"

Pure data transformation. No I/O. No LLM calls.
Input:  AmexStatement + ConcurReport
Output: AmountReconciliationResult
"""
from __future__ import annotations

from config.policy import get_policy
from models.amex import AmexStatement
from models.concur import ConcurReport
from models.tracker import AmountReconciliationResult
from shared.logger import get_logger

log = get_logger(__name__)


class AmountReconciler:
    """
    Compares the AMEX cardholder total charges against the Concur
    report total_claimed for the same employee/period.

    Matching strategy:
      1. Match by employee last name (case-insensitive)
      2. Fall back to comparing statement period to report date
      3. If no AMEX cardholder found → result.amex_total = None (flag as MISSING)
    """

    def reconcile(
        self,
        amex:   AmexStatement,
        concur: ConcurReport,
    ) -> AmountReconciliationResult:
        """
        Reconcile one AMEX statement against one Concur report.
        Returns AmountReconciliationResult.
        """
        policy        = get_policy()
        employee_name = concur.employee_name or ""
        period        = amex.period

        # Extract last name for lookup
        last_name = self._extract_last_name(employee_name)

        # Find the matching cardholder in the AMEX statement
        cardholder = amex.cardholder_by_name(last_name) if last_name else None

        if cardholder is None:
            log.warning(
                "No AMEX cardholder found for employee '%s' in statement '%s'",
                employee_name, amex.source_file,
            )
            return AmountReconciliationResult(
                employee_name = employee_name,
                period        = period,
                amex_total    = None,
                concur_total  = concur.total_claimed,
                difference    = 0.0,
                matched       = False,
                note          = f"No AMEX cardholder found for '{last_name}' in {amex.source_file}",
            )

        # Get AMEX total charges (from the total_row)
        amex_charges = float(cardholder.total_charges or 0.0)
        concur_total = round(concur.total_claimed, 2)
        amex_total   = round(amex_charges, 2)
        difference   = round(abs(amex_total - concur_total), 2)
        matched      = difference <= policy.amount_tolerance_usd

        log.info(
            "Reconciliation %s: AMEX=$%.2f  Concur=$%.2f  diff=$%.2f  %s",
            employee_name,
            amex_total,
            concur_total,
            difference,
            "✅ MATCH" if matched else "❌ MISMATCH",
        )

        note = ""
        if not matched:
            note = (
                f"AMEX charges ${amex_total:.2f} ≠ Concur claimed ${concur_total:.2f} "
                f"(diff ${difference:.2f}, tolerance ${policy.amount_tolerance_usd:.2f})"
            )

        return AmountReconciliationResult(
            employee_name = employee_name,
            period        = period,
            amex_total    = amex_total,
            concur_total  = concur_total,
            difference    = difference,
            matched       = matched,
            note          = note,
        )

    @staticmethod
    def _extract_last_name(full_name: str) -> str:
        """
        Extracts last name from employee name string.
        Handles: "Alers" / "Nicholas Alers" / "Alers, Nicholas"
        """
        if not full_name:
            return ""
        name = full_name.strip()
        # "Last, First" format
        if "," in name:
            return name.split(",")[0].strip()
        # "First Last" format — last word is last name
        parts = name.split()
        return parts[-1] if parts else ""
