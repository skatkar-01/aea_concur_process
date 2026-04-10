"""
config.py
All application settings loaded from environment variables via .env.
Raises clearly at startup if required values are missing.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Required environment variable '{key}' is not set.")
    return val


def _optional(key: str, default: str) -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class Config:
    # Azure OpenAI
    api_key: str
    base_url: str
    model: str

    # Folders
    input_folder: str
    output_folder: str

    # Cost (USD per 1K tokens)
    cost_per_1k_input: float
    cost_per_1k_output: float

    # Behaviour
    cache_enabled: bool
    log_level: str


def load_config() -> Config:
    return Config(
        api_key=_require("AZURE_OPENAI_API_KEY"),
        base_url=_require("AZURE_OPENAI_BASE_URL"),
        model=_optional("AZURE_OPENAI_MODEL", "gpt-4o"),
        input_folder=_optional("INPUT_FOLDER", "input"),
        output_folder=_optional("OUTPUT_FOLDER", "output"),
        cost_per_1k_input=float(_optional("COST_PER_1K_INPUT_TOKENS", "0.005")),
        cost_per_1k_output=float(_optional("COST_PER_1K_OUTPUT_TOKENS", "0.015")),
        cache_enabled=_optional("CACHE_ENABLED", "false").lower() == "true",
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
    )
