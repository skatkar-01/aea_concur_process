"""
watcher.py
───────────
Local automation engine for non-Azure environments.
Uses watchdog to detect new PDFs in Box sync folders,
then dispatches via run_job() — the same runner used by the Azure Function.

Two mechanisms:
  1. watchdog FileSystemEventHandler — fires within seconds of a file landing
  2. Catch-up thread (every CATCHUP_INTERVAL_H hours) — picks up anything missed

Both enqueue to a local in-process Queue and a single worker thread
calls run_job() — identical code path to the Azure Queue trigger.
"""
from __future__ import annotations

import signal
import threading
import time
from pathlib import Path
from queue import Queue, Empty
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from config.settings import get_settings
from src.runner import run_job
from utils.logging_config import configure_logging, get_logger
from utils.metrics import METRICS, start_metrics_server
from utils.month_detector import classify_pdf, detect_month, PdfType
from utils.queue_client import PipelineJob
from utils.state_manager import StateManager   # local state (no Azure needed)

logger = get_logger(__name__)


# ── Watchdog handler ──────────────────────────────────────────────────────────

class _PdfHandler(FileSystemEventHandler):
    def __init__(self, queue: Queue, delay_s: int) -> None:
        super().__init__()
        self._queue   = queue
        self._delay_s = delay_s
        self._seen: set[str] = set()

    def _enqueue(self, path_str: str) -> None:
        path = Path(path_str)
        if path.suffix.lower() != ".pdf" or str(path) in self._seen:
            return
        self._seen.add(str(path))
        logger.info("file_detected", path=path.name)
        self._queue.put((path, time.monotonic()))

    def on_created(self, event):
        if not event.is_directory:
            self._enqueue(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._enqueue(event.dest_path)


# ── Build PipelineJob from a local path ───────────────────────────────────────

def _make_local_job(pdf_path: Path, source: str) -> Optional[PipelineJob]:
    s = get_settings()
    pdf_type = classify_pdf(pdf_path, s.amex_subfolder, s.concur_subfolder)
    if pdf_type == PdfType.UNKNOWN:
        return None
    mi = detect_month(pdf_path)
    month_key = f"{mi.year}-{mi.month:02d}" if mi else "unknown"
    return PipelineJob(
        filename=pdf_path.name,
        pdf_type="amex" if pdf_type == PdfType.AMEX else "concur",
        month_key=month_key,
        source=source,
        local_path=str(pdf_path),
    )


# ── Worker thread ─────────────────────────────────────────────────────────────

def _worker(queue: Queue, stop_evt: threading.Event) -> None:
    s   = get_settings()
    log = logger.bind(thread="worker")
    log.info("worker_started")

    while not stop_evt.is_set():
        try:
            pdf_path, queued_at = queue.get(timeout=1.0)
        except Empty:
            continue

        # Wait for Box sync to complete
        elapsed   = time.monotonic() - queued_at
        remaining = s.box_sync_delay_s - elapsed
        if remaining > 0:
            time.sleep(remaining)

        if not pdf_path.exists():
            log.warning("file_disappeared", path=pdf_path.name)
            queue.task_done()
            continue

        job = _make_local_job(pdf_path, "watcher")
        if job is None:
            log.debug("unclassified_pdf_skipped", path=pdf_path.name)
            queue.task_done()
            continue

        result = run_job(job)   # local mode — no box_client
        if not result.success:
            log.error("job_failed", file=pdf_path.name, error=result.error)
            # In local mode we just log — no queue retry mechanism
            # The catch-up scan will retry it on the next cycle

        queue.task_done()


# ── Catch-up scan ─────────────────────────────────────────────────────────────

def _catchup_scan(queue: Queue) -> None:
    s     = get_settings()
    state = StateManager(s.state_path)
    log   = logger.bind(thread="catchup")
    log.info("catchup_scan_start")

    base = getattr(s, "box_base_path", None)
    if base is None or not base.exists():
        log.warning("box_base_path_missing")
        return

    enqueued = 0
    for pdf in sorted(base.rglob("*.pdf")):
        job = _make_local_job(pdf, "catchup")
        if job is None:
            continue
        if job.month_key == "unknown":
            continue

        y, m = int(job.month_key[:4]), int(job.month_key[5:])
        if job.pdf_type == "amex":
            if state.is_amex_initialized(y, m):
                continue
        else:
            if state.is_concur_processed(y, m, pdf.name):
                continue

        queue.put((pdf, time.monotonic()))
        enqueued += 1

    log.info("catchup_scan_complete", enqueued=enqueued)


def _catchup_loop(queue: Queue, interval_h: int, stop_evt: threading.Event) -> None:
    while not stop_evt.is_set():
        stop_evt.wait(timeout=interval_h * 3600)
        if not stop_evt.is_set():
            try:
                _catchup_scan(queue)
            except Exception as exc:
                logger.error("catchup_error", error=str(exc), exc_info=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def run_watcher() -> None:
    s = get_settings()
    configure_logging(level=s.log_level, fmt=s.log_format, log_dir=s.log_dir)
    s.ensure_dirs()

    log = logger.bind(component="watcher")
    log.info("watcher_starting", base=str(getattr(s, "box_base_path", "N/A")))

    if s.metrics_enabled:
        start_metrics_server(s.metrics_port)

    event_q:  Queue         = Queue()
    stop_evt: threading.Event = threading.Event()

    worker = threading.Thread(target=_worker, args=(event_q, stop_evt),
                               daemon=True, name="pipeline-worker")
    worker.start()

    observer = Observer()
    base_path = str(getattr(s, "box_base_path", "."))
    observer.schedule(_PdfHandler(event_q, s.box_sync_delay_s), base_path, recursive=True)
    observer.start()
    log.info("watchdog_started", path=base_path)

    catchup = threading.Thread(
        target=_catchup_loop,
        args=(event_q, s.catchup_interval_h, stop_evt),
        daemon=True, name="catchup-loop",
    )
    catchup.start()

    log.info("running_initial_catchup")
    _catchup_scan(event_q)

    def _shutdown(sig, _frame):
        log.info("shutdown", signal=sig)
        stop_evt.set()
        observer.stop()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("watcher_running")
    try:
        observer.join()
    except Exception:
        pass
    stop_evt.set()
    worker.join(timeout=10)
    catchup.join(timeout=5)
    log.info("watcher_stopped")


if __name__ == "__main__":
    run_watcher()
