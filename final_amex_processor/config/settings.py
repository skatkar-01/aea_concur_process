"""
config/settings.py
──────────────────
Centralised, typed configuration loaded from environment / .env file.
All other modules import `get_settings()` — never read env vars directly.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Azure OpenAI (accessed via standard OpenAI client + base_url) ───────────
    azure_openai_api_key: str  = Field(..., description="Azure OpenAI API key")
    azure_openai_base_url: str = Field(..., description="Azure endpoint, e.g. https://your-resource.openai.azure.com/openai/v1/")
    azure_openai_model: str    = Field("gpt-4o", description="Deployment / model name")

    # ── Directory paths ───────────────────────────────────────────────────────
    input_dir: Path = Field(Path("inputs/amex"), description="Folder scanned for PDFs")
    output_dir: Path = Field(Path("outputs"),    description="XLSX output folder")
    cache_dir: Path  = Field(Path("cache"),      description="JSON extraction cache")
    log_dir: Path    = Field(Path("logs"),       description="Log file directory")

    # ── Processing ────────────────────────────────────────────────────────────
    cache_enabled: bool = Field(True,  description="Skip API call when cache hit")
    max_retries: int    = Field(3,     description="Max OpenAI retry attempts")
    retry_wait_seconds: int = Field(2, description="Base wait between retries (exp back-off)")

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str  = Field("INFO",    description="Root log level")
    log_format: str = Field("json",    description="'json' or 'console'")

    # ── Metrics ───────────────────────────────────────────────────────────────
    metrics_enabled: bool = Field(True, description="Expose Prometheus metrics")
    metrics_port: int     = Field(9090, description="Prometheus HTTP server port")

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    @field_validator("log_format")
    @classmethod
    def _valid_log_format(cls, v: str) -> str:
        if v.lower() not in {"json", "console"}:
            raise ValueError("log_format must be 'json' or 'console'")
        return v.lower()

    def ensure_dirs(self) -> None:
        """Create all required directories if they don't exist."""
        for d in (self.input_dir, self.output_dir, self.cache_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()  # type: ignore[call-arg]
