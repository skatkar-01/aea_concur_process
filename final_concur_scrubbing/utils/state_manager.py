"""
utils/state_manager.py
───────────────────────
Persists a JSON log of which PDFs have been processed, so re-runs
(catch-up scans, restarts) never double-process a file.

Schema:
{
  "2026-03": {
    "sheet_name": "March 2026",
    "amex_initialized": true,
    "amex_file": "March_Statement.pdf",
    "amex_initialized_at": "2026-03-04T14:30:00",
    "concur_processed": {
      "ALTHAUS_B_report.pdf": "2026-03-10T09:15:00",
      "ANSOUR_E_report.pdf":  "2026-03-12T11:20:00"
    },
    "last_catchup": "2026-03-28T23:00:00"
  }
}
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


class StateManager:
    """
    Thread-safe reader/writer for the processed_log.json state file.
    All methods read the latest file state before every operation
    (simple approach — fine for our low write-frequency use case).
    """

    def __init__(self, state_path: Path) -> None:
        self._path = state_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ── Internal I/O ──────────────────────────────────────────────────────────

    def _read(self) -> dict:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("state_read_error", error=str(exc))
        return {}

    def _write(self, data: dict) -> None:
        try:
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            import os
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.error("state_write_error", error=str(exc))

    # ── AMEX initialisation ───────────────────────────────────────────────────

    def is_amex_initialized(self, year: int, month: int) -> bool:
        """True if init_month_sheet has already run for this period."""
        with _LOCK:
            data = self._read()
            key  = _month_key(year, month)
            return data.get(key, {}).get("amex_initialized", False)

    def mark_amex_initialized(
        self, year: int, month: int, sheet_name: str, amex_filename: str
    ) -> None:
        with _LOCK:
            data = self._read()
            key  = _month_key(year, month)
            entry = data.setdefault(key, {})
            entry["sheet_name"]          = sheet_name
            entry["amex_initialized"]    = True
            entry["amex_file"]           = amex_filename
            entry["amex_initialized_at"] = _now_iso()
            entry.setdefault("concur_processed", {})
            self._write(data)
        logger.info("state_amex_marked", key=key, file=amex_filename)

    # ── Concur processing ─────────────────────────────────────────────────────

    def is_concur_processed(self, year: int, month: int, filename: str) -> bool:
        """True if this Concur PDF has already been patched into the tracker."""
        with _LOCK:
            data = self._read()
            key  = _month_key(year, month)
            return filename in data.get(key, {}).get("concur_processed", {})

    def mark_concur_processed(self, year: int, month: int, filename: str) -> None:
        with _LOCK:
            data  = self._read()
            key   = _month_key(year, month)
            entry = data.setdefault(key, {})
            entry.setdefault("concur_processed", {})[filename] = _now_iso()
            self._write(data)
        logger.info("state_concur_marked", key=key, file=filename)

    # ── Catch-up bookkeeping ──────────────────────────────────────────────────

    def mark_catchup_run(self, year: int, month: int) -> None:
        with _LOCK:
            data  = self._read()
            key   = _month_key(year, month)
            data.setdefault(key, {})["last_catchup"] = _now_iso()
            self._write(data)

    # ── Snapshot (for UI / logging) ───────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a full copy of the current state."""
        with _LOCK:
            return self._read()

    def pending_concur_files(
        self,
        year: int,
        month: int,
        all_concur_files: list[Path],
    ) -> list[Path]:
        """
        Return Concur PDFs from all_concur_files that have NOT yet been processed.
        Used by the catch-up scan.
        """
        with _LOCK:
            data = self._read()
            key  = _month_key(year, month)
            done = set(data.get(key, {}).get("concur_processed", {}).keys())
        return [f for f in all_concur_files if f.name not in done]
