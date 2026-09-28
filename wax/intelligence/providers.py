"""
Single intelligence path: chat completion for the AI agent.

Primary provider (+ optional fallback for resilience only).
Text-only: no function-calling, no ToolSpec, no second "memory model" brain.
Embeddings / multimodal are not separate intelligence layers here.
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


def _cooldown_key(*, name: str, base_url: str = "", model: str = "") -> str:
    # Prefer host endpoint — same Groq base URL shares one rate budget across models/roles
    host = (base_url or "").strip().rstrip("/").lower()
    if host:
        return f"host:{host}"
    return f"name:{(name or '').lower()}:{(model or '').lower()}"


async def _respect_provider_cooldown(name: str, model: str, base_url: str = "") -> None:
    import asyncio

    key = _cooldown_key(name=name, base_url=base_url, model=model)
    until = _provider_cooldown_until.get(key, 0.0)
    now = _time.monotonic()
    if until > now:
        delay = min(until - now, 90.0)
        logger.warning(
            "provider_cooldown_wait",
            provider=name,
            model=model,
            base_url=(base_url or "")[:80],
            wait_seconds=round(delay, 2),
        )
        await asyncio.sleep(delay)


def _arm_provider_cooldown(
    name: str, model: str, seconds: float, base_url: str = ""
) -> None:
    key = _cooldown_key(name=name, base_url=base_url, model=model)
    # Floor 10s so concurrent jobs do not re-stampede immediately
    until = _time.monotonic() + max(10.0, min(float(seconds), 120.0))
    prev = _provider_cooldown_until.get(key, 0.0)
    if until > prev:
        _provider_cooldown_until[key] = until


def provider_cooldown_remaining(name: str, base_url: str = "", model: str = "") -> float:
    key = _cooldown_key(name=name, base_url=base_url, model=model)
    return max(0.0, _provider_cooldown_until.get(key, 0.0) - _time.monotonic())


def any_provider_cooling_down(min_seconds: float = 1.0) -> bool:
    now = _time.monotonic()
    return any(v - now >= min_seconds for v in _provider_cooldown_until.values())


class ProviderErrorClass(str, Enum):
    TIMEOUT = "provider_timeout"
    UNAVAILABLE = "provider_unavailable"
    RATE_LIMITED = "provider_rate_limited"
    AUTH_FAILURE = "provider_authentication_failure"
    INVALID_REQUEST = "provider_invalid_request"
    MALFORMED_RESPONSE = "provider_malformed_response"
    UNKNOWN = "provider_unknown"


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        error_class: ProviderErrorClass,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message)
        self.error_class = error_class
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass
class ChatMessage:
    """Text-only chat turn. No tool/function-calling roles."""

    role: str  # system | user | assistant
    content: str
    name: str | None = None


@dataclass
class CompletionRequest:
    """Text completion only — no tools catalogue; max_tokens is optional resource bound, not an intelligence budget."""

    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionResponse:
    content: str | None
    finish_reason: str | None = None
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None


class IntelligenceProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        ...


def _serialize_openai_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """OpenAI-compatible message list — text roles only."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.role if m.role in ("system", "user", "assistant") else "user"
        msg: dict[str, Any] = {
            "role": role,
            "content": m.content if m.content is not None else "",
        }
        if m.name and role == "user":
            msg["name"] = m.name
        out.append(msg)
    return out


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
            "messages": _serialize_openai_messages(request.messages),
            "temperature": request.temperature if request.temperature is not None else 0.7,
        }
        # max_tokens only when the caller sets it. Default is provider/model natural limit —
        # not an application intelligence budget. When set, treat as infrastructure resource bound.
        if request.max_tokens is not None:
            body["max_tokens"] = int(request.max_tokens)
        # Ensure body is JSON-serializable (Upstage rejects malformed bodies)
        try:
            import json as _json
            body = _json.loads(_json.dumps(body, ensure_ascii=False, default=str))
        except Exception:
            pass

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await _respect_provider_cooldown(self.name, self.model, self.base_url)
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
            _arm_provider_cooldown(self.name, self.model, wait_s, self.base_url)
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
                retry_after_seconds=wait_s,
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
            content = message.get("content")
            if isinstance(content, list):
                # Some providers return content parts
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(p.get("text") or "")
                    elif isinstance(p, str):
                        parts.append(p)
                content = "".join(parts)
            return CompletionResponse(
                content=content if isinstance(content, str) else (str(content) if content else None),
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
            else:
                messages.append(
                    {
                        "role": m.role if m.role in ("user", "assistant") else "user",
                        "content": m.content or "",
                    }
                )

        # Anthropic API requires max_tokens. Use caller value or a high resource safety floor
        # (not an application intelligence budget on how much the model may think).
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages or [{"role": "user", "content": "Hello"}],
            "max_tokens": int(request.max_tokens) if request.max_tokens is not None else 8192,
            "temperature": request.temperature if request.temperature is not None else 0.7,
        }
        if system:
            body["system"] = system

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await _respect_provider_cooldown(self.name, self.model, self.base_url)
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
            ra = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
            try:
                wait_s = float(ra) if ra else 15.0
            except ValueError:
                wait_s = 15.0
            wait_s = max(10.0, min(wait_s, 120.0))
            _arm_provider_cooldown(self.name, self.model, wait_s, self.base_url)
            raise ProviderError(
                f"Rate limited (retry_after={wait_s}s)",
                ProviderErrorClass.RATE_LIMITED,
                retryable=True,
                retry_after_seconds=wait_s,
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
            content_blocks = data.get("content", [])
            text_out = "".join(
                block.get("text", "") for block in content_blocks if block.get("type") == "text"
            )
            return CompletionResponse(
                content=text_out or None,
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
            hint="Set PRIMARY_MODEL (and FALLBACK_MODEL if using fallback)",
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
    One decision-maker path: primary chat model, optional fallback on failure.

    Fallback is resilience (same task), not a second intelligence role.
    No memory-extraction model. No multimodal side brain.
    """

    def __init__(self) -> None:
        self.primary = _build_provider(
            settings.primary_provider,
            settings.primary_api_key,
            settings.primary_model,
            settings.primary_base_url,
            getattr(settings, "primary_timeout_seconds", 60.0),
        )
        self.fallback = _build_provider(
            settings.fallback_provider,
            settings.fallback_api_key,
            settings.fallback_model,
            settings.fallback_base_url,
            getattr(settings, "fallback_timeout_seconds", 60.0),
        )
        if not self.primary and not self.fallback:
            logger.error("no_intelligence_provider_configured")

    async def complete(
        self,
        request: CompletionRequest,
        *,
        allow_fallback: bool = True,
        retries: int | None = None,
    ) -> CompletionResponse:
        """Run the single intelligence completion path."""
        providers: list = []
        if self.primary:
            providers.append(self.primary)
        if allow_fallback and self.fallback and self.fallback is not self.primary:
            providers.append(self.fallback)
        if not providers:
            raise ProviderError(
                "No intelligence provider configured",
                ProviderErrorClass.UNAVAILABLE,
                retryable=False,
            )

        last_err: Exception | None = None
        for i, provider in enumerate(providers):
            is_fallback = i > 0
            try:
                return await self._complete_with_retries(
                    provider,
                    request,
                    retries=retries
                    if retries is not None
                    else (
                        getattr(settings, "fallback_max_retries", 2)
                        if is_fallback
                        else getattr(settings, "primary_max_retries", 2)
                    ),
                )
            except ProviderError as e:
                last_err = e
                logger.warning(
                    "provider_complete_failed",
                    provider=getattr(provider, "name", "?"),
                    error_class=str(e.error_class),
                    retryable=e.retryable,
                    is_fallback=is_fallback,
                )
                # Auth / invalid: try fallback once; don't spin
                if not e.retryable and not is_fallback and allow_fallback:
                    continue
                if is_fallback or not allow_fallback:
                    raise
                continue
            except Exception as e:
                last_err = e
                logger.exception("provider_unexpected_error", provider=getattr(provider, "name", "?"))
                if i + 1 >= len(providers):
                    raise ProviderError(
                        str(e), ProviderErrorClass.UNAVAILABLE, retryable=False
                    ) from e
                continue
        assert last_err is not None
        raise last_err

    async def _complete_with_retries(
        self,
        provider: IntelligenceProvider,
        request: CompletionRequest,
        *,
        retries: int,
    ) -> CompletionResponse:
        def _should_retry(exc: BaseException) -> bool:
            return isinstance(exc, ProviderError) and bool(getattr(exc, "retryable", False))

        @retry(
            retry=retry_if_exception(_should_retry),
            stop=stop_after_attempt(max(1, retries if retries is not None else 2)),
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
