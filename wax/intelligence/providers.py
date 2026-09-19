"""
Provider-agnostic intelligence abstraction.

Primary + fallback. Retries. Error classification.
No cost accounting. No token budgets that reject learners.
Every meaningful learner message goes through intelligence.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class ProviderErrorClass(str, Enum):
    TIMEOUT = "provider_timeout"
    UNAVAILABLE = "provider_unavailable"
    RATE_LIMITED = "provider_rate_limited"
    AUTH_FAILURE = "provider_authentication_failure"
    INVALID_REQUEST = "provider_invalid_request"
    MALFORMED_RESPONSE = "provider_malformed_response"
    UNKNOWN = "provider_unknown"


class ProviderError(Exception):
    def __init__(self, message: str, error_class: ProviderErrorClass, retryable: bool = False):
        super().__init__(message)
        self.error_class = error_class
        self.retryable = retryable


@dataclass
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class CompletionRequest:
    messages: list[ChatMessage]
    tools: list[ToolSpec] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionResponse:
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None


class IntelligenceProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        ...


class OpenAICompatibleProvider(IntelligenceProvider):
    """OpenAI, Grok (xAI), OpenRouter, and any OpenAI-compatible endpoint."""

    def __init__(
        self,
        name: str,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
    ):
        self.name = name
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    **({"name": m.name} if m.name else {}),
                    **({"tool_calls": m.tool_calls} if m.tool_calls else {}),
                    **({"tool_call_id": m.tool_call_id} if m.tool_call_id else {}),
                }
                for m in request.messages
            ],
            "temperature": request.temperature if request.temperature is not None else 0.7,
            "max_tokens": request.max_tokens if request.max_tokens is not None else 4096,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
            body["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
        except httpx.TimeoutException as e:
            raise ProviderError(str(e), ProviderErrorClass.TIMEOUT, retryable=True) from e
        except httpx.ConnectError as e:
            raise ProviderError(str(e), ProviderErrorClass.UNAVAILABLE, retryable=True) from e

        if resp.status_code == 429:
            raise ProviderError("Rate limited", ProviderErrorClass.RATE_LIMITED, retryable=True)
        if resp.status_code in (401, 403):
            raise ProviderError("Auth failure", ProviderErrorClass.AUTH_FAILURE, retryable=False)
        if resp.status_code >= 500:
            raise ProviderError(
                f"Server error {resp.status_code}", ProviderErrorClass.UNAVAILABLE, retryable=True
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Invalid request: {resp.text[:500]}",
                ProviderErrorClass.INVALID_REQUEST,
                retryable=False,
            )

        data = resp.json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
            return CompletionResponse(
                content=message.get("content"),
                tool_calls=message.get("tool_calls") or [],
                finish_reason=choice.get("finish_reason"),
                model=data.get("model"),
                raw=data,
                provider=self.name,
            )
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(
                f"Malformed response: {e}", ProviderErrorClass.MALFORMED_RESPONSE, retryable=False
            ) from e


class AnthropicProvider(IntelligenceProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 120.0,
    ):
        self.name = "anthropic"
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        system = None
        messages = []
        for m in request.messages:
            if m.role == "system":
                system = m.content
            else:
                messages.append({"role": m.role, "content": m.content})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens if request.max_tokens is not None else 4096,
            "temperature": request.temperature if request.temperature is not None else 0.7,
        }
        if system:
            body["system"] = system

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/messages",
                    headers=headers,
                    json=body,
                )
        except httpx.TimeoutException as e:
            raise ProviderError(str(e), ProviderErrorClass.TIMEOUT, retryable=True) from e
        except httpx.ConnectError as e:
            raise ProviderError(str(e), ProviderErrorClass.UNAVAILABLE, retryable=True) from e

        if resp.status_code == 429:
            raise ProviderError("Rate limited", ProviderErrorClass.RATE_LIMITED, retryable=True)
        if resp.status_code in (401, 403):
            raise ProviderError("Auth failure", ProviderErrorClass.AUTH_FAILURE, retryable=False)
        if resp.status_code >= 500:
            raise ProviderError(
                f"Server error {resp.status_code}", ProviderErrorClass.UNAVAILABLE, retryable=True
            )
        if resp.status_code >= 400:
            raise ProviderError(
                f"Invalid request: {resp.text[:500]}",
                ProviderErrorClass.INVALID_REQUEST,
                retryable=False,
            )

        data = resp.json()
        try:
            content_blocks = data.get("content", [])
            text = "".join(
                block.get("text", "") for block in content_blocks if block.get("type") == "text"
            )
            return CompletionResponse(
                content=text or None,
                finish_reason=data.get("stop_reason"),
                model=data.get("model"),
                raw=data,
                provider=self.name,
            )
        except (KeyError, TypeError) as e:
            raise ProviderError(
                f"Malformed response: {e}", ProviderErrorClass.MALFORMED_RESPONSE, retryable=False
            ) from e


def _build_provider(
    provider_name: str,
    api_key: str,
    model: str,
    base_url: str = "",
    timeout: float = 120.0,
) -> IntelligenceProvider | None:
    if not api_key or provider_name in ("none", "", "null"):
        return None
    defaults = {
        "openai": "https://api.openai.com/v1",
        "grok": "https://api.x.ai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    if provider_name in ("openai", "grok", "openrouter"):
        return OpenAICompatibleProvider(
            name=provider_name,
            api_key=api_key,
            model=model,
            base_url=base_url or defaults.get(provider_name, "https://api.openai.com/v1"),
            timeout=timeout,
        )
    if provider_name == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            base_url=base_url or "https://api.anthropic.com",
            timeout=timeout,
        )
    return None


class IntelligenceService:
    """
    High-level intelligence with primary + fallback.
    No cost gates. Every learner message is eligible for intelligence.
    """

    def __init__(self) -> None:
        self.primary = _build_provider(
            settings.primary_provider,
            settings.primary_api_key,
            settings.primary_model,
            settings.primary_base_url,
            settings.primary_timeout_seconds,
        )
        self.fallback = _build_provider(
            settings.fallback_provider,
            settings.fallback_api_key,
            settings.fallback_model,
            settings.fallback_base_url,
            settings.fallback_timeout_seconds,
        )
        # Smaller model for memory operations
        mem_key = settings.memory_api_key or settings.primary_api_key
        mem_provider = settings.memory_provider if settings.memory_provider != "none" else settings.primary_provider
        self.memory_model = _build_provider(
            mem_provider,
            mem_key,
            settings.memory_model,
            settings.primary_base_url,
            60.0,
        )

    async def complete(
        self,
        request: CompletionRequest,
        *,
        allow_fallback: bool = True,
        use_memory_model: bool = False,
    ) -> CompletionResponse:
        providers: list[IntelligenceProvider] = []
        if use_memory_model and self.memory_model:
            providers.append(self.memory_model)
        if self.primary:
            providers.append(self.primary)
        if allow_fallback and self.fallback and self.fallback is not self.primary:
            providers.append(self.fallback)

        if not providers:
            raise ProviderError(
                "No intelligence providers configured. Set PRIMARY_API_KEY.",
                ProviderErrorClass.AUTH_FAILURE,
                retryable=False,
            )

        last_error: Exception | None = None
        for provider in providers:
            try:
                return await self._with_retry(provider, request)
            except ProviderError as e:
                last_error = e
                logger.warning(
                    "provider_failed",
                    provider=provider.name,
                    error_class=e.error_class.value,
                    retryable=e.retryable,
                )
                if not e.retryable and provider is providers[-1]:
                    raise
                continue
        raise last_error or ProviderError(
            "All providers failed", ProviderErrorClass.UNAVAILABLE, retryable=False
        )

    async def _with_retry(
        self, provider: IntelligenceProvider, request: CompletionRequest
    ) -> CompletionResponse:
        @retry(
            retry=retry_if_exception_type(ProviderError),
            stop=stop_after_attempt(max(1, settings.primary_max_retries)),
            wait=wait_exponential_jitter(initial=1, max=15),
            reraise=True,
        )
        async def _inner() -> CompletionResponse:
            try:
                return await provider.complete(request)
            except ProviderError as e:
                if not e.retryable:
                    raise
                raise

        return await _inner()


_intelligence: IntelligenceService | None = None


def get_intelligence() -> IntelligenceService:
    global _intelligence
    if _intelligence is None:
        _intelligence = IntelligenceService()
    return _intelligence
