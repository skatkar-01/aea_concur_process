"""
utils/logger.py
Structured JSON logging via structlog.
One call to setup_logging() in main.py wires everything up.
"""

from __future__ import annotations

import logging
import sys
import os
from pathlib import Path

import structlog


def setup_logging(log_level: str = "INFO", log_folder: str = "logs") -> None:
    """Configure structlog + stdlib logging with JSON output."""
    Path(log_folder).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_folder) / "extractor.log"

    level = getattr(logging, log_level.upper(), logging.INFO)

    # stdlib handler: file (JSON) + console (human-readable)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)

    logging.basicConfig(
        level=level,
        handlers=[file_handler, console_handler],
        format="%(message)s",
    )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    for handler in logging.root.handlers:
        handler.setFormatter(formatter)


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
