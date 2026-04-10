"""
clients/client_factory.py
Factory that returns the right LLM client based on config.provider.
This is the ONLY place that knows about all three providers.
"""

from __future__ import annotations

from clients.base_client import BaseLLMClient
from config import LLMConfig
from utils.logger import get_logger

logger = get_logger(__name__)

_REGISTRY: dict[str, str] = {
    "gemini":       "clients.gemini_client.GeminiClient",
    "claude":       "clients.claude_client.ClaudeClient",
    "azure_openai": "clients.azure_openai_client.AzureOpenAIClient",
}


def create_client(config: LLMConfig) -> BaseLLMClient:
    """
    Instantiate and return an LLM client for the given provider.

    Adding a new provider:
        1. Create clients/my_provider_client.py with a class implementing BaseLLMClient.
        2. Add an entry to _REGISTRY above.
        3. Change LLM_PROVIDER in .env — nothing else changes.
    """
    provider = config.provider.lower()
    if provider not in _REGISTRY:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            f"Valid options: {list(_REGISTRY.keys())}"
        )

    module_path, class_name = _REGISTRY[provider].rsplit(".", 1)
    module = __import__(module_path, fromlist=[class_name])
    client_cls = getattr(module, class_name)

    client: BaseLLMClient = client_cls(config)
    logger.info("llm_client_created", provider=provider, model=config.model)
    return client
