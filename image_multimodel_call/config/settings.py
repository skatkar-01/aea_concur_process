"""
config/settings.py
All environment variables loaded here. One import for the whole project.
Loads .env from the project root (same directory as main.py).
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

# ── Load .env from project root ───────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE     = _PROJECT_ROOT / ".env"

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_ENV_FILE, override=False)
except ImportError:
    # Built-in fallback — handles KEY=value, KEY="value", # comments
    if _ENV_FILE.exists():
        with open(_ENV_FILE, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _, _v = _line.partition("=")
                _k = _k.strip()
                _v = _v.strip().strip('"').strip("'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()

def _bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("1", "true", "yes")

def _int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default

def _float(key: str, default: float = 0.0) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default

def _path(key: str, default: str) -> Path:
    return Path(_env(key, default))


@dataclass(frozen=True)
class Settings:
    # ── Environment ───────────────────────────────────────────────────────────
    app_env: str

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    azure_openai_api_key:     str
    azure_openai_endpoint:    str
    azure_openai_deployment:  str
    azure_openai_api_version: str

    # ── Azure AI Foundry / Serverless (optional) ──────────────────────────────
    azure_backend:    str   # "azure_openai" | "azure_ai_inference"
    azure_ai_endpoint: str
    azure_ai_key:      str
    azure_ai_model:    str

    # ── Folders ───────────────────────────────────────────────────────────────
    amex_input_folder:   Path
    concur_input_folder: Path
    output_folder:       Path

    # ── Processing ────────────────────────────────────────────────────────────
    max_retries:           int
    confidence_threshold:  float
    pdf_render_dpi:        int
    pdf_batch_size:        int
    pdf_max_pages:         int
    pdf_min_text_chars:    int
    pdf_text_char_limit:   int

    # ── LLM token budgets ─────────────────────────────────────────────────────
    llm_max_completion_tokens_small:  int
    llm_max_completion_tokens_med:    int
    llm_max_completion_tokens_large:  int

    # ── Output formats ────────────────────────────────────────────────────────
    output_json:  bool
    output_csv:   bool
    output_excel: bool

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level:    str
    log_to_file:  bool

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def azure_openai_configured(self) -> bool:
        return bool(self.azure_openai_api_key and self.azure_openai_endpoint)

    @property
    def log_file(self) -> Path:
        return self.output_folder / "logs" / "pipeline.log"

    def validate(self) -> None:
        """Raise EnvironmentError if required credentials are missing."""
        if self.is_production and not self.azure_openai_configured:
            raise EnvironmentError(
                "Production requires AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT. "
                "Check your .env file."
            )


def _build_settings() -> Settings:
    return Settings(
        app_env                  = _env("APP_ENV", "development"),
        azure_openai_api_key     = _env("AZURE_OPENAI_API_KEY"),
        azure_openai_endpoint    = _env("AZURE_OPENAI_ENDPOINT"),
        azure_openai_deployment  = _env("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
        azure_openai_api_version = _env("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
        azure_backend            = _env("AZURE_BACKEND", "azure_openai"),
        azure_ai_endpoint        = _env("AZURE_AI_ENDPOINT"),
        azure_ai_key             = _env("AZURE_AI_KEY"),
        azure_ai_model           = _env("AZURE_AI_MODEL", "gpt-4o"),
        amex_input_folder        = _path("AMEX_INPUT_FOLDER",   "inputs/amex"),
        concur_input_folder      = _path("CONCUR_INPUT_FOLDER", "inputs/concur"),
        output_folder            = _path("OUTPUT_FOLDER",       "outputs"),
        max_retries              = _int("MAX_RETRIES", 4),
        confidence_threshold     = _float("CONFIDENCE_THRESHOLD", 0.60),
        pdf_render_dpi           = _int("PDF_RENDER_DPI", 150),
        pdf_batch_size           = _int("PDF_BATCH_SIZE", 10),
        pdf_max_pages            = _int("PDF_MAX_PAGES", 200),
        pdf_min_text_chars       = _int("PDF_MIN_TEXT_CHARS", 80),
        pdf_text_char_limit      = _int("PDF_TEXT_CHAR_LIMIT", 6000),
        llm_max_completion_tokens_small     = _int("LLM_max_completion_tokens_SMALL", 512),
        llm_max_completion_tokens_med       = _int("LLM_max_completion_tokens_MED", 1024),
        llm_max_completion_tokens_large     = _int("LLM_max_completion_tokens_LARGE", 4096),
        output_json              = _bool("OUTPUT_JSON", True),
        output_csv               = _bool("OUTPUT_CSV", True),
        output_excel             = _bool("OUTPUT_EXCEL", False),
        log_level                = _env("LOG_LEVEL", "INFO"),
        log_to_file              = _bool("LOG_TO_FILE", True),
    )


# Singleton — import this everywhere
_settings: Settings | None = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = _build_settings()
    return _settings
