"""
clients/azure_openai_client.py
Azure AI Foundry (gpt-5-mini) — using the exact client pattern from Foundry UI.

FOUNDRY CODE PATTERN (what this replicates exactly):
    from openai import OpenAI                          ← NOT AzureOpenAI

    client = OpenAI(
        base_url="https://<resource>.openai.azure.com/openai/v1/",
        api_key="<your-api-key>",
    )
    completion = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[...]
    )

KEY DIFFERENCES from old AzureOpenAI pattern:
  ┌──────────────────────┬───────────────────────────────────────────────┐
  │                      │ OLD (broken)       NEW (correct)              │
  ├──────────────────────┼───────────────────────────────────────────────┤
  │ Client class         │ AzureOpenAI        OpenAI                     │
  │ Endpoint             │ base endpoint      base endpoint + /openai/v1/│
  │ api_version param    │ required           NOT used / ignored         │
  │ URL called           │ ?api-version=...   no api-version in URL      │
  └──────────────────────┴───────────────────────────────────────────────┘

.env keys required:
    AZURE_OPENAI_API_KEY      your key from Azure Portal → Keys and Endpoint
    AZURE_OPENAI_ENDPOINT     https://aea-concur-scrubbing-resource.openai.azure.com/
    AZURE_OPENAI_DEPLOYMENT   gpt-5-mini
    (AZURE_OPENAI_API_VERSION is ignored for this client — leave it or remove it)
"""

from __future__ import annotations

import base64
from typing import Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from clients.base_client import BaseLLMClient, LLMResponse
from config import LLMConfig
from utils.file_utils import FilePayload
from utils.logger import get_logger

logger = get_logger(__name__)


class AzureOpenAIClient(BaseLLMClient):
    """
    Azure AI Foundry client for gpt-5-mini.
    Uses standard OpenAI client pointed at the /openai/v1/ endpoint,
    exactly as shown in the Foundry playground Code tab.

    PDF is sent as a native "file" content block (base64 inline),
    same as attaching a PDF in the Foundry playground UI.
    """

    def __init__(self, config: LLMConfig) -> None:
        from openai import OpenAI   # ← standard OpenAI client, NOT AzureOpenAI

        self._config     = config
        self._deployment = config.azure_deployment or config.model

        # Foundry endpoint must end with /openai/v1/
        base_endpoint = config.azure_endpoint.rstrip("/")
        if not base_endpoint.endswith("/openai/v1"):
            base_url = base_endpoint + "/openai/v1/"
        else:
            base_url = base_endpoint + "/"

        # Azure APIM expects the key in "api-key" header.
        # The standard OpenAI client sends "Authorization: Bearer <key>".
        # Sending it in both headers ensures Azure APIM authenticates correctly.
        
        self._client = OpenAI(
            base_url=base_url,
            api_key=config.api_key,
            default_headers={"api-key": config.api_key},
        )

        logger.info(
            "azure_openai_client_initialized",
            deployment=self._deployment,
            base_url=base_url,
        )

    @property
    def provider_name(self) -> str:
        return "azure_openai"

    @property
    def model_name(self) -> str:
        return self._config.model

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        payload: Optional[FilePayload] = None,
    ) -> LLMResponse:
        """
        Send system prompt + PDF to gpt-5-mini.
        PDF is attached as a native file block — identical to Foundry playground.
        Raises ValueError if payload is missing or not a PDF.
        """
        if payload is None:
            raise ValueError(
                "AzureOpenAIClient requires a PDF payload. "
                "Got payload=None."
            )

        if payload.mime_type != "application/pdf":
            raise ValueError(
                f"AzureOpenAIClient only accepts PDF files. "
                f"Got mime_type='{payload.mime_type}' for '{payload.file_name}'."
            )

        pdf_b64 = self._read_pdf_b64(payload)

        user_content = [
            # Native PDF file block — same as Foundry playground attachment
            {
                "type": "file",
                "file": {
                    "filename": payload.file_name,
                    "file_data": f"data:application/pdf;base64,{pdf_b64}",
                },
            },
            # Instruction prompt
            {
                "type": "text",
                "text": user_prompt,
            },
        ]

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ]

        logger.debug(
            "azure_openai_request_start",
            deployment=self._deployment,
            file=payload.file_name,
            pdf_size_kb=round(len(pdf_b64) * 3 / 4 / 1024, 1),
        )

        response = self._client.chat.completions.create(
            model=self._deployment,
            messages=messages,
            # max_tokens=8192,
            response_format={"type": "json_object"},
        )

        text          = response.choices[0].message.content or ""
        input_tokens  = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens

        logger.debug(
            "azure_openai_request_complete",
            file=payload.file_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self._config.model,
            provider="azure_openai",
            raw=response,
        )

    # ── private ────────────────────────────────────────────────────────────

    def _read_pdf_b64(self, payload: FilePayload) -> str:
        """
        Read the raw PDF bytes and return as base64 string.
        Always reads from disk so the actual PDF binary is sent,
        regardless of whether PyMuPDF pre-extracted text from it.
        """
        if payload.file_path and payload.file_path.exists():
            raw = payload.file_path.read_bytes()
            logger.debug(
                "azure_pdf_read_from_disk",
                file=payload.file_name,
                size_kb=round(len(raw) / 1024, 1),
            )
            return base64.b64encode(raw).decode("utf-8")

        if payload.content_b64:
            logger.debug("azure_pdf_using_payload_b64", file=payload.file_name)
            return payload.content_b64

        raise ValueError(
            f"Cannot read PDF bytes for '{payload.file_name}'. "
            f"File not found at '{payload.file_path}' and content_b64 is empty."
        )