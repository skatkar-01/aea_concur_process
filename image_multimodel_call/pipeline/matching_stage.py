"""
pipeline/matching_stage.py
Change: accepts metrics=, calls metrics.set_source_file() before each report.
"""
from __future__ import annotations
from typing import Optional

from config.policy import get_policy
from models.amex import AmexStatement
from models.concur import ConcurReport
from models.enums import MatchStatus, RecordStatus
from models.receipt import Receipt
from models.tracker import TrackerRecord
from processors.amount_reconciler import AmountReconciler
from processors.receipt_matcher import ReceiptMatcher
from shared.logger import get_logger

log = get_logger(__name__)


class MatchingStage:
    def __init__(self, azure_client, metrics=None):
        self._reconciler = AmountReconciler()
        self._matcher    = ReceiptMatcher(azure_client)
        self._metrics    = metrics

    def run(
        self,
        amex_statements:  list[AmexStatement],
        concur_reports:   list[ConcurReport],
        receipts_by_file: dict[str, list[Receipt]],
    ) -> list[TrackerRecord]:
        records: list[TrackerRecord] = []

        for report in concur_reports:
            if self._metrics:
                self._metrics.set_source_file(report.source_file)
            try:
                record = self._process_one(
                    report, amex_statements,
                    receipts_by_file.get(report.source_file, []),
                )
                records.append(record)
            except Exception as exc:
                log.error("Matching failed for %s: %s", report.source_file, exc)
                records.append(self._error_record(report, str(exc)))

        log.info(
            "Matching complete: %d record(s) — ✅ %d approved  🚩 %d flagged",
            len(records),
            sum(1 for r in records if r.status == RecordStatus.APPROVED),
            sum(1 for r in records if r.status == RecordStatus.FLAGGED),
        )
        return records

    def _process_one(
        self,
        report:          ConcurReport,
        amex_statements: list[AmexStatement],
        receipts:        list[Receipt],
    ) -> TrackerRecord:
        policy = get_policy()
        amex   = self._find_amex(report.employee_name, amex_statements)

        record = TrackerRecord(
            employee_name          = report.employee_name or "",
            employee_id            = report.employee_id,
            period                 = self._derive_period(report, amex),
            report_id              = report.report_id,
            concur_source_file     = report.source_file,
            concur_total_claimed   = report.total_claimed,
            concur_amount_approved = report.totals.amount_approved,
        )

        if amex:
            record.amex_source_file = amex.source_file
            ch = amex.cardholder_by_name(self._extract_last_name(report.employee_name or ""))
            if ch:
                record.amex_total_charges = float(ch.total_charges or 0.0)
                record.amex_total_credits = float(ch.total_credits or 0.0)

        if amex:
            recon = self._reconciler.reconcile(amex, report)
            record.amounts_match = recon.matched
            record.amount_diff   = recon.difference
            if not recon.matched:
                record.flags.append(f"AMOUNT_MISMATCH: {recon.note}")
        else:
            record.warnings.append(f"No AMEX statement found for {report.employee_name}")

        txn_results = self._matcher.match(report.transactions, receipts)
        record.transaction_match_results = txn_results
        record.transactions_total        = len(report.transactions)
        record.receipts_matched   = sum(1 for r in txn_results if r.status == MatchStatus.MATCHED)
        record.receipts_missing   = sum(1 for r in txn_results if r.status == MatchStatus.MISSING)
        record.receipts_mismatch  = sum(1 for r in txn_results if r.status == MatchStatus.MISMATCH)
        record.all_receipts_matched = (
            record.receipts_matched == record.transactions_total
            and record.transactions_total > 0
        )

        for mr in txn_results:
            if mr.status == MatchStatus.MISSING:
                vendor = (
                    report.transactions[mr.transaction_index].vendor
                    if mr.transaction_index < len(report.transactions) else "?"
                )
                record.flags.append(f"MISSING_RECEIPT: txn[{mr.transaction_index}] — {vendor}")
            elif mr.status == MatchStatus.MISMATCH:
                record.flags.append(
                    f"RECEIPT_MISMATCH: txn[{mr.transaction_index}] — {mr.summary}"
                )
            if mr.confidence.value == "LOW":
                record.warnings.append(
                    f"LOW_CONFIDENCE: txn[{mr.transaction_index}] match confidence is LOW"
                )

        self._apply_policy_checks(record, report, policy)
        record.update_status()

        log.info(
            "  %s %s: amounts=%s receipts=%d/%d flags=%d",
            "✅" if record.status == RecordStatus.APPROVED else "🚩",
            record.employee_name,
            "✅" if record.amounts_match else "❌",
            record.receipts_matched, record.transactions_total, len(record.flags),
        )
        return record

    def _apply_policy_checks(self, record: TrackerRecord, report: ConcurReport, policy) -> None:
        if report.total_claimed > policy.auto_approve_max_usd:
            record.warnings.append(
                f"REVIEW_REQUIRED: total ${report.total_claimed:.2f} "
                f"exceeds auto-approve limit ${policy.auto_approve_max_usd:.2f}"
            )
        for txn in report.transactions:
            etype = (txn.expense_type or "other").lower()
            limit = policy.category_limits.get(etype, policy.category_limits.get("other"))
            if limit and txn.amount > limit:
                record.flags.append(
                    f"OVER_LIMIT: ${txn.amount:.2f} for '{etype}' exceeds limit ${limit:.2f}"
                )
            if policy.require_named_attendees:
                unnamed = [
                    a for a in (txn.attendees or [])
                    if a.strip().lower() in ("employee", "guest", "attendee", "")
                ]
                if unnamed:
                    record.warnings.append(
                        f"UNNAMED_ATTENDEE: {len(unnamed)} unnamed for '{txn.vendor}'"
                    )
            purpose = (txn.business_purpose or "").strip()
            if purpose and len(purpose.split()) < policy.min_purpose_words:
                record.warnings.append(f"VAGUE_PURPOSE: '{purpose}' for '{txn.vendor}'")

    @staticmethod
    def _find_amex(
        employee_name: Optional[str], statements: list[AmexStatement]
    ) -> Optional[AmexStatement]:
        if not employee_name:
            return None
        last = MatchingStage._extract_last_name(employee_name)
        for stmt in statements:
            if stmt.cardholder_by_name(last):
                return stmt
        return None

    @staticmethod
    def _extract_last_name(full_name: str) -> str:
        if not full_name:
            return ""
        name = full_name.strip()
        if "," in name:
            return name.split(",")[0].strip()
        parts = name.split()
        return parts[-1] if parts else ""

    @staticmethod
    def _derive_period(report: ConcurReport, amex: Optional[AmexStatement]) -> str:
        if amex and amex.period:
            return amex.period
        return report.report_date or ""

    @staticmethod
    def _error_record(report: ConcurReport, error: str) -> TrackerRecord:
        record = TrackerRecord(
            employee_name = report.employee_name or report.source_file,
            employee_id   = report.employee_id,
            period        = report.report_date or "",
            report_id     = report.report_id,
        )
        record.status = RecordStatus.ERROR
        record.flags.append(f"PIPELINE_ERROR: {error}")
        return record