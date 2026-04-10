"""
storage/report_writer.py
Writes two human-readable reports to outputs/reports/:
  1. validation_report_<ts>.txt  — full detail for every cardholder
  2. exception_report_<ts>.txt   — flagged items only (for reviewers)

No business logic — pure formatting and I/O.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

from models.enums import RecordStatus
from models.tracker import TrackerRecord, RunSummary
from shared.exceptions import StorageError
from shared.logger import get_logger

log = get_logger(__name__)

_DIV  = "=" * 70
_SDIV = "-" * 70


class ReportWriter:
    def __init__(self, reports_dir: Path):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    def write_all(
        self,
        records:     list[TrackerRecord],
        run_summary: RunSummary,
    ) -> dict[str, Path]:
        """Write both reports. Returns {name: path}."""
        outputs = {}
        for name, method, args in [
            ("validation_report", self._write_validation, (records, run_summary)),
            ("exception_report",  self._write_exceptions,  (records, run_summary)),
        ]:
            try:
                path = method(*args)
                outputs[name] = path
                log.info("Report written: %s", path.name)
            except Exception as exc:
                log.error("Report write failed '%s': %s", name, exc)
        return outputs

    # ── Validation report (all cardholders) ───────────────────────────────────

    def _write_validation(
        self,
        records:     list[TrackerRecord],
        run_summary: RunSummary,
    ) -> Path:
        lines = self._run_header(run_summary, "FULL VALIDATION REPORT")

        for record in records:
            lines += self._record_section(record)

        lines += self._run_footer(run_summary)
        return self._write(lines, f"validation_report_{self._ts}.txt")

    # ── Exception report (flagged only) ───────────────────────────────────────

    def _write_exceptions(
        self,
        records:     list[TrackerRecord],
        run_summary: RunSummary,
    ) -> Path:
        flagged = [r for r in records if r.status == RecordStatus.FLAGGED]
        lines   = self._run_header(run_summary, "EXCEPTION REPORT — FLAGGED ITEMS ONLY")

        if not flagged:
            lines += ["", "  ✅ No flagged items — all records approved.", ""]
        else:
            lines.append(f"  {len(flagged)} cardholder(s) require attention:\n")
            for record in flagged:
                lines += self._record_section(record, exceptions_only=True)

        lines += self._run_footer(run_summary)
        return self._write(lines, f"exception_report_{self._ts}.txt")

    # ── Section builders ──────────────────────────────────────────────────────

    def _run_header(self, run_summary: RunSummary, title: str) -> list[str]:
        icon = "✅" if run_summary.errors == 0 else "⚠️"
        return [
            _DIV,
            f"  AEA CONCUR PROCESS — {title}",
            f"  Run ID    : {run_summary.run_id}",
            f"  Period    : {run_summary.period}",
            f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            _DIV,
            "",
            "  RUN SUMMARY",
            f"  {icon} AMEX files      : {run_summary.amex_files}",
            f"  {icon} Concur files    : {run_summary.concur_files}",
            f"  Cardholders    : {run_summary.cardholders_total}",
            f"  ✅ Approved    : {run_summary.approved}",
            f"  🚩 Flagged     : {run_summary.flagged}",
            f"  ❌ Errors      : {run_summary.errors}",
            f"  Processing     : {run_summary.processing_ms}ms",
            "",
        ]

    def _record_section(
        self,
        record: TrackerRecord,
        exceptions_only: bool = False,
    ) -> list[str]:
        status_icon = {
            RecordStatus.APPROVED:       "✅",
            RecordStatus.FLAGGED:        "🚩",
            RecordStatus.PENDING_REVIEW: "⚠️",
            RecordStatus.ERROR:          "❌",
        }.get(record.status, "?")

        lines = [
            _SDIV,
            f"  {status_icon} {record.employee_name.upper()}  "
            f"|  Period: {record.period}  "
            f"|  Status: {record.status.value.upper()}",
            _SDIV,
            f"  Employee ID   : {record.employee_id or 'N/A'}",
            f"  Report ID     : {record.report_id or 'N/A'}",
            "",
            "  AMOUNT RECONCILIATION",
            f"  AMEX Total    : {f'${record.amex_total_charges:.2f}' if record.amex_total_charges is not None else 'N/A'}",
            f"  Concur Total  : {f'${record.concur_total_claimed:.2f}' if record.concur_total_claimed is not None else 'N/A'}",
            f"  Match         : {'✅ YES' if record.amounts_match else ('❌ NO' if record.amounts_match is False else 'N/A')}",
        ]

        if record.amount_diff and record.amount_diff > 0:
            lines.append(f"  Difference    : ${record.amount_diff:.2f}")

        lines += [
            "",
            "  RECEIPT MATCHING",
            f"  Transactions  : {record.transactions_total}",
            f"  Matched       : {record.receipts_matched}",
            f"  Missing       : {record.receipts_missing}",
            f"  Mismatch      : {record.receipts_mismatch}",
            f"  All matched   : {'✅ YES' if record.all_receipts_matched else ('❌ NO' if record.all_receipts_matched is False else 'N/A')}",
        ]

        # Per-transaction detail
        if record.transaction_match_results:
            lines.append("")
            lines.append("  TRANSACTION DETAIL")
            for mr in record.transaction_match_results:
                icon = "✅" if mr.overall_match else "❌"
                lines.append(
                    f"    {icon} TXN[{mr.transaction_index}]  "
                    f"type={mr.receipt_type or '?'}  "
                    f"pages={mr.receipt_pages}  "
                    f"conf={mr.confidence.value}  "
                    f"status={mr.status.value}"
                )
                lines.append(f"       {mr.summary}")
                for fr in mr.field_results:
                    ficon = "✅" if fr.matched else "❌"
                    lines.append(
                        f"       {ficon} {fr.field_name.upper():<14} "
                        f"TXN: {str(fr.transaction_value or ''):<22} "
                        f"RECEIPT: {fr.receipt_value or ''}"
                    )
                    if not fr.matched and fr.note:
                        lines.append(f"             ↳ {fr.note}")
                for disc in mr.discrepancies:
                    lines.append(f"       ⚠️  {disc}")

        # Flags and warnings
        if record.flags:
            lines.append("")
            lines.append("  FLAGS (require resolution):")
            for f in record.flags:
                lines.append(f"    ❌ {f}")

        if record.warnings:
            lines.append("")
            lines.append("  WARNINGS (reviewer attention):")
            for w in record.warnings:
                lines.append(f"    ⚠️  {w}")

        lines.append("")
        return lines

    def _run_footer(self, run_summary: RunSummary) -> list[str]:
        if run_summary.error_details:
            lines = ["", _DIV, "  ERRORS DURING RUN", _DIV]
            for err in run_summary.error_details:
                lines.append(f"  ❌ {err}")
            lines.append("")
            return lines
        return ["", _DIV, ""]

    def _write(self, lines: list[str], filename: str) -> Path:
        path = self.reports_dir / filename
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return path
        except Exception as exc:
            raise StorageError(f"Failed to write {filename}: {exc}") from exc
