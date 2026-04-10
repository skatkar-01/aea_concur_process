"""
clients/base_client.py
Abstract base class that every LLM client must implement.
Enforces a uniform interface so the extractor never touches provider SDKs directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from utils.file_utils import FilePayload


@dataclass
class LLMResponse:
    text: str                       # raw JSON string from the model
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    raw: object = None              # original SDK response object (for debugging)


class BaseLLMClient(ABC):
    """
    Contract that all LLM clients must satisfy.

    Subclasses implement `call()` and optionally override `provider_name`.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @abstractmethod
    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        payload: Optional[FilePayload] = None,
    ) -> LLMResponse:
        """
        Send a prompt (+ optional file) to the LLM and return a structured response.

        Args:
            system_prompt:  Instruction prompt (role: system or equivalent).
            user_prompt:    User-facing text prompt.
            payload:        Optional file attachment (PDF, image, etc.).

        Returns:
            LLMResponse with text + token counts.
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} provider={self.provider_name} model={self.model_name}>"
