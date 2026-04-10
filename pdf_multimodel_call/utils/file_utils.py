"""
utils/file_utils.py
Reads SAP Concur report files (PDF, images, text) and prepares them
for LLM consumption — either as extracted text or base64-encoded blobs.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import NamedTuple

from utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".html"}


class FilePayload(NamedTuple):
    file_path: Path
    file_name: str
    mime_type: str
    content_text: str           # extracted text (empty if image-only)
    content_b64: str            # base64 blob (empty for pure-text files)
    is_binary: bool             # True = send as inline document/image


def read_file(file_path: Path) -> FilePayload:
    """
    Read a Concur report file and return a FilePayload.

    Strategy:
    - PDF  → try text extraction via PyMuPDF; fall back to base64 if scanned
    - Image → base64
    - Text/HTML → raw text
    """
    suffix = file_path.suffix.lower()
    mime_type = mimetypes.types_map.get(suffix, "application/octet-stream")

    if suffix == ".pdf":
        return _read_pdf(file_path, mime_type)
    elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return _read_image(file_path, mime_type)
    elif suffix in {".txt", ".html"}:
        return _read_text(file_path, mime_type)
    else:
        logger.warning("unsupported_file_type", path=str(file_path), suffix=suffix)
        raise ValueError(f"Unsupported file type: {suffix}")


def list_report_files(input_folder: str) -> list[Path]:
    """Return all supported files in the input folder (non-recursive)."""
    folder = Path(input_folder)
    if not folder.exists():
        raise FileNotFoundError(f"Input folder not found: {folder}")

    files = [
        f for f in sorted(folder.iterdir())
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    logger.info("input_files_discovered", count=len(files), folder=str(folder))
    return files


# ── private helpers ────────────────────────────────────────────────────────

def _read_pdf(file_path: Path, mime_type: str) -> FilePayload:
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(file_path))
        pages_text = [page.get_text() for page in doc]
        full_text = "\n\n".join(pages_text).strip()
        doc.close()

        if len(full_text) > 200:
            # Text-based PDF — text extracted successfully.
            # mime_type stays "application/pdf" so every client knows the
            # original file is a PDF (Azure client checks this).
            # is_binary=False signals that content_text is populated.
            logger.debug("pdf_text_extracted", file=file_path.name, chars=len(full_text))
            return FilePayload(
                file_path=file_path,
                file_name=file_path.name,
                mime_type="application/pdf",   # ← was wrongly "text/plain"
                content_text=full_text,
                content_b64="",
                is_binary=False,
            )
        else:
            # Scanned / image-only PDF — send as base64
            logger.debug("pdf_sent_as_binary", file=file_path.name, reason="low_text_content")
            return _read_as_b64(file_path, mime_type)

    except ImportError:
        logger.warning("pymupdf_not_installed", fallback="base64")
        return _read_as_b64(file_path, mime_type)


def _read_image(file_path: Path, mime_type: str) -> FilePayload:
    return _read_as_b64(file_path, mime_type)


def _read_text(file_path: Path, mime_type: str) -> FilePayload:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    logger.debug("text_file_read", file=file_path.name, chars=len(text))
    return FilePayload(
        file_path=file_path,
        file_name=file_path.name,
        mime_type=mime_type,
        content_text=text,
        content_b64="",
        is_binary=False,
    )


def _read_as_b64(file_path: Path, mime_type: str) -> FilePayload:
    raw = file_path.read_bytes()
    b64 = base64.b64encode(raw).decode("utf-8")
    logger.debug("file_encoded_base64", file=file_path.name, bytes=len(raw))
    return FilePayload(
        file_path=file_path,
        file_name=file_path.name,
        mime_type=mime_type,
        content_text="",
        content_b64=b64,
        is_binary=True,
    )