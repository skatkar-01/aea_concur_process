"""
shared/azure_client.py
Single Azure LLM client used by all extractors and processors.

Changes:
  - max_completion_tokens instead of max_completion_tokens (o-series models)
  - temperature removed (unsupported by o-series models)
  - MetricsCollector injected — records tokens + latency per call
"""
from __future__ import annotations
import json
import re
import time
from typing import Any, Optional

from shared.exceptions import LLMCallError, LLMResponseParseError
from shared.logger import get_logger

log = get_logger(__name__)

_MAX_RETRIES     = 4
_INITIAL_DELAY   = 2.0
_BACKOFF_FACTOR  = 2.0
_RETRYABLE_CODES = {429, 500, 502, 503, 529}


class AzureClient:
    def __init__(self, settings, metrics=None):
        self._settings = settings
        self._backend  = settings.azure_backend
        self._metrics  = metrics
        self._client   = self._build_client()
        log.info(
            "AzureClient ready | backend=%s | observability=%s",
            self._backend, "ON" if metrics else "OFF",
        )

    def _build_client(self):
        if self._backend == "azure_openai":
            return self._build_azure_openai()
        elif self._backend == "azure_ai_inference":
            return self._build_azure_inference()
        else:
            raise LLMCallError(
                f"Unknown AZURE_BACKEND='{self._backend}'. "
                "Use 'azure_openai' or 'azure_ai_inference'."
            )

    def _build_azure_openai(self):
        try:
            from openai import AzureOpenAI
        except ImportError:
            raise ImportError("Run: pip install openai>=1.30.0")
        s = self._settings
        if not s.azure_openai_api_key or not s.azure_openai_endpoint:
            raise LLMCallError(
                "Azure OpenAI credentials not configured. "
                "Set AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT in .env"
            )
        return AzureOpenAI(
            api_key=s.azure_openai_api_key,
            azure_endpoint=s.azure_openai_endpoint,
            api_version=s.azure_openai_api_version,
        )

    def _build_azure_inference(self):
        try:
            from azure.ai.inference import ChatCompletionsClient
            from azure.core.credentials import AzureKeyCredential
        except ImportError:
            raise ImportError("Run: pip install azure-ai-inference azure-core")
        s = self._settings
        if not s.azure_ai_endpoint or not s.azure_ai_key:
            raise LLMCallError(
                "Azure AI credentials not configured. "
                "Set AZURE_AI_ENDPOINT and AZURE_AI_KEY in .env"
            )
        from azure.core.credentials import AzureKeyCredential
        return ChatCompletionsClient(
            endpoint=s.azure_ai_endpoint,
            credential=AzureKeyCredential(s.azure_ai_key),
        )

    def call(self, messages: list[dict], max_completion_tokens: int = 1024, context: str = "") -> str:
        delay    = _INITIAL_DELAY
        last_exc = None
        model    = (
            self._settings.azure_openai_deployment
            if self._backend == "azure_openai"
            else self._settings.azure_ai_model
        )

        for attempt in range(1, _MAX_RETRIES + 1):
            t0 = time.monotonic()
            try:
                text, input_tok, output_tok = (
                    self._call_openai(messages, max_completion_tokens)
                    if self._backend == "azure_openai"
                    else self._call_inference(messages, max_completion_tokens)
                )
                latency_ms = int((time.monotonic() - t0) * 1000)

                if self._metrics:
                    self._metrics.record(
                        context=context, model=model,
                        input_tokens=input_tok, output_tokens=output_tok,
                        latency_ms=latency_ms, success=True,
                    )

                log.debug(
                    "LLM OK | context=%-35s | %d+%d tok | %dms | attempt=%d",
                    context, input_tok, output_tok, latency_ms, attempt,
                )
                return text

            except Exception as exc:
                latency_ms  = int((time.monotonic() - t0) * 1000)
                last_exc    = exc
                status_code = getattr(exc, "status_code", None)

                if self._metrics:
                    self._metrics.record(
                        context=context, model=model,
                        input_tokens=0, output_tokens=0,
                        latency_ms=latency_ms, success=False, error=str(exc),
                    )

                if status_code and status_code not in _RETRYABLE_CODES:
                    log.error("LLM non-retryable %s | context=%s | %s", status_code, context, exc)
                    raise LLMCallError(f"Non-retryable error {status_code}: {exc}") from exc

                if attempt < _MAX_RETRIES:
                    log.warning(
                        "LLM attempt %d/%d failed | context=%s | %s | retry in %.1fs",
                        attempt, _MAX_RETRIES, context, type(exc).__name__, delay,
                    )
                    time.sleep(delay)
                    delay *= _BACKOFF_FACTOR

        raise LLMCallError(
            f"LLM failed after {_MAX_RETRIES} attempts [{context}]: {last_exc}"
        ) from last_exc

    def call_json(
        self,
        messages:      list[dict],
        max_completion_tokens:    int            = 1024,
        context:       str            = "",
        required_keys: Optional[list] = None,
    ) -> dict[str, Any]:
        raw = self.call(messages, max_completion_tokens=max_completion_tokens, context=context)
        return self._parse_json(raw, context=context, required_keys=required_keys)

    def _call_openai(self, messages: list[dict], max_completion_tokens: int) -> tuple[str, int, int]:
        """Returns (text, input_tokens, output_tokens)."""
        response = self._client.chat.completions.create(
            model=self._settings.azure_openai_deployment,
            messages=messages,
            max_completion_tokens=max_completion_tokens,   # required by o-series models
        )
        text       = response.choices[0].message.content or ""
        input_tok  = getattr(response.usage, "prompt_tokens",     0) or 0
        output_tok = getattr(response.usage, "completion_tokens", 0) or 0
        return text, input_tok, output_tok

    def _call_inference(self, messages: list[dict], max_completion_tokens: int) -> tuple[str, int, int]:
        """Returns (text, input_tokens, output_tokens)."""
        converted  = self._convert_for_inference(messages)
        response   = self._client.complete(
            model=self._settings.azure_ai_model,
            messages=converted,
            max_completion_tokens=max_completion_tokens,
        )
        text       = response.choices[0].message.content or ""
        input_tok  = getattr(response.usage, "prompt_tokens",     0) or 0
        output_tok = getattr(response.usage, "completion_tokens", 0) or 0
        return text, input_tok, output_tok

    def _convert_for_inference(self, messages: list[dict]) -> list:
        from azure.ai.inference.models import (
            UserMessage, SystemMessage, AssistantMessage,
            TextContentItem, ImageContentItem, ImageUrl,
        )
        converted = []
        for msg in messages:
            role    = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, str):
                cls = {"system": SystemMessage, "assistant": AssistantMessage}.get(role, UserMessage)
                converted.append(cls(content=content))
            else:
                items = []
                for block in content:
                    if block.get("type") == "text":
                        items.append(TextContentItem(text=block["text"]))
                    elif block.get("type") == "image_url":
                        items.append(ImageContentItem(
                            image_url=ImageUrl(url=block["image_url"]["url"])
                        ))
                converted.append(UserMessage(content=items))
        return converted

    def _parse_json(
        self, raw: str, context: str = "", required_keys: Optional[list] = None
    ) -> dict[str, Any]:
        cleaned = raw.strip()
        fence   = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
        if fence:
            cleaned = fence.group(1).strip()
        brace = re.search(r"\{[\s\S]*\}", cleaned)
        if brace:
            cleaned = brace.group(0)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            log.error("JSON parse failed | context=%s | %.300s", context, raw)
            raise LLMResponseParseError(
                f"JSON parse failed [{context}]: {exc}", raw_response=raw
            ) from exc
        if not isinstance(parsed, dict):
            raise LLMResponseParseError(
                f"Expected JSON object, got {type(parsed).__name__} [{context}]",
                raw_response=raw,
            )
        if required_keys:
            missing = [k for k in required_keys if k not in parsed]
            if missing:
                raise LLMResponseParseError(
                    f"Required keys {missing} missing [{context}]", raw_response=raw
                )
        return parsed

    @staticmethod
    def text_block(text: str) -> dict:
        return {"type": "text", "text": text}

    @staticmethod
    def image_block(b64_data: str, media_type: str = "image/png") -> dict:
        return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64_data}"}}

    @staticmethod
    def user_message(content: list[dict]) -> list[dict]:
        return [{"role": "user", "content": content}]

    @staticmethod
    def system_user_message(system: str, content: list[dict]) -> list[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": content}]

    @staticmethod
    def truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars].rsplit("\n", 1)[0]
        return cut + f"\n\n[... truncated at {max_chars} chars ...]"