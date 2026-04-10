"""
processor/output_processor.py
Saves an ExtractedReport as 4 named CSV files:
  {emp_name}_{report_date}_transactions.csv
  {emp_name}_{report_date}_receipts.csv
  {emp_name}_{report_date}_reconciliation.csv
  {emp_name}_{report_date}_approvals.csv   ← derived from employee_report
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from extractor.concur_extractor import ExtractedReport
from utils.logger import get_logger

logger = get_logger(__name__)


class OutputProcessor:

    def __init__(self, output_folder: str) -> None:
        self._output_folder = Path(output_folder)
        self._output_folder.mkdir(parents=True, exist_ok=True)

    def save(self, report: ExtractedReport) -> dict[str, Path]:
        """
        Write all 4 CSVs for a report.
        Returns a dict mapping table name → file path.
        """
        if not report.is_valid:
            logger.warning(
                "skipping_invalid_report",
                file=report.source_file,
                reason=report.parse_error,
            )
            return {}

        prefix = self._safe_prefix(report.output_prefix)
        out: dict[str, Path] = {}

        out["transactions"]   = self._write_csv(report.transactions,          prefix, "transactions")
        out["receipts"]       = self._write_csv(report.receipts,              prefix, "receipts")
        out["reconciliation"] = self._write_csv(report.reconciliation,        prefix, "reconciliation")
        out["approvals"]      = self._write_csv(self._build_approvals(report), prefix, "approvals")

        logger.info(
            "output_saved",
            prefix=prefix,
            paths={k: str(v) for k, v in out.items()},
        )
        return out

    # ── builders ───────────────────────────────────────────────────────────

    def _build_approvals(self, report: ExtractedReport) -> list[dict]:
        """
        Derive the approvals table from employee_report.
        Captures approval/payment status + all financial summary fields.
        """
        er = report.employee_report
        if not er:
            return []

        return [{
            "employee_name":           er.get("employee_name"),
            "employee_id":             er.get("employee_id"),
            "report_id":               er.get("report_id"),
            "report_date":             er.get("report_date"),
            "currency":                er.get("currency"),
            "approval_status":         er.get("approval_status"),
            "payment_status":          er.get("payment_status"),
            "report_total":            er.get("report_total"),
            "personal_expenses":       er.get("personal_expenses"),
            "total_amount_claimed":    er.get("total_amount_claimed"),
            "amount_approved":         er.get("amount_approved"),
            "amount_due_employee":     er.get("amount_due_employee"),
            "amount_due_company_card": er.get("amount_due_company_card"),
            "total_paid_by_company":   er.get("total_paid_by_company"),
            "amount_due_from_employee":er.get("amount_due_from_employee"),
            "total_paid_by_employee":  er.get("total_paid_by_employee"),
        }]

    # ── internals ──────────────────────────────────────────────────────────

    def _write_csv(self, records: list[dict], prefix: str, table: str) -> Path:
        out_path = self._output_folder / f"{prefix}_{table}.csv"
        if records:
            df = pd.DataFrame(records)
        else:
            df = pd.DataFrame()
            logger.warning("empty_table", table=table, prefix=prefix)

        df.to_csv(out_path, index=False, encoding="utf-8-sig")
        logger.debug("csv_written", path=str(out_path), rows=len(df))
        return out_path

    @staticmethod
    def _safe_prefix(prefix: str) -> str:
        """Remove characters that are unsafe in file names."""
        return re.sub(r"[^\w\-]", "_", prefix).strip("_")
