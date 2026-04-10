"""
src/azure_function/webhook_trigger.py
───────────────────────────────────────
Azure Function HTTP trigger — receives Box webhook events.

Responsibility is intentionally narrow:
  1. Verify Box HMAC signature
  2. Parse event → extract file_id, filename, folder_id
  3. Classify PDF type from folder_id
  4. Detect month from Box folder ancestry
  5. Enqueue a PipelineJob to the Azure Storage Queue
  6. Return 200 immediately  ← Box requires fast ACK

Heavy lifting (extraction, reconciliation, tracker write) happens
in queue_trigger.py, not here.  Decoupled by design.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import azure.functions as func

from config.settings import get_settings
from src.box_client import BoxClient, box_client_from_settings
from utils.logging_config import configure_logging, get_logger
from utils.month_detector import detect_month
from utils.queue_client import PipelineJob, queue_client_from_settings
from utils.table_state import table_state_from_settings, DONE

settings = get_settings()
configure_logging(level=settings.log_level, fmt="json", log_dir=Path("/tmp/logs"))
logger = get_logger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="box-webhook", methods=["POST"])
def box_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """
    Receives Box FILE.UPLOADED events and immediately enqueues a PipelineJob.
    Returns 200 to Box within milliseconds — no pipeline work done here.
    """
    log = logger.bind(function="box_webhook")

    # ── 1. Signature verification ─────────────────────────────────────────────
    body = req.get_body()
    primary   = settings.box_webhook_primary_key
    secondary = settings.box_webhook_secondary_key

    if primary:
        if not BoxClient.verify_webhook_signature(
            payload=body,
            headers=dict(req.headers),
            primary_key=primary,
            secondary_key=secondary,
        ):
            log.warning("webhook_signature_invalid")
            return func.HttpResponse("Unauthorized", status_code=401)

    # ── 2. Parse event ────────────────────────────────────────────────────────
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return func.HttpResponse("Bad Request", status_code=400)

    event_type = payload.get("event", {}).get("event_type", "")
    source     = payload.get("event", {}).get("source", {})
    file_id    = source.get("id", "")
    filename   = source.get("name", "")
    folder_id  = source.get("parent", {}).get("id", "")

    log.info("webhook_received", event=event_type, file=filename)

    if event_type not in {"FILE.UPLOADED", "FILE.COPIED", "FILE.MOVED"}:
        return func.HttpResponse("OK — ignored", status_code=200)

    if not filename.lower().endswith(".pdf") or not file_id:
        return func.HttpResponse("OK — not a PDF", status_code=200)

    # ── 3. Classify PDF type ──────────────────────────────────────────────────
    if folder_id == settings.box_amex_folder_id:
        pdf_type = "amex"
    elif folder_id == settings.box_concur_folder_id:
        pdf_type = "concur"
    else:
        log.warning("unknown_folder_skipping", folder_id=folder_id)
        return func.HttpResponse("OK — unknown folder", status_code=200)

    # ── 4. Detect month from Box folder (best effort — fallback to "unknown") ─
    month_key = _detect_month_key(file_id, filename)

    # ── 5. Skip if already DONE (idempotency) ─────────────────────────────────
    if month_key != "unknown":
        year, month = int(month_key[:4]), int(month_key[5:])
        state = table_state_from_settings()
        if state.is_done(year, month, filename):
            log.info("already_done_skipping", file=filename)
            return func.HttpResponse("OK — already done", status_code=200)

    # ── 6. Enqueue ────────────────────────────────────────────────────────────
    job = PipelineJob(
        filename=filename,
        pdf_type=pdf_type,
        month_key=month_key,
        source="webhook",
        file_id=file_id,
        folder_id=folder_id,
    )

    try:
        q = queue_client_from_settings()
        q.enqueue(job)
        log.info("job_enqueued", job_id=job.job_id, type=pdf_type, month=month_key)
    except Exception as exc:
        log.error("enqueue_failed", error=str(exc), exc_info=True)
        return func.HttpResponse("Queue error", status_code=500)

    return func.HttpResponse("OK", status_code=200)


def _detect_month_key(file_id: str, filename: str) -> str:
    """
    Try to detect the month key from Box folder ancestry.
    Returns 'YYYY-MM' or 'unknown' on failure.
    """
    try:
        box = box_client_from_settings()
        info = box.get_file_info(file_id)
        # Walk parent folders to build synthetic path
        parts = []
        node = info
        for _ in range(5):
            parent = node.get("parent")
            if not parent or parent.get("id") == "0":
                break
            parts.insert(0, parent.get("name", ""))
            node = {"parent": parent}   # stop after first level for simplicity
        synthetic = "/".join(parts) + "/" + filename
        mi = detect_month(Path(synthetic))
        if mi:
            return f"{mi.year}-{mi.month:02d}"
    except Exception as exc:
        logger.warning("month_detect_failed", file=filename, error=str(exc))
    return "unknown"
