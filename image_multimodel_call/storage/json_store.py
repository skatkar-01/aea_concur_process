"""
storage/json_store.py
Writes raw extraction JSON to outputs/json/ as an audit trail.
One file per source PDF per run.
No business logic — pure I/O.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.exceptions import StorageError
from shared.logger import get_logger

log = get_logger(__name__)


def _json_safe(obj: Any) -> Any:
    """JSON serialisation fallback for non-serialisable types."""
    if hasattr(obj, "value"):      # Enum
        return obj.value
    if hasattr(obj, "to_dict"):    # model objects
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


class JSONStore:
    """Writes structured extraction data as JSON for audit/replay."""

    def __init__(self, json_dir: Path):
        self.json_dir = Path(json_dir)
        self.json_dir.mkdir(parents=True, exist_ok=True)

    def write(self, stem: str, data: dict) -> Path:
        """
        Write data to outputs/json/<stem>_<timestamp>.json.
        Returns the path written to.
        Raises StorageError on failure.
        """
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{stem}_{ts}.json"
        path     = self.json_dir / filename

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=_json_safe)
            log.debug("JSON written: %s", filename)
            return path
        except Exception as exc:
            raise StorageError(f"Failed to write JSON {filename}: {exc}") from exc

    def write_amex(self, statement) -> Path:
        """Write an AmexStatement to JSON."""
        stem = Path(statement.source_file).stem
        return self.write(f"amex_{stem}", statement.to_dict())

    def write_concur(self, report) -> Path:
        """Write a ConcurReport to JSON."""
        stem = Path(report.source_file).stem
        return self.write(f"concur_{stem}", report.to_dict())

    def write_receipts(self, source_file: str, receipts: list) -> Path:
        """Write all receipts from one PDF to JSON."""
        stem = Path(source_file).stem
        return self.write(
            f"receipts_{stem}",
            {
                "source_file":   source_file,
                "receipt_count": len(receipts),
                "receipts":      [r.to_dict() for r in receipts],
            },
        )

    def write_run_results(self, records: list, run_summary: dict) -> Path:
        """Write full run results including all tracker records."""
        return self.write(
            "run_results",
            {
                "run_summary": run_summary,
                "records":     [r.to_dict() for r in records],
            },
        )
