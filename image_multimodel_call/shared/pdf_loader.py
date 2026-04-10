"""
shared/pdf_loader.py
Shared PDF loading used by all extractors.
Returns page dicts with text + rendered image.
No business logic — pure infrastructure.
"""
from __future__ import annotations
import base64
import io
from pathlib import Path
from typing import Optional

import pdfplumber

from config.settings import get_settings
from shared.exceptions import PDFLoadError, ValidationError
from shared.logger import get_logger

log = get_logger(__name__)


class PDFLoader:
    """
    Loads every page of a PDF as:
      - text (pdfplumber extraction)
      - image (rendered PNG as base64)

    Usage:
        pages = PDFLoader.load("path/to/file.pdf")
    """

    @classmethod
    def load(cls, pdf_path: str | Path) -> list[dict]:
        """
        Load all pages from a PDF.

        Returns list of page dicts:
            page_num         : int  (1-based)
            text             : str  (empty string if none)
            image_b64        : str | None  (base64 PNG)
            has_text         : bool
            is_image_dominant: bool  (text below threshold)
            total_pages      : int
            render_failed    : bool

        Raises ValidationError, PDFLoadError.
        """
        settings = get_settings()
        path     = Path(pdf_path)
        cls._validate(path, settings.pdf_max_pages)

        log.info("Loading PDF: %s", path.name)

        try:
            pdf = pdfplumber.open(str(path))
        except Exception as exc:
            raise PDFLoadError(f"Cannot open PDF {path.name}: {exc}") from exc

        pages: list[dict] = []

        with pdf:
            total = len(pdf.pages)
            log.info("  %d page(s)", total)

            for batch_start in range(0, total, settings.pdf_batch_size):
                batch_end = min(batch_start + settings.pdf_batch_size, total)
                log.debug("  loading pages %d–%d", batch_start + 1, batch_end)

                for i in range(batch_start, batch_end):
                    pages.append(
                        cls._load_page(pdf.pages[i], i + 1, total, settings)
                    )

        log.info("  loaded %d pages from %s", len(pages), path.name)
        return pages

    @classmethod
    def _load_page(cls, page, page_num: int, total: int, settings) -> dict:
        """Load one page. Errors are isolated — never raise."""
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            log.warning("  text extraction failed page %d: %s", page_num, exc)
            text = ""

        image_b64, render_failed = cls._render(page, page_num, settings.pdf_render_dpi)

        return {
            "page_num":          page_num,
            "text":              text,
            "image_b64":         image_b64,
            "has_text":          bool(text.strip()),
            "is_image_dominant": len(text.strip()) < settings.pdf_min_text_chars,
            "total_pages":       total,
            "render_failed":     render_failed,
        }

    @staticmethod
    def _render(page, page_num: int, dpi: int) -> tuple[Optional[str], bool]:
        """Render page to base64 PNG. Returns (b64 | None, failed_bool)."""
        try:
            img    = page.to_image(resolution=dpi).original
            buf    = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode("utf-8")
            buf.close()
            return b64, False
        except Exception as exc:
            log.warning("  render failed page %d: %s", page_num, exc)
            return None, True

    @staticmethod
    def _validate(path: Path, max_pages: int) -> None:
        if not path.exists():
            raise ValidationError(f"PDF not found: {path}")
        if not path.is_file():
            raise ValidationError(f"Not a file: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValidationError(f"Not a PDF: {path}")
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > 500:
            log.warning("Large PDF: %.1f MB — %s", size_mb, path.name)

    @staticmethod
    def free_images(pages: list[dict]) -> None:
        """Release base64 image data after it is no longer needed."""
        for page in pages:
            page["image_b64"]    = None
            page["images_freed"] = True
        log.debug("Freed image data from %d pages", len(pages))
