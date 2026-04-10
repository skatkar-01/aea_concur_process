"""
src/resolver.py
────────────────
Resolves a PipelineJob to a concrete local Path before any pipeline code runs.

Two modes — same interface, transparent to the pipeline:

  LOCAL mode  (job.local_path is set)
    → validates the file exists
    → returns the path as-is (no copy, no download)

  BOX mode    (job.file_id is set)
    → downloads the file from Box API to /tmp/{job_id}_{filename}
    → returns the temp Path
    → caller must call cleanup(path) when done (or use as context manager)

Usage:
    with resolve(job, box_client) as pdf_path:
        statement = extract_statement(pdf_path)
    # temp file deleted automatically on exit
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from utils.logging_config import get_logger
from utils.queue_client import PipelineJob

logger = get_logger(__name__)


@contextmanager
def resolve(
    job: PipelineJob,
    box_client=None,   # BoxClient | None — not required in local mode
) -> Generator[Path, None, None]:
    """
    Context manager that yields a local Path to the PDF regardless of source.
    Temp files (Box mode) are deleted automatically on exit.

    Args:
        job:        PipelineJob describing the file source.
        box_client: BoxClient instance (required in cloud mode).

    Yields:
        Path to the PDF on local disk.

    Raises:
        FileNotFoundError: if local file doesn't exist.
        ValueError:        if neither local_path nor file_id is set.
        Exception:         any Box download error propagates unchanged.
    """
    tmp_path: Optional[Path] = None
    try:
        path = _resolve_to_path(job, box_client)
        if path.suffix.lower() == ".tmp_downloaded":
            tmp_path = path
        yield path
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
                logger.debug("temp_pdf_deleted", path=str(tmp_path))
            except OSError as exc:
                logger.warning("temp_pdf_delete_failed", path=str(tmp_path), error=str(exc))


def _resolve_to_path(job: PipelineJob, box_client) -> Path:
    if job.local_path:
        path = Path(job.local_path)
        if not path.exists():
            raise FileNotFoundError(f"Local PDF not found: {path}")
        logger.debug("resolved_local", file=job.filename, path=str(path))
        return path

    if job.file_id:
        if box_client is None:
            raise ValueError(
                "BoxClient is required for cloud mode jobs but was not provided."
            )
        return _download_from_box(job, box_client)

    raise ValueError(
        f"PipelineJob '{job.filename}' has neither local_path nor file_id set."
    )


def _download_from_box(job: PipelineJob, box_client) -> Path:
    """Download PDF from Box to a named temp file. Returns Path."""
    safe_name = job.filename.replace("/", "_").replace("\\", "_")
    fd, tmp = tempfile.mkstemp(
        suffix=".tmp_downloaded",
        prefix=f"{job.job_id[:8]}_{safe_name}_",
    )
    os.close(fd)
    tmp_path = Path(tmp)

    logger.info("downloading_from_box", file=job.filename, file_id=job.file_id)
    data = box_client.download_file(job.file_id)
    tmp_path.write_bytes(data)
    logger.debug("box_download_complete", file=job.filename, bytes=len(data))
    return tmp_path
