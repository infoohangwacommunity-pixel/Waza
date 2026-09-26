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
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from wax.config import get_settings
from wax.observability.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Process-local cooldown after 429 so concurrent work items do not stampede
import time as _time
_provider_cooldown_until: dict[str, float] = {}


def _cooldown_key(name: str, model: str) -> str:
    return f"{name}:{model}"


async def _respect_provider_cooldown(name: str, model: str) -> None:
    import asyncio

    key = _cooldown_key(name, model)
    until = _provider_cooldown_until.get(key, 0.0)
    now = _time.monotonic()
    if until > now:
        delay = min(until - now, 60.0)
        logger.warning(
            "provider_cooldown_wait",
            provider=name,
            model=model,
            wait_seconds=round(delay, 2),
        )
        await asyncio.sleep(delay)


def _arm_provider_cooldown(name: str, model: str, seconds: float) -> None:
    key = _cooldown_key(name, model)
    until = _time.monotonic() + max(1.0, min(float(seconds), 90.0))
    prev = _provider_cooldown_until.get(key, 0.0)
    if until > prev:
        _provider_cooldown_until[key] = until


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
                await _respect_provider_cooldown(self.name, self.model)
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
            ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
            try:
                wait_s = float(ra) if ra else 8.0
            except ValueError:
                wait_s = 8.0
            wait_s = max(5.0, min(wait_s, 60.0))
            _arm_provider_cooldown(self.name, self.model, wait_s)
            logger.warning(
                "provider_rate_limited",
                provider=self.name,
                model=self.model,
                retry_after=wait_s,
            )
            raise ProviderError(
                f"Rate limited (retry_after={wait_s}s)",
                ProviderErrorClass.RATE_LIMITED,
                retryable=True,
            )
        if resp.status_code in (401, 403):
            body_preview = (resp.text or "")[:200].replace("\n", " ")
            logger.warning(
                "provider_auth_failure",
                provider=self.name,
                status_code=resp.status_code,
                base_url=self.base_url,
                model=self.model,
                body_preview=body_preview,
                api_key_configured=bool(self.api_key),
            )
            raise ProviderError(
                f"Auth failure status={resp.status_code}",
                ProviderErrorClass.AUTH_FAILURE,
                retryable=False,
            )
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
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "system":
                system = (system + "\n" + m.content) if system else m.content
            elif m.role == "tool":
                # Anthropic tool result
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "tool",
                                "content": m.content or "",
                            }
                        ],
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    fn = tc.get("function") or {}
                    import json as _json
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        parsed = _json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        parsed = {"raw": raw_args}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id") or "tool",
                            "name": fn.get("name") or tc.get("name") or "tool",
                            "input": parsed if isinstance(parsed, dict) else {"value": parsed},
                        }
                    )
                messages.append({"role": "assistant", "content": blocks})
            else:
                messages.append({"role": m.role if m.role in ("user", "assistant") else "user", "content": m.content or ""})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages or [{"role": "user", "content": "Hello"}],
            "max_tokens": request.max_tokens if request.max_tokens is not None else 4096,
            "temperature": request.temperature if request.temperature is not None else 0.7,
        }
        if system:
            body["system"] = system
        if request.tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.parameters or {"type": "object", "properties": {}},
                }
                for t in request.tools
            ]

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
            body_preview = (resp.text or "")[:200].replace("\n", " ")
            logger.warning(
                "provider_auth_failure",
                provider=self.name,
                status_code=resp.status_code,
                base_url=self.base_url,
                model=self.model,
                body_preview=body_preview,
                api_key_configured=bool(self.api_key),
            )
            raise ProviderError(
                f"Auth failure status={resp.status_code}",
                ProviderErrorClass.AUTH_FAILURE,
                retryable=False,
            )
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
            tool_calls = []
            for block in content_blocks:
                if block.get("type") == "tool_use":
                    import json as _json
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": _json.dumps(block.get("input") or {}),
                            },
                        }
                    )
            return CompletionResponse(
                content=text or None,
                finish_reason=data.get("stop_reason"),
                model=data.get("model"),
                raw=data,
                provider=self.name,
                tool_calls=tool_calls,
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
    """
    Instantiate a chat provider from configuration.

    Provider *name* is an identifier for logging/config only.
    Any name with an API key + model uses the OpenAI-compatible client
    (/chat/completions) unless the name is a known non-compatible native API.

    Optional base_url defaults exist only as convenience when the operator
    omits PRIMARY_BASE_URL for a few well-known hosts — they are not a
    closed allowlist. Unknown names work when BASE_URL is set.
    """
    name = (provider_name or "").strip().lower()
    key = (api_key or "").strip()
    model_id = (model or "").strip()
    url = (base_url or "").strip()

    if not key or name in ("none", "", "null"):
        return None
    if not model_id:
        logger.warning(
            "provider_missing_model",
            provider=name or "(empty)",
            hint="Set PRIMARY_MODEL / FALLBACK_MODEL / matching *_MODEL",
        )
        return None

    # Native protocol (Messages API) — not OpenAI-compatible
    if name == "anthropic":
        return AnthropicProvider(
            api_key=key,
            model=model_id,
            base_url=url or "https://api.anthropic.com",
            timeout=timeout,
        )

    # Optional convenience defaults when BASE_URL is omitted (not an allowlist)
    convenience_base = {
        "openai": "https://api.openai.com/v1",
        "grok": "https://api.x.ai/v1",
        "xai": "https://api.x.ai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "groq": "https://api.groq.com/openai/v1",
        "together": "https://api.together.xyz/v1",
        "fireworks": "https://api.fireworks.ai/inference/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "mistral": "https://api.mistral.ai/v1",
    }
    resolved = url or convenience_base.get(name, "")
    if not resolved:
        logger.warning(
            "provider_missing_base_url",
            provider=name,
            hint=(
                "Set PRIMARY_BASE_URL (or FALLBACK_BASE_URL) to the OpenAI-compatible "
                "API root, e.g. https://api.groq.com/openai/v1"
            ),
        )
        return None

    return OpenAICompatibleProvider(
        name=name or "openai_compatible",
        api_key=key,
        model=model_id,
        base_url=resolved,
        timeout=timeout,
    )


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
            getattr(settings, "memory_base_url", None) or settings.primary_base_url,
            60.0,
        )
        # Context Intelligence — independent role (does not inherit primary unless configured)
        ci_provider = (getattr(settings, "context_intelligence_provider", None) or "none").strip()
        ci_key = (getattr(settings, "context_intelligence_api_key", None) or "").strip()
        ci_model = (getattr(settings, "context_intelligence_model", None) or "").strip()
        ci_base = (getattr(settings, "context_intelligence_base_url", None) or "").strip()
        ci_timeout = float(getattr(settings, "context_intelligence_timeout_seconds", 45.0) or 45.0)
        if ci_provider in ("none", "") or not ci_key or not ci_model:
            if getattr(settings, "context_intelligence_fallback_to_primary", False) and self.primary:
                self.context_model = self.primary
            else:
                self.context_model = None
        else:
            self.context_model = _build_provider(
                ci_provider,
                ci_key,
                ci_model,
                ci_base,
                ci_timeout,
            )

    async def complete(
        self,
        request: CompletionRequest,
        *,
        allow_fallback: bool = True,
        use_memory_model: bool = False,
        role: str = "primary",
    ) -> CompletionResponse:
        """
        role:
          - primary: main tutor path
          - memory: memory extraction model
          - context: Context Intelligence only (independent config)
        """
        providers: list[IntelligenceProvider] = []
        if role == "context":
            if self.context_model:
                providers.append(self.context_model)
            # Context role never silently chains to primary unless context_model IS primary
            # via explicit context_intelligence_fallback_to_primary
        elif use_memory_model and self.memory_model:
            providers.append(self.memory_model)
            # allow_fallback=False means memory model only — no silent primary/fallback chain
            if allow_fallback:
                if self.primary and self.primary is not self.memory_model:
                    providers.append(self.primary)
                if self.fallback and self.fallback not in providers:
                    providers.append(self.fallback)
        else:
            if self.primary:
                providers.append(self.primary)
            if allow_fallback and self.fallback and self.fallback is not self.primary:
                providers.append(self.fallback)

        if not providers:
            raise ProviderError(
                "No intelligence providers configured for role="
                + role
                + ". Set the matching API key / model (or CONTEXT_INTELLIGENCE_FALLBACK_TO_PRIMARY=true).",
                ProviderErrorClass.AUTH_FAILURE,
                retryable=False,
            )

        last_error: Exception | None = None
        for provider in providers:
            try:
                if role == "context" and provider is self.context_model:
                    retries = getattr(settings, "context_intelligence_max_retries", 1)
                elif use_memory_model and provider is self.memory_model:
                    retries = getattr(settings, "memory_max_retries", 1)
                elif provider is self.fallback:
                    retries = getattr(settings, "fallback_max_retries", 2)
                else:
                    retries = getattr(settings, "primary_max_retries", 2)
                return await self._with_retry(provider, request, retries=retries)
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
        self, provider: IntelligenceProvider, request: CompletionRequest, retries: int | None = None
    ) -> CompletionResponse:
        def _should_retry(exc: BaseException) -> bool:
            # Only retry ProviderError instances that are explicitly retryable
            # (429, 5xx, timeout). Auth / invalid request / malformed must not loop.
            return isinstance(exc, ProviderError) and bool(getattr(exc, "retryable", False))

        @retry(
            retry=retry_if_exception(_should_retry),
            stop=stop_after_attempt(
                max(1, retries if retries is not None else getattr(settings, "primary_max_retries", 2))
            ),
            wait=wait_exponential_jitter(initial=2, max=60),
            reraise=True,
        )
        async def _inner() -> CompletionResponse:
            return await provider.complete(request)

        return await _inner()


_intelligence: IntelligenceService | None = None


def get_intelligence() -> IntelligenceService:
    global _intelligence
    if _intelligence is None:
        _intelligence = IntelligenceService()
    return _intelligence
