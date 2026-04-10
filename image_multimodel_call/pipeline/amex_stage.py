"""
pipeline/amex_stage.py
Change: accepts metrics=, calls metrics.set_source_file() before each PDF.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from extractors.amex_extractor import AmexExtractor
from models.amex import AmexStatement
from shared.logger import get_logger
from storage.json_store import JSONStore

log = get_logger(__name__)


@dataclass
class AmexStageResult:
    statements:      list[AmexStatement] = field(default_factory=list)
    files_processed: int = 0
    errors:          list[str] = field(default_factory=list)


class AmexStage:
    def __init__(self, azure_client, json_store: JSONStore, metrics=None):
        self._extractor = AmexExtractor(azure_client)
        self._json      = json_store
        self._metrics   = metrics

    def run(self, input_folder: Path, period: str = "") -> AmexStageResult:
        result = AmexStageResult()
        pdfs   = sorted(Path(input_folder).glob("*.pdf"))

        if period:
            pdfs = [p for p in pdfs if period.upper() in p.name.upper()]

        if not pdfs:
            log.warning("No AMEX PDFs found in %s (period=%s)", input_folder, period or "all")
            return result

        log.info("Processing %d AMEX file(s)...", len(pdfs))

        for pdf in pdfs:
            if self._metrics:
                self._metrics.set_source_file(pdf.name)
            try:
                statement = self._extractor.extract(pdf)
                result.statements.append(statement)
                result.files_processed += 1
                self._json.write_amex(statement)
                log.info("  ✅ %s — %d cardholder(s)", pdf.name, len(statement.cardholders))
            except Exception as exc:
                msg = f"{pdf.name}: {exc}"
                result.errors.append(msg)
                log.error("  ❌ %s", msg)

        return result