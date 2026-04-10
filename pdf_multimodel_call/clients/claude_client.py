"""
clients/claude_client.py
Anthropic Claude — aligned with official PDF Support docs.
Reference: https://docs.anthropic.com/en/docs/build-with-claude/pdf-support

File dispatch strategy:
  ─────────────────────────────────────────────────────────────────────
  Payload type          Method
  ─────────────────────────────────────────────────────────────────────
  Text-extracted PDF    Plain text block  (no beta header needed)
  Binary PDF            "document" block, source.type = "base64"
                        media_type = "application/pdf"
                        + cache_control "ephemeral" (prompt caching)
  Image (png/jpg/webp)  "image" block, source.type = "base64"
  ─────────────────────────────────────────────────────────────────────

Limits (per Anthropic docs, as of 2025):
  • Max PDF size  : 32 MB
  • Max pages     : 100
  • Token cost    : ~1 500–3 000 tokens/page (text + image)
  • Supported on  : claude-3-5-sonnet-*, claude-3-5-haiku-*, claude-opus-4-*, etc.
"""

from __future__ import annotations

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

# Claude PDF hard limits
_MAX_PDF_BYTES  = 32 * 1024 * 1024   # 32 MB
_MAX_PDF_PAGES  = 100                 # 100 pages


class ClaudeClient(BaseLLMClient):

    def __init__(self, config: LLMConfig) -> None:
        import anthropic
        self._config  = config
        self._client  = anthropic.Anthropic(api_key=config.api_key)
        logger.info("claude_client_initialized", model=config.model)

    @property
    def provider_name(self) -> str:
        return "claude"

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
        content: list[dict] = []

        # ── Build file content block ───────────────────────────────────────
        if payload:
            block = self._build_content_block(payload)
            if block:
                content.append(block)

        # ── User text prompt ───────────────────────────────────────────────
        content.append({"type": "text", "text": user_prompt})

        logger.debug(
            "claude_request_start",
            model=self._config.model,
            file=payload.file_name if payload else None,
            blocks=len(content),
        )

        response = self._client.messages.create(
            model=self._config.model,
            max_tokens=8192,
            system=system_prompt,
            messages=[{"role": "user", "content": content}],
        )

        text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )
        input_tokens  = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        logger.debug(
            "claude_request_complete",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self._config.model,
            provider="claude",
            raw=response,
        )

    # ── private helpers ────────────────────────────────────────────────────

    def _build_content_block(self, payload: FilePayload) -> Optional[dict]:
        """
        Return the appropriate Anthropic content block dict for the payload.

        PDF (binary):
            {
              "type": "document",
              "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": "<base64-string>"
              },
              "cache_control": {"type": "ephemeral"}   ← prompt caching
            }

        Image (binary):
            {
              "type": "image",
              "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": "<base64-string>"
              }
            }

        Text (not binary):
            {
              "type": "text",
              "text": "<extracted text>"
            }
        """
        if not payload.is_binary:
            # Text-extracted PDF or plain .txt → simple text block
            # No special beta flags needed for plain text.
            logger.debug("claude_text_block", file=payload.file_name)
            return {"type": "text", "text": payload.content_text}

        if payload.mime_type == "application/pdf":
            # ── Binary PDF → "document" block ─────────────────────────────
            # Ref: https://docs.anthropic.com/en/docs/build-with-claude/pdf-support
            #
            # Limits check (warn only — let the API surface the actual error)
            import base64
            raw_size = len(base64.b64decode(payload.content_b64))
            if raw_size > _MAX_PDF_BYTES:
                logger.warning(
                    "claude_pdf_oversized",
                    file=payload.file_name,
                    size_mb=round(raw_size / 1024 / 1024, 1),
                    limit_mb=32,
                )

            logger.debug(
                "claude_document_block",
                file=payload.file_name,
                size_kb=round(raw_size / 1024, 1),
            )
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": payload.content_b64,
                },
                # Prompt caching: speeds up repeated analysis of the same doc.
                # Docs: add cache_control to any large, stable content block.
                "cache_control": {"type": "ephemeral"},
            }

        if payload.mime_type.startswith("image/"):
            # ── Binary image → "image" block ──────────────────────────────
            # Supported: image/jpeg, image/png, image/gif, image/webp
            logger.debug(
                "claude_image_block",
                file=payload.file_name,
                mime=payload.mime_type,
            )
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": payload.mime_type,
                    "data": payload.content_b64,
                },
            }

        # Unknown binary type — fall back to text if we have it
        logger.warning(
            "claude_unsupported_mime",
            file=payload.file_name,
            mime=payload.mime_type,
            fallback="text" if payload.content_text else "skip",
        )
        if payload.content_text:
            return {"type": "text", "text": payload.content_text}
        return None