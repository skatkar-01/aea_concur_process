"""
shared/logger.py
Structured logger. Call setup_logging() once in main.py.
All other modules call get_logger(__name__).
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

_LOG_FORMAT  = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(
    level:    str  = "INFO",
    log_file: Optional[Path] = None,
) -> None:
    """
    Configure root logger. Call exactly once from main.py.
    All subsequent get_logger() calls inherit this configuration.
    """
    global _configured
    if _configured:
        return

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout)
    ]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=numeric_level,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        handlers=handlers,
        force=True,
    )

    # Silence noisy third-party loggers
    for noisy in (
        "pdfminer", "pdfplumber", "pypdfium2",
        "PIL", "urllib3", "openai", "httpx", "httpcore",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Use __name__ in every module."""
    return logging.getLogger(name)
