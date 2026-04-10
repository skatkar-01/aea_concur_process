"""
src/azure_function/queue_trigger.py
─────────────────────────────────────
Azure Function Queue trigger — processes PipelineJob messages.

This is where all the real work happens:
  - Picks up a job from the Azure Storage Queue
  - Calls runner.run_job() with BoxClient
  - On success: Azure auto-deletes the message (queue trigger semantics)
  - On failure: raises exception → Azure re-enqueues up to maxDequeueCount
  - After maxDequeueCount failures: Azure moves to poison queue automatically

Azure Queue trigger retry semantics:
  maxDequeueCount (set in host.json) controls how many times Azure retries
  before moving to the -poison queue.  We set it to 5.
  Each retry has increasing visibility delay (managed by Azure).

Also contains the Timer trigger for the 6-hour catch-up scan.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import azure.functions as func

from config.settings import get_settings
from src.box_client import box_client_from_settings
from src.runner import run_job
from utils.logging_config import configure_logging, get_logger
from utils.month_detector import classify_pdf, detect_month, PdfType
from utils.queue_client import PipelineJob, queue_client_from_settings
from utils.table_state import table_state_from_settings

settings = get_settings()
configure_logging(level=settings.log_level, fmt="json", log_dir=Path("/tmp/logs"))
logger = get_logger(__name__)

# Re-use the same FunctionApp instance as webhook_trigger (same module in v2 model)
# If deploying as separate files, create a new app instance:
app = func.FunctionApp()


@app.queue_trigger(
    arg_name="msg",
    queue_name="%AZURE_QUEUE_MAIN%",   # resolved from app settings at deploy time
    connection="AZURE_STORAGE_CONNECTION_STRING",
)
def process_job(msg: func.QueueMessage) -> None:
    """
    Queue trigger — runs for every message in the main queue.

    Azure handles retries: if this function raises, Azure re-enqueues
    the message automatically.  After maxDequeueCount attempts, Azure
    moves it to the -poison queue without any code from us.

    We raise on failure so Azure's built-in retry mechanism takes over.
    We do NOT call nack() here — that's for the local watcher mode.
    """
    log = logger.bind(function="process_job")

    raw = msg.get_body().decode("utf-8")
    try:
        job = PipelineJob.from_json(raw)
    except Exception as exc:
        log.error("job_deserialize_failed", raw=raw[:200], error=str(exc))
        raise   # poison it immediately — unreadable message

    log.info("queue_job_received", job_id=job.job_id, file=job.filename,
             type=job.pdf_type, dequeue_count=msg.dequeue_count)

    # Update retry_count from Azure's dequeue counter
    job.retry_count = msg.dequeue_count - 1

    # Update table state to PROCESSING
    if job.month_key != "unknown":
        try:
            year, month = int(job.month_key[:4]), int(job.month_key[5:])
            state = table_state_from_settings()
            state.mark_processing(year, month, job.filename, job.job_id)
        except Exception as exc:
            log.warning("state_mark_processing_failed", error=str(exc))

    box = box_client_from_settings()
    result = run_job(job, box_client=box)

    if result.success:
        log.info("queue_job_done", job_id=job.job_id, detail=result.details,
                 duration_s=round(result.duration_s, 2))
        # Azure auto-deletes on clean return — nothing to do
    else:
        log.error("queue_job_failed", job_id=job.job_id, error=result.error,
                  dequeue_count=msg.dequeue_count)
        # Raise so Azure re-enqueues for retry
        raise RuntimeError(f"Pipeline failed for {job.filename}: {result.error}")


@app.timer_trigger(
    schedule="0 0 */6 * * *",
    arg_name="timer",
    run_on_startup=True,
)
def catchup_scan(timer: func.TimerRequest) -> None:
    """
    Runs every 6 hours.  Lists all PDFs in both Box folders,
    checks Table Storage, enqueues any not yet DONE.
    """
    log = logger.bind(function="catchup_scan")
    log.info("catchup_start")

    box     = box_client_from_settings()
    state   = table_state_from_settings()
    queue   = queue_client_from_settings()
    enqueued = 0

    for folder_id, pdf_type in [
        (settings.box_amex_folder_id,   "amex"),
        (settings.box_concur_folder_id, "concur"),
    ]:
        try:
            files = box.list_folder(folder_id)
        except Exception as exc:
            log.error("catchup_list_failed", folder=folder_id, error=str(exc))
            continue

        for box_file in files:
            if not box_file.name.lower().endswith(".pdf"):
                continue

            # Best-effort month detection from folder name
            month_key = "unknown"
            try:
                info = box.get_file_info(box_file.file_id)
                parent_name = info.get("parent", {}).get("name", "")
                from utils.month_detector import detect_month
                mi = detect_month(Path(f"2026/{parent_name}/{box_file.name}"))
                if mi:
                    month_key = f"{mi.year}-{mi.month:02d}"
            except Exception:
                pass

            # Skip if already DONE
            if month_key != "unknown":
                y, m = int(month_key[:4]), int(month_key[5:])
                if state.is_done(y, m, box_file.name):
                    continue

            job = PipelineJob(
                filename=box_file.name,
                pdf_type=pdf_type,
                month_key=month_key,
                source="catchup",
                file_id=box_file.file_id,
                folder_id=folder_id,
            )
            try:
                queue.enqueue(job)
                enqueued += 1
            except Exception as exc:
                log.error("catchup_enqueue_failed", file=box_file.name, error=str(exc))

    log.info("catchup_complete", enqueued=enqueued)


@app.route(route="jobs/poison", methods=["GET"])
def list_poison_jobs(req: func.HttpRequest) -> func.HttpResponse:
    """
    Simple endpoint to inspect dead-lettered jobs.
    Returns JSON array of failed PipelineJob records.
    Protected by Function key auth.
    """
    try:
        q    = queue_client_from_settings()
        jobs = q.list_poison()
        body = json.dumps([j.__dict__ for j in jobs], indent=2)
        return func.HttpResponse(body, mimetype="application/json", status_code=200)
    except Exception as exc:
        return func.HttpResponse(str(exc), status_code=500)


@app.route(route="jobs/status", methods=["GET"])
def job_status(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns current pipeline status from Table Storage.
    Query param: ?month=2026-03  (optional — returns all months if omitted)
    """
    try:
        state = table_state_from_settings()
        month = req.params.get("month")
        if month and "-" in month:
            y, m = int(month[:4]), int(month[5:])
            data = {month: state.list_month(y, m)}
        else:
            data = state.snapshot()
        return func.HttpResponse(
            json.dumps(data, indent=2, default=str),
            mimetype="application/json",
            status_code=200,
        )
    except Exception as exc:
        return func.HttpResponse(str(exc), status_code=500)
