"""
config/settings.py
──────────────────
Centralised, typed configuration. All modules import get_settings().
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pathlib import Path
from typing import List

# Project root: final_concur_scrubbing
BASE_DIR = Path(__file__).resolve().parents[1]
BASE_INPUT = BASE_DIR / "inputs"

AMEX_INPUT_TEMPLATE = BASE_INPUT / "{year}" / "{month_folder}" / "AmEx Statements" / "Individual Statements"
CONCUR_INPUT_TEMPLATE = BASE_INPUT / "{year}" / "{month_folder}" / "Final Concur Reports with Receipts and Approvals"

TRACKER_OUTPUT = BASE_DIR / "outputs" / "tracker"

def _fmt_path(tpl: Path, year: str, month_folder: str) -> Path:
    return Path(str(tpl).format(year=year, month_folder=month_folder))

def amex_files_for(year: str, month_folder: str) -> List[Path]:
    p = _fmt_path(AMEX_INPUT_TEMPLATE, year, month_folder)
    return sorted(p.glob("*")) if p.exists() else []

def concur_files_for(year: str, month_folder: str) -> List[Path]:
    p = _fmt_path(CONCUR_INPUT_TEMPLATE, year, month_folder)
    return sorted(p.glob("*")) if p.exists() else []

def ensure_tracker_output() -> Path:
    TRACKER_OUTPUT.mkdir(parents=True, exist_ok=True)
    return TRACKER_OUTPUT

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8",
        case_sensitive=False, extra="ignore",
    )

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    azure_openai_api_key: str  = Field(..., description="Azure OpenAI API key")
    azure_openai_base_url: str = Field(..., description="Azure endpoint base URL")
    azure_openai_model: str    = Field("gpt-4o")
    azure_openai_model1: str   = Field(default="", description="First fallback model to try if primary fails")
    azure_openai_model2: str   = Field(default="", description="Second fallback model to try if model_1 fails")

    # ── Box API ───────────────────────────────────────────────────────────────
    box_client_id: str       = Field(..., description="Box OAuth2 client ID")
    box_client_secret: str   = Field(..., description="Box OAuth2 client secret")
    box_enterprise_id: str   = Field(..., description="Box enterprise ID")
    box_jwt_key_id: str      = Field(..., description="Box JWT key ID")
    box_jwt_private_key: str = Field(..., description="Box RSA private key PEM")

    # ── Box resource IDs ──────────────────────────────────────────────────────
    box_tracker_file_id: str  = Field(..., description="Box file ID of tracker XLSX")
    box_amex_folder_id: str   = Field(..., description="Box folder ID for AMEX PDFs")
    box_concur_folder_id: str = Field(..., description="Box folder ID for Concur PDFs")
    box_webhook_primary_key: str   = Field(default="")
    box_webhook_secondary_key: str = Field(default="")

    # ── Azure Storage (Blob + Queue + Table — same connection string) ─────────
    azure_storage_connection_string: str = Field(..., description="Azure Storage connection string")
    azure_storage_state_container: str   = Field(default="amex-state")
    azure_storage_cache_container: str   = Field(default="amex-cache")

    # ── Azure Queue ───────────────────────────────────────────────────────────
    azure_queue_main: str           = Field(default="amex-jobs",        description="Main job queue name")
    azure_queue_poison: str         = Field(default="amex-jobs-poison", description="Dead-letter queue name")
    queue_visibility_timeout_s: int = Field(default=300, description="Seconds a job is invisible after dequeue")
    queue_max_retries: int          = Field(default=5,   description="Max retries before dead-letter")

    # ── Azure Table ───────────────────────────────────────────────────────────
    azure_table_name: str = Field(default="amexjobs", description="Table Storage table name")

    # ── Subfolder patterns ────────────────────────────────────────────────────
    amex_subfolder: str   = Field(default="AmEx Statements/Individual Statements")
    concur_subfolder: str = Field(default="Final Concur Reports with Receipts and Approvals")

    # ── Local paths (local / test mode) ───────────────────────────────────────
    box_base_path: Path  = Field(default=Path("inputs"))
    tracker_path: Path   = Field(default=Path("outputs/"))
    input_dir: Path      = Field(default=Path("inputs"), description="Folder scanned for PDFs (batch/pipeline mode)")
    output_dir: Path     = Field(default=Path("outputs"), description="XLSX output folder")
    cache_dir: Path      = Field(default=Path("cache"))
    log_dir: Path        = Field(default=Path("logs"))
    state_path: Path     = Field(default=Path("cache/processed_log.json"))

    # ── Processing ────────────────────────────────────────────────────────────
    cache_enabled: bool     = Field(default=True)
    max_retries: int        = Field(default=5, description="Max API retry attempts")
    retry_wait_seconds: int = Field(default=2, description="Initial wait between retries")
    api_timeout_seconds: int = Field(default=60, description="API request timeout in seconds (20 min for large PDFs)")
    max_tokens: int = Field(default=16000, description="Base max tokens for API response")
    max_tokens_per_kb: int = Field(default=50, description="Additional tokens per KB of PDF")
    max_tokens_cap: int = Field(default=32000, description="Hard cap on max_tokens")
    # ── LLM cost tracking (USD per 1K tokens) ─────────────────────────────────
    cost_per_1k_input: float  = Field(default=0.005,  description="Cost per 1K input tokens USD")
    cost_per_1k_output: float = Field(default=0.015,  description="Cost per 1K output tokens USD")
    box_sync_delay_s: int   = Field(default=5)
    catchup_interval_h: int = Field(default=6)

    # ── Logging / metrics ─────────────────────────────────────────────────────
    log_level: str  = Field(default="INFO")
    log_format: str = Field(default="console")
    metrics_enabled: bool = Field(default=False)
    metrics_port: int     = Field(default=9090)

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
        for d in (self.input_dir, self.output_dir, self.cache_dir, self.log_dir, self.state_path.parent):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
