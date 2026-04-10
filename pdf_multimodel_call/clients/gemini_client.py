"""
clients/gemini_client.py
Google Gemini — aligned with official File Input Methods docs.
Reference: https://ai.google.dev/gemini-api/docs/file-input-methods

File dispatch strategy (auto-selected per payload):
  ─────────────────────────────────────────────────────────────────────
  Payload type              Size          Method
  ─────────────────────────────────────────────────────────────────────
  Text-extracted PDF/TXT    any           Plain text part (cheapest)
  Binary PDF or image       < 20 MB       types.Part.from_bytes()
                                          (inline data in request)
  Binary PDF or image       ≥ 20 MB       client.files.upload()
                                          → File API URI part
  ─────────────────────────────────────────────────────────────────────

File API note: uploaded files expire after 48 h.  This client uploads
fresh per call.  For high-volume pipelines consider adding a URI cache.
"""

from __future__ import annotations

import base64
import io
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

# Inline-data threshold per Gemini docs: use File API for payloads ≥ 50 MB
_INLINE_LIMIT_BYTES = 50 * 1024 * 1024


class GeminiClient(BaseLLMClient):

    def __init__(self, config: LLMConfig) -> None:
        from google import genai
        from google.genai import types as gtypes

        self._config = config
        self._gtypes = gtypes
        self._client = genai.Client(api_key=config.api_key)
        logger.info("gemini_client_initialized", model=config.model)

    @property
    def provider_name(self) -> str:
        return "gemini"

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
        gtypes = self._gtypes
        parts = []

        # ── Build file part ────────────────────────────────────────────────
        if payload:
            if not payload.is_binary:
                # Text-extracted PDF or plain .txt — inline as text Part
                # Docs: types.Part.from_text(text=...)
                parts.append(gtypes.Part.from_text(text=payload.content_text))
                logger.debug("gemini_text_part", file=payload.file_name)

            else:
                raw_bytes = base64.b64decode(payload.content_b64)
                size = len(raw_bytes)

                if size < _INLINE_LIMIT_BYTES:
                    # ── Inline binary (< 50 MB) ───────────────────────────
                    # Docs: types.Part.from_bytes(data=..., mime_type='...')
                    logger.debug(
                        "gemini_inline_bytes",
                        file=payload.file_name,
                        size_kb=round(size / 1024, 1),
                        mime=payload.mime_type,
                    )
                    parts.append(
                        gtypes.Part.from_bytes(
                            data=raw_bytes,
                            mime_type=payload.mime_type,
                        )
                    )
                else:
                    # ── File API upload (≥ 50 MB) ─────────────────────────
                    # Docs: client.files.upload(file=<BytesIO>, config=dict(mime_type=...))
                    # Returns a File object; use .uri as a Part reference.
                    logger.info(
                        "gemini_file_api_upload_start",
                        file=payload.file_name,
                        size_mb=round(size / 1024 / 1024, 1),
                    )
                    buf = io.BytesIO(raw_bytes)
                    buf.name = payload.file_name   # displayed in Google AI Studio

                    uploaded = self._client.files.upload(
                        file=buf,
                        config=dict(mime_type=payload.mime_type),
                    )
                    logger.info(
                        "gemini_file_api_upload_complete",
                        file=payload.file_name,
                        uri=uploaded.uri,
                        api_name=uploaded.name,
                    )
                    # Reference uploaded file by URI
                    parts.append(
                        gtypes.Part.from_uri(
                            uri=uploaded.uri,
                            mime_type=payload.mime_type,
                        )
                    )

        # ── User text prompt (always last) ─────────────────────────────────
        parts.append(gtypes.Part.from_text(text=user_prompt))

        contents = [gtypes.Content(role="user", parts=parts)]
        gen_config = gtypes.GenerateContentConfig(
            thinking_config=gtypes.ThinkingConfig(
                thinking_level=self._config.thinking_level,
            ),
            response_mime_type="application/json",
            system_instruction=[gtypes.Part.from_text(text=system_prompt)],
        )

        logger.debug("gemini_request_start", model=self._config.model)

        # ── Stream and collect ─────────────────────────────────────────────
        full_text = ""
        last_chunk = None
        for chunk in self._client.models.generate_content_stream(
            model=self._config.model,
            contents=contents,
            config=gen_config,
        ):
            if chunk.text:
                full_text += chunk.text
            last_chunk = chunk

        # Token counts from the final chunk's usage_metadata
        usage = getattr(last_chunk, "usage_metadata", None)
        input_tokens  = getattr(usage, "prompt_token_count",     0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        logger.debug(
            "gemini_request_complete",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return LLMResponse(
            text=full_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self._config.model,
            provider="gemini",
            raw=last_chunk,
        )