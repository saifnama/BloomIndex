"""Shared LLM client boundary.

A process-wide AsyncOpenAI client provides unified access to chat
completions for RAG, NER, and evaluation pipelines.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_AUTH_ERROR_MSG = (
    "LLM authentication failed. Check LLM_API_KEY for "
    "the server at the configured LLM_API_BASE_URL."
)


class LLMConfigError(ValueError):
    """Raised when unified LLM configuration is invalid or missing."""


class LLMAuthError(Exception):
    """Raised when the LLM server rejects credentials."""


class LLMTimeoutError(Exception):
    """Raised when an LLM request exceeds its deadline."""


class LLMRateLimitError(Exception):
    """Raised when the upstream provider throttles requests."""


class LLMUpstreamError(Exception):
    """Raised on connection or unclassified upstream errors."""


class LLMResponse:
    """Lightweight response wrapper exposing a text content attribute."""

    __slots__ = ("content",)

    def __init__(self, content: str):
        self.content = content


def _map_sdk_error(exc: Exception) -> Exception:
    """Map OpenAI SDK exceptions to domain error types."""
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
        )
    except ImportError:
        return LLMUpstreamError(f"LLM request failed: {exc}")
    if isinstance(exc, AuthenticationError):
        return LLMAuthError(_AUTH_ERROR_MSG)
    if isinstance(exc, RateLimitError):
        return LLMRateLimitError(f"LLM rate limited: {exc}")
    if isinstance(exc, APITimeoutError):
        return LLMTimeoutError(f"LLM request timed out: {exc}")
    if isinstance(exc, APIConnectionError):
        return LLMUpstreamError(f"LLM connection failed: {exc}")
    if isinstance(exc, APIStatusError):
        if exc.status_code == 401:
            return LLMAuthError(_AUTH_ERROR_MSG)
        if exc.status_code == 429:
            return LLMRateLimitError(f"LLM rate limited: {exc}")
        return LLMUpstreamError(f"LLM upstream error ({exc.status_code})")
    if isinstance(exc, asyncio.TimeoutError):
        return LLMTimeoutError(f"LLM request timed out: {exc}")
    return LLMUpstreamError(f"LLM request failed: {exc}")


def _build_sdk_client(settings):
    """Construct an AsyncOpenAI client from application settings."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
    )


class SharedLLMClient:
    """Thin wrapper over an AsyncOpenAI chat completions client.

    Provides non-streaming and streaming interfaces with optional JSON
    mode and reasoning suppression.
    """

    def __init__(
        self,
        sdk_client=None,
        model: str | None = None,
        default_temperature: float = 0.1,
        thinking: bool | None = None,
        no_think_directive: str | None = None,
        chat_template_kwargs: bool | None = None,
    ):
        if sdk_client is None:
            from backend.src.settings import resolve_llm_settings

            settings = resolve_llm_settings()
            sdk_client = _build_sdk_client(settings)
            model = model or settings.model
            thinking = settings.thinking if thinking is None else thinking
            no_think_directive = (
                settings.no_think_directive if no_think_directive is None else no_think_directive
            )
            chat_template_kwargs = (
                settings.chat_template_kwargs
                if chat_template_kwargs is None
                else chat_template_kwargs
            )
        self._client = sdk_client
        self._model = model
        self._default_temperature = default_temperature
        self._thinking = False if thinking is None else thinking
        self._no_think_directive = "/no_think" if no_think_directive is None else no_think_directive
        self._chat_template_kwargs = False if chat_template_kwargs is None else chat_template_kwargs

    def _params(
        self,
        *,
        prompt=None,
        messages=None,
        temperature=None,
        max_tokens=None,
        json_mode=False,
        timeout_seconds=None,
        thinking: bool | None = None,
    ) -> dict:
        if messages is not None:
            msg_list = [dict(m) for m in messages]
        elif prompt is not None:
            msg_list = [{"role": "user", "content": prompt}]
        else:
            raise ValueError("Either prompt or messages must be provided")
        thinking = self._thinking if thinking is None else thinking
        directive = (self._no_think_directive or "").strip()
        # Suppress reasoning tokens when thinking is disabled.
        if not thinking and directive and msg_list and msg_list[-1].get("role") == "user":
            content = msg_list[-1].get("content")
            if isinstance(content, str) and directive not in content:
                msg_list[-1]["content"] = content + "\n\n" + directive
        params: dict = {
            "model": self._model,
            "messages": msg_list,
            "temperature": self._default_temperature if temperature is None else temperature,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if json_mode:
            params["response_format"] = {"type": "json_object"}
        if timeout_seconds is not None:
            params["timeout"] = timeout_seconds
        # Forward template kwargs for serving runtimes that support it.
        if self._chat_template_kwargs:
            params["extra_body"] = {"chat_template_kwargs": {"enable_thinking": thinking}}
        return params

    async def aclose(self) -> None:
        """Close the underlying client and release pooled connections."""
        try:
            await self._client.close()
        except Exception:
            pass

    @staticmethod
    def _text(response) -> str:
        """Extract text content safely from a completions response."""
        try:
            return response.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            return ""

    async def invoke(
        self,
        prompt: str = None,
        messages: list[dict] = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        response_format: dict | None = None,
        timeout_seconds: float | None = None,
        thinking: bool | None = None,
        **_ignored,
    ) -> LLMResponse:
        """Execute a non-streaming chat completion call.

        When thinking is disabled, appends the configured reasoning
        suppression directive to the final user message.
        """
        if response_format is not None:
            json_mode = response_format.get("type") == "json_object"
        params = self._params(
            prompt=prompt,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )
        try:
            response = await self._client.chat.completions.create(**params)
        except Exception as exc:
            raise _map_sdk_error(exc) from exc
        return LLMResponse(self._text(response))

    async def astream(
        self,
        prompt: str = None,
        messages: list[dict] = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
        thinking: bool | None = None,
        **_ignored,
    ) -> AsyncIterator[str]:
        """Stream incremental token deltas from a chat completion."""
        params = self._params(
            prompt=prompt,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )
        try:
            stream = await self._client.chat.completions.create(**params, stream=True)
            async for event in stream:
                try:
                    delta = event.choices[0].delta.content or ""
                except (AttributeError, IndexError, TypeError):
                    continue
                if delta:
                    yield delta
        except Exception as exc:
            raise _map_sdk_error(exc) from exc


_client: SharedLLMClient | None = None


def get_llm_client() -> SharedLLMClient:
    """Return the process-wide shared LLM client singleton."""
    global _client
    if _client is None:
        _client = SharedLLMClient()
    return _client


async def close_llm_client() -> None:
    """Close the shared client during application shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def create_ragas_client():
    """Create an AsyncOpenAI client and model pair for RAGAS."""
    from backend.src.settings import resolve_llm_settings

    settings = resolve_llm_settings()
    return (
        _build_sdk_client(settings),
        settings.model,
    )
