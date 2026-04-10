"""
utils/logging_config.py
────────────────────────
Structured logging with structlog.
- JSON output for production (parseable by Datadog / CloudWatch / ELK)
- Coloured console output for development
- Rotating file handler so logs don't fill the disk
Call `configure_logging()` once at startup (main.py).
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    log_dir: Path = Path("logs"),
) -> None:
    """
    Set up structlog + stdlib logging.

    Args:
        level:   Root log level string, e.g. "INFO".
        fmt:     "json" or "console".
        log_dir: Directory for rotating log files.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # ── Shared processors (run for every log event) ───────────────────────────
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # ── Output renderer ───────────────────────────────────────────────────────
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    # ── Handlers ──────────────────────────────────────────────────────────────
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / "amex_processor.log",
        maxBytes=10 * 1024 * 1024,   # 10 MB per file
        backupCount=7,                # keep 7 rotated files
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    # Silence noisy third-party loggers
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Convenience wrapper — use instead of logging.getLogger()."""
    return structlog.get_logger(name)
