"""
utils/queue_client.py
──────────────────────
Azure Storage Queue wrapper for the AMEX pipeline.

Two queues:
  - MAIN queue  → jobs waiting to be processed / retried
  - POISON queue → jobs that exceeded max retries (dead-letter)

Job message schema (JSON):
{
  "job_id":       "uuid4",
  "file_id":      "box_file_id or null",     ← null = local mode
  "local_path":   "/abs/path/file.pdf or null",
  "filename":     "ALTHAUS_B_report.pdf",
  "folder_id":    "box_folder_id or null",
  "pdf_type":     "amex | concur",
  "month_key":    "2026-03",
  "enqueued_at":  "2026-03-10T09:00:00Z",
  "retry_count":  0,
  "source":       "webhook | catchup | local | manual"
}
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

# ── Job dataclass ─────────────────────────────────────────────────────────────

@dataclass
class PipelineJob:
    filename:     str
    pdf_type:     str                    # "amex" | "concur"
    month_key:    str                    # "2026-03"
    source:       str                    # "webhook" | "catchup" | "local" | "manual"
    file_id:      Optional[str] = None   # Box file ID (cloud mode)
    local_path:   Optional[str] = None   # absolute path (local mode)
    folder_id:    Optional[str] = None   # Box folder ID (cloud mode)
    job_id:       str = field(default_factory=lambda: str(uuid.uuid4()))
    enqueued_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    retry_count:  int = 0
    mode_override: Optional[str] = None  # "local" | "cloud" or None to auto-infer

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "PipelineJob":
        return cls(**json.loads(text))

    @property
    def is_local(self) -> bool:
        if self.mode_override == "local":
            return True
        if self.mode_override == "cloud":
            return False
        return self.local_path is not None and self.file_id is None


# ── Queue client ──────────────────────────────────────────────────────────────

class QueueClient:
    """
    Thin wrapper around azure-storage-queue.

    Main queue:   jobs to process (auto-retry on failure via visibility timeout)
    Poison queue: jobs that failed > max_retries (dead-letter for alerting)

    Visibility timeout: how long a job is invisible after being dequeued.
    If the Function crashes before calling delete(), the job reappears
    automatically after visibility_timeout_s seconds — free retry.
    """

    def __init__(
        self,
        connection_string: str,
        main_queue: str   = "amex-jobs",
        poison_queue: str = "amex-jobs-poison",
        visibility_timeout_s: int = 300,   # 5 min — enough for a slow PDF
        max_retries: int = 5,
    ) -> None:
        from azure.storage.queue import QueueServiceClient
        svc = QueueServiceClient.from_connection_string(connection_string)
        self._main    = svc.get_queue_client(main_queue)
        self._poison  = svc.get_queue_client(poison_queue)
        self._vis_s   = visibility_timeout_s
        self._max_retries = max_retries
        self._ensure_queues(svc, main_queue, poison_queue)

    def _ensure_queues(self, svc, main: str, poison: str) -> None:
        for name in (main, poison):
            try:
                svc.create_queue(name)
                logger.info("queue_created", name=name)
            except Exception:
                pass   # already exists

    # ── Enqueue ───────────────────────────────────────────────────────────────

    def enqueue(self, job: PipelineJob) -> None:
        """Add a job to the main queue."""
        self._main.send_message(job.to_json())
        logger.info("job_enqueued", job_id=job.job_id, file=job.filename,
                    type=job.pdf_type, source=job.source)

    # ── Dequeue (for local / non-Function processing) ─────────────────────────

    def dequeue_one(self) -> Optional[tuple["PipelineJob", object]]:
        """
        Dequeue one job.  Returns (job, receipt_handle) or None if empty.
        Caller must call delete(receipt_handle) on success,
        or call nack(job, receipt_handle) on failure.
        """
        msgs = self._main.receive_messages(
            max_messages=1,
            visibility_timeout=self._vis_s,
        )
        for msg in msgs:
            job = PipelineJob.from_json(msg.content)
            return job, msg
        return None

    def delete(self, msg) -> None:
        """Acknowledge successful processing — removes from queue."""
        self._main.delete_message(msg)
        logger.debug("job_deleted", msg_id=msg.id)

    def nack(self, job: PipelineJob, msg) -> None:
        """
        Called on failure.
        If under max_retries: increment counter, update message, let it reappear.
        If over max_retries: move to poison queue, delete from main.
        """
        job.retry_count += 1
        if job.retry_count > self._max_retries:
            logger.error("job_dead_lettered", job_id=job.job_id,
                         file=job.filename, retries=job.retry_count)
            self._poison.send_message(job.to_json())
            self._main.delete_message(msg)
        else:
            logger.warning("job_nacked_will_retry", job_id=job.job_id,
                           file=job.filename, retry=job.retry_count)
            # Update the message content with incremented retry count
            # and set a short visibility delay before it reappears
            backoff = min(30 * (2 ** (job.retry_count - 1)), 600)  # 30s, 60s, 120s…
            self._main.update_message(
                msg,
                visibility_timeout=backoff,
                content=job.to_json(),
            )

    # ── Poison queue inspection ───────────────────────────────────────────────

    def list_poison(self, max_messages: int = 32) -> list[PipelineJob]:
        """Peek at dead-lettered jobs without removing them."""
        msgs = self._poison.peek_messages(max_messages=max_messages)
        jobs = []
        for m in msgs:
            try:
                jobs.append(PipelineJob.from_json(m.content))
            except Exception:
                pass
        return jobs

    def clear_poison(self) -> int:
        """Delete all messages from the poison queue. Returns count cleared."""
        msgs = list(self._poison.receive_messages(max_messages=32))
        for m in msgs:
            self._poison.delete_message(m)
        logger.info("poison_queue_cleared", count=len(msgs))
        return len(msgs)

    def main_queue_length(self) -> int:
        props = self._main.get_queue_properties()
        return props.approximate_message_count or 0

    def poison_queue_length(self) -> int:
        props = self._poison.get_queue_properties()
        return props.approximate_message_count or 0


def queue_client_from_settings() -> QueueClient:
    from config.settings import get_settings
    s = get_settings()
    return QueueClient(
        connection_string=s.azure_storage_connection_string,
        main_queue=s.azure_queue_main,
        poison_queue=s.azure_queue_poison,
        visibility_timeout_s=s.queue_visibility_timeout_s,
        max_retries=s.queue_max_retries,
    )
