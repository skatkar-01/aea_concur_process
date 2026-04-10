"""
utils/blob_state.py
────────────────────
Cloud replacement for utils/state_manager.py.

Instead of a local JSON file, state is stored in an Azure Blob Storage
container as a single JSON blob.  This means the state is:
  - Shared across all Azure Function instances (no local file race conditions)
  - Durable across restarts and deployments
  - Inspectable in the Azure portal

Container: set via AZURE_STORAGE_STATE_CONTAINER (default: "amex-state")
Blob name:  "processed_log.json"

Requires:
  pip install azure-storage-blob>=12.19.0

Env vars:
  AZURE_STORAGE_CONNECTION_STRING   or
  AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_ACCOUNT_KEY
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()
_BLOB_NAME = "processed_log.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


class BlobStateManager:
    """
    Thread-safe state manager backed by Azure Blob Storage.
    Identical public API to the local StateManager so callers are unaware
    of the backend difference.

    Uses an optimistic read-modify-write pattern with ETag-based concurrency.
    If two Function instances race, one will get a 412 Precondition Failed
    on write — it retries once by re-reading the latest state.
    """

    def __init__(self, connection_string: str, container: str = "amex-state") -> None:
        from azure.storage.blob import BlobServiceClient
        self._client = BlobServiceClient.from_connection_string(connection_string)
        self._container = container
        self._ensure_container()

    def _ensure_container(self) -> None:
        container_client = self._client.get_container_client(self._container)
        try:
            container_client.create_container()
            logger.info("blob_container_created", container=self._container)
        except Exception:
            pass   # already exists

    def _get_blob_client(self):
        return self._client.get_blob_client(
            container=self._container,
            blob=_BLOB_NAME,
        )

    # ── Read / write ──────────────────────────────────────────────────────────

    def _read(self) -> tuple[dict, Optional[str]]:
        """Returns (state_dict, etag). etag is None if blob doesn't exist."""
        blob = self._get_blob_client()
        try:
            dl = blob.download_blob()
            props = dl.properties
            content = dl.readall()
            return json.loads(content), props.get("etag")
        except Exception:
            return {}, None

    def _write(self, data: dict, etag: Optional[str]) -> bool:
        """
        Write state with optimistic concurrency.
        Returns True on success, False on ETag conflict (retry needed).
        """
        from azure.core.exceptions import ResourceModifiedError
        blob = self._client.get_blob_client(
            container=self._container, blob=_BLOB_NAME
        )
        content = json.dumps(data, indent=2).encode()
        try:
            if etag:
                blob.upload_blob(
                    content,
                    overwrite=True,
                    etag=etag,
                    match_condition=__import__(
                        "azure.core", fromlist=["MatchConditions"]
                    ).MatchConditions.IfNotModified,
                )
            else:
                blob.upload_blob(content, overwrite=True)
            return True
        except ResourceModifiedError:
            logger.warning("blob_etag_conflict_retrying")
            return False

    def _read_modify_write(self, modifier) -> None:
        """
        Read state, apply modifier(data: dict) → None, write back.
        Retries once on ETag conflict.
        """
        for attempt in range(2):
            data, etag = self._read()
            modifier(data)
            if self._write(data, etag):
                return
        # Second conflict — last-write-wins fallback
        data, _ = self._read()
        modifier(data)
        self._write(data, None)

    # ── Public API (mirrors StateManager) ────────────────────────────────────

    def is_amex_initialized(self, year: int, month: int) -> bool:
        data, _ = self._read()
        return data.get(_month_key(year, month), {}).get("amex_initialized", False)

    def mark_amex_initialized(
        self, year: int, month: int, sheet_name: str, amex_filename: str
    ) -> None:
        def _mod(data: dict) -> None:
            key   = _month_key(year, month)
            entry = data.setdefault(key, {})
            entry["sheet_name"]          = sheet_name
            entry["amex_initialized"]    = True
            entry["amex_file"]           = amex_filename
            entry["amex_initialized_at"] = _now_iso()
            entry.setdefault("concur_processed", {})
        self._read_modify_write(_mod)
        logger.info("blob_state_amex_marked", month=_month_key(year, month))

    def is_concur_processed(self, year: int, month: int, filename: str) -> bool:
        data, _ = self._read()
        return filename in data.get(
            _month_key(year, month), {}
        ).get("concur_processed", {})

    def mark_concur_processed(self, year: int, month: int, filename: str) -> None:
        def _mod(data: dict) -> None:
            key   = _month_key(year, month)
            entry = data.setdefault(key, {})
            entry.setdefault("concur_processed", {})[filename] = _now_iso()
        self._read_modify_write(_mod)
        logger.info("blob_state_concur_marked", file=filename)

    def mark_catchup_run(self, year: int, month: int) -> None:
        def _mod(data: dict) -> None:
            data.setdefault(_month_key(year, month), {})["last_catchup"] = _now_iso()
        self._read_modify_write(_mod)

    def snapshot(self) -> dict:
        data, _ = self._read()
        return data

    def pending_concur_files(
        self, year: int, month: int, all_files: list
    ) -> list:
        data, _ = self._read()
        done = set(
            data.get(_month_key(year, month), {}).get("concur_processed", {}).keys()
        )
        return [f for f in all_files if (f.name if hasattr(f, "name") else f) not in done]


def blob_state_from_settings() -> BlobStateManager:
    """Construct BlobStateManager from environment settings."""
    from config.settings import get_settings
    s = get_settings()
    return BlobStateManager(
        connection_string=s.azure_storage_connection_string,
        container=s.azure_storage_state_container,
    )
