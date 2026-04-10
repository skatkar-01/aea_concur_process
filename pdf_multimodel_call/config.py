"""
config.py
All application configuration, loaded from environment / .env file.
Single source of truth — never import os.environ directly elsewhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


# ──────────────────────────────────────────────
# Per-model pricing table  (USD per 1M tokens)
# Update this dict when providers change prices.
# ──────────────────────────────────────────────
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Google Gemini
    "gemini-2.0-flash-lite":             {"input": 0.075,  "output": 0.30},
    "gemini-2.0-flash":                  {"input": 0.10,   "output": 0.40},
    "gemini-2.5-flash":                  {"input": 0.30,   "output": 2.50},
    "gemini-2.5-pro":                    {"input": 1.25,   "output": 10.00},
    "gemini-3.1-flash-lite-preview":     {"input": 0.075,  "output": 0.30},  # placeholder
    # Anthropic Claude
    "claude-3-5-haiku-20241022":         {"input": 0.80,   "output": 4.00},
    "claude-3-5-sonnet-20241022":        {"input": 3.00,   "output": 15.00},
    "claude-opus-4-5":                   {"input": 15.00,  "output": 75.00},
    # Azure OpenAI / OpenAI
    "gpt-4o":                            {"input": 2.50,   "output": 10.00},
    "gpt-5-mini":                       {"input": 0.15,   "output": 0.60},
    "gpt-4-turbo":                       {"input": 10.00,  "output": 30.00},
}


@dataclass
class LLMConfig:
    provider: str = "gemini"           # gemini | claude | azure_openai
    model: str = "gemini-2.0-flash-lite"
    api_key: Optional[str] = None
    azure_endpoint: Optional[str] = None
    azure_deployment: Optional[str] = None
    azure_api_version: str = "2025-08-07"
    thinking_level: str = "MINIMAL"    # MINIMAL | LOW | HIGH
    max_retries: int = 3
    timeout: int = 180


@dataclass
class PathConfig:
    input_folder: str = "input"
    output_folder: str = "output"
    log_folder: str = "logs"
    cost_report_folder: str = "cost_reports"


@dataclass
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "AppConfig":
        provider = os.getenv("LLM_PROVIDER", "gemini").lower()

        # Resolve model default per provider if not explicitly set
        default_models = {
            "gemini": "gemini-2.0-flash-lite",
            "claude": "claude-3-5-haiku-20241022",
            "azure_openai": "gpt-5-mini",
        }
        model = os.getenv("LLM_MODEL", default_models.get(provider, "gemini-2.0-flash-lite"))

        # Resolve API key per provider
        api_key_env_map = {
            "gemini": "GEMINI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "azure_openai": "AZURE_OPENAI_API_KEY",
        }
        api_key = os.getenv(api_key_env_map.get(provider, "GEMINI_API_KEY"))
        n=0
        llm = LLMConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            azure_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
            thinking_level=os.getenv("THINKING_LEVEL", "MINIMAL"),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            timeout=int(os.getenv("REQUEST_TIMEOUT", "180")),
        )

        paths = PathConfig(
            input_folder=os.getenv("INPUT_FOLDER", "input"),
            output_folder=os.getenv("OUTPUT_FOLDER", "output"),
            log_folder=os.getenv("LOG_FOLDER", "logs"),
            cost_report_folder=os.getenv("COST_REPORT_FOLDER", "cost_reports"),
        )

        return cls(
            llm=llm,
            paths=paths,
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def get_model_pricing(self) -> dict[str, float]:
        """Return pricing dict for the configured model (input/output per 1M tokens)."""
        return MODEL_PRICING.get(
            self.llm.model,
            {"input": 0.10, "output": 0.40},  # safe fallback
        )
