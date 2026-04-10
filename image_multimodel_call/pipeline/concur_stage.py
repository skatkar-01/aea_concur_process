"""
pipeline/concur_stage.py
Change: accepts metrics=, calls metrics.set_source_file() before each PDF.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from extractors.concur_extractor import ConcurExtractor
from extractors.receipt_extractor import ReceiptExtractor
from models.concur import ConcurReport
from models.receipt import Receipt
from shared.logger import get_logger
from storage.json_store import JSONStore

log = get_logger(__name__)


@dataclass
class ConcurStageResult:
    reports:          list[ConcurReport]       = field(default_factory=list)
    receipts_by_file: dict[str, list[Receipt]] = field(default_factory=dict)
    files_processed:  int                      = 0
    errors:           list[str]                = field(default_factory=list)


class ConcurStage:
    def __init__(self, azure_client, json_store: JSONStore, metrics=None):
        self._concur_ext  = ConcurExtractor(azure_client)
        self._receipt_ext = ReceiptExtractor(azure_client)
        self._json        = json_store
        self._metrics     = metrics

    def run(self, input_folder: Path, period: str = "") -> ConcurStageResult:
        result = ConcurStageResult()
        pdfs   = sorted(Path(input_folder).glob("*.pdf"))

        if period:
            pdfs = [p for p in pdfs if period.upper() in p.name.upper()]

        if not pdfs:
            log.warning("No Concur PDFs found in %s (period=%s)", input_folder, period or "all")
            return result

        log.info("Processing %d Concur file(s)...", len(pdfs))

        for pdf in pdfs:
            if self._metrics:
                self._metrics.set_source_file(pdf.name)
            try:
                report   = self._concur_ext.extract(pdf)
                receipts = self._receipt_ext.extract(pdf)

                result.reports.append(report)
                result.receipts_by_file[pdf.name] = receipts
                result.files_processed += 1

                self._json.write_concur(report)
                self._json.write_receipts(pdf.name, receipts)

                log.info(
                    "  ✅ %s — %d txn(s), %d receipt(s)",
                    pdf.name, len(report.transactions), len(receipts),
                )
            except Exception as exc:
                msg = f"{pdf.name}: {exc}"
                result.errors.append(msg)
                log.error("  ❌ %s", msg)

        return result