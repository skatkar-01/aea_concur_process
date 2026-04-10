"""
utils/logging_config.py
Structured logging with structlog when available, plus a stdlib fallback.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

try:
    import structlog
except ImportError:
    structlog = None


class _FallbackBoundLogger:
    def __init__(self, logger: logging.Logger, context: dict | None = None) -> None:
        self._logger = logger
        self._context = context or {}

    def bind(self, **kwargs):
        return _FallbackBoundLogger(self._logger, {**self._context, **kwargs})

    def _log(self, level: str, event: str, **kwargs) -> None:
        exc_info = kwargs.pop("exc_info", False)
        merged = {**self._context, **kwargs}
        suffix = " ".join(f"{key}={value}" for key, value in merged.items())
        message = f"{event} {suffix}".strip()
        getattr(self._logger, level)(message, exc_info=exc_info)

    def debug(self, event: str, **kwargs) -> None:
        self._log("debug", event, **kwargs)

    def info(self, event: str, **kwargs) -> None:
        self._log("info", event, **kwargs)

    def warning(self, event: str, **kwargs) -> None:
        self._log("warning", event, **kwargs)

    def error(self, event: str, **kwargs) -> None:
        self._log("error", event, **kwargs)


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    log_dir: Path = Path("logs"),
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    if structlog is None:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )

        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)

        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_dir / "amex_processor.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)

        root = logging.getLogger()
        root.setLevel(numeric_level)
        root.handlers.clear()
        root.addHandler(stream_handler)
        root.addHandler(file_handler)

        for noisy in ("httpx", "httpcore", "openai"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        return

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / "amex_processor.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str):
    if structlog is None:
        return _FallbackBoundLogger(logging.getLogger(name))
    return structlog.get_logger(name)
