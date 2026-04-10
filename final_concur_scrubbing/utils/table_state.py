"""
utils/table_state.py
─────────────────────
Azure Table Storage state tracker.

Replaces blob_state.py with a proper per-file row model.
Each file gets its own row — no single-blob contention, no ETag races.

Table: "amexjobs"  (one table, partition by month)

Schema:
  PartitionKey = "2026-03"
  RowKey       = "ALTHAUS_B_report.pdf"   (sanitised filename)
  Status       = "PENDING" | "PROCESSING" | "DONE" | "FAILED"
  PdfType      = "amex" | "concur"
  JobId        = "uuid"
  RetryCount   = 0
  LastError    = ""
  EnqueuedAt   = ISO timestamp
  ProcessedAt  = ISO timestamp or ""
  SheetName    = "March 2026"
  AmexFile     = "March_Statement.pdf"    (set when AMEX initialised)

Status machine:
  PENDING    → PROCESSING  (when queue trigger picks it up)
  PROCESSING → DONE        (on success)
  PROCESSING → FAILED      (on unrecoverable error, or retry_count > max)
  FAILED     → PENDING     (manual requeue from UI / dead-letter review)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

# ── Status constants ──────────────────────────────────────────────────────────
PENDING    = "PENDING"
PROCESSING = "PROCESSING"
DONE       = "DONE"
FAILED     = "FAILED"

TABLE_NAME = "amexjobs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def _safe_row_key(filename: str) -> str:
    """
    Azure Table row keys cannot contain: / \\ # ? and control chars.
    Replace them with underscores.
    """
    return re.sub(r'[/\\#?\x00-\x1f]', '_', filename)


# ── Table state manager ───────────────────────────────────────────────────────

class TableStateManager:
    """
    Azure Table Storage backed state manager.

    Each file = one row.  No contention between concurrent Function instances
    because each instance operates on a different row (different RowKey).

    The only contention case is two instances both trying to mark AMEX
    "initialized" — handled by checking status before writing.
    """

    def __init__(self, connection_string: str, table_name: str = TABLE_NAME) -> None:
        from azure.data.tables import TableServiceClient
        svc = TableServiceClient.from_connection_string(connection_string)
        self._client = svc.get_table_client(table_name)
        self._ensure_table(svc, table_name)

    def _ensure_table(self, svc, name: str) -> None:
        try:
            svc.create_table(name)
            logger.info("table_created", table=name)
        except Exception:
            pass   # already exists

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get(self, partition: str, row: str) -> Optional[dict]:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return dict(self._client.get_entity(partition, row))
        except ResourceNotFoundError:
            return None

    def _upsert(self, entity: dict) -> None:
        from azure.data.tables import UpdateMode
        self._client.upsert_entity(entity, mode=UpdateMode.MERGE)

    # ── Job lifecycle ─────────────────────────────────────────────────────────

    def upsert_job(
        self,
        year: int, month: int,
        filename: str,
        pdf_type: str,
        job_id: str,
        status: str = PENDING,
        sheet_name: str = "",
        source: str = "",
    ) -> None:
        """Create or update a job row. Idempotent."""
        pk  = _month_key(year, month)
        row = _safe_row_key(filename)
        existing = self._get(pk, row)
        # Don't overwrite a DONE row from a catch-up re-scan
        if existing and existing.get("Status") == DONE:
            return
        entity = {
            "PartitionKey": pk,
            "RowKey":       row,
            "Status":       status,
            "PdfType":      pdf_type,
            "JobId":        job_id,
            "Filename":     filename,
            "SheetName":    sheet_name,
            "Source":       source,
            "RetryCount":   existing.get("RetryCount", 0) if existing else 0,
            "LastError":    existing.get("LastError", "")  if existing else "",
            "EnqueuedAt":   existing.get("EnqueuedAt", _now()) if existing else _now(),
            "ProcessedAt":  "",
        }
        self._upsert(entity)

    def mark_processing(self, year: int, month: int, filename: str, job_id: str) -> None:
        pk  = _month_key(year, month)
        row = _safe_row_key(filename)
        entity = self._get(pk, row) or {"PartitionKey": pk, "RowKey": row}
        entity["Status"]  = PROCESSING
        entity["JobId"]   = job_id
        self._upsert(entity)

    def mark_done(self, year: int, month: int, filename: str,
                  sheet_name: str = "", amex_file: str = "") -> None:
        pk  = _month_key(year, month)
        row = _safe_row_key(filename)
        entity = self._get(pk, row) or {"PartitionKey": pk, "RowKey": row}
        entity["Status"]      = DONE
        entity["ProcessedAt"] = _now()
        if sheet_name:
            entity["SheetName"] = sheet_name
        if amex_file:
            entity["AmexFile"] = amex_file
        entity["LastError"] = ""
        self._upsert(entity)
        logger.info("table_job_done", partition=pk, file=filename)

    def mark_failed(self, year: int, month: int, filename: str,
                    error: str, retry_count: int) -> None:
        pk  = _month_key(year, month)
        row = _safe_row_key(filename)
        entity = self._get(pk, row) or {"PartitionKey": pk, "RowKey": row}
        entity["Status"]     = FAILED
        entity["LastError"]  = error[:1000]   # table cell limit guard
        entity["RetryCount"] = retry_count
        entity["ProcessedAt"] = _now()
        self._upsert(entity)
        logger.warning("table_job_failed", partition=pk, file=filename, error=error[:120])

    # ── Query helpers ─────────────────────────────────────────────────────────

    def is_done(self, year: int, month: int, filename: str) -> bool:
        entity = self._get(_month_key(year, month), _safe_row_key(filename))
        return entity is not None and entity.get("Status") == DONE

    def is_amex_initialized(self, year: int, month: int) -> bool:
        """
        AMEX is initialized when any AMEX row for this month is DONE.
        """
        pk = _month_key(year, month)
        try:
            rows = list(self._client.query_entities(
                f"PartitionKey eq '{pk}' and PdfType eq 'amex' and Status eq 'DONE'"
            ))
            return len(rows) > 0
        except Exception:
            return False

    def get_amex_file(self, year: int, month: int) -> Optional[str]:
        """Return the AMEX filename for this month (for reconciliation look-up)."""
        pk = _month_key(year, month)
        try:
            rows = list(self._client.query_entities(
                f"PartitionKey eq '{pk}' and PdfType eq 'amex' and Status eq 'DONE'"
            ))
            return rows[0].get("Filename") if rows else None
        except Exception:
            return None

    def list_month(self, year: int, month: int) -> list[dict]:
        """Return all job rows for a given month."""
        pk = _month_key(year, month)
        try:
            return [dict(e) for e in self._client.query_entities(
                f"PartitionKey eq '{pk}'"
            )]
        except Exception:
            return []

    def list_failed(self) -> list[dict]:
        """Return all FAILED rows across all months."""
        try:
            return [dict(e) for e in self._client.query_entities(
                "Status eq 'FAILED'"
            )]
        except Exception:
            return []

    def pending_files(
        self,
        year: int,
        month: int,
        all_files: list,
    ) -> list:
        """Return files that are NOT DONE — for catch-up scan."""
        done = {
            e.get("Filename", "")
            for e in self.list_month(year, month)
            if e.get("Status") == DONE
        }
        return [
            f for f in all_files
            if (f.name if hasattr(f, "name") else str(f)) not in done
        ]

    def snapshot(self) -> dict:
        """Return a summary dict keyed by month — for the UI dashboard."""
        result: dict[str, list] = {}
        try:
            for entity in self._client.list_entities():
                pk = entity.get("PartitionKey", "unknown")
                result.setdefault(pk, []).append(dict(entity))
        except Exception as exc:
            logger.warning("table_snapshot_failed", error=str(exc))
        return result


def table_state_from_settings() -> TableStateManager:
    from config.settings import get_settings
    s = get_settings()
    return TableStateManager(
        connection_string=s.azure_storage_connection_string,
        table_name=s.azure_table_name,
    )
